from pathlib import Path

import pytest

import copy

from src.config import (
    SCREENING_KEYS,
    load_strategy_config,
    load_yaml,
    normalize_screening_rules,
    validate_strategy_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_current_config_keeps_screening_rules_disabled_without_thresholds():
    config = load_strategy_config()

    assert config["config_schema_version"] == 6
    assert config["strategy_version"] == "v1"
    rules = normalize_screening_rules(config["screening"])
    assert set(rules) == set(SCREENING_KEYS)
    assert all(rule["enabled"] is False for rule in rules.values())
    assert all(rule["threshold"] is None for rule in rules.values())
    assert rules["maximum_gap_percent"]["phase"] == "entry_gate"
    assert rules["maximum_spread"]["phase"] == "execution_gate"
    assert rules["exclude_earnings"]["phase"] == "event_gate"


def test_production_config_guard_selection_disabled_and_thresholds_null():
    """Non-negotiable: production selection stays disabled with undecided thresholds.

    This test intentionally hard-codes the expectation, not a value read back
    from the config file, so a well-intentioned edit to config/strategy.yaml
    cannot silently flip production selection on or invent a threshold.
    """
    config = load_strategy_config()
    selection = config["selection"]
    assert selection["enabled"] is False
    assert (
        selection["rules"]["minimum_turnover_yen"]["threshold_yen"] is None
    )
    assert (
        selection["rules"]["maximum_relative_tick_size"]["threshold_ratio"] is None
    )


def test_v3_config_preserves_v1_fixed_rule_values():
    current = load_strategy_config()
    archived = load_yaml(PROJECT_ROOT / "rules" / "versions" / "v1.yaml")

    assert archived["capital"] == 50000
    assert current["capital"]["total_yen"] == 100000
    assert current["capital"]["position_size"] == archived["position_size"]
    assert current["risk"]["max_loss_per_trade_yen"] == archived["risk"]["max_loss_per_trade_yen"]
    assert current["previous_day_high_breakout"]["trigger_ticks"] == archived["entry"]["trigger_ticks"]
    assert current["exit"]["take_profit_yen"] == archived["exit"]["take_profit_yen"]


def test_config_rejects_relaxed_position_limit():
    config = load_strategy_config()
    config["risk"]["max_positions"] = 2

    with pytest.raises(ValueError, match="must remain 1"):
        validate_strategy_config(config)


def test_strategy_loader_rejects_duplicate_yaml_keys():
    with pytest.raises(ValueError, match="Duplicate YAML key: capital"):
        load_strategy_config(PROJECT_ROOT / "tests" / "fixtures" / "strategy_duplicate_key.yaml")


def test_enabled_screening_rule_requires_threshold():
    config = load_strategy_config()
    config["screening"]["minimum_volume"]["enabled"] = True

    with pytest.raises(ValueError, match="threshold is required"):
        validate_strategy_config(config)


def test_v2_config_is_still_loadable_and_v4_event_gate_requires_fixed_values():
    v2_config = load_yaml(PROJECT_ROOT / "tests" / "fixtures" / "strategy_v2.yaml")
    loaded = load_strategy_config(PROJECT_ROOT / "tests" / "fixtures" / "strategy_v2.yaml")
    assert loaded["config_schema_version"] == 2
    assert v2_config["config_schema_version"] == 2

    v4_config = load_strategy_config()
    v4_config["event_gate"]["news"]["required_source_ids"][0] = "OTHER"
    with pytest.raises(ValueError, match="event_gate"):
        validate_strategy_config(v4_config)


def test_v3_config_is_still_loadable_with_legacy_event_gate_shape():
    v3_config = load_strategy_config(PROJECT_ROOT / "tests" / "fixtures" / "strategy_v3.yaml")
    assert v3_config["config_schema_version"] == 3
    assert v3_config["event_gate"]["earnings"]["primary_source_id"] == "JPX_EARNINGS_SCHEDULE"


def test_v4_config_fixture_loads_without_ranking_block():
    """Genuine legacy-shaped (pre-ranking) schema_version=4 strategy.yaml:
    this is what strategy.yaml looked like before the `ranking:` block was
    added. It must still load successfully and must NOT contain a ranking
    section -- proving true backward compatibility rather than re-testing
    the current v5 config."""
    v4_config = load_strategy_config(PROJECT_ROOT / "tests" / "fixtures" / "strategy_v4.yaml")
    assert v4_config["config_schema_version"] == 4
    assert "ranking" not in v4_config
    assert v4_config["event_gate"]["earnings"]["target_date_source_ids"] == [
        "JPX_EARNINGS_SCHEDULE",
        "COMPANY_IR",
    ]


def test_v4_config_requires_new_earnings_source_id_lists():
    config = load_strategy_config()
    config["event_gate"]["earnings"]["target_date_source_ids"] = ["OTHER"]
    with pytest.raises(ValueError, match="target_date_source_ids"):
        validate_strategy_config(config)


def test_legacy_event_screening_rules_are_rejected_in_v4():
    config = load_strategy_config()
    config["screening"]["exclude_earnings"]["enabled"] = True
    with pytest.raises(ValueError, match="exclude_earnings"):
        validate_strategy_config(config)


def test_legacy_null_screening_values_remain_read_only_compatible():
    config = load_strategy_config()
    config["screening"]["minimum_volume"] = None

    rules = normalize_screening_rules(config["screening"])

    assert rules["minimum_volume"]["enabled"] is False
    assert rules["minimum_volume"]["threshold"] is None


# --- v5 ranking block validation (anti scope-creep enforcement) --------------


def test_v5_config_ships_the_expected_fixed_ranking_block():
    config = load_strategy_config()

    ranking = config["ranking"]
    assert ranking["enabled"] is True
    assert ranking["version"] == "ranking-v1"
    assert ranking["method"] == "ordinal_rank_sum"
    assert ranking["feature_rank_method"] == "competition"
    assert ranking["aggregation"] == {"method": "rank_sum"}
    assert ranking["missing_data_policy"] == "fail_closed"
    assert ranking["tie_break"] == [
        "turnover_desc",
        "relative_tick_size_asc",
        "ticker_asc",
    ]
    assert set(ranking["features"]) == {"turnover", "relative_tick_size"}
    assert ranking["features"]["turnover"] == {
        "direction": "desc",
        "source_policy": "actual_only",
        "estimated_allowed": False,
    }
    assert ranking["features"]["relative_tick_size"] == {"direction": "asc"}


def test_v5_config_requires_a_ranking_block():
    config = load_strategy_config()
    del config["ranking"]
    with pytest.raises(ValueError, match="ranking"):
        validate_strategy_config(config)


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "score",
        "weight",
        "weights",
        "confidence",
        "expected_return",
        "expected_profit",
        "probability",
        "normalization",
        "threshold",
        "thresholds",
        "minimum_turnover",
        "maximum_relative_tick_size",
    ],
)
def test_ranking_block_rejects_permanently_out_of_scope_fields(forbidden_field):
    """Scope guard: minimum-turnover / max-relative-tick thresholds, scores,
    weights, confidences and normalization are permanently out of scope for
    ranking-v1 and must not be configurable."""
    config = load_strategy_config()
    config["ranking"][forbidden_field] = 1
    with pytest.raises(ValueError, match="ranking must not define"):
        validate_strategy_config(config)


