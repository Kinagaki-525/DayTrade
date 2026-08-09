from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlparse

from src.market.models import MarketDataRecord, SourceRecord
from src.strategy.pricing import is_tick_aligned, to_decimal


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

    for field_name in ("open", "high", "low", "close", "previous_close", "previous_high", "tick_size"):
        value = getattr(record, field_name)
        if value is not None and value <= 0:
            errors.append(f"{field_name} must be greater than 0")
    if record.volume is not None and record.volume < 0:
        errors.append("volume must be greater than or equal to 0")

    if all(value is not None for value in (record.open, record.high, record.low, record.close)):
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
        for field_name in ("open", "high", "low", "close", "previous_close", "previous_high"):
            value = getattr(record, field_name)
            if value is not None and not is_tick_aligned(value, record.tick_size):
                errors.append(f"{field_name} is not aligned to tick_size")

    if not record.sources:
        errors.append("At least one traceable source is required")
    for index, source in enumerate(record.sources):
        errors.extend(_validate_source(source, record, index))

    for field_name in SOURCED_NUMERIC_FIELDS:
        field_sources = [source for source in record.sources if source.field_name == field_name]
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
    if source.retrieved_at is not None and not _is_timezone_aware_datetime(source.retrieved_at):
        errors.append(f"{prefix}.retrieved_at must be an ISO 8601 datetime with timezone")
    if source.trading_date is not None and not _is_iso_date(source.trading_date):
        errors.append(f"{prefix}.trading_date must use YYYY-MM-DD")
    if record.ticker is not None and source.ticker != record.ticker:
        errors.append(f"{prefix}.ticker does not match the market record")
    if record.trading_date is not None and source.trading_date != record.trading_date:
        errors.append(f"{prefix}.trading_date does not match the market record")
    return errors


def _is_iso_date(value: str) -> bool:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return False
    try:
        date.fromisoformat(value)
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
