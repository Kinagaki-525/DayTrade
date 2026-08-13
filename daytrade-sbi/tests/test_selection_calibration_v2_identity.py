"""Selection Calibration v1: tests for the additive pure-function identity /
candidate / pair-evaluation layer (canonical hashing, calibration context,
cohort/observation/pair IDs, observed-value candidate generation, Cartesian
pair generation, and pair evaluation via the existing Selection v1
evaluator). These are pure functions with no filesystem I/O and do not
change the existing build_selection_calibration_report()/CLI output
contract -- see the Implementation Completion Report for scope notes.
"""

from __future__ import annotations

import copy

import pytest

from src.selection_calibration import (
    CALIBRATION_CONTEXT_EXCLUDED_PATHS,
    SelectionCalibrationHardError,
    canonical_json_bytes,
    compute_calibration_context,
    compute_calibration_context_sha256,
    compute_cohort_id,
    compute_observation_id,
    compute_pair_id,
    evaluate_all_threshold_pairs,
    evaluate_threshold_pair,
    generate_relative_tick_candidates,
    generate_threshold_pairs,
    generate_turnover_candidates,
    sha256_canonical_json,
    sha256_raw_bytes,
)


BASE_CONFIG = {
    "strategy_version": "v1",
    "other_block": {"x": 1, "y": [3, 2, 1]},
    "selection": {
        "enabled": False,
        "rules": {
            "minimum_turnover_yen": {"threshold_yen": None},
            "maximum_relative_tick_size": {"threshold_ratio": None},
        },
    },
}


# --- TC-HASH: canonical vs raw hashing --------------------------------------


def test_canonical_json_bytes_is_dict_order_invariant():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert canonical_json_bytes(a) == canonical_json_bytes(b)
    assert sha256_canonical_json(a) == sha256_canonical_json(b)


def test_canonical_json_bytes_value_diff_changes_hash():
    assert sha256_canonical_json({"a": 1}) != sha256_canonical_json({"a": 2})


def test_raw_hash_changes_on_whitespace_mutation_canonical_hash_does_not():
    raw_a = b'{"a":1}'
    raw_b = b'{"a": 1}'
    assert sha256_raw_bytes(raw_a) != sha256_raw_bytes(raw_b)
    # canonical hash operates on Python values, not raw bytes -- both raw
    # payloads decode to the same value and therefore the same canonical hash.
    import json

    assert sha256_canonical_json(json.loads(raw_a)) == sha256_canonical_json(json.loads(raw_b))


def test_canonical_and_raw_hash_are_distinct_functions_with_distinct_semantics():
    value = {"a": 1}
    raw = canonical_json_bytes(value)
    assert sha256_raw_bytes(raw) == sha256_canonical_json(value)
    # They coincide here only because canonical_json_bytes(value) IS the raw
    # input; in general raw hashing is applied to independently-sourced file
    # bytes, never to a re-serialization of an already-parsed value.
    assert sha256_raw_bytes(b"not json at all") != sha256_canonical_json(value)


def test_canonical_json_bytes_rejects_nan_and_infinity():
    with pytest.raises(ValueError):
        canonical_json_bytes({"a": float("nan")})
    with pytest.raises(ValueError):
        canonical_json_bytes({"a": float("inf")})


# --- TC-COHORT: Calibration Context / Cohort Identity -----------------------


def test_calibration_context_excludes_exactly_three_paths():
    context = compute_calibration_context(BASE_CONFIG)
    assert "enabled" not in context["selection"]
    assert "threshold_yen" not in context["selection"]["rules"]["minimum_turnover_yen"]
    assert "threshold_ratio" not in context["selection"]["rules"]["maximum_relative_tick_size"]
    # Everything else must survive untouched.
    assert context["other_block"] == {"x": 1, "y": [3, 2, 1]}
    assert context["strategy_version"] == "v1"
    assert len(CALIBRATION_CONTEXT_EXCLUDED_PATHS) == 3


def test_calibration_context_does_not_mutate_input():
    original = copy.deepcopy(BASE_CONFIG)
    compute_calibration_context(BASE_CONFIG)
    assert BASE_CONFIG == original


