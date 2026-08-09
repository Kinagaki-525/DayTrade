from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from src.strategy import exact_int, is_tick_aligned, to_decimal


@dataclass(frozen=True)
class SourceRecord:
    source_name: str | None
    source_url: str | None
    retrieved_at: str | None
    trading_date: str | None
    ticker: str | None
    field_name: str | None
    value: Any

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRecord:
        return cls(
            source_name=_optional_text(data.get("source_name")),
            source_url=_optional_text(data.get("source_url")),
            retrieved_at=_optional_text(data.get("retrieved_at")),
            trading_date=_optional_text(data.get("trading_date")),
            ticker=_optional_text(data.get("ticker")),
            field_name=_optional_text(data.get("field_name")),
            value=data.get("value"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketDataRecord:
    ticker: str | None
    company_name: str | None
    trading_date: str | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None
    previous_close: Decimal | None
    previous_high: Decimal | None
    tick_size: Decimal | None
    turnover: Decimal | None
    spread: Decimal | None
    earnings_scheduled: bool | None
    special_disclosures: bool | None
    sources: tuple[SourceRecord, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketDataRecord:
        raw_sources = data.get("sources")
        if raw_sources is None:
            sources: tuple[SourceRecord, ...] = ()
        elif not isinstance(raw_sources, list) or any(
            not isinstance(item, dict) for item in raw_sources
        ):
            raise ValueError("market data sources must be an array of objects")
        else:
            sources = tuple(SourceRecord.from_dict(item) for item in raw_sources)
        return cls(
            ticker=_optional_text(data.get("ticker")),
            company_name=_optional_text(data.get("company_name")),
            trading_date=_optional_text(data.get("trading_date")),
            open=_optional_decimal(data.get("open")),
            high=_optional_decimal(data.get("high")),
            low=_optional_decimal(data.get("low")),
            close=_optional_decimal(data.get("close")),
            volume=_optional_int(data.get("volume"), "volume"),
            previous_close=_optional_decimal(data.get("previous_close")),
            previous_high=_optional_decimal(data.get("previous_high")),
            tick_size=_optional_decimal(data.get("tick_size")),
            turnover=_optional_decimal(data.get("turnover")),
            spread=_optional_decimal(data.get("spread")),
            earnings_scheduled=_optional_bool(data.get("earnings_scheduled")),
            special_disclosures=_optional_bool(data.get("special_disclosures")),
            sources=sources,
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        return _json_ready(result)


def load_market_data(path: str | Path) -> tuple[str | None, list[MarketDataRecord]]:
    with Path(path).open("r", encoding="utf-8") as json_file:
        payload = json.load(json_file, parse_float=Decimal, parse_int=Decimal)
    if not isinstance(payload, dict):
        raise ValueError("market_data.json must contain an object")
    if payload.get("schema_version") != 1:
        raise ValueError("market_data.json schema_version must be 1")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("market_data.json records must be an array")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Every market_data.json record must be an object")
    target_date = _optional_text(payload.get("target_date"))
    if target_date is None:
        raise ValueError("market_data.json target_date is required")
    try:
        parsed_target_date = _parse_iso_date(target_date)
    except ValueError as exc:
        raise ValueError("market_data.json target_date must use YYYY-MM-DD") from exc
    parsed_records = [MarketDataRecord.from_dict(record) for record in records]
    for record in parsed_records:
        if record.trading_date is None:
            continue
        try:
            record_date = _parse_iso_date(record.trading_date)
        except ValueError:
            continue
        if record_date >= parsed_target_date:
            raise ValueError("market data trading_date must be before target_date")
    return target_date, parsed_records


def _parse_iso_date(value: str) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None or str(value).strip() == "" else to_decimal(value)


def _optional_int(value: Any, name: str) -> int | None:
    return None if value is None or str(value).strip() == "" else exact_int(value, name)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Expected boolean or null, got {value!r}")
    return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


REQUIRED_TRADE_FIELDS = (
    "ticker",
    "company_name",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "previous_close",
    "previous_high",
    "tick_size",
)
SOURCED_NUMERIC_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "previous_close",
    "previous_high",
    "tick_size",
)


@dataclass(frozen=True)
class MarketValidationResult:
    valid_for_trade: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "valid_for_trade": self.valid_for_trade,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def validate_market_data(record: MarketDataRecord) -> MarketValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    for field_name in REQUIRED_TRADE_FIELDS:
        if getattr(record, field_name) is None:
            errors.append(f"Missing required market data: {field_name}")

    if record.trading_date is not None and not _is_iso_date(record.trading_date):
        errors.append("trading_date must use YYYY-MM-DD")

    price_fields = (
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "previous_high",
        "tick_size",
    )
    for field_name in price_fields:
        value = getattr(record, field_name)
        if value is not None and value <= 0:
            errors.append(f"{field_name} must be greater than 0")
    if record.volume is not None and record.volume < 0:
        errors.append("volume must be greater than or equal to 0")

    ohlc = (record.open, record.high, record.low, record.close)
    if all(value is not None for value in ohlc):
        assert record.open is not None
        assert record.high is not None
        assert record.low is not None
        assert record.close is not None
        if record.low > record.high:
            errors.append("low must not exceed high")
        if not record.low <= record.open <= record.high:
            errors.append("open must be between low and high")
        if not record.low <= record.close <= record.high:
            errors.append("close must be between low and high")

    if record.tick_size is not None and record.tick_size > 0:
        for field_name in price_fields[:-1]:
            value = getattr(record, field_name)
            if value is not None and not is_tick_aligned(value, record.tick_size):
                errors.append(f"{field_name} is not aligned to tick_size")

    if not record.sources:
        errors.append("At least one traceable source is required")
    for index, source in enumerate(record.sources):
        errors.extend(_validate_source(source, record, index))

    for field_name in SOURCED_NUMERIC_FIELDS:
        field_sources = [
            source for source in record.sources if source.field_name == field_name
        ]
        if not field_sources:
            errors.append(f"Missing source for market data field: {field_name}")
            continue
        record_value = getattr(record, field_name)
        source_values = [_as_decimal(source.value) for source in field_sources]
        valid_values = [value for value in source_values if value is not None]
        if record_value is not None and record_value not in valid_values:
            errors.append(f"No source value matches market data field: {field_name}")
        if len(set(valid_values)) > 1:
            errors.append(f"Conflicting source values for market data field: {field_name}")

    if record.turnover is None:
        warnings.append("turnover is not available")
    if record.spread is None:
        warnings.append("spread is not available")
    if record.earnings_scheduled is None:
        warnings.append("earnings schedule is not confirmed")
    if record.special_disclosures is None:
        warnings.append("special disclosures are not confirmed")

    return MarketValidationResult(not errors, tuple(errors), tuple(warnings))


def _validate_source(
    source: SourceRecord,
    record: MarketDataRecord,
    index: int,
) -> list[str]:
    prefix = f"sources[{index}]"
    errors: list[str] = []
    for field_name in (
        "source_name",
        "source_url",
        "retrieved_at",
        "trading_date",
        "ticker",
        "field_name",
    ):
        if getattr(source, field_name) is None:
            errors.append(f"{prefix}.{field_name} is required")
    if source.value is None:
        errors.append(f"{prefix}.value is required")
    if source.source_url is not None:
        parsed = urlparse(source.source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"{prefix}.source_url must be an HTTP(S) URL")
    if source.retrieved_at is not None and not _is_timezone_aware_datetime(
        source.retrieved_at
    ):
        errors.append(f"{prefix}.retrieved_at must be an ISO 8601 datetime with timezone")
    if source.trading_date is not None and not _is_iso_date(source.trading_date):
        errors.append(f"{prefix}.trading_date must use YYYY-MM-DD")
    if record.ticker is not None and source.ticker != record.ticker:
        errors.append(f"{prefix}.ticker does not match the market record")
    if record.trading_date is not None and source.trading_date != record.trading_date:
        errors.append(f"{prefix}.trading_date does not match the market record")
    return errors


def _is_iso_date(value: str) -> bool:
    try:
        _parse_iso_date(value)
    except ValueError:
        return False
    return True


def _is_timezone_aware_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _as_decimal(value: object) -> Decimal | None:
    try:
        return to_decimal(value)  # type: ignore[arg-type]
    except (ValueError, InvalidOperation):
        return None


@dataclass(frozen=True)
class SourceLedgerValidationResult:
    valid: bool
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"valid": self.valid, "errors": list(self.errors)}


def validate_source_ledger(
    market_target_date: str,
    records: Iterable[MarketDataRecord],
    source_payload: dict[str, Any],
) -> SourceLedgerValidationResult:
    errors: list[str] = []
    if source_payload["target_date"] != market_target_date:
        errors.append("sources.json target_date does not match market_data.json")

    ledger_sources = source_payload["sources"]
    canonical_ledger = [_canonical_source(source) for source in ledger_sources]
    duplicate_count = len(canonical_ledger) - len(set(canonical_ledger))
    if duplicate_count:
        errors.append(f"sources.json contains {duplicate_count} duplicate source record(s)")

    ledger_set = set(canonical_ledger)
    for record in records:
        for index, source in enumerate(record.sources):
            if _canonical_source(source.as_dict()) not in ledger_set:
                errors.append(
                    "market_data.json source is missing from sources.json: "
                    f"ticker={record.ticker}, index={index}"
                )
    return SourceLedgerValidationResult(not errors, tuple(errors))


def _canonical_source(source: dict[str, Any]) -> str:
    normalized = dict(source)
    normalized["value"] = _canonical_value(normalized.get("value"))
    return json.dumps(
        _json_ready(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if not number.is_finite():
        return value
    return {"numeric": format(number.normalize(), "f")}
