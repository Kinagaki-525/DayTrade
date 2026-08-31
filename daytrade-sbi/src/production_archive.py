"""Production Run Archive Contract.

A Production Nightly Run leaves its evidence in ``runs/<target-date>/``. That
directory is *operational*: the next run's ``git status`` sees it, a human may
tidy it, and a crashed pipeline leaves it half-written. This module seals a
byte-exact, hash-manifested **copy** of it outside the repository, so the
evidence survives whatever happens to the working tree.

What this module is NOT:

* it is not a business verifier -- Business Validation is
  :func:`src.production_verify.verify_production_run`, reused as-is;
* it is not a backup -- the archive lives on the same machine (see
  ``docs/production-run-archive.md``);
* it is not part of the nightly -- both entry points are human-only scripts,
  never ``src.cli`` subcommands, so neither can appear in the Canonical CLI
  Pipeline Order.

Contract highlights (the full text is ``docs/production-run-archive.md``):

* the Operational Run is **read-only source**: nothing in it is written,
  renamed, normalised, re-serialised or deleted;
* every file is copied as **raw bytes** and re-hashed from the destination;
* the archive is built in ``.staging/`` and moved into place with a single
  atomic ``os.replace``; a partially built archive is never visible under
  ``runs/``;
* an existing archive is never overwritten -- an identical source is
  ``ALREADY_ARCHIVED``, a diverged source is a hard error;
* ``working/`` is a Non-Business Sidecar: whatever it holds is archived as raw
  bytes, and never as part of the Business Artifact chain;
* an INCOMPLETE business run is still archived. Losing the evidence of a run
  that stopped early is strictly worse than storing it.

There is no network access, no git invocation, no retention/deletion and no
``--force`` anywhere in this module, by contract.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from src.contracts import load_json_document, validate_json_document
from src.downstream_trust import sha256_file_bytes
from src.production_context import (
    DEFAULT_ISSUER_REGISTRY_PATH,
    ProductionContextError,
    sha256_bytes,
    validate_target_date,
)
from src.production_verify import INVALID_RUN, VERIFIED_STATUSES, verify_production_run
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH


# --------------------------------------------------------------- layout ---

#: ``daytrade-sbi/`` -- derived exactly like every other module's root.
DAYTRADE_ROOT = Path(__file__).resolve().parents[1]
#: The git repository root.
REPOSITORY_ROOT = DAYTRADE_ROOT.parent
#: The Archive Root: a sibling of the repository, outside any git work tree.
#: Fixed by contract -- the CLI cannot move it.
ARCHIVE_ROOT = REPOSITORY_ROOT.parent / "daytrade-production-archive"

#: DTWO-2026-026. Two manifest generations coexist, and they mean different
#: things. **v1** is historical: it was written while a Runtime Security
#: Attestation was part of what a complete run meant, and its stored bytes are
#: never rewritten, re-sealed or migrated -- they are read back under exactly
#: the contract they were written under. **v2** is what every new archive gets:
#: completeness is decided by the Business Verification alone, because that is
#: what says whether the market evidence and the trading decision hold up.
ARCHIVE_VERSION = "production-run-archive-v1"
ARCHIVE_VERSION_V2 = "production-run-archive-v2"
MANIFEST_SCHEMA_NAME = "production_archive_manifest.schema.json"
MANIFEST_SCHEMA_NAME_V2 = "production_archive_manifest_v2.schema.json"

#: schema_version -> the schema that generation is read under. Each schema
#: ``const``-pins its own ``archive_version``, so the pair cannot drift.
MANIFEST_SCHEMAS = {
    1: MANIFEST_SCHEMA_NAME,
    2: MANIFEST_SCHEMA_NAME_V2,
}

#: The generation new archives are written in.
CURRENT_SCHEMA_VERSION = 2

#: Legacy read contract only: the schema of the v1 Runtime Security
#: Attestation. No new run produces this artifact, and nothing outside v1
#: verification reads it.
RUNTIME_SECURITY_SCHEMA_NAME = "runtime_security.schema.json"

MANIFEST_NAME = "archive_manifest.json"
MANIFEST_SHA_NAME = "archive_manifest.sha256"

#: The three sealed subtrees of a per-run archive. ``files[]`` covers exactly
#: these; the manifest and its digest are deliberately not self-listed.
ARCHIVE_SUBTREES = ("run", "inputs", "verification")

SOURCE_MATRIX_ARCHIVE_PATH = "inputs/source_matrix.yaml"
ISSUER_REGISTRY_ARCHIVE_PATH = "inputs/issuer_domain_registry.yaml"
VERIFICATION_REPORT_PATH = "verification/production_verify.json"
RUNTIME_SECURITY_ARCHIVE_PATH = "run/working/runtime_security.json"

#: Sealed modes. Tamper *detection* is the manifest; these modes are ordinary
#: mistake prevention, not a cryptographic immutability guarantee.
SEALED_FILE_MODE = 0o444
SEALED_DIR_MODE = 0o555
REGISTRY_FILE_MODE = 0o444

ARCHIVE_STATUS_COMPLETE_VERIFIED = "COMPLETE_VERIFIED"
ARCHIVE_STATUS_INCOMPLETE = "INCOMPLETE"

RUNTIME_SECURITY_VALID = "VALID"
RUNTIME_SECURITY_MISSING = "MISSING"
RUNTIME_SECURITY_INVALID = "INVALID"

RESULT_ARCHIVED = "ARCHIVED"
RESULT_ALREADY_ARCHIVED = "ALREADY_ARCHIVED"
RESULT_ARCHIVE_VERIFIED = "ARCHIVE_VERIFIED"

ERROR_CODES = (
    "PRODUCTION_ARCHIVE_TARGET_DATE_INVALID",
    "PRODUCTION_ARCHIVE_SOURCE_MISSING",
    "PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY",
    "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY",
    "PRODUCTION_ARCHIVE_STAGING_EXISTS",
    "PRODUCTION_ARCHIVE_EXISTS_INVALID",
    "PRODUCTION_ARCHIVE_SOURCE_DIVERGED",
    "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
    "PRODUCTION_ARCHIVE_HASH_MISMATCH",
    "PRODUCTION_ARCHIVE_MISSING_FILE",
    "PRODUCTION_ARCHIVE_EXTRA_FILE",
    "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH",
    "PRODUCTION_ARCHIVE_FINALIZE_FAILED",
)


class ProductionArchiveError(RuntimeError):
    """A fail-closed Production Run Archive contract violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> ProductionArchiveError:
    return ProductionArchiveError(code, message)


