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


# ------------------------------------------------ FIX-012: keep evidence ---


def test_parse_failure_keeps_the_raw_evidence(tmp_path):
    """HTTP 2xx + raw page stored + parser fails => PARSE_FAILED, and every
    transport/evidence field STAYS on the attempt.

    Stripping them would destroy the audit trail for exactly the failures that
    most need one: we would know a parse failed but not what bytes it failed
    on.
    """
    # A page that is served fine but carries no turnover the parser can read.
    result = _stage("TURNOVER", tmp_path, b"<html><body>7203.T</body></html>")
    attempt = result.attempts[0]

    assert attempt["status"] == "PARSE_FAILED"
    assert attempt["values"] is None
    assert attempt["result_count"] is None

    for kept in (
        "acquisition_method",
        "http_status",
        "content_type",
        "transport_exit_code",
        "source_page_path",
        "source_page_sha256",
        "source_page_size_bytes",
    ):
        assert kept in attempt, f"{kept} must survive a parse failure"

    assert attempt["http_status"] == 200
    assert attempt["acquisition_method"] == "HTTP_GET"
    # The evidence is genuinely on disk and still hashes to what was recorded.
    stored = tmp_path / attempt["source_page_path"]
    assert stored.is_file()
    import hashlib

    assert hashlib.sha256(stored.read_bytes()).hexdigest() == attempt[
        "source_page_sha256"
    ]


def test_schema_v3_allows_found_shaped_fields_with_parse_failed(tmp_path):
    """Schema v3 must permit the FOUND-shaped evidence fields alongside
    status=PARSE_FAILED -- otherwise FIX-012 would be unrepresentable."""
    result = _stage("TURNOVER", tmp_path, b"<html><body>7203.T</body></html>")
    ledger = result.as_ledger()
    assert ledger["source_attempts"][0]["status"] == "PARSE_FAILED"
    validate_json_document(ledger, "sources.schema.json")


def test_access_failure_records_no_evidence(tmp_path):
    """The converse: nothing was stored, so no evidence field is invented."""
    result = _stage("TURNOVER", tmp_path, b"", status=500)
    attempt = result.attempts[0]
    assert attempt["status"] == "ACCESS_FAILED"
    for absent in ("source_page_path", "source_page_sha256", "source_page_size_bytes"):
        assert absent not in attempt


# --------------------------------------------- FIX-R2-002: Request Budget ---


def _counting_transport(body: bytes = b"", status: int = 200, exit_code: int = 0):
    calls = {"count": 0}

    def _run(url: str) -> TransportResult:
        calls["count"] += 1
        return TransportResult(exit_code, status if exit_code == 0 else None, "text/html", body)

    return _run, calls


def _run_stage_twice(tmp_path, transport, *, tickers=("7203",), source_ids=("YAHOO_JP_QUOTE",)):
    """Simulates the real acquire-* CLI: run the stage, persist the ledger,
    reload it, and run the exact same stage again against that ledger."""
    first = acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=list(tickers),
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        issuer_registry=REGISTRY,
        source_ids=source_ids,
    )
    ledger_path = tmp_path / "sources.json"
    write_ledger(ledger_path, first.as_ledger())

    second = acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=list(tickers),
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        issuer_registry=REGISTRY,
        source_ids=source_ids,
        existing_ledger=load_ledger(ledger_path),
    )
    return first, second


@pytest.mark.parametrize(
    "status,exit_code",
    [
        (200, 0),
        (403, 0),
        (404, 0),
        (429, 0),
        (500, 0),
        (None, 28),  # CURL_TIMEOUT_EXIT_CODE
        (None, 7),  # an arbitrary non-zero, non-timeout exit code -> EXECUTION_FAILED
    ],
)
def test_double_run_performs_exactly_one_transport_call(tmp_path, status, exit_code):
    body = pages.yahoo_quote_page() if status == 200 else b""
    transport, calls = _counting_transport(body, status=(status or 200), exit_code=exit_code)
    first, second = _run_stage_twice(tmp_path, transport)
    assert calls["count"] == 1

    first_attempt = first.attempts[0]
    second_attempt = second.attempts[0]
    assert first_attempt["cache_status"] == "MISS"
    assert first_attempt["network_request_performed"] is True
    assert second_attempt["cache_status"] == "MISS"
    assert second_attempt["network_request_performed"] is True
    # Exact Logical Attempt Immutability: the second call returns the SAME
    # attempt, not a re-derived one.
    assert second_attempt["requested_at"] == first_attempt["requested_at"]
    assert second_attempt["retrieved_at"] == first_attempt["retrieved_at"]
    assert second_attempt["request_id"] == first_attempt["request_id"]


def test_parse_failed_double_run_performs_one_get(tmp_path):
    # 200 + a page the turnover parser cannot read -> PARSE_FAILED.
    body = b"<html><body>7203.T no turnover here</body></html>"
    transport, calls = _counting_transport(body, status=200)
    first, second = _run_stage_twice(tmp_path, transport)
    assert calls["count"] == 1
    assert first.attempts[0]["status"] == "PARSE_FAILED"
    assert second.attempts[0]["status"] == "PARSE_FAILED"
    assert second.attempts[0]["source_page_sha256"] == first.attempts[0]["source_page_sha256"]


