from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.config import SCREENING_KEYS, normalize_screening_rules
from src.market import MarketDataRecord, validate_market_data
from src.strategy import OrderPlan, build_order_plan


@dataclass(frozen=True)
class ScreeningResult:
    ticker: str | None
    status: str
    screening_status: str
    reasons: tuple[str, ...]
    unresolved_screening: tuple[str, ...]
    passed_rules: tuple[str, ...]
    failed_rules: tuple[str, ...]
    unavailable_rules: tuple[str, ...]
    reason_codes: tuple[str, ...]
    source_refs: tuple[str, ...]
    rule_evaluations: tuple[dict[str, Any], ...]
    features: dict[str, Any]
    order_plan: OrderPlan | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "screening_status": self.screening_status,
            "reasons": list(self.reasons),
            "unresolved_screening": list(self.unresolved_screening),
            "passed_rules": list(self.passed_rules),
            "failed_rules": list(self.failed_rules),
            "unavailable_rules": list(self.unavailable_rules),
            "reason_codes": list(self.reason_codes),
            "source_refs": list(self.source_refs),
            "rule_evaluations": _json_ready(list(self.rule_evaluations)),
            "features": _json_ready(self.features),
            "order_plan": _json_ready(self.order_plan.as_dict()) if self.order_plan else None,
        }


def screen_market_record(
    record: MarketDataRecord,
    config: dict[str, Any],
    source_matrix: dict[str, Any] | None = None,
) -> ScreeningResult:
    validation = validate_market_data(record, source_matrix)
    rules = normalize_screening_rules(config["screening"])
    unresolved = tuple(
        key for key in SCREENING_KEYS if rules[key]["threshold"] is None
    )
    features = _build_features(record, order_plan=None)
    skipped_evaluations = tuple(_rule_evaluation(rule, "DISABLED") for rule in rules.values())
    if not validation.valid_for_trade:
        status = "DATA_UNAVAILABLE" if _is_data_unavailable(validation.errors) else "REJECTED"
        screening_status = "DATA_UNAVAILABLE" if status == "DATA_UNAVAILABLE" else "REJECT"
        return ScreeningResult(
            ticker=record.ticker,
            status=status,
            screening_status=screening_status,
            reasons=validation.errors,
            unresolved_screening=unresolved,
            passed_rules=(),
            failed_rules=(),
            unavailable_rules=(),
            reason_codes=("SCREENING_DATA_UNAVAILABLE",)
            if status == "DATA_UNAVAILABLE"
            else ("MARKET_DATA_INVALID",),
            source_refs=(),
            rule_evaluations=skipped_evaluations,
            features=features,
            order_plan=None,
        )

    assert record.previous_high is not None
    assert record.tick_size is not None
    plan = build_order_plan(record.previous_high, record.tick_size, config=config)
    features = _build_features(record, order_plan=plan)
    reasons: list[str] = []
    if not plan.affordable:
        reasons.append("entry_limit multiplied by shares exceeds capital")
    evaluations = tuple(_evaluate_rule(rule, features) for rule in rules.values())
    passed_rules = tuple(
        item["rule_id"] for item in evaluations if item["result"] == "PASS"
    )
    failed_rules = tuple(
        item["rule_id"] for item in evaluations if item["result"] == "REJECT"
    )
    unavailable_rules = tuple(
        item["rule_id"] for item in evaluations if item["result"] == "DATA_UNAVAILABLE"
    )
    reason_codes = tuple(
        item["reason_code"]
        for item in evaluations
        if item["result"] in {"REJECT", "DATA_UNAVAILABLE"} and item.get("reason_code")
    )
    for item in evaluations:
        if item["result"] == "REJECT":
            reasons.append(f"screening.{item['rule_id']} failed: {item['reason_code']}")
        elif item["result"] == "DATA_UNAVAILABLE":
            reasons.append(f"screening.{item['rule_id']} input is unavailable")
    source_refs = _unique(
        source_ref
        for item in evaluations
        if item["result"] in {"PASS", "REJECT", "DATA_UNAVAILABLE"}
        for source_ref in item.get("source_refs", [])
    )
    if unavailable_rules:
        screening_status = "DATA_UNAVAILABLE"
    elif failed_rules or reasons:
        screening_status = "REJECT"
    else:
        screening_status = "PASS"
    status = (
        "DATA_UNAVAILABLE"
        if unavailable_rules
        else ("REJECTED" if reasons else "ELIGIBLE")
    )
    return ScreeningResult(
        ticker=record.ticker,
        status=status,
        screening_status=screening_status,
        reasons=tuple(reasons),
        unresolved_screening=unresolved,
        passed_rules=passed_rules,
        failed_rules=failed_rules,
        unavailable_rules=unavailable_rules,
        reason_codes=reason_codes or (("CAPITAL_LIMIT_EXCEEDED",) if reasons else ()),
        source_refs=tuple(source_refs),
        rule_evaluations=evaluations,
        features=features,
        order_plan=plan,
    )


