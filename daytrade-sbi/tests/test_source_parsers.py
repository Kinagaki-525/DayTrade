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


def _context(
    source_id: str,
    ticker: str | None = None,
    *,
    previous_trading_date: str | None = None,
) -> ParseContext:
    return ParseContext(
        source_id=source_id,
        trading_date=TRADING_DATE,
        ticker=ticker,
        content_type="text/html; charset=utf-8",
        previous_trading_date=previous_trading_date,
    )


# --------------------------------------------------------------- registry ---


def test_every_matrix_source_has_a_registered_parser():
    verify_all_source_parser_bindings(MATRIX)
    for source_id in DEFINITIONS:
        assert source_id in SOURCE_PARSER_NAMES


def test_parser_mismatch_is_a_hard_error():
    definition = dict(DEFINITIONS["YAHOO_JP_QUOTE"])
    definition["acquisition"] = dict(definition["acquisition"], parser_id="jpx.calendar")
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
        pages.yahoo_history_page(),
        DEFINITIONS["YAHOO_JP_HISTORY"],
        _context("YAHOO_JP_HISTORY", "7203", previous_trading_date="2026-08-11"),
    )
    assert result.status == "FOUND"
    assert {value.field_name for value in result.values} == {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "previous_close",
        "previous_high",
    }
    assert all(value.trading_date == TRADING_DATE for value in result.values)
    close = next(v for v in result.values if v.field_name == "close")
    assert close.value == "1050"
    # previous_close/previous_high come from the row whose own parsed date
    # exactly equals context.previous_trading_date (here 2026-08-11, matching
    # the fixture's second row) -- resolved by the caller from verified JPX
    # calendar evidence, never guessed by the parser itself.
    previous_close = next(v for v in result.values if v.field_name == "previous_close")
    previous_high = next(v for v in result.values if v.field_name == "previous_high")
    assert previous_close.value == "1000"
    assert previous_high.value == "1010"


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


# ----------------------------------------------------- FIX-PR13 discovery ----
#
# The 2026-08-27 production discovery lost 5 of 50 gainers and 3 of 50 most
# traded names, because the ranking matcher only accepted 4-DIGIT .T symbols:
# post-2024 alphanumeric codes (278A) and the Fukuoka / Sapporo symbols an
# ALL_MARKETS ranking legitimately publishes never matched. Discovery must
# take them all; whether a non-TSE candidate survives is the TSE Listing
# Batch Gate's decision downstream, not this parser's.


def _ranking(page: bytes):
    return yahoo_jp.parse_ranking(
        page,
        DEFINITIONS["YAHOO_JP_VOLUME_RANKING"],
        _context("YAHOO_JP_VOLUME_RANKING"),
    )


def _ranking_value(result, field_name: str):
    return next(value.value for value in result.values if value.field_name == field_name)


@pytest.mark.parametrize(
    ("symbol", "ticker"),
    [
        ("7203.T", "7203"),  # numeric TSE
        ("123A.T", "123A"),  # alphanumeric TSE
        ("4567.F", "4567"),  # Fukuoka
        ("8901.S", "8901"),  # Sapporo
    ],
)
def test_discovery_accepts_every_published_exchange_symbol(symbol, ticker):
    """The canonical ticker is the code with the exchange suffix removed."""
    result = _ranking(pages.yahoo_ranking_page_from_symbols((symbol,)))

    assert result.status == "FOUND"
    assert _ranking_value(result, "ranking_tickers") == [ticker]
    assert _ranking_value(result, "ranking_rows") == [
        {"ticker": ticker, "company_name": "ExampleA Corporation", "rank": 1}
    ]


def test_discovery_keeps_published_order_across_mixed_symbols():
    symbols = ("7203.T", "123A.T", "4567.F", "8901.S")
    result = _ranking(pages.yahoo_ranking_page_from_symbols(symbols))

    assert _ranking_value(result, "ranking_tickers") == ["7203", "123A", "4567", "8901"]
    assert [row["rank"] for row in _ranking_value(result, "ranking_rows")] == [1, 2, 3, 4]


def test_discovery_does_not_double_count_the_forum_link_of_a_row():
    """Every row publishes both /quote/<symbol> and /quote/<symbol>/forum."""
    page = pages.yahoo_ranking_page_from_symbols(("278A.T",))

    assert b"/forum" in page
    assert _ranking_value(_ranking(page), "ranking_tickers") == ["278A"]


def test_discovery_mixed_top50_yields_all_fifty_tickers():
    symbols = pages.mixed_top50_symbols()
    result = _ranking(pages.yahoo_mixed_top50_ranking_page())

    tickers = _ranking_value(result, "ranking_tickers")
    assert len(tickers) == 50
    assert tickers == [symbol.partition(".")[0] for symbol in symbols]


