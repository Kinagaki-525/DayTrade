from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.contracts import load_json_document
from src.market import validate_source_ledger
from src.research import validate_market_research


@dataclass(frozen=True)
class ResolvedResearchWindow:
    target_date: str
    previous_trading_day: str
    research_cutoff: str
    post_cutoff_information_status: str
    research_window: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "target_date": self.target_date,
            "previous_trading_day": self.previous_trading_day,
            "research_cutoff": self.research_cutoff,
            "post_cutoff_information_status": self.post_cutoff_information_status,
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
    post_cutoff_status = _post_cutoff_information_status(
        target_date,
        previous_trading_day,
    )
    selected = _select_previous_run(runs_dir, target_date, source_matrix)
    if selected is None:
        window_start = cutoff - timedelta(days=lookback_days)
        return ResolvedResearchWindow(
            target_date=target_date,
            previous_trading_day=previous_trading_day,
            research_cutoff=cutoff.isoformat(),
            post_cutoff_information_status=post_cutoff_status,
            research_window={
                "run_type": "FIRST_RUN",
                "window_start": window_start.isoformat(),
                "window_end": cutoff.isoformat(),
                "previous_research_cutoff": None,
                "previous_run_date": None,
                "bootstrap_lookback_days": lookback_days,
            },
        )

    previous_run, payload = selected
    previous_cutoff = _parse_datetime(payload["research_cutoff"], "research_cutoff")
    return ResolvedResearchWindow(
        target_date=target_date,
        previous_trading_day=previous_trading_day,
        research_cutoff=cutoff.isoformat(),
        post_cutoff_information_status=post_cutoff_status,
        research_window={
            "run_type": "NORMAL_RUN",
            "window_start": previous_cutoff.isoformat(),
            "window_end": cutoff.isoformat(),
            "previous_research_cutoff": previous_cutoff.isoformat(),
            "previous_run_date": previous_run.name,
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


def _post_cutoff_information_status(
    target_date: str,
    previous_trading_day: str,
) -> str:
    target = _parse_date(target_date, "target_date")
    previous = _parse_date(previous_trading_day, "previous_trading_day")
    if (target - previous).days > 1:
        return "OUT_OF_SCOPE"
    return "NO_NON_BUSINESS_GAP"


def _bootstrap_lookback_days(source_matrix: dict[str, Any]) -> int:
    try:
        lookback_days = int(source_matrix["tdnet_bootstrap_lookback_days"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("tdnet_bootstrap_lookback_days must be 1") from exc
    if lookback_days != 1:
        raise ValueError("tdnet_bootstrap_lookback_days must be 1")
    return lookback_days


def _previous_run_candidates(runs_dir: Path, target_date: str) -> list[Path]:
    """Every prior run directory that could supply a research cutoff, newest first.

    Only ``YYYY-MM-DD`` *directories* strictly older than ``target_date`` are
    candidates. A non-date child, a file, and the target date itself are not
    history and are ignored rather than rejected -- ``runs/`` legitimately holds
    a README, and the target run's own directory usually exists by now.
    """
    if not runs_dir.exists():
        return []
    candidates: list[tuple[date, Path]] = []
    target = _parse_date(target_date, "target_date")
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            run_date = _parse_date(child.name, f"run directory {child.name}")
        except ValueError:
            continue
        if run_date >= target:
            continue
        candidates.append((run_date, child))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [run_dir for _, run_dir in candidates]


def _select_previous_run(
    runs_dir: Path,
    target_date: str,
    source_matrix: dict[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    """The newest prior run whose research is complete enough to continue from.

    The newest directory is not automatically the answer. A run can stop before
    it ever wrote ``market_research.json``, and a run that finished writing one
    can still have ended ``DISCOVERY_INCOMPLETE`` -- neither establishes a
    research cutoff, so neither may become the window start. Both are stepped
    over in favour of an older *complete* run.

    What is never stepped over is corruption. A history entry that exists but
    fails its canonical validation is a Fail-Closed error, not a reason to
    silently reach further back: falling through to an older run there would
    turn a tampered artifact into a successful resolve. Nothing in this scan
    writes, repairs, or rewrites history.

    Returns ``(run_dir, market_research payload)``, or ``None`` when no eligible
    history exists (a FIRST_RUN).
    """
    for run_dir in _previous_run_candidates(runs_dir, target_date):
        payload = _eligible_previous_market_research(run_dir, source_matrix)
        if payload is not None:
            return run_dir, payload
    return None


def _eligible_previous_market_research(
    run_dir: Path,
    source_matrix: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate one history entry. ``None`` means "valid but not eligible".

    The absence of ``market_research.json`` is the *only* way a directory stops
    being history. Once that file exists the run is existing historical
    evidence, and every check from there is Fail-Closed: the Trust Chain is
    re-verified with this repository's own canonical validators, and a failure
    raises rather than falling through to an older run. Falling through on a
    failure would let a tampered artifact be laundered into a successful
    resolve simply by also corrupting the run that produced it.
    """
    research_path = run_dir / "market_research.json"
    if not research_path.exists():
        # A run that never got as far as writing its research is not history.
        return None

    try:
        payload = load_json_document(research_path, "market_research.schema.json")
    except ValueError as exc:
        raise ValueError(
            f"HISTORY_INVALID: previous run {run_dir.name} has invalid "
            f"market_research.json: {exc}"
        ) from exc

    # The research is only trustworthy together with the Source Ledger it cites,
    # so an absent ledger is an error rather than an absence of history.
    sources_path = run_dir / "sources.json"
    if not sources_path.exists():
        raise ValueError(
            f"HISTORY_INVALID: previous run {run_dir.name} lacks the sources.json "
            "its market_research.json must be validated against"
        )
    try:
        source_payload = load_json_document(sources_path, "sources.schema.json")
    except ValueError as exc:
        raise ValueError(
            f"HISTORY_INVALID: previous run {run_dir.name} has invalid "
            f"sources.json: {exc}"
        ) from exc

    for label, actual in (
        ("market_research.json", payload.get("target_date")),
        ("sources.json", source_payload.get("target_date")),
    ):
        if actual != run_dir.name:
            raise ValueError(
                f"HISTORY_INVALID: previous run {run_dir.name} {label} target_date "
                f"{actual!r} does not match its directory"
            )

    # Canonical Source Ledger verification -- the same function the acquisition
    # and validation CLIs use, not a history-specific reimplementation. Passing
    # no market_data records skips only the market_data-to-ledger membership
    # check, which has no meaning here; Attempt Value integrity, source page
    # paths and the source page SHA256 verification all still run, anchored at
    # this run's directory.
    ledger = validate_source_ledger(
        run_dir.name,
        (),
        source_payload,
        source_matrix,
        run_dir,
    )
    if not ledger.valid:
        raise ValueError(
            f"HISTORY_INVALID: previous run {run_dir.name} Source Ledger failed "
            "verification: " + "; ".join(ledger.errors)
        )

    validation = validate_market_research(payload, source_matrix, source_payload)
    if not validation.valid:
        raise ValueError(
            f"HISTORY_INVALID: previous run {run_dir.name} market_research "
            "failed validation: "
            + "; ".join(validation.errors)
        )

    # Eligibility is decided only after the whole Trust Chain has passed. A run
    # that is trustworthy but never completed established no research cutoff,
    # so the scan looks further back instead of inheriting an incomplete window.
    if not validation.discovery_complete or payload.get("overall_status") != "COMPLETE":
        return None
    return payload


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
