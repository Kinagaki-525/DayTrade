from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "rules" / "strategy.yaml"
SUPPORTED_STRATEGY = "previous_day_high_breakout"


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects accidental duplicate settings."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"Duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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


@dataclass(frozen=True)
class OrderPlan:
    strategy_version: str
    validation_status: str
    entry_trigger: Decimal
    entry_limit: Decimal
    affordable: bool
    estimated_purchase_amount: Decimal
    shares: int
    take_profit_price: Decimal
    stop_loss_price: Decimal

    def as_dict(self) -> dict[str, Decimal | bool | int | str]:
        return asdict(self)


def to_decimal(value: Decimal | int | str | float) -> Decimal:
    """Convert values through str() to avoid binary float artifacts."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"Decimal value must be finite: {value!r}")
    return decimal_value


def load_strategy_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")

    loaded = yaml.load(text, Loader=UniqueKeyLoader)
    config = loaded if loaded is not None else {}
    if not isinstance(config, dict):
        raise ValueError("Strategy configuration must be a mapping")
    validate_strategy_config(config)
    return config


def validate_strategy_config(config: dict[str, Any]) -> None:
    """Validate configuration shape and safety-related consistency."""
    strategy_version = config.get("strategy_version")
    validation_status = config.get("validation_status")
    if not isinstance(strategy_version, str) or not strategy_version.strip():
        raise ValueError("strategy_version must be a non-empty string")
    if validation_status != "unvalidated":
        raise ValueError("validation_status must remain 'unvalidated' until evidence is reviewed")

    _positive_decimal(_required(config, "capital"), "capital")
    _positive_int(_required(config, "position_size"), "position_size")
    if config.get("market") != "japanese_stocks":
        raise ValueError("market must be 'japanese_stocks'")
    if config.get("account_type") != "cash":
        raise ValueError("account_type must be 'cash'")

    entry = _required_mapping(config, "entry")
    if entry.get("strategy") != SUPPORTED_STRATEGY:
        raise ValueError(f"entry.strategy must be {SUPPORTED_STRATEGY!r}")
    _non_negative_int(_required(entry, "trigger_ticks", "entry"), "entry.trigger_ticks")
    _non_negative_int(
        _required(entry, "limit_offset_ticks", "entry"),
        "entry.limit_offset_ticks",
    )

    risk = _required_mapping(config, "risk")
    max_loss = _positive_decimal(
        _required(risk, "max_loss_per_trade_yen", "risk"),
        "risk.max_loss_per_trade_yen",
    )
    _positive_int(
        _required(risk, "max_trades_per_day", "risk"),
        "risk.max_trades_per_day",
    )
    _positive_int(_required(risk, "max_positions", "risk"), "risk.max_positions")
    _require_bool(risk, "averaging_down", "risk")
    _require_bool(risk, "overnight_hold", "risk")

    exit_config = _required_mapping(config, "exit")
    _positive_decimal(
        _required(exit_config, "take_profit_yen", "exit"),
        "exit.take_profit_yen",
    )
    stop_loss = _positive_decimal(
        _required(exit_config, "stop_loss_yen", "exit"),
        "exit.stop_loss_yen",
    )
    _require_bool(exit_config, "close_by_end_of_day", "exit")
    if stop_loss > max_loss:
        raise ValueError(
            "exit.stop_loss_yen must not exceed risk.max_loss_per_trade_yen"
        )

    validation = _required_mapping(config, "validation")
    _require_bool(validation, "require_affordable_position", "validation")


def calculate_entry_prices(
    previous_high: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
    trigger_ticks: int = 1,
    limit_offset_ticks: int = 1,
) -> EntryPrices:
    tick = _positive_decimal(tick_size, "tick_size")
    high = _positive_decimal(previous_high, "previous_high")
    trigger_count = _non_negative_int(trigger_ticks, "trigger_ticks")
    limit_count = _non_negative_int(limit_offset_ticks, "limit_offset_ticks")

    if not _is_tick_aligned(high, tick):
        raise ValueError("previous_high must be aligned to tick_size")

    trigger = high + tick * Decimal(trigger_count)
    limit = trigger + tick * Decimal(limit_count)
    return EntryPrices(entry_trigger=trigger, entry_limit=limit)


def is_affordable(
    price: Decimal | int | str | float,
    shares: int,
    capital: Decimal | int | str | float,
) -> bool:
    share_count = _positive_int(shares, "shares")
    purchase_price = _positive_decimal(price, "price")
    available_capital = _positive_decimal(capital, "capital")
    return purchase_price * Decimal(share_count) <= available_capital


def calculate_exit_prices(
    entry_price: Decimal | int | str | float,
    shares: int,
    take_profit_yen: Decimal | int | str | float,
    stop_loss_yen: Decimal | int | str | float,
    tick_size: Decimal | int | str | float | None = None,
) -> ExitPrices:
    share_count = _positive_int(shares, "shares")
    entry = _positive_decimal(entry_price, "entry_price")
    take_profit_per_share = _positive_decimal(
        take_profit_yen, "take_profit_yen"
    ) / Decimal(share_count)
    stop_loss_per_share = _positive_decimal(
        stop_loss_yen, "stop_loss_yen"
    ) / Decimal(share_count)

    take_profit_price = entry + take_profit_per_share
    stop_loss_price = entry - stop_loss_per_share

    if tick_size is not None:
        tick = _positive_decimal(tick_size, "tick_size")
        if not _is_tick_aligned(entry, tick):
            raise ValueError("entry_price must be aligned to tick_size")
        take_profit_price = round_price_to_tick(take_profit_price, tick, mode="ceiling")
        stop_loss_price = round_price_to_tick(stop_loss_price, tick, mode="ceiling")

    if stop_loss_price <= 0:
        raise ValueError("stop_loss_price must be greater than 0")

    return ExitPrices(
        take_profit_price=take_profit_price,
        stop_loss_price=stop_loss_price,
    )


def round_price_to_tick(
    price: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
    mode: str,
) -> Decimal:
    tick = _positive_decimal(tick_size, "tick_size")
    value = to_decimal(price)

    if mode == "floor":
        rounding = ROUND_FLOOR
    elif mode == "ceiling":
        rounding = ROUND_CEILING
    else:
        raise ValueError("mode must be 'floor' or 'ceiling'")

    ticks = (value / tick).to_integral_value(rounding=rounding)
    return ticks * tick


def build_order_plan(
    previous_high: Decimal | int | str | float,
    tick_size: Decimal | int | str | float,
    config: dict[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> OrderPlan:
    strategy_config = config if config is not None else load_strategy_config(config_path)
    validate_strategy_config(strategy_config)

    shares = _positive_int(strategy_config["position_size"], "position_size")
    capital = to_decimal(strategy_config["capital"])
    entry_config = strategy_config["entry"]
    exit_config = strategy_config["exit"]

    entry_prices = calculate_entry_prices(
        previous_high=previous_high,
        tick_size=tick_size,
        trigger_ticks=entry_config["trigger_ticks"],
        limit_offset_ticks=entry_config["limit_offset_ticks"],
    )
    estimated_purchase_amount = entry_prices.entry_limit * Decimal(shares)
    affordable = estimated_purchase_amount <= capital

    exit_prices = calculate_exit_prices(
        entry_price=entry_prices.entry_limit,
        shares=shares,
        take_profit_yen=exit_config["take_profit_yen"],
        stop_loss_yen=exit_config["stop_loss_yen"],
        tick_size=tick_size,
    )

    return OrderPlan(
        strategy_version=strategy_config["strategy_version"],
        validation_status=strategy_config["validation_status"],
        entry_trigger=entry_prices.entry_trigger,
        entry_limit=entry_prices.entry_limit,
        affordable=affordable,
        estimated_purchase_amount=estimated_purchase_amount,
        shares=shares,
        take_profit_price=exit_prices.take_profit_price,
        stop_loss_price=exit_prices.stop_loss_price,
    )


def _positive_decimal(value: Decimal | int | str | float, name: str) -> Decimal:
    decimal_value = to_decimal(value)
    if decimal_value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return decimal_value


def _positive_int(value: Any, name: str) -> int:
    int_value = _exact_int(value, name)
    if int_value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return int_value


def _non_negative_int(value: Any, name: str) -> int:
    int_value = _exact_int(value, name)
    if int_value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    return int_value


def _exact_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    decimal_value = to_decimal(value)
    if decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"{name} must be an integer")
    return int(decimal_value)


def _required(mapping: dict[str, Any], key: str, path: str = "configuration") -> Any:
    if key not in mapping:
        raise ValueError(f"Missing required setting: {path}.{key}")
    return mapping[key]


def _required_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = _required(config, key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _require_bool(mapping: dict[str, Any], key: str, path: str) -> bool:
    value = _required(mapping, key, path)
    if not isinstance(value, bool):
        raise ValueError(f"{path}.{key} must be a boolean")
    return value


def _is_tick_aligned(price: Decimal, tick_size: Decimal) -> bool:
    return price % tick_size == 0
