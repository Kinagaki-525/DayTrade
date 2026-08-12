from __future__ import annotations

from src.config import load_strategy_config
from src.contracts import validate_json_document
from src.event_gate import build_event_gate
from tests.factories import (
    EVENT_GATE_INPUT_HASHES,
    make_event_research,
    make_event_source_attempt,
)


TARGET_DATE = "2026-08-10"
PREVIOUS_TRADING_DAY = "2026-08-07"
EVENT_GATE_AS_OF = "2026-08-09T21:00:00+09:00"


def test_event_gate_payload_matches_schema_when_empty():
    config = load_strategy_config()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        tickers=[],
        candidates=[],
    )
    candidate_pipeline = {
        "target_date": TARGET_DATE,
        "summary": {"pipeline_complete": True, "screening_complete": True},
    }
    candidates = {"target_date": TARGET_DATE, "candidates": []}
    source_payload = {"target_date": TARGET_DATE, "source_attempts": []}
    payload = build_event_gate(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=EVENT_GATE_INPUT_HASHES,
    )
    validate_json_document(payload, "event_gate.schema.json")


def test_event_gate_payload_matches_schema_with_candidates():
    config = load_strategy_config()
    source_ids = [
        "JPX_EARNINGS_SCHEDULE",
        "COMPANY_IR",
        "JPX_TDNET",
        "COMPANY_IR_DISCLOSURE",
        "YAHOO_JP_NEWS",
        "KABUTAN_NEWS",
    ]
    attempts = [
        make_event_source_attempt(
            attempt_id=f"{source_id.lower()}-1234",
            source_id=source_id,
            ticker="1234",
            target_date=TARGET_DATE,
            values=[],
        )
        for source_id in source_ids
    ]
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        tickers=["1234"],
    )
    candidate_pipeline = {
        "target_date": TARGET_DATE,
        "summary": {"pipeline_complete": True, "screening_complete": True},
    }
    candidates = {
        "target_date": TARGET_DATE,
        "candidates": [{"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"}],
    }
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    payload = build_event_gate(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=EVENT_GATE_INPUT_HASHES,
    )
    validate_json_document(payload, "event_gate.schema.json")
    assert payload["candidates"][0]["gate_status"] == "PASS"