def _utc_now() -> _datetime.datetime:
    return _datetime.datetime.now(_datetime.timezone.utc)


# ------------------------------------------------------------- scanning ---


def _is_regular_file(path: Path) -> bool:
    """True only for a real regular file -- symlinks are never followed."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def scan_tree(root: Path) -> tuple[str, ...]:
    """Return every regular file under ``root`` as a sorted relative POSIX path.

    Fail-closed on anything that is not a real directory or a real regular
    file: a symlink (never followed), FIFO, socket or device node aborts the
    whole scan with ``PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY``. A partial
    archive is never produced from an unsafe tree.
    """
    root = Path(root)
    if root.is_symlink():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY",
            f"tree root is a symbolic link: {root}",
        )
    if not root.is_dir():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY", f"not a directory: {root}"
        )

    found: list[str] = []
    pending: list[Path] = [root]
    while pending:
        directory = pending.pop()
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            mode = os.lstat(entry).st_mode
            if stat.S_ISLNK(mode):
                raise _fail(
                    "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY",
                    f"symbolic link is not archivable: {entry}",
                )
            if stat.S_ISDIR(mode):
                pending.append(entry)
                continue
            if not stat.S_ISREG(mode):
                raise _fail(
                    "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY",
                    f"not a regular file: {entry}",
                )
            found.append(entry.relative_to(root).as_posix())
    return tuple(sorted(found))


def _tree_digest(root: Path, relative_paths: Iterable[str]) -> dict[str, tuple[int, str]]:
    """Map each relative path onto ``(size_bytes, raw sha256)``."""
    digest: dict[str, tuple[int, str]] = {}
    for relative in relative_paths:
        path = root / relative
        payload = path.read_bytes()
        digest[relative] = (len(payload), sha256_bytes(payload))
    return digest


# ---------------------------------------------------------- permissions ---


def _seal_tree(root: Path, *, seal_root: bool = True) -> None:
    """Make a finished per-run archive read-only (files 0444, dirs 0555).

    Deepest first, so a directory is not made unwritable before the files
    inside it have been sealed.

    ``seal_root=False`` leaves ``root`` itself owner-writable. That matters for
    the staging directory: renaming a directory into a *different* parent
    updates its ``..`` entry, which the kernel refuses (EACCES) on a ``0555``
    directory. So staging is sealed inside-out, moved, and the final archive
    root is sealed afterwards.

    ``root`` is decided in exactly one place -- the walk never seals it -- so
    there is no unconditional trailing chmod to undo ``seal_root=False``.
    """
    root = Path(root)
    for directory, _dirnames, filenames in os.walk(root, topdown=False):
        directory_path = Path(directory)
        for name in filenames:
            os.chmod(directory_path / name, SEALED_FILE_MODE)
        if directory_path == root:
            continue
        os.chmod(directory_path, SEALED_DIR_MODE)
    if seal_root:
        os.chmod(root, SEALED_DIR_MODE)


def _force_rmtree(root: Path) -> None:
    """Remove a tree this process created, including sealed directories.

    Only ever called on **our own** staging directory. A pre-existing stale
    staging directory is reported, never deleted.
    """

    def _on_error(_func, path, _exc_info):  # pragma: no cover - defensive
        try:
            os.chmod(Path(path).parent, 0o700)
            os.chmod(path, 0o700)
        except OSError:
            return

    for directory, _dirnames, _filenames in os.walk(root, topdown=False):
        try:
            os.chmod(directory, 0o700)
        except OSError:  # pragma: no cover - defensive
            pass
    try:
        os.chmod(root, 0o700)
    except OSError:  # pragma: no cover - defensive
        pass
    shutil.rmtree(root, onerror=_on_error)


# ------------------------------------------------------------ JSON form ---


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """The repository's artifact JSON form: indent 2, sorted keys, LF end."""
    text = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, default=str
    )
    return (text + "\n").encode("utf-8")


