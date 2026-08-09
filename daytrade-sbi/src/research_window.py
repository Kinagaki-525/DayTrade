from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.contracts import load_json_document
from src.research import validate_market_research
from src.source_matrix import source_by_id


@dataclass(frozen=True)
class ResolvedResearchWindow:
    target_date: str
    previous_trading_day: str
    research_cutoff: str
    research_window: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "target_date": self.target_date,
            "previous_trading_day": self.previous_trading_day,
            "research_cutoff": self.research_cutoff,
            "research_window": self.research_window,
        }


def resolve_research_window(
    *,
    target_date: str,
    previous_trading_day: str,
    runs_dir: Path,
    source_matrix: dict[str, Any],
) -> ResolvedResearchWindow:
    _validate_trading_date_order(target_date, previous_trading_day)
    lookback_days = _bootstrap_lookback_days(source_matrix)
    cutoff = _research_cutoff(previous_trading_day, source_matrix)
    latest_run = _latest_previous_run(runs_dir, target_date)
    if latest_run is None:
        window_start = cutoff - timedelta(days=lookback_days)
        return ResolvedResearchWindow(
            target_date=target_date,
            previous_trading_day=previous_trading_day,
            research_cutoff=cutoff.isoformat(),
            research_window={
                "run_type": "FIRST_RUN",
                "window_start": window_start.isoformat(),
                "window_end": cutoff.isoformat(),
                "previous_research_cutoff": None,
                "previous_run_date": None,
                "bootstrap_lookback_days": lookback_days,
            },
        )

    payload = _load_valid_previous_market_research(latest_run, source_matrix)
    previous_cutoff = _parse_datetime(payload["research_cutoff"], "research_cutoff")
    return ResolvedResearchWindow(
        target_date=target_date,
        previous_trading_day=previous_trading_day,
        research_cutoff=cutoff.isoformat(),
        research_window={
            "run_type": "NORMAL_RUN",
            "window_start": previous_cutoff.isoformat(),
            "window_end": cutoff.isoformat(),
            "previous_research_cutoff": previous_cutoff.isoformat(),
            "previous_run_date": latest_run.name,
            "bootstrap_lookback_days": None,
        },
    )


def _validate_trading_date_order(target_date: str, previous_trading_day: str) -> None:
    target = _parse_date(target_date, "target_date")
    previous = _parse_date(previous_trading_day, "previous_trading_day")
    if previous >= target:
        raise ValueError("previous_trading_day must be before target_date")


def _research_cutoff(
    previous_trading_day: str,
    source_matrix: dict[str, Any],
) -> datetime:
    _parse_date(previous_trading_day, "previous_trading_day")
    cutoff_time = source_matrix["default_research_cutoff_time"]
    return _parse_datetime(
        f"{previous_trading_day}T{cutoff_time}",
        "current research_cutoff",
    )


def _bootstrap_lookback_days(source_matrix: dict[str, Any]) -> int:
    try:
        lookback_days = int(source_matrix["tdnet_bootstrap_lookback_days"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("tdnet_bootstrap_lookback_days must be 1") from exc
    if lookback_days != 1:
        raise ValueError("tdnet_bootstrap_lookback_days must be 1")
    return lookback_days


def _latest_previous_run(runs_dir: Path, target_date: str) -> Path | None:
    target = _parse_date(target_date, "target_date")
    candidates: list[tuple[date, Path]] = []
    if not runs_dir.exists():
        return None
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            run_date = _parse_date(child.name, f"run directory {child.name}")
        except ValueError:
            continue
        if run_date < target:
            candidates.append((run_date, child))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _load_valid_previous_market_research(
    run_dir: Path,
    source_matrix: dict[str, Any],
) -> dict[str, Any]:
    research_path = run_dir / "market_research.json"
    if not research_path.exists():
        raise ValueError(
            f"HISTORY_INVALID: latest previous run {run_dir.name} lacks market_research.json"
        )
    try:
        payload = load_json_document(research_path, "market_research.schema.json")
    except ValueError as exc:
        raise ValueError(
            f"HISTORY_INVALID: latest previous run {run_dir.name} has invalid "
            f"market_research.json: {exc}"
        ) from exc

    if payload.get("target_date") != run_dir.name:
        raise ValueError(
            f"HISTORY_INVALID: latest previous run {run_dir.name} target_date does not "
            "match its directory"
        )
    validation = validate_market_research(payload, source_matrix)
    if not validation.valid:
        raise ValueError(
            f"HISTORY_INVALID: latest previous run {run_dir.name} market_research "
            "failed validation: "
            + "; ".join(validation.errors)
        )
    if not _tdnet_discovery_confirmed(payload, source_matrix):
        raise ValueError(
            f"HISTORY_INVALID: latest previous run {run_dir.name} did not confirm "
            "JPX_TDNET discovery"
        )
    return payload


def _tdnet_discovery_confirmed(
    payload: dict[str, Any],
    source_matrix: dict[str, Any],
) -> bool:
    definitions = source_by_id(source_matrix)
    tdnet = definitions.get("JPX_TDNET")
    if tdnet is None or tdnet.get("criticality") != "DISCOVERY_CRITICAL":
        return False
    for route in payload["discovery"]:
        if (
            route["discovery_type"] == "TIMELY_DISCLOSURE"
            and route["source_id"] == "JPX_TDNET"
        ):
            return route["status"] == "FOUND"
    return False


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date-time") from exc
