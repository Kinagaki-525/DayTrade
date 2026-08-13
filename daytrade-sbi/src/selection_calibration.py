"""Selection Calibration v1: threshold-sensitivity report over historical
Rank 1 observations.

Unlike the pure computation helpers below (threshold-sensitivity math, exact
ratio canonicalization/comparison), the orchestration code in this module
performs real filesystem I/O: it walks ``runs_dir``, reads and hashes every
per-run artifact (``ranking.json`` and the full upstream chain it claims to
have been built from -- ``event_gate.json``, ``candidates.json``,
``market_data.json``, ``sources.json``, ``strategy_snapshot.yaml`` -- plus
the caller-supplied ``--source-matrix`` file), and re-verifies each
``ranking.json`` against that upstream chain using the same Ranking Contract
functions the Ranking CLI itself uses (``validate_ranking_preconditions`` /
``validate_ranking_output_contract``). A run directory whose ranking.json is
schema-valid but was not genuinely produced from its claimed upstream
artifacts is a Hard Error, never a silently-skipped observation.

It reads a set of already-produced per-day run directories
(``runs_dir/YYYY-MM-DD/``) that share one (strategy_version, config_sha256,
source_matrix_sha256) cohort and reports, per distinct observed feature
value, how many historical Rank 1 observations would PASS/REJECT a given
minimum-turnover or maximum-relative-tick-size threshold.

It never optimizes for profit, never recommends a threshold, and never
writes to ``config/strategy.yaml``. It only ever evaluates Rank 1 -- there is
no Rank 2 fallback anywhere in this module, under any circumstance.
"""

from __future__ import annotations

