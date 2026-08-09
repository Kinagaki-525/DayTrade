from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.config import SCREENING_KEYS
from src.market import MarketDataRecord, validate_market_data
from src.strategy import OrderPlan, build_order_plan


@dataclass(frozen=True)
class ScreeningResult:
    ticker: str | None
    status: str
    reasons: tuple[str, ...]
    unresolved_screening: tuple[str, ...]
    order_plan: OrderPlan | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "reasons": list(self.reasons),
            "unresolved_screening": list(self.unresolved_screening),
            "order_plan": _json_ready(self.order_plan.as_dict()) if self.order_plan else None,
        }


def screen_market_record(
    record: MarketDataRecord,
    config: dict[str, Any],
) -> ScreeningResult:
    validation = validate_market_data(record)
    screening = config["screening"]
    unresolved = tuple(key for key in SCREENING_KEYS if screening[key] is None)
    if not validation.valid_for_trade:
        return ScreeningResult(
            ticker=record.ticker,
            status="REJECTED",
            reasons=validation.errors,
            unresolved_screening=unresolved,
            order_plan=None,
        )

    assert record.previous_high is not None
    assert record.tick_size is not None
    plan = build_order_plan(record.previous_high, record.tick_size, config=config)
    reasons: list[str] = []
    if not plan.affordable:
        reasons.append("entry_limit multiplied by shares exceeds capital")
    configured_without_approved_filter = [
        key for key in SCREENING_KEYS if screening[key] is not None
    ]
    reasons.extend(
        f"screening.{key} has a value but no user-approved filter implementation"
        for key in configured_without_approved_filter
    )
    return ScreeningResult(
        ticker=record.ticker,
        status="REJECTED" if reasons else "ELIGIBLE",
        reasons=tuple(reasons),
        unresolved_screening=unresolved,
        order_plan=plan,
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
