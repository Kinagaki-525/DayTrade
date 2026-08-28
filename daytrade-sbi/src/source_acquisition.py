"""Stage-aware deterministic Source Acquisition.

One engine, five stages (Discovery / Stage1 / Stage2 / Turnover / Event).
Every stage:

1. resolves its URL from ``config/source_matrix.yaml`` (never from a page,
   never from an agent),
2. verifies the ``source_id -> parser`` binding **before** any network call,
3. fetches at most one GET per **Physical Request** -- identified by
   ``request_id = f(url, target_date, research_cutoff)`` and recorded in
   ``runs/<target_date>/network_requests/<request_id>.json``, which is the
   Request Budget SSOT -- through :mod:`src.source_fetch`. One Physical
   Request can satisfy N Logical Attempts (a shared page such as the TDnet
   index), so the Logical ``attempt_id`` is *not* the Request Budget key,
4. stores the unmodified raw bytes with their SHA256,
5. parses them with the registered deterministic parser,
6. writes a Source Ledger v3 attempt.

No AI component participates in any step. The agent's only role is invoking
the CLI.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.event_objects import (
    deterministic_coverage_status,
    deterministic_event_objects,
)
from src.file_io import atomic_write_text
from src.network_policy import (
    NetworkPolicyError,
    approved_issuer_hosts,
    load_issuer_domain_registry,
    validate_request_url,
)
from src.source_fetch import (
    SourceFetchError,
    curl_transport,
    user_agent,
    _fetch_source,
    verify_source_page,
)
from src.source_matrix import (
    AI_CLASSIFICATION_SOURCE_IDS,
    DEFAULT_SOURCE_MATRIX_PATH,
    GLOBAL_SOURCE_IDS,
    load_source_matrix,
    source_by_id,
)
from src.source_parsers.base import ParseContext
from src.source_parsers.registry import (
    ParserRegistryError,
    parse_source_page,
    verify_source_parser_binding,
)
from src.request_budget import (
    RequestBudgetError,
    complete_request,
    load_request_record,
    reserve_request,
)
from src.trading_calendar import verified_previous_trading_date


SOURCES_SCHEMA_VERSION = 3

#: Stage -> the ordered source ids acquired by that stage.
STAGE_SOURCE_IDS: dict[str, tuple[str, ...]] = {
    "DISCOVERY": ("YAHOO_JP_VOLUME_RANKING", "YAHOO_JP_GAIN_RANKING"),
    "STAGE1": ("JPX_CALENDAR", "JPX_LISTED_COMPANY", "JPX_TRADING_UNIT"),
    "STAGE2": (
        "YAHOO_JP_HISTORY",
        "KABUTAN_HISTORY",
        "JPX_TICK_SIZE",
        "JPX_TOPIX500",
    ),
    "TURNOVER": ("YAHOO_JP_QUOTE",),
    # All six event sources the Event Gate configuration references. COMPANY_IR
    # is required by event_gate.earnings.target_date_source_ids; dropping it
    # would make every earnings rule permanently DATA_UNAVAILABLE.
    "EVENT": (
        "JPX_TDNET",
        "JPX_EARNINGS_SCHEDULE",
        "COMPANY_IR",
        "COMPANY_IR_DISCLOSURE",
        "YAHOO_JP_NEWS",
        "KABUTAN_NEWS",
    ),
}

TSE_LISTING_SOURCE_ID = "JPX_LISTED_COMPANY"
TURNOVER_SOURCE_ID = "YAHOO_JP_QUOTE"

_TICKER_PATTERN = re.compile(r"^[0-9A-Z]{4}$")


class AcquisitionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class AcquisitionResult:
    stage: str
    target_date: str
    research_cutoff: str
    attempts: list[dict[str, Any]] = field(default_factory=list)
    values: list[dict[str, Any]] = field(default_factory=list)
    gate_status: str = "OPEN"
    gate_reason_codes: list[str] = field(default_factory=list)

    def as_ledger(self) -> dict[str, Any]:
        return {
            "schema_version": SOURCES_SCHEMA_VERSION,
            "target_date": self.target_date,
            "sources": self.values,
            "source_attempts": self.attempts,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def attempt_id_for(
    *,
    source_id: str,
    candidate_code: str | None,
    url: str,
    target_date: str,
    research_cutoff: str,
) -> str:
    """Deterministic **Logical Attempt** identity.

    ``attempt_id = f(source_id, candidate_code, url, target_date,
    research_cutoff)``: the same tuple can only ever produce one Logical
    Attempt per run, so no retry loop over one Attempt is representable.

    This is *not* the Physical Request Budget key. The Physical Request is
    identified by ``request_id = f(url, target_date, research_cutoff)`` and
    the SSOT for the Request Budget is its Request Record at
    ``runs/<target_date>/network_requests/<request_id>.json`` -- see
    :mod:`src.request_budget`. A shared page is one Physical Request serving
    N Logical Attempts, so the two identities are deliberately distinct.
    """
    payload = "|".join(
        [source_id, candidate_code or "GLOBAL", url, target_date, research_cutoff]
    )
    return "att-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class RequestBudgetCache:
    """Exact Logical Attempt identity, tracked against evidence already on disk.

    Attempt identity is the tuple ``(source_id, candidate_code, resolved_url,
    target_date, research_cutoff)`` -- exactly what :func:`attempt_id_for`
    hashes. If the exact same identity was already acquired (this run or a
    previous invocation of the same CLI against the same run directory), that
    Attempt is returned byte-for-byte immutable: it is never re-derived, and
    a MISS is never silently overwritten into a HIT.

    The actual "was a physical GET already made for this URL" question is a
    separate concern, answered by :mod:`src.request_budget`'s Physical
    Request Records on disk, not by anything this cache tracks in memory.
    """

    def __init__(self, run_dir: Path, existing: dict[str, Any] | None = None) -> None:
        self.run_dir = Path(run_dir)
        self._by_id: dict[str, dict[str, Any]] = {}
        for attempt in (existing or {}).get("source_attempts", []):
            if isinstance(attempt, dict) and attempt.get("attempt_id"):
                self.remember(attempt)

    def existing_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        return self._by_id.get(attempt_id)

    def remember(self, attempt: dict[str, Any]) -> None:
        if not attempt.get("attempt_id"):
            return
        self._by_id[str(attempt["attempt_id"])] = dict(attempt)


def resolve_url(
    definition: dict[str, Any],
    *,
    ticker: str | None,
    issuer_registry: dict[str, Any] | None,
) -> str:
    """Substitute the fixed template placeholders. No free-form URLs."""
    template = str(definition["url_template"])
    url = template
    if "{ticker}" in url:
        if not ticker:
            raise AcquisitionError(
                "URL_TEMPLATE_TICKER_REQUIRED",
                f"{definition['source_id']}: url_template requires a ticker",
            )
        url = url.replace("{ticker}", ticker)
    if "{issuer_domain}" in url:
        if not ticker:
            raise AcquisitionError(
                "URL_TEMPLATE_TICKER_REQUIRED",
                f"{definition['source_id']}: issuer url_template requires a ticker",
            )
        registry = (
            issuer_registry
            if issuer_registry is not None
            else load_issuer_domain_registry()
        )
        hosts = approved_issuer_hosts(ticker, registry)
        if len(hosts) != 1:
            raise AcquisitionError(
                "ISSUER_DOMAIN_AMBIGUOUS",
                f"ticker {ticker} has {len(hosts)} approved issuer hosts; "
                "acquisition requires exactly one",
            )
        url = url.replace("{issuer_domain}", hosts[0])
    if "{" in url:
        raise AcquisitionError(
            "URL_TEMPLATE_UNRESOLVED",
            f"{definition['source_id']}: unresolved placeholder in {url}",
        )
    return url


def _failed_attempt(
    *,
    definition: dict[str, Any],
    candidate_code: str | None,
    url: str,
    target_date: str,
    research_cutoff: str,
    status: str,
    notes: tuple[str, ...],
    requested_at: str | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Build the base Attempt dict.

    ``requested_at``/``retrieved_at`` are timestamp-truth overrides (FIX-R2-002A
    section 7): when a Physical Request Record already carries the true
    ``reserved_at``/``completed_at`` for this attempt, the caller passes those
    values through here rather than letting this function mint a fresh
    ``utc_now_iso()`` that would misrepresent when the network activity (or
    lack of it) actually happened. When omitted, ``requested_at`` defaults to
    "now" (Logical Attempt generation time -- used for pre-network and HIT
    attempts, which never claim to be the origin's ``reserved_at``).
    """
    return {
        "attempt_id": attempt_id_for(
            source_id=definition["source_id"],
            candidate_code=candidate_code,
            url=url,
            target_date=target_date,
            research_cutoff=research_cutoff,
        ),
        "source_id": definition["source_id"],
        "source_role": definition["role"],
        "criticality": definition["criticality"],
        "information_type": definition["information_type"],
        "candidate_code": candidate_code,
        "target_date": target_date,
        "research_cutoff": research_cutoff,
        "requested_at": requested_at if requested_at is not None else utc_now_iso(),
        "retrieved_at": retrieved_at,
        "url": url,
        "status": status,
        "values": None,
        "result_count": None,
        "notes": list(notes),
    }


