"""Physical Network Request Records: crash-safe, deterministic Request Budget.

A **Physical Request** (this module) and a **Logical Attempt**
(:mod:`src.source_acquisition`) are different things. The same physical GET
of a globally-shared page is consumed by many logical attempts (one per
candidate); conversely, a single logical attempt corresponds to at most one
physical GET, ever, for a given run.

Every physical request this run has ever *started* -- not merely finished --
is written to ``runs/<target_date>/network_requests/<request_id>.json``
**before** the transport is invoked (:func:`reserve_request`), and is
updated in place to ``COMPLETED`` afterward (:func:`complete_request`). This
is what makes the Request Budget crash-safe: if the process dies between
those two writes, the next run sees a ``RESERVED`` record and refuses to
retry blindly (:data:`RequestBudgetError` ``REQUEST_BUDGET_STATE_INDETERMINATE``)
rather than either silently re-requesting (breaking the one-GET guarantee)
or silently trusting an unknown outcome.

``request_id`` is a pure function of ``(url, target_date, research_cutoff)``
-- computed here once, in :func:`request_id_for`, and never recomputed with
different logic anywhere else in the codebase.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.contracts import validate_json_document
from src.file_io import atomic_write_text

NETWORK_REQUESTS_DIRNAME = "network_requests"
NETWORK_REQUEST_SCHEMA_VERSION = 1
NETWORK_REQUEST_SCHEMA_NAME = "network_request.schema.json"

REQUEST_ID_PATTERN_LENGTH = 32


class RequestBudgetError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_id_for(*, url: str, target_date: str, research_cutoff: str) -> str:
    """The one, deterministic identity of a Physical Network Request.

    Identity is exactly ``(url, target_date, research_cutoff)`` -- the same
    tuple :func:`src.source_acquisition.attempt_id_for` folds ``source_id``
    and ``candidate_code`` into for the *logical* attempt id. Two different
    logical attempts (different source_id/candidate_code) that happen to
    resolve to the same URL share this same physical request_id.
    """
    payload = "|".join([url, target_date, research_cutoff])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return "req-" + digest[:REQUEST_ID_PATTERN_LENGTH]


def network_request_path(run_dir: str | Path, request_id: str) -> Path:
    return Path(run_dir) / NETWORK_REQUESTS_DIRNAME / f"{request_id}.json"


def validate_request_record(record: dict[str, Any]) -> None:
    """Raises ``ValueError`` if ``record`` does not match the schema."""
    validate_json_document(record, NETWORK_REQUEST_SCHEMA_NAME)


def load_request_record(run_dir: str | Path, request_id: str) -> dict[str, Any] | None:
    path = network_request_path(run_dir, request_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_record(run_dir: str | Path, record: dict[str, Any]) -> None:
    validate_request_record(record)
    path = network_request_path(run_dir, str(record["request_id"]))
    atomic_write_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


@dataclass(frozen=True)
class ReservationOutcome:
    """What :func:`reserve_request` found or created.

    ``record`` is always a ``COMPLETED`` or freshly-``RESERVED`` record.
    ``already_completed`` tells the caller whether it must still invoke the
    transport (``False``) or may reuse ``record`` as-is (``True``).
    """

    record: dict[str, Any]
    already_completed: bool


def reserve_request(
    run_dir: str | Path,
    *,
    url: str,
    target_date: str,
    research_cutoff: str,
    origin_source_id: str,
    origin_candidate_code: str | None,
    origin_attempt_id: str,
) -> ReservationOutcome:
    """Reserve (or reuse) the Physical Request for this exact tuple.

    Must be called, and its result obeyed, **before** the transport is ever
    invoked for this tuple. Three outcomes:

    * No record exists yet -> a new ``RESERVED`` record is written
      atomically and returned (``already_completed=False``): the caller must
      now run the transport and call :func:`complete_request`.
    * A ``COMPLETED`` record already exists -> returned as-is
      (``already_completed=True``): the caller must not touch the transport.
    * A ``RESERVED`` record already exists (a prior process reserved this
      exact request and never completed it -- most likely a crash) -> hard
      error. There is no retry: the state is indeterminate, and guessing
      would risk either a silent duplicate GET or silently trusting an
      outcome that never happened.
    """
    request_id = request_id_for(url=url, target_date=target_date, research_cutoff=research_cutoff)
    existing = load_request_record(run_dir, request_id)
    if existing is not None:
        if existing.get("state") == "COMPLETED":
            return ReservationOutcome(record=existing, already_completed=True)
        raise RequestBudgetError(
            "REQUEST_BUDGET_STATE_INDETERMINATE",
            f"request {request_id} for {url} is RESERVED but never COMPLETED "
            "(a prior run likely crashed mid-request); refusing to retry",
        )

    record = {
        "schema_version": NETWORK_REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "url": url,
        "target_date": target_date,
        "research_cutoff": research_cutoff,
        "state": "RESERVED",
        "reserved_at": utc_now_iso(),
        "completed_at": None,
        "origin_source_id": origin_source_id,
        "origin_candidate_code": origin_candidate_code,
        "origin_attempt_id": origin_attempt_id,
        "source_status": None,
        "http_status": None,
        "content_type": None,
        "transport_exit_code": None,
        "source_page_path": None,
        "source_page_sha256": None,
        "source_page_size_bytes": None,
    }
    _write_record(run_dir, record)
    return ReservationOutcome(record=record, already_completed=False)


def complete_request(
    run_dir: str | Path,
    request_id: str,
    *,
    source_status: str,
    http_status: int | None,
    content_type: str | None,
    transport_exit_code: int | None,
    source_page_path: str | None,
    source_page_sha256: str | None,
    source_page_size_bytes: int | None,
) -> dict[str, Any]:
    """Mark a ``RESERVED`` request ``COMPLETED``, success or failure alike.

    Must be called exactly once per reservation, whatever the transport
    outcome was (200, 403, timeout, execution failure...): a Physical
    Request is "used" the moment the transport was invoked, not only when it
    happens to succeed.
    """
    existing = load_request_record(run_dir, request_id)
    if existing is None:
        raise RequestBudgetError(
            "REQUEST_RECORD_MISSING",
            f"cannot complete request {request_id}: no RESERVED record found",
        )
    if existing.get("state") == "COMPLETED":
        raise RequestBudgetError(
            "REQUEST_ALREADY_COMPLETED",
            f"request {request_id} is already COMPLETED; completion is not idempotent",
        )

    record = dict(existing)
    record.update(
        {
            "state": "COMPLETED",
            "completed_at": utc_now_iso(),
            "source_status": source_status,
            "http_status": http_status,
            "content_type": content_type,
            "transport_exit_code": transport_exit_code,
            "source_page_path": source_page_path,
            "source_page_sha256": source_page_sha256,
            "source_page_size_bytes": source_page_size_bytes,
        }
    )
    _write_record(run_dir, record)
    return record


def list_request_records(run_dir: str | Path) -> list[dict[str, Any]]:
    """Every Physical Request Record for this run, in file order.

    This -- not ``source_attempts[].cache_status`` -- is the primary source
    of truth for "how many real network requests did this run make".
    """
    directory = Path(run_dir) / NETWORK_REQUESTS_DIRNAME
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if path.suffix != ".json" or not path.stem.startswith("req-"):
            continue
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


__all__ = [
    "NETWORK_REQUESTS_DIRNAME",
    "NETWORK_REQUEST_SCHEMA_NAME",
    "ReservationOutcome",
    "RequestBudgetError",
    "complete_request",
    "list_request_records",
    "load_request_record",
    "network_request_path",
    "request_id_for",
    "reserve_request",
    "utc_now_iso",
    "validate_request_record",
]
