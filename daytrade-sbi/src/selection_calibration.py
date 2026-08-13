"""Selection Calibration v1: threshold-sensitivity report over historical
Rank 1 observations.

Schema v2 authoritative flow. Trust-before-Cohort ordering (spec section 4):

  ranking.json discovery
  -> ranking raw bytes read
  -> ranking raw sha256 computed
  -> ranking schema validation
  -> target_date check
  -> ranking.input_hashes.source_matrix_sha256 read (path-resolution hint only)
  -> Historical Source Matrix Path resolution
  -> load_and_verify_ranking_trust_chain()
  -> VerifiedRankingBundle obtained
  -> ranking_status determined
  -> Calibration Context generated
  -> Cohort ID generated
  -> compare against Target Cohort
  -> Observation accepted/excluded

Cohort comparison is only ever performed AFTER
``load_and_verify_ranking_trust_chain`` has succeeded. Before Trust
succeeds, the only permitted uses of ranking.json contents are: schema
validation, target_date check, and reading the expected source_matrix SHA
as a hint for where to look for the historical Source Matrix file (never as
trusted business data for Cohort decisions).

It never optimizes for profit, never recommends a threshold, and never
writes to ``config/strategy.yaml``. It only ever evaluates Rank 1 -- there is
no Rank 2 fallback anywhere in this module, under any circumstance.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
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
CALIBRATION_SCHEMA_VERSION = 2

_ALLOWED_REJECTION_REASON_SETS = (
    frozenset({"SELECTION_TURNOVER_BELOW_MINIMUM"}),
    frozenset({"SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"}),
    frozenset(
        {"SELECTION_TURNOVER_BELOW_MINIMUM", "SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"}
    ),
)


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_REQUIRED_UPSTREAM_ARTIFACTS = (
    "strategy_snapshot.yaml",
    "event_gate.json",
    "candidates.json",
    "market_data.json",
    "sources.json",
)


# ---------------------------------------------------------------------------
# Canonical JSON hashing vs. raw-byte hashing (kept as two distinct helpers
# per spec INV-010/INV-011: raw-byte hashing verifies Artifact identity;
# canonical-JSON hashing derives semantic identity such as cohort_id,
# observation_id, pair_id, and calibration_context_sha256. The two must
# never be interchanged.)
# ---------------------------------------------------------------------------


def sha256_raw_bytes(data: bytes) -> str:
    """Raw-byte SHA256, used for Artifact identity verification (INV-010).

    This is semantically distinct from ``sha256_canonical_json`` below --
    never substitute one for the other.
    """
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic canonical JSON encoding used for semantic-identity
    hashing only (cohort_id / observation_id / pair_id /
    calibration_context_sha256). Never used for Artifact byte-identity
    verification (INV-011).

    Contract: UTF-8, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False, NaN/Infinity rejected (allow_nan=False).
    """

    def _default(obj: Any) -> Any:
        raise TypeError(f"canonical_json_bytes: unsupported type {type(obj)!r}")

    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_default,
    )
    return text.encode("utf-8")


def sha256_canonical_json(value: Any) -> str:
    """SHA256 over ``canonical_json_bytes(value)``. Semantic-identity hash
    only -- never used for raw Artifact byte-identity verification."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


# ---------------------------------------------------------------------------
# Calibration Context / Cohort Identity (spec section 25-30).
#
# Only these three mutable Selection config paths are excluded from the
# Calibration Context -- nothing else (INV-013). The full config_sha256 is
# never used directly as the Cohort key (INV-012); instead a
# calibration_context_sha256 is derived over the config with exactly these
# three paths removed.
# ---------------------------------------------------------------------------

CALIBRATION_CONTEXT_EXCLUDED_PATHS: tuple[tuple[str, ...], ...] = (
    ("selection", "enabled"),
    ("selection", "rules", "minimum_turnover_yen", "threshold_yen"),
    ("selection", "rules", "maximum_relative_tick_size", "threshold_ratio"),
)


def _delete_path(container: dict[str, Any], path: tuple[str, ...]) -> None:
    node = container
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return
        node = node[key]
    if isinstance(node, dict):
        node.pop(path[-1], None)


def compute_calibration_context(validated_config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``validated_config`` with exactly the three
    allowed mutable Selection paths removed. Never mutates the input
    (spec section 27)."""
    import copy

    context = copy.deepcopy(validated_config)
    for path in CALIBRATION_CONTEXT_EXCLUDED_PATHS:
        _delete_path(context, path)
    return context


