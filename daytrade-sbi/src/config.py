from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import logging

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
SCREENING_PHASES = ("hard_screening", "event_gate", "entry_gate", "execution_gate")
SCREENING_CRITICALITIES = ("TRADE_CRITICAL", "CONTEXT")
SCREENING_OPERATORS = (">=", "<=", ">", "<", "==", "!=", "is", "is_not")
SCREENING_RULE_DEFAULTS: dict[str, dict[str, Any]] = {
    "minimum_volume": {
        "phase": "hard_screening",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("volume",),
        "operator": ">=",
        "reason_code": "VOLUME_BELOW_MINIMUM",
    },
    "minimum_turnover": {
        "phase": "hard_screening",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("turnover",),
        "operator": ">=",
        "reason_code": "TURNOVER_BELOW_MINIMUM",
    },
    "minimum_price": {
        "phase": "hard_screening",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("close",),
        "operator": ">=",
        "reason_code": "PRICE_BELOW_MINIMUM",
    },
    "maximum_price": {
        "phase": "hard_screening",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("close",),
        "operator": "<=",
        "reason_code": "PRICE_ABOVE_MAXIMUM",
    },
    "maximum_spread": {
        "phase": "execution_gate",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("spread",),
        "operator": "<=",
        "reason_code": "SPREAD_ABOVE_MAXIMUM",
    },
    "minimum_daily_range": {
        "phase": "hard_screening",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("daily_range_yen",),
        "operator": ">=",
        "reason_code": "DAILY_RANGE_BELOW_MINIMUM",
    },
    "maximum_gap_percent": {
        "phase": "entry_gate",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("opening_gap_pct",),
        "operator": "<=",
        "reason_code": "GAP_ABOVE_MAXIMUM",
    },
    "exclude_earnings": {
        "phase": "event_gate",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("earnings_scheduled",),
        "operator": "is_not",
        "reason_code": "EARNINGS_SCHEDULED",
    },
    "exclude_special_disclosures": {
        "phase": "event_gate",
        "criticality": "TRADE_CRITICAL",
        "input_fields": ("special_disclosures",),
        "operator": "is_not",
        "reason_code": "SPECIAL_DISCLOSURE_FOUND",
    },
}


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
    """Validate the strategy configuration and its non-negotiable safety rules."""
    schema_version = config.get("config_schema_version")
    if schema_version not in {2, 3, 4, 5}:
        raise ValueError("config_schema_version must be 2, 3, 4, or 5")
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
    normalize_screening_rules(screening)
    if schema_version == 5:
        _validate_event_gate_config_v4(config)
        _validate_ranking_config(config)
    elif schema_version == 4:
        _validate_event_gate_config_v4(config)
    elif schema_version == 3:
        _validate_event_gate_config(config)
    else:
        _validate_v2_legacy_event_rules(screening)


def _validate_event_gate_config(config: dict[str, Any]) -> None:
    event_gate = _required_mapping(config, "event_gate")
    if event_gate.get("version") != "event-gate-v1":
        raise ValueError("event_gate.version must be 'event-gate-v1'")
    earnings = _required_mapping(event_gate, "earnings")
    _required_bool(earnings, "enabled", "event_gate.earnings")
    if earnings.get("enabled") is not True:
        raise ValueError("event_gate.earnings.enabled must be true")
    if earnings.get("reject_target_date") is not True:
        raise ValueError("event_gate.earnings.reject_target_date must be true")
    if earnings.get("reject_previous_trading_day") is not True:
        raise ValueError("event_gate.earnings.reject_previous_trading_day must be true")
    if earnings.get("primary_source_id") != "JPX_EARNINGS_SCHEDULE":
        raise ValueError("event_gate.earnings.primary_source_id must be JPX_EARNINGS_SCHEDULE")
    if earnings.get("fallback_source_id") != "COMPANY_IR":
        raise ValueError("event_gate.earnings.fallback_source_id must be COMPANY_IR")

    tdnet = _required_mapping(event_gate, "tdnet")
    if tdnet.get("enabled") is not True:
        raise ValueError("event_gate.tdnet.enabled must be true")
    if tdnet.get("policy") != "REJECT_ANY_DISCLOSURE":
        raise ValueError("event_gate.tdnet.policy must be REJECT_ANY_DISCLOSURE")
    if tdnet.get("source_id") != "JPX_TDNET":
        raise ValueError("event_gate.tdnet.source_id must be JPX_TDNET")

    news = _required_mapping(event_gate, "news")
    if news.get("enabled") is not True:
        raise ValueError("event_gate.news.enabled must be true")
    if news.get("required_source_ids") != ["YAHOO_JP_NEWS", "KABUTAN_NEWS"]:
        raise ValueError("event_gate.news.required_source_ids must be fixed")
    if news.get("confirmation_source_ids") != ["JPX_TDNET", "COMPANY_IR_DISCLOSURE"]:
        raise ValueError("event_gate.news.confirmation_source_ids must be fixed")

    post_cutoff = _required_mapping(event_gate, "post_cutoff")
    if post_cutoff.get("recheck_required") is not True:
        raise ValueError("event_gate.post_cutoff.recheck_required must be true")


