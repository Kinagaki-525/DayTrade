"""Shared, schema-valid fixture builders for happy-path
build_selection_recommendation() tests.

Unlike the deliberately-simplified dicts used by negative/unit tests of the
function's own input guards, everything built here is asserted to validate
against the real production JSON schemas (schemas/selection.schema.json,
schemas/candidates.schema.json, schemas/candidate_pipeline.schema.json,
schemas/market_data.schema.json, schemas/research_window.schema.json,
schemas/sources.schema.json) via src.contracts.validate_json_document, so
the happy-path tests exercise genuinely representative production shapes.
"""

from __future__ import annotations

import copy
from typing import Any

from src.contracts import validate_json_document

STRATEGY_VERSION = "sv-1"
CONFIG_SHA = "a" * 64
RANKING_SHA = "c" * 64
STRATEGY_SNAPSHOT_SHA = "d" * 64
TARGET_DATE = "2026-08-12"
PREVIOUS_TRADING_DAY = "2026-08-11"


def _rule_evaluations(*, turnover_pass: bool, tick_pass: bool) -> list[dict[str, Any]]:
    return [
        {
            "rule_id": "minimum_turnover_yen",
            "operator": ">=",
            "actual_value_yen": "50000000",
            "threshold_value_yen": 30000000,
            "result": "PASS" if turnover_pass else "REJECT",
            "reason_code": None if turnover_pass else "SELECTION_TURNOVER_BELOW_MINIMUM",
        },
        {
            "rule_id": "maximum_relative_tick_size",
            "operator": "<=",
            "actual_ratio": {"numerator_yen": "1", "denominator_yen": "400"},
            "threshold_ratio": {"numerator": 1, "denominator": 100},
            "result": "PASS" if tick_pass else "REJECT",
            "reason_code": None
            if tick_pass
            else "SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM",
        },
    ]


def make_valid_selection(
    *, selected: bool, ticker: str = "1234"
) -> dict[str, Any]:
    """A genuinely schema-valid selection.json payload (SELECTED or NO_TRADE)."""
    if selected:
        status = "SELECTED"
        selected_ticker = ticker
        reason_codes = ["SELECTION_ALL_RULES_PASSED"]
        rule_evaluations = _rule_evaluations(turnover_pass=True, tick_pass=True)
    else:
        status = "NO_TRADE"
        selected_ticker = None
        reason_codes = ["SELECTION_TURNOVER_BELOW_MINIMUM"]
        rule_evaluations = _rule_evaluations(turnover_pass=False, tick_pass=True)

    payload = {
        "schema_version": 1,
        "selection_version": "selection-v1",
        "target_date": TARGET_DATE,
        "previous_trading_day": PREVIOUS_TRADING_DAY,
        "generated_at": "2026-08-12T00:00:00+00:00",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "input_hashes": {
            "ranking_sha256": RANKING_SHA,
            "strategy_snapshot_sha256": STRATEGY_SNAPSHOT_SHA,
        },
        "ranking_version": "ranking-v1",
        "ranking_method": "ordinal_rank_sum",
        "evaluated_ticker": ticker,
        "ranking_final_rank": 1,
        "selection_status": status,
        "selected_ticker": selected_ticker,
        "reason_codes": reason_codes,
        "rule_evaluations": rule_evaluations,
    }
    validate_json_document(payload, "selection.schema.json")
    return payload


def _feature_value(value: str) -> dict[str, Any]:
    return {
        "value": value,
        "source_refs": ["ref-1"],
        "source_urls": ["https://example.test/feature"],
        "estimated": False,
    }


def make_valid_candidates(
    *, ticker: str = "1234", other_ticker: str = "9999"
) -> dict[str, Any]:
    """A genuinely schema-valid candidates.json payload with one ELIGIBLE
    candidate (the selected ticker) and one REJECTED candidate."""
    order_plan = {
        "strategy_version": STRATEGY_VERSION,
        "validation_status": "unvalidated",
        "entry_trigger": "401",
        "entry_limit": "402",
        "affordable": True,
        "estimated_purchase_amount": "40200",
        "expected_loss_yen": "500",
        "shares": 100,
        "take_profit_price": "410",
        "stop_loss_price": "397",
    }
    features = {
        "previous_day_volume": _feature_value("1000000"),
        "required_capital_yen": _feature_value("40200"),
        "daily_range_yen": _feature_value("10"),
        "daily_range_pct": _feature_value("2.5"),
        "previous_day_change_pct": _feature_value("1.2"),
        "estimated_turnover": _feature_value("50000000"),
    }
    eligible = {
        "ticker": ticker,
        "status": "ELIGIBLE",
        "screening_status": "PASS",
        "reasons": [],
        "unresolved_screening": [],
        "passed_rules": ["minimum_volume"],
        "failed_rules": [],
        "unavailable_rules": [],
        "reason_codes": [],
        "source_refs": ["ref-1"],
        "rule_evaluations": [],
        "features": features,
        "order_plan": order_plan,
    }
    rejected = {
        "ticker": other_ticker,
        "status": "REJECTED",
        "screening_status": "REJECT",
        "reasons": ["below minimum volume"],
        "unresolved_screening": [],
        "passed_rules": [],
        "failed_rules": ["minimum_volume"],
        "unavailable_rules": [],
        "reason_codes": [],
        "source_refs": ["ref-2"],
        "rule_evaluations": [],
        "features": features,
        "order_plan": None,
    }
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "generated_at": "2026-08-12T00:00:00+00:00",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "candidates": [eligible, rejected],
    }
    validate_json_document(payload, "candidates.schema.json")
    return payload