@pytest.mark.parametrize(
    ("block_path", "field_name"),
    [
        (("ranking",), "momentum"),
        (("ranking", "features"), "topix500_membership"),
        (("ranking", "features"), "previous_day_change_pct"),
        (("ranking", "features"), "daily_range_pct"),
        (("ranking", "features", "turnover"), "weight"),
        (("ranking", "features", "relative_tick_size"), "score"),
        (("ranking", "aggregation"), "weights"),
    ],
)
def test_ranking_block_rejects_unknown_fields(block_path, field_name):
    config = load_strategy_config()
    block = config
    for key in block_path:
        block = block[key]
    block[field_name] = 1
    with pytest.raises(ValueError, match="ranking"):
        validate_strategy_config(config)


@pytest.mark.parametrize(
    ("block_path", "field_name", "value"),
    [
        (("ranking",), "enabled", False),
        (("ranking",), "version", "ranking-v2"),
        (("ranking",), "method", "weighted_score"),
        (("ranking",), "feature_rank_method", "dense"),
        (("ranking",), "missing_data_policy", "best_effort"),
        (("ranking",), "tie_break", ["ticker_asc"]),
        (("ranking", "features", "turnover"), "direction", "asc"),
        (("ranking", "features", "turnover"), "source_policy", "estimated_allowed"),
        (("ranking", "features", "turnover"), "estimated_allowed", True),
        (("ranking", "features", "relative_tick_size"), "direction", "desc"),
        (("ranking", "aggregation"), "method", "weighted_sum"),
    ],
)
def test_ranking_block_rejects_changed_fixed_values(block_path, field_name, value):
    """Mutation check: every fixed ranking-v1 setting must be pinned, so a
    silent change of ranking method, direction, tie-break, competition
    ranking, fail-closed policy or the actual-turnover-only source policy
    cannot slip through configuration."""
    config = load_strategy_config()
    block = config
    for key in block_path:
        block = block[key]
    assert block[field_name] != value
    block[field_name] = value
    with pytest.raises(ValueError, match="ranking"):
        validate_strategy_config(config)