def test_cohort_id_unchanged_by_threshold_or_enabled_changes():
    config_a = copy.deepcopy(BASE_CONFIG)
    config_b = copy.deepcopy(BASE_CONFIG)
    config_b["selection"]["enabled"] = True
    config_b["selection"]["rules"]["minimum_turnover_yen"]["threshold_yen"] = 5_000_000
    config_b["selection"]["rules"]["maximum_relative_tick_size"]["threshold_ratio"] = {
        "numerator": 1,
        "denominator": 1000,
    }
    ctx_a = compute_calibration_context_sha256(config_a)
    ctx_b = compute_calibration_context_sha256(config_b)
    assert ctx_a == ctx_b

    cohort_a = compute_cohort_id(
        strategy_version="v1",
        ranking_version="ranking-v1",
        ranking_schema_version=1,
        selection_version="selection-v1",
        calibration_context_sha256=ctx_a,
        source_matrix_raw_sha256="s" * 64,
    )
    cohort_b = compute_cohort_id(
        strategy_version="v1",
        ranking_version="ranking-v1",
        ranking_schema_version=1,
        selection_version="selection-v1",
        calibration_context_sha256=ctx_b,
        source_matrix_raw_sha256="s" * 64,
    )
    assert cohort_a == cohort_b


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.__setitem__("strategy_version", "v2"),
        lambda c: c["other_block"].__setitem__("x", 999),
        lambda c: c["selection"].__setitem__("new_field", True),
    ],
)
def test_cohort_id_splits_on_any_non_excluded_change(mutate):
    config_a = copy.deepcopy(BASE_CONFIG)
    config_b = copy.deepcopy(BASE_CONFIG)
    mutate(config_b)
    ctx_a = compute_calibration_context_sha256(config_a)
    ctx_b = compute_calibration_context_sha256(config_b)
    assert ctx_a != ctx_b


def test_source_matrix_hash_change_splits_cohort():
    ctx = compute_calibration_context_sha256(BASE_CONFIG)
    cohort_a = compute_cohort_id(
        strategy_version="v1",
        ranking_version="ranking-v1",
        ranking_schema_version=1,
        selection_version="selection-v1",
        calibration_context_sha256=ctx,
        source_matrix_raw_sha256="a" * 64,
    )
    cohort_b = compute_cohort_id(
        strategy_version="v1",
        ranking_version="ranking-v1",
        ranking_schema_version=1,
        selection_version="selection-v1",
        calibration_context_sha256=ctx,
        source_matrix_raw_sha256="b" * 64,
    )
    assert cohort_a != cohort_b


# --- Observation ID / Pair ID determinism -----------------------------------


def test_observation_id_deterministic_and_sensitive_to_inputs():
    a = compute_observation_id(
        cohort_id="cohort1", target_date="2026-08-10", ranking_raw_sha256="r" * 64
    )
    b = compute_observation_id(
        cohort_id="cohort1", target_date="2026-08-10", ranking_raw_sha256="r" * 64
    )
    assert a == b
    c = compute_observation_id(
        cohort_id="cohort1", target_date="2026-08-11", ranking_raw_sha256="r" * 64
    )
    assert a != c


def test_pair_id_deterministic_and_sensitive_to_ratio():
    a = compute_pair_id(
        cohort_id="cohort1",
        minimum_turnover_yen=1000,
        maximum_relative_tick_numerator=1,
        maximum_relative_tick_denominator=1000,
    )
    b = compute_pair_id(
        cohort_id="cohort1",
        minimum_turnover_yen=1000,
        maximum_relative_tick_numerator=1,
        maximum_relative_tick_denominator=1000,
    )
    assert a == b
    c = compute_pair_id(
        cohort_id="cohort1",
        minimum_turnover_yen=1000,
        maximum_relative_tick_numerator=1,
        maximum_relative_tick_denominator=2000,
    )
    assert a != c


# --- TC-CAND: candidate generation ------------------------------------------


def _obs(target_date, turnover_yen, num, den):
    return {
        "target_date": target_date,
        "ticker": "1234",
        "turnover_yen": str(turnover_yen),
        "relative_tick_size": {"numerator_yen": "1", "denominator_yen": "1000"},
        "canonical_relative_tick_ratio": {"numerator": num, "denominator": den},
    }


def test_turnover_candidates_distinct_ascending():
    obs = [
        _obs("2026-08-10", 5_000_000, 1, 1000),
        _obs("2026-08-11", 3_000_000, 1, 1000),
        _obs("2026-08-12", 5_000_000, 1, 1000),
    ]
    assert generate_turnover_candidates(obs) == [3_000_000, 5_000_000]


