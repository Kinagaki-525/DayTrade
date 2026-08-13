"""Parser contract shared by every deterministic source parser.

A parser is a **pure function** of:

* the raw response bytes of one stored source page,
* its source definition (from ``config/source_matrix.yaml``),
* a target context (ticker / trading date / content type).

It must not call an LLM, must not touch the network, and must not read the
clock. Given identical inputs it returns identical output, forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class ParseContext:
    source_id: str
    trading_date: str
    ticker: str | None = None
    content_type: str | None = None
    #: The exact JPX trading day immediately before ``trading_date``, as
    #: resolved by :mod:`src.trading_calendar` from verified JPX_CALENDAR
    #: evidence. ``None`` means that evidence was unavailable this run, so a
    #: history parser must not derive previous_close/previous_high at all --
    #: it must never fall back to guessing (e.g. the nearest earlier row a
    #: page happens to publish). A parser only ever reads this field; it must
    #: never compute its own notion of "the previous day".
    previous_trading_date: str | None = None


@dataclass(frozen=True)
class ParsedValue:
    field_name: str
    ticker: str | None
    trading_date: str
    value: Any
    raw_value: str | None = None
    raw_unit: str | None = None
    canonical_value_yen: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "field_name": self.field_name,
            "ticker": self.ticker,
            "trading_date": self.trading_date,
            "value": _jsonable(self.value),
        }
        if self.raw_value is not None:
            payload["raw_value"] = self.raw_value
        if self.raw_unit is not None:
            payload["raw_unit"] = self.raw_unit
        if self.canonical_value_yen is not None:
            payload["canonical_value_yen"] = self.canonical_value_yen
        return payload


@dataclass(frozen=True)
class ParseResult:
    status: str
    values: tuple[ParsedValue, ...] = ()
    reason_codes: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "values": [value.as_dict() for value in self.values],
            "reason_codes": list(self.reason_codes),
        }


class SourceParser(Protocol):
    def __call__(
        self,
        raw: bytes,
        source_definition: dict[str, Any],
        context: ParseContext,
    ) -> ParseResult: ...


def parse_failed(message: str) -> ParseResult:
    return ParseResult(status="PARSE_FAILED", reason_codes=(f"PARSE_FAILED: {message}",))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value
