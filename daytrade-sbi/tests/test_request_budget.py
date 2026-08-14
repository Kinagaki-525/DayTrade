from __future__ import annotations

import pytest

from src.request_budget import (
    RequestBudgetError,
    complete_request,
    load_request_record,
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


def test_request_id_tamper_is_invalid(tmp_path):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    recomputed = request_id_for(url=URL, target_date=TARGET_DATE, research_cutoff=RESEARCH_CUTOFF)
    tampered = dict(outcome.record)
    tampered["request_id"] = "req-" + "0" * 32
    assert tampered["request_id"] != recomputed


def test_url_tamper_is_invalid(tmp_path):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    tampered_url = URL.replace("7203", "9999")
    recomputed = request_id_for(
        url=tampered_url, target_date=TARGET_DATE, research_cutoff=RESEARCH_CUTOFF
    )
    assert recomputed != outcome.record["request_id"]


def test_target_date_tamper_is_invalid(tmp_path):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    recomputed = request_id_for(
        url=URL, target_date="2099-01-01", research_cutoff=RESEARCH_CUTOFF
    )
    assert recomputed != outcome.record["request_id"]


def test_cutoff_tamper_is_invalid(tmp_path):
    outcome = reserve_request(
        tmp_path,
        url=URL,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="YAHOO_JP_HISTORY",
        origin_candidate_code="7203",
        origin_attempt_id="att-abc",
    )
    recomputed = request_id_for(
        url=URL, target_date=TARGET_DATE, research_cutoff="2099-01-01T00:00:00+09:00"
    )
    assert recomputed != outcome.record["request_id"]
