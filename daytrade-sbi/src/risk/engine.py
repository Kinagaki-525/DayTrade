from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.config import SUPPORTED_STRATEGY, validate_strategy_config
from src.strategy.pricing import (
    calculate_entry_prices,
    calculate_exit_prices,
    exact_int,
    is_tick_aligned,
    to_decimal,
)


@dataclass(frozen=True)
class OrderProposal:
    target_date: str | None
    ticker: str | None
    company_name: str | None
    strategy_type: str | None
    previous_high: Decimal | None
    tick_size: Decimal | None
    entry_trigger: Decimal | None
    entry_limit: Decimal | None
    take_profit: Decimal | None
    stop_loss: Decimal | None
    shares: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderProposal:
        return cls(
            target_date=_optional_text(data.get("target_date")),
            ticker=_optional_text(data.get("ticker")),
            company_name=_optional_text(data.get("company_name")),
            strategy_type=_optional_text(data.get("strategy_type")),
            previous_high=_optional_decimal(data.get("previous_high")),
            tick_size=_optional_decimal(data.get("tick_size")),
            entry_trigger=_optional_decimal(data.get("entry_trigger")),
            entry_limit=_optional_decimal(data.get("entry_limit")),
            take_profit=_optional_decimal(data.get("take_profit")),
            stop_loss=_optional_decimal(data.get("stop_loss")),
            shares=_optional_int(data.get("shares")),
        )


@dataclass(frozen=True)
class RiskResult:
    status: str
    ticker: str | None
    required_capital_yen: Decimal | None
    expected_loss_yen: Decimal | None
    violations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ticker": self.ticker,
            "required_capital_yen": (
                str(self.required_capital_yen)
                if self.required_capital_yen is not None
                else None
            ),
            "expected_loss_yen": (
                str(self.expected_loss_yen)
                if self.expected_loss_yen is not None
                else None
            ),
            "violations": list(self.violations),
        }


