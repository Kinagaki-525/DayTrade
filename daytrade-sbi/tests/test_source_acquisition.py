"""Stage-aware acquisition contracts: gates, budgets and scoping."""

from __future__ import annotations

import pytest

from src.contracts import validate_json_document
from src.source_acquisition import (
    GLOBAL_SOURCE_IDS,
    STAGE_SOURCE_IDS,
    AcquisitionError,
    acquire_stage,
    attempt_id_for,
    load_ledger,
    merge_ledger,
    resolve_url,
    write_ledger,
)
from src.source_fetch import TransportResult
from src.source_matrix import load_source_matrix, source_by_id
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
CUTOFF = "2026-08-12T20:00:00+09:00"
MATRIX = load_source_matrix()
DEFINITIONS = source_by_id(MATRIX)

REGISTRY = {
    "registry_schema_version": 1,
    "approval_policy": {"human_approved_only": True, "auto_discovery_allowed": False},
    "issuers": [
        {
            "ticker": "7203",
            "issuer_name": "Example Corp",
            "approved_hosts": ["ir.example.co.jp"],
            "approved_by": "human-reviewer",
            "approved_at": "2026-08-12",
        }
    ],
}


def _transport(body: bytes, status: int = 200):
    def _run(url: str) -> TransportResult:
        return TransportResult(0, status, "text/html; charset=utf-8", body)

    return _run


def _stage(stage: str, tmp_path, body: bytes, tickers=("7203",), status=200, source_ids=None):
    return acquire_stage(
        stage,
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=list(tickers),
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=_transport(body, status),
        issuer_registry=REGISTRY,
        source_ids=source_ids,
    )


def test_all_five_stages_are_defined():
    assert set(STAGE_SOURCE_IDS) == {
        "DISCOVERY",
        "STAGE1",
        "STAGE2",
        "TURNOVER",
        "EVENT",
    }


def test_unknown_stage_is_a_hard_error(tmp_path):
    with pytest.raises(AcquisitionError) as exc_info:
        _stage("STAGE9", tmp_path, pages.yahoo_quote_page())
    assert exc_info.value.code == "UNKNOWN_ACQUISITION_STAGE"


def test_malformed_ticker_is_rejected_before_any_fetch(tmp_path):
    with pytest.raises(AcquisitionError) as exc_info:
        _stage("TURNOVER", tmp_path, pages.yahoo_quote_page(), tickers=("72031",))
    assert exc_info.value.code == "CANDIDATE_TICKER_MALFORMED"


def test_discovery_is_global_and_needs_no_candidates(tmp_path):
    result = _stage("DISCOVERY", tmp_path, pages.yahoo_ranking_page(), tickers=())
    assert len(result.attempts) == 2
    assert all(attempt["candidate_code"] is None for attempt in result.attempts)
    assert result.attempts[0]["values"][0]["field_name"] == "ranking_tickers"


def test_global_sources_are_fetched_once_not_per_candidate(tmp_path):
    result = _stage(
        "STAGE2",
        tmp_path,
        pages.jpx_tick_size_page(),
        tickers=("7203", "6758"),
        source_ids=("JPX_TICK_SIZE",),
    )
    assert "JPX_TICK_SIZE" in GLOBAL_SOURCE_IDS
    assert len(result.attempts) == 1


def test_tse_listing_gate_is_batch_all_or_nothing(tmp_path):
    """One unlisted candidate closes the gate for the whole batch."""
    result = _stage(
        "STAGE1",
        tmp_path,
        pages.jpx_listed_company_page(ticker="7203"),
        tickers=("7203", "6758"),
        source_ids=("JPX_LISTED_COMPANY",),
    )
    assert result.gate_status == "CLOSED"
    assert result.gate_reason_codes == ["TSE_LISTING_BATCH_GATE_FAILED"]
    # no candidate was silently dropped: both were attempted and recorded
    assert {a["candidate_code"] for a in result.attempts} == {"7203", "6758"}


def test_tse_listing_gate_opens_when_every_candidate_is_listed(tmp_path):
    result = _stage(
        "STAGE1",
        tmp_path,
        pages.jpx_listed_company_page(ticker="7203"),
        tickers=("7203",),
        source_ids=("JPX_LISTED_COMPANY",),
    )
    assert result.gate_status == "OPEN"
    assert result.gate_reason_codes == []


