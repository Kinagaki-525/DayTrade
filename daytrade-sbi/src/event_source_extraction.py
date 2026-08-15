"""Merge Event AI Classification output into the Source Ledger.

The AI classification step may only read **already fetched, local** raw pages
for the four AI-classifiable event source ids, and may only write the
temporary working artifact
``runs/<date>/working/event_source_extraction.json``. It never edits
``sources.json``.

This module is the one door into the ledger. Every extraction is revalidated
here against the ledger and the stored raw evidence:

* the referenced ``source_attempt_id`` must exist and be a FOUND attempt,
* ``source_id`` must be one of the four AI-classifiable event sources and must
  match the attempt,
* ``ticker`` and ``trading_date`` must match the attempt (no cross
  contamination, no date drift),
* ``source_page_sha256`` must match both the attempt and the bytes still on
  disk,
* every Event Object must be structurally valid against the shape the
  **existing** Event Gate reads.

The validated Event Objects are written into ``source_attempts[].values`` --
the exact place ``src.event_gate`` looks -- and the attempt's
``coverage_status`` is set, because the gate refuses to issue a PASS from a
page whose coverage is not COMPLETE.

The staging schema is *not* a second Event Research contract: news
classifications staged here are copied into the existing
``event_research.json`` contract, field for field, by
:mod:`src.event_research_completion`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.contracts import validate_json_document
from src.event_objects import validate_event_object
from src.source_acquisition import merge_ledger, write_ledger
from src.source_fetch import SourceFetchError, verify_source_page
from src.source_matrix import AI_CLASSIFICATION_SOURCE_IDS


class EventExtractionMergeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def validate_extraction_document(payload: dict[str, Any]) -> None:
    validate_json_document(payload, "event_source_extraction.schema.json")


def build_attempt_updates(
    extraction: dict[str, Any],
    ledger: dict[str, Any],
    run_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Revalidate the extraction; return ``attempt_id -> attempt patch``."""
    validate_extraction_document(extraction)

    if extraction["target_date"] != ledger.get("target_date"):
        raise EventExtractionMergeError(
            "EVENT_EXTRACTION_TARGET_DATE_MISMATCH",
            "extraction target_date does not match sources.json target_date",
        )

    attempts = {
        attempt["attempt_id"]: attempt
        for attempt in ledger.get("source_attempts", [])
        if isinstance(attempt, dict)
    }

    updates: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for index, item in enumerate(extraction["extractions"]):
        prefix = f"extractions[{index}]"
        attempt_id = item["source_attempt_id"]
        attempt = attempts.get(attempt_id)
        if attempt is None:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_ATTEMPT_UNKNOWN",
                f"{prefix}: source_attempt_id {attempt_id} is not in sources.json",
            )
        if attempt.get("status") != "FOUND":
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_ATTEMPT_NOT_FOUND",
                f"{prefix}: attempt {attempt_id} is not a FOUND attempt",
            )
        if item["source_id"] not in AI_CLASSIFICATION_SOURCE_IDS:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_SOURCE_NOT_CLASSIFIABLE",
                f"{prefix}: {item['source_id']} is not an AI-classifiable event source",
            )
        if attempt.get("source_id") != item["source_id"]:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_SOURCE_ID_MISMATCH",
                f"{prefix}: source_id does not match attempt {attempt_id}",
            )
        if attempt.get("candidate_code") != item["ticker"]:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_TICKER_MISMATCH",
                f"{prefix}: ticker does not match attempt {attempt_id}",
            )
        if attempt.get("target_date") != item["trading_date"]:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_TRADING_DATE_MISMATCH",
                f"{prefix}: trading_date does not match attempt {attempt_id}",
            )
        if attempt.get("source_page_sha256") != item["source_page_sha256"]:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_HASH_MISMATCH",
                f"{prefix}: source_page_sha256 does not match attempt {attempt_id}",
            )
        try:
            verify_source_page(
                run_dir,
                str(attempt["source_page_path"]),
                str(attempt["source_page_sha256"]),
            )
        except SourceFetchError as exc:
            raise EventExtractionMergeError(exc.code, f"{prefix}: {exc.message}") from exc

        if attempt_id in seen:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_DUPLICATE_ATTEMPT",
                f"{prefix}: duplicate extraction for attempt {attempt_id}",
            )
        seen.add(attempt_id)

        errors: list[str] = []
        evidence_ids: set[str] = set()
        for event_index, event in enumerate(item["events"]):
            errors.extend(
                validate_event_object(event, f"{prefix}.events[{event_index}]")
            )
            evidence_ids.add(str(event.get("evidence_id")))
        if len(evidence_ids) != len(item["events"]):
            errors.append(f"{prefix}: duplicate evidence_id within one extraction")
        if errors:
            raise EventExtractionMergeError(
                "EVENT_EXTRACTION_EVENT_OBJECT_INVALID", "; ".join(errors)
            )

        # Every staged news classification must point at an Event Object this
        # same extraction actually produced: a classification of evidence that
        # does not exist can never become a gate decision.
        for classification in item.get("news_classifications", []):
            if classification["news_evidence_id"] not in evidence_ids:
                raise EventExtractionMergeError(
                    "EVENT_EXTRACTION_NEWS_EVIDENCE_UNKNOWN",
                    f"{prefix}: news_evidence_id "
                    f"{classification['news_evidence_id']} is not among this "
                    "extraction's events",
                )

        updates[attempt_id] = {
            "values": _merged_values(attempt, item["events"]),
            "coverage_status": item["coverage_status"],
        }
    return updates


