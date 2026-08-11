from decimal import Decimal

import pytest

from src.strategy import (
    build_order_plan,
    calculate_entry_prices,
    calculate_exit_prices,
    is_affordable,
)


def test_entry_prices_with_one_yen_tick():
    prices = calculate_entry_prices(previous_high="400", tick_size="1")

    assert prices.entry_trigger == Decimal("401")
    assert prices.entry_limit == Decimal("402")


def test_entry_prices_with_half_yen_tick():
    prices = calculate_entry_prices(previous_high="3022", tick_size="0.5")

    assert prices.entry_trigger == Decimal("3022.5")
    assert prices.entry_limit == Decimal("3023.0")


def test_affordability_boundary():
    assert is_affordable("999", 100, "100000") is True
    assert is_affordable("1000", 100, "100000") is True
    assert is_affordable("1000.01", 100, "100000") is False


def test_exit_prices_are_rounded_without_weakening_amounts():
    prices = calculate_exit_prices("402", 100, "825", "475", "0.5")

    assert prices.take_profit_price == Decimal("410.5")
    assert prices.stop_loss_price == Decimal("397.5")


def test_default_order_plan_uses_v2_config():
    plan = build_order_plan("400", "1")

    assert plan.entry_trigger == Decimal("401")
    assert plan.entry_limit == Decimal("402")
    assert plan.estimated_purchase_amount == Decimal("40200")
    assert plan.expected_loss_yen == Decimal("500")
    assert plan.affordable is True


def test_previous_high_must_align_to_tick():
    with pytest.raises(ValueError, match="aligned"):
        calculate_entry_prices("3022.2", "0.5")