@pytest.mark.parametrize("missing_block", ["features", "aggregation"])
def test_ranking_block_requires_its_nested_blocks(missing_block):
    config = load_strategy_config()
    del config["ranking"][missing_block]
    with pytest.raises(ValueError, match="ranking"):
        validate_strategy_config(config)


@pytest.mark.parametrize("missing_feature", ["turnover", "relative_tick_size"])
def test_ranking_features_require_both_features(missing_feature):
    config = load_strategy_config()
    del config["ranking"]["features"][missing_feature]
    with pytest.raises(ValueError, match="ranking|turnover|relative_tick_size"):
        validate_strategy_config(config)


# --- genuine v5 config compatibility (real historical snapshot, not v6) -----


def test_genuine_v5_snapshot_loads_without_a_selection_block():
    """tests/fixtures/strategy_v5.yaml is a real historical
    config_schema_version=5 strategy_snapshot.yaml (copied from a genuine
    Ranking regression run, predating the Selection v6 work), not the
    current v6 config with a version number swapped in. load_strategy_config
    validates via the config's own real v5 code path (_validate_ranking_config,
    no _validate_selection_config), so a passing load proves genuine v5/Ranking
    backward compatibility rather than merely re-testing v6."""
    v5_config = load_strategy_config(
        PROJECT_ROOT / "tests" / "fixtures" / "strategy_v5.yaml"
    )
    assert v5_config["config_schema_version"] == 5
    assert "selection" not in v5_config
    assert v5_config["ranking"]["enabled"] is True


# --- Config v6 Selection validation matrix (bug fixes + full coverage) -----


def _v6_selection_config(**overrides):
    config = copy.deepcopy(load_strategy_config())
    assert config["config_schema_version"] == 6
    selection = copy.deepcopy(config["selection"])
    selection.update(overrides)
    config["selection"] = selection
    return config


def test_v6_production_default_selection_disabled_both_null_passes():
    config = load_strategy_config()
    validate_strategy_config(config)  # must not raise


def test_v6_selection_enabled_both_valid_passes():
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1000},
            },
        },
    )
    validate_strategy_config(config)  # must not raise


