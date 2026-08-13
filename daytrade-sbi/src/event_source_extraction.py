"""Merge Event AI Classification output into the Source Ledger.

The AI classification step may only read **already fetched, local** raw pages
for four event source ids, and may only write the temporary working artifact
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
  disk.

Anything else is rejected and nothing is written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.contracts import validate_json_document
from src.source_acquisition import merge_ledger, write_ledger
from src.source_fetch import SourceFetchError, verify_source_page
from src.source_matrix import AI_CLASSIFICATION_SOURCE_IDS


EVENT_FIELD_NAMES = ("event_type", "event_date")


class EventExtractionMergeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def validate_extraction_document(payload: dict[str, Any]) -> None:
    validate_json_document(payload, "event_source_extraction.schema.json")


def build_merge_values(
    extraction: dict[str, Any],
    ledger: dict[str, Any],
    run_dir: Path,
) -> list[dict[str, Any]]:
    """Revalidate the extraction and return the ledger value records."""
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

    values: list[dict[str, Any]] = []
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

        for field_name in EVENT_FIELD_NAMES:
            value = item[field_name]
            if value is None:
                continue
            values.append(
                {
                    "source_ref": f"{attempt_id}#{field_name}",
                    "source_id": item["source_id"],
                    "source_role": attempt["source_role"],
                    "information_type": attempt["information_type"],
                    "source_status": "FOUND",
                    "source_name": _source_name(attempt, ledger),
                    "source_url": attempt["url"],
                    "retrieved_at": attempt["retrieved_at"],
                    # The Market Data trading_date, never the event_date: the
                    # two are deliberately separate fields.
                    "trading_date": item["trading_date"],
                    "ticker": item["ticker"],
                    "field_name": field_name,
                    "value": value,
                }
            )
    return values


def merge_event_source_extraction(
    *,
    extraction: dict[str, Any],
    ledger: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    values = build_merge_values(extraction, ledger, run_dir)
    merged = merge_ledger(
        ledger,
        {
            "schema_version": ledger.get("schema_version", 3),
            "target_date": ledger["target_date"],
            "sources": values,
            "source_attempts": [],
        },
    )
    validate_json_document(merged, "sources.schema.json")
    return merged


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


def _source_name(attempt: dict[str, Any], ledger: dict[str, Any]) -> str:
    for value in ledger.get("sources", []):
        if value.get("source_id") == attempt.get("source_id"):
            return str(value.get("source_name"))
    return str(attempt.get("source_id"))
