"""Selection Calibration v1: pure, deterministic threshold-sensitivity report.

This module never performs I/O, never touches the network, and never uses
randomness. It reads a set of already-produced per-day run directories
(``runs_dir/YYYY-MM-DD/ranking.json``) that share one (strategy_version,
config_sha256) cohort and reports, per distinct observed feature value, how
many historical Rank 1 observations would PASS/REJECT a given
minimum-turnover or maximum-relative-tick-size threshold.

It never optimizes for profit, never recommends a threshold, and never
writes to ``config/strategy.yaml``. It only ever evaluates Rank 1 -- there is
no Rank 2 fallback anywhere in this module, under any circumstance.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.selection import (
    SelectionHardError,
    canonicalize_decimal_ratio,
    compare_exact_ratios,
    validate_rank1_integrity,
)


CALIBRATION_VERSION = "selection-calibration-v1"
CALIBRATION_SCHEMA_VERSION = 1


class SelectionCalibrationHardError(SelectionHardError):
    """Raised for any Selection Calibration precondition/integrity violation."""


def _hard_error(code: str, message: str) -> None:
    raise SelectionCalibrationHardError(f"{code}: {message}")


def _is_iso_date(name: str) -> bool:
    try:
        date.fromisoformat(name)
    except ValueError:
        return False
    return True


def collect_run_dates(runs_dir: Path) -> tuple[list[str], int]:
    """Return (sorted ISO-date directory names, ignored_non_date_entries).

    Scans only direct children of runs_dir; no recursion.
    """
    if not runs_dir.is_dir():
        _hard_error("CALIBRATION_RUNS_DIR_INVALID", f"runs_dir is not a directory: {runs_dir}")
    kept: list[str] = []
    ignored = 0
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            ignored += 1
            continue
        if _is_iso_date(entry.name):
            kept.append(entry.name)
        else:
            ignored += 1
    kept.sort()
    return kept, ignored


def _decimal_str(value: Decimal) -> str:
    return str(value)


def build_selection_calibration_report(
    *,
    runs_dir: Path,
    cohort_strategy_version: str,
    cohort_config_sha256: str,
    source_matrix_sha256: str,
    validate_json_document: Any,
) -> dict[str, Any]:
    """Build the Selection Calibration v1 report.

    ``validate_json_document`` is injected (rather than imported) so this
    module stays free of a hard import-time dependency on the schema
    validation utility's exact module location; callers pass
    ``src.contracts.validate_json_document``.
    """
    run_dates, ignored_non_date_entries = collect_run_dates(runs_dir)

    run_directories_scanned = len(run_dates)
    ranking_artifacts_found = 0
    missing_ranking_count = 0
    config_mismatch_count = 0
    ranking_data_unavailable_count = 0
    observations: list[dict[str, Any]] = []

    for target_date in run_dates:
        run_dir = runs_dir / target_date
        ranking_path = run_dir / "ranking.json"
        if not ranking_path.is_file():
            missing_ranking_count += 1
            continue
        ranking_artifacts_found += 1

        ranking_bytes = ranking_path.read_bytes()
        import json as _json

        ranking = _json.loads(ranking_bytes.decode("utf-8"), parse_float=Decimal, parse_int=int)
        # Hard Error on schema failure (never silently swallow a corrupted
        # artifact). This raises ValueError with schema details on failure.
        validate_json_document(ranking, "ranking.schema.json")

        if ranking.get("target_date") != target_date:
            _hard_error(
                "CALIBRATION_RANKING_TARGET_DATE_MISMATCH",
                f"ranking.json in {run_dir} has target_date "
                f"{ranking.get('target_date')!r} but directory is {target_date!r}",
            )

        if (
            ranking.get("strategy_version") != cohort_strategy_version
            or ranking.get("config_sha256") != cohort_config_sha256
        ):
            config_mismatch_count += 1
            continue

        if ranking.get("ranking_status") == "DATA_UNAVAILABLE":
            ranking_data_unavailable_count += 1
            continue

        rank1 = validate_rank1_integrity(ranking)
        feature_values = rank1["feature_values"]
        turnover_yen = Decimal(str(feature_values["turnover_yen"]))
        tick_size_yen = Decimal(str(feature_values["tick_size_yen"]))
        entry_trigger_yen = Decimal(str(feature_values["entry_trigger_yen"]))
        relative_tick_size = feature_values["relative_tick_size"]
        numerator_yen = Decimal(str(relative_tick_size["numerator_yen"]))
        denominator_yen = Decimal(str(relative_tick_size["denominator_yen"]))
        canonical_num, canonical_den = canonicalize_decimal_ratio(numerator_yen, denominator_yen)

        ranking_sha256 = hashlib.sha256(ranking_bytes).hexdigest()

        observations.append(
            {
                "target_date": target_date,
                "previous_trading_day": ranking["previous_trading_day"],
                "ranking_sha256": ranking_sha256,
                "strategy_version": ranking["strategy_version"],
                "config_sha256": ranking["config_sha256"],
                "ticker": rank1["ticker"],
                "turnover_yen": _decimal_str(turnover_yen),
                "tick_size_yen": _decimal_str(tick_size_yen),
                "entry_trigger_yen": _decimal_str(entry_trigger_yen),
                "relative_tick_size": {
                    "numerator_yen": _decimal_str(numerator_yen),
                    "denominator_yen": _decimal_str(denominator_yen),
                },
                "canonical_relative_tick_ratio": {
                    "numerator": canonical_num,
                    "denominator": canonical_den,
                },
            }
        )

    observations.sort(key=lambda item: item["target_date"])

    turnover_sensitivity = _build_turnover_sensitivity(observations)
    tick_sensitivity = _build_tick_sensitivity(observations)

    calibration_status = "COMPLETE" if observations else "NO_OBSERVATIONS"

    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calibration_status": calibration_status,
        "strategy_version": cohort_strategy_version,
        "config_sha256": cohort_config_sha256,
        "source_matrix_sha256": source_matrix_sha256,
        "summary": {
            "run_directories_scanned": run_directories_scanned,
            "ranking_artifacts_found": ranking_artifacts_found,
            "matching_complete_observations": len(observations),
            "ranking_data_unavailable_count": ranking_data_unavailable_count,
            "config_mismatch_count": config_mismatch_count,
            "missing_ranking_count": missing_ranking_count,
            "ignored_non_date_entries": ignored_non_date_entries,
        },
        "observations": observations,
        "minimum_turnover_sensitivity": turnover_sensitivity,
        "maximum_relative_tick_size_sensitivity": tick_sensitivity,
    }


def _build_turnover_sensitivity(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not observations:
        return []
    total = len(observations)
    turnover_values = sorted(
        {Decimal(item["turnover_yen"]) for item in observations}
    )
    entries: list[dict[str, Any]] = []
    for threshold in turnover_values:
        pass_count = sum(1 for item in observations if Decimal(item["turnover_yen"]) >= threshold)
        reject_count = total - pass_count
        entries.append(
            {
                "threshold_yen": int(threshold),
                "pass_count": pass_count,
                "reject_count": reject_count,
                "pass_rate": {"numerator": pass_count, "denominator": total},
            }
        )
    return entries


def _build_tick_sensitivity(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not observations:
        return []
    total = len(observations)
    distinct_ratios: dict[tuple[int, int], None] = {}
    for item in observations:
        ratio = item["canonical_relative_tick_ratio"]
        distinct_ratios[(ratio["numerator"], ratio["denominator"])] = None

    def _sort_key(ratio: tuple[int, int]):
        return ratio

    # Sort ascending by exact ratio value via cross-multiplication, using a
    # simple insertion-style ordering built on compare_exact_ratios.
    ordered: list[tuple[int, int]] = []
    for ratio in distinct_ratios:
        inserted = False
        for index, existing in enumerate(ordered):
            if compare_exact_ratios(ratio[0], ratio[1], existing[0], existing[1]) < 0:
                ordered.insert(index, ratio)
                inserted = True
                break
        if not inserted:
            ordered.append(ratio)

    entries: list[dict[str, Any]] = []
    for threshold_num, threshold_den in ordered:
        pass_count = 0
        for item in observations:
            ratio = item["canonical_relative_tick_ratio"]
            if (
                compare_exact_ratios(
                    ratio["numerator"], ratio["denominator"], threshold_num, threshold_den
                )
                <= 0
            ):
                pass_count += 1
        reject_count = total - pass_count
        entries.append(
            {
                "threshold_ratio": {"numerator": threshold_num, "denominator": threshold_den},
                "pass_count": pass_count,
                "reject_count": reject_count,
                "pass_rate": {"numerator": pass_count, "denominator": total},
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Threshold evaluation (separate CLI: evaluate-selection-thresholds).
# ---------------------------------------------------------------------------


def evaluate_calibration_against_thresholds(
    *,
    calibration_report: dict[str, Any],
    source_calibration_sha256: str,
    minimum_turnover_yen: int,
    maximum_relative_tick_numerator: int,
    maximum_relative_tick_denominator: int,
) -> dict[str, Any]:
    """Classify each calibration observation SELECTED/NO_TRADE against a
    caller-supplied threshold pair, reusing Selection v1's exact rule
    evaluation logic. Never writes config, never suggests a threshold."""
    import math

    from src.selection import evaluate_selection_rules

    if calibration_report.get("calibration_status") == "NO_OBSERVATIONS" or not calibration_report.get(
        "observations"
    ):
        _hard_error(
            "CALIBRATION_NO_OBSERVATIONS",
            "calibration report has no observations; cannot evaluate thresholds",
        )

    for name, value in (
        ("minimum_turnover_yen", minimum_turnover_yen),
        ("maximum_relative_tick_numerator", maximum_relative_tick_numerator),
        ("maximum_relative_tick_denominator", maximum_relative_tick_denominator),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _hard_error(
                "CALIBRATION_THRESHOLD_INVALID", f"{name} must be a positive plain integer"
            )
    if math.gcd(maximum_relative_tick_numerator, maximum_relative_tick_denominator) != 1:
        _hard_error(
            "CALIBRATION_THRESHOLD_INVALID",
            "maximum_relative_tick threshold ratio must be expressed in lowest terms (gcd == 1)",
        )

    selection_config = {
        "rules": {
            "minimum_turnover_yen": {"threshold_yen": minimum_turnover_yen},
            "maximum_relative_tick_size": {
                "threshold_ratio": {
                    "numerator": maximum_relative_tick_numerator,
                    "denominator": maximum_relative_tick_denominator,
                }
            },
        }
    }

    selected_count = 0
    no_trade_count = 0
    rejected_turnover_only_count = 0
    rejected_relative_tick_only_count = 0
    rejected_both_count = 0
    selected_observations: list[dict[str, Any]] = []
    rejected_observations: list[dict[str, Any]] = []

    for observation in calibration_report["observations"]:
        rank1 = {
            "ticker": observation["ticker"],
            "feature_values": {
                "turnover_yen": observation["turnover_yen"],
                "relative_tick_size": {
                    "numerator_yen": observation["relative_tick_size"]["numerator_yen"],
                    "denominator_yen": observation["relative_tick_size"]["denominator_yen"],
                },
            },
        }
        rule_evaluations = evaluate_selection_rules(rank1, selection_config)
        reason_codes = [item["reason_code"] for item in rule_evaluations if item["result"] == "REJECT"]

        if not reason_codes:
            selected_count += 1
            selected_observations.append(
                {"target_date": observation["target_date"], "ticker": observation["ticker"]}
            )
        else:
            no_trade_count += 1
            rejected_observations.append(
                {
                    "target_date": observation["target_date"],
                    "ticker": observation["ticker"],
                    "reason_codes": reason_codes,
                }
            )
            if reason_codes == ["SELECTION_TURNOVER_BELOW_MINIMUM"]:
                rejected_turnover_only_count += 1
            elif reason_codes == ["SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"]:
                rejected_relative_tick_only_count += 1
            else:
                rejected_both_count += 1

    total = len(calibration_report["observations"])

    return {
        "schema_version": 1,
        "evaluation_version": "selection-threshold-evaluation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_calibration_sha256": source_calibration_sha256,
        "minimum_turnover_yen": minimum_turnover_yen,
        "maximum_relative_tick_size": {
            "numerator": maximum_relative_tick_numerator,
            "denominator": maximum_relative_tick_denominator,
        },
        "observation_count": total,
        "selected_count": selected_count,
        "no_trade_count": no_trade_count,
        "selection_rate": {"numerator": selected_count, "denominator": total},
        "rejected_turnover_only_count": rejected_turnover_only_count,
        "rejected_relative_tick_only_count": rejected_relative_tick_only_count,
        "rejected_both_count": rejected_both_count,
        "selected_observations": selected_observations,
        "rejected_observations": rejected_observations,
    }
