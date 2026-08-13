"""Event Objects: the shape the **existing** Event Gate already reads.

The Event Gate consumes ``source_attempts[].values`` and looks for dicts
carrying ``event_type`` / ``event_date`` / ``published_at`` / ``evidence_id``
(see ``src.event_gate._attempt_events`` and the earnings / TDnet rules). This
module is the single place those objects are constructed, so the acquisition
path and the AI-classification merge path can never drift into two different
vocabularies.

Deliberately **no new enum names**: ``event_type`` for an earnings event is
``EARNINGS`` -- exactly the literal the gate matches on -- not
``EARNINGS_ANNOUNCEMENT`` or anything else invented alongside it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


#: The literal the Event Gate earnings rules match on.
EARNINGS_EVENT_TYPE = "EARNINGS"

#: The literal used for a TDnet / issuer timely disclosure. The TDnet rule
#: matches on ``published_at`` being inside the event window, not on the
#: event_type string, but the value is fixed here so it stays stable.
DISCLOSURE_EVENT_TYPE = "TIMELY_DISCLOSURE"

EVENT_TYPES = frozenset(
    {
        EARNINGS_EVENT_TYPE,
        DISCLOSURE_EVENT_TYPE,
        "IR_DISCLOSURE",
        "NEWS",
    }
)

#: Source ids whose Event Objects are produced deterministically from the
#: parsed page. No AI participates for these.
DETERMINISTIC_EVENT_SOURCE_IDS = frozenset({"JPX_TDNET", "JPX_EARNINGS_SCHEDULE"})


def evidence_id(attempt_id: str, *parts: Any) -> str:
    """A stable, content-derived evidence id.

    Deterministic in the attempt plus the event's own identifying fields, so
    the same stored page always yields the same evidence ids and a merge can
    be re-run without inventing new identities.
    """
    payload = "|".join([str(attempt_id), *(str(part) for part in parts)])
    return "ev-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def parse_timestamp(value: Any) -> str | None:
    """Normalize a published timestamp to a timezone-aware ISO string.

    The Event Gate parses ``published_at`` strictly and rejects naive
    datetimes, so anything ambiguous is dropped here rather than passed on as
    a value the gate will reject at evaluation time.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _values_named(values: list[dict[str, Any]], field_name: str) -> Any:
    for value in values:
        if isinstance(value, dict) and value.get("field_name") == field_name:
            return value.get("value")
    return None


def deterministic_event_objects(
    source_id: str,
    attempt_id: str,
    parsed_values: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Event Objects for the deterministic event sources.

    JPX_EARNINGS_SCHEDULE -> ``EARNINGS`` events carrying ``event_date``.
    JPX_TDNET             -> ``TIMELY_DISCLOSURE`` events carrying
                             ``published_at`` for the event-window rule.
    """
    if source_id == "JPX_EARNINGS_SCHEDULE":
        events = []
        for entry in _values_named(parsed_values, "earnings_schedule_events") or []:
            event_date = entry.get("event_date")
            if not event_date:
                continue
            events.append(
                {
                    "evidence_id": evidence_id(attempt_id, "EARNINGS", event_date),
                    "event_type": EARNINGS_EVENT_TYPE,
                    "event_date": event_date,
                    "published_at": None,
                    "headline": entry.get("headline") or "",
                }
            )
        return events

    if source_id == "JPX_TDNET":
        events = []
        for entry in _values_named(parsed_values, "disclosure_entries") or []:
            published_at = parse_timestamp(entry.get("published"))
            if published_at is None:
                # A row whose timestamp cannot be read is not silently dated:
                # it contributes no event, and the attempt's coverage_status
                # is what tells the gate the page was only partially usable.
                continue
            headline = entry.get("headline") or ""
            events.append(
                {
                    "evidence_id": evidence_id(
                        attempt_id, DISCLOSURE_EVENT_TYPE, published_at, headline
                    ),
                    "event_type": DISCLOSURE_EVENT_TYPE,
                    "event_date": published_at[:10],
                    "published_at": published_at,
                    "headline": headline,
                }
            )
        return events

    return []


def deterministic_coverage_status(
    source_id: str,
    parsed_values: list[dict[str, Any]],
) -> str | None:
    """``COMPLETE`` when the deterministic parse covered the whole page.

    The Event Gate only issues a PASS when ``coverage_status == "COMPLETE"``,
    so a page we could only partially read stays DATA_UNAVAILABLE rather than
    quietly counting as "no event found".
    """
    if source_id == "JPX_EARNINGS_SCHEDULE":
        return "COMPLETE" if _values_named(
            parsed_values, "earnings_schedule_events"
        ) is not None else "PARTIAL"
    if source_id == "JPX_TDNET":
        entries = _values_named(parsed_values, "disclosure_entries")
        if entries is None:
            return "PARTIAL"
        unreadable = [
            entry
            for entry in entries
            if parse_timestamp(entry.get("published")) is None
        ]
        return "PARTIAL" if unreadable else "COMPLETE"
    return None


def validate_event_object(event: dict[str, Any], prefix: str) -> list[str]:
    """Structural check against the shape the Event Gate reads."""
    errors: list[str] = []
    if not isinstance(event, dict):
        return [f"{prefix}: event object must be an object"]
    if not str(event.get("evidence_id") or "").strip():
        errors.append(f"{prefix}: evidence_id is required")
    event_type = event.get("event_type")
    if event_type not in EVENT_TYPES:
        errors.append(f"{prefix}: unknown event_type {event_type!r}")
    published_at = event.get("published_at")
    if published_at is not None and parse_timestamp(published_at) is None:
        errors.append(
            f"{prefix}: published_at must be a timezone-aware ISO datetime"
        )
    if event_type == EARNINGS_EVENT_TYPE and not event.get("event_date"):
        errors.append(f"{prefix}: an EARNINGS event requires event_date")
    return errors


__all__ = [
    "DETERMINISTIC_EVENT_SOURCE_IDS",
    "DISCLOSURE_EVENT_TYPE",
    "EARNINGS_EVENT_TYPE",
    "EVENT_TYPES",
    "deterministic_coverage_status",
    "deterministic_event_objects",
    "evidence_id",
    "parse_timestamp",
    "validate_event_object",
]