def _merged_values(attempt: dict[str, Any], events: list[dict[str, Any]]) -> list[Any]:
    """Deterministic parse values are kept; Event Objects are replaced.

    Re-running a merge replaces the attempt's previous Event Objects instead
    of accumulating duplicates, so the operation is idempotent.
    """
    existing = attempt.get("values")
    kept = [
        value
        for value in (existing if isinstance(existing, list) else [])
        if not (isinstance(value, dict) and "event_type" in value)
    ]
    return kept + list(events)


def merge_event_source_extraction(
    *,
    extraction: dict[str, Any],
    ledger: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    updates = build_attempt_updates(extraction, ledger, run_dir)

    patched_attempts: list[dict[str, Any]] = []
    for attempt in ledger.get("source_attempts", []):
        patch = updates.get(str(attempt.get("attempt_id")))
        if patch is None:
            patched_attempts.append(attempt)
            continue
        updated = dict(attempt)
        updated["values"] = patch["values"]
        updated["coverage_status"] = patch["coverage_status"]
        updated["result_count"] = len(patch["values"])
        patched_attempts.append(updated)

    merged = merge_ledger(
        ledger,
        {
            "schema_version": ledger.get("schema_version", 3),
            "target_date": ledger["target_date"],
            "sources": [],
            "source_attempts": patched_attempts,
        },
    )
    validate_json_document(merged, "sources.schema.json")
    return merged


def staged_news_classifications(
    extraction: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """``ticker -> news_classifications`` in the *existing* contract shape.

    ``source_id`` / ``source_attempt_id`` are filled in from the extraction
    itself, so the AI never gets to claim a provenance other than the attempt
    it actually read.
    """
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in extraction.get("extractions", []):
        if item["source_id"] not in {"YAHOO_JP_NEWS", "KABUTAN_NEWS"}:
            continue
        for classification in item.get("news_classifications", []):
            by_ticker.setdefault(item["ticker"], []).append(
                {
                    "news_evidence_id": classification["news_evidence_id"],
                    "source_id": item["source_id"],
                    "source_attempt_id": item["source_attempt_id"],
                    "published_at": classification["published_at"],
                    "signal_type": classification["signal_type"],
                    "subject_status": classification["subject_status"],
                    "confirmation_evidence_ids": list(
                        classification["confirmation_evidence_ids"]
                    ),
                    "confirmation_source_attempt_ids": list(
                        classification["confirmation_source_attempt_ids"]
                    ),
                }
            )
    return by_ticker


def merge_event_source_extraction_files(
    *,
    extraction_path: Path,
    sources_path: Path,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Atomic file-level merge: validate everything, then replace."""
    extraction = json.loads(Path(extraction_path).read_text(encoding="utf-8"))
    ledger = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    effective_run_dir = run_dir if run_dir is not None else Path(sources_path).parent
    merged = merge_event_source_extraction(
        extraction=extraction, ledger=ledger, run_dir=effective_run_dir
    )
    write_ledger(Path(sources_path), merged)
    return merged


__all__ = [
    "EventExtractionMergeError",
    "build_attempt_updates",
    "merge_event_source_extraction",
    "merge_event_source_extraction_files",
    "staged_news_classifications",
    "validate_extraction_document",
]