# ------------------------------------------------------- input handling ---


def _validated_target_date(value: str) -> str:
    """Reuse the shared total target-date validator, re-coded for Archive."""
    try:
        return validate_target_date(value)
    except ProductionContextError as error:
        raise _fail(
            "PRODUCTION_ARCHIVE_TARGET_DATE_INVALID", error.message
        ) from None


def _operational_run_dir(daytrade_root: Path, target_date: str) -> Path:
    """``<daytrade_root>/runs/<target_date>``.

    ``target_date`` is already a validated ``YYYY-MM-DD``, so it cannot carry
    a path separator, ``.`` or ``..``; the join is therefore containment-safe
    without resolving (and thereby following) any symlink.
    """
    return Path(daytrade_root) / "runs" / target_date


# ----------------------------------------------- business verification ---


def _business_verification_report(
    run_path: Path, source_matrix_path: Path
) -> dict[str, Any]:
    """Run the existing Production Verifier over an archived run.

    The Business Validation logic is **not** reimplemented here: this calls
    ``verify_production_run`` and stores its report verbatim. An INCOMPLETE run
    makes the verifier report ``INVALID_RUN``; that is a business outcome, not
    an archive failure, so an unexpected exception from the verifier is
    likewise recorded as evidence instead of destroying the archive.
    """
    try:
        return verify_production_run(
            run_path, source_matrix_path=source_matrix_path
        ).as_dict()
    except Exception as exc:  # noqa: BLE001 - evidence beats a lost archive
        return {
            "status": INVALID_RUN,
            "run_dir": str(run_path),
            "checks": [],
            "errors": [f"verifier raised {type(exc).__name__}: {exc}"],
            "network_audit": {},
        }


def _runtime_security_evidence(
    archive_root_dir: Path, target_date: str
) -> dict[str, Any]:
    """Classify ``run/working/runtime_security.json`` as VALID/MISSING/INVALID.

    **Legacy v1 read path only.** No new run produces this attestation and no
    v2 manifest records it; this exists so that archives sealed before
    DTWO-2026-026 keep verifying under the contract they were written under.

    No field name is invented: ``git_head_sha`` is read only from a
    schema-valid attestation, and no git command is ever run to guess it.
    """
    path = archive_root_dir / RUNTIME_SECURITY_ARCHIVE_PATH
    if not _is_regular_file(path):
        return {
            "status": RUNTIME_SECURITY_MISSING,
            "path": None,
            "sha256": None,
            "git_head_sha": None,
        }

    sha256 = sha256_file_bytes(path)
    try:
        payload = load_json_document(path, RUNTIME_SECURITY_SCHEMA_NAME)
    except (ValueError, OSError, json.JSONDecodeError):
        payload = None

    if payload is None or payload.get("target_date") != target_date:
        return {
            "status": RUNTIME_SECURITY_INVALID,
            "path": RUNTIME_SECURITY_ARCHIVE_PATH,
            "sha256": sha256,
            "git_head_sha": None,
        }

    return {
        "status": RUNTIME_SECURITY_VALID,
        "path": RUNTIME_SECURITY_ARCHIVE_PATH,
        "sha256": sha256,
        "git_head_sha": payload["git_head_sha"],
    }


# ------------------------------------------- historical Source Matrix ---


def registry_dir(archive_root: Path) -> Path:
    return Path(archive_root) / "registries" / "source_matrix"


