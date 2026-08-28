"""Stage 1 JPX source contracts: listing, product class and trading unit.

Covers the three sources Stage 1 acquires from JPX and the deterministic
business logic that turns them into a Stage 1 verdict:

* JPX_LISTED_COMPANY -- candidate-specific TSE listing evidence;
* JPX_FOREIGN_STOCK_LIST -- the Global foreign listed-issues evidence;
* JPX_TRADING_UNIT -- the Global domestic trading-unit rule.

The property under test throughout is the separation the 2026-08-27 production
failure exposed: *listing confirmation* is not *strategy eligibility*. An ETF
that is listed is FOUND, and is rejected afterwards by an evidence-backed
Stage 1 check -- never by a listing NOT_FOUND and never by a silent exclusion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.candidate_research import _apply_stage1_to_research
from src.market import MarketDataRecord
from src.security_type import (
    DOMESTIC_COMMON_STOCK,
    FOREIGN_STOCK,
    classify_security_type,
)
from src.source_acquisition import STAGE_SOURCE_IDS, acquire_stage
from src.source_fetch import TransportResult
from src.source_matrix import GLOBAL_SOURCE_IDS, load_source_matrix, source_by_id
from src.source_parsers.base import ParseContext
from src.source_parsers.registry import (
    SOURCE_PARSER_NAMES,
    verify_all_source_parser_bindings,
)
from src.source_parsers import jpx
from src.stage_wiring import reflect_market_data
from tests import source_page_fixtures as pages


TRADING_DATE = pages.TRADING_DATE
TARGET_DATE = pages.TRADING_DATE
CUTOFF = "2026-08-12T20:00:00+09:00"
MATRIX = load_source_matrix()
DEFINITIONS = source_by_id(MATRIX)

LISTED_COMPANY = DEFINITIONS["JPX_LISTED_COMPANY"]
FOREIGN_STOCK_LIST = DEFINITIONS["JPX_FOREIGN_STOCK_LIST"]
TRADING_UNIT = DEFINITIONS["JPX_TRADING_UNIT"]


def _context(ticker: str | None = "7203") -> ParseContext:
    return ParseContext(
        source_id="JPX_LISTED_COMPANY",
        trading_date=TRADING_DATE,
        ticker=ticker,
        content_type="text/html; charset=utf-8",
    )


def _values(result) -> dict[str, object]:
    return {value.field_name: value.value for value in result.values}


# ------------------------------------------------- JPX_LISTED_COMPANY ---
# T01-T08: the candidate-specific TSE listing search.


def test_a_listed_company_is_found_with_its_name_and_segment():
    """T01: the 7203-shaped search result yields name + market segment."""
    result = jpx.parse_stock_search(
        pages.jpx_stock_search_page("7203"), LISTED_COMPANY, _context("7203")
    )
    assert result.status == "FOUND"
    assert _values(result) == {
        "listed_company_name": "Example Motor Corporation",
        "market_segment": "プライム",
    }


def test_an_alphanumeric_canonical_code_is_matched_exactly():
    """T02: 285A is an ordinary candidate; its displayed code is 285A0."""
    result = jpx.parse_stock_search(
        pages.jpx_stock_search_page("285A", company_name="Example Holdings"),
        LISTED_COMPANY,
        _context("285A"),
    )
    assert result.status == "FOUND"
    assert _values(result)["listed_company_name"] == "Example Holdings"


def test_only_the_candidate_code_plus_zero_is_accepted():
    """T03: the displayed code must equal candidate + "0" exactly."""
    page = pages.jpx_stock_search_page("7203")
    for wrong in ("720", "72031", "7204"):
        result = jpx.parse_stock_search(page, LISTED_COMPANY, _context(wrong))
        assert result.status in {"NOT_FOUND", "PARSE_FAILED"}, wrong
        assert result.status != "FOUND"


@pytest.mark.parametrize("displayed", ["72031", "17203", "7203", "720300"])
def test_a_similar_but_different_displayed_code_is_never_accepted(displayed):
    """T04: no substring, prefix or numeric-conversion match, ever."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><table><tbody>'
        f"<tr><td>{displayed}</td><td>Example Co.</td><td>プライム</td></tr>"
        "<tr><td>99990</td><td>Other Co.</td><td>プライム</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_stock_search(page, LISTED_COMPANY, _context("7203"))
    assert result.status == "NOT_FOUND"
    assert result.reason_codes == ("TICKER_NOT_LISTED_ON_PAGE",)