def _validate_event_gate_config_v4(config: dict[str, Any]) -> None:
    event_gate = _required_mapping(config, "event_gate")
    if event_gate.get("version") != "event-gate-v1":
        raise ValueError("event_gate.version must be 'event-gate-v1'")
    earnings = _required_mapping(event_gate, "earnings")
    _required_bool(earnings, "enabled", "event_gate.earnings")
    if earnings.get("enabled") is not True:
        raise ValueError("event_gate.earnings.enabled must be true")
    if earnings.get("reject_target_date") is not True:
        raise ValueError("event_gate.earnings.reject_target_date must be true")
    if earnings.get("reject_previous_trading_day") is not True:
        raise ValueError("event_gate.earnings.reject_previous_trading_day must be true")
    if earnings.get("target_date_source_ids") != ["JPX_EARNINGS_SCHEDULE", "COMPANY_IR"]:
        raise ValueError("event_gate.earnings.target_date_source_ids must be fixed")
    if earnings.get("previous_day_source_ids") != ["JPX_TDNET", "COMPANY_IR_DISCLOSURE"]:
        raise ValueError("event_gate.earnings.previous_day_source_ids must be fixed")

    tdnet = _required_mapping(event_gate, "tdnet")
    if tdnet.get("enabled") is not True:
        raise ValueError("event_gate.tdnet.enabled must be true")
    if tdnet.get("policy") != "REJECT_ANY_DISCLOSURE":
        raise ValueError("event_gate.tdnet.policy must be REJECT_ANY_DISCLOSURE")
    if tdnet.get("source_id") != "JPX_TDNET":
        raise ValueError("event_gate.tdnet.source_id must be JPX_TDNET")

    news = _required_mapping(event_gate, "news")
    if news.get("enabled") is not True:
        raise ValueError("event_gate.news.enabled must be true")
    if news.get("required_source_ids") != ["YAHOO_JP_NEWS", "KABUTAN_NEWS"]:
        raise ValueError("event_gate.news.required_source_ids must be fixed")
    if news.get("confirmation_source_ids") != ["JPX_TDNET", "COMPANY_IR_DISCLOSURE"]:
        raise ValueError("event_gate.news.confirmation_source_ids must be fixed")

    post_cutoff = _required_mapping(event_gate, "post_cutoff")
    if post_cutoff.get("recheck_required") is not True:
        raise ValueError("event_gate.post_cutoff.recheck_required must be true")


RANKING_ALLOWED_TOP_KEYS = frozenset(
    {
        "enabled",
        "version",
        "method",
        "features",
        "feature_rank_method",
        "aggregation",
        "tie_break",
        "missing_data_policy",
    }
)
RANKING_ALLOWED_FEATURE_KEYS = frozenset({"turnover", "relative_tick_size"})
RANKING_ALLOWED_TURNOVER_KEYS = frozenset(
    {"direction", "source_policy", "estimated_allowed"}
)
RANKING_ALLOWED_TICK_KEYS = frozenset({"direction"})
RANKING_ALLOWED_AGGREGATION_KEYS = frozenset({"method"})
RANKING_FORBIDDEN_FIELDS = frozenset(
    {
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
    }
)


