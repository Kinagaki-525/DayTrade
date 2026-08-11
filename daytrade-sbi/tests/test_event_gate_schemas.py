from __future__ import annotations

from src.contracts import validate_json_document
from src.event_gate import build_event_gate


def test_event_gate_payload_matches_schema():
    payload = build_event_gate(
        market_research={"target_date": "2026-08-12", "previous_trading_day": "2026-08-11", "research_cutoff": "2026-08-12T20:00:00+09:00", "candidate_research": [{"ticker": "1234", "source_attempt_ids": [], "event_context": {"selected_attempt_ids": {"earnings_schedule": None, "tdnet": None, "yahoo_news": None, "kabutan_news": None, "issuer_disclosure": None}, "news_classifications": []}}]},
        candidate_pipeline={"summary": {}},
        candidates={"candidates": [{"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"}]},
        source_payload={"source_attempts": []},
        research_window={"window_start": "2026-08-10T00:00:00+09:00"},
        config={"strategy_version": "v1"},
        input_hashes={"market_research_sha256": "a" * 64, "candidate_pipeline_sha256": "b" * 64, "candidates_sha256": "c" * 64, "sources_sha256": "d" * 64, "research_window_sha256": "e" * 64, "strategy_snapshot_sha256": "f" * 64},
    )
    validate_json_document(payload, "event_gate.schema.json")