def test_a_recognizable_result_page_without_the_code_is_not_found():
    """T05: zero matches on an understood page means "not listed"."""
    result = jpx.parse_stock_search(
        pages.jpx_stock_search_empty_page(), LISTED_COMPANY, _context("7203")
    )
    assert result.status == "NOT_FOUND"
    assert result.reason_codes == ("TICKER_NOT_LISTED_ON_PAGE",)


def test_an_unrecognizable_page_is_a_parse_failure_not_a_not_found():
    """A page we do not understand must never read as "not listed".

    This is the exact confusion behind the 2026-08-27 production failure: the
    search *form* page carried no result rows and every candidate came back
    NOT_FOUND / TICKER_NOT_LISTED_ON_PAGE.
    """
    result = jpx.parse_stock_search(
        b'<html><head><meta charset="utf-8"></head><body><form/></body></html>',
        LISTED_COMPANY,
        _context("7203"),
    )
    assert result.status == "PARSE_FAILED"


def test_conflicting_rows_for_one_code_are_a_parse_failure():
    """T06: two different rows claiming the same code resolve to nothing."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><table><tbody>'
        "<tr><td>72030</td><td>Example Motor Corporation</td><td>プライム</td></tr>"
        "<tr><td>72030</td><td>Other Corporation</td><td>スタンダード</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_stock_search(page, LISTED_COMPANY, _context("7203"))
    assert result.status == "PARSE_FAILED"


@pytest.mark.parametrize("segment,name", [("", "Example Co."), ("プライム", "")])
def test_an_empty_segment_or_name_is_a_parse_failure(segment, name):
    """T07: a half-filled row is never FOUND."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><table><tbody>'
        f"<tr><td>72030</td><td>{name}</td><td>{segment}</td></tr>"
        "</tbody></table></body></html>"
    ).encode("utf-8")
    result = jpx.parse_stock_search(page, LISTED_COMPANY, _context("7203"))
    assert result.status == "PARSE_FAILED"


def test_an_etf_that_is_listed_is_found_like_any_other_issue():
    """T08: an ETF is listed; the parser never rejects it for being an ETF."""
    result = jpx.parse_stock_search(
        pages.jpx_stock_search_page(
            "1306",
            company_name="Example TOPIX Listed Index Fund",
            market_segment="ETF",
        ),
        LISTED_COMPANY,
        _context("1306"),
    )
    assert result.status == "FOUND"
    assert _values(result)["market_segment"] == "ETF"


def test_the_search_string_alone_is_never_evidence_of_a_listing():
    """Passing a code to the search box proves nothing about the response."""
    result = jpx.parse_stock_search(
        pages.jpx_stock_search_empty_page(), LISTED_COMPANY, _context("6758")
    )
    assert result.status != "FOUND"


def test_a_non_canonical_candidate_code_is_rejected_before_matching():
    result = jpx.parse_stock_search(
        pages.jpx_stock_search_page("7203"), LISTED_COMPANY, _context("72031")
    )
    assert result.status == "PARSE_FAILED"


# --------------------------------------------- JPX_FOREIGN_STOCK_LIST ---


def _foreign_context() -> ParseContext:
    return ParseContext(
        source_id="JPX_FOREIGN_STOCK_LIST",
        trading_date=TRADING_DATE,
        ticker=None,
        content_type="text/html; charset=utf-8",
    )


def _foreign(page: bytes):
    return jpx.parse_foreign_stock_list(page, FOREIGN_STOCK_LIST, _foreign_context())


def test_all_three_sections_yield_one_complete_global_value():
    """T07: Prime + Standard + Growth all recognized -> FOUND."""
    result = _foreign(pages.jpx_foreign_stock_list_page())
    assert result.status == "FOUND"
    assert len(result.values) == 1
    value = result.values[0]
    assert value.ticker is None
    assert value.field_name == "foreign_stock_trading_units"
    assert value.value == {"1773": "1000", "9399": "1", "4875": "100"}


