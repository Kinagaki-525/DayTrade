from decimal import Decimal
from pathlib import Path

import pytest

from src.strategy import (
    build_order_plan,
    calculate_entry_prices,
    calculate_exit_prices,
    is_affordable,
    load_strategy_config,
    validate_strategy_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def strategy_config():
    return {
        "strategy_version": "v1",
        "validation_status": "unvalidated",
        "capital": 50000,
        "position_size": 100,
        "market": "japanese_stocks",
        "account_type": "cash",
        "entry": {
            "strategy": "previous_day_high_breakout",
            "trigger_ticks": 1,
            "limit_offset_ticks": 1,
        },
        "risk": {
            "max_loss_per_trade_yen": 500,
            "max_trades_per_day": 1,
            "max_positions": 1,
            "averaging_down": False,
            "overnight_hold": False,
        },
        "exit": {
            "take_profit_yen": 800,
            "stop_loss_yen": 500,
            "close_by_end_of_day": True,
        },
        "validation": {"require_affordable_position": True},
    }


def test_entry_prices_with_one_yen_tick():
    prices = calculate_entry_prices(previous_high="400", tick_size="1")

    assert prices.entry_trigger == Decimal("401")
    assert prices.entry_limit == Decimal("402")


def test_entry_prices_with_half_yen_tick():
    prices = calculate_entry_prices(previous_high="3022", tick_size="0.5")

    assert prices.entry_trigger == Decimal("3022.5")
    assert prices.entry_limit == Decimal("3023.0")


def test_affordable_when_total_purchase_is_equal_to_capital():
    assert is_affordable(price="500", shares=100, capital="50000") is True


def test_not_affordable_when_total_purchase_exceeds_capital():
    assert is_affordable(price="500.5", shares=100, capital="50000") is False


def test_exit_prices_are_calculated_from_trade_level_yen_amounts():
    prices = calculate_exit_prices(
        entry_price="402",
        shares=100,
        take_profit_yen="800",
        stop_loss_yen="500",
        tick_size="1",
    )

    assert prices.take_profit_price == Decimal("410")
    assert prices.stop_loss_price == Decimal("397")


def test_exit_prices_round_to_ticks_without_weakening_amounts():
    prices = calculate_exit_prices(
        entry_price="402",
        shares=100,
        take_profit_yen="825",
        stop_loss_yen="475",
        tick_size="0.5",
    )

    assert prices.take_profit_price == Decimal("410.5")
    assert prices.stop_loss_price == Decimal("397.5")


def test_build_order_plan_uses_strategy_config_values():
    config = strategy_config()

    plan = build_order_plan(previous_high="400", tick_size="1", config=config)

    assert plan.strategy_version == "v1"
    assert plan.validation_status == "unvalidated"
    assert plan.entry_trigger == Decimal("401")
    assert plan.entry_limit == Decimal("402")
    assert plan.estimated_purchase_amount == Decimal("40200")
    assert plan.affordable is True
    assert plan.take_profit_price == Decimal("410")
    assert plan.stop_loss_price == Decimal("397")


def test_entry_price_rejects_previous_high_not_aligned_to_tick():
    with pytest.raises(ValueError, match="aligned"):
        calculate_entry_prices(previous_high="3022.2", tick_size="0.5")


def test_config_rejects_stop_loss_above_risk_limit():
    config = strategy_config()
    config["exit"]["stop_loss_yen"] = 501

    with pytest.raises(ValueError, match="must not exceed"):
        validate_strategy_config(config)


def test_config_rejects_fractional_position_size():
    config = strategy_config()
    config["position_size"] = "100.5"

    with pytest.raises(ValueError, match="must be an integer"):
        validate_strategy_config(config)


def test_current_strategy_matches_its_versioned_snapshot():
    current = load_strategy_config(PROJECT_ROOT / "rules" / "strategy.yaml")
    snapshot = load_strategy_config(
        PROJECT_ROOT / "rules" / "versions" / f"{current['strategy_version']}.yaml"
    )

    assert current == snapshot


def test_strategy_loader_rejects_duplicate_yaml_keys():
    with pytest.raises(ValueError, match="Duplicate YAML key: capital"):
        load_strategy_config(PROJECT_ROOT / "tests" / "fixtures" / "strategy_duplicate_key.yaml")
