"""Deterministic parsers for Yahoo! Finance Japan source pages."""

from __future__ import annotations

import re
from typing import Any

from src.source_parsers.base import ParseContext, ParseResult, ParsedValue, parse_failed
from src.source_parsers.decode import DecodeError, decode_source_page
from src.source_parsers.html import (
    hrefs,
    rows_with_hrefs,
    table_rows,
    values_for_label,
)
from src.source_parsers.numeric import (
    ParseFailed,
    canonical_turnover_from_raw,
    parse_grouped_decimal,
    parse_grouped_integer,
    unambiguous,
)


#: Discovery ranking pages are ALL_MARKETS: the same TOP50 list mixes Tokyo
#: (``.T``), Fukuoka (``.F``) and Sapporo (``.S``) Yahoo symbols, and the
#: 4-character code itself is alphanumeric for post-2024 listings (``278A``).
#: Only these three exchange suffixes are accepted -- each one was observed in
#: real Discovery evidence; unknown suffixes are never guessed into the set.
#: Whether a non-TSE candidate survives is the existing TSE Listing Batch
#: Gate's decision, not this parser's: dropping ``.F`` / ``.S`` here would
#: silently shrink the candidate universe instead.
DISCOVERY_QUOTE_HREF_PATTERN = re.compile(
    r"^https://finance\.yahoo\.co\.jp/quote/([0-9A-Z]{4})\.[TFS](?:[/?#].*)?$"
)

#: The Quote / History sources are TSE-only by construction (their URL
#: templates in the Source Matrix append ``.T``), so their page-ownership
#: matcher stays TSE-only: a ``.F`` / ``.S`` page must never satisfy it.
QUOTE_HREF_PATTERN = re.compile(
    r"^https://finance\.yahoo\.co\.jp/quote/([0-9A-Z]{4})\.T(?:[/?#].*)?$"
)
JP_DATE_PATTERN = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")

#: Yahoo! Finance Japan publishes 売買代金 in thousands of yen. The unit is
#: fixed by this parser: it is never inferred from the page.
TURNOVER_RAW_UNIT = "THOUSAND_YEN"
TURNOVER_LABEL = "売買代金"


def _decode(raw: bytes, context: ParseContext):
    return decode_source_page(raw, context.content_type)


def parse_ranking(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """Discovery ranking: the ordered list of ticker codes, nothing else.

    Discovery deliberately extracts *no* market numerics: the ranking page
    only contributes the candidate universe, and Ranking never consumes
    discovery order.
    """
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    tickers: list[str] = []
    for href in hrefs(page.text):
        match = DISCOVERY_QUOTE_HREF_PATTERN.match(href)
        if match is None:
            continue
        ticker = match.group(1)
        if ticker not in tickers:
            tickers.append(ticker)

    if not tickers:
        return ParseResult(status="NOT_FOUND", reason_codes=("NO_RANKING_ROWS",))

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="ranking_tickers",
                ticker=None,
                trading_date=context.trading_date,
                value=list(tickers),
            ),
            ParsedValue(
                field_name="ranking_rows",
                ticker=None,
                trading_date=context.trading_date,
                value=_ranking_rows(page.text, tickers),
            ),
        ),
    )


def _ranking_company_name(cells: list[str], ticker: str) -> str | None:
    """The display name carried by the row's company cell, or ``None``.

    Yahoo's ranking rows put the company identity in a single cell shaped
    ``<company name><canonical ticker><market><掲示板>`` (e.g.
    ``クボテック(株)7709東証STD掲示板``). The name is therefore the text that
    precedes the canonical ticker inside that cell -- never "the first
    non-empty cell", which is the rank number ("1", "2", ...) and was stored
    as the company name until this was fixed.

    Only cells that actually contain the canonical ticker are eligible, and
    only when they agree on a single name; anything else returns ``None`` so
    the caller falls back to the ticker rather than inventing a name.
    """
    names = {
        prefix
        for cell in cells
        if ticker in cell and (prefix := cell.split(ticker, 1)[0].strip())
    }
    if len(names) != 1:
        return None
    return next(iter(names))


def _ranking_rows(text: str, ordered_tickers: list[str]) -> list[dict[str, Any]]:
    """``[{ticker, company_name, rank}]`` in published ranking order.

    The ticker always comes from the canonical quote anchor, never from cell
    text. The company name comes from the row's company cell (see
    :func:`_ranking_company_name`); when the page carries no usable display
    name the ticker is used, so Discovery never invents one.
    """
    names: dict[str, str] = {}
    for cells, row_hrefs in rows_with_hrefs(text):
        row_tickers = {
            match.group(1)
            for href in row_hrefs
            if (match := DISCOVERY_QUOTE_HREF_PATTERN.match(href)) is not None
        }
        if len(row_tickers) != 1:
            continue
        ticker = next(iter(row_tickers))
        if ticker in names:
            continue
        name = _ranking_company_name(cells, ticker)
        if name is not None:
            names[ticker] = name

    return [
        {
            "ticker": ticker,
            "company_name": names.get(ticker, ticker),
            "rank": index,
        }
        for index, ticker in enumerate(ordered_tickers, start=1)
    ]