def test_event_stage_only_touches_the_supplied_candidates(tmp_path):
    result = _stage(
        "EVENT",
        tmp_path,
        pages.yahoo_quote_page(),
        tickers=("7203",),
        source_ids=("YAHOO_JP_NEWS",),
    )
    assert {a["candidate_code"] for a in result.attempts} == {"7203"}


def test_issuer_url_template_resolves_only_from_the_registry():
    url = resolve_url(
        DEFINITIONS["COMPANY_IR_DISCLOSURE"], ticker="7203", issuer_registry=REGISTRY
    )
    assert url == "https://ir.example.co.jp/ir/"


def test_issuer_url_for_an_unregistered_ticker_fails(tmp_path):
    from src.network_policy import NetworkPolicyError

    with pytest.raises(NetworkPolicyError) as exc_info:
        resolve_url(
            DEFINITIONS["COMPANY_IR_DISCLOSURE"], ticker="9999", issuer_registry=REGISTRY
        )
    assert exc_info.value.code == "ISSUER_DOMAIN_NOT_APPROVED"


def test_unregistered_issuer_never_reaches_the_transport(tmp_path):
    def exploding(url):  # pragma: no cover - must not run
        raise AssertionError("no fetch may happen for an unapproved issuer domain")

    result = acquire_stage(
        "EVENT",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["9999"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=exploding,
        issuer_registry=REGISTRY,
        source_ids=("COMPANY_IR_DISCLOSURE",),
    )
    attempt = result.attempts[0]
    assert attempt["status"] == "ACCESS_FAILED"
    assert attempt["notes"] == ["ISSUER_DOMAIN_NOT_APPROVED"]


def test_attempt_id_is_the_request_budget_key():
    first = attempt_id_for(
        source_id="YAHOO_JP_QUOTE",
        candidate_code="7203",
        url="https://finance.yahoo.co.jp/quote/7203.T",
        target_date=TARGET_DATE,
        research_cutoff=CUTOFF,
    )
    same = attempt_id_for(
        source_id="YAHOO_JP_QUOTE",
        candidate_code="7203",
        url="https://finance.yahoo.co.jp/quote/7203.T",
        target_date=TARGET_DATE,
        research_cutoff=CUTOFF,
    )
    other_date = attempt_id_for(
        source_id="YAHOO_JP_QUOTE",
        candidate_code="7203",
        url="https://finance.yahoo.co.jp/quote/7203.T",
        target_date="2026-08-13",
        research_cutoff=CUTOFF,
    )
    assert first == same
    assert first != other_date


def test_ledger_round_trip_is_schema_valid(tmp_path):
    result = _stage("TURNOVER", tmp_path, pages.yahoo_quote_page())
    path = tmp_path / "sources.json"
    write_ledger(path, result.as_ledger())
    loaded = load_ledger(path)
    validate_json_document(loaded, "sources.schema.json")
    assert loaded["schema_version"] == 3


def test_merging_a_stage_twice_does_not_duplicate_attempts(tmp_path):
    result = _stage("TURNOVER", tmp_path, pages.yahoo_quote_page())
    once = result.as_ledger()
    twice = merge_ledger(once, result.as_ledger())
    assert len(twice["source_attempts"]) == len(once["source_attempts"])
    assert len(twice["sources"]) == len(once["sources"])


def test_ledger_values_carry_provenance_back_to_the_attempt(tmp_path):
    result = _stage("TURNOVER", tmp_path, pages.yahoo_quote_page())
    attempt = result.attempts[0]
    value = result.values[0]
    assert value["source_ref"].startswith(attempt["attempt_id"])
    assert value["ticker"] == "7203"
    assert value["source_status"] == "FOUND"
    assert value["source_url"] == attempt["url"]


def test_not_found_status_produces_no_values(tmp_path):
    result = _stage("TURNOVER", tmp_path, b"", status=404)
    attempt = result.attempts[0]
    assert attempt["status"] == "NOT_FOUND"
    assert attempt["values"] is None
    assert result.values == []
