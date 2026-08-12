"""Selection v1: pure Rank-1-only decision logic.

This module never performs I/O, never touches the network, and never uses
randomness. It reads a completed ``ranking.json`` payload and the strategy
config's ``selection`` block, and produces the selection decision payload
(SELECTED / NO_TRADE). Selection only ever evaluates Rank 1 -- there is no
Rank 2 fallback anywhere in this module, under any circumstance.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


SELECTION_VERSION = "selection-v1"
SELECTION_SCHEMA_VERSION = 1

_REASON_ORDER = ("TURNOVER", "RELATIVE_TICK")


class SelectionHardError(ValueError):
    """Raised for any Selection precondition or integrity violation."""


def _hard_error(code: str, message: str) -> None:
    raise SelectionHardError(f"{code}: {message}")


# ---------------------------------------------------------------------------
# Decimal / ratio helpers (also reused by the Calibration tool).
# ---------------------------------------------------------------------------


def parse_positive_decimal(value: Any, name: str) -> Decimal:
    """Parse a strictly positive Decimal, rejecting bool/None/NaN/Infinity."""
    if isinstance(value, bool) or value is None:
        _hard_error("SELECTION_FEATURE_INVALID", f"{name} must be a positive number")
    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            text = str(value)
            if not text.strip():
                _hard_error(
                    "SELECTION_FEATURE_INVALID", f"{name} must be a positive number"
                )
            decimal_value = Decimal(text)
        except (InvalidOperation, ValueError):
            _hard_error("SELECTION_FEATURE_INVALID", f"{name} must be a positive number")
    if not decimal_value.is_finite():
        _hard_error("SELECTION_FEATURE_INVALID", f"{name} must be finite")
    if decimal_value <= 0:
        _hard_error("SELECTION_FEATURE_INVALID", f"{name} must be > 0")
    return decimal_value


def canonicalize_decimal_ratio(numerator: Decimal, denominator: Decimal) -> tuple[int, int]:
    """Canonicalize a Decimal/Decimal ratio into an exact reduced int fraction.

    No float() is ever used. Each Decimal is converted to its exact
    (sign, digits, exponent) integer representation and cross-multiplied so
    that the resulting fraction is exact, then reduced via gcd.
    """
    num_sign, num_digits, num_exp = numerator.as_tuple()
    den_sign, den_digits, den_exp = denominator.as_tuple()
    num_int = int("".join(str(d) for d in num_digits)) * (-1 if num_sign else 1)
    den_int = int("".join(str(d) for d in den_digits)) * (-1 if den_sign else 1)

    # Normalize exponents so num_int/den_int * 10**(num_exp-den_exp) == numerator/denominator
    if num_exp >= den_exp:
        num_int *= 10 ** (num_exp - den_exp)
    else:
        den_int *= 10 ** (den_exp - num_exp)

    if den_int == 0:
        raise SelectionHardError("SELECTION_FEATURE_INVALID: ratio denominator is 0")
    if den_int < 0:
        num_int, den_int = -num_int, -den_int

    divisor = math.gcd(abs(num_int), abs(den_int))
    if divisor == 0:
        divisor = 1
    return num_int // divisor, den_int // divisor


def compare_exact_ratios(
    num_a: int, den_a: int, num_b: int, den_b: int
) -> int:
    """Compare a/den_a vs b/den_b exactly via cross-multiplication.

    Returns -1, 0, or 1. den_a and den_b must both be strictly positive.
    """
    if den_a <= 0 or den_b <= 0:
        raise SelectionHardError(
            "SELECTION_FEATURE_INVALID: ratio denominator must be positive"
        )
    left = num_a * den_b
    right = num_b * den_a
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def relative_tick_within_threshold(
    *, numerator_yen: Decimal, denominator_yen: Decimal, threshold_numerator: int, threshold_denominator: int
) -> bool:
    """True iff numerator_yen/denominator_yen <= threshold_numerator/threshold_denominator.

    Exact integer/Decimal cross-multiplication only; float() is never used.
    """
    num, den = canonicalize_decimal_ratio(numerator_yen, denominator_yen)
    if den <= 0:
        raise SelectionHardError(
            "SELECTION_FEATURE_INVALID: relative tick ratio denominator must be positive"
        )
    return compare_exact_ratios(num, den, threshold_numerator, threshold_denominator) <= 0


# ---------------------------------------------------------------------------
# Rank 1 integrity.
# ---------------------------------------------------------------------------


def validate_rank1_integrity(ranking: dict[str, Any]) -> dict[str, Any]:
    """Validate ranking.json integrity and return the Rank 1 candidate dict.

    Raises SelectionHardError with a SELECTION_ prefixed code on any
    violation. Never re-ranks and never picks Rank 2.
    """
    if ranking.get("ranking_version") != "ranking-v1":
        _hard_error("SELECTION_RANKING_HEADER_INVALID", "ranking_version must be 'ranking-v1'")
    if ranking.get("ranking_method") != "ordinal_rank_sum":
        _hard_error("SELECTION_RANKING_HEADER_INVALID", "ranking_method must be 'ordinal_rank_sum'")
    if ranking.get("ranking_status") != "COMPLETE":
        _hard_error("SELECTION_RANKING_NOT_COMPLETE", "ranking_status must be COMPLETE")
    if ranking.get("ranking_complete") is not True:
        _hard_error("SELECTION_RANKING_NOT_COMPLETE", "ranking_complete must be true")
    if ranking.get("reason_codes") != []:
        _hard_error("SELECTION_RANKING_HEADER_INVALID", "ranking.reason_codes must be empty")
    for field in ("target_date", "previous_trading_day", "strategy_version"):
        value = ranking.get(field)
        if not isinstance(value, str) or not value.strip():
            _hard_error("SELECTION_RANKING_HEADER_INVALID", f"ranking.{field} must be a non-empty string")
    config_sha256 = ranking.get("config_sha256")
    if (
        not isinstance(config_sha256, str)
        or len(config_sha256) != 64
        or any(c not in "0123456789abcdef" for c in config_sha256)
    ):
        _hard_error("SELECTION_RANKING_HEADER_INVALID", "ranking.config_sha256 must be 64 lowercase hex chars")

    summary = ranking.get("summary")
    if not isinstance(summary, dict):
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "ranking.summary must be an object")
    input_count = summary.get("input_count")
    valid_input_count = summary.get("valid_input_count")
    data_unavailable_count = summary.get("data_unavailable_count")
    ranked_count = summary.get("ranked_count")
    top_ranked_ticker = summary.get("top_ranked_ticker")
    if not isinstance(input_count, int) or isinstance(input_count, bool) or input_count < 1:
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.input_count must be >= 1")
    if not isinstance(valid_input_count, int) or isinstance(valid_input_count, bool) or valid_input_count < 1:
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.valid_input_count must be >= 1")
    if data_unavailable_count != 0:
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.data_unavailable_count must be 0")
    if not isinstance(ranked_count, int) or isinstance(ranked_count, bool) or ranked_count < 1:
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.ranked_count must be >= 1")
    if valid_input_count != input_count:
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.valid_input_count must equal input_count")
    if ranked_count != input_count:
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.ranked_count must equal input_count")
    if not isinstance(top_ranked_ticker, str) or not top_ranked_ticker.strip():
        _hard_error("SELECTION_RANKING_SUMMARY_INVALID", "summary.top_ranked_ticker must be a non-empty string")

    candidates = ranking.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != input_count:
        _hard_error(
            "SELECTION_RANKING_CANDIDATES_INVALID",
            "ranking.candidates length must equal summary.input_count",
        )

    seen_tickers: set[str] = set()
    final_ranks: list[int] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", "each candidate must be an object")
        ticker = candidate.get("ticker")
        if not isinstance(ticker, str) or not ticker or ticker != ticker.strip():
            _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", "candidate.ticker must be a non-empty, non-whitespace string")
        if ticker in seen_tickers:
            _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", f"duplicate candidate ticker: {ticker}")
        seen_tickers.add(ticker)
        if candidate.get("input_status") != "VALID":
            _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", f"candidate {ticker} input_status must be VALID")
        if candidate.get("reason_codes") != []:
            _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", f"candidate {ticker} reason_codes must be empty")
        feature_ranks = candidate.get("feature_ranks")
        rank_points = candidate.get("rank_points")
        final_rank = candidate.get("final_rank")
        if feature_ranks is None or rank_points is None or final_rank is None:
            _hard_error(
                "SELECTION_RANKING_CANDIDATES_INVALID",
                f"candidate {ticker} feature_ranks/rank_points/final_rank must not be null",
            )
        if isinstance(final_rank, bool) or not isinstance(final_rank, int) or final_rank < 1:
            _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", f"candidate {ticker} final_rank must be a positive int")
        final_ranks.append(final_rank)

    if set(final_ranks) != set(range(1, len(candidates) + 1)):
        _hard_error(
            "SELECTION_RANKING_CANDIDATES_INVALID",
            "final_rank values must be exactly {1..ranked_count}",
        )

    rank1_candidates = [c for c in candidates if c.get("final_rank") == 1]
    if len(rank1_candidates) != 1:
        _hard_error("SELECTION_RANK1_CARDINALITY_INVALID", "exactly one candidate must have final_rank == 1")
    rank1 = rank1_candidates[0]

    if top_ranked_ticker != rank1.get("ticker"):
        _hard_error(
            "SELECTION_TOP_RANKED_TICKER_MISMATCH",
            "summary.top_ranked_ticker must equal the Rank 1 candidate ticker",
        )

    input_candidate_tickers = ranking.get("input_candidate_tickers")
    if not isinstance(input_candidate_tickers, list):
        _hard_error("SELECTION_RANKING_CANDIDATES_INVALID", "input_candidate_tickers must be a list")
    if (
        len(input_candidate_tickers) != len(set(input_candidate_tickers))
        or set(input_candidate_tickers) != seen_tickers
        or input_candidate_tickers != sorted(input_candidate_tickers)
    ):
        _hard_error(
            "SELECTION_RANKING_CANDIDATES_INVALID",
            "input_candidate_tickers must be unique, sorted, and match candidate tickers",
        )

    feature_values = rank1.get("feature_values")
    if not isinstance(feature_values, dict):
        _hard_error("SELECTION_FEATURE_INVALID", "rank1 candidate feature_values must be an object")
    turnover_yen = parse_positive_decimal(feature_values.get("turnover_yen"), "rank1.feature_values.turnover_yen")
    if turnover_yen != turnover_yen.to_integral_value():
        _hard_error("SELECTION_FEATURE_INVALID", "rank1.feature_values.turnover_yen must be integral JPY")
    tick_size_yen = parse_positive_decimal(feature_values.get("tick_size_yen"), "rank1.feature_values.tick_size_yen")
    entry_trigger_yen = parse_positive_decimal(
        feature_values.get("entry_trigger_yen"), "rank1.feature_values.entry_trigger_yen"
    )
    relative_tick_size = feature_values.get("relative_tick_size")
    if not isinstance(relative_tick_size, dict):
        _hard_error("SELECTION_FEATURE_INVALID", "rank1.feature_values.relative_tick_size must be an object")
    numerator_yen = parse_positive_decimal(
        relative_tick_size.get("numerator_yen"), "rank1.feature_values.relative_tick_size.numerator_yen"
    )
    denominator_yen = parse_positive_decimal(
        relative_tick_size.get("denominator_yen"), "rank1.feature_values.relative_tick_size.denominator_yen"
    )
    if numerator_yen != tick_size_yen:
        _hard_error(
            "SELECTION_FEATURE_INVALID",
            "relative_tick_size.numerator_yen must equal tick_size_yen",
        )
    if denominator_yen != entry_trigger_yen:
        _hard_error(
            "SELECTION_FEATURE_INVALID",
            "relative_tick_size.denominator_yen must equal entry_trigger_yen",
        )

    return rank1


# ---------------------------------------------------------------------------
# Rule evaluation.
# ---------------------------------------------------------------------------


def evaluate_selection_rules(rank1: dict[str, Any], selection_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate both selection rules, in fixed order, without short-circuit.

    Returns a list of 2 rule_evaluation dicts in canonical order
    (TURNOVER, RELATIVE_TICK).
    """
    feature_values = rank1["feature_values"]
    rules = selection_config["rules"]

    turnover_yen = parse_positive_decimal(feature_values["turnover_yen"], "turnover_yen")
    threshold_yen = rules["minimum_turnover_yen"]["threshold_yen"]
    turnover_pass = turnover_yen >= Decimal(threshold_yen)
    turnover_eval = {
        "rule_id": "minimum_turnover_yen",
        "reason_code": "TURNOVER",
        "status": "PASS" if turnover_pass else "REJECT",
        "observed_value_yen": str(turnover_yen),
        "threshold_yen": threshold_yen,
    }

    relative_tick_size = feature_values["relative_tick_size"]
    numerator_yen = parse_positive_decimal(relative_tick_size["numerator_yen"], "numerator_yen")
    denominator_yen = parse_positive_decimal(relative_tick_size["denominator_yen"], "denominator_yen")
    threshold_ratio = rules["maximum_relative_tick_size"]["threshold_ratio"]
    tick_pass = relative_tick_within_threshold(
        numerator_yen=numerator_yen,
        denominator_yen=denominator_yen,
        threshold_numerator=threshold_ratio["numerator"],
        threshold_denominator=threshold_ratio["denominator"],
    )
    tick_eval = {
        "rule_id": "maximum_relative_tick_size",
        "reason_code": "RELATIVE_TICK",
        "status": "PASS" if tick_pass else "REJECT",
        "observed_ratio": {
            "numerator_yen": str(numerator_yen),
            "denominator_yen": str(denominator_yen),
        },
        "threshold_ratio": threshold_ratio,
    }

    return [turnover_eval, tick_eval]


