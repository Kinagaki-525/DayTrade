from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


DEFAULT_RECOMMENDATIONS_PATH = (
    Path(__file__).resolve().parents[1] / "trades" / "recommendations.csv"
)
RECOMMENDATION_COLUMNS = (
    "target_date",
    "strategy_version",
    "config_sha256",
    "ticker",
    "decision",
    "strategy_type",
    "entry_trigger",
    "entry_limit",
    "take_profit",
    "stop_loss",
    "risk_result",
    "order_submitted",
    "entry_triggered",
    "entry_filled",
    "notes",
)


def recommendation_to_row(
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> dict[str, str]:
    return {
        "target_date": _text(recommendation.get("target_date")),
        "strategy_version": _text(recommendation.get("strategy_version")),
        "config_sha256": _text(recommendation.get("config_sha256")),
        "ticker": _text(recommendation.get("ticker")),
        "decision": _text(recommendation.get("decision")),
        "strategy_type": _text(recommendation.get("strategy_type")),
        "entry_trigger": _text(recommendation.get("entry_trigger")),
        "entry_limit": _text(recommendation.get("entry_limit")),
        "take_profit": _text(recommendation.get("take_profit")),
        "stop_loss": _text(recommendation.get("stop_loss")),
        "risk_result": _text(risk_result.get("status")),
        "order_submitted": _text(recommendation.get("order_submitted")),
        "entry_triggered": _text(recommendation.get("entry_triggered")),
        "entry_filled": _text(recommendation.get("entry_filled")),
        "notes": _text(recommendation.get("notes")),
    }


def append_recommendation(
    row: dict[str, str],
    csv_path: str | Path = DEFAULT_RECOMMENDATIONS_PATH,
) -> None:
    path = Path(csv_path)
    file_exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if file_exists:
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file)
            header = next(reader, None)
        if header != list(RECOMMENDATION_COLUMNS):
            raise ValueError("recommendations.csv header does not match v2 schema")
    with path.open("a", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=RECOMMENDATION_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in RECOMMENDATION_COLUMNS})


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value).strip()
