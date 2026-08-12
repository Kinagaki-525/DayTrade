from __future__ import annotations

import pytest

from src.config import load_strategy_config
from src.event_research import (
    event_research_contract_errors,
    hard_screening_pass_tickers,
    init_event_research_payload,
)
from tests.factories import EVENT_RESEARCH_INPUT_HASHES, make_event_research, make_event_source_attempt


TARGET_DATE = "2026-08-10"


def test_hard_screening_pass_tickers_filters_eligible_pass_only():
    candidates = {
        "candidates": [
            {"ticker": "1111", "status": "ELIGIBLE", "screening_status": "PASS"},
            {"ticker": "2222", "status": "ELIGIBLE", "screening_status": "FAIL"},
            {"ticker": "3333", "status": "REJECTED", "screening_status": "PASS"},
        ]
    }
    assert hard_screening_pass_tickers(candidates) == ["1111"]


def test_init_event_research_requires_pipeline_complete():
    config = load_strategy_config()
    candidate_pipeline = {
        "target_date": TARGET_DATE,
        "summary": {"pipeline_complete": False, "screening_complete": True},
    }
    candidates = {"candidates": []}
    with pytest.raises(ValueError, match="not complete"):
        init_event_research_payload(
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            config=config,
            previous_trading_day="2026-08-07",
            input_hashes=EVENT_RESEARCH_INPUT_HASHES,
        )


def test_init_event_research_builds_skeleton_for_pass_tickers():
    config = load_strategy_config()
    candidate_pipeline = {
        "target_date": TARGET_DATE,
        "summary": {"pipeline_complete": True, "screening_complete": True},
    }
    candidates = {
        "candidates": [
            {"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"},
            {"ticker": "5678", "status": "ELIGIBLE", "screening_status": "FAIL"},
        ]
    }
    payload = init_event_research_payload(
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        config=config,
        previous_trading_day="2026-08-07",
        input_hashes=EVENT_RESEARCH_INPUT_HASHES,
    )
    assert payload["event_gate_input_tickers"] == ["1234"]
    assert payload["event_gate_as_of"] is None
    assert len(payload["candidates"]) == 1
    assert set(payload["candidates"][0]["selected_attempt_ids"]) == {
        "earnings_schedule_jpx",
        "earnings_schedule_issuer",
        "tdnet",
        "issuer_disclosure",
        "yahoo_news",
        "kabutan_news",
    }


def test_event_research_contract_errors_flags_naive_published_at():
    config = load_strategy_config()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        event_gate_as_of="2026-08-09T21:00:00+09:00",
        strategy_version=config["strategy_version"],
        tickers=["1234"],
        candidates=[
            {
                "ticker": "1234",
                "selected_attempt_ids": {
                    "earnings_schedule_jpx": None,
                    "earnings_schedule_issuer": None,
                    "tdnet": None,
                    "issuer_disclosure": None,
                    "yahoo_news": None,
                    "kabutan_news": None,
                },
                "news_classifications": [
                    {
                        "news_evidence_id": "n-1",
                        "source_id": "YAHOO_JP_NEWS",
                        "source_attempt_id": "yahoo-1234",
                        "published_at": "2026-08-09T10:00:00",
                        "signal_type": "NON_EVENT",
                        "subject_status": "NOT_SUBJECT",
                        "confirmation_evidence_ids": [],
                        "confirmation_source_attempt_ids": [],
                    }
                ],
            }
        ],
    )
    errors = event_research_contract_errors(
        event_research, source_payload={"target_date": TARGET_DATE, "source_attempts": []}
    )
    assert any("timezone-aware" in error for error in errors)


def test_event_research_contract_errors_flags_unknown_attempt_reference():
    config = load_strategy_config()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        event_gate_as_of="2026-08-09T21:00:00+09:00",
        strategy_version=config["strategy_version"],
        tickers=["1234"],
    )
    event_research["candidates"][0]["selected_attempt_ids"]["tdnet"] = "does-not-exist"
    errors = event_research_contract_errors(
        event_research, source_payload={"target_date": TARGET_DATE, "source_attempts": []}
    )
    assert any("unknown attempt_id" in error for error in errors)


def test_event_research_contract_errors_empty_when_consistent():
    config = load_strategy_config()
    attempt = make_event_source_attempt(
        attempt_id="tdnet-1234",
        source_id="JPX_TDNET",
        ticker="1234",
        target_date=TARGET_DATE,
        requested_at="2026-08-09T20:00:00+09:00",
        values=[],
    )
    event_research = make_event_research(
        target_date=TARGET_DATE,
        event_gate_as_of="2026-08-09T21:00:00+09:00",
        strategy_version=config["strategy_version"],
        tickers=["1234"],
    )
    event_research["candidates"][0]["selected_attempt_ids"]["tdnet"] = "tdnet-1234"
    errors = event_research_contract_errors(
        event_research, source_payload={"target_date": TARGET_DATE, "source_attempts": [attempt]}
    )
    assert errors == []
