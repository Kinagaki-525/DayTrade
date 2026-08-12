from __future__ import annotations

from src.config import load_strategy_config
from src.contracts import validate_json_document
from src.event_research import init_event_research_payload
from tests.factories import EVENT_RESEARCH_INPUT_HASHES, make_event_research, make_news_classification


TARGET_DATE = "2026-08-10"


def test_init_event_research_payload_matches_schema():
    config = load_strategy_config()
    candidate_pipeline = {
        "target_date": TARGET_DATE,
        "summary": {"pipeline_complete": True, "screening_complete": True},
    }
    candidates = {
        "candidates": [{"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"}]
    }
    payload = init_event_research_payload(
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        config=config,
        previous_trading_day="2026-08-07",
        input_hashes=EVENT_RESEARCH_INPUT_HASHES,
    )
    validate_json_document(payload, "event_research.schema.json")


def test_completed_event_research_payload_matches_schema():
    config = load_strategy_config()
    payload = make_event_research(
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
                    make_news_classification(news_evidence_id="n-1", signal_type="NON_EVENT")
                ],
            }
        ],
    )
    validate_json_document(payload, "event_research.schema.json")