@pytest.mark.parametrize(
    "sections",
    [
        ("プライム市場外国株",),
        ("プライム市場外国株", "スタンダード市場外国株"),
        ("プライム市場外国株", "グロース市場外国株"),
        ("スタンダード市場外国株", "グロース市場外国株"),
        (),
    ],
)
def test_a_partial_foreign_page_is_never_found(sections):
    """T08/T09/T10: coverage must be proven for every published section.

    A partial page is the dangerous case: reading it as complete would turn
    "we never saw this section" into "this issue is not foreign".
    """
    result = _foreign(pages.jpx_foreign_stock_list_partial_page(sections))
    assert result.status == "PARSE_FAILED"


def test_a_malformed_header_in_one_section_fails_the_whole_page():
    """T11: one bad section is not skipped, it fails the page."""
    body = (
        pages._foreign_stock_section("プライム市場外国株", (("1773", "1,000"),))
        + pages._foreign_stock_section(
            "スタンダード市場外国株",
            (("9399", "1"),),
            header="<tr><th>会社名</th><th>銘柄コード</th><th>単元</th></tr>",
        )
        + pages._foreign_stock_section("グロース市場外国株", (("4875", "100"),))
    )
    page = f'<html><head><meta charset="utf-8"></head><body>{body}</body></html>'
    assert _foreign(page.encode("utf-8")).status == "PARSE_FAILED"


def test_a_malformed_row_in_one_section_fails_the_whole_page():
    """T11 (rows): a row we cannot parse is never silently skipped."""
    body = (
        pages._foreign_stock_section("プライム市場外国株", (("1773", "1,000"),))
        + pages._foreign_stock_section("スタンダード市場外国株", (("9399", "n/a"),))
        + pages._foreign_stock_section("グロース市場外国株", (("4875", "100"),))
    )
    page = f'<html><head><meta charset="utf-8"></head><body>{body}</body></html>'
    assert _foreign(page.encode("utf-8")).status == "PARSE_FAILED"


def test_a_duplicated_section_heading_is_ambiguous():
    """T12: two Prime sections -- the section-to-table mapping is ambiguous."""
    body = (
        pages._foreign_stock_section("プライム市場外国株", (("1773", "1,000"),))
        + pages._foreign_stock_section("プライム市場外国株", (("1234", "100"),))
        + pages._foreign_stock_section("スタンダード市場外国株", (("9399", "1"),))
        + pages._foreign_stock_section("グロース市場外国株", (("4875", "100"),))
    )
    page = f'<html><head><meta charset="utf-8"></head><body>{body}</body></html>'
    assert _foreign(page.encode("utf-8")).status == "PARSE_FAILED"


def test_two_tables_under_one_section_heading_are_ambiguous():
    """T12: which table belongs to the section cannot be decided."""
    body = (
        pages._foreign_stock_section(
            "プライム市場外国株", (("1773", "1,000"),), tables=2
        )
        + pages._foreign_stock_section("スタンダード市場外国株", (("9399", "1"),))
        + pages._foreign_stock_section("グロース市場外国株", (("4875", "100"),))
    )
    page = f'<html><head><meta charset="utf-8"></head><body>{body}</body></html>'
    assert _foreign(page.encode("utf-8")).status == "PARSE_FAILED"


def test_conflicting_foreign_trading_units_are_a_parse_failure():
    """T13: the same code published with two different units."""
    result = _foreign(
        pages.jpx_foreign_stock_list_page(
            prime=(("1773", "1,000"),), standard=(("1773", "100"),)
        )
    )
    assert result.status == "PARSE_FAILED"


def test_a_section_without_data_rows_is_a_parse_failure():
    result = _foreign(pages.jpx_foreign_stock_list_page(standard=()))
    assert result.status == "PARSE_FAILED"


def test_the_section_contents_are_never_hardcoded():
    """Issue counts and codes come from the page, not from the parser."""
    result = _foreign(
        pages.jpx_foreign_stock_list_page(
            prime=(("1111", "10"), ("2222", "50")),
            standard=(("3333", "500"),),
            growth=(("444A", "1"),),
        )
    )
    assert result.status == "FOUND"
    assert result.values[0].value == {
        "1111": "10",
        "2222": "50",
        "3333": "500",
        "444A": "1",
    }


