from __future__ import annotations

from decimal import Decimal

import pytest

from src.ranking import canonical_turnover_yen
from src.source_matrix import load_source_matrix, source_by_id
from src.source_parsers import jpx, kabutan, yahoo_jp
from src.source_parsers.base import ParseContext
from src.source_parsers.decode import DecodeError, decode_source_page
from src.source_parsers.numeric import ParseFailed, parse_grouped_integer, unambiguous
from src.source_parsers.registry import (
    SOURCE_PARSER_NAMES,
    ParserRegistryError,
    get_parser,
    parse_source_page,
    verify_all_source_parser_bindings,
    verify_source_parser_binding,
)
from tests import source_page_fixtures as pages


TRADING_DATE = pages.TRADING_DATE
MATRIX = load_source_matrix()
DEFINITIONS = source_by_id(MATRIX)


def _context(source_id: str, ticker: str | None = None) -> ParseContext:
    return ParseContext(
        source_id=source_id,
        trading_date=TRADING_DATE,
        ticker=ticker,
        content_type="text/html; charset=utf-8",
    )


# --------------------------------------------------------------- registry ---


def test_every_matrix_source_has_a_registered_parser():
    verify_all_source_parser_bindings(MATRIX)
    for source_id in DEFINITIONS:
        assert source_id in SOURCE_PARSER_NAMES


def test_parser_mismatch_is_a_hard_error():
    definition = dict(DEFINITIONS["YAHOO_JP_QUOTE"])
    definition["acquisition"] = dict(definition["acquisition"], parser="jpx.calendar")
    with pytest.raises(ParserRegistryError) as exc_info:
        verify_source_parser_binding(definition)
    assert exc_info.value.code == "SOURCE_PARSER_MISMATCH"


def test_missing_acquisition_block_is_a_hard_error():
    definition = {k: v for k, v in DEFINITIONS["YAHOO_JP_QUOTE"].items() if k != "acquisition"}
    with pytest.raises(ParserRegistryError) as exc_info:
        verify_source_parser_binding(definition)
    assert exc_info.value.code == "SOURCE_ACQUISITION_BLOCK_MISSING"


def test_unregistered_source_id_is_a_hard_error():
    with pytest.raises(ParserRegistryError) as exc_info:
        get_parser("NOT_A_SOURCE")
    assert exc_info.value.code == "SOURCE_PARSER_NOT_REGISTERED"


# ----------------------------------------------------------------- decode ---


def test_decode_prefers_bom_then_header_then_meta():
    assert decode_source_page("﻿あ".encode("utf-8-sig")).encoding_source == "BOM"
    assert (
        decode_source_page("あ".encode("euc_jp"), "text/html; charset=euc-jp").encoding_source
        == "CONTENT_TYPE"
    )
    meta = '<meta charset="shift_jis">あ'.encode("shift_jis")
    assert decode_source_page(meta).encoding_source == "META"
    assert decode_source_page(b"plain").encoding_source == "DEFAULT_UTF8"


def test_undecodable_page_is_parse_failed():
    with pytest.raises(DecodeError) as exc_info:
        decode_source_page(b"\x80\x81 not valid utf-8 \xfe")
    assert exc_info.value.code == "PARSE_FAILED"


# ---------------------------------------------------------------- numerics ---


def test_grouped_integer_requires_valid_comma_grouping():
    assert parse_grouped_integer("1,234,567") == Decimal("1234567")
    for bad in ("1,23,456", "12,34", "1 234", "abc", "1.5"):
        with pytest.raises(ParseFailed):
            parse_grouped_integer(bad)


def test_ambiguous_candidates_never_pick_first_max_or_last():
    with pytest.raises(ParseFailed) as exc_info:
        unambiguous(["1,000", "2,000"], field_name="turnover")
    assert "ambiguous" in exc_info.value.message
    # identical repeated spellings are not ambiguity
    assert unambiguous(["1,000", "1,000"], field_name="turnover") == "1,000"
    with pytest.raises(ParseFailed):
        unambiguous([], field_name="turnover")


# ----------------------------------------------------------------- yahoo ----


def test_quote_turnover_uses_rankings_canonicalization():
    result = yahoo_jp.parse_quote_turnover(
        pages.yahoo_quote_page(), DEFINITIONS["YAHOO_JP_QUOTE"], _context("YAHOO_JP_QUOTE", "7203")
    )
    assert result.status == "FOUND"
    value = result.values[0]
    assert value.field_name == "turnover"
    assert value.raw_unit == "THOUSAND_YEN"
    assert value.raw_value == "1,234,567"
    assert value.canonical_value_yen == str(canonical_turnover_yen("1,234,567"))