def _build_features(
    record: MarketDataRecord,
    *,
    order_plan: OrderPlan | None,
) -> dict[str, Any]:
    daily_range = (
        record.high - record.low
        if record.high is not None and record.low is not None
        else None
    )
    daily_range_pct = (
        daily_range / record.close * Decimal("100")
        if daily_range is not None and record.close is not None and record.close != 0
        else None
    )
    previous_day_change_pct = (
        (record.close - record.previous_close) / record.previous_close * Decimal("100")
        if record.close is not None
        and record.previous_close is not None
        and record.previous_close != 0
        else None
    )
    estimated_turnover = (
        record.close * Decimal(record.volume)
        if record.close is not None and record.volume is not None
        else None
    )
    return {
        "previous_day_volume": _feature(record.volume, _source_refs(record, ("volume",))),
        "required_capital_yen": _feature(
            order_plan.estimated_purchase_amount if order_plan else None,
            _source_refs(record, ("previous_high", "tick_size")),
        ),
        "daily_range_yen": _feature(
            daily_range,
            _source_refs(record, ("high", "low")),
        ),
        "daily_range_pct": _feature(
            daily_range_pct,
            _source_refs(record, ("high", "low", "close")),
        ),
        "previous_day_change_pct": _feature(
            previous_day_change_pct,
            _source_refs(record, ("close", "previous_close")),
        ),
        "turnover": _feature(
            record.turnover,
            _source_refs(record, ("turnover",)),
            estimated=False,
        ),
        "estimated_turnover": _feature(
            estimated_turnover,
            _source_refs(record, ("close", "volume")),
            estimated=True,
        ),
    }


def _feature(
    value: Any,
    source_refs: list[dict[str, str]],
    *,
    estimated: bool = False,
) -> dict[str, Any]:
    return {
        "value": value,
        "source_refs": [item["source_ref"] for item in source_refs],
        "source_urls": _unique(item["source_url"] for item in source_refs),
        "estimated": estimated,
    }


def _source_refs(
    record: MarketDataRecord,
    field_names: tuple[str, ...],
) -> list[dict[str, str]]:
    source_by_ref = {
        source.source_ref: source
        for source in record.sources
        if source.source_ref is not None
    }
    refs: list[str] = [
        source.source_ref
        for source in record.sources
        if source.source_ref is not None and source.field_name in field_names
    ]
    for provenance in record.field_provenance:
        if str(provenance.get("field_name", "")).strip() not in field_names:
            continue
        refs.extend(str(item) for item in provenance.get("source_refs", []))
    result: list[dict[str, str]] = []
    for source_ref in _unique(refs):
        source = source_by_ref.get(source_ref)
        if source is None or source.source_url is None:
            continue
        result.append({"source_ref": source_ref, "source_url": source.source_url})
    return result


def _evaluate_rule(rule: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    if not rule["enabled"]:
        return _rule_evaluation(rule, "DISABLED")
    if rule["phase"] != "hard_screening":
        return _rule_evaluation(rule, "SKIPPED_PHASE")
    if rule["criticality"] != "TRADE_CRITICAL":
        return _rule_evaluation(rule, "SKIPPED_CRITICALITY")

    field_name = rule["input_fields"][0]
    feature = features.get(_feature_name(field_name))
    value = feature.get("value") if isinstance(feature, dict) else None
    source_refs = feature.get("source_refs", []) if isinstance(feature, dict) else []
    source_urls = feature.get("source_urls", []) if isinstance(feature, dict) else []
    if value is None or not source_refs:
        return _rule_evaluation(
            rule,
            "DATA_UNAVAILABLE",
            input_values={field_name: value},
            source_refs=source_refs,
            source_urls=source_urls,
        )
    result = "PASS" if _compare(value, rule["operator"], rule["threshold"]) else "REJECT"
    return _rule_evaluation(
        rule,
        result,
        input_values={field_name: value},
        source_refs=source_refs,
        source_urls=source_urls,
    )


def _feature_name(field_name: str) -> str:
    if field_name == "volume":
        return "previous_day_volume"
    return field_name


def _compare(value: Any, operator: str, threshold: Any) -> bool:
    if operator == ">=":
        return Decimal(str(value)) >= Decimal(str(threshold))
    if operator == "<=":
        return Decimal(str(value)) <= Decimal(str(threshold))
    if operator == ">":
        return Decimal(str(value)) > Decimal(str(threshold))
    if operator == "<":
        return Decimal(str(value)) < Decimal(str(threshold))
    if operator == "==":
        return value == threshold
    if operator == "!=":
        return value != threshold
    if operator == "is":
        return value is threshold
    if operator == "is_not":
        return value is not threshold
    raise ValueError(f"Unsupported screening operator: {operator}")


def _rule_evaluation(
    rule: dict[str, Any],
    result: str,
    *,
    input_values: dict[str, Any] | None = None,
    source_refs: list[str] | None = None,
    source_urls: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule["rule_id"],
        "enabled": rule["enabled"],
        "phase": rule["phase"],
        "criticality": rule["criticality"],
        "input_fields": list(rule["input_fields"]),
        "operator": rule["operator"],
        "threshold": rule["threshold"],
        "result": result,
        "reason_code": rule["reason_code"]
        if result in {"REJECT", "DATA_UNAVAILABLE"}
        else None,
        "input_values": input_values or {},
        "source_refs": source_refs or [],
        "source_urls": source_urls or [],
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _unique(values: Any) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_data_unavailable(errors: tuple[str, ...]) -> bool:
    data_error_prefixes = (
        "Missing required market data:",
        "Market data status is not verified:",
        "Source policy is undefined",
        "Conflicting source values for market data field:",
        "No source value matches market data field:",
        "Missing source for market data field:",
        "SINGLE_SOURCE_ONLY for OHLCV field:",
        "Missing primary OHLCV source",
        "Missing secondary OHLCV source",
        "Missing JPX primary source for tick_size",
    )
    return any(
        error.startswith(data_error_prefixes)
        or "source is missing from sources.json" in error
        for error in errors
    )
