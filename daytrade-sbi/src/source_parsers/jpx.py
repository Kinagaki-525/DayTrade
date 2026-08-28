"""Deterministic parsers for JPX source pages."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.source_parsers.base import ParseContext, ParseResult, ParsedValue, parse_failed
from src.source_parsers.decode import DecodeError, decode_source_page
from src.source_parsers.html import clean, definition_pairs, table_rows
from src.source_parsers.numeric import (
    ParseFailed,
    parse_grouped_decimal,
    parse_grouped_integer,
)


ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The current JPX official non-business-day calendar publishes each
#: holiday's date as ``YYYY/MM/DD（曜）`` -- the parenthesized weekday
#: character is display-only and is never used for business-day judgement,
#: only the digits are. Used with ``.search()`` for the whole-page
#: "any stray date" sweep, and with ``.fullmatch()`` against an individual
#: table cell, which must be *exactly* this shape and nothing else.
JPX_SLASH_DATE_PATTERN = re.compile(r"(\d{4})/(\d{2})/(\d{2})（[^）]*）")

#: A year section heading: an isolated ``<h1>``-``<h4>`` whose entire text
#: content is ``YYYY年`` and nothing else. Everything between one heading and
#: the next (or the end of the page) is that year's officially published
#: holiday section; a date can only be trusted if it falls inside one.
YEAR_HEADING_PATTERN = re.compile(r"<h[1-4][^>]*>\s*(\d{4})年\s*</h[1-4]>")

#: A complete, closed table block. A heading with no table, or a table that
#: never closes before the next heading / end of page, is a structural
#: failure -- not an empty-but-valid section.
TABLE_BLOCK_PATTERN = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)


def _decode(raw: bytes, context: ParseContext):
    return decode_source_page(raw, context.content_type)


def parse_calendar(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_CALENDAR -> non-business days AND the years that evidence covers.

    Production JPX holiday tables are organized by year: an ``<h?>YYYY年</h?>``
    heading followed by that year's holiday table, with rows in
    ``YYYY/MM/DD（曜）`` form. A year only counts as "covered" when its own
    section was structurally recognized end to end AND yielded at least one
    valid holiday row -- a bare heading, an empty or header-only table, a
    table that never closes, or a row whose date does not belong to that
    section's year are all structural failures of the *whole* calendar, not
    an empty-but-valid section quietly skipped: one broken year section
    fails the entire parse rather than silently degrading coverage.
    """
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    headings = list(YEAR_HEADING_PATTERN.finditer(page.text))
    if not headings:
        return ParseResult(status="NOT_FOUND", reason_codes=("NO_CALENDAR_YEAR_SECTIONS",))

    covered_years: list[str] = []
    dates: list[str] = []
    for index, heading in enumerate(headings):
        year = heading.group(1)
        section_start = heading.end()
        section_end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(page.text)
        )
        section_text = page.text[section_start:section_end]

        table_match = TABLE_BLOCK_PATTERN.search(section_text)
        if table_match is None:
            return parse_failed(
                f"{year}年 section has no complete holiday table"
            )
        table_html = table_match.group(1)

        rows = [row for row in table_rows(table_html) if row]
        if not rows:
            return parse_failed(f"{year}年 section's holiday table has no rows")

        # The first row must be the expected 日付 / 名称 header -- a table
        # that doesn't carry that header is not recognizably a JPX holiday
        # table at all, even if its body happens to contain date-shaped text.
        header, data_rows = rows[0], rows[1:]
        if (
            len(header) < 2
            or "日付" not in header[0]
            or "名称" not in header[1]
        ):
            return parse_failed(
                f"{year}年 section's table header is not 日付/名称"
            )
        if not data_rows:
            return parse_failed(f"{year}年 section's holiday table has no data rows")

        year_dates: list[str] = []
        for row in data_rows:
            # Every data row must have at least the date and name columns,
            # and the date column must be *exactly* YYYY/MM/DD（曜）-- a
            # malformed row is never silently skipped, it fails the whole
            # calendar the same as an invalid or cross-year date would.
            if len(row) < 2:
                return parse_failed(
                    f"{year}年 section holiday row is missing the name column: {row!r}"
                )
            match = JPX_SLASH_DATE_PATTERN.fullmatch(row[0])
            if match is None:
                return parse_failed(
                    f"{year}年 section holiday row date is not YYYY/MM/DD（曜）: {row[0]!r}"
                )
            if not row[1]:
                return parse_failed(
                    f"{year}年 section holiday row has an empty name column"
                )
            row_year, month, day = match.groups()
            if row_year != year:
                return parse_failed(
                    f"calendar date {row_year}/{month}/{day} appears inside the "
                    f"{year}年 section"
                )
            try:
                iso = date(int(row_year), int(month), int(day)).isoformat()
            except ValueError:
                return parse_failed(
                    f"calendar date {row_year}/{month}/{day} is not a valid date"
                )
            if iso not in year_dates:
                year_dates.append(iso)

        covered_years.append(year)
        for iso in year_dates:
            if iso not in dates:
                dates.append(iso)

    # Any slash-formatted date outside every year section is untrustworthy:
    # it cannot be attributed to a confirmed-covered year.
    covered_spans = [(h.end(), (headings[i + 1].start() if i + 1 < len(headings) else len(page.text)))
                      for i, h in enumerate(headings)]

    def _inside_a_section(position: int) -> bool:
        return any(start <= position < end for start, end in covered_spans)

    for match in JPX_SLASH_DATE_PATTERN.finditer(page.text):
        if not _inside_a_section(match.start()):
            return parse_failed(
                f"calendar date {match.group(0)} is outside every YYYY年 section"
            )

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="non_business_days",
                ticker=None,
                trading_date=context.trading_date,
                value=sorted(dates),
            ),
            ParsedValue(
                field_name="calendar_covered_years",
                ticker=None,
                trading_date=context.trading_date,
                value=sorted(covered_years),
            ),
        ),
    )


