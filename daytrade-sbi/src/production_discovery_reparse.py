"""HUMAN-ONLY offline Discovery reparse recovery. Zero network, by construction.

Why this exists
---------------

A Logical Attempt is immutable (:mod:`src.source_acquisition`): re-running
``acquire-discovery`` against a run that already holds the exact same Logical
Attempt reuses it byte-for-byte and never re-evaluates the stored raw page.
That is the correct normal behaviour -- a nightly re-run must not silently
change what a run recorded -- but it means a *parser fix* cannot reach the
Raw Source Evidence a stopped Production Nightly already paid a Physical
Request for.

The 2026-08-27 Production Nightly is exactly that case: the two Yahoo ranking
pages are on disk with verified SHA256s, the current parser reads a full
TOP50 out of each of them, and the pre-fix Logical Attempts still say 47/45.
Discovery is fail-closed on that, so the whole run is blocked behind evidence
that is already good.

What this module does
---------------------

It re-runs the **current** deterministic parser over the **already-stored**
raw bytes of the two Discovery sources and, only if that produces a genuine
TOP50 on both routes, atomically rewrites the parser-derived fields of those
two Logical Attempts in ``sources.json``. Nothing else.

What it must never do (contract, enforced by the code below and by
``tests/test_production_discovery_reparse.py``):

* no HTTP GET, no retry, no transport of any kind -- this module does not
  import ``curl_transport`` / ``_fetch_source``;
* no Physical Request mutation -- it does not import ``reserve_request`` /
  ``complete_request``; ``network_requests/*.json`` is read-only evidence and
  is byte-identical before and after;
* no Source Page mutation -- ``source_pages/`` is read-only evidence and is
  byte-identical before and after;
* no ``attempt_id`` / ``request_id`` change: the Logical Acquisition Identity
  and every physical fact of the wire are unchanged, because no new
  acquisition happened;
* no downstream repair: if any artifact after Discovery already exists, the
  recovery refuses outright rather than invalidating a Trust Chain (and it
  never deletes one to make room);
* no ``--force``, no ``--run-dir``, no ``--parser``, no source selection: the
  only human input is ``--target-date``;
* nothing is written at all unless every precondition passed and both routes
  reparse to a full TOP50.

It is HUMAN-ONLY. It is not a canonical ``src.cli`` subcommand, so it cannot
appear in the Production Managed Policy's ``APPROVED_SUBCOMMANDS``, and a
coding agent must never run it. Production Claude stops and hands the run
back to a human instead.

``market_research.json`` is deliberately **not** written here. After a
successful recovery the human simply re-runs the canonical
``acquire-discovery``, which reuses the corrected Logical Attempts, performs
zero network requests, and regenerates ``market_research.json`` through the
one canonical code path that is allowed to build it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from src.claude_runtime_security import (
    PROTECTED_TREE_PREFIXES,
    RuntimeSecurityError,
    resolve_run_dir,
    validate_target_date,
    verify_source_tree_clean,
)
from src.contracts import load_json_document, validate_json_document
from src.file_io import atomic_write_text
from src.request_budget import (
    RequestBudgetError,
    load_request_record,
    network_request_path,
)
from src.source_acquisition import (
    AcquisitionResult,
    apply_parse_result_to_attempt,
)
from src.source_fetch import SourceFetchError, verify_source_page
from src.source_matrix import (
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
from src.stage_wiring import (
    StageWiringError,
    build_discovery_routes,
    confirm_discovery_top50,
)


# --------------------------------------------------------------- layout ---

#: ``daytrade-sbi/`` -- derived exactly like every other module's root.
DAYTRADE_ROOT = Path(__file__).resolve().parents[1]
#: The git repository root, used only for local read-only git inspection.
REPOSITORY_ROOT = DAYTRADE_ROOT.parent

STAGE = "DISCOVERY"

#: The only two sources this recovery may ever touch. Fixed by contract:
#: there is no CLI option, and no caller-supplied source id, that can widen
#: this into a generic replay of any stage.
DISCOVERY_SOURCE_IDS: tuple[str, ...] = (
    "YAHOO_JP_VOLUME_RANKING",
    "YAHOO_JP_GAIN_RANKING",
)

RUNTIME_SECURITY_SCHEMA_NAME = "runtime_security.schema.json"
RESEARCH_WINDOW_SCHEMA_NAME = "research_window.schema.json"
SOURCES_SCHEMA_NAME = "sources.schema.json"
MARKET_RESEARCH_SCHEMA_NAME = "market_research.schema.json"
AUDIT_SCHEMA_NAME = "production_discovery_reparse.schema.json"
AUDIT_SCHEMA_VERSION = 1

#: ``working/<AUDIT_DIRNAME>/<git_head_sha>.json`` -- a Non-Business Sidecar.
#: It is deliberately NOT added to ``contracts.RUN_ARTIFACT_ALLOWLIST``: the
#: Business Verifier skips ``working/`` wholesale and must keep doing so.
AUDIT_DIRNAME = "production_discovery_reparse"
WORKING_DIRNAME = "working"

RESULT_REPARSED = "REPARSED"
RESULT_ALREADY_REPARSED = "ALREADY_REPARSED"

#: Business Artifacts a run is allowed to hold at the moment Discovery is
#: being recovered. Anything else in the run directory means the pipeline
#: already ran past Discovery, so correcting Discovery evidence now would
#: invalidate a Trust Chain that is already built on the old values.
#: ``working/`` is the Non-Business Sidecar and is always allowed.
ALLOWED_PRE_DISCOVERY_ENTRIES: frozenset[str] = frozenset(
    {
        "strategy_snapshot.yaml",
        "research_window.json",
        "market_research.json",
        "sources.json",
        "source_pages",
        "network_requests",
        WORKING_DIRNAME,
    }
)

#: Every Logical Attempt field that describes *identity* or *what physically
#: happened on the wire*. A recovery re-parses stored bytes; it does not
#: acquire anything, so each of these must be bit-identical before and after.
#: ``status`` is deliberately absent -- it is parser-derived and is exactly
#: what a Parser fix may legitimately correct -- but the normal
#: ``merge_ledger`` immutability contract (which does include ``status``) is
#: untouched: this recovery bypasses that merge rather than relaxing it.
IMMUTABLE_ATTEMPT_FIELDS: tuple[str, ...] = (
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
)

#: Evidence fields a Logical Attempt and its Physical Request Record must
#: agree on before the stored page is parsed at all.
EVIDENCE_CROSS_CHECK_FIELDS: tuple[str, ...] = (
    "source_page_path",
    "source_page_sha256",
    "source_page_size_bytes",
    "http_status",
    "content_type",
    "transport_exit_code",
)

ERROR_PREFIX = "PRODUCTION_DISCOVERY_REPARSE_"

ERROR_CODES = (
    "PRODUCTION_DISCOVERY_REPARSE_TARGET_DATE_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_RUN_MISSING",
    "PRODUCTION_DISCOVERY_REPARSE_RUNTIME_SECURITY_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_GIT_HEAD_MISMATCH",
    "PRODUCTION_DISCOVERY_REPARSE_SOURCE_TREE_DIRTY",
    "PRODUCTION_DISCOVERY_REPARSE_RESEARCH_WINDOW_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT",
    "PRODUCTION_DISCOVERY_REPARSE_DISCOVERY_ALREADY_COMPLETE",
    "PRODUCTION_DISCOVERY_REPARSE_ATTEMPT_SET_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_REQUEST_NOT_COMPLETED",
    "PRODUCTION_DISCOVERY_REPARSE_REQUEST_NOT_FOUND",
    "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_MISMATCH",
    "PRODUCTION_DISCOVERY_REPARSE_SOURCE_PAGE_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_PARSER_BINDING_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_IDENTITY_VIOLATION",
    "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_CHANGED_DURING_RECOVERY",
    "PRODUCTION_DISCOVERY_REPARSE_STILL_INCOMPLETE",
    "PRODUCTION_DISCOVERY_REPARSE_WRITE_FAILED",
    "PRODUCTION_DISCOVERY_REPARSE_AUDIT_CONFLICT",
    "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
    "PRODUCTION_DISCOVERY_REPARSE_AUDIT_WRITE_FAILED",
    "PRODUCTION_DISCOVERY_REPARSE_ROLLBACK_FAILED",
)


class ProductionDiscoveryReparseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> ProductionDiscoveryReparseError:
    assert code in ERROR_CODES, f"undeclared recovery error code: {code}"
    return ProductionDiscoveryReparseError(code, message)


# ------------------------------------------------------- small utilities ---


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_regular_file(path: Path) -> bool:
    """True only for a real regular file -- a symlink is never followed."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _is_real_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def default_run_command(argv: list[str], cwd: Path) -> str:
    """Run one local, read-only command and return its stdout.

    Used exclusively for ``git rev-parse HEAD`` and ``git status
    --porcelain``. No git network subcommand is ever invoked here, and the
    caller may replace this entirely (the tests do).
    """
    completed = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RUNTIME_SECURITY_INVALID",
            f"command failed: {' '.join(argv)}: {completed.stderr.strip()}",
        )
    return completed.stdout