# ------------------------------------------------- JPX_TRADING_UNIT ---


def _unit_context() -> ParseContext:
    return ParseContext(
        source_id="JPX_TRADING_UNIT",
        trading_date=TRADING_DATE,
        ticker=None,
        content_type="text/html; charset=utf-8",
    )


def _unit(page: bytes):
    return jpx.parse_domestic_trading_unit_rule(page, TRADING_UNIT, _unit_context())


@pytest.mark.parametrize(
    "assertions",
    [("traded",), ("unified",), ("traded", "unified")],
)
def test_each_official_assertion_yields_the_published_unit(assertions):
    """T01/T02/T03: either published sentence, or both agreeing -> FOUND/100."""
    result = _unit(pages.jpx_trading_unit_page("100", assertions=assertions))
    assert result.status == "FOUND"
    assert len(result.values) == 1
    assert result.values[0].ticker is None
    assert result.values[0].field_name == "trading_unit"
    assert result.values[0].value == "100"


def test_the_unit_is_read_from_the_evidence_not_hardcoded():
    """The parser never emits a constant 100: change the page, change the value."""
    result = _unit(pages.jpx_trading_unit_page("1,000"))
    assert result.status == "FOUND"
    assert result.values[0].value == "1000"


def test_disagreeing_official_assertions_are_a_parse_failure():
    """T04: two recognized assertions publishing different units."""
    page = (
        '<html><head><meta charset="utf-8"></head><body>'
        "<p>内国株では100株単位で取引されています。</p>"
        "<p>内国株の売買単位を1,000株へ統一しました。</p>"
        "</body></html>"
    ).encode("utf-8")
    assert _unit(page).status == "PARSE_FAILED"


def test_a_stray_year_or_bare_unit_mention_is_never_read_as_the_rule():
    """T05: only a recognized assertion counts -- not any number on the page."""
    page = (
        '<html><head><meta charset="utf-8"></head><body>'
        "<p>2018年10月1日に制度が変わりました。</p>"
        "<p>100株ごとの取扱いについては各社にお問い合わせください。</p>"
        "<table><tbody><tr><td>売買単位</td><td>100</td></tr></tbody></table>"
        "</body></html>"
    ).encode("utf-8")
    assert _unit(page).status == "PARSE_FAILED"


def test_a_page_without_the_rule_is_a_parse_failure_not_a_default():
    """T06: no recognized assertion -> PARSE_FAILED, never a defaulted 100."""
    result = _unit(
        b'<html><head><meta charset="utf-8"></head><body><p>x</p></body></html>'
    )
    assert result.status == "PARSE_FAILED"


# ------------------------------------------- security_type classification ---


@pytest.mark.parametrize("segment", ["プライム", "スタンダード", "グロース"])
def test_a_common_stock_segment_absent_from_the_foreign_list_is_domestic(segment):
    assert (
        classify_security_type(
            market_segment=segment,
            candidate_code="7203",
            foreign_issue_codes=["1773", "9399"],
        )
        == DOMESTIC_COMMON_STOCK
    )


def test_a_common_stock_segment_present_in_the_foreign_list_is_foreign():
    assert (
        classify_security_type(
            market_segment="プライム",
            candidate_code="1773",
            foreign_issue_codes=["1773"],
        )
        == FOREIGN_STOCK
    )


@pytest.mark.parametrize(
    "segment,expected",
    [
        ("ETF", "ETF"),
        ("ETN", "ETN"),
        ("REIT", "REIT"),
        ("インフラファンド", "INFRASTRUCTURE_FUND"),
    ],
)
def test_a_non_common_stock_segment_classifies_without_the_foreign_list(
    segment, expected
):
    assert (
        classify_security_type(
            market_segment=segment,
            candidate_code="1306",
            foreign_issue_codes=None,
        )
        == expected
    )


def test_an_unavailable_foreign_list_leaves_a_common_stock_unclassified():
    """T27: no fallback to DOMESTIC_COMMON_STOCK when the evidence is missing."""
    assert (
        classify_security_type(
            market_segment="プライム",
            candidate_code="7203",
            foreign_issue_codes=None,
        )
        is None
    )


