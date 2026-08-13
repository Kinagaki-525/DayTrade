"""Stage-aware deterministic Source Acquisition.

One engine, five stages (Discovery / Stage1 / Stage2 / Turnover / Event).
Every stage:

1. resolves its URL from ``config/source_matrix.yaml`` (never from a page,
   never from an agent),
2. verifies the ``source_id -> parser`` binding **before** any network call,
3. fetches at most one GET per (source, candidate, url, date, cutoff) tuple
   (Request Budget) through :mod:`src.source_fetch`,
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
)
from src.source_fetch import (
    FetchResult,
    SourceFetchError,
    curl_transport,
    _fetch_source,
    verify_source_page,
)
from src.source_matrix import (
    AI_CLASSIFICATION_SOURCE_IDS,
    DEFAULT_SOURCE_MATRIX_PATH,
    load_source_matrix,
    source_by_id,
)
from src.source_parsers.base import ParseContext
from src.source_parsers.registry import (
    ParserRegistryError,
    parse_source_page,
    verify_source_parser_binding,
)


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

#: Source ids fetched once per run rather than once per candidate.
GLOBAL_SOURCE_IDS = frozenset(
    {
        "YAHOO_JP_VOLUME_RANKING",
        "YAHOO_JP_GAIN_RANKING",
        "JPX_CALENDAR",
        "JPX_TICK_SIZE",
        "JPX_TDNET",
        "JPX_EARNINGS_SCHEDULE",
    }
)

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
    """Deterministic attempt id.

    It doubles as the Request Budget key: the same (source, candidate, url,
    date, cutoff) tuple can only ever produce one attempt per run, so no
    retry loop and no accidental duplicate GET is representable.
    """
    payload = "|".join(
        [source_id, candidate_code or "GLOBAL", url, target_date, research_cutoff]
    )
    return "att-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class RequestBudgetCache:
    """The Request Budget, enforced against evidence already on disk.

    Attempt identity is the tuple ``(source_id, candidate_code, resolved_url,
    target_date, research_cutoff)`` -- exactly what :func:`attempt_id_for`
    hashes. Before any network request, an attempt whose identity is already
    satisfied by a stored, hash-verified raw page is reused with
    ``cache_status="HIT"`` and **no GET is issued**.

    A stored page whose bytes no longer hash to the recorded SHA256 is a hard
    stop (``SOURCE_PAGE_HASH_MISMATCH`` from :func:`verify_source_page`): the
    run is tampered with or corrupted, and silently re-fetching to "self-heal"
    would hide exactly that.
    """

    def __init__(self, run_dir: Path, existing: dict[str, Any] | None = None) -> None:
        self.run_dir = Path(run_dir)
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_page_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for attempt in (existing or {}).get("source_attempts", []):
            if isinstance(attempt, dict) and attempt.get("attempt_id"):
                self.remember(attempt)

    @staticmethod
    def _page_key(attempt: dict[str, Any]) -> tuple[str, str, str]:
        """Identity of the *raw page*, which is candidate-independent.

        A globally-shared page (one JPX_TDNET index relevant to every
        candidate) is fetched exactly once; each relevant candidate then gets
        its own candidate-scoped Source Attempt referencing the same stored
        bytes and the same SHA256.
        """
        return (
            str(attempt.get("url") or ""),
            str(attempt.get("target_date") or ""),
            str(attempt.get("research_cutoff") or ""),
        )

    def lookup_page(
        self,
        *,
        url: str,
        target_date: str,
        research_cutoff: str,
    ) -> dict[str, Any] | None:
        """A stored raw page for this exact URL, whoever fetched it."""
        return self._by_page_key.get((url, target_date, research_cutoff))

    def lookup(
        self,
        *,
        source_id: str,
        candidate_code: str | None,
        url: str,
        target_date: str,
        research_cutoff: str,
    ) -> dict[str, Any] | None:
        attempt_id = attempt_id_for(
            source_id=source_id,
            candidate_code=candidate_code,
            url=url,
            target_date=target_date,
            research_cutoff=research_cutoff,
        )
        attempt = self._by_id.get(attempt_id)
        if attempt is None:
            return None
        # Only a genuinely completed acquisition is reusable, and only when
        # the whole identity tuple -- not just the id -- still agrees.
        if attempt.get("status") not in {"FOUND", "PARSE_FAILED"}:
            return None
        if (
            str(attempt.get("source_id") or "") != source_id
            or (attempt.get("candidate_code") or None) != candidate_code
            or str(attempt.get("url") or "") != url
            or str(attempt.get("target_date") or "") != target_date
            or str(attempt.get("research_cutoff") or "") != research_cutoff
        ):
            return None
        if not attempt.get("source_page_path") or not attempt.get("source_page_sha256"):
            return None
        if attempt.get("source_page_size_bytes") is None:
            return None
        return attempt

    def remember(self, attempt: dict[str, Any]) -> None:
        if not attempt.get("attempt_id"):
            return
        record = dict(attempt)
        self._by_id[str(record["attempt_id"])] = record
        if (
            record.get("source_page_path")
            and record.get("source_page_sha256")
            and record.get("source_page_size_bytes") is not None
        ):
            self._by_page_key.setdefault(self._page_key(record), record)


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
) -> dict[str, Any]:
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
        "requested_at": utc_now_iso(),
        "retrieved_at": None,
        "url": url,
        "status": status,
        "values": None,
        "result_count": None,
        "notes": list(notes),
    }


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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Acquire and parse a single source. Returns (attempt, ledger values)."""
    # Parser binding is verified *before* the network call: a mis-wired
    # Source Matrix must never cause a fetch.
    verify_source_parser_binding(definition)

    source_id = str(definition["source_id"])
    try:
        url = resolve_url(
            definition, ticker=candidate_code, issuer_registry=issuer_registry
        )
    except (AcquisitionError, NetworkPolicyError) as exc:
        return (
            _failed_attempt(
                definition=definition,
                candidate_code=candidate_code,
                url=str(definition["url_template"]),
                target_date=target_date,
                research_cutoff=research_cutoff,
                status="ACCESS_FAILED",
                notes=(getattr(exc, "code", "URL_RESOLUTION_FAILED"),),
            ),
            [],
        )

    # ---- Request Budget: is this exact tuple already satisfied on disk? ----
    cached = None
    if cache is not None:
        cached = cache.lookup(
            source_id=source_id,
            candidate_code=candidate_code,
            url=url,
            target_date=target_date,
            research_cutoff=research_cutoff,
        )
        if cached is None:
            # Globally-shared page: the same URL was already fetched for
            # another candidate. Reuse the raw bytes, still record a separate
            # candidate-scoped attempt. Network requests: 1. Attempts: N.
            cached = cache.lookup_page(
                url=url,
                target_date=target_date,
                research_cutoff=research_cutoff,
            )

    if cached is not None:
        # A cache HIT costs zero network requests. The stored bytes are
        # re-hashed first; a mismatch is a hard stop, never a silent re-fetch.
        stored = verify_source_page(
            run_dir, str(cached["source_page_path"]), str(cached["source_page_sha256"])
        )
        result = FetchResult(
            url=str(cached["url"]),
            source_id=source_id,
            candidate_code=candidate_code,
            status="FOUND",
            http_status=cached.get("http_status"),
            content_type=cached.get("content_type"),
            transport_exit_code=int(cached.get("transport_exit_code", 0)),
            body=stored,
            source_page_sha256=str(cached["source_page_sha256"]),
            source_page_size_bytes=int(cached["source_page_size_bytes"]),
            source_page_path=str(cached["source_page_path"]),
        )
        cache_status = "HIT"
        retrieved_at = str(cached.get("retrieved_at") or utc_now_iso())
    else:
        result = _fetch_source(
            url,
            source_id=source_id,
            candidate_code=candidate_code,
            ticker=candidate_code,
            issuer_registry=issuer_registry,
            run_dir=run_dir,
            transport=transport,
        )
        cache_status = "MISS"
        retrieved_at = utc_now_iso()

    attempt = _failed_attempt(
        definition=definition,
        candidate_code=candidate_code,
        url=result.url,
        target_date=target_date,
        research_cutoff=research_cutoff,
        status=result.status,
        notes=result.notes,
    )

    if result.status != "FOUND":
        return attempt, []

    if cache_status == "MISS":
        # Re-read the bytes from disk and re-verify their hash before parsing:
        # the parser must operate on exactly the stored evidence.
        stored = verify_source_page(
            run_dir, result.source_page_path, result.source_page_sha256
        )

    parsed = parse_source_page(
        stored,
        definition,
        ParseContext(
            source_id=source_id,
            trading_date=trading_date,
            ticker=candidate_code,
            content_type=result.content_type,
        ),
    )

    attempt.update(
        {
            "acquisition_method": result.acquisition_method,
            "http_status": result.http_status,
            "content_type": result.content_type,
            "transport_exit_code": result.transport_exit_code,
            "source_page_path": result.source_page_path,
            "source_page_sha256": result.source_page_sha256,
            "source_page_size_bytes": result.source_page_size_bytes,
            "retrieved_at": retrieved_at,
            "cache_status": cache_status,
        }
    )
    if cache is not None:
        cache.remember(attempt)

    if parsed.status != "FOUND":
        # FIX-012: the raw evidence WAS acquired and stored, so every
        # transport/evidence field stays on the attempt. Only the parsed
        # values are absent. Stripping the evidence here would destroy the
        # audit trail for exactly the failures that most need one.
        attempt["status"] = parsed.status
        attempt["values"] = None
        attempt["result_count"] = None
        attempt["notes"] = list(attempt["notes"]) + list(parsed.reason_codes)
        return attempt, []

    values: list[dict[str, Any]] = []
    ledger_values: list[dict[str, Any]] = []
    for parsed_value in parsed.values:
        payload = parsed_value.as_dict()
        source_ref = f"{attempt['attempt_id']}#{payload['field_name']}"
        payload["source_ref"] = source_ref
        values.append(payload)
        if parsed_value.ticker:
            ledger_values.append(
                {
                    "source_ref": source_ref,
                    "source_id": source_id,
                    "source_role": definition["role"],
                    "information_type": definition["information_type"],
                    "source_status": "FOUND",
                    "source_name": definition["source_name"],
                    "source_url": result.url,
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

    attempt["values"] = values
    attempt["result_count"] = len(values)
    return attempt, ledger_values


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


def merge_ledger(
    existing: dict[str, Any] | None,
    addition: dict[str, Any],
) -> dict[str, Any]:
    """Merge a stage's ledger into the run ledger.

    Attempts are keyed by ``attempt_id`` and values by ``source_ref``;
    re-running a stage replaces its own attempts rather than appending
    duplicates.
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
        attempts[attempt["attempt_id"]] = attempt
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
    "attempt_id_for",
    "load_ledger",
    "merge_ledger",
    "resolve_url",
    "write_ledger",
    "ParserRegistryError",
    "SourceFetchError",
]