def store_source_matrix_in_registry(
    *, archive_root: Path, source_matrix_bytes: bytes, sha256: str
) -> Path:
    """Content-address the in-force Source Matrix into the historical registry.

    The layout is exactly the one
    ``src.selection_calibration.resolve_historical_source_matrix_path`` reads:
    ``<sha256-of-own-bytes>.yaml``. An existing entry is **never** overwritten
    or "repaired": matching bytes are reused, mismatching bytes are a hard
    error, because that file is the evidence a historical Trust Chain depends
    on.
    """
    directory = registry_dir(archive_root)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{sha256}.yaml"

    if destination.exists() or destination.is_symlink():
        if not _is_regular_file(destination):
            raise _fail(
                "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH",
                f"registry entry is not a regular file: {destination}",
            )
        actual = sha256_file_bytes(destination)
        if actual != sha256:
            raise _fail(
                "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH",
                f"registry file {destination} is named for SHA256 {sha256} "
                f"but its actual byte SHA256 is {actual}",
            )
        return destination

    handle, temporary_name = tempfile.mkstemp(dir=str(directory), prefix=".tmp-")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(source_matrix_bytes)
        os.replace(temporary, destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise _fail(
            "PRODUCTION_ARCHIVE_FINALIZE_FAILED",
            f"could not write registry entry {destination}: {error}",
        ) from None

    written = sha256_file_bytes(destination)
    if written != sha256:  # pragma: no cover - defensive
        raise _fail(
            "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH",
            f"registry entry {destination} hashed to {written} after write",
        )
    os.chmod(destination, REGISTRY_FILE_MODE)
    return destination


# -------------------------------------------------- integrity checking ---


def _read_manifest(archive_dir: Path) -> tuple[dict[str, Any], bytes]:
    manifest_path = archive_dir / MANIFEST_NAME
    sha_path = archive_dir / MANIFEST_SHA_NAME

    if not _is_regular_file(manifest_path):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID", f"missing manifest: {manifest_path}"
        )
    if not _is_regular_file(sha_path):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID", f"missing digest: {sha_path}"
        )

    raw = manifest_path.read_bytes()
    digest_raw = sha_path.read_bytes()
    expected = sha256_bytes(raw)
    if len(digest_raw) != 65 or not digest_raw.endswith(b"\n"):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            f"{MANIFEST_SHA_NAME} must be exactly 64 lowercase hex digits and one LF",
        )
    recorded = digest_raw[:64].decode("ascii", errors="replace")
    if recorded != recorded.lower() or any(
        character not in "0123456789abcdef" for character in recorded
    ):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            f"{MANIFEST_SHA_NAME} is not 64 lowercase hex digits",
        )
    if recorded != expected:
        raise _fail(
            "PRODUCTION_ARCHIVE_HASH_MISMATCH",
            f"{MANIFEST_NAME} hashes to {expected} but {MANIFEST_SHA_NAME} records "
            f"{recorded}",
        )

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID", f"{MANIFEST_NAME}: {error}"
        ) from None
    if not isinstance(manifest, dict):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID", f"{MANIFEST_NAME} is not an object"
        )

    # The generation is read *before* validation, because it selects the
    # contract to validate against. An unknown generation is refused rather
    # than guessed at: reading a v3 archive under the v2 rules would be a
    # silent reinterpretation of somebody else's bytes.
    version = manifest.get("schema_version")
    if version not in MANIFEST_SCHEMAS:
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            f"unsupported manifest schema_version: {version!r}",
        )
    try:
        validate_json_document(manifest, MANIFEST_SCHEMAS[version])
    except ValueError as error:
        raise _fail("PRODUCTION_ARCHIVE_MANIFEST_INVALID", str(error)) from None
    return manifest, raw


