from __future__ import annotations

from src.event_gate import build_event_gate


def test_event_gate_marks_missing_tdnet_attempt_as_data_unavailable():
    payload = build_event_gate(
        market_research={
            "target_date": "2026-08-12",
            "previous_trading_day": "2026-08-11",
            "research_cutoff": "2026-08-12T20:00:00+09:00",
            "candidate_research": [
                {
                    "ticker": "1234",
                    "source_attempt_ids": [],
                    "event_context": {
                        "selected_attempt_ids": {
                            "earnings_schedule": None,
                            "tdnet": None,
                            "yahoo_news": None,
                            "kabutan_news": None,
                            "issuer_disclosure": None,
                        },
                        "news_classifications": [],
                    },
                }
            ],
        },
        candidate_pipeline={"summary": {}},
        candidates={"candidates": [{"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"}]},
        source_payload={"source_attempts": []},
        research_window={"window_start": "2026-08-10T00:00:00+09:00"},
        config={"strategy_version": "v1"},
        input_hashes={"market_research_sha256": "a" * 64, "candidate_pipeline_sha256": "b" * 64, "candidates_sha256": "c" * 64, "sources_sha256": "d" * 64, "research_window_sha256": "e" * 64, "strategy_snapshot_sha256": "f" * 64},
    )

    candidate = payload["candidates"][0]
    tdnet_rule = next(rule for rule in candidate["rule_evaluations"] if rule["rule_id"] == "tdnet_any_disclosure")
    assert tdnet_rule["result"] == "DATA_UNAVAILABLE"
    assert candidate["gate_status"] == "DATA_UNAVAILABLE"


def test_event_gate_aggregates_pass_reject_and_data_unavailable():
    payload = build_event_gate(
        market_research={
            "target_date": "2026-08-12",
            "previous_trading_day": "2026-08-11",
            "research_cutoff": "2026-08-12T20:00:00+09:00",
            "candidate_research": [
                {
                    "ticker": "1234",
                    "source_attempt_ids": ["jpx_earnings_schedule-1234-found"],
                    "event_context": {
                        "selected_attempt_ids": {
                            "earnings_schedule": "jpx_earnings_schedule-1234-found",
                            "tdnet": None,
                            "yahoo_news": None,
                            "kabutan_news": None,
                            "issuer_disclosure": None,
                        },
                        "news_classifications": [],
                    },
                },
                {
                    "ticker": "5678",
                    "source_attempt_ids": ["jpx_earnings_schedule-5678-found"],
                    "event_context": {
                        "selected_attempt_ids": {
                            "earnings_schedule": "jpx_earnings_schedule-5678-found",
                            "tdnet": None,
                            "yahoo_news": None,
                            "kabutan_news": None,
                            "issuer_disclosure": None,
                        },
                        "news_classifications": [],
                    },
                },
                {
                    "ticker": "9012",
                    "source_attempt_ids": ["jpx_earnings_schedule-9012-found"],
                    "event_context": {
                        "selected_attempt_ids": {
                            "earnings_schedule": "jpx_earnings_schedule-9012-found",
                            "tdnet": None,
                            "yahoo_news": None,
                            "kabutan_news": None,
                            "issuer_disclosure": None,
                        },
                        "news_classifications": [],
                    },
                },
            ],
        },
        candidate_pipeline={"summary": {}},
        candidates={
            "candidates": [
                {"ticker": "1234", "status": "ELIGIBLE", "screening_status": "PASS"},
                {"ticker": "5678", "status": "ELIGIBLE", "screening_status": "PASS"},
                {"ticker": "9012", "status": "ELIGIBLE", "screening_status": "PASS"},
            ]
        },
        source_payload={
            "source_attempts": [
                {
                    "attempt_id": "jpx_earnings_schedule-1234-found",
                    "source_id": "JPX_EARNINGS_SCHEDULE",
                    "candidate_code": "1234",
                    "target_date": "2026-08-12",
                    "requested_at": "2026-08-12T10:00:00+09:00",
                    "status": "FOUND",
                    "values": [{"evidence_id": "jpx_earnings_schedule-1234-found#0", "ticker": "1234", "scheduled_date": "2026-08-12", "title": "Earnings"}],
                    "result_count": 1,
                },
                {
                    "attempt_id": "jpx_earnings_schedule-5678-found",
                    "source_id": "JPX_EARNINGS_SCHEDULE",
                    "candidate_code": "5678",
                    "target_date": "2026-08-12",
                    "requested_at": "2026-08-12T10:00:00+09:00",
                    "status": "FOUND",
                    "values": [{"evidence_id": "jpx_earnings_schedule-5678-found#0", "ticker": "5678", "scheduled_date": "2026-08-10", "title": "Earnings"}],
                    "result_count": 1,
                },
                {
                    "attempt_id": "jpx_earnings_schedule-9012-found",
                    "source_id": "JPX_EARNINGS_SCHEDULE",
                    "candidate_code": "9012",
                    "target_date": "2026-08-12",
                    "requested_at": "2026-08-12T10:00:00+09:00",
                    "status": "FOUND",
                    "values": [],
                    "result_count": 0,
                },
            ]
        },
        research_window={"window_start": "2026-08-10T00:00:00+09:00"},
        config={"strategy_version": "v1"},
        input_hashes={
            "market_research_sha256": "a" * 64,
            "candidate_pipeline_sha256": "b" * 64,
            "candidates_sha256": "c" * 64,
            "sources_sha256": "d" * 64,
            "research_window_sha256": "e" * 64,
            "strategy_snapshot_sha256": "f" * 64,
        },
    )

    assert payload["summary"]["input_count"] == 3
    assert payload["summary"]["pass_count"] == 0
    assert payload["summary"]["reject_count"] == 1
    assert payload["summary"]["data_unavailable_count"] == 2
    assert payload["event_gate_complete"] is True
    assert payload["ranking_ready"] is False