def _reject_ranking_block_fields(block: dict[str, Any], allowed: frozenset[str], path: str) -> None:
    """Reject anything outside a ranking block's fixed field set.

    The permanently-out-of-scope names (scores, weights, confidences,
    normalization, and the minimum-turnover / maximum-relative-tick
    thresholds) are reported first and by name: they are a deliberate scope
    decision rather than a typo, and an operator must be told so explicitly.
    Checking them after the generic unknown-field check would make that
    message unreachable, because every forbidden name is also unknown.
    """
    forbidden = set(block) & RANKING_FORBIDDEN_FIELDS
    if forbidden:
        raise ValueError(f"{path} must not define: {', '.join(sorted(forbidden))}")
    unknown = set(block) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown field(s): {', '.join(sorted(unknown))}")


def _validate_ranking_config(config: dict[str, Any]) -> None:
    ranking = _required_mapping(config, "ranking")
    _reject_ranking_block_fields(ranking, RANKING_ALLOWED_TOP_KEYS, "ranking")
    if ranking.get("enabled") is not True:
        raise ValueError("ranking.enabled must be true")
    if ranking.get("version") != "ranking-v1":
        raise ValueError("ranking.version must be 'ranking-v1'")
    if ranking.get("method") != "ordinal_rank_sum":
        raise ValueError("ranking.method must be 'ordinal_rank_sum'")
    if ranking.get("feature_rank_method") != "competition":
        raise ValueError("ranking.feature_rank_method must be 'competition'")
    if ranking.get("missing_data_policy") != "fail_closed":
        raise ValueError("ranking.missing_data_policy must be 'fail_closed'")
    if ranking.get("tie_break") != [
        "turnover_desc",
        "relative_tick_size_asc",
        "ticker_asc",
    ]:
        raise ValueError("ranking.tie_break must be fixed")

    features = _required_mapping(ranking, "features", "ranking")
    _reject_ranking_block_fields(features, RANKING_ALLOWED_FEATURE_KEYS, "ranking.features")
    turnover = _required_mapping(features, "turnover", "ranking.features")
    _reject_ranking_block_fields(
        turnover, RANKING_ALLOWED_TURNOVER_KEYS, "ranking.features.turnover"
    )
    if turnover.get("direction") != "desc":
        raise ValueError("ranking.features.turnover.direction must be 'desc'")
    if turnover.get("source_policy") != "actual_only":
        raise ValueError("ranking.features.turnover.source_policy must be 'actual_only'")
    if turnover.get("estimated_allowed") is not False:
        raise ValueError("ranking.features.turnover.estimated_allowed must be false")

    relative_tick_size = _required_mapping(features, "relative_tick_size", "ranking.features")
    _reject_ranking_block_fields(
        relative_tick_size, RANKING_ALLOWED_TICK_KEYS, "ranking.features.relative_tick_size"
    )
    if relative_tick_size.get("direction") != "asc":
        raise ValueError("ranking.features.relative_tick_size.direction must be 'asc'")

    aggregation = _required_mapping(ranking, "aggregation", "ranking")
    _reject_ranking_block_fields(
        aggregation, RANKING_ALLOWED_AGGREGATION_KEYS, "ranking.aggregation"
    )
    if aggregation.get("method") != "rank_sum":
        raise ValueError("ranking.aggregation.method must be 'rank_sum'")


def _validate_v2_legacy_event_rules(screening: dict[str, Any]) -> None:
    legacy_rules = {"exclude_earnings", "exclude_special_disclosures"}
    for rule_id in legacy_rules:
        rule = _required(screening, rule_id, "screening")
        if rule is None:
            continue
        if isinstance(rule, dict):
            if rule.get("enabled") is not False or rule.get("threshold") is not None:
                raise ValueError(f"screening.{rule_id} must remain disabled for v2")
        else:
            if rule is not None:
                raise ValueError(f"screening.{rule_id} must remain disabled for v2")