def compute_calibration_context_sha256(validated_config: dict[str, Any]) -> str:
    """sha256_canonical_json of the Calibration Context (spec section 27)."""
    return sha256_canonical_json(compute_calibration_context(validated_config))


def compute_cohort_id(
    *,
    strategy_version: str,
    ranking_version: str,
    ranking_schema_version: int,
    selection_version: str,
    calibration_context_sha256: str,
    source_matrix_raw_sha256: str,
) -> str:
    """Cohort ID per spec section 30: canonical-JSON SHA256 over exactly
    these six fields. UUIDs are never used (INV-014/INV-024 determinism)."""
    key = {
        "strategy_version": strategy_version,
        "ranking_version": ranking_version,
        "ranking_schema_version": ranking_schema_version,
        "selection_version": selection_version,
        "calibration_context_sha256": calibration_context_sha256,
        "source_matrix_raw_sha256": source_matrix_raw_sha256,
    }
    return sha256_canonical_json(key)


def compute_observation_id(
    *, cohort_id: str, target_date: str, ranking_raw_sha256: str
) -> str:
    """Observation ID per spec section 24/17: canonical-JSON SHA256 over
    (cohort_id, target_date, ranking_raw_sha256). UUIDs are never used."""
    return sha256_canonical_json(
        {
            "cohort_id": cohort_id,
            "target_date": target_date,
            "ranking_raw_sha256": ranking_raw_sha256,
        }
    )


def compute_pair_id(
    *,
    cohort_id: str,
    minimum_turnover_yen: int,
    maximum_relative_tick_numerator: int,
    maximum_relative_tick_denominator: int,
) -> str:
    """Pair ID per spec section 40: canonical-JSON SHA256 over cohort_id,
    minimum_turnover_yen, and the canonical maximum_relative_tick_size
    ratio. UUIDs are never used."""
    return sha256_canonical_json(
        {
            "cohort_id": cohort_id,
            "minimum_turnover_yen": minimum_turnover_yen,
            "maximum_relative_tick_size": {
                "numerator": maximum_relative_tick_numerator,
                "denominator": maximum_relative_tick_denominator,
            },
        }
    )


# ---------------------------------------------------------------------------
# Historical Source Matrix resolution (spec section 7/9).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceMatrixResolution:
    """Outcome of resolving the historical Source Matrix bytes needed to
    re-verify a given run's Ranking Trust Chain."""

    status: str  # "RESOLVED" or "UNVERIFIED"
    path: Path | None
    reason_code: str | None


def resolve_historical_source_matrix_path(
    *,
    expected_source_matrix_sha256: str,
    target_source_matrix_path: Path,
    source_matrix_registry_dir: Path | None,
) -> SourceMatrixResolution:
    """Resolve which Source Matrix file's bytes to use for Trust Chain
    re-verification of a historical run (spec section 7/9).

    1. If the caller-supplied --source-matrix file's raw SHA256 already
       matches ``expected_source_matrix_sha256`` -- use it directly.
    2. Otherwise look in ``source_matrix_registry_dir`` for
       ``<expected_sha>.yaml``.
    3. If absent -- UNVERIFIED / SOURCE_MATRIX_BYTES_UNAVAILABLE (not a Hard
       Error; the run is excluded, not an Observation).
    4. If present but its actual byte SHA256 does not match its own
       filename -- Hard Error CALIBRATION_SOURCE_MATRIX_HASH_MISMATCH.
    """
    target_bytes = target_source_matrix_path.read_bytes()
    if sha256_raw_bytes(target_bytes) == expected_source_matrix_sha256:
        return SourceMatrixResolution("RESOLVED", target_source_matrix_path, None)

    if source_matrix_registry_dir is not None:
        candidate_path = source_matrix_registry_dir / f"{expected_source_matrix_sha256}.yaml"
        if candidate_path.is_file():
            candidate_bytes = candidate_path.read_bytes()
            actual_sha256 = sha256_raw_bytes(candidate_bytes)
            if actual_sha256 != expected_source_matrix_sha256:
                _hard_error(
                    "CALIBRATION_SOURCE_MATRIX_HASH_MISMATCH",
                    f"registry file {candidate_path} is named for SHA256 "
                    f"{expected_source_matrix_sha256} but its actual byte SHA256 is "
                    f"{actual_sha256}",
                )
            return SourceMatrixResolution("RESOLVED", candidate_path, None)

    return SourceMatrixResolution("UNVERIFIED", None, "SOURCE_MATRIX_BYTES_UNAVAILABLE")


