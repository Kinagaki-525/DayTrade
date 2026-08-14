"""Deterministic parsers for JPX source pages."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.source_parsers.base import ParseContext, ParseResult, ParsedValue, parse_failed
from src.source_parsers.decode import DecodeError, decode_source_page
from src.source_parsers.html import clean, table_rows
from src.source_parsers.numeric import (
    ParseFailed,
    parse_grouped_decimal,
    parse_grouped_integer,
    unambiguous,
)


ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The current JPX official non-business-day calendar publishes each
#: holiday's date as ``YYYY/MM/DD（曜）`` -- the parenthesized weekday
#: character is display-only and is never used for business-day judgement,
#: only the digits are.
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

        year_dates: list[str] = []
        for row in table_rows(table_html):
            if not row:
                continue
            match = JPX_SLASH_DATE_PATTERN.search(row[0])
            if match is None:
                # A header row ("日付" / "名称") or other non-date row.
                continue
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

        if not year_dates:
            return parse_failed(
                f"{year}年 section's holiday table has no recognizable holiday rows"
            )

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
    """JPX_LISTED_COMPANY -> TSE listing facts for one ticker.

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


def parse_trading_unit(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    tokens = [
        row[1]
        for row in table_rows(page.text)
        if len(row) >= 2 and row[0] == "売買単位"
    ]
    try:
        token = unambiguous(tokens, field_name="trading_unit")
        unit = parse_grouped_integer(token)
    except ParseFailed as exc:
        return parse_failed(exc.message)

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="trading_unit",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=str(unit),
                raw_value=token,
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
