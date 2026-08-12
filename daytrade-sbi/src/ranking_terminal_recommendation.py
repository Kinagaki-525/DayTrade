"""Ranking Terminal Recommendation Builder (Case A / Case B).

Pure function that turns a *terminal* ``ranking.json`` outcome -- one where
Selection never runs at all -- into a plain (non-Selection-driven,
schema_version 1) Recommendation payload. This module never performs I/O and
never invents values.

Two, and only two, applicable cases:

  * Case A -- ``ranking.ranking_status == "DATA_UNAVAILABLE"``: Ranking
    itself could not complete. ``decision = "DATA_UNAVAILABLE"``, and
    ``selection_reasons`` is ``ranking.reason_codes`` copied verbatim.
  * Case B -- ``ranking.ranking_status == "COMPLETE"`` but
    ``config.selection.enabled == False``: Ranking completed but Selection
    is not active yet (pending threshold calibration).
    ``decision = "NO_TRADE"`` with the single orchestration-level reason
    ``SELECTION_NOT_ACTIVE_PENDING_CALIBRATION`` -- a string that must never
    appear inside an actual ``selection.json``, because Selection literally
    never ran here.

A COMPLETE ranking with ``selection.enabled == True`` is explicitly NOT
handled by this module: that combination must go through
``build-selection`` -> ``build-selection-recommendation`` instead. There is
no Rank 2 fallback and no free-text/AI-generated content anywhere here.
"""

from __future__ import annotations

import copy
from typing import Any


RANKING_TERMINAL_RECOMMENDATION_SCHEMA_VERSION = 1

_ORCHESTRATION_NO_TRADE_REASON = "SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"

_NULL_TRADE_FIELDS: dict[str, None] = {
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


class RankingTerminalRecommendationHardError(ValueError):
    """Raised for any Ranking Terminal Recommendation Builder violation."""


def _hard_error(code: str, message: str) -> None:
    raise RankingTerminalRecommendationHardError(f"{code}: {message}")


def determine_ranking_terminal_case(ranking: dict[str, Any], config: dict[str, Any]) -> str:
    """Return "A" or "B", or raise a Hard Error for any other combination.

    Checked in this fixed order:
      1. Case A: ranking_status == DATA_UNAVAILABLE and ranking_complete == False.
      2. Case B: ranking_status == COMPLETE and ranking_complete == True and
         config.selection.enabled == False.
      3. ranking_status == COMPLETE and config.selection.enabled == True:
         wrong tool -- must go through build-selection instead.
      4. Anything else: unsupported combination.
    """
    status = ranking.get("ranking_status")
    complete = ranking.get("ranking_complete")
    selection_config = config.get("selection")
    selection_enabled = isinstance(selection_config, dict) and selection_config.get("enabled") is True

    if status == "DATA_UNAVAILABLE" and complete is False:
        return "A"
    if status == "COMPLETE" and complete is True and not selection_enabled:
        return "B"
    if status == "COMPLETE" and selection_enabled:
        _hard_error(
            "RANKING_TERMINAL_SELECTION_REQUIRED",
            "ranking_status COMPLETE with selection.enabled=true must go through "
            "build-selection -> build-selection-recommendation, never "
            "build-ranking-terminal-recommendation",
        )
    _hard_error(
        "RANKING_TERMINAL_STATE_UNSUPPORTED",
        f"unsupported combination for build-ranking-terminal-recommendation: "
        f"ranking_status={status!r} ranking_complete={complete!r}",
    )
    raise AssertionError("unreachable")  # pragma: no cover


def build_ranking_terminal_recommendation(
    *,
    ranking: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    research_window: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    case = determine_ranking_terminal_case(ranking, config)

    pipeline_summary = copy.deepcopy(candidate_pipeline.get("summary"))
    if not isinstance(pipeline_summary, dict):
        _hard_error(
            "RANKING_TERMINAL_PIPELINE_SUMMARY_INVALID",
            "candidate_pipeline.summary must be an object",
        )

    research_cutoff = research_window.get("research_cutoff")
    post_cutoff_information_status = research_window.get(
        "post_cutoff_information_status", "OUT_OF_SCOPE"
    )

    if case == "A":
        reason_codes = ranking.get("reason_codes")
        if not isinstance(reason_codes, list) or not reason_codes:
            _hard_error(
                "RANKING_TERMINAL_REASON_CODES_EMPTY",
                "Case A (DATA_UNAVAILABLE) ranking.reason_codes must be a non-empty list "
                "-- a DATA_UNAVAILABLE ranking must always carry reason codes",
            )
        decision = "DATA_UNAVAILABLE"
        selection_reasons = list(reason_codes)
    else:
        decision = "NO_TRADE"
        selection_reasons = [_ORCHESTRATION_NO_TRADE_REASON]

    return {
        "schema_version": RANKING_TERMINAL_RECOMMENDATION_SCHEMA_VERSION,
        "target_date": ranking["target_date"],
        "strategy_version": ranking["strategy_version"],
        "config_sha256": ranking["config_sha256"],
        "decision": decision,
        **_NULL_TRADE_FIELDS,
        "selection_reasons": selection_reasons,
        "research_cutoff": research_cutoff,
        "post_cutoff_information_status": post_cutoff_information_status,
        "pipeline_summary": pipeline_summary,
        "source_statuses": [],
        "source_urls": [],
        "notes": None,
    }
