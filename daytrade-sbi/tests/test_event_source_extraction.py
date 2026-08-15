"""The AI classification merge door: everything is revalidated at the door."""

from __future__ import annotations

import json

import pytest

from src.event_source_extraction import (
    EventExtractionMergeError,
    merge_event_source_extraction,
    merge_event_source_extraction_files,
)
from src.source_acquisition import acquire_stage, write_ledger
from src.source_fetch import TransportResult
from src.source_matrix import AI_CLASSIFICATION_SOURCE_IDS, load_source_matrix
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
CUTOFF = "2026-08-12T20:00:00+09:00"
MATRIX = load_source_matrix()


def _news_transport(url: str) -> TransportResult:
    return TransportResult(0, 200, "text/html; charset=utf-8", pages.yahoo_quote_page())


@pytest.fixture()
def ledger(tmp_path):
    result = acquire_stage(
        "EVENT",
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=["7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=_news_transport,
        source_ids=("YAHOO_JP_NEWS",),
    )
    return result.as_ledger()


def _extraction(ledger, **overrides):
    attempt = ledger["source_attempts"][0]
    item = {
        "source_attempt_id": attempt["attempt_id"],
        "source_id": attempt["source_id"],
        "ticker": attempt["candidate_code"],
        "trading_date": attempt["target_date"],
        "source_page_sha256": attempt["source_page_sha256"],
        "coverage_status": "COMPLETE",
        # Event Objects use the event_type literals the EXISTING Event Gate
        # matches on -- "EARNINGS", not an invented "EARNINGS_ANNOUNCEMENT".
        "events": [
            {
                "evidence_id": "ev-test-earnings",
                "event_type": "EARNINGS",
                "event_date": "2026-08-14",
                "published_at": "2026-08-12T15:00:00+09:00",
            }
        ],
    }
    item.update(overrides)
    return {
        "schema_version": 1,
        "target_date": ledger["target_date"],
        "generated_at": "2026-08-12T21:00:00Z",
        "classifier": "claude-code-event-classifier",
        "extractions": [item],
    }


def test_event_extraction_updates_attempt_values_consumed_by_event_gate(
    tmp_path, ledger
):
    """The merge must land in source_attempts[].values -- the exact place
    src.event_gate reads -- in the exact shape it reads."""
    from src.event_gate import _attempt_events

    merged = merge_event_source_extraction(
        extraction=_extraction(ledger), ledger=ledger, run_dir=tmp_path
    )
    attempt = merged["source_attempts"][0]
    assert attempt["coverage_status"] == "COMPLETE"

    events = _attempt_events(attempt)
    earnings = [event for event in events if event.get("event_type") == "EARNINGS"]
    assert len(earnings) == 1
    assert earnings[0]["event_date"] == "2026-08-14"
    assert earnings[0]["evidence_id"] == "ev-test-earnings"
    # The deterministic parse values survive alongside the Event Objects.
    assert any(
        isinstance(value, dict) and value.get("field_name")
        for value in attempt["values"]
    )


def test_merge_is_idempotent(tmp_path, ledger):
    once = merge_event_source_extraction(
        extraction=_extraction(ledger), ledger=ledger, run_dir=tmp_path
    )
    twice = merge_event_source_extraction(
        extraction=_extraction(ledger), ledger=once, run_dir=tmp_path
    )
    assert twice["source_attempts"] == once["source_attempts"]


def test_event_object_with_an_invented_event_type_is_rejected(tmp_path, ledger):
    extraction = _extraction(ledger)
    extraction["extractions"][0]["events"][0]["event_type"] = "EARNINGS_ANNOUNCEMENT"
    with pytest.raises(ValueError):
        merge_event_source_extraction(
            extraction=extraction, ledger=ledger, run_dir=tmp_path
        )


def test_news_classification_must_reference_a_real_event(tmp_path, ledger):
    extraction = _extraction(ledger)
    extraction["extractions"][0]["news_classifications"] = [
        {
            "news_evidence_id": "ev-does-not-exist",
            "published_at": "2026-08-12T15:00:00+09:00",
            "signal_type": "NON_EVENT",
            "subject_status": "NOT_SUBJECT",
            "confirmation_evidence_ids": [],
            "confirmation_source_attempt_ids": [],
        }
    ]
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=extraction, ledger=ledger, run_dir=tmp_path
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_NEWS_EVIDENCE_UNKNOWN"


def test_unknown_attempt_id_is_rejected(tmp_path, ledger):
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=_extraction(ledger, source_attempt_id="att-nope"),
            ledger=ledger,
            run_dir=tmp_path,
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_ATTEMPT_UNKNOWN"


def test_ticker_cross_contamination_is_rejected(tmp_path, ledger):
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=_extraction(ledger, ticker="6758"),
            ledger=ledger,
            run_dir=tmp_path,
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_TICKER_MISMATCH"


def test_trading_date_mismatch_is_rejected(tmp_path, ledger):
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=_extraction(ledger, trading_date="2026-08-13"),
            ledger=ledger,
            run_dir=tmp_path,
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_TRADING_DATE_MISMATCH"


def test_declared_hash_mismatch_is_rejected(tmp_path, ledger):
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=_extraction(ledger, source_page_sha256="b" * 64),
            ledger=ledger,
            run_dir=tmp_path,
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_HASH_MISMATCH"


def test_tampered_raw_page_is_rejected_at_merge_time(tmp_path, ledger):
    attempt = ledger["source_attempts"][0]
    stored = tmp_path / attempt["source_page_path"]
    stored.write_bytes(stored.read_bytes() + b" ")
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=_extraction(ledger), ledger=ledger, run_dir=tmp_path
        )
    assert exc_info.value.code == "SOURCE_PAGE_HASH_MISMATCH"


