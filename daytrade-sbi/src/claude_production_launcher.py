"""FIX-R2-004A: the unit-testable core of ``scripts/claude-production``.

``scripts/claude-production`` is a human-only entry point; the security value
of its preflight is only real if the preflight itself is tested. This module
holds that preflight as an injectable pure-ish function: every OS boundary
(paths, environment, subprocess, ``which``, platform) is a keyword argument,
so tests drive it against a tmp directory instead of the real ``/etc``.

The *command line* deliberately exposes none of those seams (FIX-R2-004A
section 19): a human may only choose ``--target-date`` and
``--preflight-only``. Security-boundary paths are fixed constants.
"""

from __future__ import annotations

import argparse
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from src.claude_runtime_security import (
    DEFAULT_ISSUER_REGISTRY_PATH,
    DEFAULT_SOURCE_MATRIX_PATH,
    MANAGED_GUARD_PATH,
    MANAGED_SETTINGS_PATH,
    PRODUCTION_MARKER_PATH,
    REQUIRED_SANDBOX_BINARIES,
    RUNTIME_SECURITY_CHECKS,
    SECCOMP_MARKER_PATH,
    RuntimeSecurityError,
    build_runtime_security_document,
    canonical_production_python,
    derive_expected_domains,
    render_managed_settings,
    resolve_run_dir,
    validate_target_date,
    verify_claude_version,
    verify_http_user_agent,
    verify_installed_managed_settings,
    verify_installed_runtime_guard,
    verify_platform,
    verify_production_marker,
    verify_sandbox_dependencies,
    verify_seccomp_marker,
    verify_source_tree_clean,
)
from src.contracts import validate_json_document
from src.file_io import atomic_write_text

DAYTRADE_ROOT = Path(__file__).resolve().parents[1]

RUNTIME_SECURITY_SCHEMA_NAME = "runtime_security.schema.json"


