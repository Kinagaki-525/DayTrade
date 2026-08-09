from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from src.contracts import validate_recommendation_risk_link
from src.file_io import atomic_write_text
from src.market import MarketDataRecord, validate_market_data
from src.metrics import DEFAULT_TRADES_PATH, TRADE_COLUMNS, load_trades
from src.strategy import to_decimal


def build_trade_row(
    execution: dict[str, Any],
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
    market_target_date: str,
    market_records: Iterable[MarketDataRecord],
) -> dict[str, str]:
    validate_recommendation_risk_link(recommendation, risk_result)
    if recommendation.get("decision") != "TRADE":
        raise ValueError("Only a TRADE recommendation can produce a trade record")
    if risk_result.get("status") != "PASS":
        raise ValueError("Only a PASS risk result can produce a trade record")

    _require_equal(
        execution.get("trade_date"),
        recommendation.get("target_date"),
        "execution/recommendation trade_date",
    )
    _require_equal(
        execution.get("trade_date"),
        market_target_date,
        "execution/market_data trade_date",
    )
    for field_name in ("ticker", "strategy_version", "config_sha256"):
        _require_equal(
            execution.get(field_name),
            recommendation.get(field_name),
            f"execution/recommendation {field_name}",
        )

    planned_shares = recommendation.get("shares")
    if execution.get("shares") != planned_shares:
        raise ValueError(
            "Partial or mismatched fills are unresolved in TODO.md; "
            "execution shares must match recommendation shares"
        )

    ticker = str(execution["ticker"])
    matches = [record for record in market_records if record.ticker == ticker]
    if len(matches) != 1:
        raise ValueError("Execution must reference exactly one market data record")
    market_record = matches[0]
    market_validation = validate_market_data(market_record)
    if not market_validation.valid_for_trade:
        raise ValueError(
            "Execution market data validation failed: "
            + "; ".join(market_validation.errors)
        )
    _require_equal(
        recommendation.get("company_name"),
        market_record.company_name,
        "recommendation/market_data company_name",
    )
    if market_record.previous_close is None:
        raise ValueError("Market data previous_close is required for a trade record")
    _require_decimal_equal(
        recommendation.get("previous_high"),
        market_record.previous_high,
        "recommendation/market_data previous_high",
    )
    _require_decimal_equal(
        recommendation.get("tick_size"),
        market_record.tick_size,
        "recommendation/market_data tick_size",
    )

    actual_entry = to_decimal(execution["actual_entry"])
    actual_exit = to_decimal(execution["actual_exit"])
    if actual_entry <= 0 or actual_exit <= 0:
        raise ValueError("Actual execution prices must be greater than zero")
    if _time_key(execution["exit_time"]) < _time_key(execution["entry_time"]):
        raise ValueError("exit_time must not be earlier than entry_time")

    return {
        "trade_date": _text(execution["trade_date"]),
        "ticker": ticker,
        "company_name": _text(recommendation["company_name"]),
        "strategy_type": _text(recommendation["strategy_type"]),
        "previous_close": _text(market_record.previous_close),
        "previous_high": _text(recommendation["previous_high"]),
        "tick_size": _text(recommendation["tick_size"]),
        "entry_trigger": _text(recommendation["entry_trigger"]),
        "entry_limit": _text(recommendation["entry_limit"]),
        "planned_take_profit": _text(recommendation["take_profit"]),
        "planned_stop_loss": _text(recommendation["stop_loss"]),
        "actual_entry": _text(actual_entry),
        "actual_exit": _text(actual_exit),
        "shares": _text(execution["shares"]),
        "profit_loss_yen": _text(execution.get("profit_loss_yen")),
        "exit_reason": _text(execution["exit_reason"]),
        "entry_time": _canonical_time(execution["entry_time"]),
        "exit_time": _canonical_time(execution["exit_time"]),
        "strategy_version": _text(execution["strategy_version"]),
        "notes": _text(execution.get("notes")),
    }


def append_trade(
    row: dict[str, str],
    csv_path: str | Path = DEFAULT_TRADES_PATH,
) -> bool:
    path = Path(csv_path)
    existing_rows = load_trades(path)
    normalized = {name: _text(row.get(name)) for name in TRADE_COLUMNS}
    identity = _trade_identity(normalized)
    matching = [row for row in existing_rows if _trade_identity(row) == identity]
    if matching:
        if len(matching) == 1 and _normalized_row(matching[0]) == normalized:
            return False
        raise ValueError(
            "A conflicting trade record already exists for trade_date"
        )

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=TRADE_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows([*_normalized_rows(existing_rows), normalized])
    atomic_write_text(path, output.getvalue())
    return True


def _normalized_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    return [_normalized_row(row) for row in rows]


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    return {name: _text(row.get(name)) for name in TRADE_COLUMNS}


def _trade_identity(row: dict[str, Any]) -> str:
    return _text(row.get("trade_date"))


def _require_equal(left: Any, right: Any, label: str) -> None:
    if left != right:
        raise ValueError(f"{label} does not match")


def _require_decimal_equal(left: Any, right: Decimal | None, label: str) -> None:
    if right is None or to_decimal(left) != right:
        raise ValueError(f"{label} does not match")


def _time_key(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _canonical_time(value: str) -> str:
    hour, minute, second = _time_key(value)
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
