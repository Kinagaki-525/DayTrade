from pathlib import Path

import pytest

from src.config import load_strategy_config, load_yaml, validate_strategy_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_v2_config_keeps_unresolved_screening_values_null():
    config = load_strategy_config()

    assert config["strategy_version"] == "v1"
    assert all(value is None for value in config["screening"].values())


def test_v2_config_preserves_v1_fixed_rule_values():
    current = load_strategy_config()
    archived = load_yaml(PROJECT_ROOT / "rules" / "versions" / "v1.yaml")

    assert current["capital"]["total_yen"] == archived["capital"]
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
