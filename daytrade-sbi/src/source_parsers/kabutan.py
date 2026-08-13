"""Deterministic parsers for Kabutan source pages."""

from __future__ import annotations

import re
from typing import Any

from src.source_parsers.base import ParseContext, ParseResult, ParsedValue, parse_failed
from src.source_parsers.decode import DecodeError, decode_source_page
from src.source_parsers.html import table_rows
from src.source_parsers.numeric import (
    ParseFailed,
    parse_grouped_decimal,
    parse_grouped_integer,
)


ISO_DATE_PATTERN = re.compile(r"^(\d{4})[/-](\d{2})[/-](\d{2})$")
_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


def parse_history(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """KABUTAN_HISTORY -> secondary OHLCV for exactly ``context.trading_date``."""
    if not context.ticker:
        return parse_failed("KABUTAN_HISTORY requires a target ticker")
    try:
        page = decode_source_page(raw, context.content_type)
    except DecodeError as exc:
        return parse_failed(exc.message)

    if f"code={context.ticker}" not in page.text and context.ticker not in page.text:
        return parse_failed(f"source page does not belong to ticker {context.ticker}")

    matching = [
        row
        for row in table_rows(page.text)
        if len(row) >= 6 and _iso_date(row[0]) == context.trading_date
    ]
    if not matching:
        return ParseResult(status="NOT_FOUND", reason_codes=("TRADING_DATE_ROW_ABSENT",))
    if len({tuple(row[1:6]) for row in matching}) > 1:
        return parse_failed(f"ambiguous history rows for {context.trading_date}")

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

    try:
        previous_row = _previous_trading_day_row(
            table_rows(page.text), context.trading_date
        )
    except ParseFailed as exc:
        return parse_failed(exc.message)
    if previous_row is not None:
        try:
            values.extend(_previous_day_values(previous_row, context))
        except ParseFailed as exc:
            return parse_failed(exc.message)

    return ParseResult(status="FOUND", values=tuple(values))


def _previous_trading_day_row(
    rows: list[list[str]],
    trading_date: str,
) -> list[str] | None:
    """The row for the trading day immediately preceding ``trading_date``.

    Selected only by explicit date comparison against the dates the page
    itself publishes -- never by row position. See yahoo_jp.parse_history
    for the identical policy on the primary source.
    """
    dated_rows: list[tuple[str, list[str]]] = []
    for row in rows:
        if len(row) < 6:
            continue
        row_date = _iso_date(row[0])
        if row_date is None or row_date >= trading_date:
            continue
        dated_rows.append((row_date, row))
    if not dated_rows:
        return None
    latest_date = max(row_date for row_date, _ in dated_rows)
    candidates = [row for row_date, row in dated_rows if row_date == latest_date]
    if len({tuple(row[1:6]) for row in candidates}) > 1:
        raise ParseFailed(f"ambiguous previous trading day rows for {trading_date}")
    return candidates[0]


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
    """News carries no market numerics; classification happens elsewhere."""
    try:
        page = decode_source_page(raw, context.content_type)
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


def _iso_date(token: str) -> str | None:
    match = ISO_DATE_PATTERN.match(token.strip())
    if match is None:
        return None
    return "-".join(match.groups())