@pytest.mark.parametrize("segment", ["", None, "TSE Prime", "未知区分", "プライム市場"])
def test_an_unknown_market_segment_is_never_domestic(segment):
    """T28: an unrecognized segment is unclassified, never a default."""
    assert (
        classify_security_type(
            market_segment=segment,
            candidate_code="7203",
            foreign_issue_codes=[],
        )
        is None
    )


# ----------------------------------------------- Stage 1 market_data wiring ---


def _stage1_result(tmp_path, tickers, segments, *, foreign=(("1773", "1,000"),)):
    """Run a real STAGE1 acquisition over synthetic JPX pages."""
    bodies = {}
    for ticker in tickers:
        bodies[
            "https://www2.jpx.co.jp/tseHpFront/StockSearch.do"
            f"?method=topsearch&topSearchStr={ticker}"
        ] = pages.jpx_stock_search_page(
            ticker,
            company_name=f"Example {ticker} Corporation",
            market_segment=segments[ticker],
        )
    bodies[
        "https://www.jpx.co.jp/equities/products/foreign/issues/index.html"
    ] = pages.jpx_foreign_stock_list_page(foreign)
    bodies[
        "https://www.jpx.co.jp/equities/trading/domestic/03.html"
    ] = pages.jpx_trading_unit_page()

    calls: list[str] = []

    def transport(url: str) -> TransportResult:
        calls.append(url)
        return TransportResult(0, 200, "text/html; charset=utf-8", bodies[url])

    result = acquire_stage(
        "STAGE1",
        target_date=TARGET_DATE,
        trading_date=TRADING_DATE,
        research_cutoff=CUTOFF,
        tickers=list(tickers),
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        source_ids=("JPX_LISTED_COMPANY", "JPX_FOREIGN_STOCK_LIST", "JPX_TRADING_UNIT"),
    )
    return result, calls


def _records(result) -> dict[str, dict]:
    payload = reflect_market_data(
        "STAGE1", result, existing=None, trading_date=TRADING_DATE
    )
    return {record["ticker"]: record for record in payload["records"]}


def test_the_global_trading_unit_rule_is_one_attempt_not_one_per_candidate(tmp_path):
    """T25: JPX_TRADING_UNIT is a Global Source: 1 GET, 1 Attempt, ticker=None."""
    assert "JPX_TRADING_UNIT" in GLOBAL_SOURCE_IDS
    result, calls = _stage1_result(
        tmp_path, ("7203", "6758"), {"7203": "プライム", "6758": "プライム"}
    )
    attempts = [a for a in result.attempts if a["source_id"] == "JPX_TRADING_UNIT"]
    assert len(attempts) == 1
    assert attempts[0]["candidate_code"] is None
    assert attempts[0]["values"][0]["ticker"] is None
    assert calls.count("https://www.jpx.co.jp/equities/trading/domestic/03.html") == 1


def test_the_global_foreign_stock_list_is_one_attempt_not_one_per_candidate(tmp_path):
    """T26: same Global Source contract for JPX_FOREIGN_STOCK_LIST."""
    assert "JPX_FOREIGN_STOCK_LIST" in GLOBAL_SOURCE_IDS
    result, calls = _stage1_result(
        tmp_path, ("7203", "6758"), {"7203": "プライム", "6758": "プライム"}
    )
    attempts = [a for a in result.attempts if a["source_id"] == "JPX_FOREIGN_STOCK_LIST"]
    assert len(attempts) == 1
    assert attempts[0]["candidate_code"] is None
    assert (
        calls.count(
            "https://www.jpx.co.jp/equities/products/foreign/issues/index.html"
        )
        == 1
    )


def test_listing_is_fetched_once_per_candidate(tmp_path):
    """T16: a candidate-specific source spends exactly one GET per candidate."""
    _, calls = _stage1_result(
        tmp_path, ("7203", "6758"), {"7203": "プライム", "6758": "プライム"}
    )
    for ticker in ("7203", "6758"):
        assert (
            calls.count(
                "https://www2.jpx.co.jp/tseHpFront/StockSearch.do"
                f"?method=topsearch&topSearchStr={ticker}"
            )
            == 1
        )
    assert len(calls) == 4  # 2 candidates + 2 global pages