def make_valid_candidate_pipeline() -> dict[str, Any]:
    summary = {
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
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "generated_at": "2026-08-12T00:00:00+00:00",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "summary": summary,
        "candidates": [],
    }
    validate_json_document(payload, "candidate_pipeline.schema.json")
    return payload


def make_valid_market_data(*, ticker: str = "1234") -> dict[str, Any]:
    record = {
        "ticker": ticker,
        "company_name": "Example Co.",
        "market": "TSE",
        "share_unit": 100,
        "data_status": "VERIFIED",
        "trading_date": PREVIOUS_TRADING_DAY,
        "open": "395",
        "high": "405",
        "low": "393",
        "close": "400",
        "volume": "500000",
        "previous_close": "398",
        "previous_high": "400",
        "tick_size": "1",
        "sources": [],
    }
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "records": [record],
    }
    validate_json_document(payload, "market_data.schema.json")
    return payload


def make_valid_research_window() -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "previous_trading_day": PREVIOUS_TRADING_DAY,
        "research_cutoff": "2026-08-11T21:00:00+00:00",
        "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
        "research_window": {
            "run_type": "FIRST_RUN",
            "window_start": "2026-08-11T00:00:00+00:00",
            "window_end": "2026-08-11T21:00:00+00:00",
            "previous_research_cutoff": None,
            "previous_run_date": None,
            "bootstrap_lookback_days": 5,
        },
    }
    validate_json_document(payload, "research_window.schema.json")
    return payload


def _source_value(*, ticker: str, source_url: str, source_ref: str) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "source_id": "KABUTAN",
        "source_role": "PRIMARY",
        "information_type": "price_history",
        "source_status": "FOUND",
        "source_name": "Kabutan",
        "source_url": source_url,
        "retrieved_at": "2026-08-11T21:00:00+00:00",
        "trading_date": PREVIOUS_TRADING_DAY,
        "ticker": ticker,
        "field_name": "previous_high",
        "value": "400",
    }


def _source_attempt(
    *, candidate_code: str, url: str, attempt_id: str
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "source_id": "KABUTAN",
        "source_role": "PRIMARY",
        "criticality": "TRADE_CRITICAL",
        "information_type": "price_history",
        "candidate_code": candidate_code,
        "target_date": TARGET_DATE,
        "research_cutoff": "2026-08-11T21:00:00+00:00",
        "requested_at": "2026-08-11T20:59:00+00:00",
        "retrieved_at": "2026-08-11T21:00:00+00:00",
        "url": url,
        "status": "FOUND",
        "values": {},
        "result_count": 1,
    }


def make_valid_sources(
    *, ticker: str = "1234", other_ticker: str = "9999"
) -> dict[str, Any]:
    """A genuinely schema-valid sources.json payload.

    Includes a regression guard fixture: a sources[] entry keyed by
    ``ticker`` for the selected stock, plus a source_attempts[] entry that
    uses ``candidate_code`` for a *different* ticker. sources[] entries have
    no candidate_code field at all -- only ticker -- so this guards against
    reintroducing the previously-fixed bug where sources[] URL matching was
    wrongly keyed off candidate_code instead of ticker.
    """
    sources = [
        _source_value(
            ticker=ticker,
            source_url="https://example.test/a",
            source_ref="ref-a",
        ),
        _source_value(
            ticker=ticker,
            source_url="https://example.test/b",
            source_ref="ref-b",
        ),
        _source_value(
            ticker=other_ticker,
            source_url="https://example.test/c",
            source_ref="ref-c",
        ),
    ]
    source_attempts = [
        # candidate_code intentionally does NOT equal `ticker` -- if
        # sources[] matching were ever wrongly keyed off candidate_code
        # (belonging only to source_attempts[]) instead of ticker, this
        # would cause source_urls to wrongly include/exclude entries.
        _source_attempt(
            candidate_code=f"CAND-{ticker}",
            url="https://example.test/attempt-a",
            attempt_id="attempt-1",
        ),
        _source_attempt(
            candidate_code=f"CAND-{other_ticker}",
            url="https://example.test/attempt-c",
            attempt_id="attempt-2",
        ),
    ]
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "sources": sources,
        "source_attempts": source_attempts,
    }
    validate_json_document(payload, "sources.schema.json")
    return payload


def make_valid_selected_kwargs(
    *, ticker: str = "1234", other_ticker: str = "9999"
) -> dict[str, Any]:
    """Full, schema-valid kwargs for a SELECTED -> TRADE happy-path call to
    build_selection_recommendation()."""
    return dict(
        selection=make_valid_selection(selected=True, ticker=ticker),
        candidates=make_valid_candidates(ticker=ticker, other_ticker=other_ticker),
        candidate_pipeline=make_valid_candidate_pipeline(),
        market_data=make_valid_market_data(ticker=ticker),
        research_window=make_valid_research_window(),
        source_payload=make_valid_sources(ticker=ticker, other_ticker=other_ticker),
        config={},
        selection_sha256="b" * 64,
    )


def make_valid_no_trade_kwargs(
    *, ticker: str = "1234", other_ticker: str = "9999"
) -> dict[str, Any]:
    """Full, schema-valid kwargs for a NO_TRADE happy-path call to
    build_selection_recommendation()."""
    kwargs = make_valid_selected_kwargs(ticker=ticker, other_ticker=other_ticker)
    kwargs["selection"] = make_valid_selection(selected=False, ticker=ticker)
    return kwargs
