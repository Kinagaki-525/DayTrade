import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from src.config import load_strategy_config, strategy_config_sha256
from src.contracts import load_json_document, validate_json_document

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


def test_all_json_schemas_are_valid_json_documents():
    schema_paths = sorted(SCHEMAS_DIR.glob("*.schema.json"))

    assert schema_paths
    for path in schema_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(payload)


def recommendation_payload(decision="TRADE"):
    config = load_strategy_config()
    metadata = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "decision": decision,
        "selection_reasons": ["confirmed comparison"],
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
        "pipeline_summary": {
            "discovered": 1,
            "research_complete": 1,
            "research_incomplete": 0,
            "data_unavailable": 0,
            "screened": 1,
            "eligible": 1 if decision == "TRADE" else 0,
            "rejected": 0,
            "pipeline_complete": True,
            "stage2_target_count": 1,
            "stage2_completed_count": 1,
            "stage2_unavailable_count": 0,
            "stage2_incomplete_count": 0,
            "coverage_rate": 1,
            "research_incomplete_reason_counts": {},
            "screening_input_count": 1,
            "screening_pass_count": 1 if decision == "TRADE" else 0,
            "screening_reject_count": 0,
            "screening_data_unavailable_count": 0,
            "screening_incomplete_count": 0,
            "screening_complete": True,
            "candidate_count_consistent": True,
            "all_enabled_rules_evaluated": True,
            "screening_rule_counts": [],
            "ranking_complete": False,
        },
        "source_urls": ["https://example.test/source"],
        "notes": None,
    }
    if decision == "TRADE":
        metadata.update(
            {
                "strategy_type": "previous_day_high_breakout",
                "ticker": "1234",
                "company_name": "Example Co.",
                "previous_high": "400",
                "tick_size": "1",
                "entry_trigger": "401",
                "entry_limit": "402",
                "take_profit": "410",
                "stop_loss": "397",
                "shares": 100,
            }
        )
    else:
        metadata.update(
            {
                "strategy_type": None,
                "ticker": None,
                "company_name": None,
                "previous_high": None,
                "tick_size": None,
                "entry_trigger": None,
                "entry_limit": None,
                "take_profit": None,
                "stop_loss": None,
                "shares": None,
            }
        )
    return metadata


def test_recommendation_schema_rejects_order_values_for_no_trade():
    payload = recommendation_payload("NO_TRADE")
    payload["entry_limit"] = "402"

    with pytest.raises(ValueError, match="recommendation.schema.json"):
        validate_json_document(payload, "recommendation.schema.json")


def test_recommendation_schema_requires_post_cutoff_status():
    payload = recommendation_payload("TRADE")
    del payload["post_cutoff_information_status"]

    with pytest.raises(ValueError, match="post_cutoff_information_status"):
        validate_json_document(payload, "recommendation.schema.json")


def test_recommendation_schema_accepts_explicit_no_trade_without_order_values():
    validate_json_document(
        recommendation_payload("NO_TRADE"),
        "recommendation.schema.json",
    )


def test_recommendation_schema_accepts_data_unavailable_without_order_values():
    validate_json_document(
        recommendation_payload("DATA_UNAVAILABLE"),
        "recommendation.schema.json",
    )


def test_recommendation_schema_v2_trade_is_valid():
    payload = recommendation_payload("TRADE")
    payload["schema_version"] = 2
    payload["selection_sha256"] = "a" * 64
    validate_json_document(payload, "recommendation.schema.json")


def test_recommendation_schema_v2_no_trade_is_valid():
    payload = recommendation_payload("NO_TRADE")
    payload["schema_version"] = 2
    payload["selection_sha256"] = "a" * 64
    validate_json_document(payload, "recommendation.schema.json")