import hashlib
import json
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
    """Observation ID per spec section 24: canonical-JSON SHA256 over
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
# Candidate generation (spec section 33-37): observed values only. No
# quantiles, grids, percentiles, top-N, every-Nth-value, or random sampling.
# ---------------------------------------------------------------------------


def generate_turnover_candidates(observations: list[dict[str, Any]]) -> list[int]:
    """Distinct observed turnover_yen values, ascending. Observed values
    only (spec section 33/35) -- never quantiles/grids/samples."""
    distinct = {int(Decimal(str(item["turnover_yen"]))) for item in observations}
    return sorted(distinct)


def generate_relative_tick_candidates(
    observations: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """Distinct observed canonical relative-tick ratios, exact-ratio
    ascending (spec section 34) -- never Python tuple lexicographic sort."""
    distinct: dict[tuple[int, int], None] = {}
    for item in observations:
        ratio = item["canonical_relative_tick_ratio"]
        distinct[(ratio["numerator"], ratio["denominator"])] = None

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
# Pair evaluation (spec section 41-46): reuses the existing Selection v1
# evaluator (evaluate_selection_rules) -- never a re-implemented rule
# evaluator. Observations are evaluated in target_date ascending order.
# ---------------------------------------------------------------------------


def evaluate_threshold_pair(
    *,
    cohort_id: str,
    minimum_turnover_yen: int,
    maximum_relative_tick_ratio: tuple[int, int],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate one Threshold Pair against all Observations (already sorted
    ascending by target_date by the caller), reusing
    ``evaluate_selection_rules`` from src.selection -- never a re-implemented
    Selection rule evaluator (INV-016)."""
    from src.selection import evaluate_selection_rules

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
        reason_codes = [
            item["reason_code"] for item in rule_evaluations if item["result"] == "REJECT"
        ]
        if not reason_codes:
            selected_count += 1
            decision_bits.append("1")
        else:
            rejected_count += 1
            decision_bits.append("0")
            if reason_codes == ["SELECTION_TURNOVER_BELOW_MINIMUM"]:
                rejected_turnover_only_count += 1
            elif reason_codes == ["SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"]:
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
        "selection_rate": {"numerator": selected_count, "denominator": observation_count}
        if observation_count
        else {"numerator": 0, "denominator": 0},
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
    deterministic order (spec section 38-39/41/45). No pair is skipped."""
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


def build_selection_calibration_report(
    *,
    runs_dir: Path,
    config_path: Path,
    source_matrix_path: Path,
) -> dict[str, Any]:
    """Build the Selection Calibration v1 report.

    This is the module's I/O-performing orchestration entrypoint: it loads
    and validates the cohort's strategy config and source matrix, scans
    ``runs_dir`` for per-day run directories, and for each run directory
    that claims to be part of this cohort, re-verifies its ``ranking.json``
    against its full upstream artifact chain using the real Ranking
    Contract functions (``validate_ranking_preconditions`` /
    ``validate_ranking_output_contract``) -- exactly as the Ranking CLI
    itself does -- before counting it as an Observation.
    """
    from src.config import load_strategy_config, strategy_config_sha256
    from src.contracts import (
        load_json_document,
        validate_json_document,
        validate_ranking_output_contract,
        validate_ranking_preconditions,
    )
    from src.source_matrix import load_source_matrix, validate_source_matrix

    cohort_config = load_strategy_config(config_path)
    cohort_strategy_version = cohort_config["strategy_version"]
    cohort_config_sha256 = strategy_config_sha256(cohort_config)

    # Startup validation: the --source-matrix file itself must be structurally
    # valid before any run-directory scanning begins.
    source_matrix_bytes = source_matrix_path.read_bytes()
    source_matrix_sha256 = _sha256_bytes(source_matrix_bytes)
    source_matrix = load_source_matrix(source_matrix_path)
    source_matrix_result = validate_source_matrix(source_matrix)
    if not source_matrix_result.valid:
        _hard_error(
            "CALIBRATION_SOURCE_MATRIX_INVALID",
            "Source matrix validation failed: " + "; ".join(source_matrix_result.errors),
        )

    run_dates, ignored_non_date_entries = collect_run_dates(runs_dir)

    run_directories_scanned = len(run_dates)
    ranking_artifacts_found = 0
    missing_ranking_count = 0
    config_mismatch_count = 0
    source_matrix_mismatch_count = 0
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
        ranking_sha256 = _sha256_bytes(ranking_bytes)
        # Hard Error on schema failure (never silently swallow a corrupted
        # artifact); load_json_document raises ValueError with schema
        # details on failure.
        ranking = load_json_document(ranking_path, "ranking.schema.json")

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

        ranking_input_hashes = ranking.get("input_hashes") or {}
        if ranking_input_hashes.get("source_matrix_sha256") != source_matrix_sha256:
            # Treated as a legitimate cohort split (source policy changed
            # for this run), not an attack: cohort-excluded, not a Hard
            # Error.
            source_matrix_mismatch_count += 1
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

        strategy_snapshot_path = run_dir / "strategy_snapshot.yaml"
        event_gate_path = run_dir / "event_gate.json"
        candidates_path = run_dir / "candidates.json"
        market_data_path = run_dir / "market_data.json"
        sources_path = run_dir / "sources.json"

        actual_hashes = {
            "event_gate_sha256": _sha256_bytes(event_gate_path.read_bytes()),
            "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
            "market_data_sha256": _sha256_bytes(market_data_path.read_bytes()),
            "sources_sha256": _sha256_bytes(sources_path.read_bytes()),
            "source_matrix_sha256": source_matrix_sha256,
            "strategy_snapshot_sha256": _sha256_bytes(strategy_snapshot_path.read_bytes()),
        }
        for hash_key, actual_value in actual_hashes.items():
            recorded_value = ranking_input_hashes.get(hash_key)
            if recorded_value != actual_value:
                _hard_error(
                    "CALIBRATION_INPUT_HASH_MISMATCH",
                    f"run directory {run_dir}: ranking.input_hashes.{hash_key} "
                    f"({recorded_value!r}) does not match the actual sha256 of the "
                    f"upstream artifact on disk ({actual_value!r})",
                )

        run_config = load_strategy_config(strategy_snapshot_path)
        run_config_sha256 = strategy_config_sha256(run_config)
        if (
            ranking.get("strategy_version") != run_config.get("strategy_version")
            or ranking.get("config_sha256") != run_config_sha256
            or run_config.get("strategy_version") != cohort_strategy_version
            or run_config_sha256 != cohort_config_sha256
        ):
            _hard_error(
                "CALIBRATION_STRATEGY_SNAPSHOT_MISMATCH",
                f"run directory {run_dir}: strategy_snapshot.yaml does not match "
                "ranking.json's recorded strategy_version/config_sha256 and the "
                "calibration cohort",
            )

        event_gate = load_json_document(event_gate_path, "event_gate.schema.json")
        candidates = load_json_document(candidates_path, "candidates.schema.json")
        market_data = load_json_document(market_data_path, "market_data.schema.json")
        source_payload = load_json_document(sources_path, "sources.schema.json")

        # Full Ranking Contract re-verification, exactly as build-ranking's
        # own CLI path performs it, applying to both COMPLETE and
        # DATA_UNAVAILABLE rankings alike -- a corrupted DATA_UNAVAILABLE
        # artifact must not be silently accepted either.
        try:
            validate_ranking_preconditions(
                event_gate=event_gate,
                candidates=candidates,
                market_data=market_data,
                source_payload=source_payload,
                source_matrix=source_matrix,
                config=run_config,
                input_hashes=ranking_input_hashes,
                source_base_dir=run_dir,
            )
            validate_ranking_output_contract(
                ranking=ranking,
                event_gate=event_gate,
                candidates=candidates,
                market_data=market_data,
                source_payload=source_payload,
                config=run_config,
            )
        except ValueError as exc:
            _hard_error(
                "CALIBRATION_RANKING_CONTRACT_VIOLATION",
                f"run directory {run_dir}: ranking.json failed full Ranking "
                f"Contract re-verification against its upstream artifacts: {exc}",
            )

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
            "source_matrix_mismatch_count": source_matrix_mismatch_count,
            "missing_ranking_count": missing_ranking_count,
            "ignored_non_date_entries": ignored_non_date_entries,
        },
        "observations": observations,
        "minimum_turnover_sensitivity": turnover_sensitivity,
        "maximum_relative_tick_size_sensitivity": tick_sensitivity,
    }


def _build_turnover_sensitivity(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure computation: no I/O. Builds the minimum-turnover sensitivity
    table from already-loaded observations only."""
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
    """Pure computation: no I/O. Builds the maximum-relative-tick-size
    sensitivity table from already-loaded observations only."""
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
    """Pure computation: no I/O. Classifies each calibration observation
    SELECTED/NO_TRADE against a caller-supplied threshold pair, reusing
    Selection v1's exact rule evaluation logic. Never writes config, never
    suggests a threshold."""
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
