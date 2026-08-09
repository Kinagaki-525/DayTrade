from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from src.market.models import MarketDataRecord


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


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