def test_ranking_company_name_is_never_the_rank_cell():
    """The regression itself: rank cells were stored as company names."""
    rows = _ranking_value(_ranking(pages.yahoo_mixed_top50_ranking_page()), "ranking_rows")

    assert len(rows) == 50
    for row in rows:
        assert row["company_name"] != str(row["rank"])
        assert row["company_name"].startswith("Example")
        assert row["company_name"].endswith("Corporation")


def test_ranking_company_name_falls_back_to_the_ticker_when_absent():
    """No usable display name is a fallback to the ticker, never a guess."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body><table><tbody>"
        "<tr><td>1</td>"
        '<td><a href="https://finance.yahoo.co.jp/quote/278A.T">278A</a></td>'
        "<td>1,234</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")

    assert _ranking_value(_ranking(page), "ranking_rows") == [
        {"ticker": "278A", "company_name": "278A", "rank": 1}
    ]


def test_tse_quote_and_history_accept_an_alphanumeric_tse_code():
    quote = yahoo_jp.parse_quote_turnover(
        pages.yahoo_quote_page(ticker="123A"),
        DEFINITIONS["YAHOO_JP_QUOTE"],
        _context("YAHOO_JP_QUOTE", "123A"),
    )
    assert quote.status == "FOUND"

    history = yahoo_jp.parse_history(
        pages.yahoo_history_page(ticker="123A"),
        DEFINITIONS["YAHOO_JP_HISTORY"],
        _context("YAHOO_JP_HISTORY", "123A"),
    )
    assert history.status == "FOUND"


@pytest.mark.parametrize("exchange", ["F", "S"])
def test_tse_quote_ownership_never_accepts_a_non_tse_page(exchange):
    """Quote / History are TSE-only sources: .F / .S is cross-contamination."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        f'<a href="https://finance.yahoo.co.jp/quote/1234.{exchange}">quote</a>'
        "<dl><dt>売買代金</dt><dd>1,234,567</dd></dl>"
        "</body></html>"
    ).encode("utf-8")

    result = yahoo_jp.parse_quote_turnover(
        page,
        DEFINITIONS["YAHOO_JP_QUOTE"],
        _context("YAHOO_JP_QUOTE", "1234"),
    )

    assert result.status == "PARSE_FAILED"


# ---------------------------------------------------------------- kabutan ----


def test_kabutan_history_parses_the_requested_date():
    result = kabutan.parse_history(
        pages.kabutan_history_page(),
        DEFINITIONS["KABUTAN_HISTORY"],
        _context("KABUTAN_HISTORY", "7203", previous_trading_date="2026-08-11"),
    )
    assert result.status == "FOUND"
    assert next(v for v in result.values if v.field_name == "high").value == "1100"
    assert next(v for v in result.values if v.field_name == "previous_close").value == "1000"
    assert next(v for v in result.values if v.field_name == "previous_high").value == "1010"


def test_history_without_calendar_evidence_never_derives_previous_fields():
    """context.previous_trading_date is None -- e.g. JPX_CALENDAR evidence was
    unavailable this run -- so previous_close/previous_high must not be
    derived at all, from any row, no matter how plausible it looks."""
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

    result = kabutan.parse_history(
        pages.kabutan_history_page(), DEFINITIONS["KABUTAN_HISTORY"], _context("KABUTAN_HISTORY", "7203")
    )
    assert result.status == "FOUND"
    assert {value.field_name for value in result.values} == {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }


def test_history_ignores_a_row_that_is_not_exactly_the_expected_previous_date():
    """A row one day off the resolved previous trading date (e.g. the fixture's
    2026-08-11 row, when the calendar says the previous trading day was
    2026-08-10) must never be used -- no nearest-earlier-row fallback."""
    result = yahoo_jp.parse_history(
        pages.yahoo_history_page(),
        DEFINITIONS["YAHOO_JP_HISTORY"],
        _context("YAHOO_JP_HISTORY", "7203", previous_trading_date="2026-08-10"),
    )
    assert result.status == "FOUND"
    assert {value.field_name for value in result.values} == {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }


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
    assert calendar.status == "FOUND"
    non_business_days = next(
        v for v in calendar.values if v.field_name == "non_business_days"
    )
    covered_years = next(
        v for v in calendar.values if v.field_name == "calendar_covered_years"
    )
    assert non_business_days.value == ["2026-01-01", "2026-01-12"]
    # calendar_covered_years is derived from the YYYY年 section heading, not
    # from the dates it happens to list, so an unlisted month (August) still
    # falls inside a confirmed-covered year.
    assert covered_years.value == ["2026"]

    ticks = jpx.parse_tick_size(
        pages.jpx_tick_size_page(), DEFINITIONS["JPX_TICK_SIZE"], _context("JPX_TICK_SIZE")
    )
    assert ticks.status == "FOUND"
    assert ticks.values[0].value[0] == {"price_band": "3000以下", "tick_size": "1"}