# ------------------------------------------------------------ validation ---


def _validated_target_date(value: str) -> str:
    try:
        return validate_target_date(value)
    except RuntimeSecurityError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_TARGET_DATE_INVALID", error.message
        ) from None


def _resolve_run_dir(daytrade_root: Path, target_date: str) -> Path:
    try:
        run_dir = resolve_run_dir(daytrade_root, target_date)
    except RuntimeSecurityError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_TARGET_DATE_INVALID", error.message
        ) from None
    if Path(run_dir).is_symlink() or not _is_real_directory(Path(run_dir)):
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RUN_MISSING",
            f"run directory is not a real directory: {run_dir}",
        )
    return Path(run_dir)


def _load_runtime_security(run_dir: Path, target_date: str) -> dict[str, Any]:
    path = run_dir / WORKING_DIRNAME / "runtime_security.json"
    if not _is_regular_file(path):
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RUNTIME_SECURITY_INVALID",
            f"Runtime Security Attestation not found: {path}",
        )
    try:
        payload = load_json_document(path, RUNTIME_SECURITY_SCHEMA_NAME)
    except (ValueError, OSError) as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RUNTIME_SECURITY_INVALID",
            f"{path} is not a valid Runtime Security Attestation: {error}",
        ) from None
    if payload.get("target_date") != target_date:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RUNTIME_SECURITY_INVALID",
            f"{path} attests target_date {payload.get('target_date')!r}, "
            f"not {target_date!r}",
        )
    return payload