def test_recommendation_schema_v2_data_unavailable_is_invalid():
    payload = recommendation_payload("DATA_UNAVAILABLE")
    payload["schema_version"] = 2
    payload["selection_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="recommendation.schema.json"):
        validate_json_document(payload, "recommendation.schema.json")


def test_recommendation_schema_v2_missing_selection_sha256_is_invalid():
    payload = recommendation_payload("TRADE")
    payload["schema_version"] = 2
    with pytest.raises(ValueError, match="selection_sha256"):
        validate_json_document(payload, "recommendation.schema.json")


def test_risk_result_schema_accepts_data_unavailable_as_not_applicable():
    config = load_strategy_config()
    validate_json_document(
        {
            "schema_version": 1,
            "target_date": "2026-08-10",
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "decision": "DATA_UNAVAILABLE",
            "status": "NOT_APPLICABLE",
            "ticker": None,
            "required_capital_yen": None,
            "expected_loss_yen": None,
            "violations": [],
        },
        "risk_result.schema.json",
    )


def test_sources_schema_requires_source_attempts():
    with pytest.raises(ValueError, match="source_attempts"):
        validate_json_document(
            {
                "schema_version": 1,
                "target_date": "2026-08-10",
                "sources": [],
            },
            "sources.schema.json",
        )


def test_sources_schema_accepts_empty_source_attempts():
    validate_json_document(
        {
            "schema_version": 1,
            "target_date": "2026-08-10",
            "sources": [],
            "source_attempts": [],
        },
        "sources.schema.json",
    )


def test_sources_schema_rejects_parse_failed_result_count_zero():
    with pytest.raises(ValueError, match="result_count"):
        validate_json_document(
            {
                "schema_version": 1,
                "target_date": "2026-08-10",
                "sources": [],
                "source_attempts": [
                    {
                        "attempt_id": "tdnet-parse-failed",
                        "source_id": "JPX_TDNET",
                        "source_role": "CONTEXT",
                        "criticality": "CONTEXT",
                        "information_type": "TIMELY_DISCLOSURE",
                        "candidate_code": "1234",
                        "target_date": "2026-08-10",
                        "research_cutoff": "2026-08-07T20:00:00+09:00",
                        "requested_at": "2026-08-07T20:01:00+09:00",
                        "retrieved_at": "2026-08-07T20:02:00+09:00",
                        "url": "https://example.test/tdnet",
                        "status": "PARSE_FAILED",
                        "values": None,
                        "result_count": 0,
                    }
                ],
            },
            "sources.schema.json",
        )


def test_sources_schema_accepts_found_result_count_zero():
    validate_json_document(
        {
            "schema_version": 1,
            "target_date": "2026-08-10",
            "sources": [],
            "source_attempts": [
                {
                    "attempt_id": "tdnet-empty",
                    "source_id": "JPX_TDNET",
                    "source_role": "CONTEXT",
                    "criticality": "CONTEXT",
                    "information_type": "TIMELY_DISCLOSURE",
                    "candidate_code": "1234",
                    "target_date": "2026-08-10",
                    "research_cutoff": "2026-08-07T20:00:00+09:00",
                    "requested_at": "2026-08-07T20:01:00+09:00",
                    "retrieved_at": "2026-08-07T20:02:00+09:00",
                    "url": "https://example.test/tdnet",
                    "status": "FOUND",
                    "values": [],
                    "result_count": 0,
                }
            ],
        },
        "sources.schema.json",
    )


def test_sources_schema_requires_attempt_id():
    with pytest.raises(ValueError, match="attempt_id"):
        validate_json_document(
            {
                "schema_version": 1,
                "target_date": "2026-08-10",
                "sources": [],
                "source_attempts": [
                    {
                        "source_id": "JPX_TDNET",
                        "source_role": "CONTEXT",
                        "criticality": "CONTEXT",
                        "information_type": "TIMELY_DISCLOSURE",
                        "candidate_code": "1234",
                        "target_date": "2026-08-10",
                        "research_cutoff": "2026-08-07T20:00:00+09:00",
                        "requested_at": "2026-08-07T20:01:00+09:00",
                        "retrieved_at": "2026-08-07T20:02:00+09:00",
                        "url": "https://example.test/tdnet",
                        "status": "PARSE_FAILED",
                        "values": None,
                        "result_count": None,
                    }
                ],
            },
            "sources.schema.json",
        )


def test_recommendation_file_keeps_json_integers_schema_compatible():
    loaded = load_json_document(
        Path(__file__).resolve().parent / "fixtures" / "recommendation_valid.json",
        "recommendation.schema.json",
    )

    assert loaded["schema_version"] == 1
    assert loaded["shares"] == 100


# ---------------------------------------------------------------------------
# Risk Result v2 (FIX-R2-003)
# ---------------------------------------------------------------------------


def _risk_input_hashes(**overrides):
    hashes = {
        "recommendation_sha256": "a" * 64,
        "candidates_sha256": "b" * 64,
        "candidate_pipeline_sha256": "c" * 64,
        "market_data_sha256": "d" * 64,
        "sources_sha256": "e" * 64,
        "source_matrix_sha256": "f" * 64,
        "strategy_snapshot_sha256": "0" * 64,
        "ranking_sha256": "1" * 64,
        "event_gate_sha256": "2" * 64,
        "research_window_sha256": "3" * 64,
        "selection_sha256": None,
        "market_research_sha256": None,
    }
    hashes.update(overrides)
    return hashes


def risk_result_v2_payload(decision="TRADE"):
    config = load_strategy_config()
    trade = decision == "TRADE"
    return {
        "schema_version": 2,
        "target_date": "2026-08-10",
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "decision": decision,
        "status": "PASS" if trade else "NOT_APPLICABLE",
        "ticker": "1234" if trade else None,
        "required_capital_yen": "40200" if trade else None,
        "expected_loss_yen": "500" if trade else None,
        "violations": [],
        "evaluation_context": (
            {"current_positions": 0, "trades_today": 0}
            if trade
            else {"current_positions": None, "trades_today": None}
        ),
        "input_hashes": _risk_input_hashes(
            selection_sha256=("4" * 64) if trade else None
        ),
    }


def test_risk_result_schema_v1_historical_payload_remains_valid():
    config = load_strategy_config()
    validate_json_document(
        {
            "schema_version": 1,
            "target_date": "2026-08-10",
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "decision": "TRADE",
            "status": "PASS",
            "ticker": "1234",
            "required_capital_yen": "40200",
            "expected_loss_yen": "500",
            "violations": [],
        },
        "risk_result.schema.json",
    )


@pytest.mark.parametrize("decision", ["TRADE", "NO_TRADE", "DATA_UNAVAILABLE"])
def test_risk_result_schema_v2_is_valid(decision):
    validate_json_document(risk_result_v2_payload(decision), "risk_result.schema.json")


def test_risk_result_schema_v2_missing_evaluation_context_is_invalid():
    payload = risk_result_v2_payload("TRADE")
    del payload["evaluation_context"]
    with pytest.raises(ValueError, match="evaluation_context"):
        validate_json_document(payload, "risk_result.schema.json")


def test_risk_result_schema_v2_missing_input_hashes_is_invalid():
    payload = risk_result_v2_payload("TRADE")
    del payload["input_hashes"]
    with pytest.raises(ValueError, match="input_hashes"):
        validate_json_document(payload, "risk_result.schema.json")


def test_risk_result_schema_v2_extra_input_hash_is_invalid():
    payload = risk_result_v2_payload("TRADE")
    payload["input_hashes"]["extra_sha256"] = "5" * 64
    with pytest.raises(ValueError):
        validate_json_document(payload, "risk_result.schema.json")


def test_risk_result_schema_v2_missing_input_hash_key_is_invalid():
    payload = risk_result_v2_payload("TRADE")
    del payload["input_hashes"]["ranking_sha256"]
    with pytest.raises(ValueError, match="ranking_sha256"):
        validate_json_document(payload, "risk_result.schema.json")


@pytest.mark.parametrize("field", ["current_positions", "trades_today"])
def test_risk_result_schema_v2_trade_context_null_is_invalid(field):
    payload = risk_result_v2_payload("TRADE")
    payload["evaluation_context"][field] = None
    with pytest.raises(ValueError):
        validate_json_document(payload, "risk_result.schema.json")


@pytest.mark.parametrize("decision", ["NO_TRADE", "DATA_UNAVAILABLE"])
@pytest.mark.parametrize("field", ["current_positions", "trades_today"])
def test_risk_result_schema_v2_non_trade_context_zero_is_invalid(decision, field):
    payload = risk_result_v2_payload(decision)
    payload["evaluation_context"][field] = 0
    with pytest.raises(ValueError):
        validate_json_document(payload, "risk_result.schema.json")


def test_risk_result_schema_v1_with_v2_fields_is_invalid():
    payload = risk_result_v2_payload("TRADE")
    payload["schema_version"] = 1
    with pytest.raises(ValueError):
        validate_json_document(payload, "risk_result.schema.json")