def test_jpx_calendar_production_slash_date_format():
    """The real JPX holiday table publishes YYYY/MM/DD（曜）, not YYYY年M月D日;
    the weekday character is display-only and never drives the parse."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "<tr><td>2026/08/11（火）</td><td>山の日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "FOUND"
    non_business_days = next(v for v in result.values if v.field_name == "non_business_days")
    assert non_business_days.value == ["2026-01-01", "2026-08-11"]


def test_jpx_calendar_invalid_date_is_parse_failed():
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/13/40（木）</td><td>存在しない日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_date_outside_any_year_section_is_parse_failed():
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<p>2026/08/11（火）山の日</p>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_date_mismatched_with_its_section_year_is_parse_failed():
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2027/01/01（金）</td><td>元日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_no_year_section_is_not_found():
    result = jpx.parse_calendar(
        pages.jpx_calendar_page_unparseable(),
        DEFINITIONS["JPX_CALENDAR"],
        _context("JPX_CALENDAR"),
    )
    assert result.status == "NOT_FOUND"


# ------------------------------------------------- FIX-R2-001D: coverage ----


def test_jpx_calendar_bare_year_heading_cannot_create_coverage():
    """A year heading with no table at all must not become "covered"."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2>"
        "</body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status != "FOUND"


def test_jpx_calendar_empty_year_table_cannot_create_coverage():
    """A year heading followed by a header-only, zero-row table must not
    become "covered" either."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr>"
        "</thead><tbody></tbody></table>"
        "</body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status != "FOUND"


def test_jpx_calendar_malformed_row_in_year_table_is_parse_failed():
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead>"
        "<tbody><tr><td>not a date at all</td><td>謎の休日</td></tr></tbody></table>"
        "</body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    # No recognizable holiday row in the table -> the whole section (and
    # therefore the whole parse) fails, the same as an empty table.
    assert result.status != "FOUND"


def test_jpx_calendar_truncated_table_is_parse_failed():
    """A table that never closes before the next heading / end of page is a
    structural failure, not an empty-but-valid section."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td>"
        "</body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status != "FOUND"


def test_jpx_calendar_one_broken_year_section_fails_the_whole_calendar():
    """A genuinely valid 2026 section next to a broken, empty 2027 section:
    the whole parse fails rather than silently reporting 2026 as covered
    and 2027 as merely uncovered."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "</tbody></table>"
        "<h2>2027年</h2><table><tbody></tbody></table>"
        "</body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_only_a_structurally_valid_section_creates_coverage():
    """The positive counterpart: a proper heading + header row + >=1 valid
    data row does create coverage for that year."""
    page = pages.jpx_calendar_page((("2026-08-11", "山の日"),))
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "FOUND"
    covered_years = next(v for v in result.values if v.field_name == "calendar_covered_years")
    non_business_days = next(v for v in result.values if v.field_name == "non_business_days")
    assert covered_years.value == ["2026"]
    assert non_business_days.value == ["2026-08-11"]


# ------------------------------------------------- FIX-R2-001E: row rigor ---


def test_jpx_calendar_valid_row_mixed_with_malformed_row_is_parse_failed():
    """Test A: one well-formed row plus one malformed row in the same
    table -- the malformed row must not be silently dropped."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "<tr><td>not a date</td><td>謎の休日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_row_with_only_a_date_column_is_parse_failed():
    """Test B: a data row with just one column (date, no name) fails --
    never treated as "close enough"."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_unrecognized_header_with_real_looking_dates_is_parse_failed():
    """Test C: the table body has genuine-looking YYYY/MM/DD（曜） rows, but
    the header does not read as 日付/名称 -- this is not confidently a JPX
    holiday table, regardless of what the body contains."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>Col A</th><th>Col B</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


def test_jpx_calendar_normal_jpx_shaped_table_is_found():
    """Test D: a fully well-formed JPX-shaped table (header + >=1 valid
    two-column row, all in the section's own year) is FOUND with coverage."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "<tr><td>2026/08/11（火）</td><td>山の日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "FOUND"
    covered_years = next(v for v in result.values if v.field_name == "calendar_covered_years")
    non_business_days = next(v for v in result.values if v.field_name == "non_business_days")
    assert covered_years.value == ["2026"]
    assert non_business_days.value == ["2026-01-01", "2026-08-11"]


def test_jpx_calendar_valid_row_mixed_with_cross_year_row_is_parse_failed():
    """Test E: a normal in-year row alongside a cross-year row in the same
    2026年 section table."""
    page = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><thead><tr><th>日付</th><th>名称</th></tr></thead><tbody>"
        "<tr><td>2026/01/01（木）</td><td>元日</td></tr>"
        "<tr><td>2027/01/01（金）</td><td>元日</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_calendar(page, DEFINITIONS["JPX_CALENDAR"], _context("JPX_CALENDAR"))
    assert result.status == "PARSE_FAILED"


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