def verify_archive_integrity(archive_dir: Path, *, target_date: str) -> dict[str, Any]:
    """Byte-level verification of a sealed per-run archive.

    Checks, in order: manifest present, digest present and well-formed,
    manifest raw-byte SHA256, manifest schema, target_date, duplicate paths,
    every listed file present / regular / right size / right SHA256, no extra
    regular file, no symlink or special file, ``total_file_count`` and
    ``total_size_bytes``, and the two ``inputs/`` digests.

    Business status is deliberately *not* consulted: an archive of an
    INCOMPLETE run is a valid archive.
    """
    archive_dir = Path(archive_dir)
    if archive_dir.is_symlink() or not archive_dir.is_dir():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY",
            f"archive directory is not a directory: {archive_dir}",
        )

    manifest, _raw = _read_manifest(archive_dir)
    if manifest.get("target_date") != target_date:
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            f"manifest target_date {manifest.get('target_date')!r} does not match "
            f"{target_date!r}",
        )

    entries = manifest["files"]
    listed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = entry["path"]
        if path in listed:
            raise _fail(
                "PRODUCTION_ARCHIVE_MANIFEST_INVALID", f"duplicate manifest path: {path}"
            )
        listed[path] = entry
    if [entry["path"] for entry in entries] != sorted(listed):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            "manifest files[] is not sorted by path",
        )
    if manifest["total_file_count"] != len(entries):
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            f"total_file_count {manifest['total_file_count']} != {len(entries)}",
        )
    total = sum(int(entry["size_bytes"]) for entry in entries)
    if manifest["total_size_bytes"] != total:
        raise _fail(
            "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
            f"total_size_bytes {manifest['total_size_bytes']} != {total}",
        )

    present: list[str] = []
    for subtree in ARCHIVE_SUBTREES:
        subtree_root = archive_dir / subtree
        if not subtree_root.exists() and not subtree_root.is_symlink():
            raise _fail(
                "PRODUCTION_ARCHIVE_MISSING_FILE", f"missing subtree: {subtree_root}"
            )
        present.extend(
            f"{subtree}/{relative}" for relative in scan_tree(subtree_root)
        )

    top_level_extra = sorted(
        item.name
        for item in archive_dir.iterdir()
        if item.name not in ARCHIVE_SUBTREES
        and item.name not in {MANIFEST_NAME, MANIFEST_SHA_NAME}
    )
    if top_level_extra:
        raise _fail(
            "PRODUCTION_ARCHIVE_EXTRA_FILE",
            "unexpected archive entries: " + ", ".join(top_level_extra),
        )

    missing = sorted(set(listed) - set(present))
    if missing:
        raise _fail(
            "PRODUCTION_ARCHIVE_MISSING_FILE",
            "manifest-listed file(s) not present: " + ", ".join(missing),
        )
    extra = sorted(set(present) - set(listed))
    if extra:
        raise _fail(
            "PRODUCTION_ARCHIVE_EXTRA_FILE",
            "file(s) not listed in the manifest: " + ", ".join(extra),
        )

    for path in sorted(listed):
        entry = listed[path]
        payload = (archive_dir / path).read_bytes()
        if len(payload) != int(entry["size_bytes"]):
            raise _fail(
                "PRODUCTION_ARCHIVE_HASH_MISMATCH",
                f"{path}: size {len(payload)} != manifest {entry['size_bytes']}",
            )
        actual = sha256_bytes(payload)
        if actual != entry["sha256"]:
            raise _fail(
                "PRODUCTION_ARCHIVE_HASH_MISMATCH",
                f"{path}: SHA256 {actual} != manifest {entry['sha256']}",
            )

    for key, archive_path in (
        ("source_matrix", SOURCE_MATRIX_ARCHIVE_PATH),
        ("issuer_domain_registry", ISSUER_REGISTRY_ARCHIVE_PATH),
    ):
        recorded = manifest["inputs"][key]
        if recorded["path"] != archive_path:
            raise _fail(
                "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
                f"inputs.{key}.path is {recorded['path']!r}",
            )
        actual = sha256_file_bytes(archive_dir / archive_path)
        if actual != recorded["sha256"]:
            raise _fail(
                "PRODUCTION_ARCHIVE_HASH_MISMATCH",
                f"{archive_path}: SHA256 {actual} != manifest {recorded['sha256']}",
            )

    # Legacy read contract: only a v1 manifest carries Runtime Security
    # evidence, and it is still checked exactly as it was recorded. A v2
    # manifest has no such field to check -- it is not that the check was
    # weakened, it is that the evidence is no longer part of what an archive
    # claims.
    if manifest["schema_version"] == 1:
        runtime_security = manifest["runtime_security"]
        recomputed = _runtime_security_evidence(archive_dir, target_date)
        if (
            runtime_security["status"] != recomputed["status"]
            or runtime_security["sha256"] != recomputed["sha256"]
            or runtime_security["path"] != recomputed["path"]
        ):
            raise _fail(
                "PRODUCTION_ARCHIVE_MANIFEST_INVALID",
                "runtime_security evidence does not match the manifest: "
                f"stored {runtime_security['status']}, recomputed {recomputed['status']}",
            )

    return manifest


# ------------------------------------------------------------- archive ---