def test_re_running_the_same_stage_issues_no_further_gets(tmp_path):
    """T17: the same Logical Attempt is reused, never re-fetched."""
    _stage1_result(tmp_path, ("7203",), {"7203": "プライム"})
    _, calls = _stage1_result(tmp_path, ("7203",), {"7203": "プライム"})
    assert calls == []


def test_only_a_domestic_common_stock_receives_the_100_share_rule(tmp_path):
    """T31: the domestic rule reaches domestic common stocks and no one else."""
    result, _ = _stage1_result(tmp_path, ("7203",), {"7203": "プライム"})
    record = _records(result)["7203"]
    assert record["security_type"] == DOMESTIC_COMMON_STOCK
    assert record["share_unit"] == "100"


@pytest.mark.parametrize(
    "ticker,segment,expected_type",
    [
        ("1306", "ETF", "ETF"),
        ("1773", "プライム", FOREIGN_STOCK),
        ("7203", "未知区分", None),
    ],
)
def test_no_share_unit_is_written_for_anything_but_a_domestic_common_stock(
    tmp_path, ticker, segment, expected_type
):
    """T10/T11/T32: an ETF, a foreign stock and an unclassified candidate all
    keep share_unit=None. 100 is never defaulted onto them."""
    result, _ = _stage1_result(tmp_path, (ticker,), {ticker: segment})
    record = _records(result)[ticker]
    assert record["security_type"] == expected_type
    assert record["share_unit"] is None


def test_an_unavailable_foreign_list_blocks_domestic_classification(tmp_path):
    """T27 end to end: no foreign evidence, no domestic classification, no unit."""
    result, _ = _stage1_result(
        tmp_path,
        ("7203",),
        {"7203": "プライム"},
        foreign=(),  # header-only table -> PARSE_FAILED
    )
    record = _records(result)["7203"]
    assert record["security_type"] is None
    assert record["share_unit"] is None


# ----------------------------------------------------- Stage 1 eligibility ---


def _research(ticker: str = "7203") -> dict:
    from tests.factories import make_candidate_research

    return make_candidate_research(
        ticker,
        data_status="NOT_STARTED",
        stage1_status="NOT_STARTED",
        stage2_status="NOT_STARTED",
        context_research_status="NOT_STARTED",
    )


def _record_with(
    *,
    market_segment: str = "プライム",
    foreign_units: dict[str, str] | None = None,
    drop_foreign_source: bool = False,
    share_unit: int | None = 100,
    security_type: str | None = "__derive__",
    drop_refs: tuple[str, ...] = (),
) -> tuple[MarketDataRecord, set[str], dict[str, str]]:
    """A market_data record whose security_type follows from its own evidence.

    Nothing here injects a classification: the market segment and the foreign
    listed-issues map are set on the Source Records, and ``security_type`` is
    derived from them the way the Stage Wiring would. ``security_type`` is only
    overridden explicitly to model tampering.
    """
    from tests.factories import make_market_record

    base = make_market_record(previous_high="400", tick_size="1").as_dict()
    sources = []
    for source in base["sources"]:
        if source["field_name"] == "market_segment":
            source = {**source, "value": market_segment}
        if source["field_name"] == "foreign_stock_trading_units":
            if drop_foreign_source:
                continue
            source = {
                **source,
                "value": (
                    foreign_units
                    if foreign_units is not None
                    else {"1773": "1000", "9399": "1"}
                ),
            }
        sources.append(source)
    base["sources"] = sources
    base["market"] = market_segment
    base["share_unit"] = share_unit

    derived = classify_security_type(
        market_segment=market_segment,
        candidate_code=str(base["ticker"]),
        foreign_issue_codes=(
            None
            if drop_foreign_source
            else list(
                foreign_units
                if foreign_units is not None
                else {"1773": "1000", "9399": "1"}
            )
        ),
    )
    base["security_type"] = derived if security_type == "__derive__" else security_type

    record = MarketDataRecord.from_dict(base)
    valid_refs = {
        str(source.source_ref)
        for source in record.sources
        if source.source_ref and str(source.source_ref) not in drop_refs
    }
    source_ids_by_ref = {
        str(source.source_ref): str(source.source_id)
        for source in record.sources
        if source.source_ref
    }
    return record, valid_refs, source_ids_by_ref