def evaluate_order(
    proposal: OrderProposal,
    config: dict[str, Any],
    *,
    market_data_valid: bool,
    market_target_date: str | None,
    market_previous_high: Decimal | None = None,
    market_tick_size: Decimal | None = None,
    current_positions: int,
    trades_today: int,
) -> RiskResult:
    """Evaluate without changing any value in the proposed order."""
    validate_strategy_config(config)
    violations: list[str] = []
    required_fields = (
        "target_date",
        "ticker",
        "company_name",
        "strategy_type",
        "previous_high",
        "tick_size",
        "entry_trigger",
        "entry_limit",
        "take_profit",
        "stop_loss",
        "shares",
    )
    for field_name in required_fields:
        if getattr(proposal, field_name) is None:
            violations.append(f"Missing or invalid required field: {field_name}")
    if not market_data_valid:
        violations.append("Market data validation did not pass")
    if market_target_date is None:
        violations.append("Validated market target_date is required")
    elif proposal.target_date is not None and proposal.target_date != market_target_date:
        violations.append("target_date does not match validated market data")
    if market_data_valid and market_previous_high is None:
        violations.append("Validated market previous_high is required")
    if market_data_valid and market_tick_size is None:
        violations.append("Validated market tick_size is required")
    if (
        market_previous_high is not None
        and proposal.previous_high is not None
        and proposal.previous_high != market_previous_high
    ):
        violations.append("previous_high does not match validated market data")
    if (
        market_tick_size is not None
        and proposal.tick_size is not None
        and proposal.tick_size != market_tick_size
    ):
        violations.append("tick_size does not match validated market data")

    capital = to_decimal(config["capital"]["total_yen"])
    configured_shares = int(config["capital"]["position_size"])
    max_loss = to_decimal(config["risk"]["max_loss_per_trade_yen"])
    max_positions = int(config["risk"]["max_positions"])
    max_trades = int(config["risk"]["max_trades_per_day"])

    if proposal.strategy_type is not None and proposal.strategy_type != SUPPORTED_STRATEGY:
        violations.append("strategy_type is not allowed")
    if proposal.shares is not None and proposal.shares != configured_shares:
        violations.append(f"shares must equal {configured_shares}")
    if current_positions < 0:
        violations.append("current_positions must not be negative")
    elif current_positions >= max_positions:
        violations.append("max_positions would be exceeded")
    if trades_today < 0:
        violations.append("trades_today must not be negative")
    elif trades_today >= max_trades:
        violations.append("max_trades_per_day would be exceeded")

    required_capital: Decimal | None = None
    expected_loss: Decimal | None = None
    if proposal.entry_limit is not None and proposal.shares is not None:
        required_capital = proposal.entry_limit * Decimal(proposal.shares)
        if required_capital > capital:
            violations.append("Required capital exceeds capital.total_yen")
    if proposal.entry_trigger is not None and proposal.entry_limit is not None:
        if proposal.entry_trigger > proposal.entry_limit:
            violations.append("entry_trigger must not exceed entry_limit")
    if proposal.stop_loss is not None and proposal.entry_limit is not None:
        if proposal.stop_loss >= proposal.entry_limit:
            violations.append("stop_loss must be below entry_limit")
    if proposal.take_profit is not None and proposal.entry_limit is not None:
        if proposal.take_profit <= proposal.entry_limit:
            violations.append("take_profit must be above entry_limit")
    if (
        proposal.stop_loss is not None
        and proposal.entry_limit is not None
        and proposal.shares is not None
        and proposal.stop_loss < proposal.entry_limit
    ):
        expected_loss = (
            proposal.entry_limit - proposal.stop_loss
        ) * Decimal(proposal.shares)
        if expected_loss > max_loss:
            violations.append("Expected loss exceeds risk.max_loss_per_trade_yen")

    if proposal.tick_size is not None and proposal.tick_size > 0:
        for field_name in (
            "previous_high",
            "entry_trigger",
            "entry_limit",
            "take_profit",
            "stop_loss",
        ):
            value = getattr(proposal, field_name)
            if value is not None and not is_tick_aligned(value, proposal.tick_size):
                violations.append(f"{field_name} is not aligned to tick_size")
    elif proposal.tick_size is not None:
        violations.append("tick_size must be greater than 0")

    if proposal.previous_high is not None and proposal.tick_size is not None:
        try:
            breakout = config[SUPPORTED_STRATEGY]
            expected_entry = calculate_entry_prices(
                proposal.previous_high,
                proposal.tick_size,
                breakout["trigger_ticks"],
                breakout["entry_limit_offset_ticks"],
            )
            if proposal.entry_trigger != expected_entry.entry_trigger:
                violations.append("entry_trigger does not match strategy calculation")
            if proposal.entry_limit != expected_entry.entry_limit:
                violations.append("entry_limit does not match strategy calculation")
            if proposal.entry_limit is not None and proposal.shares is not None:
                expected_exit = calculate_exit_prices(
                    proposal.entry_limit,
                    proposal.shares,
                    config["exit"]["take_profit_yen"],
                    config["risk"]["max_loss_per_trade_yen"],
                    proposal.tick_size,
                )
                if proposal.take_profit != expected_exit.take_profit_price:
                    violations.append("take_profit does not match strategy calculation")
                if proposal.stop_loss != expected_exit.stop_loss_price:
                    violations.append("stop_loss does not match strategy calculation")
        except ValueError as exc:
            violations.append(f"Strategy price calculation failed: {exc}")

    return RiskResult(
        status="REJECTED" if violations else "PASS",
        ticker=proposal.ticker,
        required_capital_yen=required_capital,
        expected_loss_yen=expected_loss,
        violations=tuple(violations),
    )


def not_applicable_result(ticker: str | None = None) -> RiskResult:
    return RiskResult(
        status="NOT_APPLICABLE",
        ticker=ticker,
        required_capital_yen=None,
        expected_loss_yen=None,
        violations=(),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return to_decimal(value)
    except ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return exact_int(value, "shares")
    except ValueError:
        return None