def _build_manifest(
    staging: Path,
    *,
    target_date: str,
    archived_at: str,
    business_status: str,
    source_matrix_sha256: str,
    issuer_registry_sha256: str,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for subtree in ARCHIVE_SUBTREES:
        subtree_root = staging / subtree
        for relative in scan_tree(subtree_root):
            path = f"{subtree}/{relative}"
            payload = (staging / path).read_bytes()
            files.append(
                {
                    "path": path,
                    "size_bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                }
            )
    files.sort(key=lambda entry: entry["path"])

    # v2: the Business Verification decides, alone. Whether the local Claude
    # executor ran under some particular OS policy says nothing about whether
    # the market evidence and the trading decision in this run hold up.
    complete = business_status in VERIFIED_STATUSES
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "archive_version": ARCHIVE_VERSION_V2,
        "target_date": target_date,
        "archived_at": archived_at,
        "archive_status": (
            ARCHIVE_STATUS_COMPLETE_VERIFIED if complete else ARCHIVE_STATUS_INCOMPLETE
        ),
        "source": {
            "run_relative_path": f"runs/{target_date}",
        },
        "business_verification": {
            "status": business_status,
            "report_path": VERIFICATION_REPORT_PATH,
        },
        "inputs": {
            "source_matrix": {
                "path": SOURCE_MATRIX_ARCHIVE_PATH,
                "sha256": source_matrix_sha256,
            },
            "issuer_domain_registry": {
                "path": ISSUER_REGISTRY_ARCHIVE_PATH,
                "sha256": issuer_registry_sha256,
            },
        },
        "files": files,
        "total_file_count": len(files),
        "total_size_bytes": sum(entry["size_bytes"] for entry in files),
    }


def _copy_raw(source: Path, destination: Path) -> None:
    """Copy raw bytes and verify the destination re-reads to the same SHA256."""
    payload = source.read_bytes()
    expected = sha256_bytes(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    actual = sha256_file_bytes(destination)
    if actual != expected:  # pragma: no cover - defensive
        raise _fail(
            "PRODUCTION_ARCHIVE_HASH_MISMATCH",
            f"{destination} hashed to {actual} after copy, expected {expected}",
        )


def _require_input_file(path: Path, label: str) -> bytes:
    if not _is_regular_file(path):
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_MISSING", f"{label} is not a regular file: {path}"
        )
    return path.read_bytes()


def archive_production_run(
    target_date: str,
    *,
    daytrade_root: str | Path = DAYTRADE_ROOT,
    archive_root: str | Path = ARCHIVE_ROOT,
    source_matrix_path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
    issuer_registry_path: str | Path = DEFAULT_ISSUER_REGISTRY_PATH,
    clock: Callable[[], _datetime.datetime] = _utc_now,
) -> dict[str, Any]:
    """Seal ``runs/<target_date>/`` into ``<archive_root>/runs/<target_date>/``.

    The Operational Run is treated as read-only source and is never modified.
    Returns the machine-readable result document
    (``ARCHIVED`` or ``ALREADY_ARCHIVED``).

    Every path seam is keyword-only for testability; the human CLI exposes
    none of them (``--target-date`` is the only input).
    """
    target_date = _validated_target_date(target_date)
    daytrade_root = Path(daytrade_root)
    archive_root = Path(archive_root)
    source_matrix_path = Path(source_matrix_path)
    issuer_registry_path = Path(issuer_registry_path)

    run_dir = _operational_run_dir(daytrade_root, target_date)
    if run_dir.is_symlink():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_UNSAFE_ENTRY",
            f"run directory is a symbolic link: {run_dir}",
        )
    if not run_dir.exists():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_MISSING", f"run directory not found: {run_dir}"
        )
    if not run_dir.is_dir():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_NOT_DIRECTORY",
            f"run path is not a directory: {run_dir}",
        )

    source_files = scan_tree(run_dir)
    archive_dir = archive_root / "runs" / target_date

    if archive_dir.exists() or archive_dir.is_symlink():
        return _confirm_existing_archive(
            archive_dir,
            run_dir=run_dir,
            target_date=target_date,
            source_files=source_files,
        )

    source_matrix_bytes = _require_input_file(source_matrix_path, "source matrix")
    issuer_registry_bytes = _require_input_file(
        issuer_registry_path, "issuer domain registry"
    )
    source_matrix_sha256 = sha256_bytes(source_matrix_bytes)
    issuer_registry_sha256 = sha256_bytes(issuer_registry_bytes)

    staging = _create_staging(archive_root, target_date)
    try:
        (staging / "run").mkdir(parents=True, exist_ok=True)
        for relative in source_files:
            _copy_raw(run_dir / relative, staging / "run" / relative)

        (staging / "inputs").mkdir(parents=True, exist_ok=True)
        (staging / SOURCE_MATRIX_ARCHIVE_PATH).write_bytes(source_matrix_bytes)
        (staging / ISSUER_REGISTRY_ARCHIVE_PATH).write_bytes(issuer_registry_bytes)

        report = _business_verification_report(
            staging / "run", staging / SOURCE_MATRIX_ARCHIVE_PATH
        )
        (staging / "verification").mkdir(parents=True, exist_ok=True)
        (staging / VERIFICATION_REPORT_PATH).write_bytes(_canonical_json_bytes(report))

        manifest = _build_manifest(
            staging,
            target_date=target_date,
            archived_at=clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            business_status=str(report.get("status", INVALID_RUN)),
            source_matrix_sha256=source_matrix_sha256,
            issuer_registry_sha256=issuer_registry_sha256,
        )
        validate_json_document(manifest, MANIFEST_SCHEMA_NAME_V2)

        manifest_bytes = _canonical_json_bytes(manifest)
        (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
        (staging / MANIFEST_SHA_NAME).write_bytes(
            (sha256_bytes(manifest_bytes) + "\n").encode("ascii")
        )

        # Re-verify the finished staging tree from disk before anything is
        # sealed or moved: missing, extra, resized or re-hashed files all
        # abort here, leaving no final archive behind.
        verify_archive_integrity(staging, target_date=target_date)

        store_source_matrix_in_registry(
            archive_root=archive_root,
            source_matrix_bytes=source_matrix_bytes,
            sha256=source_matrix_sha256,
        )

        _seal_tree(staging, seal_root=False)

        if archive_dir.exists() or archive_dir.is_symlink():  # pragma: no cover
            raise _fail(
                "PRODUCTION_ARCHIVE_FINALIZE_FAILED",
                f"archive appeared during staging: {archive_dir}",
            )
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staging, archive_dir)
        except OSError as error:
            raise _fail(
                "PRODUCTION_ARCHIVE_FINALIZE_FAILED",
                f"could not move staging into place: {error}",
            ) from None
        os.chmod(archive_dir, SEALED_DIR_MODE)
    except BaseException:
        if staging.exists():
            _force_rmtree(staging)
        raise

    return {
        "result": RESULT_ARCHIVED,
        "target_date": target_date,
        "archive_status": manifest["archive_status"],
        "archive_dir": str(archive_dir),
    }