def _apply(record, valid_refs, source_ids_by_ref):
    from src.config import load_yaml

    config = load_yaml(Path("config/strategy.yaml"))
    return _apply_stage1_to_research(
        _research(str(record.ticker)),
        record,
        valid_source_refs=valid_refs,
        source_ids_by_ref=source_ids_by_ref,
        config=config,
    )


def _security_type_check(research: dict) -> dict:
    return next(
        check
        for check in research["stage1_checks"]
        if check["check_id"] == "security_type"
    )


FOREIGN_REF = "JPX_FOREIGN_STOCK_LIST:1234:foreign_stock_trading_units"
LISTING_REF = "JPX_LISTED_COMPANY:1234:market_segment"


def test_a_domestic_common_stock_carries_both_listing_and_foreign_refs():
    """T14: absence from the complete foreign list is what makes it domestic,
    so both Source Records must be referenced by the check."""
    record, refs, by_ref = _record_with(market_segment="プライム")
    assert record.security_type == DOMESTIC_COMMON_STOCK
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "PASSED"
    check = _security_type_check(research)
    assert set(check["source_refs"]) == {LISTING_REF, FOREIGN_REF}


def test_a_foreign_stock_is_rejected_with_both_refs():
    """T15: present in the foreign list -> FOREIGN_STOCK -> unsupported."""
    record, refs, by_ref = _record_with(
        market_segment="プライム", foreign_units={"1234": "100"}
    )
    assert record.security_type == FOREIGN_STOCK
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "REJECTED"
    assert research["reason_codes"] == ["SECURITY_TYPE_UNSUPPORTED"]
    assert set(_security_type_check(research)["source_refs"]) == {
        LISTING_REF,
        FOREIGN_REF,
    }


@pytest.mark.parametrize(
    "segment,expected",
    [
        ("ETF", "ETF"),
        ("ETN", "ETN"),
        ("REIT", "REIT"),
        ("インフラファンド", "INFRASTRUCTURE_FUND"),
    ],
)
def test_an_explicit_product_segment_is_rejected_on_listing_evidence_alone(
    segment, expected
):
    """T16: an ETF is decided by the listing alone -- the foreign list is not
    part of that classification and must not be claimed as evidence."""
    record, refs, by_ref = _record_with(market_segment=segment, share_unit=None)
    assert record.security_type == expected
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "REJECTED"
    assert research["reason_codes"] == ["SECURITY_TYPE_UNSUPPORTED"]
    assert _security_type_check(research)["source_refs"] == [LISTING_REF]


def test_an_explicit_product_segment_still_rejects_without_the_foreign_list():
    """A foreign-list failure must not break an ETF rejection."""
    record, refs, by_ref = _record_with(
        market_segment="ETF", share_unit=None, drop_foreign_source=True
    )
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "REJECTED"
    assert research["reason_codes"] == ["SECURITY_TYPE_UNSUPPORTED"]


def test_an_unsupported_security_type_reject_is_source_backed():
    from src.stage1 import source_backed_stage1_reject

    for segment in ("ETF", "プライム"):
        record, refs, by_ref = _record_with(
            market_segment=segment,
            foreign_units={"1234": "100"} if segment == "プライム" else None,
            share_unit=None,
        )
        research = _apply(record, refs, by_ref)
        assert research["stage1_status"] == "REJECTED"
        assert source_backed_stage1_reject(
            research,
            valid_source_refs=refs,
            valid_source_attempt_ids=set(),
            source_ids_by_evidence_id=by_ref,
        ), segment


def test_a_missing_foreign_list_blocks_a_common_stock_from_passing():
    """T17: Prime + no foreign evidence -> unclassified -> Stage 1 stays open."""
    record, refs, by_ref = _record_with(
        market_segment="プライム", drop_foreign_source=True
    )
    assert record.security_type is None
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "NOT_STARTED"


def test_an_unknown_market_segment_neither_passes_nor_rejects():
    """T28 at the eligibility layer: Stage 1 stays open, it does not guess."""
    record, refs, by_ref = _record_with(market_segment="未知区分")
    assert record.security_type is None
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "NOT_STARTED"


