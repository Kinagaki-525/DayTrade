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
    fetch_source,
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
    "EVENT": (
        "JPX_TDNET",
        "JPX_EARNINGS_SCHEDULE",
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

    result: FetchResult = fetch_source(
        url,
        source_id=source_id,
        candidate_code=candidate_code,
        ticker=candidate_code,
        issuer_registry=issuer_registry,
        run_dir=run_dir,
        transport=transport,
    )

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
            "retrieved_at": utc_now_iso(),
            "cache_status": "MISS",
        }
    )

    if parsed.status != "FOUND":
        attempt["status"] = parsed.status
        attempt["values"] = None
        attempt["result_count"] = None
        attempt["notes"] = list(attempt["notes"]) + list(parsed.reason_codes)
        if parsed.status != "FOUND":
            # A non-FOUND parse keeps the evidence, but the attempt is not a
            # FOUND attempt, so v3's FOUND-only required fields do not apply.
            for key in (
                "acquisition_method",
                "http_status",
                "content_type",
                "transport_exit_code",
                "source_page_sha256",
                "source_page_size_bytes",
            ):
                attempt.pop(key, None)
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
) -> AcquisitionResult:
    """Run one acquisition stage.

    ``tickers`` is the exact candidate set the caller is authorized to
    acquire for; the engine never widens it.
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

    seen_attempt_ids: set[str] = set()
    for source_id in selected:
        definition = definitions[source_id]
        candidates: list[str | None] = (
            [None] if source_id in GLOBAL_SOURCE_IDS else list(tickers)
        )
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