def _hash_hex(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _hard_error("SELECTION_INPUT_HASHES_INVALID", f"{name} must be 64 lowercase hex chars")
    return value


def build_selection(
    *,
    ranking: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, Any],
) -> dict[str, Any]:
    """Build the Selection v1 decision payload.

    Fixed processing order:
      1. selection.enabled must be True.
      2. Validate ranking integrity and extract Rank 1.
      3. Validate/chain input_hashes against ranking.
      4. Evaluate both rules (no short-circuit).
      5. Determine status and build the payload.
    """
    selection_config = config.get("selection")
    if not isinstance(selection_config, dict) or selection_config.get("enabled") is not True:
        _hard_error("SELECTION_CONFIG_DISABLED", "selection.enabled must be true to build a selection")

    rank1 = validate_rank1_integrity(ranking)

    if set(input_hashes) != {"ranking_sha256", "strategy_snapshot_sha256"}:
        _hard_error(
            "SELECTION_INPUT_HASHES_INVALID",
            "input_hashes must contain exactly {ranking_sha256, strategy_snapshot_sha256}",
        )
    ranking_sha256 = _hash_hex(input_hashes["ranking_sha256"], "input_hashes.ranking_sha256")
    strategy_snapshot_sha256 = _hash_hex(
        input_hashes["strategy_snapshot_sha256"], "input_hashes.strategy_snapshot_sha256"
    )
    ranking_strategy_snapshot_sha256 = ranking.get("input_hashes", {}).get("strategy_snapshot_sha256")
    if strategy_snapshot_sha256 != ranking_strategy_snapshot_sha256:
        _hard_error(
            "SELECTION_HASH_CHAIN_MISMATCH",
            "input_hashes.strategy_snapshot_sha256 must match ranking.input_hashes.strategy_snapshot_sha256",
        )
    from src.config import strategy_config_sha256

    if ranking.get("config_sha256") != strategy_config_sha256(config):
        _hard_error("SELECTION_HASH_CHAIN_MISMATCH", "ranking.config_sha256 does not match --config")
    if ranking.get("strategy_version") != config.get("strategy_version"):
        _hard_error("SELECTION_HASH_CHAIN_MISMATCH", "ranking.strategy_version does not match --config")

    rule_evaluations = evaluate_selection_rules(rank1, selection_config)
    all_passed = all(item["status"] == "PASS" for item in rule_evaluations)

    if all_passed:
        status = "SELECTED"
        selected_ticker = rank1["ticker"]
        reason_codes = ["SELECTION_ALL_RULES_PASSED"]
    else:
        status = "NO_TRADE"
        selected_ticker = None
        reason_codes = [
            item["reason_code"] for item in rule_evaluations if item["status"] == "REJECT"
        ]

    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_version": SELECTION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": ranking["target_date"],
        "previous_trading_day": ranking["previous_trading_day"],
        "strategy_version": ranking["strategy_version"],
        "config_sha256": ranking["config_sha256"],
        "input_hashes": {
            "ranking_sha256": ranking_sha256,
            "strategy_snapshot_sha256": strategy_snapshot_sha256,
        },
        "rank1_ticker": rank1["ticker"],
        "selection_status": status,
        "selected_ticker": selected_ticker,
        "reason_codes": reason_codes,
        "rule_evaluations": rule_evaluations,
    }
