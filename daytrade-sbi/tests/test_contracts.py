import pytest

from src.contracts import (
    validate_recommendation_candidate_link,
    validate_recommendation_risk_link,
    validate_recommendation_sources,
)


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