def test_v6_selection_disabled_both_valid_non_null_passes():
    """Emergency Disable: previously-calibrated values kept in config while
    selection.enabled is temporarily false."""
    config = _v6_selection_config(
        enabled=False,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1000},
            },
        },
    )
    validate_strategy_config(config)  # must not raise


@pytest.mark.parametrize(
    "rules",
    [
        {
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {"operator": "<=", "threshold_ratio": None},
        },
        {
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": None},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1000},
            },
        },
    ],
)
def test_v6_selection_disabled_mixed_null_fails(rules):
    config = _v6_selection_config(enabled=False, rules=rules)
    with pytest.raises(ValueError, match="must either both be null or both be set"):
        validate_strategy_config(config)


def test_v6_selection_enabled_missing_turnover_fails():
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": None},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1000},
            },
        },
    )
    with pytest.raises(ValueError, match="minimum_turnover_yen"):
        validate_strategy_config(config)


def test_v6_selection_enabled_missing_ratio_fails():
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {"operator": "<=", "threshold_ratio": None},
        },
    )
    with pytest.raises(ValueError, match="threshold_ratio"):
        validate_strategy_config(config)


def test_v6_selection_ratio_numerator_zero_fails():
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 0, "denominator": 1000},
            },
        },
    )
    with pytest.raises(ValueError, match="numerator"):
        validate_strategy_config(config)


def test_v6_selection_ratio_denominator_zero_fails():
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 0},
            },
        },
    )
    with pytest.raises(ValueError, match="denominator"):
        validate_strategy_config(config)


def test_v6_selection_ratio_not_reduced_fails():
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 2, "denominator": 2000},
            },
        },
    )
    with pytest.raises(ValueError, match="lowest terms"):
        validate_strategy_config(config)


def test_v6_selection_unknown_rule_name_fails():
    config = _v6_selection_config(enabled=False)
    config["selection"]["rules"]["unknown_rule"] = {"operator": ">=", "threshold": 1}
    with pytest.raises(ValueError, match="unknown field"):
        validate_strategy_config(config)


def test_v6_selection_fallback_policy_not_none_fails():
    config = _v6_selection_config(fallback_policy="rank2")
    with pytest.raises(ValueError, match="fallback_policy"):
        validate_strategy_config(config)


def test_v6_selection_candidate_policy_not_rank1_only_fails():
    config = _v6_selection_config(candidate_policy="rank1_and_rank2")
    with pytest.raises(ValueError, match="candidate_policy"):
        validate_strategy_config(config)


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "score",
        "scores",
        "weight",
        "weights",
        "confidence",
        "probability",
        "expected_return",
        "expected_profit",
        "alpha",
        "ranking_score",
        "normalization",
        "recommended_threshold",
        "optimal_threshold",
        "best_threshold",
        "suggested_threshold",
        "rank2",
        "rank2_fallback",
        "fallback_rank",
        "fallback_candidates",
        "ai_score",
        "llm_score",
    ],
)
def test_v6_selection_forbidden_fields_fail(forbidden_field):
    config = _v6_selection_config(enabled=False)
    config["selection"][forbidden_field] = 1
    with pytest.raises(ValueError, match="must not define"):
        validate_strategy_config(config)


def test_v6_hard_screening_minimum_turnover_and_selection_both_enabled_fails():
    """Bug 2 regression: the mutual-exclusion check must use the
    NORMALIZED screening dict, not the raw/legacy-shorthand one, so a
    Hard Screening minimum_turnover expressed in legacy scalar shorthand
    (a bare threshold value instead of {"enabled": ..., ...}) is still
    correctly detected."""
    config = _v6_selection_config(
        enabled=True,
        rules={
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1000},
            },
        },
    )
    # Legacy shorthand: a bare positive threshold value means "enabled with
    # this threshold" per _normalize_screening_rule.
    config["screening"]["minimum_turnover"] = 500
    with pytest.raises(ValueError, match="minimum_turnover.*selection"):
        validate_strategy_config(config)