def test_non_classifiable_source_is_rejected(tmp_path, ledger):
    """JPX_TDNET is DETERMINISTIC: its Event Objects come from the parser, so
    the AI may not stage a classification for it at all. The staging schema
    rejects it before any ledger check is reached."""
    extraction = _extraction(ledger)
    extraction["extractions"][0]["source_id"] = "JPX_TDNET"
    with pytest.raises(ValueError) as exc_info:
        merge_event_source_extraction(
            extraction=extraction, ledger=ledger, run_dir=tmp_path
        )
    assert "JPX_TDNET" in str(exc_info.value)


def test_classifiable_source_must_still_match_the_attempt(tmp_path, ledger):
    extraction = _extraction(ledger)
    extraction["extractions"][0]["source_id"] = "KABUTAN_NEWS"
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=extraction, ledger=ledger, run_dir=tmp_path
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_SOURCE_ID_MISMATCH"


def test_schema_limits_classification_to_the_four_event_sources():
    schema = json.loads(
        (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "schemas/event_source_extraction.schema.json"
        ).read_text(encoding="utf-8")
    )
    allowed = schema["$defs"]["extraction"]["properties"]["source_id"]["enum"]
    assert set(allowed) == set(AI_CLASSIFICATION_SOURCE_IDS)


def test_extraction_target_date_must_match_the_ledger(tmp_path, ledger):
    extraction = _extraction(ledger)
    extraction["target_date"] = "2026-08-13"
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=extraction, ledger=ledger, run_dir=tmp_path
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_TARGET_DATE_MISMATCH"


def test_duplicate_extractions_for_one_attempt_are_rejected(tmp_path, ledger):
    extraction = _extraction(ledger)
    extraction["extractions"] *= 2
    with pytest.raises(EventExtractionMergeError) as exc_info:
        merge_event_source_extraction(
            extraction=extraction, ledger=ledger, run_dir=tmp_path
        )
    assert exc_info.value.code == "EVENT_EXTRACTION_DUPLICATE_ATTEMPT"


def test_file_level_merge_is_atomic_and_leaves_sources_valid(tmp_path, ledger):
    sources_path = tmp_path / "sources.json"
    write_ledger(sources_path, ledger)
    extraction_path = tmp_path / "event_source_extraction.json"
    extraction_path.write_text(json.dumps(_extraction(ledger)), encoding="utf-8")

    merge_event_source_extraction_files(
        extraction_path=extraction_path,
        sources_path=sources_path,
        run_dir=tmp_path,
    )
    merged = json.loads(sources_path.read_text(encoding="utf-8"))
    assert any(
        isinstance(value, dict) and value.get("event_type") == "EARNINGS"
        for attempt in merged["source_attempts"]
        for value in attempt.get("values") or []
    )


def test_failed_merge_leaves_sources_json_untouched(tmp_path, ledger):
    sources_path = tmp_path / "sources.json"
    write_ledger(sources_path, ledger)
    original = sources_path.read_bytes()

    extraction_path = tmp_path / "event_source_extraction.json"
    extraction_path.write_text(
        json.dumps(_extraction(ledger, ticker="6758")), encoding="utf-8"
    )
    with pytest.raises(EventExtractionMergeError):
        merge_event_source_extraction_files(
            extraction_path=extraction_path,
            sources_path=sources_path,
            run_dir=tmp_path,
        )
    assert sources_path.read_bytes() == original
