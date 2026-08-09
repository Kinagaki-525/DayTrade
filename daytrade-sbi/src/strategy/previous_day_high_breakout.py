from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, validate_strategy_config
from src.strategy.pricing import (
    calculate_entry_prices,
    calculate_exit_prices,
    positive_int,
    to_decimal,
)


@dataclass(frozen=True)
class OrderPlan:
    strategy_version: str
    validation_status: str
    entry_trigger: Decimal
    entry_limit: Decimal
    affordable: bool
    estimated_purchase_amount: Decimal
    expected_loss_yen: Decimal
    shares: int
    take_profit_price: Decimal
    stop_loss_price: Decimal

    def as_dict(self) -> dict[str, Decimal | bool | int | str]:
        return asdict(self)


def build_order_plan(
    previous_high: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
    config: dict[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> OrderPlan:
    strategy_config = config if config is not None else load_strategy_config(config_path)
    validate_strategy_config(strategy_config)
    shares = positive_int(strategy_config["capital"]["position_size"], "position_size")
    capital = to_decimal(strategy_config["capital"]["total_yen"])
    entry_config = strategy_config["previous_day_high_breakout"]
    risk_config = strategy_config["risk"]
    exit_config = strategy_config["exit"]

    entry_prices = calculate_entry_prices(
        previous_high=previous_high,
        tick_size=tick_size,
        trigger_ticks=entry_config["trigger_ticks"],
        limit_offset_ticks=entry_config["entry_limit_offset_ticks"],
    )
    estimated_purchase_amount = entry_prices.entry_limit * Decimal(shares)
    exit_prices = calculate_exit_prices(
        entry_price=entry_prices.entry_limit,
        shares=shares,
        take_profit_yen=exit_config["take_profit_yen"],
        stop_loss_yen=risk_config["max_loss_per_trade_yen"],
        tick_size=tick_size,
    )
    expected_loss = (
        entry_prices.entry_limit - exit_prices.stop_loss_price
    ) * Decimal(shares)

    return OrderPlan(
        strategy_version=strategy_config["strategy_version"],
        validation_status=strategy_config["validation_status"],
        entry_trigger=entry_prices.entry_trigger,
        entry_limit=entry_prices.entry_limit,
        affordable=estimated_purchase_amount <= capital,
        estimated_purchase_amount=estimated_purchase_amount,
        expected_loss_yen=expected_loss,
        shares=shares,
        take_profit_price=exit_prices.take_profit_price,
        stop_loss_price=exit_prices.stop_loss_price,
    )