def parse_listed_company(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_LISTED_COMPANY_AUDIT -> TSE listing facts for one ticker.

    The AUDIT listed-issues page parser. JPX_LISTED_COMPANY itself is
    acquired from the candidate-specific 東証上場会社情報 search and parsed by
    :func:`parse_stock_search`; this parser and its binding are unchanged.

    Listing is a binary fact read straight off the page. There is no
    ``.T`` suffix guessing anywhere: an absent ticker is ``NOT_FOUND``, which
    the TSE Listing Gate turns into a batch failure, never a silent
    per-ticker exclusion.
    """
    if not context.ticker:
        return parse_failed("JPX_LISTED_COMPANY requires a target ticker")
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    rows = [row for row in table_rows(page.text) if row and row[0] == context.ticker]
    if not rows:
        return ParseResult(status="NOT_FOUND", reason_codes=("TICKER_NOT_LISTED_ON_PAGE",))
    if len({tuple(row) for row in rows}) > 1:
        return parse_failed(f"ambiguous listing rows for ticker {context.ticker}")

    row = rows[0]
    if len(row) < 3:
        return parse_failed(f"listing row for {context.ticker} is missing columns")

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="listed_company_name",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=row[1],
            ),
            ParsedValue(
                field_name="market_segment",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=row[2],
            ),
        ),
    )


#: The canonical candidate code contract shared with
#: :mod:`src.source_acquisition` -- four characters, digits and capitals only.
#: New alphanumeric TSE codes (``285A``) are ordinary candidates under it.
CANONICAL_CODE_PATTERN = re.compile(r"^[0-9A-Z]{4}$")

#: How the JPX 東証上場会社情報 search result publishes a code: the canonical
#: four-character code plus one trailing character. The parser never derives a
#: canonical code *from* this shape (no suffix stripping, no numeric
#: conversion); it only recognizes that a cell is code-shaped, so that a page
#: whose structure we do not recognize fails as PARSE_FAILED rather than
#: masquerading as "this ticker is not listed".
DISPLAYED_CODE_PATTERN = re.compile(r"^[0-9A-Z]{5}$")

#: Header labels that identify the JPX foreign-stock listed-issues table. Both
#: must be present in one header row before any data row is read: a table we
#: cannot label is never treated as a partial foreign-stock list.
FOREIGN_LIST_CODE_LABEL = "コード"

#: The label JPX publishes a trading unit under, on both the foreign-stock
#: listed-issues table and the domestic trading-unit rule page.
TRADING_UNIT_LABEL = "売買単位"

#: The published domestic trading-unit rule, written as prose rather than as a
#: labelled cell. Used together with the labelled extractions below: every
#: extraction that matches must agree on one value, or the parse fails.
DOMESTIC_TRADING_UNIT_PROSE_PATTERN = re.compile(
    r"売買単位(?:は|：|:)\s*(?:原則として)?(\d{1,3}(?:,\d{3})*)\s*株"
)


def parse_stock_search(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_LISTED_COMPANY -> TSE listing facts for one candidate.

    Reads the candidate-specific 東証上場会社情報 search result. Listing is a
    binary fact read straight off the response body: passing a code as the
    search string is never itself evidence that the code is listed.

    The displayed code is matched as ``context.ticker + "0"`` and nothing
    else -- no substring, no ``startswith``, no numeric conversion -- so an
    unrelated but similar code can never satisfy a candidate.

    This parser deliberately distinguishes two failures the TSE Listing Gate
    must not confuse: a page with no recognizable search-result rows at all
    is ``PARSE_FAILED`` (we do not understand the page), while a recognizable
    result page that simply does not carry this code is ``NOT_FOUND`` (the
    code is not listed). Neither is ever silently downgraded to a per-ticker
    exclusion, and there is no ``.T`` suffix guessing anywhere.

    Product classification is **not** decided here: this parser publishes the
    market segment verbatim and nothing else. ``security_type`` is composed
    from several sources by deterministic business logic (see
    :mod:`src.security_type`), never by a single parser.
    """
    if not context.ticker:
        return parse_failed("JPX_LISTED_COMPANY requires a target ticker")
    if not CANONICAL_CODE_PATTERN.fullmatch(context.ticker):
        return parse_failed(
            f"{context.ticker!r} is not a canonical four-character candidate code"
        )
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    result_rows = [
        row
        for row in table_rows(page.text)
        if row and DISPLAYED_CODE_PATTERN.fullmatch(row[0])
    ]
    if not result_rows:
        return parse_failed(
            "no JPX stock-search result rows found on the source page"
        )

    expected_code = context.ticker + "0"
    rows = [row for row in result_rows if row[0] == expected_code]
    if not rows:
        return ParseResult(status="NOT_FOUND", reason_codes=("TICKER_NOT_LISTED_ON_PAGE",))
    if len({tuple(row) for row in rows}) > 1:
        return parse_failed(f"ambiguous listing rows for ticker {context.ticker}")

    row = rows[0]
    if len(row) < 3:
        return parse_failed(f"listing row for {context.ticker} is missing columns")
    company_name, market_segment = row[1], row[2]
    if not company_name:
        return parse_failed(f"listing row for {context.ticker} has no company name")
    if not market_segment:
        return parse_failed(f"listing row for {context.ticker} has no market segment")

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="listed_company_name",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=company_name,
            ),
            ParsedValue(
                field_name="market_segment",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=market_segment,
            ),
        ),
    )