def test_different_research_cutoff_permits_a_new_get(tmp_path):
    body = pages.yahoo_quote_page()
    transport, calls = _counting_transport(body, status=200)

    first = acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        issuer_registry=REGISTRY,
        source_ids=("YAHOO_JP_QUOTE",),
    )
    ledger_path = tmp_path / "sources.json"
    write_ledger(ledger_path, first.as_ledger())

    second = acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff="2026-08-11T20:00:00+09:00",
        tickers=["7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        issuer_registry=REGISTRY,
        source_ids=("YAHOO_JP_QUOTE",),
        existing_ledger=load_ledger(ledger_path),
    )
    assert calls["count"] == 2
    assert first.attempts[0]["request_id"] != second.attempts[0]["request_id"]


def test_raw_page_tamper_never_triggers_a_refetch(tmp_path):
    body = pages.yahoo_quote_page()
    transport, calls = _counting_transport(body, status=200)
    first, _ = _run_stage_twice(tmp_path, transport)
    assert calls["count"] == 1

    stored_path = tmp_path / first.attempts[0]["source_page_path"]
    stored_path.write_bytes(stored_path.read_bytes() + b"\x00")

    from src.source_fetch import SourceFetchError

    with pytest.raises(SourceFetchError) as excinfo:
        acquire_stage(
            "TURNOVER",
            target_date=TARGET_DATE,
            trading_date=TARGET_DATE,
            research_cutoff=CUTOFF,
            tickers=["7203"],
            run_dir=tmp_path,
            source_matrix=MATRIX,
            transport=transport,
            issuer_registry=REGISTRY,
            source_ids=("YAHOO_JP_QUOTE",),
            existing_ledger=load_ledger(tmp_path / "sources.json"),
        )
    assert excinfo.value.code == "SOURCE_PAGE_HASH_MISMATCH"
    assert calls["count"] == 1


def test_failed_shared_request_performs_one_get_for_three_candidates(tmp_path):
    """Three candidates sharing one EVENT-stage page (JPX_TDNET), the page
    returns HTTP 403: exactly one transport call, three attempts, the
    origin is MISS/network_request_performed=True, the other two are
    HIT/network_request_performed=False, all three ACCESS_FAILED."""
    transport, calls = _counting_transport(b"", status=403)
    result = acquire_stage(
        "EVENT",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["7203", "6758", "9984"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        issuer_registry=REGISTRY,
        source_ids=("JPX_TDNET",),
    )
    assert calls["count"] == 1
    assert len(result.attempts) == 3
    assert all(a["status"] == "ACCESS_FAILED" for a in result.attempts)

    misses = [a for a in result.attempts if a["cache_status"] == "MISS"]
    hits = [a for a in result.attempts if a["cache_status"] == "HIT"]
    assert len(misses) == 1
    assert len(hits) == 2
    assert misses[0]["network_request_performed"] is True
    assert all(h["network_request_performed"] is False for h in hits)
    assert all(h["reused_from_attempt_id"] == misses[0]["attempt_id"] for h in hits)
    assert all(a["request_id"] == misses[0]["request_id"] for a in result.attempts)


def test_merge_ledger_preserves_the_original_immutable_attempt(tmp_path):
    """merge_ledger must never let a later call overwrite an existing
    attempt_id's immutable network facts -- when the incoming addition
    agrees, the original is kept; forging a different addition is an error."""
    from src.source_acquisition import AcquisitionError

    body = pages.yahoo_quote_page()
    transport, _ = _counting_transport(body, status=200)
    first = _stage_with_transport(tmp_path, transport)
    existing = first.as_ledger()

    # Re-running the identical stage produces the identical attempt: merging
    # it back in is a no-op, not a violation.
    merged = merge_ledger(existing, first.as_ledger())
    assert merged["source_attempts"] == existing["source_attempts"]

    # A forged addition claiming a different http_status for the same
    # attempt_id must be rejected, not silently accepted as the new truth.
    forged = {
        "schema_version": 3,
        "target_date": existing["target_date"],
        "sources": [],
        "source_attempts": [
            {**existing["source_attempts"][0], "http_status": 999},
        ],
    }
    with pytest.raises(AcquisitionError) as excinfo:
        merge_ledger(existing, forged)
    assert excinfo.value.code == "SOURCE_ATTEMPT_IMMUTABILITY_VIOLATION"


def _stage_with_transport(tmp_path, transport):
    return acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport,
        issuer_registry=REGISTRY,
        source_ids=("YAHOO_JP_QUOTE",),
    )


# --------------------------------- FIX-R2-002A: Physical Request Trust -----


