"""Fixed ``source_id`` -> parser mapping.

The mapping is static. A source definition whose declared ``parser`` does not
match this registry is a hard error raised **before any network call is
made**, so a mis-wired Source Matrix can never cause a fetch whose bytes we
would not know how to parse deterministically.
"""

from __future__ import annotations

from typing import Any

from src.source_parsers import jpx, kabutan, yahoo_jp
from src.source_parsers.base import ParseContext, ParseResult, SourceParser


class ParserRegistryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


#: parser name -> callable
PARSERS: dict[str, SourceParser] = {
    "jpx.calendar": jpx.parse_calendar,
    "jpx.listed_company": jpx.parse_listed_company,
    "jpx.trading_unit": jpx.parse_trading_unit,
    "jpx.tick_size": jpx.parse_tick_size,
    "jpx.topix500": jpx.parse_topix500,
    "jpx.disclosure_index": jpx.parse_disclosure_index,
    "jpx.earnings_schedule": jpx.parse_earnings_schedule,
    "jpx.daily_report": jpx.parse_daily_report,
    "yahoo_jp.ranking": yahoo_jp.parse_ranking,
    "yahoo_jp.history": yahoo_jp.parse_history,
    "yahoo_jp.quote_turnover": yahoo_jp.parse_quote_turnover,
    "yahoo_jp.news": yahoo_jp.parse_news,
    "kabutan.history": kabutan.parse_history,
    "kabutan.news": kabutan.parse_news,
}

#: source_id -> parser name. Fixed; never resolved at runtime from the page.
SOURCE_PARSER_NAMES: dict[str, str] = {
    "JPX_CALENDAR": "jpx.calendar",
    "JPX_LISTED_COMPANY": "jpx.listed_company",
    "JPX_LISTED_COMPANY_AUDIT": "jpx.listed_company",
    "JPX_TRADING_UNIT": "jpx.trading_unit",
    "JPX_TICK_SIZE": "jpx.tick_size",
    "JPX_TOPIX500": "jpx.topix500",
    "JPX_TDNET": "jpx.disclosure_index",
    "JPX_EARNINGS_SCHEDULE": "jpx.earnings_schedule",
    "JPX_DAILY_REPORT": "jpx.daily_report",
    "YAHOO_JP_VOLUME_RANKING": "yahoo_jp.ranking",
    "YAHOO_JP_GAIN_RANKING": "yahoo_jp.ranking",
    "YAHOO_JP_HISTORY": "yahoo_jp.history",
    "YAHOO_JP_QUOTE": "yahoo_jp.quote_turnover",
    "YAHOO_JP_NEWS": "yahoo_jp.news",
    "KABUTAN_HISTORY": "kabutan.history",
    "KABUTAN_NEWS": "kabutan.news",
    "COMPANY_IR": "yahoo_jp.news",
    "COMPANY_IR_DISCLOSURE": "yahoo_jp.news",
}


def parser_name_for_source(source_id: str) -> str:
    name = SOURCE_PARSER_NAMES.get(source_id)
    if name is None:
        raise ParserRegistryError(
            "SOURCE_PARSER_NOT_REGISTERED",
            f"no deterministic parser is registered for source_id {source_id!r}",
        )
    return name


def get_parser(source_id: str) -> SourceParser:
    return PARSERS[parser_name_for_source(source_id)]


def verify_source_parser_binding(source_definition: dict[str, Any]) -> str:
    """Verify a Source Matrix v3 acquisition block against this registry.

    Called before acquisition begins. Raises on any mismatch.
    """
    source_id = source_definition.get("source_id")
    expected = parser_name_for_source(str(source_id))
    acquisition = source_definition.get("acquisition")
    if not isinstance(acquisition, dict):
        raise ParserRegistryError(
            "SOURCE_ACQUISITION_BLOCK_MISSING",
            f"{source_id}: source definition has no acquisition block",
        )
    declared = acquisition.get("parser_id")
    if declared != expected:
        raise ParserRegistryError(
            "SOURCE_PARSER_MISMATCH",
            f"{source_id}: acquisition.parser_id is {declared!r}, "
            f"registry requires {expected!r}",
        )
    return expected


def verify_all_source_parser_bindings(source_matrix: dict[str, Any]) -> None:
    for definition in source_matrix.get("sources", []):
        verify_source_parser_binding(definition)


def parse_source_page(
    raw: bytes,
    source_definition: dict[str, Any],
    context: ParseContext,
) -> ParseResult:
    """Dispatch to the registered parser after verifying the binding."""
    verify_source_parser_binding(source_definition)
    parser = get_parser(str(source_definition["source_id"]))
    return parser(raw, source_definition, context)
