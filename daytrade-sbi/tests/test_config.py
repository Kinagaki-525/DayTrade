from pathlib import Path

import pytest

from src.config import (
    SCREENING_KEYS,
    load_strategy_config,
    load_yaml,
    normalize_screening_rules,
    validate_strategy_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_v4_config_keeps_screening_rules_disabled_without_thresholds():
    config = load_strategy_config()

    assert config["config_schema_version"] == 5
    assert config["strategy_version"] == "v1"
    rules = normalize_screening_rules(config["screening"])
    assert set(rules) == set(SCREENING_KEYS)
    assert all(rule["enabled"] is False for rule in rules.values())
    assert all(rule["threshold"] is None for rule in rules.values())
    assert rules["maximum_gap_percent"]["phase"] == "entry_gate"
    assert rules["maximum_spread"]["phase"] == "execution_gate"
    assert rules["exclude_earnings"]["phase"] == "event_gate"


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
