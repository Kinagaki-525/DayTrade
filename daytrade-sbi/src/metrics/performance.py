from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


DEFAULT_TRADES_PATH = Path(__file__).resolve().parents[2] / "trades" / "trades.csv"
EXIT_REASONS = ("take_profit", "stop_loss", "end_of_day")
ALLOWED_EXIT_REASONS = (*EXIT_REASONS, "other")
TRADE_COLUMNS = (
    "trade_date",
    "ticker",
    "company_name",
    "strategy_type",
    "previous_close",
    "previous_high",
    "tick_size",
    "entry_trigger",
    "entry_limit",
    "planned_take_profit",
    "planned_stop_loss",
    "actual_entry",
    "actual_exit",
    "shares",
    "profit_loss_yen",
    "exit_reason",
    "entry_time",
    "exit_time",
    "strategy_version",
    "notes",
)


def load_trades(csv_path: str | Path = DEFAULT_TRADES_PATH) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            return []
        _validate_trade_columns(reader.fieldnames)
        rows: list[dict[str, str]] = []
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"CSV row {line_number} contains extra values")
            if _has_any_value(row):
                _validate_trade_row(row, line_number)
                rows.append(row)
        return rows


def calculate_metrics_from_csv(csv_path: str | Path = DEFAULT_TRADES_PATH) -> dict[str, Any]:
    return calculate_metrics(load_trades(csv_path))


def calculate_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    trade_rows = [row for row in rows if _has_any_value(row)]
    profit_losses = [
        result
        for result in (_parse_decimal(row.get("profit_loss_yen")) for row in trade_rows)
        if result is not None
    ]
    wins = [value for value in profit_losses if value > 0]
    losses = [value for value in profit_losses if value < 0]
    exit_counts = {reason: 0 for reason in EXIT_REASONS}
    for row in trade_rows:
        reason = str(row.get("exit_reason", "")).strip()
        if reason in exit_counts:
            exit_counts[reason] += 1
    calculable_count = len(profit_losses)
    total_trades = len(trade_rows)
    return {
        "total_trades": total_trades,
        "calculable_trades": calculable_count,
        "uncalculable_trades": total_trades - calculable_count,
        "win_trades": len(wins),
        "loss_trades": len(losses),
        "win_rate": len(wins) / calculable_count if calculable_count else None,
        "cumulative_profit_loss_yen": sum(profit_losses, Decimal("0")),
        "average_profit": _average(wins),
        "average_loss": _average(losses),
        "average_profit_loss_per_trade": _average(profit_losses),
        "max_profit": max(wins) if wins else None,
        "max_loss": min(losses) if losses else None,
        "take_profit_count": exit_counts["take_profit"],
        "stop_loss_count": exit_counts["stop_loss"],
        "end_of_day_count": exit_counts["end_of_day"],
    }


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _has_any_value(row: dict[str, Any]) -> bool:
    return any(str(value).strip() for value in row.values() if value is not None)


def _validate_trade_columns(fieldnames: list[str]) -> None:
    duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate CSV columns: {', '.join(duplicates)}")
    missing = [name for name in TRADE_COLUMNS if name not in fieldnames]
    if missing:
        raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")


def _validate_trade_row(row: dict[str, str], line_number: int) -> None:
    exit_reason = row.get("exit_reason", "").strip()
    if exit_reason and exit_reason not in ALLOWED_EXIT_REASONS:
        allowed = ", ".join(ALLOWED_EXIT_REASONS)
        raise ValueError(
            f"Invalid exit_reason at CSV row {line_number}: "
            f"{exit_reason!r}; expected one of {allowed}"
        )
