"""DTWO-2026-026: the small, non-security helpers a Production run needs.

The retired ``src/claude_runtime_security.py`` mixed two unrelated things: an
OS-level attestation of the local Claude executor (managed policy, runtime
guard, seccomp marker, exact provider version) and a handful of plain layout
helpers -- "is this a real calendar date", "where does that run directory
live", "sha256 of these bytes". The first belonged to Layer C (Local
Operational Governance) and is gone. The second is used by the Production
Archive and by the human-only discovery reparse recovery, neither of which is
an executor attestation, so it lives here instead.

Nothing in this module inspects ``/etc``, the provider version, the sandbox or
any managed policy, and nothing here may grow to. It is deliberately the
boring half of what was removed.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent

#: The human-approved issuer registry. ``src/source_matrix.py`` already owns
#: ``DEFAULT_SOURCE_MATRIX_PATH``; this is its sibling and has no other home.
DEFAULT_ISSUER_REGISTRY_PATH = PROJECT_ROOT / "config" / "issuer_domain_registry.yaml"

#: Tree prefixes the human-only discovery reparse recovery refuses to run over
#: with uncommitted changes. This is a recovery precondition, not a launcher
#: gate: the recovery rewrites stored evidence, so the code that produced it
#: must be the committed code.
PROTECTED_TREE_PREFIXES = (
    "daytrade-sbi/src/",
    "daytrade-sbi/config/",
    "daytrade-sbi/schemas/",
    "daytrade-sbi/scripts/",
    ".claude/",
    "CLAUDE.md",
    "daytrade-sbi/AGENTS.md",
)

ERROR_CODES = (
    "CLAUDE_TARGET_DATE_INVALID",
    "CLAUDE_SOURCE_TREE_DIRTY",
)


class ProductionContextError(Exception):
    """A fail-closed refusal carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> ProductionContextError:
    assert code in ERROR_CODES, code
    return ProductionContextError(code, message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


_TARGET_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_target_date(value: str) -> str:
    """Return ``value`` if it is exactly one real ``YYYY-MM-DD`` calendar date.

    This is the launcher's only accepted form of untrusted human input, so it
    is deliberately total: no whitespace, no separators, no path segment, no
    non-existent calendar date.
    """
    if not isinstance(value, str):
        raise _fail("CLAUDE_TARGET_DATE_INVALID", "--target-date must be a string")
    if not _TARGET_DATE_RE.match(value):
        raise _fail(
            "CLAUDE_TARGET_DATE_INVALID",
            f"--target-date must be exactly YYYY-MM-DD: {value!r}",
        )
    try:
        parsed = _datetime.date.fromisoformat(value)
    except ValueError:
        raise _fail(
            "CLAUDE_TARGET_DATE_INVALID", f"not a real calendar date: {value!r}"
        ) from None
    if parsed.isoformat() != value:
        raise _fail(
            "CLAUDE_TARGET_DATE_INVALID", f"not a canonical ISO date: {value!r}"
        )
    return value


def resolve_run_dir(daytrade_root: str | Path, target_date: str) -> Path:
    """Return ``<daytrade_root>/runs/<target_date>``, containment-checked."""
    target_date = validate_target_date(target_date)
    runs_root = (Path(daytrade_root) / "runs").resolve()
    run_dir = (runs_root / target_date).resolve()
    if run_dir.parent != runs_root or run_dir.name != target_date:
        raise _fail(
            "CLAUDE_TARGET_DATE_INVALID",
            f"run directory escapes {runs_root}: {run_dir}",
        )
    return run_dir


def verify_source_tree_clean(porcelain_output: str) -> None:
    """Refuse when a protected source path has uncommitted changes.

    Run directories are excluded: they are evidence produced *by* a run, and
    ``daytrade-sbi/.gitignore`` keeps them untracked anyway.
    """
    dirty: list[str] = []
    for line in (porcelain_output or "").splitlines():
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else line
        entry = entry.strip().strip('"')
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry.startswith("runs/") or entry.startswith("daytrade-sbi/runs/"):
            continue
        for prefix in PROTECTED_TREE_PREFIXES:
            if entry == prefix or entry.startswith(prefix):
                dirty.append(entry)
                break
    if dirty:
        raise _fail(
            "CLAUDE_SOURCE_TREE_DIRTY",
            f"protected tree has uncommitted changes: {sorted(set(dirty))}",
        )


__all__ = [
    "DEFAULT_ISSUER_REGISTRY_PATH",
    "ERROR_CODES",
    "PROJECT_ROOT",
    "PROTECTED_TREE_PREFIXES",
    "REPOSITORY_ROOT",
    "ProductionContextError",
    "resolve_run_dir",
    "sha256_bytes",
    "validate_target_date",
    "verify_source_tree_clean",
]