@pytest.mark.parametrize("tampered", [DOMESTIC_COMMON_STOCK, "ETF", None])
def test_a_security_type_that_contradicts_its_evidence_decides_nothing(tampered):
    """T18: record.security_type is re-derived, never trusted as a string.

    The evidence here says FOREIGN_STOCK. Any other value written into
    market_data -- including the one that would let the candidate through --
    leaves Stage 1 open rather than deciding anything.
    """
    record, refs, by_ref = _record_with(
        market_segment="プライム",
        foreign_units={"1234": "100"},
        security_type=tampered,
    )
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "NOT_STARTED"


def test_a_domestic_stock_missing_its_foreign_list_ref_cannot_pass():
    """T19: the classification's evidence must be in the Source Ledger."""
    record, refs, by_ref = _record_with(
        market_segment="プライム", drop_refs=(FOREIGN_REF,)
    )
    assert record.security_type == DOMESTIC_COMMON_STOCK
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "NOT_STARTED"


def test_a_foreign_stock_missing_its_foreign_list_ref_is_not_a_valid_reject():
    """T20: an unbacked FOREIGN_STOCK reject is not emitted at all."""
    record, refs, by_ref = _record_with(
        market_segment="プライム",
        foreign_units={"1234": "100"},
        drop_refs=(FOREIGN_REF,),
    )
    assert record.security_type == FOREIGN_STOCK
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "NOT_STARTED"


def test_security_type_is_checked_before_share_unit():
    """T21: an ETF handed a share_unit of 100 still rejects on security_type."""
    record, refs, by_ref = _record_with(market_segment="ETF", share_unit=100)
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "REJECTED"
    assert research["reason_codes"] == ["SECURITY_TYPE_UNSUPPORTED"]


def test_a_non_100_share_unit_still_rejects_with_the_existing_reason_code():
    """T12: SHARE_UNIT_NOT_100 is unchanged and still reachable."""
    record, refs, by_ref = _record_with(market_segment="プライム", share_unit=1000)
    research = _apply(record, refs, by_ref)
    assert research["stage1_status"] == "REJECTED"
    assert research["reason_codes"] == ["SHARE_UNIT_NOT_100"]


# ------------------------------------------------ Source Matrix / bindings ---


def test_stage1_acquires_exactly_the_four_jpx_sources():
    assert STAGE_SOURCE_IDS["STAGE1"] == (
        "JPX_CALENDAR",
        "JPX_LISTED_COMPANY",
        "JPX_FOREIGN_STOCK_LIST",
        "JPX_TRADING_UNIT",
    )


def test_every_source_matrix_parser_binding_is_registered():
    """T19: the live matrix's parser_id bindings all resolve."""
    verify_all_source_parser_bindings(MATRIX)
    assert LISTED_COMPANY["acquisition"]["parser_id"] == "jpx.stock_search"
    assert FOREIGN_STOCK_LIST["acquisition"]["parser_id"] == "jpx.foreign_stock_list"
    assert TRADING_UNIT["acquisition"]["parser_id"] == "jpx.domestic_trading_unit_rule"


def test_the_listed_company_audit_binding_is_untouched():
    """T33: PR #15 does not touch the AUDIT source or its parser."""
    audit = DEFINITIONS["JPX_LISTED_COMPANY_AUDIT"]
    assert (
        audit["url_template"]
        == "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
    )
    assert audit["acquisition"]["parser_id"] == "jpx.listed_company"
    assert SOURCE_PARSER_NAMES["JPX_LISTED_COMPANY_AUDIT"] == "jpx.listed_company"


def test_the_listing_search_host_is_an_allowed_production_domain():
    """T20: the new host reaches the derived Production allowlist."""
    from src.claude_runtime_security import derive_expected_domains
    from src.network_policy import ALLOWED_HOSTS

    assert "www2.jpx.co.jp" in ALLOWED_HOSTS
    assert "www2.jpx.co.jp" in derive_expected_domains()


def test_no_wildcard_ever_enters_the_allowed_host_set():
    from src.network_policy import ALLOWED_HOSTS

    assert not any("*" in host for host in ALLOWED_HOSTS)