def test_quote_turnover_is_parse_failed_when_the_page_is_ambiguous():
    result = yahoo_jp.parse_quote_turnover(
        pages.yahoo_quote_page(extra_turnover="9,999,999"),
        DEFINITIONS["YAHOO_JP_QUOTE"],
        _context("YAHOO_JP_QUOTE", "7203"),
    )
    assert result.status == "PARSE_FAILED"


def test_quote_turnover_rejects_a_page_for_another_ticker():
    result = yahoo_jp.parse_quote_turnover(
        pages.yahoo_quote_page(ticker="6758"),
        DEFINITIONS["YAHOO_JP_QUOTE"],
        _context("YAHOO_JP_QUOTE", "7203"),
    )
    assert result.status == "PARSE_FAILED"


def test_history_parses_only_the_requested_trading_date():
    result = yahoo_jp.parse_history(
        pages.yahoo_history_page(), DEFINITIONS["YAHOO_JP_HISTORY"], _context("YAHOO_JP_HISTORY", "7203")
    )
    assert result.status == "FOUND"
    assert {value.field_name for value in result.values} == {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    assert all(value.trading_date == TRADING_DATE for value in result.values)
    close = next(v for v in result.values if v.field_name == "close")
    assert close.value == "1050"


def test_history_missing_trading_date_is_not_found():
    result = yahoo_jp.parse_history(
        pages.yahoo_history_page(date_text="2026年7月1日"),
        DEFINITIONS["YAHOO_JP_HISTORY"],
        _context("YAHOO_JP_HISTORY", "7203"),
    )
    assert result.status == "NOT_FOUND"


def test_ranking_extracts_tickers_and_no_market_numerics():
    result = yahoo_jp.parse_ranking(
        pages.yahoo_ranking_page(),
        DEFINITIONS["YAHOO_JP_VOLUME_RANKING"],
        _context("YAHOO_JP_VOLUME_RANKING"),
    )
    assert result.status == "FOUND"
    assert result.values[0].field_name == "ranking_tickers"
    assert result.values[0].value == ["7203", "6758", "9984"]


# ---------------------------------------------------------------- kabutan ----


def test_kabutan_history_parses_the_requested_date():
    result = kabutan.parse_history(
        pages.kabutan_history_page(),
        DEFINITIONS["KABUTAN_HISTORY"],
        _context("KABUTAN_HISTORY", "7203"),
    )
    assert result.status == "FOUND"
    assert next(v for v in result.values if v.field_name == "high").value == "1100"


# -------------------------------------------------------------------- jpx ----


def test_jpx_listed_company_parses_a_listing_row():
    result = jpx.parse_listed_company(
        pages.jpx_listed_company_page(),
        DEFINITIONS["JPX_LISTED_COMPANY"],
        _context("JPX_LISTED_COMPANY", "7203"),
    )
    assert result.status == "FOUND"
    assert result.values[0].value == "Example Motor Corporation"


def test_jpx_listed_company_absent_ticker_is_not_found_not_a_guess():
    result = jpx.parse_listed_company(
        pages.jpx_listed_company_page(ticker="6758"),
        DEFINITIONS["JPX_LISTED_COMPANY"],
        _context("JPX_LISTED_COMPANY", "7203"),
    )
    assert result.status == "NOT_FOUND"
    assert result.reason_codes == ("TICKER_NOT_LISTED_ON_PAGE",)


def test_jpx_calendar_and_tick_size_parse():
    calendar = jpx.parse_calendar(
        pages.jpx_calendar_page(), DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR")
    )
    assert calendar.values[0].value == ["2026-01-01", "2026-01-12"]

    ticks = jpx.parse_tick_size(
        pages.jpx_tick_size_page(), DEFINITIONS["JPX_TICK_SIZE"], _context("JPX_TICK_SIZE")
    )
    assert ticks.status == "FOUND"
    assert ticks.values[0].value[0] == {"price_band": "3000以下", "tick_size": "1"}


# ------------------------------------------------------------- dispatch -----


def test_parse_source_page_dispatches_through_the_registry():
    result = parse_source_page(
        pages.yahoo_quote_page(),
        DEFINITIONS["YAHOO_JP_QUOTE"],
        _context("YAHOO_JP_QUOTE", "7203"),
    )
    assert result.status == "FOUND"


def test_parsers_are_pure_functions_of_their_inputs():
    page = pages.yahoo_quote_page()
    context = _context("YAHOO_JP_QUOTE", "7203")
    first = parse_source_page(page, DEFINITIONS["YAHOO_JP_QUOTE"], context).as_dict()
    second = parse_source_page(page, DEFINITIONS["YAHOO_JP_QUOTE"], context).as_dict()
    assert first == second