def _create_staging(archive_root: Path, target_date: str) -> Path:
    staging_root = archive_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    stale = sorted(
        item.name
        for item in staging_root.iterdir()
        if item.name == target_date or item.name.startswith(f"{target_date}.")
    )
    if stale:
        raise _fail(
            "PRODUCTION_ARCHIVE_STAGING_EXISTS",
            f"stale staging directory for {target_date} in {staging_root}: "
            + ", ".join(stale)
            + " -- investigate and remove it manually",
        )
    return Path(tempfile.mkdtemp(dir=str(staging_root), prefix=f"{target_date}."))


def _confirm_existing_archive(
    archive_dir: Path,
    *,
    run_dir: Path,
    target_date: str,
    source_files: tuple[str, ...],
) -> dict[str, Any]:
    """Idempotency: an existing archive is verified and compared, never rewritten."""
    try:
        manifest = verify_archive_integrity(archive_dir, target_date=target_date)
    except ProductionArchiveError as error:
        raise _fail(
            "PRODUCTION_ARCHIVE_EXISTS_INVALID",
            f"existing archive {archive_dir} is not valid ({error})",
        ) from None

    archived_run = archive_dir / "run"
    archived_files = scan_tree(archived_run)
    if archived_files != source_files:
        only_source = sorted(set(source_files) - set(archived_files))
        only_archive = sorted(set(archived_files) - set(source_files))
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_DIVERGED",
            f"{run_dir} no longer matches {archived_run}: "
            f"only in run: {only_source or '-'}; only in archive: {only_archive or '-'}",
        )

    source_digest = _tree_digest(run_dir, source_files)
    archive_digest = _tree_digest(archived_run, archived_files)
    diverged = sorted(
        relative
        for relative in source_files
        if source_digest[relative] != archive_digest[relative]
    )
    if diverged:
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_DIVERGED",
            f"{run_dir} differs from {archived_run} in: " + ", ".join(diverged),
        )

    return {
        "result": RESULT_ALREADY_ARCHIVED,
        "target_date": target_date,
        "archive_status": manifest["archive_status"],
        "archive_dir": str(archive_dir),
    }


# -------------------------------------------------------------- verify ---