def _verify_local_git_state(
    *,
    attested_head: str,
    repository_root: Path,
    run_command: Callable[[list[str], Path], str],
) -> None:
    """Local, read-only git inspection. No network git operation exists here."""
    head = run_command(["git", "rev-parse", "HEAD"], repository_root).strip()
    if head != attested_head:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_GIT_HEAD_MISMATCH",
            f"local HEAD {head!r} does not match the run's attested "
            f"git_head_sha {attested_head!r}; the recovery must run on exactly "
            "the commit whose parser fix it is applying",
        )
    porcelain = run_command(["git", "status", "--porcelain"], repository_root)
    try:
        verify_source_tree_clean(porcelain)
    except RuntimeSecurityError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCE_TREE_DIRTY", error.message
        ) from None


def _load_research_window(run_dir: Path, target_date: str) -> dict[str, Any]:
    path = run_dir / "research_window.json"
    if not _is_regular_file(path):
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RESEARCH_WINDOW_INVALID",
            f"canonical research window not found: {path}",
        )
    try:
        payload = load_json_document(path, RESEARCH_WINDOW_SCHEMA_NAME)
    except (ValueError, OSError) as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RESEARCH_WINDOW_INVALID",
            f"{path} could not be loaded: {error}",
        ) from None
    if payload.get("target_date") != target_date:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_RESEARCH_WINDOW_INVALID",
            f"{path} carries target_date {payload.get('target_date')!r}, "
            f"not {target_date!r}",
        )
    return payload


def _verify_no_downstream_artifact(run_dir: Path) -> None:
    """Fail closed on any Business Artifact produced after Discovery.

    Nothing is ever deleted, and no deletion is ever suggested: a run that
    already went past Discovery has a Trust Chain built on the old Discovery
    values, and silently re-basing that chain is precisely the failure this
    contract exists to prevent.
    """
    for entry in sorted(run_dir.iterdir(), key=lambda item: item.name):
        name = entry.name
        if entry.is_symlink():
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT",
                f"run directory holds a symbolic link, which is never followed: {name}",
            )
        if name not in ALLOWED_PRE_DISCOVERY_ENTRIES:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT",
                f"{name} already exists: the pipeline ran past Discovery, so "
                "Discovery evidence must not be corrected underneath it "
                "(nothing is deleted to make room)",
            )


def _market_research_before(run_dir: Path) -> str | None:
    """Hash ``market_research.json`` if present; refuse if Discovery passed.

    The recovery never writes this artifact. A schema-valid one that already
    records a completed Discovery means the run does not need recovering.
    """
    path = run_dir / "market_research.json"
    if not _is_regular_file(path):
        return None
    raw = path.read_bytes()
    try:
        payload = load_json_document(path, MARKET_RESEARCH_SCHEMA_NAME)
    except (ValueError, OSError, json.JSONDecodeError):
        # A schema-invalid remnant (e.g. the 2026-08-27 CLI result summary
        # that overwrote the artifact) neither proves nor disproves that
        # Discovery completed. It is evidence, so it is hashed and left
        # exactly as it is.
        return sha256_hex(raw)
    if payload.get("overall_status") != "DISCOVERY_INCOMPLETE":
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_DISCOVERY_ALREADY_COMPLETE",
            f"{path} reports overall_status "
            f"{payload.get('overall_status')!r}: Discovery already passed, so "
            "there is nothing to recover",
        )
    return sha256_hex(raw)


def _load_sources(run_dir: Path, target_date: str) -> tuple[bytes, dict[str, Any]]:
    path = run_dir / "sources.json"
    if not _is_regular_file(path):
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
            f"source ledger not found: {path}",
        )
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
            f"{path} is not valid JSON: {error}",
        ) from None
    if not isinstance(payload, dict):
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
            f"{path} is not a JSON object",
        )
    try:
        validate_json_document(payload, SOURCES_SCHEMA_NAME)
    except ValueError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
            f"{path} failed schema validation: {error}",
        ) from None
    if payload.get("target_date") != target_date:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
            f"{path} carries target_date {payload.get('target_date')!r}, "
            f"not {target_date!r}",
        )
    return raw, payload


def _discovery_attempt_indexes(
    ledger: dict[str, Any],
    *,
    target_date: str,
    research_cutoff: str,
) -> dict[str, int]:
    """Exactly one GLOBAL Logical Attempt per Discovery source, by index."""
    indexes: dict[str, list[int]] = {source_id: [] for source_id in DISCOVERY_SOURCE_IDS}
    for index, attempt in enumerate(ledger.get("source_attempts", [])):
        source_id = str(attempt.get("source_id"))
        if source_id in indexes:
            indexes[source_id].append(index)

    resolved: dict[str, int] = {}
    for source_id in DISCOVERY_SOURCE_IDS:
        found = indexes[source_id]
        if len(found) != 1:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_ATTEMPT_SET_INVALID",
                f"{source_id}: expected exactly 1 Logical Attempt, found "
                f"{len(found)}",
            )
        attempt = ledger["source_attempts"][found[0]]
        if attempt.get("candidate_code") is not None:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_ATTEMPT_SET_INVALID",
                f"{source_id}: Discovery is a GLOBAL source, so candidate_code "
                f"must be null, not {attempt.get('candidate_code')!r}",
            )
        if attempt.get("target_date") != target_date:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
                f"{source_id}: attempt target_date {attempt.get('target_date')!r} "
                f"does not match the canonical research window {target_date!r}",
            )
        if attempt.get("research_cutoff") != research_cutoff:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
                f"{source_id}: attempt research_cutoff "
                f"{attempt.get('research_cutoff')!r} does not match the canonical "
                f"research window {research_cutoff!r}",
            )
        resolved[source_id] = found[0]
    return resolved


