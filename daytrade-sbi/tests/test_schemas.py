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


def test_recommendation_file_keeps_json_integers_schema_compatible():
    loaded = load_json_document(
        Path(__file__).resolve().parent / "fixtures" / "recommendation_valid.json",
        "recommendation.schema.json",
    )

    assert loaded["schema_version"] == 1
    assert loaded["shares"] == 100
