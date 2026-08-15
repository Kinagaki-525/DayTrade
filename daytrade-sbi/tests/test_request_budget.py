from __future__ import annotations

import json

import pytest

from src.request_budget import (
    RequestBudgetError,
    complete_request,
    load_request_record,
    network_request_path,
    request_id_for,
    reserve_request,
)


URL = "https://finance.yahoo.co.jp/quote/7203.T/history"
TARGET_DATE = "2026-08-13"
RESEARCH_CUTOFF = "2026-08-12T20:00:00+09:00"


def test_request_id_is_deterministic():
    first = request_id_for(url=URL, target_date=TARGET_DATE, research_cutoff=RESEARCH_CUTOFF)
    second = request_id_for(url=URL, target_date=TARGET_DATE, research_cutoff=RESEARCH_CUTOFF)
    assert first == second
    assert first.startswith("req-")


def test_different_cutoff_is_different_request():
    first = request_id_for(url=URL, target_date=TARGET_DATE, research_cutoff=RESEARCH_CUTOFF)
    second = request_id_for(
        url=URL, target_date=TARGET_DATE, research_cutoff="2026-08-11T20:00:00+09:00"
    )
    assert first != second


def test_reserve_creates_reserved_record(tmp_path):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    assert outcome.already_completed is False
    assert outcome.record["state"] == "RESERVED"
    assert outcome.record["completed_at"] is None

    on_disk = load_request_record(tmp_path, outcome.record["request_id"])
    assert on_disk == outcome.record


def test_completed_request_is_reused(tmp_path):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    complete_request(
        tmp_path,
        outcome.record["request_id"],
        source_status="FOUND",
        http_status=200,
        content_type="text/html",
        transport_exit_code=0,
        source_page_path="source_pages/x.raw",
        source_page_sha256="a" * 64,
        source_page_size_bytes=123,
    )

    second = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="6758",
        origin_attempt_id="att-def",
    )
    assert second.already_completed is True
    assert second.record["state"] == "COMPLETED"
    assert second.record["http_status"] == 200
    # Reuse never mutates the origin attribution.
    assert second.record["origin_attempt_id"] == "att-abc"


def test_reserved_request_blocks_retry(tmp_path):
    reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    # Never completed -- simulates a crash between reserve and complete.
    with pytest.raises(RequestBudgetError) as excinfo:
        reserve_request(
            tmp_path,
            url=URL,
            target_date=TARGET_DATE,
            research_cutoff=RESEARCH_CUTOFF,
            origin_source_id="YAHOO_JP_HISTORY",
            origin_candidate_code="7203",
            origin_attempt_id="att-abc",
        )
    assert excinfo.value.code == "REQUEST_BUDGET_STATE_INDETERMINATE"


def _reserve_and_complete(tmp_path, *, candidate_code="7203", attempt_id="att-abc"):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code=candidate_code,
        origin_attempt_id=attempt_id,
    )
    return complete_request(
        tmp_path,
        outcome.record["request_id"],
        source_status="FOUND",
        http_status=200,
        content_type="text/html",
        transport_exit_code=0,
        source_page_path="source_pages/x.raw",
        source_page_sha256="a" * 64,
        source_page_size_bytes=123,
    )


def _rewrite_record(path, record) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_request_id_tamper_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["request_id"] = "req-" + "0" * 32
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"

    with pytest.raises(RequestBudgetError):
        reserve_request(
            tmp_path,
            url=URL,
            target_date=TARGET_DATE,
            research_cutoff=RESEARCH_CUTOFF,
            origin_source_id="YAHOO_JP_HISTORY",
            origin_candidate_code="6758",
            origin_attempt_id="att-def",
        )