def verify_production_archive(
    target_date: str,
    *,
    archive_root: str | Path = ARCHIVE_ROOT,
) -> dict[str, Any]:
    """Verify one sealed per-run archive.

    Archive validity is decided by raw bytes and the manifest only. The
    archived run's business status is *re-derived* and reported alongside the
    stored one, but a difference between them means the verifier's business
    rules changed, not that the archive was tampered with -- so it is reported,
    never treated as corruption.
    """
    target_date = _validated_target_date(target_date)
    archive_root = Path(archive_root)
    archive_dir = archive_root / "runs" / target_date
    if not archive_dir.exists() and not archive_dir.is_symlink():
        raise _fail(
            "PRODUCTION_ARCHIVE_SOURCE_MISSING", f"archive not found: {archive_dir}"
        )

    manifest = verify_archive_integrity(archive_dir, target_date=target_date)

    source_matrix_sha256 = manifest["inputs"]["source_matrix"]["sha256"]
    registry_entry = registry_dir(archive_root) / f"{source_matrix_sha256}.yaml"
    registry_status = "ABSENT"
    if registry_entry.exists() or registry_entry.is_symlink():
        if not _is_regular_file(registry_entry):
            raise _fail(
                "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH",
                f"registry entry is not a regular file: {registry_entry}",
            )
        actual = sha256_file_bytes(registry_entry)
        if actual != source_matrix_sha256:
            raise _fail(
                "PRODUCTION_ARCHIVE_REGISTRY_HASH_MISMATCH",
                f"registry file {registry_entry} is named for SHA256 "
                f"{source_matrix_sha256} but its actual byte SHA256 is {actual}",
            )
        registry_status = "PRESENT"

    report = _business_verification_report(
        archive_dir / "run", archive_dir / SOURCE_MATRIX_ARCHIVE_PATH
    )

    return {
        "result": RESULT_ARCHIVE_VERIFIED,
        "target_date": target_date,
        "archive_dir": str(archive_dir),
        "archive_status": manifest["archive_status"],
        "stored_business_verification_status": manifest["business_verification"][
            "status"
        ],
        "current_business_reverification_status": str(
            report.get("status", INVALID_RUN)
        ),
        "schema_version": manifest["schema_version"],
        # v1 archives recorded this; v2 archives have nothing to report here.
        "runtime_security_status": (
            manifest["runtime_security"]["status"]
            if manifest["schema_version"] == 1
            else None
        ),
        "source_matrix_registry": registry_status,
        "total_file_count": manifest["total_file_count"],
        "total_size_bytes": manifest["total_size_bytes"],
    }


# ---------------------------------------------------------------- CLIs ---


def _parse_target_date_only(description: str, argv: list[str] | None) -> str:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--target-date", required=True)
    return parser.parse_args(argv).target_date


def _run(operation: Callable[[], dict[str, Any]]) -> int:
    try:
        result = operation()
    except ProductionArchiveError as error:
        sys.stderr.write(f"{error}\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def archive_main(argv: list[str] | None = None) -> int:
    """HUMAN-ONLY entry point for ``scripts/archive-production-run``."""
    target_date = _parse_target_date_only(
        "Seal a Production Nightly Run into the local Production Run Archive.", argv
    )
    return _run(lambda: archive_production_run(target_date))


def verify_main(argv: list[str] | None = None) -> int:
    """HUMAN-ONLY entry point for ``scripts/verify-production-archive``."""
    target_date = _parse_target_date_only(
        "Verify a sealed Production Run Archive against its SHA256 manifest.", argv
    )
    return _run(lambda: verify_production_archive(target_date))


__all__ = [
    "ARCHIVE_ROOT",
    "ARCHIVE_STATUS_COMPLETE_VERIFIED",
    "ARCHIVE_STATUS_INCOMPLETE",
    "ARCHIVE_VERSION",
    "ARCHIVE_VERSION_V2",
    "CURRENT_SCHEMA_VERSION",
    "DAYTRADE_ROOT",
    "ERROR_CODES",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_NAME",
    "MANIFEST_SCHEMA_NAME_V2",
    "MANIFEST_SCHEMAS",
    "MANIFEST_SHA_NAME",
    "REPOSITORY_ROOT",
    "RESULT_ALREADY_ARCHIVED",
    "RESULT_ARCHIVED",
    "RESULT_ARCHIVE_VERIFIED",
    "RUNTIME_SECURITY_INVALID",
    "RUNTIME_SECURITY_MISSING",
    "RUNTIME_SECURITY_VALID",
    "ProductionArchiveError",
    "archive_main",
    "archive_production_run",
    "registry_dir",
    "scan_tree",
    "store_source_matrix_in_registry",
    "verify_archive_integrity",
    "verify_main",
    "verify_production_archive",
]