def parse_foreign_stock_list(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_FOREIGN_STOCK_LIST -> the published foreign listed-issue codes.

    A Global Source: one GET, one Global Attempt, one Source Value carrying
    ``ticker=None``, shared by every candidate that consumes it.

    Columns are located from the table's own header labels rather than from
    assumed offsets, and a table whose header cannot be recognized is
    ``PARSE_FAILED``. That refusal is what keeps a partially-rendered or
    restructured page from being read as "a complete foreign-stock list in
    which this candidate does not appear" -- the distinction the domestic
    classification depends on.
    """
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    units: dict[str, str] = {}
    recognized_tables = 0
    for table_html in TABLE_BLOCK_PATTERN.findall(page.text):
        rows = [row for row in table_rows(table_html) if row]
        header_index = _foreign_list_header_index(rows)
        if header_index is None:
            continue
        recognized_tables += 1
        header = rows[header_index]
        code_column = header.index(FOREIGN_LIST_CODE_LABEL)
        unit_column = header.index(TRADING_UNIT_LABEL)
        for row in rows[header_index + 1 :]:
            if len(row) <= max(code_column, unit_column):
                return parse_failed(
                    f"foreign stock list row is missing columns: {row!r}"
                )
            code = row[code_column]
            if not CANONICAL_CODE_PATTERN.fullmatch(code):
                return parse_failed(
                    f"foreign stock list row has a non-canonical code: {code!r}"
                )
            try:
                unit = str(parse_grouped_integer(row[unit_column]))
            except ParseFailed as exc:
                return parse_failed(exc.message)
            if code in units and units[code] != unit:
                return parse_failed(
                    f"foreign stock list publishes conflicting trading units "
                    f"for {code}"
                )
            units[code] = unit

    if not recognized_tables:
        return parse_failed(
            "no JPX foreign stock listed-issues table found on the source page"
        )
    if not units:
        return parse_failed("JPX foreign stock listed-issues table has no data rows")

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="foreign_stock_trading_units",
                ticker=None,
                trading_date=context.trading_date,
                value={code: units[code] for code in sorted(units)},
            ),
        ),
    )


def _foreign_list_header_index(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows):
        if (
            FOREIGN_LIST_CODE_LABEL in row
            and TRADING_UNIT_LABEL in row
            and row.count(FOREIGN_LIST_CODE_LABEL) == 1
            and row.count(TRADING_UNIT_LABEL) == 1
        ):
            return index
    return None


def parse_domestic_trading_unit_rule(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_TRADING_UNIT -> the published domestic-stock trading-unit rule.

    A Global Source: this page states the market-wide rule for domestic
    stocks, so it yields exactly one Global Source Value with ``ticker=None``.
    It is **not** a per-candidate trading-unit page, and this parser never
    produces a candidate-scoped ``share_unit``. Which candidates are entitled
    to consume this rule is decided later, from ``security_type``, by
    :mod:`src.stage_wiring` -- an ETF or a foreign stock never receives it.

    Every extraction strategy that matches must agree on one value. This is
    consensus, not a fallback chain: nothing here picks the first, largest or
    last candidate value, and a page yielding two different numbers is
    ``PARSE_FAILED``.
    """
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    tokens = [
        row[1]
        for row in table_rows(page.text)
        if len(row) >= 2 and row[0] == TRADING_UNIT_LABEL
    ]
    tokens.extend(
        value
        for term, value in definition_pairs(page.text)
        if term == TRADING_UNIT_LABEL
    )
    tokens.extend(DOMESTIC_TRADING_UNIT_PROSE_PATTERN.findall(page.text))

    try:
        units = {str(parse_grouped_integer(token)) for token in tokens}
    except ParseFailed as exc:
        return parse_failed(exc.message)
    if not units:
        return parse_failed("trading_unit: no value found on the source page")
    if len(units) > 1:
        return parse_failed(
            "the domestic trading-unit page publishes conflicting units: "
            + ", ".join(sorted(units))
        )

    unit = units.pop()
    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="trading_unit",
                ticker=None,
                trading_date=context.trading_date,
                value=unit,
                raw_unit="SHARES",
            ),
        ),
    )