def test_url_tamper_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["url"] = URL.replace("7203", "9999")
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_target_date_tamper_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["target_date"] = "2099-01-01"
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_cutoff_tamper_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["research_cutoff"] = "2099-01-01T00:00:00+09:00"
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_schema_invalid_source_status_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["source_status"] = "NOT_A_REAL_STATUS"
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_malformed_json_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_filename_mismatch_is_rejected_on_reload(tmp_path):
    record = _reserve_and_complete(tmp_path)
    real_path = network_request_path(tmp_path, record["request_id"])
    wrong_id = "req-" + "1" * 32
    wrong_path = network_request_path(tmp_path, wrong_id)
    _rewrite_record(wrong_path, record)
    real_path.unlink()

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, wrong_id)
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_concurrent_reservation_loser_fails_closed_on_reserved(tmp_path, monkeypatch):
    import src.request_budget as rb

    request_id = rb.request_id_for(url=URL, target_date=TARGET_DATE, research_cutoff=RESEARCH_CUTOFF)
    winner_record = {
        "schema_version": rb.NETWORK_REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "url": URL,
        "target_date": TARGET_DATE,
        "research_cutoff": RESEARCH_CUTOFF,
        "state": "RESERVED",
        "reserved_at": rb.utc_now_iso(),
        "completed_at": None,
        "origin_source_id": "YAHOO_JP_HISTORY",
        "origin_candidate_code": "7203",
        "origin_attempt_id": "att-winner",
        "source_status": None,
        "http_status": None,
        "content_type": None,
        "transport_exit_code": None,
        "source_page_path": None,
        "source_page_sha256": None,
        "source_page_size_bytes": None,
    }
    path = rb.network_request_path(tmp_path, request_id)
    path.parent.mkdir(parents=True)
    _rewrite_record(path, winner_record)

    original_load = rb.load_request_record
    calls = {"n": 0}

    def fake_load(run_dir, req_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original_load(run_dir, req_id)

    monkeypatch.setattr(rb, "load_request_record", fake_load)

    with pytest.raises(RequestBudgetError) as excinfo:
        rb.reserve_request(
            tmp_path,
            url=URL,
            target_date=TARGET_DATE,
            research_cutoff=RESEARCH_CUTOFF,
            origin_source_id="YAHOO_JP_HISTORY",
            origin_candidate_code="7203",
            origin_attempt_id="att-loser",
        )
    assert excinfo.value.code == "REQUEST_BUDGET_STATE_INDETERMINATE"


def test_concurrent_reservation_loser_reuses_completed_winner(tmp_path, monkeypatch):
    import src.request_budget as rb

    winner = _reserve_and_complete(tmp_path, candidate_code="7203", attempt_id="att-winner")

    original_load = rb.load_request_record
    calls = {"n": 0}

    def fake_load(run_dir, req_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original_load(run_dir, req_id)

    monkeypatch.setattr(rb, "load_request_record", fake_load)

    outcome = rb.reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="6758",
        origin_attempt_id="att-loser",
    )
    assert outcome.already_completed is True
    assert outcome.record["request_id"] == winner["request_id"]
    assert outcome.record["origin_attempt_id"] == "att-winner"


# --------------------------------------- FIX-R2-002B: State Exhaustiveness --


def test_completed_record_with_null_source_status_is_rejected(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["source_status"] = None
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_completed_at_before_reserved_at_is_rejected(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["reserved_at"] = "2026-08-13T12:00:10Z"
    tampered["completed_at"] = "2026-08-13T12:00:01Z"
    _rewrite_record(path, tampered)

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_completed_at_equal_to_reserved_at_is_valid(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["reserved_at"] = "2026-08-13T12:00:10Z"
    tampered["completed_at"] = "2026-08-13T12:00:10Z"
    _rewrite_record(path, tampered)

    loaded = load_request_record(tmp_path, record["request_id"])
    assert loaded["completed_at"] == "2026-08-13T12:00:10Z"


def test_completed_at_after_reserved_at_is_valid(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["reserved_at"] = "2026-08-13T12:00:01Z"
    tampered["completed_at"] = "2026-08-13T12:00:10Z"
    _rewrite_record(path, tampered)

    loaded = load_request_record(tmp_path, record["request_id"])
    assert loaded["completed_at"] == "2026-08-13T12:00:10Z"


def test_invalid_utf8_bytes_are_rejected_not_crashed(tmp_path):
    record = _reserve_and_complete(tmp_path)
    path = network_request_path(tmp_path, record["request_id"])
    path.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")

    with pytest.raises(RequestBudgetError) as excinfo:
        load_request_record(tmp_path, record["request_id"])
    assert excinfo.value.code == "REQUEST_RECORD_INTEGRITY_VIOLATION"


def test_network_requests_as_regular_file_is_a_directory_violation(tmp_path):
    import src.request_budget as rb

    (tmp_path / rb.NETWORK_REQUESTS_DIRNAME).write_text("not a directory", encoding="utf-8")
    records, violations = rb.scan_network_requests_directory(tmp_path)
    assert records == []
    assert violations
