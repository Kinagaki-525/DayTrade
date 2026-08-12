from __future__ import annotations

import pytest

from src.config import load_strategy_config
from src.contracts import validate_event_research_contract, validate_event_research_inputs
from tests.factories import make_event_research, make_event_source_attempt


TARGET_DATE = "2026-08-10"


def _candidate_pipeline():
    config = load_strategy_config()
    return {
        "target_date": TARGET_DATE,
        "strategy_version": config["strategy_version"],
        "summary": {"pipeline_complete": True, "screening_complete": True},
    }


def _candidates():
    from src.config import strategy_config_sha256

    config = load_strategy_config()
    return {
        "target_date": TARGET_DATE,
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "candidates": [{"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"}],
    }


def test_validate_event_research_inputs_requires_matching_target_date():
    config = load_strategy_config()
    candidate_pipeline = _candidate_pipeline()
    candidates = dict(_candidates())
    candidates["target_date"] = "2026-08-11"
    with pytest.raises(ValueError, match="does not match"):
        validate_event_research_inputs(
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            config=config,
        )


def test_validate_event_research_contract_accepts_consistent_document():
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
    validate_event_research_contract(
        event_research=event_research,
        source_payload={"target_date": TARGET_DATE, "source_attempts": [attempt]},
    )


def test_validate_event_research_contract_rejects_duplicate_attempt_ids():
    config = load_strategy_config()
    attempt = make_event_source_attempt(
        attempt_id="tdnet-1234",
        source_id="JPX_TDNET",
        ticker="1234",
        target_date=TARGET_DATE,
        values=[],
    )
    event_research = make_event_research(
        target_date=TARGET_DATE,
        event_gate_as_of="2026-08-09T21:00:00+09:00",
        strategy_version=config["strategy_version"],
        tickers=["1234"],
    )
    with pytest.raises(ValueError, match="duplicate source_attempt"):
        validate_event_research_contract(
            event_research=event_research,
            source_payload={"target_date": TARGET_DATE, "source_attempts": [attempt, dict(attempt)]},
        )