def _ledger_values_from_attempt(
    attempt: dict[str, Any],
    source_id: str,
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reconstruct the Source Ledger entries an immutable, reused Attempt
    already implies -- from its own (unchanged) ``values``, never
    recomputed or re-derived. Only genuine parsed-field dicts (carrying
    ``source_ref``) count; Event Objects mixed into the same list do not.
    """
    if attempt.get("status") != "FOUND":
        return []
    ledger_values: list[dict[str, Any]] = []
    for value in attempt.get("values") or []:
        if not isinstance(value, dict) or not value.get("source_ref"):
            continue
        ledger_values.append(
            {
                "source_ref": value["source_ref"],
                "source_id": source_id,
                "source_role": definition["role"],
                "information_type": definition["information_type"],
                "source_status": "FOUND",
                "source_name": definition["source_name"],
                "source_url": attempt["url"],
                "retrieved_at": attempt["retrieved_at"],
                "trading_date": value.get("trading_date"),
                "ticker": value.get("ticker"),
                "field_name": value.get("field_name"),
                "value": value.get("value"),
            }
        )
    return ledger_values


def apply_parse_result_to_attempt(
    attempt: dict[str, Any],
    parsed: Any,
    source_id: str,
    definition: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fold a Parse Result into an Attempt whose raw evidence is already stored.

    This is the *only* implementation of "parsed values -> attempt business
    fields + Source Ledger values" in the codebase. It is deliberately
    side-effect free apart from mutating the ``attempt`` dict it is handed:
    it performs no network access, reserves no Physical Request, and writes
    nothing to disk. Only the parser-derived fields are touched --
    ``status`` (which the caller has already established as the transport's
    own FOUND), ``values``, ``result_count``, ``notes`` and
    ``coverage_status``. Every identity/evidence field is left exactly as the
    caller set it.

    Two callers share it: normal acquisition (:func:`_finish_found_attempt`,
    immediately after the transport stored the bytes) and the HUMAN-ONLY
    offline Discovery reparse recovery
    (:mod:`src.production_discovery_reparse`), which re-runs the *current*
    parser over already-stored raw evidence. Copying this logic into the
    recovery path would have meant two divergent definitions of what a
    parsed Attempt is.
    """
    if parsed.status != "FOUND":
        # FIX-012: the raw evidence WAS acquired and stored, so every
        # transport/evidence field stays on the attempt. Only the parsed
        # values are absent. Stripping the evidence here would destroy the
        # audit trail for exactly the failures that most need one.
        attempt["status"] = parsed.status
        attempt["values"] = None
        attempt["result_count"] = None
        attempt["notes"] = list(attempt.get("notes") or []) + list(parsed.reason_codes)
        return attempt, []

    values: list[dict[str, Any]] = []
    ledger_values: list[dict[str, Any]] = []
    for parsed_value in parsed.values:
        payload = parsed_value.as_dict()
        source_ref = f"{attempt['attempt_id']}#{payload['field_name']}"
        payload["source_ref"] = source_ref
        values.append(payload)
        # Every parsed value becomes a canonical Source Ledger entry, even a
        # Global Source's ticker=None value (source_id in GLOBAL_SOURCE_IDS,
        # e.g. JPX_TICK_SIZE's band table): it is one Source Record shared by
        # every candidate that consumes it, not a per-candidate clone --
        # see src.stage_wiring._ledger_value_records and
        # src.source_matrix.GLOBAL_SOURCE_IDS.
        ledger_values.append(
            {
                "source_ref": source_ref,
                "source_id": source_id,
                "source_role": definition["role"],
                "information_type": definition["information_type"],
                "source_status": "FOUND",
                "source_name": definition["source_name"],
                "source_url": attempt["url"],
                "retrieved_at": attempt["retrieved_at"],
                "trading_date": parsed_value.trading_date,
                "ticker": parsed_value.ticker,
                "field_name": parsed_value.field_name,
                "value": payload["value"],
            }
        )

    # Event sources additionally contribute Event Objects in exactly the shape
    # the existing Event Gate reads. They live alongside the parsed values in
    # the same list: the gate picks out dicts carrying ``event_type`` and
    # ignores everything else.
    event_objects = deterministic_event_objects(source_id, attempt["attempt_id"], values)
    if event_objects:
        values = values + event_objects
    coverage_status = deterministic_coverage_status(source_id, values)
    if coverage_status is not None:
        attempt["coverage_status"] = coverage_status

    # The transport outcome this helper is only ever called for is FOUND, so
    # this is a no-op for normal acquisition. It is stated explicitly because
    # the offline reparse recovery may be correcting an Attempt whose stored
    # status came from an older parser (e.g. PARSE_FAILED) over evidence the
    # Physical Request Record itself records as FOUND.
    attempt["status"] = "FOUND"
    attempt["values"] = values
    attempt["result_count"] = len(values)
    return attempt, ledger_values


def _finish_found_attempt(
    attempt: dict[str, Any],
    parsed: Any,
    source_id: str,
    definition: dict[str, Any],
    cache: "RequestBudgetCache | None",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Branch a FOUND transport outcome on parse status and build ledger values."""
    attempt, ledger_values = apply_parse_result_to_attempt(
        attempt, parsed, source_id, definition
    )
    if cache is not None:
        cache.remember(attempt)
    return attempt, ledger_values


def acquire_source(
    definition: dict[str, Any],
    *,
    target_date: str,
    trading_date: str,
    research_cutoff: str,
    candidate_code: str | None,
    run_dir: Path,
    issuer_registry: dict[str, Any] | None = None,
    transport: Callable[[str], Any] = curl_transport,
    cache: "RequestBudgetCache | None" = None,
    previous_trading_date: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Acquire and parse a single source. Returns (attempt, ledger values).

    FIX-R2-002: a Physical Network Request (:mod:`src.request_budget`) and a
    Logical Attempt are different things, tracked independently:

    1. If this exact Logical Attempt (source_id, candidate_code, resolved
       url, target_date, research_cutoff) already exists in ``cache``, it is
       returned byte-for-byte unchanged -- never re-derived, never
       "upgraded" from MISS to HIT.
    2. Otherwise the tuple's Physical Request is reserved (crash-safe,
       written to disk *before* any transport call) or found already
       COMPLETED (this run or a previous one): either way, the transport is
       invoked **at most once** per physical (url, target_date,
       research_cutoff) regardless of success, HTTP failure, timeout, or
       parse failure.
    """
    # Parser binding is verified *before* the network call: a mis-wired
    # Source Matrix must never cause a fetch.
    verify_source_parser_binding(definition)

    source_id = str(definition["source_id"])
    try:
        url = resolve_url(
            definition, ticker=candidate_code, issuer_registry=issuer_registry
        )
    except (AcquisitionError, NetworkPolicyError) as exc:
        # Pre-network failure: no Physical Request was ever consumed.
        attempt = _failed_attempt(
            definition=definition,
            candidate_code=candidate_code,
            url=str(definition["url_template"]),
            target_date=target_date,
            research_cutoff=research_cutoff,
            status="ACCESS_FAILED",
            notes=(getattr(exc, "code", "URL_RESOLUTION_FAILED"),),
        )
        attempt.update(
            {
                "request_id": None,
                "network_request_performed": False,
                "cache_status": "NOT_CACHEABLE",
                "reused_from_attempt_id": None,
            }
        )
        return attempt, []

    attempt_id = attempt_id_for(
        source_id=source_id,
        candidate_code=candidate_code,
        url=url,
        target_date=target_date,
        research_cutoff=research_cutoff,
    )

    # ---- Exact Logical Attempt Immutability -------------------------------
    if cache is not None:
        existing_attempt = cache.existing_attempt(attempt_id)
        if existing_attempt is not None:
            page_path = existing_attempt.get("source_page_path")
            page_sha = existing_attempt.get("source_page_sha256")
            if page_path and page_sha:
                verify_source_page(run_dir, str(page_path), str(page_sha))
            # FIX-R2-002A section 19: reuse of an existing MISS/HIT attempt
            # is never based on the attempt's own claims alone -- the
            # Physical Request Record it names must itself be present,
            # integrity-valid, and COMPLETED. NOT_CACHEABLE attempts never
            # had a Physical Request, so there is nothing to re-check.
            existing_cache_status = existing_attempt.get("cache_status")
            if existing_cache_status in ("MISS", "HIT"):
                existing_request_id = existing_attempt.get("request_id")
                if not existing_request_id:
                    raise AcquisitionError(
                        "EXACT_ATTEMPT_REQUEST_RECORD_MISSING",
                        f"attempt {attempt_id} claims cache_status="
                        f"{existing_cache_status} but carries no request_id",
                    )
                existing_record = load_request_record(run_dir, str(existing_request_id))
                if existing_record is None:
                    raise AcquisitionError(
                        "EXACT_ATTEMPT_REQUEST_RECORD_MISSING",
                        f"attempt {attempt_id} references request "
                        f"{existing_request_id} but no such Physical Request "
                        "Record exists on disk",
                    )
                if existing_record.get("state") != "COMPLETED":
                    raise AcquisitionError(
                        "EXACT_ATTEMPT_REQUEST_RECORD_NOT_COMPLETED",
                        f"attempt {attempt_id} references request "
                        f"{existing_request_id} which is not COMPLETED "
                        f"(state={existing_record.get('state')!r})",
                    )
            return dict(existing_attempt), _ledger_values_from_attempt(
                existing_attempt, source_id, definition
            )

    # ---- Pre-Network Failure Boundary (FIX-R2-002A section 5) -------------
    # Any validation that can determine, without ever touching the
    # transport, that this request will never be sent must run *before*
    # reserve_request(): a Physical Request Record must never be created for
    # something that was always going to fail pre-network.
    try:
        validate_request_url(
            url,
            source_id=source_id,
            ticker=candidate_code,
            issuer_registry=issuer_registry,
        )
        # User-Agent presence only matters to the real curl transport (it is
        # what curl_transport() embeds in the request); a caller-supplied
        # Fake Transport (every test in this codebase) never reads it, so
        # this check is scoped to the default transport only.
        if transport is curl_transport:
            user_agent()
    except (NetworkPolicyError, SourceFetchError) as exc:
        attempt = _failed_attempt(
            definition=definition,
            candidate_code=candidate_code,
            url=url,
            target_date=target_date,
            research_cutoff=research_cutoff,
            status="ACCESS_FAILED",
            notes=(getattr(exc, "code", "PRE_NETWORK_VALIDATION_FAILED"),),
        )
        attempt.update(
            {
                "request_id": None,
                "network_request_performed": False,
                "cache_status": "NOT_CACHEABLE",
                "reused_from_attempt_id": None,
            }
        )
        if cache is not None:
            cache.remember(attempt)
        return attempt, []

    # ---- Physical Request: reserve (crash-safe) before any transport call -
    reservation = reserve_request(
        run_dir,
        url=url,
        target_date=target_date,
        research_cutoff=research_cutoff,
        origin_source_id=source_id,
        origin_candidate_code=candidate_code,
        origin_attempt_id=attempt_id,
    )
    record = reservation.record
    request_id = str(record["request_id"])

    if reservation.already_completed:
        # Shared Physical Request: another attempt (this run or a previous
        # invocation) already consumed the GET for this exact URL. Reuse the
        # outcome; the transport is never called again for it.
        #
        # Timestamp truth (FIX-R2-002A section 7): a HIT's retrieved_at is
        # always the Physical Request's own completed_at -- never a fresh
        # utc_now_iso(). Its requested_at is this Logical Attempt's own
        # generation time (documented, not the origin MISS's reserved_at):
        # the HIT attempt itself was not "requested" at the origin's time,
        # it is being recorded now as a reuse of that origin's evidence.
        page_path = record.get("source_page_path")
        page_sha = record.get("source_page_sha256")
        stored = b""
        if page_path and page_sha:
            stored = verify_source_page(run_dir, str(page_path), str(page_sha))
        attempt = _failed_attempt(
            definition=definition,
            candidate_code=candidate_code,
            url=url,
            target_date=target_date,
            research_cutoff=research_cutoff,
            status=str(record.get("source_status")),
            notes=(),
            retrieved_at=record.get("completed_at"),
        )
        attempt.update(
            {
                "acquisition_method": "HTTP_GET",
                "http_status": record.get("http_status"),
                "content_type": record.get("content_type"),
                "transport_exit_code": record.get("transport_exit_code"),
                "cache_status": "HIT",
                "request_id": request_id,
                "network_request_performed": False,
                "reused_from_attempt_id": record.get("origin_attempt_id"),
            }
        )
        if page_path:
            attempt.update(
                {
                    "source_page_path": page_path,
                    "source_page_sha256": page_sha,
                    "source_page_size_bytes": record.get("source_page_size_bytes"),
                }
            )
        if str(record.get("source_status")) != "FOUND":
            if cache is not None:
                cache.remember(attempt)
            return attempt, []
        parsed = parse_source_page(
            stored,
            definition,
            ParseContext(
                source_id=source_id,
                trading_date=trading_date,
                ticker=candidate_code,
                content_type=record.get("content_type"),
                previous_trading_date=previous_trading_date,
            ),
        )
        return _finish_found_attempt(attempt, parsed, source_id, definition, cache)

    # ---- New Physical Request: the transport runs exactly once here -------
    #
    # FIX-R2-002A sections 17/18: once reserve_request() has written a
    # RESERVED record, this attempt to invoke the transport must resolve to
    # exactly one of two outcomes -- never a silent guess:
    #
    # * A known-safe launch failure (curl binary missing, permission denied,
    #   ...) is *known* to mean nothing was ever sent: it is completed as
    #   EXECUTION_FAILED like any other transport-execution fault.
    # * Anything else unexpected leaves send/no-send genuinely ambiguous:
    #   the RESERVED record is left untouched (never guessed at, never
    #   retried) and REQUEST_BUDGET_STATE_INDETERMINATE is raised.
    try:
        result = _fetch_source(
            url,
            source_id=source_id,
            candidate_code=candidate_code,
            ticker=candidate_code,
            issuer_registry=issuer_registry,
            run_dir=run_dir,
            transport=transport,
        )
    except OSError as exc:
        completed_record = complete_request(
            run_dir,
            request_id,
            source_status="EXECUTION_FAILED",
            http_status=None,
            content_type=None,
            transport_exit_code=-1,
            source_page_path=None,
            source_page_sha256=None,
            source_page_size_bytes=None,
        )
        attempt = _failed_attempt(
            definition=definition,
            candidate_code=candidate_code,
            url=url,
            target_date=target_date,
            research_cutoff=research_cutoff,
            status="EXECUTION_FAILED",
            notes=("TRANSPORT_LAUNCH_FAILED",),
            requested_at=record.get("reserved_at"),
            retrieved_at=completed_record.get("completed_at"),
        )
        attempt.update(
            {
                "acquisition_method": "HTTP_GET",
                "http_status": None,
                "content_type": None,
                "transport_exit_code": -1,
                "cache_status": "MISS",
                "request_id": request_id,
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            }
        )
        if cache is not None:
            cache.remember(attempt)
        return attempt, []
    except Exception as exc:  # noqa: BLE001 - deliberately broad: see docstring
        raise RequestBudgetError(
            "REQUEST_BUDGET_STATE_INDETERMINATE",
            f"transport invocation for request {request_id} raised an "
            f"unexpected {type(exc).__name__}; whether the request was sent "
            "is unknown, so the RESERVED record is left untouched and no "
            "retry is attempted",
        ) from exc
    completed_record = complete_request(
        run_dir,
        request_id,
        source_status=result.status,
        http_status=result.http_status,
        content_type=result.content_type,
        transport_exit_code=result.transport_exit_code,
        source_page_path=result.source_page_path,
        source_page_sha256=result.source_page_sha256,
        source_page_size_bytes=result.source_page_size_bytes,
    )

    # Timestamp truth (FIX-R2-002A section 7): the origin MISS attempt's
    # requested_at/retrieved_at are literally the Physical Request Record's
    # own reserved_at/completed_at -- never a separately generated
    # utc_now_iso() that could drift from what was actually reserved/completed.
    attempt = _failed_attempt(
        definition=definition,
        candidate_code=candidate_code,
        url=result.url,
        target_date=target_date,
        research_cutoff=research_cutoff,
        status=result.status,
        notes=result.notes,
        requested_at=record.get("reserved_at"),
        retrieved_at=completed_record.get("completed_at"),
    )
    attempt.update(
        {
            "acquisition_method": result.acquisition_method,
            "http_status": result.http_status,
            "content_type": result.content_type,
            "transport_exit_code": result.transport_exit_code,
            "cache_status": "MISS",
            "request_id": request_id,
            "network_request_performed": True,
            "reused_from_attempt_id": None,
        }
    )
    if result.source_page_path:
        attempt.update(
            {
                "source_page_path": result.source_page_path,
                "source_page_sha256": result.source_page_sha256,
                "source_page_size_bytes": result.source_page_size_bytes,
            }
        )

    if result.status != "FOUND":
        if cache is not None:
            cache.remember(attempt)
        return attempt, []

    # Re-read the bytes from disk and re-verify their hash before parsing:
    # the parser must operate on exactly the stored evidence.
    stored = verify_source_page(run_dir, result.source_page_path, result.source_page_sha256)
    parsed = parse_source_page(
        stored,
        definition,
        ParseContext(
            source_id=source_id,
            trading_date=trading_date,
            ticker=candidate_code,
            content_type=result.content_type,
            previous_trading_date=previous_trading_date,
        ),
    )
    return _finish_found_attempt(attempt, parsed, source_id, definition, cache)


def acquire_stage(
    stage: str,
    *,
    target_date: str,
    trading_date: str,
    research_cutoff: str,
    tickers: list[str],
    run_dir: Path,
    source_matrix: dict[str, Any] | None = None,
    issuer_registry: dict[str, Any] | None = None,
    transport: Callable[[str], Any] = curl_transport,
    source_ids: tuple[str, ...] | None = None,
    existing_ledger: dict[str, Any] | None = None,
) -> AcquisitionResult:
    """Run one acquisition stage.

    ``tickers`` is the exact candidate set the caller is authorized to
    acquire for; the engine never widens it.

    ``existing_ledger`` is the run's current sources.json. It seeds the
    Request Budget cache, so re-running the same acquisition CLI against an
    already-satisfied run performs **zero** additional network requests.
    """
    if stage not in STAGE_SOURCE_IDS:
        raise AcquisitionError("UNKNOWN_ACQUISITION_STAGE", f"unknown stage {stage!r}")
    for ticker in tickers:
        if not _TICKER_PATTERN.match(ticker):
            raise AcquisitionError(
                "CANDIDATE_TICKER_MALFORMED",
                f"candidate ticker is not canonical: {ticker!r}",
            )

    matrix = source_matrix if source_matrix is not None else load_source_matrix()
    definitions = source_by_id(matrix)
    result = AcquisitionResult(
        stage=stage,
        target_date=target_date,
        research_cutoff=research_cutoff,
    )

    selected = source_ids if source_ids is not None else STAGE_SOURCE_IDS[stage]
    # Verify every binding for the whole stage before the first GET.
    for source_id in selected:
        if source_id not in definitions:
            raise AcquisitionError(
                "SOURCE_NOT_IN_MATRIX",
                f"{source_id} is not defined in the Source Matrix",
            )
        verify_source_parser_binding(definitions[source_id])

    cache = RequestBudgetCache(run_dir, existing_ledger)

    # The exact previous JPX trading date, resolved once per stage from
    # verified JPX_CALENDAR evidence already on the run's ledger (Stage1
    # acquires JPX_CALENDAR; Stage2 consumes it). Only the history parsers
    # (YAHOO_JP_HISTORY / KABUTAN_HISTORY) read this; every other source
    # simply ignores it. None (calendar evidence unavailable) means those
    # parsers must not derive previous_close/previous_high at all.
    previous_trading_date = (
        verified_previous_trading_date(
            existing_ledger,
            run_dir=run_dir,
            trading_date=trading_date,
            target_date=target_date,
            research_cutoff=research_cutoff,
        )
        if stage == "STAGE2"
        else None
    )

    seen_attempt_ids: set[str] = set()
    for source_id in selected:
        definition = definitions[source_id]
        candidates: list[str | None] = _candidate_scope(stage, source_id, tickers)
        for candidate_code in candidates:
            attempt, values = acquire_source(
                definition,
                target_date=target_date,
                trading_date=trading_date,
                research_cutoff=research_cutoff,
                candidate_code=candidate_code,
                run_dir=run_dir,
                issuer_registry=issuer_registry,
                transport=transport,
                cache=cache,
                previous_trading_date=previous_trading_date,
            )
            # Request Budget: one GET per exact tuple, per run.
            if attempt["attempt_id"] in seen_attempt_ids:
                continue
            seen_attempt_ids.add(attempt["attempt_id"])
            result.attempts.append(attempt)
            result.values.extend(values)

    if stage == "STAGE1":
        _apply_tse_listing_gate(result, tickers)
    if stage == "TURNOVER":
        _apply_turnover_semantics(result)

    return result


def _candidate_scope(
    stage: str,
    source_id: str,
    tickers: list[str],
) -> list[str | None]:
    """Which candidate codes this source produces attempts for.

    The Event Gate selects evidence with ``candidate_code == ticker``, so
    every event source -- including the globally-shared index pages -- must
    yield one **candidate-scoped** attempt per candidate. The Request Budget
    cache collapses those N attempts onto a single network GET of the shared
    page (see :class:`RequestBudgetCache`), so this widens the attempt count,
    never the request count.
    """
    if stage == "EVENT":
        return list(tickers)
    if source_id in GLOBAL_SOURCE_IDS:
        return [None]
    return list(tickers)


def _apply_tse_listing_gate(result: AcquisitionResult, tickers: list[str]) -> None:
    """TSE Listing Gate: batch, all-or-nothing.

    If listing cannot be confirmed for **every** candidate, the whole batch
    fails. There is no per-ticker exclusion (which would silently reshape the
    candidate universe) and no ``.T`` suffix guessing.
    """
    listing_attempts = {
        attempt["candidate_code"]: attempt
        for attempt in result.attempts
        if attempt["source_id"] == TSE_LISTING_SOURCE_ID
    }
    unresolved = [
        ticker
        for ticker in tickers
        if listing_attempts.get(ticker, {}).get("status") != "FOUND"
    ]
    if unresolved:
        result.gate_status = "CLOSED"
        result.gate_reason_codes.append("TSE_LISTING_BATCH_GATE_FAILED")


def _apply_turnover_semantics(result: AcquisitionResult) -> None:
    """A failed turnover acquisition yields turnover=null downstream.

    A previous run's FOUND turnover is never reusable here: this stage only
    ever emits attempts it made itself, so no stale FOUND can leak in.
    """
    failed = [
        attempt["candidate_code"]
        for attempt in result.attempts
        if attempt["source_id"] == TURNOVER_SOURCE_ID
        and attempt["status"] != "FOUND"
    ]
    if failed:
        result.gate_status = "PARTIAL"
        result.gate_reason_codes.append("TURNOVER_SOURCE_UNAVAILABLE")


#: Fields a Logical Attempt's identity/network facts must never change once
#: written -- see FIX-R2-002 section 21. Business fields Event processing
#: legitimately adds later (coverage_status, notes, values/result_count) are
#: deliberately excluded: only the fields that describe *what physically
#: happened on the wire* are immutable.
_IMMUTABLE_ATTEMPT_FIELDS: tuple[str, ...] = (
    "attempt_id",
    "request_id",
    "source_id",
    "source_role",
    "criticality",
    "information_type",
    "candidate_code",
    "target_date",
    "research_cutoff",
    "requested_at",
    "retrieved_at",
    "url",
    "network_request_performed",
    "cache_status",
    "reused_from_attempt_id",
    "acquisition_method",
    "http_status",
    "content_type",
    "transport_exit_code",
    "source_page_path",
    "source_page_sha256",
    "source_page_size_bytes",
    "status",
)


def merge_ledger(
    existing: dict[str, Any] | None,
    addition: dict[str, Any],
) -> dict[str, Any]:
    """Merge a stage's ledger into the run ledger.

    Attempts are keyed by ``attempt_id`` and values by ``source_ref``. A
    reappearing ``attempt_id`` is never a later-wins overwrite: its
    immutable network facts (:data:`_IMMUTABLE_ATTEMPT_FIELDS`) must agree
    with what is already on disk, or the merge is a hard error -- silently
    replacing a MISS's evidence with something else (even something that
    looks like a HIT of it) would erase the truth of what actually happened
    on the wire.
    """
    if existing is None:
        return addition
    if existing.get("target_date") != addition.get("target_date"):
        raise AcquisitionError(
            "SOURCE_LEDGER_TARGET_DATE_MISMATCH",
            "cannot merge acquisition results across different target dates",
        )
    attempts = {attempt["attempt_id"]: attempt for attempt in existing.get("source_attempts", [])}
    for attempt in addition.get("source_attempts", []):
        attempt_id = attempt["attempt_id"]
        prior = attempts.get(attempt_id)
        if prior is not None:
            mismatched = [
                field_name
                for field_name in _IMMUTABLE_ATTEMPT_FIELDS
                if prior.get(field_name) != attempt.get(field_name)
            ]
            if mismatched:
                raise AcquisitionError(
                    "SOURCE_ATTEMPT_IMMUTABILITY_VIOLATION",
                    f"attempt {attempt_id} network facts changed on re-merge: "
                    + ", ".join(mismatched),
                )
        # Immutable network facts agree (or this is a genuinely new
        # attempt_id): the addition wins, carrying forward whatever
        # legitimate business-field update it makes (values, coverage_status,
        # notes, ...).
        attempts[attempt_id] = attempt
    values = {value["source_ref"]: value for value in existing.get("sources", [])}
    for value in addition.get("sources", []):
        values[value["source_ref"]] = value
    return {
        "schema_version": max(
            int(existing.get("schema_version", 1)),
            int(addition.get("schema_version", 1)),
        ),
        "target_date": addition["target_date"],
        "sources": list(values.values()),
        "source_attempts": list(attempts.values()),
    }


def write_ledger(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_ledger(path: Path) -> dict[str, Any] | None:
    if not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "AcquisitionError",
    "AcquisitionResult",
    "AI_CLASSIFICATION_SOURCE_IDS",
    "DEFAULT_SOURCE_MATRIX_PATH",
    "GLOBAL_SOURCE_IDS",
    "STAGE_SOURCE_IDS",
    "acquire_source",
    "acquire_stage",
    "apply_parse_result_to_attempt",
    "attempt_id_for",
    "load_ledger",
    "merge_ledger",
    "resolve_url",
    "write_ledger",
    "ParserRegistryError",
    "SourceFetchError",
]
