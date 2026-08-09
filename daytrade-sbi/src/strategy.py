from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, validate_strategy_config


@dataclass(frozen=True)
class EntryPrices:
    entry_trigger: Decimal
    entry_limit: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return asdict(self)


@dataclass(frozen=True)
class ExitPrices:
    take_profit_price: Decimal
    stop_loss_price: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return asdict(self)


def to_decimal(value: Decimal | int | str | float) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"Decimal value must be finite: {value!r}")
    return decimal_value


def calculate_entry_prices(
    previous_high: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
    trigger_ticks: int = 1,
    limit_offset_ticks: int = 1,
) -> EntryPrices:
    tick = positive_decimal(tick_size, "tick_size")
    high = positive_decimal(previous_high, "previous_high")
    trigger_count = non_negative_int(trigger_ticks, "trigger_ticks")
    limit_count = non_negative_int(limit_offset_ticks, "limit_offset_ticks")
    if not is_tick_aligned(high, tick):
        raise ValueError("previous_high must be aligned to tick_size")
    trigger = high + tick * Decimal(trigger_count)
    limit = trigger + tick * Decimal(limit_count)
    return EntryPrices(entry_trigger=trigger, entry_limit=limit)


def is_affordable(
    price: Decimal | int | str | float,
    shares: int,
    capital: Decimal | int | str | float,
) -> bool:
    share_count = positive_int(shares, "shares")
    purchase_price = positive_decimal(price, "price")
    available_capital = positive_decimal(capital, "capital")
    return purchase_price * Decimal(share_count) <= available_capital


def calculate_exit_prices(
    entry_price: Decimal | int | str | float,
    shares: int,
    take_profit_yen: Decimal | int | str | float,
    stop_loss_yen: Decimal | int | str | float,
    tick_size: Decimal | int | str | float | None = None,
) -> ExitPrices:
    share_count = positive_int(shares, "shares")
    entry = positive_decimal(entry_price, "entry_price")
    take_profit_per_share = positive_decimal(
        take_profit_yen, "take_profit_yen"
    ) / Decimal(share_count)
    stop_loss_per_share = positive_decimal(
        stop_loss_yen, "stop_loss_yen"
    ) / Decimal(share_count)
    take_profit_price = entry + take_profit_per_share
    stop_loss_price = entry - stop_loss_per_share

    if tick_size is not None:
        tick = positive_decimal(tick_size, "tick_size")
        if not is_tick_aligned(entry, tick):
            raise ValueError("entry_price must be aligned to tick_size")
        take_profit_price = round_price_to_tick(take_profit_price, tick, "ceiling")
        stop_loss_price = round_price_to_tick(stop_loss_price, tick, "ceiling")
    if stop_loss_price <= 0:
        raise ValueError("stop_loss_price must be greater than 0")
    return ExitPrices(take_profit_price, stop_loss_price)


def round_price_to_tick(
    price: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
    mode: str,
) -> Decimal:
    tick = positive_decimal(tick_size, "tick_size")
    value = to_decimal(price)
    if mode == "floor":
        rounding = ROUND_FLOOR
    elif mode == "ceiling":
        rounding = ROUND_CEILING
    else:
        raise ValueError("mode must be 'floor' or 'ceiling'")
    ticks = (value / tick).to_integral_value(rounding=rounding)
    return ticks * tick


def is_tick_aligned(
    price: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
) -> bool:
    tick = positive_decimal(tick_size, "tick_size")
    return to_decimal(price) % tick == 0


def positive_decimal(value: Any, name: str) -> Decimal:
    decimal_value = to_decimal(value)
    if decimal_value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return decimal_value


def positive_int(value: Any, name: str) -> int:
    int_value = exact_int(value, name)
    if int_value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return int_value


def non_negative_int(value: Any, name: str) -> int:
    int_value = exact_int(value, name)
    if int_value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    return int_value


def exact_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    decimal_value = to_decimal(value)
    if decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"{name} must be an integer")
    return int(decimal_value)


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
