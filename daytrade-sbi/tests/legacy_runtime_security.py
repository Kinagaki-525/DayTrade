"""Historical fixture builder for the retired Runtime Security Attestation.

DTWO-2026-026 retired the attestation itself: no new Production run writes
``working/runtime_security.json``, and no launcher, policy or guard produces
one. What survives is the **Legacy Read Contract** -- Production Archive v1
manifests recorded this evidence, and those sealed archives must keep verifying
byte for byte under the rules they were written under.

Verifying that path needs v1-shaped attestations to test against, so this
module builds them. It is fixture data, not a mechanism: nothing here inspects
``/etc``, a managed policy, a runtime guard, a sandbox or a provider version,
and nothing in ``src/`` imports it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

RUNTIME_SECURITY_SCHEMA_VERSION = 1
RUNTIME_PROFILE = "production"

#: The exact check names a v1 attestation carried, in order.
RUNTIME_SECURITY_CHECKS = (
    "managed_settings",
    "managed_settings_permissions",
    "runtime_guard",
    "runtime_guard_sha",
    "claude_version",
    "sandbox_dependencies",
    "sandbox_required",
    "sandbox_escape_disabled",
    "strict_network_allowlist",
    "managed_domain_lock",
    "managed_hook_lock",
    "managed_permission_lock",
    "mcp_lockdown",
    "domain_sync",
    "git_clean",
    "http_user_agent",
    "production_marker",
    "sandbox_seccomp",
)


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_runtime_security_document(
    *,
    target_date: str,
    platform_name: str,
    claude_code_version: str,
    git_head_sha: str,
    production_python: str | Path,
    managed_settings_sha256: str,
    managed_runtime_guard_sha256: str,
    source_matrix_path: str | Path,
    issuer_registry_path: str | Path,
    allowed_domains: Sequence[str],
    checks: Mapping[str, str],
    http_user_agent_present: bool,
) -> dict[str, Any]:
    """A v1 attestation document, exactly as the retired launcher wrote it."""
    missing = [name for name in RUNTIME_SECURITY_CHECKS if name not in checks]
    if missing:
        raise ValueError(f"runtime_security checks incomplete: {missing}")
    return {
        "schema_version": RUNTIME_SECURITY_SCHEMA_VERSION,
        "runtime": "claude-code",
        "runtime_profile": RUNTIME_PROFILE,
        "target_date": target_date,
        "platform": platform_name,
        "claude_code_version": claude_code_version,
        "git_head_sha": git_head_sha,
        "production_python": str(production_python),
        "managed_settings_sha256": managed_settings_sha256,
        "managed_runtime_guard_sha256": managed_runtime_guard_sha256,
        "source_matrix_sha256": _sha256_file(source_matrix_path),
        "issuer_domain_registry_sha256": _sha256_file(issuer_registry_path),
        "allowed_domains": list(allowed_domains),
        "http_user_agent_present": bool(http_user_agent_present),
        "checks": {name: checks[name] for name in RUNTIME_SECURITY_CHECKS},
    }
