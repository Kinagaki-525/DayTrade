"""The single, shared JPX previous-trading-day resolver.

Both Yahoo and Kabutan history parsers need the exact date of the trading
day immediately before a given ``trading_date`` so they can select the
correct row for ``previous_close`` / ``previous_high`` -- never the nearest
earlier row a page happens to publish, and never a positional guess. This
module is the *only* place that logic lives, and it is deliberately split
in two:

* :func:`previous_trading_date` is a pure function of ``trading_date`` and a
  confirmed set of non-business dates. It excludes Saturdays and Sundays
  (computed, never guessed) and every date literally present in
  ``non_business_days`` -- nothing else.
* :func:`verified_previous_trading_date` is the Fail Closed wrapper: it only
  trusts ``non_business_days`` when it comes from a JPX_CALENDAR Source
  Attempt that is actually FOUND, whose stored raw page still hashes to what
  was recorded, and whose target_date/research_cutoff match the current run.
  Without that evidence it returns ``None`` rather than assume "no evidence
  of a holiday" means "it was a business day".
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.source_fetch import SourceFetchError, verify_source_page


class TradingCalendarError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


#: How many calendar days to walk backward before giving up. JPX never has a
#: gap this long between trading days; a longer gap means the calendar
#: evidence itself is wrong, which is a hard error, not a wider search.
_MAX_LOOKBACK_DAYS = 30


def previous_trading_date(trading_date: str, non_business_days: Iterable[str]) -> str:
    """The JPX trading day immediately before ``trading_date``.

    Walks backward one calendar day at a time, skipping Saturdays, Sundays,
    and every date confirmed non-business by ``non_business_days``. Every
    excluded date is either a weekend (a date computation, not a guess) or
    literally present in the confirmed calendar evidence -- there is no
    third, inferred, category.
    """
    confirmed = set(non_business_days)
    cursor = date.fromisoformat(trading_date)
    for _ in range(_MAX_LOOKBACK_DAYS):
        cursor -= timedelta(days=1)
        if cursor.weekday() >= 5:  # Saturday=5, Sunday=6
            continue
        if cursor.isoformat() in confirmed:
            continue
        return cursor.isoformat()
    raise TradingCalendarError(
        "PREVIOUS_TRADING_DATE_UNRESOLVED",
        f"no previous trading date resolved within {_MAX_LOOKBACK_DAYS} days "
        f"before {trading_date}",
    )


def verified_non_business_days(
    ledger: dict[str, Any] | None,
    *,
    run_dir: Path,
    target_date: str,
    research_cutoff: str,
) -> set[str] | None:
    """The confirmed JPX_CALENDAR non-business days for this exact run tuple.

    Returns ``None`` (Fail Closed) unless a JPX_CALENDAR Source Attempt in
    ``ledger`` is FOUND for this ``target_date``/``research_cutoff``, its
    stored raw page still hashes to what was recorded at acquisition time,
    and it actually carries a parsed ``non_business_days`` value. A missing,
    unparsed, tampered, or wrong-run-tuple calendar is exactly the same as
    "unavailable": previous_close/previous_high must not be derived from a
    guess about which days were trading days.
    """
    if not ledger:
        return None
    attempt = next(
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
    if attempt is None:
        return None
    source_page_path = attempt.get("source_page_path")
    source_page_sha256 = attempt.get("source_page_sha256")
    if not source_page_path or not source_page_sha256:
        return None
    try:
        verify_source_page(run_dir, str(source_page_path), str(source_page_sha256))
    except SourceFetchError:
        # Tampered or missing raw evidence: fail closed, never fall back to
        # trusting the unverified parsed value alone.
        return None
    for value in attempt.get("values") or []:
        if isinstance(value, dict) and value.get("field_name") == "non_business_days":
            raw = value.get("value")
            if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
                return set(raw)
    return None


def verified_previous_trading_date(
    ledger: dict[str, Any] | None,
    *,
    run_dir: Path,
    trading_date: str,
    target_date: str,
    research_cutoff: str,
) -> str | None:
    """The Fail Closed, evidence-backed previous JPX trading date, or None."""
    non_business_days = verified_non_business_days(
        ledger,
        run_dir=run_dir,
        target_date=target_date,
        research_cutoff=research_cutoff,
    )
    if non_business_days is None:
        return None
    try:
        return previous_trading_date(trading_date, non_business_days)
    except TradingCalendarError:
        return None


__all__ = [
    "TradingCalendarError",
    "previous_trading_date",
    "verified_non_business_days",
    "verified_previous_trading_date",
]