def parse_tick_size(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_TICK_SIZE -> the published tick-size table (price band -> tick)."""
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    bands: list[dict[str, str]] = []
    for row in table_rows(page.text):
        if len(row) < 2:
            continue
        upper, tick = row[0], row[1]
        if not _looks_numeric(tick):
            continue
        bands.append({"price_band": upper, "tick_size": tick})
    if not bands:
        return ParseResult(status="NOT_FOUND", reason_codes=("NO_TICK_SIZE_ROWS",))

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="tick_size_table",
                ticker=None,
                trading_date=context.trading_date,
                value=bands,
            ),
        ),
    )


def parse_topix500(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    if not context.ticker:
        return parse_failed("JPX_TOPIX500 requires a target ticker")
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)
    member = any(
        row and row[0] == context.ticker for row in table_rows(page.text)
    )
    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="topix500_membership",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=member,
            ),
        ),
    )


EARNINGS_SCHEDULE_DATE_PATTERN = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


def _iso_date_in(token: str) -> str | None:
    match = EARNINGS_SCHEDULE_DATE_PATTERN.search(token or "")
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_earnings_schedule(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_EARNINGS_SCHEDULE -> the scheduled earnings date for one ticker.

    Deterministic end to end: the row is matched on the ticker code published
    in the first column and the announcement date is read from that row --
    never inferred from prose, never summarized by an AI. An absent ticker is
    a legitimately empty result (no scheduled earnings), which the Event Gate
    reads as "clean", not as missing data.
    """
    if not context.ticker:
        return parse_failed("JPX_EARNINGS_SCHEDULE requires a target ticker")
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    events: list[dict[str, str]] = []
    for row in table_rows(page.text):
        if len(row) < 2 or row[0] != context.ticker:
            continue
        announced = _iso_date_in(row[1])
        if announced is None:
            return parse_failed(
                f"earnings schedule row for {context.ticker} has no parseable "
                f"date: {row[1]!r}"
            )
        entry = {
            "event_date": announced,
            "headline": clean(row[2]) if len(row) > 2 else "",
        }
        if entry not in events:
            events.append(entry)

    if len({event["event_date"] for event in events}) > 1:
        return parse_failed(
            f"ambiguous earnings schedule dates for ticker {context.ticker}"
        )

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="earnings_schedule_events",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=events,
            ),
        ),
    )


