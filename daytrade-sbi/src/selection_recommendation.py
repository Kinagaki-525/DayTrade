"""Selection Recommendation Builder (Recommendation v2).

Pure function that turns a completed ``selection.json`` decision into the
Recommendation v2 payload. This module never performs I/O and never invents
values: TRADE fields for a SELECTED decision are copied verbatim from the
matching candidate's order_plan and the matching market_data record. There
is no Rank 2 fallback and no free-text/AI-generated content anywhere here.
"""

from __future__ import annotations

import copy
from typing import Any


RECOMMENDATION_VERSION_2_SCHEMA_VERSION = 2


class SelectionRecommendationHardError(ValueError):
    """Raised for any Selection Recommendation Builder precondition violation."""


def _hard_error(code: str, message: str) -> None:
    raise SelectionRecommendationHardError(f"{code}: {message}")


def build_selection_recommendation(
    *,
    selection: dict[str, Any],
    candidates: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    market_data: dict[str, Any],
    research_window: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
    selection_sha256: str,
) -> dict[str, Any]:
    status = selection.get("selection_status")
    if status not in ("SELECTED", "NO_TRADE"):
        _hard_error(
            "SELECTION_RECOMMENDATION_STATUS_INVALID",
            f"selection.selection_status must be SELECTED or NO_TRADE, got {status!r}",
        )

    if (
        not isinstance(selection_sha256, str)
        or len(selection_sha256) != 64
        or any(c not in "0123456789abcdef" for c in selection_sha256)
    ):
        _hard_error(
            "SELECTION_RECOMMENDATION_HASH_INVALID",
            "selection_sha256 must be a 64-char lowercase hex string",
        )

    pipeline_summary = copy.deepcopy(candidate_pipeline.get("summary"))
    if not isinstance(pipeline_summary, dict):
        _hard_error(
            "SELECTION_RECOMMENDATION_PIPELINE_SUMMARY_INVALID",
            "candidate_pipeline.summary must be an object",
        )

    selection_reasons = selection.get("reason_codes")
    if not isinstance(selection_reasons, list) or not selection_reasons:
        _hard_error(
            "SELECTION_RECOMMENDATION_REASON_CODES_INVALID",
            "selection.reason_codes must be a non-empty list",
        )

    research_cutoff = research_window.get("research_cutoff")
    post_cutoff_information_status = research_window.get(
        "post_cutoff_information_status", "OUT_OF_SCOPE"
    )

    if status == "NO_TRADE":
        return {
            "schema_version": RECOMMENDATION_VERSION_2_SCHEMA_VERSION,
            "target_date": selection["target_date"],
            "strategy_version": selection["strategy_version"],
            "config_sha256": selection["config_sha256"],
            "decision": "NO_TRADE",
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
            "selection_reasons": list(selection_reasons),
            "research_cutoff": research_cutoff,
            "post_cutoff_information_status": post_cutoff_information_status,
            "pipeline_summary": pipeline_summary,
            "source_statuses": [],
            "source_urls": [],
            "notes": None,
            "selection_sha256": selection_sha256,
        }

    # SELECTED -> TRADE
    ticker = selection.get("selected_ticker")
    if not isinstance(ticker, str) or not ticker:
        _hard_error(
            "SELECTION_RECOMMENDATION_TICKER_INVALID",
            "selection.selected_ticker must be a non-empty string for SELECTED",
        )

    candidate_matches = [
        c
        for c in candidates.get("candidates", [])
        if c.get("ticker") == ticker
        and c.get("status") == "ELIGIBLE"
        and c.get("screening_status") == "PASS"
        and c.get("order_plan") is not None
    ]
    if len(candidate_matches) != 1:
        _hard_error(
            "SELECTION_RECOMMENDATION_CANDIDATE_MATCH_INVALID",
            f"expected exactly 1 matching ELIGIBLE/PASS candidate with order_plan for {ticker}, "
            f"found {len(candidate_matches)}",
        )
    candidate = candidate_matches[0]
    order_plan = candidate["order_plan"]

    market_matches = [
        r
        for r in market_data.get("records", [])
        if r.get("ticker") == ticker and r.get("data_status") == "VERIFIED"
    ]
    if len(market_matches) != 1:
        _hard_error(
            "SELECTION_RECOMMENDATION_MARKET_MATCH_INVALID",
            f"expected exactly 1 VERIFIED market_data record for {ticker}, found {len(market_matches)}",
        )
    market_record = market_matches[0]

    source_urls: set[str] = set()
    for source in source_payload.get("sources", []):
        if source.get("candidate_code") == ticker and source.get("source_url"):
            source_urls.add(source["source_url"])
    if not source_urls:
        _hard_error(
            "SELECTION_RECOMMENDATION_SOURCE_URLS_EMPTY",
            f"no source_urls found for selected ticker {ticker}",
        )

    return {
        "schema_version": RECOMMENDATION_VERSION_2_SCHEMA_VERSION,
        "target_date": selection["target_date"],
        "strategy_version": selection["strategy_version"],
        "config_sha256": selection["config_sha256"],
        "decision": "TRADE",
        "strategy_type": "previous_day_high_breakout",
        "ticker": ticker,
        "company_name": market_record.get("company_name"),
        "previous_high": market_record.get("previous_high"),
        "tick_size": market_record.get("tick_size"),
        "entry_trigger": order_plan["entry_trigger"],
        "entry_limit": order_plan["entry_limit"],
        "take_profit": order_plan["take_profit_price"],
        "stop_loss": order_plan["stop_loss_price"],
        "shares": order_plan["shares"],
        "selection_reasons": list(selection_reasons),
        "research_cutoff": research_cutoff,
        "post_cutoff_information_status": post_cutoff_information_status,
        "pipeline_summary": pipeline_summary,
        "source_statuses": [],
        "source_urls": sorted(source_urls),
        "notes": None,
        "selection_sha256": selection_sha256,
    }