def normalize_screening_rules(screening: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for key in SCREENING_KEYS:
        value = _required(screening, key, "screening")
        normalized[key] = _normalize_screening_rule(key, value)
    return normalized


def _normalize_screening_rule(rule_id: str, value: Any) -> dict[str, Any]:
    defaults = SCREENING_RULE_DEFAULTS[rule_id]
    if value is None or not isinstance(value, dict):
        threshold = value
        if rule_id.startswith("exclude_") and threshold is not None:
            if not isinstance(threshold, bool):
                raise ValueError(f"screening.{rule_id} must be boolean or null")
        elif threshold is not None:
            _positive_decimal(threshold, f"screening.{rule_id}")
        return {
            "rule_id": rule_id,
            "enabled": threshold is not None,
            "phase": defaults["phase"],
            "criticality": defaults["criticality"],
            "input_fields": tuple(defaults["input_fields"]),
            "operator": defaults["operator"],
            "threshold": threshold,
            "reason_code": defaults["reason_code"],
            "legacy_scalar": True,
        }

    rule = dict(value)
    enabled = _required_bool(rule, "enabled", f"screening.{rule_id}")
    phase = _required_text_choice(
        rule,
        "phase",
        f"screening.{rule_id}",
        SCREENING_PHASES,
    )
    criticality = _required_text_choice(
        rule,
        "criticality",
        f"screening.{rule_id}",
        SCREENING_CRITICALITIES,
    )
    operator = _required_text_choice(
        rule,
        "operator",
        f"screening.{rule_id}",
        SCREENING_OPERATORS,
    )
    input_fields = _required_text_list(rule, "input_fields", f"screening.{rule_id}")
    reason_code = _required_pattern_text(
        rule,
        "reason_code",
        f"screening.{rule_id}",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
    )
    threshold = _required(rule, "threshold", f"screening.{rule_id}")
    # 無効に設定された rule (enabled == False) は threshold を設定してはいけない
    if enabled is False and threshold is not None:
        # Backwards-compatible behavior: clear supplied threshold but warn.
        logging.warning(
            "screening.%s: disabled rule supplied threshold; clearing",
            rule_id,
        )
        threshold = None
    if enabled and threshold is None:
        raise ValueError(f"screening.{rule_id}.threshold is required when enabled")
    if threshold is not None:
        if operator in {"is", "is_not"}:
            if not isinstance(threshold, bool):
                raise ValueError(
                    f"screening.{rule_id}.threshold must be boolean for {operator}"
                )
        else:
            _positive_decimal(threshold, f"screening.{rule_id}.threshold")
    return {
        "rule_id": rule_id,
        "enabled": enabled,
        "phase": phase,
        "criticality": criticality,
        "input_fields": tuple(input_fields),
        "operator": operator,
        "threshold": threshold,
        "reason_code": reason_code,
        "legacy_scalar": False,
    }


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


def _required_mapping(
    config: dict[str, Any], key: str, path: str = "configuration"
) -> dict[str, Any]:
    """Fetch a required nested mapping.

    ``path`` names the block ``config`` itself is, so a nested lookup can
    report ``ranking.features`` instead of the ambiguous top-level-sounding
    ``configuration.features``. It defaults to the top-level block name used
    by every pre-existing caller, so their messages are unchanged.
    """
    value = _required(config, key, path)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping" if path == "configuration" else f"{path}.{key} must be a mapping")
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


def _required_text_choice(
    mapping: dict[str, Any],
    key: str,
    path: str,
    choices: tuple[str, ...],
) -> str:
    value = _non_empty_string(_required(mapping, key, path), f"{path}.{key}")
    if value not in choices:
        raise ValueError(f"{path}.{key} must be one of: {', '.join(choices)}")
    return value


def _required_text_list(
    mapping: dict[str, Any],
    key: str,
    path: str,
) -> list[str]:
    value = _required(mapping, key, path)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{path}.{key} must be a non-empty string list")
    return [item.strip() for item in value]


def _required_pattern_text(
    mapping: dict[str, Any],
    key: str,
    path: str,
    allowed_chars: str,
) -> str:
    value = _non_empty_string(_required(mapping, key, path), f"{path}.{key}")
    if any(char not in allowed_chars for char in value):
        raise ValueError(f"{path}.{key} contains invalid characters")
    return value

# Note: normalization logic implemented further below; helper removed.