def test_relative_tick_candidates_exact_ascending_not_lexicographic():
    # 1/500 (=0.002) must sort AFTER 1/1000 (=0.001) even though the tuple
    # (1, 500) is lexicographically smaller than (1, 1000).
    obs = [
        _obs("2026-08-10", 1_000_000, 1, 500),
        _obs("2026-08-11", 1_000_000, 1, 1000),
    ]
    candidates = generate_relative_tick_candidates(obs)
    assert candidates == [(1, 1000), (1, 500)]


def test_relative_tick_candidates_deduplicated():
    obs = [
        _obs("2026-08-10", 1_000_000, 1, 1000),
        _obs("2026-08-11", 1_000_000, 1, 1000),
    ]
    assert generate_relative_tick_candidates(obs) == [(1, 1000)]


# --- TC-PAIR: Cartesian product + evaluation --------------------------------


def test_generate_threshold_pairs_is_full_cartesian_product_fixed_order():
    turnover = [1_000_000, 2_000_000]
    ratios = [(1, 2000), (1, 1000)]
    pairs = generate_threshold_pairs(turnover, ratios)
    assert pairs == [
        (1_000_000, (1, 2000)),
        (1_000_000, (1, 1000)),
        (2_000_000, (1, 2000)),
        (2_000_000, (1, 1000)),
    ]
    assert len(pairs) == len(turnover) * len(ratios)


def test_evaluate_threshold_pair_boundary_equality_passes():
    obs = [_obs("2026-08-10", 1_000_000, 1, 1000)]
    result = evaluate_threshold_pair(
        cohort_id="cohort1",
        minimum_turnover_yen=1_000_000,
        maximum_relative_tick_ratio=(1, 1000),
        observations=obs,
    )
    assert result["selected_count"] == 1
    assert result["rejected_count"] == 0


def test_evaluate_threshold_pair_invariants_hold():
    obs = [
        _obs("2026-08-10", 1_000_000, 1, 1000),
        _obs("2026-08-11", 500_000, 1, 2000),
        _obs("2026-08-12", 2_000_000, 1, 500),
    ]
    result = evaluate_threshold_pair(
        cohort_id="cohort1",
        minimum_turnover_yen=1_000_000,
        maximum_relative_tick_ratio=(1, 1000),
        observations=obs,
    )
    assert (
        result["selected_count"] + result["rejected_count"] == result["observation_count"]
    )
    assert (
        result["rejected_turnover_only_count"]
        + result["rejected_relative_tick_only_count"]
        + result["rejected_both_count"]
        == result["rejected_count"]
    )


def test_evaluate_all_threshold_pairs_evaluates_full_cartesian_product():
    obs = [
        _obs("2026-08-10", 1_000_000, 1, 1000),
        _obs("2026-08-11", 500_000, 1, 2000),
    ]
    turnover_candidates = generate_turnover_candidates(obs)
    ratio_candidates = generate_relative_tick_candidates(obs)
    evaluated = evaluate_all_threshold_pairs(
        cohort_id="cohort1",
        turnover_candidates=turnover_candidates,
        relative_tick_candidates=ratio_candidates,
        observations=obs,
    )
    assert len(evaluated) == len(turnover_candidates) * len(ratio_candidates)


def test_outcome_signature_deterministic_and_order_independent_of_input_order():
    obs_in_order = [
        _obs("2026-08-10", 1_000_000, 1, 1000),
        _obs("2026-08-11", 500_000, 1, 2000),
    ]
    obs_reversed = list(reversed(obs_in_order))
    result_a = evaluate_threshold_pair(
        cohort_id="cohort1",
        minimum_turnover_yen=1_000_000,
        maximum_relative_tick_ratio=(1, 1000),
        observations=obs_in_order,
    )
    result_b = evaluate_threshold_pair(
        cohort_id="cohort1",
        minimum_turnover_yen=1_000_000,
        maximum_relative_tick_ratio=(1, 1000),
        observations=obs_reversed,
    )
    assert result_a["outcome_signature_sha256"] == result_b["outcome_signature_sha256"]


def test_pair_id_matches_evaluate_threshold_pair_output():
    obs = [_obs("2026-08-10", 1_000_000, 1, 1000)]
    result = evaluate_threshold_pair(
        cohort_id="cohort1",
        minimum_turnover_yen=1_000_000,
        maximum_relative_tick_ratio=(1, 1000),
        observations=obs,
    )
    expected_pair_id = compute_pair_id(
        cohort_id="cohort1",
        minimum_turnover_yen=1_000_000,
        maximum_relative_tick_numerator=1,
        maximum_relative_tick_denominator=1000,
    )
    assert result["pair_id"] == expected_pair_id