def parse_quote_turnover(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """YAHOO_JP_QUOTE -> the actual turnover (売買代金) for one ticker."""
    if not context.ticker:
        return parse_failed("YAHOO_JP_QUOTE requires a target ticker")
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    if not _page_is_for_ticker(page.text, context.ticker):
        return parse_failed(
            f"source page does not belong to ticker {context.ticker}"
        )

    try:
        raw_value = unambiguous(
            values_for_label(page.text, TURNOVER_LABEL),
            field_name="turnover",
        )
        canonical = canonical_turnover_from_raw(raw_value, TURNOVER_RAW_UNIT)
    except ParseFailed as exc:
        return parse_failed(exc.message)

    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="turnover",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=str(canonical),
                raw_value=raw_value,
                raw_unit=TURNOVER_RAW_UNIT,
                canonical_value_yen=str(canonical),
            ),
        ),
    )


_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


def parse_history(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """YAHOO_JP_HISTORY -> OHLCV for exactly ``context.trading_date``."""
    if not context.ticker:
        return parse_failed("YAHOO_JP_HISTORY requires a target ticker")
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)

    if not _page_is_for_ticker(page.text, context.ticker):
        return parse_failed(
            f"source page does not belong to ticker {context.ticker}"
        )

    matching = [
        row
        for row in table_rows(page.text)
        if len(row) >= 6 and _iso_date(row[0]) == context.trading_date
    ]
    if not matching:
        return ParseResult(status="NOT_FOUND", reason_codes=("TRADING_DATE_ROW_ABSENT",))
    if len({tuple(row[1:6]) for row in matching}) > 1:
        return parse_failed(
            f"ambiguous history rows for {context.trading_date}"
        )

    row = matching[0]
    values: list[ParsedValue] = []
    try:
        for index, field_name in enumerate(_OHLCV_FIELDS, start=1):
            token = row[index]
            number = (
                parse_grouped_integer(token)
                if field_name == "volume"
                else parse_grouped_decimal(token)
            )
            values.append(
                ParsedValue(
                    field_name=field_name,
                    ticker=context.ticker,
                    trading_date=context.trading_date,
                    value=str(number),
                    raw_value=token,
                )
            )
    except ParseFailed as exc:
        return parse_failed(exc.message)

    if context.previous_trading_date is not None:
        try:
            previous_row = _exact_previous_trading_day_row(
                table_rows(page.text), context.previous_trading_date, _iso_date
            )
        except ParseFailed as exc:
            return parse_failed(exc.message)
        if previous_row is not None:
            try:
                values.extend(_previous_day_values(previous_row, context))
            except ParseFailed as exc:
                return parse_failed(exc.message)

    return ParseResult(status="FOUND", values=tuple(values))


def _exact_previous_trading_day_row(
    rows: list[list[str]],
    expected_previous_trading_date: str,
    parse_date,
) -> list[str] | None:
    """The row whose own parsed date exactly equals the resolved previous
    JPX trading date -- never the nearest earlier row a page happens to
    publish. ``expected_previous_trading_date`` already accounts for
    weekends and confirmed JPX holidays (see :mod:`src.trading_calendar`),
    so an older, non-adjacent, or holiday row is never eligible, no matter
    how close its date is.

    Zero matching rows is not an error: it means this page's evidence does
    not cover the expected date, so previous_close/previous_high are simply
    not derived this run -- never a fallback to an older row.
    """
    matching = [
        row
        for row in rows
        if len(row) >= 6 and parse_date(row[0]) == expected_previous_trading_date
    ]
    if not matching:
        return None
    if len({tuple(row[1:6]) for row in matching}) > 1:
        raise ParseFailed(
            f"ambiguous previous trading day rows for {expected_previous_trading_date}"
        )
    return matching[0]


def _previous_day_values(row: list[str], context: ParseContext) -> list[ParsedValue]:
    close_token = row[4]
    high_token = row[2]
    previous_close = parse_grouped_decimal(close_token)
    previous_high = parse_grouped_decimal(high_token)
    return [
        ParsedValue(
            field_name="previous_close",
            ticker=context.ticker,
            trading_date=context.trading_date,
            value=str(previous_close),
            raw_value=close_token,
        ),
        ParsedValue(
            field_name="previous_high",
            ticker=context.ticker,
            trading_date=context.trading_date,
            value=str(previous_high),
            raw_value=high_token,
        ),
    ]


def parse_news(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """News pages carry no market numerics; only headline text is extracted.

    Interpretation of the headlines is Event AI Classification's job and
    happens against the stored local page, never here.
    """
    try:
        page = _decode(raw, context)
    except DecodeError as exc:
        return parse_failed(exc.message)
    return ParseResult(
        status="FOUND",
        values=(
            ParsedValue(
                field_name="raw_text_length",
                ticker=context.ticker,
                trading_date=context.trading_date,
                value=len(page.text),
            ),
        ),
    )


def _page_is_for_ticker(text: str, ticker: str) -> bool:
    """Cross-contamination guard: the page must reference this ticker."""
    return any(
        (match := QUOTE_HREF_PATTERN.match(href)) is not None
        and match.group(1) == ticker
        for href in hrefs(text)
    ) or f"{ticker}.T" in text


def _iso_date(token: str) -> str | None:
    match = JP_DATE_PATTERN.match(token.strip())
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"
