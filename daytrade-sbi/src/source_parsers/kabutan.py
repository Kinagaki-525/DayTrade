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
    return ParseResult(status="FOUND", values=tuple(values))


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