def parse_disclosure_index(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """TDnet-style disclosure index pages.

    Deterministic: each row contributes its published timestamp and headline
    text verbatim. Whether a headline is *dangerous* is a later, separate
    judgement; whether a disclosure exists in the window is decided by code
    from these entries.
    """
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    entries: list[dict[str, str]] = []
    for row in table_rows(page.text):
        if len(row) < 2:
            continue
        entries.append({"published": row[0], "headline": clean(row[1])})
    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="disclosure_entries",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=entries,
            ),
        ),
    )


def parse_daily_report(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """JPX_DAILY_REPORT -> official OHLCV audit values for one ticker."""
    if not context.ticker:
        return parse_failed("JPX_DAILY_REPORT requires a target ticker")
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    rows = [row for row in table_rows(page.text) if row and row[0] == context.ticker]
    if not rows:
        return ParseResult(status="NOT_FOUND", reason_codes=("TICKER_ROW_ABSENT",))
    if len({tuple(row) for row in rows}) > 1:
        return parse_failed(f"ambiguous daily report rows for {context.ticker}")

    row = rows[0]
    if len(row) < 6:
        return parse_failed(f"daily report row for {context.ticker} is missing columns")

    field_names = ("open", "high", "low", "close", "volume")
    values: list[ParsedValue] = []
    try:
        for index, field_name in enumerate(field_names, start=1):
            token = row[index]
            parsed = (
                parse_grouped_integer(token)
                if field_name == "volume"
                else parse_grouped_decimal(token)
            )
            values.append(
                ParsedValue(
                    field_name=field_name,
                    ticker=context.ticker,
                    trading_date=context.trading_date,
                    value=str(parsed),
                    raw_value=token,
                )
            )
    except ParseFailed as exc:
        return parse_failed(exc.message)
    return ParseResult(status="FOUND", values=tuple(values))


def _looks_numeric(token: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?", token.strip()))