def _verified_request_record(run_dir: Path, attempt: dict[str, Any]) -> dict[str, Any]:
    """The COMPLETED, FOUND Physical Request Record backing this Attempt.

    Read-only: the record is never written, renamed, deleted or re-issued.
    """
    source_id = attempt.get("source_id")
    request_id = attempt.get("request_id")
    if not request_id:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID",
            f"{source_id}: Logical Attempt carries no request_id, so there is "
            "no Physical Request evidence to reparse",
        )
    try:
        record = load_request_record(run_dir, str(request_id))
    except RequestBudgetError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID",
            f"{source_id}: {error.code}: {error.message}",
        ) from None
    if record is None:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID",
            f"{source_id}: no Physical Request Record on disk for "
            f"{request_id!r}",
        )
    if record.get("state") != "COMPLETED":
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_REQUEST_NOT_COMPLETED",
            f"{source_id}: request {request_id} is in state "
            f"{record.get('state')!r}; a recovery never completes or retries a "
            "reservation",
        )
    if record.get("source_status") != "FOUND":
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_REQUEST_NOT_FOUND",
            f"{source_id}: request {request_id} recorded source_status "
            f"{record.get('source_status')!r}; a Parser fix cannot turn a "
            "failed HTTP acquisition into FOUND",
        )

    for field_name in ("url", "target_date", "research_cutoff", "request_id"):
        if record.get(field_name) != attempt.get(field_name):
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_MISMATCH",
                f"{source_id}: {field_name} differs between the Logical Attempt "
                f"({attempt.get(field_name)!r}) and its Physical Request Record "
                f"({record.get(field_name)!r})",
            )
    for field_name in EVIDENCE_CROSS_CHECK_FIELDS:
        if record.get(field_name) != attempt.get(field_name):
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_MISMATCH",
                f"{source_id}: {field_name} differs between the Logical Attempt "
                f"({attempt.get(field_name)!r}) and its Physical Request Record "
                f"({record.get(field_name)!r})",
            )
    for field_name in (
        "source_page_path",
        "source_page_sha256",
        "source_page_size_bytes",
    ):
        if record.get(field_name) in (None, ""):
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_MISMATCH",
                f"{source_id}: request {request_id} carries no {field_name}, so "
                "there is no stored Raw Evidence to reparse",
            )
    return record


def _request_record_bytes(run_dir: Path, request_id: str) -> bytes:
    """The Physical Request Record file's raw bytes, read-only."""
    path = network_request_path(run_dir, request_id)
    try:
        return path.read_bytes()
    except OSError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID",
            f"request record {path} could not be read: {error}",
        ) from None


def _verified_raw_page(run_dir: Path, record: dict[str, Any]) -> bytes:
    """Re-read stored raw bytes and re-verify hash *and* recorded size."""
    page_path = str(record["source_page_path"])
    expected_sha = str(record["source_page_sha256"])
    try:
        raw = verify_source_page(run_dir, page_path, expected_sha)
    except SourceFetchError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCE_PAGE_INVALID",
            f"{error.code}: {error.message}",
        ) from None
    expected_size = record["source_page_size_bytes"]
    if len(raw) != expected_size:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCE_PAGE_INVALID",
            f"{page_path}: stored page is {len(raw)} bytes but the Physical "
            f"Request Record says {expected_size}",
        )
    return raw


def _source_definition(
    source_matrix: dict[str, Any], source_id: str
) -> tuple[dict[str, Any], str]:
    definitions = source_by_id(source_matrix)
    definition = definitions.get(source_id)
    if definition is None:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_PARSER_BINDING_INVALID",
            f"{source_id} is not defined in the Source Matrix",
        )
    try:
        parser_id = verify_source_parser_binding(definition)
    except ParserRegistryError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_PARSER_BINDING_INVALID",
            f"{error.code}: {error.message}",
        ) from None
    return definition, parser_id


def _reparse(
    raw: bytes,
    definition: dict[str, Any],
    *,
    source_id: str,
    trading_date: str,
    content_type: Any,
) -> Any:
    """Run the *current* deterministic parser over already-stored bytes."""
    try:
        return parse_source_page(
            raw,
            definition,
            ParseContext(
                source_id=source_id,
                trading_date=trading_date,
                # Discovery is GLOBAL: no ticker is ever supplied by a human
                # or an agent, and the ranking parsers do not take one.
                ticker=None,
                content_type=content_type,
                previous_trading_date=None,
            ),
        )
    except ParserRegistryError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_PARSER_BINDING_INVALID",
            f"{error.code}: {error.message}",
        ) from None


