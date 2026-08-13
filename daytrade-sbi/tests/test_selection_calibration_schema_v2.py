"""Schema v2 contract tests for schemas/selection_calibration.schema.json
(spec section 52)."""

from __future__ import annotations

import copy

import pytest

from src.contracts import validate_json_document


def _valid_complete_payload() -> dict:
    sha = "a" * 64
    return {
        "schema_version": 2,
        "calibration_version": "selection-calibration-v1",
        "calibration_status": "COMPLETE",
        "status_reason_codes": [],
        "cohort": {
            "cohort_id": sha,
            "strategy_version": "v1",
            "ranking_version": "ranking-v1",
            "ranking_schema_version": 1,
            "selection_version": "selection-v1",
            "calibration_context_sha256": sha,
            "source_matrix_raw_sha256": sha,
        },
        "dataset": {
            "observation_count": 1,
            "excluded_count": 0,
            "observations": [
                {
                    "observation_id": sha,
                    "target_date": "2026-08-10",
                    "previous_trading_day": "2026-08-07",
                    "rank1": {
                        "ticker": "1234",
                        "turnover_yen": 10000000,
                        "relative_tick_size": {"numerator": 1, "denominator": 1000},
                    },
                    "ranking_identity": {
                        "ranking_schema_version": 1,
                        "ranking_version": "ranking-v1",
                        "strategy_version": "v1",
                        "config_sha256": sha,
                    },
                    "provenance": {
                        "ranking_raw_sha256": sha,
                        "event_gate_raw_sha256": sha,
                        "candidates_raw_sha256": sha,
                        "market_data_raw_sha256": sha,
                        "sources_raw_sha256": sha,
                        "source_matrix_raw_sha256": sha,
                        "strategy_snapshot_raw_sha256": sha,
                    },
                    "cohort_id": sha,
                }
            ],
            "excluded_observations": [],
        },
        "readiness": {"status": "NOT_ASSESSED", "policy_version": None},
        "candidate_space": {
            "turnover_candidate_count": 1,
            "relative_tick_candidate_count": 1,
            "evaluated_pair_count": 1,
            "minimum_turnover_yen": [10000000],
            "maximum_relative_tick_size": [{"numerator": 1, "denominator": 1000}],
        },
        "evaluated_threshold_pairs": [
            {
                "pair_id": sha,
                "minimum_turnover_yen": 10000000,
                "maximum_relative_tick_size": {"numerator": 1, "denominator": 1000},
                "observation_count": 1,
                "selected_count": 1,
                "rejected_count": 0,
                "selection_rate": {"numerator": 1, "denominator": 1},
                "rejected_turnover_only_count": 0,
                "rejected_relative_tick_only_count": 0,
                "rejected_both_count": 0,
                "outcome_signature_sha256": sha,
            }
        ],
        "calibration_policy": {
            "candidate_policy": "rank1_only",
            "fallback_policy": "none",
            "rule_logic": "all",
            "candidate_generation": "observed_breakpoints",
            "performance_objective": "none",
            "automatic_threshold_selection": False,
        },
    }


def _valid_data_unavailable_payload() -> dict:
    payload = _valid_complete_payload()
    payload["calibration_status"] = "DATA_UNAVAILABLE"
    payload["status_reason_codes"] = ["CALIBRATION_NO_OBSERVATIONS"]
    payload["dataset"]["observation_count"] = 0
    payload["dataset"]["observations"] = []
    payload["candidate_space"] = {
        "turnover_candidate_count": 0,
        "relative_tick_candidate_count": 0,
        "evaluated_pair_count": 0,
        "minimum_turnover_yen": [],
        "maximum_relative_tick_size": [],
    }
    payload["evaluated_threshold_pairs"] = []
    return payload


def test_schema_v2_accepts_valid_complete_payload():
    validate_json_document(_valid_complete_payload(), "selection_calibration.schema.json")


def test_schema_v2_accepts_valid_data_unavailable_payload():
    validate_json_document(
        _valid_data_unavailable_payload(), "selection_calibration.schema.json"
    )


def test_schema_v2_rejects_generated_at():
    payload = _valid_complete_payload()
    payload["generated_at"] = "2026-08-13T00:00:00+00:00"
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")


def test_schema_v2_rejects_best_pair():
    payload = _valid_complete_payload()
    payload["best_pair"] = payload["evaluated_threshold_pairs"][0]
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")


def test_schema_v2_rejects_recommended_pair():
    payload = _valid_complete_payload()
    payload["recommended_pair"] = payload["evaluated_threshold_pairs"][0]
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")


def test_schema_v2_rejects_unknown_top_level_field():
    payload = _valid_complete_payload()
    payload["score"] = 1.0
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")


def test_schema_v2_rejects_unknown_nested_field():
    payload = _valid_complete_payload()
    payload["cohort"]["confidence"] = 0.9
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")


def test_schema_v2_rejects_malformed_sha_string():
    payload = _valid_complete_payload()
    payload["cohort"]["cohort_id"] = "not-a-sha"
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")


def test_schema_v2_rejects_selection_rate_denominator_zero():
    payload = _valid_complete_payload()
    payload["evaluated_threshold_pairs"][0]["selection_rate"] = {"numerator": 0, "denominator": 0}
    with pytest.raises(Exception):
        validate_json_document(payload, "selection_calibration.schema.json")
