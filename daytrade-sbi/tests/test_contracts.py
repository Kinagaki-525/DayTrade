import pytest

from src.contracts import (
    validate_candidate_pipeline_inputs,
    validate_performance_inputs,
    validate_recommendation_candidate_link,
    validate_recommendation_risk_link,
    validate_recommendation_sources,
)
from src.config import load_strategy_config, strategy_config_sha256


METADATA = {
    "target_date": "2026-08-10",
    "strategy_version": "v1",
    "config_sha256": "a" * 64,
}


def test_trade_recommendation_must_reference_an_eligible_candidate():
    recommendation = {**METADATA, "decision": "TRADE", "ticker": "1234"}
    candidates = {
        **METADATA,
        "candidates": [{"ticker": "1234", "status": "REJECTED"}],
    }

    with pytest.raises(ValueError, match="ELIGIBLE"):
        validate_recommendation_candidate_link(recommendation, candidates)


def test_data_unavailable_recommendation_does_not_require_eligible_candidate():
    recommendation = {**METADATA, "decision": "DATA_UNAVAILABLE", "ticker": None}
    candidates = {
        **METADATA,
        "candidates": [{"ticker": "1234", "status": "DATA_UNAVAILABLE"}],
    }

    validate_recommendation_candidate_link(recommendation, candidates)


def test_recommendation_and_risk_result_must_use_same_config():
    recommendation = {**METADATA, "decision": "NO_TRADE", "ticker": None}
    risk_result = {
        **METADATA,
        "decision": "NO_TRADE",
        "ticker": None,
        "config_sha256": "b" * 64,
    }

    with pytest.raises(ValueError, match="config_sha256"):
        validate_recommendation_risk_link(recommendation, risk_result)


def test_recommendation_urls_must_exist_in_source_ledger():
    recommendation = {
        **METADATA,
        "source_urls": ["https://example.test/not-recorded"],
    }
    source_payload = {
        "target_date": "2026-08-10",
        "sources": [{"source_url": "https://example.test/recorded"}],
    }

    with pytest.raises(ValueError, match="missing from sources.json"):
        validate_recommendation_sources(recommendation, source_payload)


def test_recommendation_source_statuses_may_reference_source_attempts():
    recommendation = {
        **METADATA,
        "source_urls": [],
        "source_statuses": [
            {
                "source_id": "JPX_TDNET",
                "status": "PARSE_FAILED",
                "url": "https://example.test/tdnet",
            }
        ],
    }
    source_payload = {
        "target_date": "2026-08-10",
        "sources": [],
        "source_attempts": [{"url": "https://example.test/tdnet"}],
    }

    validate_recommendation_sources(recommendation, source_payload)


def test_candidate_pipeline_inputs_must_use_same_target_date():
    config = load_strategy_config()
    config_sha256 = strategy_config_sha256(config)

    with pytest.raises(ValueError, match="target_date"):
        validate_candidate_pipeline_inputs(
            market_research={"target_date": "2026-08-10"},
            market_target_date="2026-08-11",
            candidates={
                "target_date": "2026-08-10",
                "strategy_version": config["strategy_version"],
                "config_sha256": config_sha256,
            },
            source_payload={"target_date": "2026-08-10"},
            config=config,
        )


def test_candidate_pipeline_inputs_must_use_same_config():
    config = load_strategy_config()

    with pytest.raises(ValueError, match="config_sha256"):
        validate_candidate_pipeline_inputs(
            market_research={"target_date": "2026-08-10"},
            market_target_date="2026-08-10",
            candidates={
                "target_date": "2026-08-10",
                "strategy_version": config["strategy_version"],
                "config_sha256": "0" * 64,
            },
            source_payload={"target_date": "2026-08-10"},
            config=config,
        )


def test_performance_inputs_must_use_same_target_date():
    with pytest.raises(ValueError, match="target_date"):
        validate_performance_inputs(
            market_research={"target_date": "2026-08-10"},
            candidate_pipeline={"target_date": "2026-08-11"},
            source_payload={"target_date": "2026-08-10"},
        )
