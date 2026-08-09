from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from src.file_io import atomic_write_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "strategy.yaml"
SUPPORTED_STRATEGY = "previous_day_high_breakout"
SCREENING_KEYS = (
    "minimum_volume",
    "minimum_turnover",
    "minimum_price",
    "maximum_price",
    "maximum_spread",
    "minimum_daily_range",
    "maximum_gap_percent",
    "exclude_earnings",
    "exclude_special_disclosures",
)


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate settings."""


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


def load_yaml(path: str | Path) -> dict[str, Any]:
    loaded = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    if not isinstance(loaded, dict):
        raise ValueError("YAML document must be a mapping")
    return loaded


def load_strategy_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config = load_yaml(path)
    validate_strategy_config(config)
    return config


def strategy_config_sha256(config: dict[str, Any]) -> str:
    validate_strategy_config(config)
    canonical = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def snapshot_strategy_config(
    output_path: str | Path,
    source_path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    source = Path(source_path)
    load_strategy_config(source)
    atomic_write_bytes(output_path, source.read_bytes())


def validate_strategy_config(config: dict[str, Any]) -> None:
    """Validate the v2 configuration and its non-negotiable safety rules."""
    if config.get("config_schema_version") != 2:
        raise ValueError("config_schema_version must be 2")
    _non_empty_string(config.get("strategy_version"), "strategy_version")
    if config.get("validation_status") != "unvalidated":
        raise ValueError("validation_status must be 'unvalidated'")

    account = _required_mapping(config, "account")
    if account.get("broker") != "sbi":
        raise ValueError("account.broker must be 'sbi'")
    if account.get("market") != "japanese_stocks":
        raise ValueError("account.market must be 'japanese_stocks'")
    if account.get("account_type") != "cash":
        raise ValueError("account.account_type must be 'cash'")

    capital = _required_mapping(config, "capital")
    _positive_decimal(_required(capital, "total_yen", "capital"), "capital.total_yen")
    position_size = _positive_int(
        _required(capital, "position_size", "capital"),
        "capital.position_size",
    )
    if position_size != 100:
        raise ValueError("capital.position_size must remain 100")

    risk = _required_mapping(config, "risk")
    _positive_decimal(
        _required(risk, "max_loss_per_trade_yen", "risk"),
        "risk.max_loss_per_trade_yen",
    )
    if _positive_int(_required(risk, "max_positions", "risk"), "risk.max_positions") != 1:
        raise ValueError("risk.max_positions must remain 1")
    if _positive_int(
        _required(risk, "max_trades_per_day", "risk"),
        "risk.max_trades_per_day",
    ) != 1:
        raise ValueError("risk.max_trades_per_day must remain 1")
    for key in ("averaging_down", "overnight_hold", "short_selling", "margin_trading"):
        if _required_bool(risk, key, "risk"):
            raise ValueError(f"risk.{key} must remain false")

    strategy = _required_mapping(config, "strategy")
    allowed = strategy.get("allowed")
    if allowed != [SUPPORTED_STRATEGY]:
        raise ValueError(
            f"strategy.allowed must contain only {SUPPORTED_STRATEGY!r}"
        )

    breakout = _required_mapping(config, SUPPORTED_STRATEGY)
    _non_negative_int(
        _required(breakout, "trigger_ticks", SUPPORTED_STRATEGY),
        f"{SUPPORTED_STRATEGY}.trigger_ticks",
    )
    _non_negative_int(
        _required(breakout, "entry_limit_offset_ticks", SUPPORTED_STRATEGY),
        f"{SUPPORTED_STRATEGY}.entry_limit_offset_ticks",
    )

    exit_config = _required_mapping(config, "exit")
    _positive_decimal(
        _required(exit_config, "take_profit_yen", "exit"),
        "exit.take_profit_yen",
    )

    screening = _required_mapping(config, "screening")
    for key in SCREENING_KEYS:
        value = _required(screening, key, "screening")
        if value is None:
            continue
        if key.startswith("exclude_"):
            if not isinstance(value, bool):
                raise ValueError(f"screening.{key} must be boolean or null")
        else:
            _positive_decimal(value, f"screening.{key}")


def _to_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{name} must be finite")
    return decimal_value


def _positive_decimal(value: Any, name: str) -> Decimal:
    decimal_value = _to_decimal(value, name)
    if decimal_value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return decimal_value


def _exact_int(value: Any, name: str) -> int:
    decimal_value = _to_decimal(value, name)
    if decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"{name} must be an integer")
    return int(decimal_value)


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


def _required(mapping: dict[str, Any], key: str, path: str = "configuration") -> Any:
    if key not in mapping:
        raise ValueError(f"Missing required setting: {path}.{key}")
    return mapping[key]


def _required_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = _required(config, key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _required_bool(mapping: dict[str, Any], key: str, path: str) -> bool:
    value = _required(mapping, key, path)
    if not isinstance(value, bool):
        raise ValueError(f"{path}.{key} must be boolean")
    return value


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value
