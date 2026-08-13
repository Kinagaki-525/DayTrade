"""Deterministic parsers for Yahoo! Finance Japan source pages."""

from __future__ import annotations

import re
from typing import Any

from src.source_parsers.base import ParseContext, ParseResult, ParsedValue, parse_failed
from src.source_parsers.decode import DecodeError, decode_source_page
from src.source_parsers.html import hrefs, table_rows, values_for_label
from src.source_parsers.numeric import (
    ParseFailed,
    canonical_turnover_from_raw,
    parse_grouped_decimal,
    parse_grouped_integer,
    unambiguous,
)


QUOTE_HREF_PATTERN = re.compile(
    r"^https://finance\.yahoo\.co\.jp/quote/(\d{4})\.T(?:[/?#].*)?$"
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
        match = QUOTE_HREF_PATTERN.match(href)
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
        ),
    )


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

    return ParseResult(status="FOUND", values=tuple(values))


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
