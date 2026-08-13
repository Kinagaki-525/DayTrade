"""Raw evidence integrity: the bytes a parser read must still be the bytes
the transport wrote, and a mismatch must never be silently healed."""

from __future__ import annotations

import json

import pytest

from src.market import validate_source_ledger
from src.source_acquisition import acquire_stage, merge_ledger
from src.source_fetch import SourceFetchError, TransportResult, verify_source_page
from src.source_matrix import load_source_matrix
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
CUTOFF = "2026-08-12T20:00:00+09:00"
MATRIX = load_source_matrix()


def turnover_transport(body: bytes = None):
    payload = body if body is not None else pages.yahoo_quote_page()

    def _run(url: str) -> TransportResult:
        return TransportResult(
            exit_code=0,
            http_status=200,
            content_type="text/html; charset=utf-8",
            body=payload,
        )

    return _run


def _acquire_turnover(tmp_path, transport=None):
    return acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=transport or turnover_transport(),
    )


def test_acquisition_records_raw_evidence_provenance(tmp_path):
    result = _acquire_turnover(tmp_path)
    attempt = result.attempts[0]
    assert attempt["status"] == "FOUND"
    assert attempt["acquisition_method"] == "HTTP_GET"
    assert attempt["http_status"] == 200
    assert attempt["transport_exit_code"] == 0
    assert len(attempt["source_page_sha256"]) == 64
    assert attempt["source_page_size_bytes"] > 0
    assert (tmp_path / attempt["source_page_path"]).is_file()


def test_ledger_v3_schema_accepts_the_acquired_ledger(tmp_path):
    from src.contracts import validate_json_document

    result = _acquire_turnover(tmp_path)
    validate_json_document(result.as_ledger(), "sources.schema.json")


def test_one_byte_tamper_of_the_raw_page_is_a_hash_mismatch(tmp_path):
    result = _acquire_turnover(tmp_path)
    attempt = result.attempts[0]
    stored = tmp_path / attempt["source_page_path"]

    data = bytearray(stored.read_bytes())
    data[10] = data[10] ^ 0x01
    stored.write_bytes(bytes(data))

    with pytest.raises(SourceFetchError) as exc_info:
        verify_source_page(
            tmp_path, attempt["source_page_path"], attempt["source_page_sha256"]
        )
    assert exc_info.value.code == "SOURCE_PAGE_HASH_MISMATCH"


def test_source_ledger_validation_reports_the_hash_mismatch(tmp_path):
    result = _acquire_turnover(tmp_path)
    ledger = result.as_ledger()
    (tmp_path / "sources.json").write_text(json.dumps(ledger), encoding="utf-8")

    attempt = result.attempts[0]
    stored = tmp_path / attempt["source_page_path"]
    stored.write_bytes(stored.read_bytes() + b"!")

    outcome = validate_source_ledger(TARGET_DATE, [], ledger, MATRIX, source_base_dir=tmp_path)
    assert not outcome.valid
    assert any("SOURCE_PAGE_HASH_MISMATCH" in error for error in outcome.errors)


def test_cache_tamper_is_not_silently_repaired_by_a_new_fetch(tmp_path):
    """A tampered cached page must fail, not trigger a self-healing re-fetch."""
    result = _acquire_turnover(tmp_path)
    attempt = result.attempts[0]
    stored = tmp_path / attempt["source_page_path"]
    stored.write_bytes(b"tampered")

    def exploding_transport(url):  # pragma: no cover - must not run
        raise AssertionError("verification must not trigger a network call")

    with pytest.raises(SourceFetchError):
        verify_source_page(
            tmp_path, attempt["source_page_path"], attempt["source_page_sha256"]
        )
    del exploding_transport


def test_request_budget_allows_one_attempt_per_tuple(tmp_path):
    calls: list[str] = []

    def counting_transport(url: str) -> TransportResult:
        calls.append(url)
        return TransportResult(0, 200, "text/html; charset=utf-8", pages.yahoo_quote_page())

    result = acquire_stage(
        "TURNOVER",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["7203", "7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=counting_transport,
    )
    assert len(result.attempts) == 1
    attempt_ids = [attempt["attempt_id"] for attempt in result.attempts]
    assert len(attempt_ids) == len(set(attempt_ids))


def test_merge_ledger_rejects_a_target_date_change(tmp_path):
    result = _acquire_turnover(tmp_path)
    other = dict(result.as_ledger(), target_date="2026-08-13")
    with pytest.raises(Exception):
        merge_ledger(result.as_ledger(), other)


def test_transport_failure_yields_a_null_turnover_attempt(tmp_path):
    def failing(url: str) -> TransportResult:
        return TransportResult(exit_code=28, http_status=None, content_type=None, body=b"")

    result = _acquire_turnover(tmp_path, transport=failing)
    attempt = result.attempts[0]
    assert attempt["status"] == "ACCESS_FAILED"
    assert attempt["values"] is None
    assert attempt["result_count"] is None
    assert "source_page_sha256" not in attempt
    assert result.gate_reason_codes == ["TURNOVER_SOURCE_UNAVAILABLE"]
