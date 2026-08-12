from __future__ import annotations

import pytest

from src.contracts import validate_recommendation_selection_link
from src.selection_recommendation import (
    SelectionRecommendationHardError,
    build_selection_recommendation,
)

STRATEGY_VERSION = "sv-1"
CONFIG_SHA = "a" * 64
SEL_SHA = "b" * 64


def _pipeline_summary():
    return {
        "discovered": 1,
        "research_complete": 1,
        "research_incomplete": 0,
        "data_unavailable": 0,
        "screened": 1,
        "eligible": 1,
        "rejected": 0,
        "pipeline_complete": True,
        "stage2_target_count": 1,
        "stage2_completed_count": 1,
        "stage2_unavailable_count": 0,
        "stage2_incomplete_count": 0,
        "coverage_rate": 1,
        "research_incomplete_reason_counts": {},
        "screening_input_count": 1,
        "screening_pass_count": 1,
        "screening_reject_count": 0,
        "screening_data_unavailable_count": 0,
        "screening_incomplete_count": 0,
        "screening_complete": True,
        "candidate_count_consistent": True,
        "all_enabled_rules_evaluated": True,
        "screening_rule_counts": [],
        "ranking_complete": False,
    }


def _base_selected():
    selection = {
        "target_date": "2026-08-12",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "selection_status": "SELECTED",
        "selected_ticker": "1234",
        "reason_codes": ["SELECTION_ALL_RULES_PASSED"],
    }
    order_plan = {
        "entry_trigger": "401",
        "entry_limit": "402",
        "take_profit_price": "410",
        "stop_loss_price": "397",
        "shares": 100,
    }
    candidates = {
        "candidates": [
            {
                "ticker": "1234",
                "status": "ELIGIBLE",
                "screening_status": "PASS",
                "order_plan": order_plan,
            }
        ]
    }
    market_data = {
        "records": [
            {
                "ticker": "1234",
                "data_status": "VERIFIED",
                "company_name": "Example Co.",
                "previous_high": "400",
                "tick_size": "1",
            }
        ]
    }
    candidate_pipeline = {"summary": _pipeline_summary()}
    research_window = {
        "research_cutoff": "2026-08-11T21:00:00+00:00",
        "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
    }
    source_payload = {
        "sources": [
            {"ticker": "1234", "source_url": "https://example.test/a"},
            {"ticker": "1234", "source_url": "https://example.test/b"},
            {"ticker": "9999", "source_url": "https://example.test/c"},
        ]
    }
    return dict(
        selection=selection,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        research_window=research_window,
        source_payload=source_payload,
        config={},
        selection_sha256=SEL_SHA,
    )


def _base_no_trade():
    kwargs = _base_selected()
    kwargs["selection"] = {
        "target_date": "2026-08-12",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "selection_status": "NO_TRADE",
        "selected_ticker": None,
        "reason_codes": ["SELECTION_TURNOVER_BELOW_MINIMUM"],
    }
    return kwargs


def test_build_trade_recommendation_copies_order_plan_verbatim():
    payload = build_selection_recommendation(**_base_selected())
    assert payload["decision"] == "TRADE"
    assert payload["ticker"] == "1234"
    assert payload["strategy_type"] == "previous_day_high_breakout"
    assert payload["entry_trigger"] == "401"
    assert payload["entry_limit"] == "402"
    assert payload["take_profit"] == "410"
    assert payload["stop_loss"] == "397"
    assert payload["shares"] == 100
    assert payload["source_urls"] == ["https://example.test/a", "https://example.test/b"]
    assert payload["selection_sha256"] == SEL_SHA
    assert payload["schema_version"] == 2
    # candidate_pipeline.ranking_complete stays false, never rewritten.
    assert payload["pipeline_summary"]["ranking_complete"] is False


def test_build_no_trade_recommendation_has_all_null_trade_fields():
    payload = build_selection_recommendation(**_base_no_trade())
    assert payload["decision"] == "NO_TRADE"
    for field in (
        "strategy_type",
        "ticker",
        "company_name",
        "previous_high",
        "tick_size",
        "entry_trigger",
        "entry_limit",
        "take_profit",
        "stop_loss",
        "shares",
    ):
        assert payload[field] is None
    assert payload["source_urls"] == []
    assert payload["selection_sha256"] == SEL_SHA


def test_missing_matching_candidate_is_hard_error():
    kwargs = _base_selected()
    kwargs["candidates"] = {"candidates": []}
    with pytest.raises(SelectionRecommendationHardError):
        build_selection_recommendation(**kwargs)


def test_missing_source_urls_is_hard_error():
    kwargs = _base_selected()
    kwargs["source_payload"] = {"sources": []}
    with pytest.raises(SelectionRecommendationHardError):
        build_selection_recommendation(**kwargs)


def test_invalid_selection_status_is_hard_error():
    kwargs = _base_selected()
    kwargs["selection"]["selection_status"] = "WEIRD"
    with pytest.raises(SelectionRecommendationHardError):
        build_selection_recommendation(**kwargs)


# --- validate_recommendation_selection_link tamper detection ---


def test_link_ticker_mismatch_detected():
    kwargs = _base_selected()
    recommendation = build_selection_recommendation(**kwargs)
    recommendation["ticker"] = "9999"
    with pytest.raises(ValueError, match="ticker"):
        validate_recommendation_selection_link(
            recommendation=recommendation,
            selection=kwargs["selection"],
            selection_sha256=SEL_SHA,
        )


def test_link_selection_sha256_mismatch_detected():
    kwargs = _base_selected()
    recommendation = build_selection_recommendation(**kwargs)
    with pytest.raises(ValueError, match="selection_sha256"):
        validate_recommendation_selection_link(
            recommendation=recommendation,
            selection=kwargs["selection"],
            selection_sha256="c" * 64,
        )


def test_link_reasons_mismatch_detected():
    kwargs = _base_selected()
    recommendation = build_selection_recommendation(**kwargs)
    recommendation["selection_reasons"] = ["OTHER_REASON"]
    with pytest.raises(ValueError, match="selection_reasons"):
        validate_recommendation_selection_link(
            recommendation=recommendation,
            selection=kwargs["selection"],
            selection_sha256=SEL_SHA,
        )


def test_link_decision_mismatch_detected():
    kwargs = _base_selected()
    recommendation = build_selection_recommendation(**kwargs)
    recommendation["decision"] = "NO_TRADE"
    with pytest.raises(ValueError, match="decision"):
        validate_recommendation_selection_link(
            recommendation=recommendation,
            selection=kwargs["selection"],
            selection_sha256=SEL_SHA,
        )


def test_link_ok_on_valid_trade_pair():
    kwargs = _base_selected()
    recommendation = build_selection_recommendation(**kwargs)
    validate_recommendation_selection_link(
        recommendation=recommendation,
        selection=kwargs["selection"],
        selection_sha256=SEL_SHA,
    )


def test_link_ok_on_valid_no_trade_pair():
    kwargs = _base_no_trade()
    recommendation = build_selection_recommendation(**kwargs)
    validate_recommendation_selection_link(
        recommendation=recommendation,
        selection=kwargs["selection"],
        selection_sha256=SEL_SHA,
    )
