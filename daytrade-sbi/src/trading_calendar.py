"""The single, shared JPX previous-trading-day resolver.

Both Yahoo and Kabutan history parsers need the exact date of the trading
day immediately before a given ``trading_date`` so they can select the
correct row for ``previous_close`` / ``previous_high`` -- never the nearest
earlier row a page happens to publish, and never a positional guess. This
module is the *only* place that logic lives, and it is deliberately split
in two:

* :func:`previous_trading_date` is a pure function of ``trading_date``, a
  confirmed set of non-business dates, and the set of years that evidence
  actually covers. It excludes Saturdays and Sundays (computed, never
  guessed) and every date literally present in ``non_business_days`` --
  nothing else -- and it refuses to treat *any* day as confirmed business
  unless that day's year is itself confirmed covered.
* :func:`verified_previous_trading_date` is the Fail Closed wrapper: it only
  trusts calendar evidence that comes from a JPX_CALENDAR Source Attempt
  that is actually FOUND, whose stored raw page still hashes to what was
  recorded, whose target_date/research_cutoff match the current run, and
  whose recorded Attempt Values reproduce exactly when that same stored page
  is re-parsed right now. Without all of that it returns ``None`` rather
  than assume "no evidence of a holiday" means "it was a business day".
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.source_fetch import SourceFetchError, verify_source_page
from src.source_parsers.base import ParseContext
from src.source_parsers.jpx import parse_calendar


class TradingCalendarError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


#: How many calendar days to walk backward before giving up. JPX never has a
#: gap this long between trading days; a longer gap means the calendar
#: evidence itself is wrong, which is a hard error, not a wider search.
_MAX_LOOKBACK_DAYS = 30


def previous_trading_date(
    trading_date: str,
    non_business_days: Iterable[str],
    covered_years: Iterable[str],
) -> str:
    """The JPX trading day immediately before ``trading_date``.

    Walks backward one calendar day at a time, skipping Saturdays, Sundays,
    and every date confirmed non-business by ``non_business_days``. Every
    excluded date is either a weekend (a date computation, not a guess) or
    literally present in the confirmed calendar evidence. Crucially, a day
    is only ever accepted as a confirmed *business* day (i.e. "not in
    non_business_days" is trusted) when its own year is in
    ``covered_years`` -- calendar evidence for 2026 alone says nothing about
    whether some day in 2025 was a holiday.
    """
    confirmed_holidays = set(non_business_days)
    confirmed_years = set(covered_years)
    cursor = date.fromisoformat(trading_date)
    for _ in range(_MAX_LOOKBACK_DAYS):
        cursor -= timedelta(days=1)
        if str(cursor.year) not in confirmed_years:
            raise TradingCalendarError(
                "CALENDAR_YEAR_NOT_COVERED",
                f"calendar evidence does not cover {cursor.year}, required to "
                f"resolve the previous trading date before {trading_date}",
            )
        if cursor.weekday() >= 5:  # Saturday=5, Sunday=6
            continue
        if cursor.isoformat() in confirmed_holidays:
            continue
        return cursor.isoformat()
    raise TradingCalendarError(
        "PREVIOUS_TRADING_DATE_UNRESOLVED",
        f"no previous trading date resolved within {_MAX_LOOKBACK_DAYS} days "
        f"before {trading_date}",
    )


def _calendar_attempt(
    ledger: dict[str, Any] | None,
    *,
    target_date: str,
    research_cutoff: str,
) -> dict[str, Any] | None:
    if not ledger:
        return None
    return next(
        (
            item
            for item in ledger.get("source_attempts", [])
            if isinstance(item, dict)
            and item.get("source_id") == "JPX_CALENDAR"
            and item.get("status") == "FOUND"
            and str(item.get("target_date") or "") == target_date
            and str(item.get("research_cutoff") or "") == research_cutoff
        ),
        None,
    )


def _string_list_value(values: list[Any], field_name: str) -> list[str] | None:
    for value in values:
        if isinstance(value, dict) and value.get("field_name") == field_name:
            raw = value.get("value")
            if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
                return list(raw)
    return None


def verified_calendar_evidence(
    ledger: dict[str, Any] | None,
    *,
    run_dir: Path,
    target_date: str,
    research_cutoff: str,
) -> tuple[set[str], set[str]] | None:
    """The confirmed ``(non_business_days, covered_years)`` for this run tuple.

    Returns ``None`` (Fail Closed) unless a JPX_CALENDAR Source Attempt in
    ``ledger`` is FOUND for this ``target_date``/``research_cutoff``, its
    stored raw page still hashes to what was recorded at acquisition time,
    it carries parsed ``non_business_days`` and ``calendar_covered_years``
    values, and -- re-verified here, not merely trusted -- re-parsing that
    exact stored page right now with the same deterministic parser reproduces
    those same two values. A stored Attempt Value that was tampered with
    after acquisition (raw page hash still valid, but the recorded values no
    longer match what that page actually parses to) is caught by this last
    check and is exactly as untrusted as a missing calendar.
    """
    attempt = _calendar_attempt(ledger, target_date=target_date, research_cutoff=research_cutoff)
    if attempt is None:
        return None
    source_page_path = attempt.get("source_page_path")
    source_page_sha256 = attempt.get("source_page_sha256")
    if not source_page_path or not source_page_sha256:
        return None
    try:
        stored = verify_source_page(run_dir, str(source_page_path), str(source_page_sha256))
    except SourceFetchError:
        # Tampered or missing raw evidence: fail closed, never fall back to
        # trusting the unverified parsed value alone.
        return None

    values = attempt.get("values") or []
    recorded_non_business_days = _string_list_value(values, "non_business_days")
    recorded_covered_years = _string_list_value(values, "calendar_covered_years")
    if recorded_non_business_days is None or recorded_covered_years is None:
        return None

    reparsed = parse_calendar(
        stored,
        {},
        ParseContext(
            source_id="JPX_CALENDAR",
            trading_date=str(attempt.get("target_date") or target_date),
            content_type=attempt.get("content_type"),
        ),
    )
    if reparsed.status != "FOUND":
        return None
    reparsed_non_business_days = None
    reparsed_covered_years = None
    for parsed_value in reparsed.values:
        if parsed_value.field_name == "non_business_days":
            reparsed_non_business_days = list(parsed_value.value)
        elif parsed_value.field_name == "calendar_covered_years":
            reparsed_covered_years = list(parsed_value.value)
    if (
        reparsed_non_business_days is None
        or reparsed_covered_years is None
        or sorted(reparsed_non_business_days) != sorted(recorded_non_business_days)
        or sorted(reparsed_covered_years) != sorted(recorded_covered_years)
    ):
        return None

    return set(recorded_non_business_days), set(recorded_covered_years)


def verified_previous_trading_date(
    ledger: dict[str, Any] | None,
    *,
    run_dir: Path,
    trading_date: str,
    target_date: str,
    research_cutoff: str,
) -> str | None:
    """The Fail Closed, evidence-backed previous JPX trading date, or None."""
    evidence = verified_calendar_evidence(
        ledger,
        run_dir=run_dir,
        target_date=target_date,
        research_cutoff=research_cutoff,
    )
    if evidence is None:
        return None
    non_business_days, covered_years = evidence
    try:
        return previous_trading_date(trading_date, non_business_days, covered_years)
    except TradingCalendarError:
        return None


__all__ = [
    "TradingCalendarError",
    "previous_trading_date",
    "verified_calendar_evidence",
    "verified_previous_trading_date",
]