def test_missing_user_agent_creates_zero_request_records(tmp_path, monkeypatch):
    """DAYTRADE_HTTP_USER_AGENT unset, using the real curl transport: the
    pre-network boundary must reject before reserve_request() ever runs, so
    zero Physical Request Records are created."""
    from src.source_acquisition import acquire_source
    from src.source_fetch import curl_transport

    monkeypatch.delenv("DAYTRADE_HTTP_USER_AGENT", raising=False)

    def _explode(argv, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("transport must never be invoked")

    monkeypatch.setattr("subprocess.run", _explode)

    attempt, values = acquire_source(
        DEFINITIONS["YAHOO_JP_QUOTE"],
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        candidate_code="7203",
        run_dir=tmp_path,
        issuer_registry=REGISTRY,
        transport=curl_transport,
    )
    assert values == []
    assert attempt["cache_status"] == "NOT_CACHEABLE"
    assert attempt["network_request_performed"] is False
    assert attempt["request_id"] is None
    assert not (tmp_path / "network_requests").exists() or not list(
        (tmp_path / "network_requests").iterdir()
    )


def test_invalid_host_creates_zero_request_records(tmp_path):
    """A URL resolving to a host outside the Network Policy allowlist must
    fail before reserve_request(): zero Physical Request Records, one
    NOT_CACHEABLE attempt, and the transport is never invoked."""
    from src.source_acquisition import acquire_source

    bad_definition = dict(DEFINITIONS["YAHOO_JP_QUOTE"])
    bad_definition["url_template"] = "https://evil.example.com/quote/{ticker}.T"

    transport, calls = _counting_transport(b"", status=200)
    attempt, values = acquire_source(
        bad_definition,
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        candidate_code="7203",
        run_dir=tmp_path,
        issuer_registry=REGISTRY,
        transport=transport,
    )
    assert calls["count"] == 0
    assert values == []
    assert attempt["cache_status"] == "NOT_CACHEABLE"
    assert attempt["network_request_performed"] is False
    assert attempt["request_id"] is None
    assert not (tmp_path / "network_requests").exists() or not list(
        (tmp_path / "network_requests").iterdir()
    )


def test_origin_miss_timestamps_equal_request_record_timestamps(tmp_path):
    """FIX-R2-002A section 7/8: the origin MISS attempt's requested_at /
    retrieved_at are literally the Physical Request Record's own
    reserved_at / completed_at -- not a separately generated timestamp."""
    from src.request_budget import list_request_records
    from src.source_acquisition import acquire_source

    transport, _ = _counting_transport(pages.yahoo_quote_page(), status=200)
    attempt, _ = acquire_source(
        DEFINITIONS["YAHOO_JP_QUOTE"],
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        candidate_code="7203",
        run_dir=tmp_path,
        issuer_registry=REGISTRY,
        transport=transport,
    )
    records = list_request_records(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert attempt["cache_status"] == "MISS"
    assert attempt["requested_at"] == record["reserved_at"]
    assert attempt["retrieved_at"] == record["completed_at"]


def test_exact_attempt_reuse_validates_the_physical_request_record(tmp_path):
    """FIX-R2-002A section 19: reusing an Exact Logical Attempt (found in the
    cache from a prior ledger) must not just trust the attempt's own claims
    -- it must load and integrity-validate the Physical Request Record the
    attempt names. A missing record for a MISS/HIT attempt is a hard error."""
    from src.request_budget import network_request_path
    from src.source_acquisition import AcquisitionError, RequestBudgetCache, acquire_source

    transport, calls = _counting_transport(pages.yahoo_quote_page(), status=200)
    definition = DEFINITIONS["YAHOO_JP_QUOTE"]
    first_attempt, _ = acquire_source(
        definition,
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        candidate_code="7203",
        run_dir=tmp_path,
        issuer_registry=REGISTRY,
        transport=transport,
    )
    assert calls["count"] == 1

    # Delete the backing Physical Request Record out from under the cached
    # attempt, simulating a corrupted/lost network_requests/ directory.
    network_request_path(tmp_path, first_attempt["request_id"]).unlink()

    cache = RequestBudgetCache(tmp_path, {"source_attempts": [first_attempt]})
    with pytest.raises(AcquisitionError) as excinfo:
        acquire_source(
            definition,
            target_date=TARGET_DATE,
            trading_date=TARGET_DATE,
            research_cutoff=CUTOFF,
            candidate_code="7203",
            run_dir=tmp_path,
            issuer_registry=REGISTRY,
            transport=transport,
            cache=cache,
        )
    assert excinfo.value.code == "EXACT_ATTEMPT_REQUEST_RECORD_MISSING"
    # The transport must never be invoked again while resolving this reuse.
    assert calls["count"] == 1


# --------------------------------------- FIX-R2-002B: State Exhaustiveness --


def test_schema_v3_rejects_stale_cache_status(tmp_path):
    """Production v3 never mints STALE; the schema must reject it outright."""
    result = _stage("TURNOVER", tmp_path, pages.yahoo_quote_page())
    ledger = result.as_ledger()
    ledger["source_attempts"][0]["cache_status"] = "STALE"
    with pytest.raises(ValueError):
        validate_json_document(ledger, "sources.schema.json")
