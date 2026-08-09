from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.strategy.pricing import exact_int, to_decimal


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