#: Attempt fields that are *derived from a parse* rather than from the wire.
#: A recovery re-derives the whole set from the current Parse Result, so the
#: previous parser's leftovers must be cleared first -- a stale
#: ``PARSE_FAILED`` note or a stale ``coverage_status`` next to freshly
#: parsed values would describe a parse that no longer exists.
#:
#: ``notes`` is safe to clear wholesale here precisely because this recovery
#: only ever runs against a Physical Request Record whose ``source_status``
#: is ``FOUND``: :mod:`src.source_fetch` attaches notes only to non-FOUND
#: transport outcomes, so every note such an Attempt carries came from the
#: parser.
STALE_PARSER_DERIVED_FIELDS: tuple[str, ...] = (
    "coverage_status",
    "coverage_start",
    "coverage_end",
    "covered_dates",
)


def _cleared_for_reparse(attempt: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``attempt`` with every parser-derived field reset.

    Identity and physical fields are untouched: only what a parser produced
    is cleared, so the current Parse Result is applied to a clean slate
    instead of being merged into the previous parser's output.
    """
    cleared = copy.deepcopy(attempt)
    cleared["notes"] = []
    cleared["values"] = None
    cleared["result_count"] = None
    for field_name in STALE_PARSER_DERIVED_FIELDS:
        cleared.pop(field_name, None)
    return cleared


def _verify_identity_unchanged(
    before: dict[str, Any], after: dict[str, Any], source_id: str
) -> None:
    changed = [
        field_name
        for field_name in IMMUTABLE_ATTEMPT_FIELDS
        if before.get(field_name) != after.get(field_name)
    ]
    if changed:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_IDENTITY_VIOLATION",
            f"{source_id}: recovery changed Logical Attempt identity/physical "
            "fields, which is never permitted: " + ", ".join(changed),
        )


def _attempt_field_value(attempt: dict[str, Any], field_name: str) -> Any:
    for value in attempt.get("values") or []:
        if isinstance(value, dict) and value.get("field_name") == field_name:
            return value.get("value")
    return None


def _count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _rebuild_sources(
    ledger: dict[str, Any],
    *,
    corrected: dict[str, dict[str, Any]],
    indexes: dict[str, int],
    ledger_values: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """The new ledger payload: only the two target Attempts change.

    Every other Attempt and Source Value is carried across unchanged, in its
    existing order. Source Ledger Values keep the existing
    ``<attempt_id>#<field_name>`` reference scheme -- no new id space is
    invented -- so a value that exists both before and after is replaced in
    place rather than appended.
    """
    payload = copy.deepcopy(ledger)

    attempts = list(payload.get("source_attempts", []))
    target_attempt_ids: set[str] = set()
    for source_id, index in indexes.items():
        attempts[index] = corrected[source_id]
        target_attempt_ids.add(str(corrected[source_id]["attempt_id"]))
    payload["source_attempts"] = attempts

    replacements: dict[str, dict[str, Any]] = {}
    for values in ledger_values.values():
        for value in values:
            replacements[str(value["source_ref"])] = value

    prefixes = tuple(f"{attempt_id}#" for attempt_id in sorted(target_attempt_ids))
    rebuilt: list[dict[str, Any]] = []
    used: set[str] = set()
    for value in payload.get("sources", []):
        source_ref = str(value.get("source_ref"))
        if not source_ref.startswith(prefixes):
            rebuilt.append(value)
            continue
        replacement = replacements.get(source_ref)
        if replacement is None:
            # A Source Value the current parser no longer produces. It is
            # dropped, never kept alongside the corrected values: a stale
            # value whose Attempt has been reparsed is not evidence of
            # anything.
            continue
        rebuilt.append(replacement)
        used.add(source_ref)
    for source_ref, value in replacements.items():
        if source_ref not in used:
            rebuilt.append(value)
    payload["sources"] = rebuilt
    return payload


# ----------------------------------------------------------- audit sidecar ---


def audit_path_for(run_dir: Path, git_head_sha: str) -> Path:
    return Path(run_dir) / WORKING_DIRNAME / AUDIT_DIRNAME / f"{git_head_sha}.json"


def _audit_bytes(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _prepare_audit_destination(run_dir: Path, git_head_sha: str) -> Path:
    """Validate -- and make usable -- the audit destination *before* commit.

    Two things are settled here, while the Business Artifact is still
    untouched:

    1. **Containment.** Every path component from the run directory down to
       the audit directory is inspected with ``lstat`` and a symbolic link is
       refused, never followed. A ``working`` or
       ``working/production_discovery_reparse`` symlink pointing outside the
       run would otherwise let this recovery drop a file anywhere on the
       machine. The resolved parent must still be inside
       ``<run-dir>/working``.
    2. **Writability.** The directory is created and probed now, so a
       destination that cannot be written is a failure *before* sources.json
       changes rather than after it.
    """
    run_dir = Path(run_dir)
    working_dir = run_dir / WORKING_DIRNAME
    audit_dir = working_dir / AUDIT_DIRNAME

    for path in (working_dir, audit_dir):
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
                f"{path} could not be inspected: {error}",
            ) from None
        if stat.S_ISLNK(mode):
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
                f"{path} is a symbolic link, which is never followed",
            )
        if not stat.S_ISDIR(mode):
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
                f"{path} exists but is not a directory",
            )

    audit_path = audit_dir / f"{git_head_sha}.json"
    if audit_path.is_symlink():
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
            f"{audit_path} is a symbolic link, which is never followed",
        )

    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
            f"{audit_dir} could not be created: {error}",
        ) from None

    # Containment is re-checked against the *resolved* directory, after
    # creation: a component that resolves outside <run-dir>/working must
    # never receive a file, however it got there.
    resolved_working = working_dir.resolve()
    try:
        audit_dir.resolve().relative_to(resolved_working)
        resolved_working.relative_to(run_dir.resolve())
    except ValueError:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
            f"{audit_dir} resolves outside {working_dir}",
        ) from None

    probe = audit_dir / f".{git_head_sha}.json.probe"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID",
            f"{audit_dir} is not writable: {error}",
        ) from None
    finally:
        try:
            probe.unlink()
        except OSError:  # pragma: no cover - defensive
            pass
    return audit_path


#: Fields whose value legitimately differs between the recovery that changed
#: the ledger and a later idempotent re-verification of the same corrected
#: ledger. Everything else must match exactly.
_REPLAY_VARIABLE_KEYS = ("sources_before_sha256", "market_research_before_sha256")
_REPLAY_VARIABLE_ATTEMPT_KEYS = (
    "before_status",
    "before_result_count",
    "before_ranking_ticker_count",
    "before_ranking_row_count",
)


def _is_idempotent_replay(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """True when ``candidate`` re-derives exactly what ``existing`` recorded.

    A second run of the recovery against an already-corrected ledger reads a
    "before" state that is the first run's "after" state, so byte equality of
    the two audits is not the right test. What must hold is that nothing
    would change: the candidate's before-state already equals its own
    after-state, that state is the one the stored audit recorded, and every
    identity/evidence field agrees.
    """
    if candidate["sources_before_sha256"] != candidate["sources_after_sha256"]:
        return False
    if existing.get("sources_after_sha256") != candidate["sources_after_sha256"]:
        return False
    for key in candidate:
        if key in _REPLAY_VARIABLE_KEYS or key == "attempts":
            continue
        if existing.get(key) != candidate[key]:
            return False

    existing_attempts = existing.get("attempts")
    if not isinstance(existing_attempts, list):
        return False
    if len(existing_attempts) != len(candidate["attempts"]):
        return False
    for stored, fresh in zip(existing_attempts, candidate["attempts"]):
        for key in fresh:
            if key in _REPLAY_VARIABLE_ATTEMPT_KEYS:
                continue
            if stored.get(key) != fresh[key]:
                return False
        # Nothing left to correct: the candidate's own before-state already
        # is its after-state.
        if fresh["before_status"] != fresh["after_status"]:
            return False
        if fresh["before_result_count"] != fresh["after_result_count"]:
            return False
        if fresh["before_ranking_ticker_count"] != fresh["after_ranking_ticker_count"]:
            return False
        if fresh["before_ranking_row_count"] != fresh["after_ranking_row_count"]:
            return False
    return True


def _rollback_sources(
    run_dir: Path,
    original_raw: bytes,
    original_sha256: str,
    cause: ProductionDiscoveryReparseError,
) -> None:
    """Put ``sources.json`` back to the exact bytes the recovery started from.

    Called only when the commit failed after the ledger was already written.
    The restoration is verified by re-reading and re-hashing; if *that* fails
    the run is in a state no automatic action can fix, so it is reported as
    such rather than left to look like an ordinary failure.
    """
    path = run_dir / "sources.json"
    try:
        atomic_write_text(path, original_raw.decode("utf-8"))
        restored = path.read_bytes()
    except (OSError, UnicodeDecodeError) as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_ROLLBACK_FAILED",
            f"{cause.code}: {cause.message}; and sources.json could NOT be "
            f"restored to its pre-recovery bytes ({error}). The run directory "
            "needs human inspection: expected SHA256 "
            f"{original_sha256}",
        ) from None
    if sha256_hex(restored) != original_sha256:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_ROLLBACK_FAILED",
            f"{cause.code}: {cause.message}; and the restored sources.json does "
            f"not match its pre-recovery SHA256 {original_sha256}. The run "
            "directory needs human inspection.",
        )


# ------------------------------------------------------------- operation ---


def reparse_production_discovery(
    target_date: str,
    *,
    daytrade_root: str | Path = DAYTRADE_ROOT,
    repository_root: str | Path | None = None,
    source_matrix_path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
    run_command: Callable[[list[str], Path], str] = default_run_command,
) -> dict[str, Any]:
    """Reparse a stopped run's stored Discovery evidence. HUMAN-ONLY.

    Returns the machine-readable result dict. Raises
    :class:`ProductionDiscoveryReparseError` -- always before a single byte of
    any Business Artifact has been written -- on every contract violation.
    """
    target_date = _validated_target_date(target_date)
    daytrade_root = Path(daytrade_root)
    repository_root = (
        Path(repository_root) if repository_root is not None else daytrade_root.parent
    )

    # ---- 1-2. inputs and their before-state ------------------------------
    run_dir = _resolve_run_dir(daytrade_root, target_date)
    runtime_security = _load_runtime_security(run_dir, target_date)
    git_head_sha = str(runtime_security["git_head_sha"])
    _verify_local_git_state(
        attested_head=git_head_sha,
        repository_root=repository_root,
        run_command=run_command,
    )
    research_window = _load_research_window(run_dir, target_date)
    research_cutoff = str(research_window["research_cutoff"])
    previous_trading_day = str(research_window["previous_trading_day"])

    _verify_no_downstream_artifact(run_dir)
    market_research_before_sha256 = _market_research_before(run_dir)

    sources_raw_before, ledger = _load_sources(run_dir, target_date)
    sources_before_sha256 = sha256_hex(sources_raw_before)
    indexes = _discovery_attempt_indexes(
        ledger, target_date=target_date, research_cutoff=research_cutoff
    )

    source_matrix = load_source_matrix(source_matrix_path)

    # ---- 3-6. verify all evidence, then reparse, entirely in memory ------
    corrected: dict[str, dict[str, Any]] = {}
    ledger_values: dict[str, list[dict[str, Any]]] = {}
    audit_attempts: list[dict[str, Any]] = []
    verified_pages: dict[str, bytes] = {}
    verified_records: dict[str, bytes] = {}

    for source_id in DISCOVERY_SOURCE_IDS:
        original = ledger["source_attempts"][indexes[source_id]]
        record = _verified_request_record(run_dir, original)
        raw = _verified_raw_page(run_dir, record)
        # The *whole* Request Record, as raw bytes -- not just the fields the
        # cross-check reads. Anything at all changing in that file while the
        # recovery runs must be caught, including fields this module never
        # looks at (origin_*, reserved_at, ...).
        verified_records[source_id] = _request_record_bytes(
            run_dir, str(record["request_id"])
        )
        verified_pages[source_id] = raw
        definition, parser_id = _source_definition(source_matrix, source_id)
        parsed = _reparse(
            raw,
            definition,
            source_id=source_id,
            trading_date=previous_trading_day,
            content_type=record.get("content_type"),
        )

        attempt = _cleared_for_reparse(original)
        attempt, values = apply_parse_result_to_attempt(
            attempt, parsed, source_id, definition
        )
        _verify_identity_unchanged(original, attempt, source_id)
        corrected[source_id] = attempt
        ledger_values[source_id] = values

        audit_attempts.append(
            {
                "source_id": source_id,
                "parser_id": parser_id,
                "attempt_id": str(original["attempt_id"]),
                "request_id": str(record["request_id"]),
                "source_page_path": str(record["source_page_path"]),
                "source_page_sha256": str(record["source_page_sha256"]),
                "source_page_size_bytes": int(record["source_page_size_bytes"]),
                "before_status": str(original.get("status")),
                "after_status": str(attempt.get("status")),
                "before_result_count": original.get("result_count"),
                "after_result_count": attempt.get("result_count"),
                "before_ranking_ticker_count": _count(
                    _attempt_field_value(original, "ranking_tickers")
                ),
                "after_ranking_ticker_count": _count(
                    _attempt_field_value(attempt, "ranking_tickers")
                ),
                "before_ranking_row_count": _count(
                    _attempt_field_value(original, "ranking_rows")
                ),
                "after_ranking_row_count": _count(
                    _attempt_field_value(attempt, "ranking_rows")
                ),
            }
        )

    # ---- 6. Discovery must actually be OPEN under the current parser -----
    #
    # Decided with the canonical Discovery logic itself (build_discovery_routes
    # / confirm_discovery_top50), never with a second, parallel notion of
    # "looks complete enough". A partial ranking is not accepted at any count.
    result = AcquisitionResult(
        stage=STAGE,
        target_date=target_date,
        research_cutoff=research_cutoff,
        attempts=[corrected[source_id] for source_id in DISCOVERY_SOURCE_IDS],
    )
    try:
        routes = build_discovery_routes(result)
    except StageWiringError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_STILL_INCOMPLETE",
            f"{error.code}: {error.message}",
        ) from None
    incomplete = confirm_discovery_top50(routes)
    if incomplete:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_STILL_INCOMPLETE",
            "the current parser still cannot confirm a full TOP50 from the "
            f"stored evidence ({', '.join(incomplete)}); nothing was written",
        )

    # ---- 7-8. build and validate the new payload, still in memory --------
    payload = _rebuild_sources(
        ledger,
        corrected=corrected,
        indexes=indexes,
        ledger_values=ledger_values,
    )
    try:
        validate_json_document(payload, SOURCES_SCHEMA_NAME)
    except ValueError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID",
            f"the corrected source ledger failed schema validation: {error}",
        ) from None

    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    sources_after_sha256 = sha256_hex(serialized.encode("utf-8"))

    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "target_date": target_date,
        "stage": STAGE,
        "result": RESULT_REPARSED,
        "git_head_sha": git_head_sha,
        "research_cutoff": research_cutoff,
        "previous_trading_day": previous_trading_day,
        "sources_before_sha256": sources_before_sha256,
        "sources_after_sha256": sources_after_sha256,
        "market_research_before_sha256": market_research_before_sha256,
        # Structural, not observed: no code path in this module can perform a
        # network request at all.
        "network_request_count": 0,
        "attempts": audit_attempts,
    }
    validate_json_document(audit, AUDIT_SCHEMA_NAME)

    # ---- 9-10. re-confirm nothing moved underneath the recovery ----------
    for source_id in DISCOVERY_SOURCE_IDS:
        _verify_identity_unchanged(
            ledger["source_attempts"][indexes[source_id]],
            corrected[source_id],
            source_id,
        )
        recheck = _verified_request_record(run_dir, corrected[source_id])
        if (
            _request_record_bytes(run_dir, str(recheck["request_id"]))
            != verified_records[source_id]
        ):
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_CHANGED_DURING_RECOVERY",
                f"{source_id}: the Physical Request Record's raw bytes changed "
                "while the recovery was running; nothing was written",
            )
        if _verified_raw_page(run_dir, recheck) != verified_pages[source_id]:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_CHANGED_DURING_RECOVERY",
                f"{source_id}: the stored Raw Page changed while the recovery "
                "was running; nothing was written",
            )
    current_raw = (run_dir / "sources.json").read_bytes()
    if sha256_hex(current_raw) != sources_before_sha256:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_CHANGED_DURING_RECOVERY",
            "sources.json changed while the recovery was running; nothing was "
            "written",
        )

    # ---- audit destination + idempotency, before anything is written -----
    #
    # The audit is part of the commit, not an afterthought: a recovery that
    # changed sources.json but left no evidence of having done so is exactly
    # the state this contract must never produce. So the destination is
    # validated, created and probed here, while the Business Artifact is
    # still untouched.
    audit_path = _prepare_audit_destination(run_dir, git_head_sha)
    if _is_regular_file(audit_path):
        try:
            existing_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_AUDIT_CONFLICT",
                f"{audit_path} exists but is not valid JSON ({error}); it is "
                "never overwritten",
            ) from None
        if existing_audit == audit or _is_idempotent_replay(existing_audit, audit):
            return {
                "result": RESULT_ALREADY_REPARSED,
                "target_date": target_date,
                "stage": STAGE,
                "network_request_count": 0,
                "source_count": len(DISCOVERY_SOURCE_IDS),
                "audit_path": str(audit_path),
            }
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_AUDIT_CONFLICT",
            f"{audit_path} already records a different recovery for this git "
            "HEAD; it is never overwritten and there is no --force",
        )

    # ---- 11-13. the commit: ledger + audit, all-or-nothing ---------------
    #
    # Committing the ledger and finalising the audit is one transaction. If
    # any step after the ledger write fails -- the read-back, the audit write
    # -- sources.json is restored to the exact bytes the recovery started
    # from and that restoration is itself verified. A failed recovery
    # (exit 2) must leave the run byte-identical to how it was found.
    try:
        atomic_write_text(run_dir / "sources.json", serialized)
    except OSError as error:
        raise _fail(
            "PRODUCTION_DISCOVERY_REPARSE_WRITE_FAILED",
            f"could not write the corrected source ledger: {error}",
        ) from None

    try:
        written_raw, written = _load_sources(run_dir, target_date)
        if sha256_hex(written_raw) != sources_after_sha256 or written != payload:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_WRITE_FAILED",
                "the corrected source ledger on disk does not match what was built",
            )
        try:
            atomic_write_text(audit_path, _audit_bytes(audit))
        except OSError as error:
            raise _fail(
                "PRODUCTION_DISCOVERY_REPARSE_AUDIT_WRITE_FAILED",
                f"the corrected ledger could not be evidenced at {audit_path}: "
                f"{error}",
            ) from None
    except ProductionDiscoveryReparseError as error:
        _rollback_sources(run_dir, sources_raw_before, sources_before_sha256, error)
        raise

    return {
        "result": RESULT_REPARSED,
        "target_date": target_date,
        "stage": STAGE,
        "network_request_count": 0,
        "source_count": len(DISCOVERY_SOURCE_IDS),
        "audit_path": str(audit_path),
    }


# ------------------------------------------------------------ entry point ---


def reparse_main(argv: list[str] | None = None, **overrides: Any) -> int:
    """HUMAN-ONLY entry point for ``scripts/reparse-production-discovery``.

    ``overrides`` exists for the tests only: the script itself calls this with
    no arguments at all, so the operator's single input stays
    ``--target-date``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "HUMAN-ONLY offline recovery: reparse one stopped Production run's "
            "already-stored Discovery evidence with the current deterministic "
            "parser. No network access, no retry, no Physical Request or Raw "
            "Page mutation."
        )
    )
    parser.add_argument("--target-date", required=True)
    args = parser.parse_args(argv)
    try:
        result = reparse_production_discovery(args.target_date, **overrides)
    except (
        ProductionDiscoveryReparseError,
        RuntimeSecurityError,
        StageWiringError,
        ValueError,
        OSError,
    ) as error:
        code = getattr(error, "code", "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID")
        if not str(code).startswith(ERROR_PREFIX):
            error = ProductionDiscoveryReparseError(
                "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID", str(error)
            )
        sys.stderr.write(f"{error}\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


__all__ = [
    "ALLOWED_PRE_DISCOVERY_ENTRIES",
    "AUDIT_DIRNAME",
    "AUDIT_SCHEMA_NAME",
    "AUDIT_SCHEMA_VERSION",
    "DAYTRADE_ROOT",
    "DISCOVERY_SOURCE_IDS",
    "ERROR_CODES",
    "IMMUTABLE_ATTEMPT_FIELDS",
    "PROTECTED_TREE_PREFIXES",
    "ProductionDiscoveryReparseError",
    "REPOSITORY_ROOT",
    "RESULT_ALREADY_REPARSED",
    "RESULT_REPARSED",
    "STAGE",
    "audit_path_for",
    "default_run_command",
    "reparse_main",
    "reparse_production_discovery",
]
