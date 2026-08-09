from src.contracts import validate_json_document
from src.performance import build_performance_payload


def test_performance_counts_stage_candidates_and_duplicate_requests():
    payload = build_performance_payload(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [{"ticker": "1234"}, {"ticker": "5678"}],
            "candidate_research": [
                {
                    "ticker": "1234",
                    "data_status": "VERIFIED",
                    "status_reasons": [],
                    "stage1_status": "PASSED",
                    "stage2_status": "COMPLETE",
                    "context_research_status": "COMPLETE",
                },
                {
                    "ticker": "5678",
                    "data_status": "DATA_UNAVAILABLE",
                    "status_reasons": [],
                    "stage1_status": "REJECTED",
                    "stage2_status": "SKIPPED",
                    "context_research_status": "SKIPPED",
                },
            ],
        },
        candidate_pipeline={
            "candidates": [
                {"pipeline_status": "ELIGIBLE"},
                {"pipeline_status": "REJECTED"},
            ]
        },
        source_payload={
            "sources": [{"source_ref": "source-1"}],
            "source_attempts": [
                {
                    "url": "https://example.test/source",
                    "target_date": "2026-08-10",
                    "research_cutoff": "2026-08-07T20:00:00+09:00",
                },
                {
                    "url": "https://example.test/source",
                    "target_date": "2026-08-10",
                    "research_cutoff": "2026-08-07T20:00:00+09:00",
                },
            ],
        },
    )

    validate_json_document(payload, "performance.schema.json")
    assert payload["counts"]["source_request_count"] == 2
    assert payload["counts"]["duplicate_source_request_count"] == 1
    assert payload["counts"]["stage1_candidate_count"] == 2
    assert payload["counts"]["stage1_rejected_count"] == 1
    assert payload["counts"]["stage2_candidate_count"] == 1
    assert payload["counts"]["context_research_candidate_count"] == 1
    assert payload["timings"]["total"] is None


def test_performance_uses_provided_timings_without_guessing_missing_values():
    payload = build_performance_payload(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [],
            "candidate_research": [],
        },
        candidate_pipeline={"candidates": []},
        source_payload={"sources": [], "source_attempts": []},
        timings_payload={"timings": {"total": 12.5}},
    )

    assert payload["timings"]["total"] == 12.5
    assert payload["timings"]["discovery"] is None