# ---------------------------------------------------------------------------
# Candidate generation (spec section 19/33-37): observed values only. No
# quantiles, grids, percentiles, top-N, every-Nth-value, or random sampling.
# Reads the nested Observation shape: item["rank1"]["turnover_yen"] /
# item["rank1"]["relative_tick_size"].
# ---------------------------------------------------------------------------


def generate_turnover_candidates(observations: list[dict[str, Any]]) -> list[int]:
    """Distinct observed turnover_yen values, ascending. Observed values
    only (spec section 33/35) -- never quantiles/grids/samples."""
    distinct = {int(item["rank1"]["turnover_yen"]) for item in observations}
    return sorted(distinct)


def generate_relative_tick_candidates(
    observations: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """Distinct observed canonical relative-tick ratios, exact-ratio
    ascending (spec section 34) -- never Python tuple lexicographic sort.

    Fails closed (spec section 20) if any Observation's ratio is not
    already a canonically-reduced positive fraction -- never silently
    re-reduces a non-canonical ratio here.
    """
    distinct: dict[tuple[int, int], None] = {}
    for item in observations:
        ratio = item["rank1"]["relative_tick_size"]
        numerator = ratio["numerator"]
        denominator = ratio["denominator"]
        if numerator <= 0 or denominator <= 0 or math.gcd(numerator, denominator) != 1:
            _hard_error(
                "CALIBRATION_RATIO_NOT_CANONICAL",
                f"observation {item.get('observation_id')} relative_tick_size "
                f"{numerator}/{denominator} is not a canonically-reduced positive fraction",
            )
        distinct[(numerator, denominator)] = None

    ordered: list[tuple[int, int]] = []
    for ratio in distinct:
        inserted = False
        for index, existing in enumerate(ordered):
            if compare_exact_ratios(ratio[0], ratio[1], existing[0], existing[1]) < 0:
                ordered.insert(index, ratio)
                inserted = True
                break
        if not inserted:
            ordered.append(ratio)
    return ordered


# ---------------------------------------------------------------------------
# Threshold Pair generation (spec section 38-39): full Cartesian product,
# fixed deterministic ordering (turnover ascending, then relative-tick exact
# ascending). Nothing is thinned.
# ---------------------------------------------------------------------------


def generate_threshold_pairs(
    turnover_candidates: list[int], relative_tick_candidates: list[tuple[int, int]]
) -> list[tuple[int, tuple[int, int]]]:
    pairs: list[tuple[int, tuple[int, int]]] = []
    for turnover in turnover_candidates:
        for ratio in relative_tick_candidates:
            pairs.append((turnover, ratio))
    return pairs


# ---------------------------------------------------------------------------
# Pair evaluation (spec section 21-26/41-46): reuses the existing Selection
# v1 evaluator (evaluate_selection_rules) -- never a re-implemented rule
# evaluator. Observations are evaluated in target_date ascending order.
# Reads the nested Observation shape:
# observation["rank1"]["ticker"] / ["turnover_yen"] / ["relative_tick_size"].
# ---------------------------------------------------------------------------


def evaluate_threshold_pair(
    *,
    cohort_id: str,
    minimum_turnover_yen: int,
    maximum_relative_tick_ratio: tuple[int, int],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate one Threshold Pair against all Observations, reusing
    ``evaluate_selection_rules`` from src.selection -- never a re-implemented
    Selection rule evaluator (INV-016)."""
    from src.selection import evaluate_selection_rules

    if not observations:
        _hard_error(
            "CALIBRATION_NO_OBSERVATIONS",
            "cannot evaluate a threshold pair with zero observations",
        )

    numerator, denominator = maximum_relative_tick_ratio
    selection_config = {
        "rules": {
            "minimum_turnover_yen": {"threshold_yen": minimum_turnover_yen},
            "maximum_relative_tick_size": {
                "threshold_ratio": {"numerator": numerator, "denominator": denominator}
            },
        }
    }

    selected_count = 0
    rejected_count = 0
    rejected_turnover_only_count = 0
    rejected_relative_tick_only_count = 0
    rejected_both_count = 0
    decision_bits: list[str] = []

    ordered_observations = sorted(observations, key=lambda item: item["target_date"])

    for observation in ordered_observations:
        rank1_obs = observation["rank1"]
        rank1 = {
            "ticker": rank1_obs["ticker"],
            "feature_values": {
                "turnover_yen": rank1_obs["turnover_yen"],
                "relative_tick_size": {
                    "numerator_yen": rank1_obs["relative_tick_size"]["numerator"],
                    "denominator_yen": rank1_obs["relative_tick_size"]["denominator"],
                },
            },
        }
        rule_evaluations = evaluate_selection_rules(rank1, selection_config)
        reason_codes = [
            item["reason_code"] for item in rule_evaluations if item["result"] == "REJECT"
        ]
        if not reason_codes:
            selected_count += 1
            decision_bits.append("1")
            continue

        reason_set = frozenset(reason_codes)
        if reason_set not in _ALLOWED_REJECTION_REASON_SETS:
            _hard_error(
                "CALIBRATION_EVALUATION_INVARIANT_VIOLATION",
                f"unrecognized rejection reason set {sorted(reason_set)!r} for "
                f"observation {observation.get('observation_id')}",
            )

        rejected_count += 1
        decision_bits.append("0")
        if reason_set == _ALLOWED_REJECTION_REASON_SETS[0]:
            rejected_turnover_only_count += 1
        elif reason_set == _ALLOWED_REJECTION_REASON_SETS[1]:
            rejected_relative_tick_only_count += 1
        else:
            rejected_both_count += 1

    observation_count = len(ordered_observations)
    if selected_count + rejected_count != observation_count:
        _hard_error(
            "CALIBRATION_EVALUATION_INVARIANT_VIOLATION",
            "selected_count + rejected_count must equal observation_count",
        )
    if (
        rejected_turnover_only_count + rejected_relative_tick_only_count + rejected_both_count
        != rejected_count
    ):
        _hard_error(
            "CALIBRATION_EVALUATION_INVARIANT_VIOLATION",
            "rejected_turnover_only_count + rejected_relative_tick_only_count + "
            "rejected_both_count must equal rejected_count",
        )

    outcome_signature_sha256 = sha256_raw_bytes("".join(decision_bits).encode("utf-8"))
    pair_id = compute_pair_id(
        cohort_id=cohort_id,
        minimum_turnover_yen=minimum_turnover_yen,
        maximum_relative_tick_numerator=numerator,
        maximum_relative_tick_denominator=denominator,
    )

    return {
        "pair_id": pair_id,
        "minimum_turnover_yen": minimum_turnover_yen,
        "maximum_relative_tick_size": {"numerator": numerator, "denominator": denominator},
        "observation_count": observation_count,
        "selected_count": selected_count,
        "rejected_count": rejected_count,
        "selection_rate": {"numerator": selected_count, "denominator": observation_count},
        "rejected_turnover_only_count": rejected_turnover_only_count,
        "rejected_relative_tick_only_count": rejected_relative_tick_only_count,
        "rejected_both_count": rejected_both_count,
        "outcome_signature_sha256": outcome_signature_sha256,
    }


def evaluate_all_threshold_pairs(
    *,
    cohort_id: str,
    turnover_candidates: list[int],
    relative_tick_candidates: list[tuple[int, int]],
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate the full Cartesian product of candidate pairs, in fixed
    deterministic order (spec section 25-26/38-39/41/45). No pair is
    skipped. Forbidden with zero observations (spec section 23)."""
    if not observations:
        _hard_error(
            "CALIBRATION_NO_OBSERVATIONS",
            "cannot evaluate threshold pairs with zero observations",
        )
    pairs = generate_threshold_pairs(turnover_candidates, relative_tick_candidates)
    evaluated = [
        evaluate_threshold_pair(
            cohort_id=cohort_id,
            minimum_turnover_yen=turnover,
            maximum_relative_tick_ratio=ratio,
            observations=observations,
        )
        for turnover, ratio in pairs
    ]
    expected_pair_count = len(turnover_candidates) * len(relative_tick_candidates)
    if len(evaluated) != expected_pair_count:
        _hard_error(
            "CALIBRATION_PAIR_COUNT_INVARIANT_VIOLATION",
            f"evaluated_pair_count ({len(evaluated)}) must equal "
            f"turnover_candidate_count * relative_tick_candidate_count "
            f"({expected_pair_count})",
        )
    return evaluated


# ---------------------------------------------------------------------------
# Authoritative orchestration entrypoint (I/O-performing).
# ---------------------------------------------------------------------------


def build_selection_calibration_report(
    *,
    runs_dir: Path,
    config_path: Path,
    source_matrix_path: Path,
    source_matrix_registry_dir: Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Build the Selection Calibration v1 report (Schema v2).

    Implements the Trust-before-Cohort ordering from spec section 4: Cohort
    comparison for a given run is only ever performed AFTER
    ``load_and_verify_ranking_trust_chain`` has succeeded for that run.
    Before Trust succeeds, ``ranking.json`` contents are used only for
    schema validation, the target_date check, and reading the expected
    source_matrix SHA as a path-resolution hint.
    """
    from src.config import load_strategy_config, strategy_config_sha256
    from src.contracts import load_json_document
    from src.ranking_trust import load_and_verify_ranking_trust_chain
    from src.source_matrix import load_source_matrix, validate_source_matrix

    if (start_date is None) != (end_date is None):
        _hard_error(
            "CALIBRATION_DATE_RANGE_INVALID",
            "--start-date and --end-date must both be given together, or neither given",
        )
    if start_date is not None and end_date is not None and start_date > end_date:
        _hard_error(
            "CALIBRATION_DATE_RANGE_INVALID",
            f"--start-date ({start_date}) must not be after --end-date ({end_date})",
        )

    # --- Target Cohort construction (spec section 10) ----------------------
    target_config = load_strategy_config(config_path)
    target_strategy_version = target_config["strategy_version"]
    target_selection_version = target_config["selection"]["version"]
    target_calibration_context_sha256 = compute_calibration_context_sha256(target_config)

    target_source_matrix_bytes = source_matrix_path.read_bytes()
    target_source_matrix_sha256 = sha256_raw_bytes(target_source_matrix_bytes)
    target_source_matrix = load_source_matrix(source_matrix_path)
    source_matrix_result = validate_source_matrix(target_source_matrix)
    if not source_matrix_result.valid:
        _hard_error(
            "CALIBRATION_SOURCE_MATRIX_INVALID",
            "Source matrix validation failed: " + "; ".join(source_matrix_result.errors),
        )

    target_cohort_id = compute_cohort_id(
        strategy_version=target_strategy_version,
        ranking_version="ranking-v1",
        ranking_schema_version=1,
        selection_version=target_selection_version,
        calibration_context_sha256=target_calibration_context_sha256,
        source_matrix_raw_sha256=target_source_matrix_sha256,
    )

    run_dates, ignored_non_date_entries = collect_run_dates(runs_dir)
    if start_date is not None:
        run_dates = [d for d in run_dates if start_date <= d <= end_date]

    observations: list[dict[str, Any]] = []
    excluded_observations: list[dict[str, Any]] = []
    seen_target_dates: set[str] = set()
    seen_observation_ids: set[str] = set()

    for target_date in run_dates:
        run_dir = runs_dir / target_date
        ranking_path = run_dir / "ranking.json"
        if not ranking_path.is_file():
            excluded_observations.append(
                {
                    "target_date": target_date,
                    "disposition": "UNVERIFIED",
                    "reason_code": "RANKING_ARTIFACT_MISSING",
                }
            )
            continue

        # ranking raw bytes read -> ranking raw sha256 computed
        ranking_bytes = ranking_path.read_bytes()
        ranking_raw_sha256 = sha256_raw_bytes(ranking_bytes)

        # ranking schema validation (Hard Error on schema failure -- never
        # silently swallow a corrupted artifact)
        ranking = load_json_document(ranking_path, "ranking.schema.json")

        # target_date check
        if ranking.get("target_date") != target_date:
            _hard_error(
                "CALIBRATION_RANKING_TARGET_DATE_MISMATCH",
                f"ranking.json in {run_dir} has target_date "
                f"{ranking.get('target_date')!r} but directory is {target_date!r}",
            )

        # ranking.input_hashes.source_matrix_sha256 read: path-resolution
        # hint only -- NOT used for any Cohort/business decision before
        # Trust succeeds (spec section 5).
        source_matrix_sha_hint = (ranking.get("input_hashes") or {}).get(
            "source_matrix_sha256"
        )
        if not isinstance(source_matrix_sha_hint, str):
            excluded_observations.append(
                {
                    "target_date": target_date,
                    "disposition": "UNVERIFIED",
                    "reason_code": "SOURCE_MATRIX_BYTES_UNAVAILABLE",
                }
            )
            continue

        resolution = resolve_historical_source_matrix_path(
            expected_source_matrix_sha256=source_matrix_sha_hint,
            target_source_matrix_path=source_matrix_path,
            source_matrix_registry_dir=source_matrix_registry_dir,
        )
        if resolution.status == "UNVERIFIED":
            excluded_observations.append(
                {
                    "target_date": target_date,
                    "disposition": "UNVERIFIED",
                    "reason_code": resolution.reason_code,
                }
            )
            continue

        missing_upstream = [
            name for name in _REQUIRED_UPSTREAM_ARTIFACTS if not (run_dir / name).is_file()
        ]
        if missing_upstream:
            _hard_error(
                "CALIBRATION_UPSTREAM_ARTIFACT_MISSING",
                f"run directory {run_dir} is missing required upstream artifact(s) "
                "needed to re-verify ranking.json: " + ", ".join(sorted(missing_upstream)),
            )

        # load_and_verify_ranking_trust_chain(): the ONLY point at which
        # ranking.json's business content becomes trustworthy. Any failure
        # here is a Hard Error -- tampering is never silently reclassified
        # as a cohort exclusion (spec section 4/5/6).
        try:
            bundle = load_and_verify_ranking_trust_chain(
                ranking_path=ranking_path,
                event_gate_path=run_dir / "event_gate.json",
                candidates_path=run_dir / "candidates.json",
                market_data_path=run_dir / "market_data.json",
                sources_path=run_dir / "sources.json",
                source_matrix_path=resolution.path,
                config_path=run_dir / "strategy_snapshot.yaml",
            )
        except ValueError as exc:
            _hard_error(
                "CALIBRATION_RANKING_CONTRACT_VIOLATION",
                f"run directory {run_dir}: ranking.json failed full Ranking Trust "
                f"Chain re-verification: {exc}",
            )

        verified_ranking = bundle.ranking

        # ranking_status determined
        if verified_ranking.get("ranking_status") == "DATA_UNAVAILABLE":
            excluded_observations.append(
                {
                    "target_date": target_date,
                    "disposition": "TRUSTED_NOT_ELIGIBLE",
                    "reason_code": "RANKING_DATA_UNAVAILABLE",
                }
            )
            continue

        run_ranking_version = verified_ranking.get("ranking_version")
        run_ranking_schema_version = verified_ranking.get("schema_version")
        if run_ranking_version != "ranking-v1" or run_ranking_schema_version != 1:
            _hard_error(
                "CALIBRATION_RANKING_VERSION_UNSUPPORTED",
                f"run directory {run_dir}: only ranking-v1/schema_version 1 is "
                f"supported by Selection Calibration v1, got "
                f"ranking_version={run_ranking_version!r} "
                f"schema_version={run_ranking_schema_version!r}",
            )

        # Calibration Context generated -> Cohort ID generated (only for a
        # Trust-verified run)
        run_config = bundle.config
        run_strategy_version = run_config["strategy_version"]
        run_selection_version = run_config["selection"]["version"]
        run_calibration_context_sha256 = compute_calibration_context_sha256(run_config)
        run_source_matrix_raw_sha256 = sha256_raw_bytes(resolution.path.read_bytes())

        run_cohort_id = compute_cohort_id(
            strategy_version=run_strategy_version,
            ranking_version=run_ranking_version,
            ranking_schema_version=run_ranking_schema_version,
            selection_version=run_selection_version,
            calibration_context_sha256=run_calibration_context_sha256,
            source_matrix_raw_sha256=run_source_matrix_raw_sha256,
        )

        # compare against Target Cohort (safe now: Trust already succeeded)
        if run_cohort_id != target_cohort_id:
            excluded_observations.append(
                {
                    "target_date": target_date,
                    "disposition": "OUT_OF_COHORT",
                    "reason_code": "COHORT_IDENTITY_MISMATCH",
                }
            )
            continue

        # Observation accepted -- duplicate detection (spec section 18)
        if target_date in seen_target_dates:
            _hard_error(
                "CALIBRATION_DUPLICATE_TARGET_DATE",
                f"target_date {target_date!r} appears more than once in the dataset",
            )
        seen_target_dates.add(target_date)

        rank1 = validate_rank1_integrity(verified_ranking)
        feature_values = rank1["feature_values"]

        turnover_yen_decimal = Decimal(str(feature_values["turnover_yen"]))
        if turnover_yen_decimal != turnover_yen_decimal.to_integral_value():
            _hard_error(
                "CALIBRATION_FEATURE_NOT_INTEGRAL",
                f"run directory {run_dir}: turnover_yen "
                f"{turnover_yen_decimal} is not an integral JPY amount",
            )
        turnover_yen_int = int(turnover_yen_decimal)

        relative_tick_size = feature_values["relative_tick_size"]
        numerator_yen = Decimal(str(relative_tick_size["numerator_yen"]))
        denominator_yen = Decimal(str(relative_tick_size["denominator_yen"]))
        canonical_num, canonical_den = canonicalize_decimal_ratio(numerator_yen, denominator_yen)
        if canonical_num <= 0 or canonical_den <= 0 or math.gcd(canonical_num, canonical_den) != 1:
            _hard_error(
                "CALIBRATION_RATIO_NOT_CANONICAL",
                f"run directory {run_dir}: canonicalized relative_tick_size "
                f"{canonical_num}/{canonical_den} is not a valid reduced positive fraction",
            )

        observation_id = compute_observation_id(
            cohort_id=run_cohort_id,
            target_date=target_date,
            ranking_raw_sha256=ranking_raw_sha256,
        )
        if observation_id in seen_observation_ids:
            _hard_error(
                "CALIBRATION_DUPLICATE_OBSERVATION_ID",
                f"observation_id {observation_id!r} appears more than once in the dataset",
            )
        seen_observation_ids.add(observation_id)

        observations.append(
            {
                "observation_id": observation_id,
                "target_date": target_date,
                "previous_trading_day": verified_ranking["previous_trading_day"],
                "rank1": {
                    "ticker": rank1["ticker"],
                    "turnover_yen": turnover_yen_int,
                    "relative_tick_size": {
                        "numerator": canonical_num,
                        "denominator": canonical_den,
                    },
                },
                "ranking_identity": {
                    "ranking_schema_version": run_ranking_schema_version,
                    "ranking_version": run_ranking_version,
                    "strategy_version": run_strategy_version,
                    "config_sha256": strategy_config_sha256(run_config),
                },
                "provenance": {
                    "ranking_raw_sha256": ranking_raw_sha256,
                    "event_gate_raw_sha256": bundle.actual_ranking_input_hashes[
                        "event_gate_sha256"
                    ],
                    "candidates_raw_sha256": bundle.actual_ranking_input_hashes[
                        "candidates_sha256"
                    ],
                    "market_data_raw_sha256": bundle.actual_ranking_input_hashes[
                        "market_data_sha256"
                    ],
                    "sources_raw_sha256": bundle.actual_ranking_input_hashes["sources_sha256"],
                    "source_matrix_raw_sha256": run_source_matrix_raw_sha256,
                    "strategy_snapshot_raw_sha256": bundle.actual_ranking_input_hashes[
                        "strategy_snapshot_sha256"
                    ],
                },
                "cohort_id": run_cohort_id,
            }
        )

    observations.sort(key=lambda item: item["target_date"])
    excluded_observations.sort(key=lambda item: item["target_date"])

    if observations:
        turnover_candidates = generate_turnover_candidates(observations)
        relative_tick_candidates = generate_relative_tick_candidates(observations)
        evaluated_pairs = evaluate_all_threshold_pairs(
            cohort_id=target_cohort_id,
            turnover_candidates=turnover_candidates,
            relative_tick_candidates=relative_tick_candidates,
            observations=observations,
        )
        calibration_status = "COMPLETE"
        status_reason_codes: list[str] = []
    else:
        turnover_candidates = []
        relative_tick_candidates = []
        evaluated_pairs = []
        calibration_status = "DATA_UNAVAILABLE"
        status_reason_codes = ["CALIBRATION_NO_OBSERVATIONS"]

    dataset: dict[str, Any] = {
        "observation_count": len(observations),
        "excluded_count": len(excluded_observations),
        "observations": observations,
        "excluded_observations": excluded_observations,
    }
    if start_date is not None:
        dataset["start_date"] = start_date
        dataset["end_date"] = end_date

    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "calibration_status": calibration_status,
        "status_reason_codes": status_reason_codes,
        "cohort": {
            "cohort_id": target_cohort_id,
            "strategy_version": target_strategy_version,
            "ranking_version": "ranking-v1",
            "ranking_schema_version": 1,
            "selection_version": target_selection_version,
            "calibration_context_sha256": target_calibration_context_sha256,
            "source_matrix_raw_sha256": target_source_matrix_sha256,
        },
        "dataset": dataset,
        "readiness": {"status": "NOT_ASSESSED", "policy_version": None},
        "candidate_space": {
            "turnover_candidate_count": len(turnover_candidates),
            "relative_tick_candidate_count": len(relative_tick_candidates),
            "evaluated_pair_count": len(evaluated_pairs),
            "minimum_turnover_yen": turnover_candidates,
            "maximum_relative_tick_size": [
                {"numerator": n, "denominator": d} for n, d in relative_tick_candidates
            ],
        },
        "evaluated_threshold_pairs": evaluated_pairs,
        "calibration_policy": {
            "candidate_policy": "rank1_only",
            "fallback_policy": "none",
            "rule_logic": "all",
            "candidate_generation": "observed_breakpoints",
            "performance_objective": "none",
            "automatic_threshold_selection": False,
        },
    }


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
    """Pure computation: no I/O. Classifies each calibration observation
    SELECTED/NO_TRADE against a caller-supplied threshold pair, reusing
    Selection v1's exact rule evaluation logic. Never writes config, never
    suggests a threshold. Reads the Schema v2 nested dataset.observations
    shape."""
    import math as _math

    from src.selection import evaluate_selection_rules

    dataset = calibration_report.get("dataset") or {}
    observations = dataset.get("observations") or []
    if calibration_report.get("calibration_status") == "DATA_UNAVAILABLE" or not observations:
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
    if _math.gcd(maximum_relative_tick_numerator, maximum_relative_tick_denominator) != 1:
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

    for observation in observations:
        rank1_obs = observation["rank1"]
        rank1 = {
            "ticker": rank1_obs["ticker"],
            "feature_values": {
                "turnover_yen": rank1_obs["turnover_yen"],
                "relative_tick_size": {
                    "numerator_yen": rank1_obs["relative_tick_size"]["numerator"],
                    "denominator_yen": rank1_obs["relative_tick_size"]["denominator"],
                },
            },
        }
        rule_evaluations = evaluate_selection_rules(rank1, selection_config)
        reason_codes = [item["reason_code"] for item in rule_evaluations if item["result"] == "REJECT"]

        if not reason_codes:
            selected_count += 1
            selected_observations.append(
                {"target_date": observation["target_date"], "ticker": rank1_obs["ticker"]}
            )
        else:
            no_trade_count += 1
            rejected_observations.append(
                {
                    "target_date": observation["target_date"],
                    "ticker": rank1_obs["ticker"],
                    "reason_codes": reason_codes,
                }
            )
            if reason_codes == ["SELECTION_TURNOVER_BELOW_MINIMUM"]:
                rejected_turnover_only_count += 1
            elif reason_codes == ["SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"]:
                rejected_relative_tick_only_count += 1
            else:
                rejected_both_count += 1

    total = len(observations)

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