def default_run_command(argv: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeSecurityError(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"command failed: {' '.join(argv)}: {completed.stderr.strip()}",
        )
    return completed.stdout


def preflight(
    *,
    target_date: str,
    daytrade_root: str | Path = DAYTRADE_ROOT,
    project_root: str | Path | None = None,
    managed_settings_path: str | Path = MANAGED_SETTINGS_PATH,
    managed_guard_path: str | Path = MANAGED_GUARD_PATH,
    production_marker_path: str | Path = PRODUCTION_MARKER_PATH,
    seccomp_marker_path: str | Path = SECCOMP_MARKER_PATH,
    source_matrix_path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
    issuer_registry_path: str | Path = DEFAULT_ISSUER_REGISTRY_PATH,
    expected_uid: int = 0,
    environ: Mapping[str, str] | None = None,
    run_command: Callable[[list[str], Path], str] = default_run_command,
    which: Callable[[str], str | None] = shutil.which,
    platform_system: Callable[[], str] = _platform.system,
    platform_name: Callable[[], str] = _platform.platform,
    write_artifact: bool = True,
) -> dict[str, Any]:
    """Run the full fail-closed production preflight.

    Raises :class:`RuntimeSecurityError` on the first violation; nothing is
    written to disk unless every check passed.
    """
    environ = os.environ if environ is None else environ

    # 0. untrusted human input, before any filesystem work at all.
    target_date = validate_target_date(target_date)

    daytrade_root = Path(daytrade_root).resolve()
    project_root = (
        Path(project_root).resolve() if project_root is not None else daytrade_root.parent
    )
    run_dir = resolve_run_dir(daytrade_root, target_date)

    checks: dict[str, str] = {}

    # 1. platform
    verify_platform(platform_system())

    # 2. dedicated production runtime marker (root-owned)
    verify_production_marker(production_marker_path, expected_uid=expected_uid)
    checks["production_marker"] = "PASS"

    # 3. human seccomp attestation marker (root-owned) -- never guessed
    verify_seccomp_marker(seccomp_marker_path, expected_uid=expected_uid)
    checks["sandbox_seccomp"] = "PASS"

    # 4. protected tree must be clean
    verify_source_tree_clean(run_command(["git", "status", "--porcelain"], project_root))
    checks["git_clean"] = "PASS"

    # 5. Claude Code version
    claude_version = run_command(["claude", "--version"], project_root).strip()
    verify_claude_version(claude_version)
    checks["claude_version"] = "PASS"

    # 6. sandbox dependencies (never installed here)
    verify_sandbox_dependencies({name: which(name) for name in REQUIRED_SANDBOX_BINARIES})
    checks["sandbox_dependencies"] = "PASS"

    # 7-8. expected domains + expected managed policy (in memory)
    allowed_domains = derive_expected_domains(source_matrix_path, issuer_registry_path)
    production_python_candidate = environ.get("DAYTRADE_PRODUCTION_PYTHON") or which(
        "python3"
    )
    if not production_python_candidate:
        raise RuntimeSecurityError(
            "CLAUDE_MANAGED_POLICY_INVALID", "no production python3 was found"
        )
    # FIX-R2-004B: one canonical identity from here on -- the managed Bash allow
    # rule, the managed hook command, runtime_security.json, the preflight
    # result and DAYTRADE_PRODUCTION_PYTHON all carry this exact string.
    production_python = canonical_production_python(production_python_candidate)
    expected = render_managed_settings(
        project_root=project_root,
        daytrade_root=daytrade_root,
        production_python=production_python,
        source_matrix_path=source_matrix_path,
        issuer_registry_path=issuer_registry_path,
        guard_path=managed_guard_path,
    )

    # 9. installed managed policy
    settings_sha = verify_installed_managed_settings(
        managed_settings_path, expected=expected, expected_uid=expected_uid
    )
    for name in (
        "managed_settings",
        "managed_settings_permissions",
        "sandbox_required",
        "sandbox_escape_disabled",
        "strict_network_allowlist",
        "managed_domain_lock",
        "managed_hook_lock",
        "managed_permission_lock",
        "mcp_lockdown",
        "domain_sync",
    ):
        checks[name] = "PASS"

    # 10. installed runtime guard
    guard_sha = verify_installed_runtime_guard(
        managed_guard_path, expected_uid=expected_uid
    )
    checks["runtime_guard"] = "PASS"
    checks["runtime_guard_sha"] = "PASS"

    # 11. HTTP User-Agent (the value itself is never recorded)
    verify_http_user_agent(environ)
    checks["http_user_agent"] = "PASS"

    # 12. runtime_security.json -- schema-validated, then atomically written
    head = run_command(["git", "rev-parse", "HEAD"], project_root).strip()
    document = build_runtime_security_document(
        target_date=target_date,
        platform_name=platform_name(),
        claude_code_version=claude_version,
        git_head_sha=head,
        production_python=production_python,
        managed_settings_sha256=settings_sha,
        managed_runtime_guard_sha256=guard_sha,
        source_matrix_path=source_matrix_path,
        issuer_registry_path=issuer_registry_path,
        allowed_domains=allowed_domains,
        checks={name: checks[name] for name in RUNTIME_SECURITY_CHECKS},
        http_user_agent_present=True,
    )
    try:
        validate_json_document(document, RUNTIME_SECURITY_SCHEMA_NAME)
    except ValueError as error:
        raise RuntimeSecurityError(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"runtime_security document failed schema validation: {error}",
        ) from None

    artifact_path = run_dir / "working" / "runtime_security.json"
    if write_artifact:
        atomic_write_text(
            artifact_path, json.dumps(document, indent=2, sort_keys=True) + "\n"
        )

    return {
        "target_date": target_date,
        "run_dir": run_dir,
        "artifact_path": artifact_path,
        "document": document,
        "production_python": production_python,
        "project_root": project_root,
        "daytrade_root": daytrade_root,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HUMAN-ONLY DayTrade production Claude Code launcher"
    )
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def main(argv: list[str] | None = None, **overrides: Any) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = preflight(target_date=args.target_date, **overrides)
    except RuntimeSecurityError as error:
        sys.stderr.write(f"{error}\n")
        return 2

    if args.preflight_only:
        sys.stdout.write("runtime security preflight PASS\n")
        return 0

    env = dict(os.environ)
    env.update(
        {
            "DAYTRADE_RUNTIME_PROFILE": "production",
            "DAYTRADE_PROJECT_ROOT": str(result["project_root"]),
            "DAYTRADE_ROOT": str(result["daytrade_root"]),
            "DAYTRADE_RUN_DIR": str(result["run_dir"]),
            "DAYTRADE_TARGET_DATE": result["target_date"],
            "DAYTRADE_PRODUCTION_PYTHON": result["production_python"],
        }
    )
    os.chdir(result["daytrade_root"])
    os.execvpe("claude", ["claude"], env)  # pragma: no cover
    return 0  # pragma: no cover
