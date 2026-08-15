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

import errno
import hashlib
import json
import os
import re
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


def _parse_iso_datetime(value: str):
    """Timezone-aware ``datetime`` from an ISO-8601 string, or ``None``.

    ``None`` (never an exception) signals "unparsable" to callers, which
    treat it as an integrity violation -- an invalid or missing timezone is
    never silently coerced into "now" or compared as a naive string.
    """
    from datetime import datetime as _datetime

    try:
        parsed = _datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def load_request_record(run_dir: str | Path, request_id: str) -> dict[str, Any] | None:
    """Load, and fully verify, the Request Record named ``request_id``.

    A record is never handed back to a caller unverified: this is the sole
    gate through which a Physical Request Record may be reused. Every check
    below is a hard error (``REQUEST_RECORD_INTEGRITY_VIOLATION``) -- a
    record that fails any of them is not "probably fine", it is untrusted
    evidence and must not be reused, extended, or treated as authoritative.
    """
    path = network_request_path(run_dir, request_id)
    if not path.is_file():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record {path} could not be read: {exc}",
        ) from exc
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record {path} is not valid JSON: {exc}",
        ) from exc

    if not isinstance(record, dict):
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record {path} is not a JSON object",
        )

    try:
        validate_request_record(record)
    except ValueError as exc:
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record {path} failed schema validation: {exc}",
        ) from exc

    for field in ("url", "target_date", "research_cutoff", "request_id"):
        if field not in record or not isinstance(record[field], str):
            raise RequestBudgetError(
                "REQUEST_RECORD_INTEGRITY_VIOLATION",
                f"request record {path} is missing required field {field!r}",
            )

    recomputed_id = request_id_for(
        url=record["url"],
        target_date=record["target_date"],
        research_cutoff=record["research_cutoff"],
    )
    if recomputed_id != record["request_id"]:
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record {path} has request_id {record['request_id']!r} but "
            f"url+target_date+research_cutoff recompute to {recomputed_id!r}",
        )
    if record["request_id"] != request_id:
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record {path} has request_id {record['request_id']!r} but "
            f"filename implies {request_id!r}",
        )
    if path.stem != record["request_id"]:
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            f"request record file name {path.name!r} does not match its own "
            f"request_id {record['request_id']!r}",
        )

    # FIX-R2-002B section 5: a COMPLETED record's completed_at can never
    # precede its own reserved_at. Compared as timezone-aware datetimes, not
    # ISO strings -- an unparsable or naive timestamp is itself a violation.
    if record.get("state") == "COMPLETED":
        reserved_at = _parse_iso_datetime(str(record.get("reserved_at")))
        completed_at = _parse_iso_datetime(str(record.get("completed_at")))
        if reserved_at is None or completed_at is None:
            raise RequestBudgetError(
                "REQUEST_RECORD_INTEGRITY_VIOLATION",
                f"request record {path} has an unparsable reserved_at/completed_at "
                f"timestamp: {record.get('reserved_at')!r} / {record.get('completed_at')!r}",
            )
        if completed_at < reserved_at:
            raise RequestBudgetError(
                "REQUEST_RECORD_INTEGRITY_VIOLATION",
                f"request record {path} has completed_at {record.get('completed_at')!r} "
                f"before its own reserved_at {record.get('reserved_at')!r}",
            )

    return record


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


def _verify_reuse_arguments(
    record: dict[str, Any],
    *,
    url: str,
    target_date: str,
    research_cutoff: str,
) -> None:
    """A reused ``COMPLETED`` record must agree with the caller's own tuple.

    ``request_id`` matching alone is not proof: it is only proof once we
    know the record itself is not lying about the tuple it was reserved
    for, which is exactly what this cross-check exists to catch.
    """
    if (
        record.get("url") != url
        or record.get("target_date") != target_date
        or record.get("research_cutoff") != research_cutoff
    ):
        raise RequestBudgetError(
            "REQUEST_RECORD_INTEGRITY_VIOLATION",
            "request record tuple mismatch: caller requested "
            f"({url!r}, {target_date!r}, {research_cutoff!r}) but record "
            f"{record.get('request_id')!r} holds "
            f"({record.get('url')!r}, {record.get('target_date')!r}, "
            f"{record.get('research_cutoff')!r})",
        )


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
            _verify_reuse_arguments(
                existing, url=url, target_date=target_date, research_cutoff=research_cutoff
            )
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
    validate_request_record(record)
    path = network_request_path(run_dir, request_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Concurrency hardening: the RESERVED record is created with an atomic
    # exclusive open (O_CREAT|O_EXCL). Exactly one process can win this
    # race for a given request_id; the loser must never proceed to invoke
    # the transport itself, and must never wait-and-retry.
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        winner = load_request_record(run_dir, request_id)
        if winner is None:
            raise RequestBudgetError(
                "REQUEST_BUDGET_STATE_INDETERMINATE",
                f"request {request_id} lost the reservation race and no readable "
                "record was found afterward; refusing to retry",
            ) from exc
        if winner.get("state") == "COMPLETED":
            _verify_reuse_arguments(
                winner, url=url, target_date=target_date, research_cutoff=research_cutoff
            )
            return ReservationOutcome(record=winner, already_completed=True)
        raise RequestBudgetError(
            "REQUEST_BUDGET_STATE_INDETERMINATE",
            f"request {request_id} lost the reservation race to a concurrent "
            "process and is still RESERVED; refusing to retry or wait",
        ) from exc

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
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


_REQUEST_FILENAME_PATTERN = re.compile(r"^req-[0-9a-f]{32}\.json$")


def scan_network_requests_directory(
    run_dir: str | Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Strictly scan ``network_requests/`` (FIX-R2-002A section 6/20-22).

    Returns ``(records, violations)``. Only files named exactly
    ``req-[0-9a-f]{32}.json`` are permitted; anything else (a stray file, a
    hidden file, a subdirectory, a wrongly-named or malformed/tampered
    ``.json``) is reported as a violation string and **never** silently
    skipped -- this is what lets callers (production verification) turn a
    directory-integrity problem into an ``INVALID_RUN`` check failure
    instead of either crashing or quietly ignoring it.
    """
    directory = Path(run_dir) / NETWORK_REQUESTS_DIRNAME
    if not directory.exists():
        return [], []
    if not directory.is_dir():
        return [], [f"network_requests exists but is not a directory: {directory}"]

    records: list[dict[str, Any]] = []
    violations: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.is_dir():
            violations.append(f"unexpected subdirectory in network_requests/: {path.name}")
            continue
        if not _REQUEST_FILENAME_PATTERN.match(path.name):
            violations.append(f"unexpected file in network_requests/: {path.name}")
            continue
        request_id = path.stem
        try:
            record = load_request_record(run_dir, request_id)
        except RequestBudgetError as exc:
            violations.append(f"{path.name}: {exc.code}: {exc.message}")
            continue
        if record is None:  # pragma: no cover - defensive, path.is_file() just checked
            violations.append(f"{path.name}: request record vanished during scan")
            continue
        records.append(record)
    return records, violations


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
    "scan_network_requests_directory",
    "utc_now_iso",
    "validate_request_record",
]
