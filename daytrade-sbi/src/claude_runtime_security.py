"""FIX-R2-004: Claude Code Production Runtime Security Gate.

The production security boundary for a Claude-driven nightly run is an **OS
Managed Policy** (``/etc/claude-code/managed-settings.json``), not the project
``.claude/settings.json``. This module is the repository-side source of truth
for that policy:

* :func:`derive_expected_domains` -- the exact sandbox host allowlist, derived
  from ``config/source_matrix.yaml`` + ``config/issuer_domain_registry.yaml``.
  Nothing is hardcoded.
* :func:`render_managed_settings` -- a pure function that renders the managed
  settings template. It never writes to ``/etc``.
* the ``verify_*`` preflight helpers -- fail-closed checks a launcher runs
  before ``exec claude``.
* :func:`deploy_managed_policy` -- the testable core of the human-only deploy
  script. It refuses to overwrite an existing managed policy.

Nothing in this module executes ``sudo``, installs packages, starts Claude
Code, or opens a network connection.
"""

from __future__ import annotations

import datetime as _datetime
import errno
import hashlib
import ipaddress
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent

# ------------------------------------------------------------ constants ---

RUNTIME_SECURITY_SCHEMA_VERSION = 1
REQUIRED_MINIMUM_CLAUDE_VERSION = "2.1.219"
REQUIRED_RUNTIME_PROFILE = "production"

DEFAULT_MANAGED_ROOT = Path("/etc/claude-code")
MANAGED_SETTINGS_FILENAME = "managed-settings.json"
MANAGED_GUARD_FILENAME = "daytrade-runtime-guard.py"
MANAGED_SETTINGS_PATH = DEFAULT_MANAGED_ROOT / MANAGED_SETTINGS_FILENAME
MANAGED_GUARD_PATH = DEFAULT_MANAGED_ROOT / MANAGED_GUARD_FILENAME

PRODUCTION_MARKER_PATH = Path("/etc/daytrade-production-runtime")
PRODUCTION_MARKER_CONTENT = "DAYTRADE_PRODUCTION_RUNTIME_V1"

#: FIX-R2-004A section 12 (V2): a root-owned attestation that a human accepted
#: Claude Sandbox seccomp enforcement via the repository's
#: Sandbox-outside / Sandbox-inside differential probe. Nothing in this
#: repository ever creates the marker -- it is a Human Runtime Acceptance
#: artifact.
SECCOMP_MARKER_PATH = Path("/etc/daytrade-seccomp-verified")
SECCOMP_MARKER_CONTENT = "DAYTRADE_SECCOMP_VERIFIED_V2"

REQUIRED_SANDBOX_BINARIES = ("bwrap", "socat")

MANAGED_SETTINGS_TEMPLATE_PATH = (
    PROJECT_ROOT / "ops" / "claude" / "managed-settings.template.json"
)
RUNTIME_GUARD_SOURCE_PATH = PROJECT_ROOT / "ops" / "claude" / "daytrade_runtime_guard.py"

DEFAULT_SOURCE_MATRIX_PATH = PROJECT_ROOT / "config" / "source_matrix.yaml"
DEFAULT_ISSUER_REGISTRY_PATH = PROJECT_ROOT / "config" / "issuer_domain_registry.yaml"

#: Paths whose modification invalidates a production run (git dirty check).
PROTECTED_TREE_PREFIXES = (
    "daytrade-sbi/src/",
    "daytrade-sbi/config/",
    "daytrade-sbi/schemas/",
    "daytrade-sbi/ops/",
    "daytrade-sbi/scripts/",
    ".claude/",
    "CLAUDE.md",
    "daytrade-sbi/AGENTS.md",
)

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

ERROR_CODES = (
    "CLAUDE_RUNTIME_OS_UNSUPPORTED",
    "CLAUDE_RUNTIME_VERSION_INVALID",
    "CLAUDE_RUNTIME_VERSION_UNSUPPORTED",
    "CLAUDE_PRODUCTION_MARKER_MISSING",
    "CLAUDE_PRODUCTION_MARKER_PERMISSION_INVALID",
    "CLAUDE_TARGET_DATE_INVALID",
    "CLAUDE_SANDBOX_DEPENDENCY_MISSING",
    "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED",
    "CLAUDE_MANAGED_SETTINGS_MISSING",
    "CLAUDE_MANAGED_SETTINGS_PERMISSION_INVALID",
    "CLAUDE_MANAGED_POLICY_INVALID",
    "CLAUDE_MANAGED_GUARD_MISSING",
    "CLAUDE_MANAGED_GUARD_PERMISSION_INVALID",
    "CLAUDE_MANAGED_GUARD_MISMATCH",
    "CLAUDE_NETWORK_STRICT_ALLOWLIST_DISABLED",
    "CLAUDE_NETWORK_MANAGED_DOMAIN_LOCK_DISABLED",
    "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
    "CLAUDE_MCP_NOT_LOCKED_DOWN",
    "CLAUDE_HOOKS_NOT_MANAGED_ONLY",
    "CLAUDE_PERMISSIONS_NOT_MANAGED_ONLY",
    "CLAUDE_SANDBOX_NOT_REQUIRED",
    "CLAUDE_SANDBOX_ESCAPE_ENABLED",
    "CLAUDE_SANDBOX_EXCLUDED_COMMANDS_PRESENT",
    "CLAUDE_SANDBOX_FILESYSTEM_DISABLED",
    "CLAUDE_HTTP_USER_AGENT_MISSING",
    "CLAUDE_SOURCE_TREE_DIRTY",
    "CLAUDE_RUNTIME_PROFILE_MISSING",
    "CLAUDE_PRODUCTION_BASH_DENIED",
    "CLAUDE_PRODUCTION_WRITE_DENIED",
    "CLAUDE_PRODUCTION_CONFIG_CHANGE_DENIED",
    "CLAUDE_PRODUCTION_COMMAND_NOT_CANONICAL",
    "CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN",
    "EXISTING_MANAGED_POLICY_PRESENT",
    # Managed Policy replacement. Deliberately one code per condition: a single
    # generic "replacement failed" would tell the operator nothing about which
    # guarantee stopped the write, and every one of these stops it for a
    # different reason.
    "CLAUDE_MANAGED_POLICY_ATTESTATION_INVALID",
    "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID",
    "CLAUDE_MANAGED_POLICY_TARGET_INVALID",
    "CLAUDE_MANAGED_POLICY_INSTALLED_SHA_MISMATCH",
    "CLAUDE_MANAGED_POLICY_CANDIDATE_SHA_MISMATCH",
    "CLAUDE_MANAGED_POLICY_GUARD_MISMATCH",
    "CLAUDE_MANAGED_POLICY_STAGING_FAILED",
    "CLAUDE_MANAGED_POLICY_REPLACE_FAILED",
    "CLAUDE_MANAGED_POLICY_DURABILITY_FAILED",
    "CLAUDE_MANAGED_POLICY_POST_VERIFY_FAILED",
)


class RuntimeSecurityError(Exception):
    """A fail-closed production runtime security violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> "RuntimeSecurityError":
    return RuntimeSecurityError(code, message)


# --------------------------------------------------------------- hashes ---


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ------------------------------------------------ permission path forms ---


def absolute_edit_permission_pattern(path: str | Path) -> str:
    """Render an absolute filesystem path as a Claude permission-rule path.

    A Claude Code permission rule addresses an absolute filesystem path with a
    **double** leading slash (``Edit(//home/...)``); a single leading slash is
    interpreted relative to the settings file. A filesystem path string must
    therefore never be pasted into ``Edit(...)`` unchanged.
    """
    text = str(path)
    if not text.startswith("/"):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"permission pattern requires an absolute path: {text!r}",
        )
    return "//" + text.lstrip("/")


# ---------------------------------------------------- target date input ---

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


# --------------------------------------------------------- WSL2 / Linux ---


def detect_wsl2(osrelease_text: str = "", version_text: str = "") -> bool:
    """Deterministic WSL2 detection from existing Linux kernel strings.

    Only the documented ``microsoft``/``WSL`` markers are looked at; there is
    no heuristic scoring. The production preflight requires the seccomp
    attestation marker on *every* Linux host regardless of this answer, so a
    false negative can never weaken the gate.
    """
    blob = f"{osrelease_text}\n{version_text}".lower()
    return "microsoft" in blob or "wsl" in blob


def read_wsl2_marker(
    osrelease_path: str | Path = "/proc/sys/kernel/osrelease",
    version_path: str | Path = "/proc/version",
) -> bool:
    def _read(path: str | Path) -> str:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    return detect_wsl2(_read(osrelease_path), _read(version_path))


# ---------------------------------------------------- domain derivation ---


_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")


def _is_raw_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def validate_allowed_host(host: str) -> str:
    """Return ``host`` if it is an acceptable sandbox allowlist entry.

    Rejects: uppercase, wildcards, empty, ``localhost``, raw IP literals and
    anything that is not a plain dotted hostname.
    """
    if not isinstance(host, str) or not host.strip():
        raise _fail("CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", "empty allowlist host")
    if host != host.lower():
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", f"host must be lowercase: {host!r}"
        )
    if "*" in host:
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", f"wildcard host is forbidden: {host!r}"
        )
    if host == "localhost" or host.endswith(".localhost"):
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", f"localhost is forbidden: {host!r}"
        )
    if ":" in host:
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", f"explicit port is forbidden: {host!r}"
        )
    if _is_raw_ip(host):
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", f"raw IP literal is forbidden: {host!r}"
        )
    if not _HOSTNAME_RE.match(host):
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH", f"not a plain hostname: {host!r}"
        )
    return host


def _host_from_url_template(url_template: str, source_id: str) -> str:
    split = urlsplit(url_template)
    if split.scheme != "https":
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
            f"{source_id}: only https url_template hosts may be allowlisted",
        )
    if "@" in (split.netloc or ""):
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
            f"{source_id}: url userinfo is forbidden",
        )
    if split.port is not None:
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
            f"{source_id}: explicit port is forbidden",
        )
    raw_host = (split.netloc or "").split("@")[-1]
    if raw_host != raw_host.lower():
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
            f"{source_id}: url_template host must already be lowercase",
        )
    return validate_allowed_host(split.hostname or "")


def _load_yaml(path: str | Path) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise _fail("CLAUDE_MANAGED_POLICY_INVALID", f"{path} is not a YAML mapping")
    return payload


def derive_expected_domains(
    source_matrix_path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
    issuer_registry_path: str | Path = DEFAULT_ISSUER_REGISTRY_PATH,
) -> tuple[str, ...]:
    """The exact sandbox allowlist for this repository state.

    ``MATRIX_TEMPLATE_HOST`` sources contribute their ``url_template``
    hostname. ``VERIFIED_ISSUER_DOMAIN`` sources contribute **nothing** from
    the matrix (their host is a ``{issuer_domain}`` placeholder); the only
    issuer hosts allowed are the human-approved entries in the issuer domain
    registry.
    """
    matrix = _load_yaml(source_matrix_path)
    registry = _load_yaml(issuer_registry_path)

    hosts: set[str] = set()
    for source in matrix.get("sources") or []:
        acquisition = source.get("acquisition") or {}
        scope = acquisition.get("network_scope")
        if scope != "MATRIX_TEMPLATE_HOST":
            continue
        template = source.get("url_template")
        if not isinstance(template, str) or not template:
            continue
        hosts.add(
            _host_from_url_template(template, str(source.get("source_id", "?")))
        )

    policy = registry.get("approval_policy") or {}
    if policy.get("human_approved_only") is not True:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            "issuer_domain_registry.approval_policy.human_approved_only must be true",
        )
    if policy.get("auto_discovery_allowed") is not False:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            "issuer_domain_registry.approval_policy.auto_discovery_allowed must be false",
        )
    for issuer in registry.get("issuers") or []:
        for host in issuer.get("approved_hosts") or []:
            hosts.add(validate_allowed_host(str(host)))

    return tuple(sorted(hosts))


# ------------------------------------------------------------- renderer ---


def _require_absolute_dir(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise _fail("CLAUDE_MANAGED_POLICY_INVALID", f"{label} must be absolute")
    return path.resolve()


def canonical_production_python(value: str | Path) -> str:
    """FIX-R2-004B: the single canonicalization of the production interpreter.

    Every place that names the production Python -- the managed ``Bash`` allow
    rule, the managed hook ``command``, ``runtime_security.json``,
    ``DAYTRADE_PRODUCTION_PYTHON`` and therefore the runtime guard's own
    comparison -- must agree on *one* identity. A symlink alias
    (a ``python3`` symlink pointing at a versioned interpreter) is not a second
    identity: it resolves to the same canonical path here, so an alias invocation can never
    be mistaken for the allowed interpreter.

    Contract: absolute path required, ``Path.resolve()``-d, and the resolved
    path must be an existing regular file. Relative and nonexistent paths are
    rejected.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", "production_python must be a non-empty path"
        )
    path = Path(value)
    if not path.is_absolute():
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", "production_python must be an absolute path"
        )
    resolved = path.resolve()
    if not resolved.is_file():
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"production_python is not a file: {resolved}",
        )
    return str(resolved)


def render_managed_settings(
    *,
    project_root: str | Path,
    daytrade_root: str | Path,
    production_python: str | Path,
    source_matrix_path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
    issuer_registry_path: str | Path = DEFAULT_ISSUER_REGISTRY_PATH,
    guard_path: str | Path = MANAGED_GUARD_PATH,
    template_path: str | Path = MANAGED_SETTINGS_TEMPLATE_PATH,
) -> dict[str, Any]:
    """Render the managed settings policy. Pure: nothing is written to disk."""
    project_root = _require_absolute_dir(project_root, "project_root")
    daytrade_root = _require_absolute_dir(daytrade_root, "daytrade_root")

    canonical_python = canonical_production_python(production_python)

    guard_path = Path(guard_path)
    if not guard_path.is_absolute():
        raise _fail("CLAUDE_MANAGED_POLICY_INVALID", "guard_path must be absolute")

    domains = derive_expected_domains(source_matrix_path, issuer_registry_path)

    text = Path(template_path).read_text(encoding="utf-8")
    replacements = {
        "__PRODUCTION_PYTHON__": canonical_python,
        "__PROJECT_ROOT__": str(project_root),
        "__DAYTRADE_ROOT__": str(daytrade_root),
        "__EVENT_EXTRACTION_PATH_PATTERN__": absolute_edit_permission_pattern(
            daytrade_root / "runs" / "*" / "working" / "event_source_extraction.json"
        ),
        "__MANAGED_GUARD_PATH__": str(guard_path),
        "__ALLOWED_DOMAINS_JSON__": json.dumps(list(domains)),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)

    leftover = re.findall(r"__[A-Z_]+__", text)
    if leftover:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"unresolved template placeholders: {sorted(set(leftover))}",
        )

    payload = json.loads(text)
    verify_managed_settings_contract(payload, expected_domains=domains)
    return payload


def serialize_managed_settings(payload: Mapping[str, Any]) -> bytes:
    """The one canonical byte form of a Managed Policy.

    Every place that writes a policy, and every place that hashes one, goes
    through here. Duplicating the ``json.dumps`` arguments would let the
    installer and the replacement path drift into producing different bytes
    for the same policy -- and the whole replacement contract is built on the
    operator comparing hashes of those bytes.
    """
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def render_managed_settings_json(**kwargs: Any) -> str:
    return serialize_managed_settings(render_managed_settings(**kwargs)).decode("utf-8")


# ------------------------------------------------------------ preflight ---


def parse_semver(text: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        raise _fail(
            "CLAUDE_RUNTIME_VERSION_INVALID", f"cannot parse a version from {text!r}"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def verify_claude_version(
    version_output: str, minimum: str = REQUIRED_MINIMUM_CLAUDE_VERSION
) -> tuple[int, int, int]:
    found = parse_semver(version_output)
    if found < parse_semver(minimum):
        raise _fail(
            "CLAUDE_RUNTIME_VERSION_UNSUPPORTED",
            f"claude {'.'.join(map(str, found))} < required {minimum}",
        )
    return found


def verify_platform(system: str) -> str:
    if system.lower() != "linux":
        raise _fail(
            "CLAUDE_RUNTIME_OS_UNSUPPORTED",
            f"production runtime must be Linux/WSL2, not {system!r}",
        )
    return system


def _verify_root_owned_marker(
    path: str | Path,
    *,
    expected_content: str,
    missing_code: str,
    permission_code: str,
    expected_uid: int,
    label: str,
) -> None:
    """A marker is an OS attestation: root-owned, not writable by anyone else."""
    target = Path(path)
    verify_file_ownership(
        target,
        missing_code=missing_code,
        permission_code=permission_code,
        expected_uid=expected_uid,
    )
    if target.read_text(encoding="utf-8").strip() != expected_content:
        raise _fail(
            missing_code,
            f"{label} content must be exactly {expected_content}",
        )


def verify_production_marker(
    marker_path: str | Path = PRODUCTION_MARKER_PATH, *, expected_uid: int = 0
) -> None:
    _verify_root_owned_marker(
        marker_path,
        expected_content=PRODUCTION_MARKER_CONTENT,
        missing_code="CLAUDE_PRODUCTION_MARKER_MISSING",
        permission_code="CLAUDE_PRODUCTION_MARKER_PERMISSION_INVALID",
        expected_uid=expected_uid,
        label="production runtime marker",
    )


def verify_seccomp_marker(
    marker_path: str | Path = SECCOMP_MARKER_PATH, *, expected_uid: int = 0
) -> None:
    """Verify the Human-created seccomp attestation marker (section 12/13).

    The Claude Sandbox seccomp state is never guessed here. A Human completes
    the Sandbox-outside / Sandbox-inside differential probe and records that
    Runtime Acceptance as a root-owned V2 marker; preflight only verifies that
    attestation.
    """
    attested: bool | None
    detail = ""
    try:
        _verify_root_owned_marker(
            marker_path,
            expected_content=SECCOMP_MARKER_CONTENT,
            missing_code="CLAUDE_SANDBOX_SECCOMP_UNVERIFIED",
            permission_code="CLAUDE_SANDBOX_SECCOMP_UNVERIFIED",
            expected_uid=expected_uid,
            label="seccomp attestation marker",
        )
    except RuntimeSecurityError as failure:
        attested, detail = False, failure.message
    else:
        attested = True

    # The attestation marker is the *only* accepted evidence; the seccomp gate
    # itself stays a single fail-closed contract.
    try:
        verify_sandbox_seccomp(attested)
    except RuntimeSecurityError as failure:
        raise _fail(failure.code, f"{failure.message} ({detail})") from None


def verify_sandbox_dependencies(available: Mapping[str, str | None]) -> None:
    """``available`` maps binary name -> resolved path (or None).

    The caller performs the lookup (``shutil.which``); nothing is installed
    here, ever.
    """
    missing = [name for name in REQUIRED_SANDBOX_BINARIES if not available.get(name)]
    if missing:
        raise _fail(
            "CLAUDE_SANDBOX_DEPENDENCY_MISSING",
            f"missing sandbox dependency: {', '.join(missing)}",
        )


def verify_sandbox_seccomp(seccomp_available: bool | None) -> None:
    """``None`` means "could not be determined mechanically" -- never guess."""
    if seccomp_available is not True:
        raise _fail(
            "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED",
            "Claude Sandbox seccomp filter availability could not be verified; "
            "complete Human Runtime Acceptance v2 using the Sandbox-outside / "
            "Sandbox-inside differential probe and V2 attestation marker",
        )


def verify_file_ownership(
    path: str | Path,
    *,
    missing_code: str,
    permission_code: str,
    expected_uid: int = 0,
) -> None:
    target = Path(path)
    if not target.exists():
        raise _fail(missing_code, f"{target} does not exist")
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise _fail(missing_code, f"{target} is not a regular file")
    if info.st_uid != expected_uid:
        raise _fail(
            permission_code, f"{target} must be owned by uid {expected_uid}"
        )
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _fail(permission_code, f"{target} must not be group/world writable")


def verify_managed_settings_contract(
    payload: Mapping[str, Any], *, expected_domains: Sequence[str]
) -> None:
    """Every hard requirement of section 9/16/18 of FIX-R2-004."""
    if not isinstance(payload, Mapping):
        raise _fail("CLAUDE_MANAGED_POLICY_INVALID", "managed settings must be an object")

    if payload.get("requiredMinimumVersion") != REQUIRED_MINIMUM_CLAUDE_VERSION:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"requiredMinimumVersion must be {REQUIRED_MINIMUM_CLAUDE_VERSION}",
        )

    permissions = payload.get("permissions") or {}
    if permissions.get("defaultMode") != "dontAsk":
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", "permissions.defaultMode must be dontAsk"
        )
    if permissions.get("disableBypassPermissionsMode") != "disable":
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            "permissions.disableBypassPermissionsMode must be 'disable'",
        )
    if permissions.get("disableAutoMode") != "disable":
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            "permissions.disableAutoMode must be 'disable'",
        )
    deny = list(permissions.get("deny") or [])
    for rule in ("WebSearch", "WebFetch", "Agent", "mcp__*"):
        if rule not in deny:
            raise _fail(
                "CLAUDE_MANAGED_POLICY_INVALID", f"permissions.deny must contain {rule}"
            )

    verify_edit_permission_rules(permissions.get("allow") or [])

    if payload.get("disableSideloadFlags") is not True:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", "disableSideloadFlags must be true"
        )
    if payload.get("disableRemoteControl") is not True:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", "disableRemoteControl must be true"
        )

    if payload.get("allowManagedPermissionRulesOnly") is not True:
        raise _fail(
            "CLAUDE_PERMISSIONS_NOT_MANAGED_ONLY",
            "allowManagedPermissionRulesOnly must be true",
        )
    if payload.get("allowManagedHooksOnly") is not True:
        raise _fail(
            "CLAUDE_HOOKS_NOT_MANAGED_ONLY", "allowManagedHooksOnly must be true"
        )
    if payload.get("allowManagedMcpServersOnly") is not True:
        raise _fail(
            "CLAUDE_MCP_NOT_LOCKED_DOWN", "allowManagedMcpServersOnly must be true"
        )
    if list(payload.get("allowedMcpServers") or []) != []:
        raise _fail("CLAUDE_MCP_NOT_LOCKED_DOWN", "allowedMcpServers must be empty")
    if payload.get("disableClaudeAiConnectors") is not True:
        raise _fail(
            "CLAUDE_MCP_NOT_LOCKED_DOWN", "disableClaudeAiConnectors must be true"
        )
    if payload.get("disableSkillShellExecution") is not True:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", "disableSkillShellExecution must be true"
        )

    sandbox = payload.get("sandbox") or {}
    if sandbox.get("enabled") is not True or sandbox.get("failIfUnavailable") is not True:
        raise _fail(
            "CLAUDE_SANDBOX_NOT_REQUIRED",
            "sandbox.enabled and sandbox.failIfUnavailable must both be true",
        )
    if (
        sandbox.get("allowUnsandboxedCommands") is not False
        or sandbox.get("autoAllowBashIfSandboxed") is not False
        or sandbox.get("enableWeakerNestedSandbox")
        or sandbox.get("enableWeakerNetworkIsolation")
        or sandbox.get("allowAppleEvents")
    ):
        raise _fail(
            "CLAUDE_SANDBOX_ESCAPE_ENABLED", "a sandbox escape option is enabled"
        )
    if list(sandbox.get("excludedCommands") or []) != []:
        raise _fail(
            "CLAUDE_SANDBOX_EXCLUDED_COMMANDS_PRESENT",
            "sandbox.excludedCommands must be empty",
        )
    filesystem = sandbox.get("filesystem") or {}
    if filesystem.get("disabled") is not False:
        raise _fail(
            "CLAUDE_SANDBOX_FILESYSTEM_DISABLED",
            "sandbox.filesystem.disabled must be false",
        )

    network = sandbox.get("network") or {}
    if network.get("strictAllowlist") is not True:
        raise _fail(
            "CLAUDE_NETWORK_STRICT_ALLOWLIST_DISABLED",
            "sandbox.network.strictAllowlist must be true",
        )
    if network.get("allowManagedDomainsOnly") is not True:
        raise _fail(
            "CLAUDE_NETWORK_MANAGED_DOMAIN_LOCK_DISABLED",
            "sandbox.network.allowManagedDomainsOnly must be true",
        )
    if network.get("allowLocalBinding") is not False:
        raise _fail(
            "CLAUDE_SANDBOX_ESCAPE_ENABLED", "sandbox.network.allowLocalBinding must be false"
        )
    if network.get("allowAllUnixSockets") is not False:
        raise _fail(
            "CLAUDE_SANDBOX_ESCAPE_ENABLED",
            "sandbox.network.allowAllUnixSockets must be false",
        )

    verify_domain_sync(network.get("allowedDomains") or [], expected_domains)

    hooks = payload.get("hooks") or {}
    for event in ("PreToolUse", "ConfigChange"):
        if not hooks.get(event):
            raise _fail(
                "CLAUDE_MANAGED_POLICY_INVALID", f"managed hooks.{event} is missing"
            )


_EDIT_RULE_RE = re.compile(r"^Edit\((?P<target>.*)\)$")


def verify_edit_permission_rules(allow_rules: Iterable[str]) -> tuple[str, ...]:
    """Every ``Edit(...)`` allow rule must use the ``//absolute`` path form."""
    edit_rules: list[str] = []
    for rule in allow_rules:
        match = _EDIT_RULE_RE.match(str(rule))
        if not match:
            continue
        target = match.group("target")
        if not target.startswith("//"):
            raise _fail(
                "CLAUDE_MANAGED_POLICY_INVALID",
                "an absolute Edit permission rule must start with '//': "
                f"{rule!r}",
            )
        if target.startswith("///"):
            raise _fail(
                "CLAUDE_MANAGED_POLICY_INVALID",
                f"malformed absolute Edit permission rule: {rule!r}",
            )
        edit_rules.append(str(rule))
    if not edit_rules:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            "permissions.allow must contain the event_source_extraction Edit rule",
        )
    return tuple(edit_rules)


def verify_domain_sync(
    installed: Iterable[str], expected: Iterable[str]
) -> tuple[str, ...]:
    installed_list = [str(host) for host in installed]
    for host in installed_list:
        validate_allowed_host(host)
    installed_set = set(installed_list)
    expected_set = set(expected)
    if installed_set != expected_set:
        missing = sorted(expected_set - installed_set)
        extra = sorted(installed_set - expected_set)
        raise _fail(
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
            f"allowedDomains mismatch (missing={missing}, extra={extra})",
        )
    return tuple(sorted(installed_set))


def verify_installed_managed_settings(
    managed_settings_path: str | Path,
    *,
    expected: Mapping[str, Any],
    expected_uid: int = 0,
) -> str:
    """Ownership + permission + semantic comparison. Returns the raw SHA256."""
    path = Path(managed_settings_path)
    verify_file_ownership(
        path,
        missing_code="CLAUDE_MANAGED_SETTINGS_MISSING",
        permission_code="CLAUDE_MANAGED_SETTINGS_PERMISSION_INVALID",
        expected_uid=expected_uid,
    )
    raw = path.read_bytes()
    try:
        installed = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID", f"{path} is not valid JSON"
        ) from None

    expected_domains = (
        ((expected.get("sandbox") or {}).get("network") or {}).get("allowedDomains") or []
    )
    verify_managed_settings_contract(installed, expected_domains=expected_domains)

    if _canonical(installed) != _canonical(expected):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"{path} differs semantically from the rendered production policy",
        )
    return sha256_bytes(raw)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def verify_installed_runtime_guard(
    installed_guard_path: str | Path,
    *,
    canonical_guard_path: str | Path = RUNTIME_GUARD_SOURCE_PATH,
    expected_uid: int = 0,
) -> str:
    path = Path(installed_guard_path)
    verify_file_ownership(
        path,
        missing_code="CLAUDE_MANAGED_GUARD_MISSING",
        permission_code="CLAUDE_MANAGED_GUARD_PERMISSION_INVALID",
        expected_uid=expected_uid,
    )
    installed_sha = sha256_file(path)
    canonical_sha = sha256_file(canonical_guard_path)
    if installed_sha != canonical_sha:
        raise _fail(
            "CLAUDE_MANAGED_GUARD_MISMATCH",
            f"{path} does not match ops/claude/daytrade_runtime_guard.py",
        )
    return installed_sha


def verify_source_tree_clean(porcelain_output: str) -> None:
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


def verify_http_user_agent(environ: Mapping[str, str]) -> bool:
    value = environ.get("DAYTRADE_HTTP_USER_AGENT")
    if not value or not value.strip():
        raise _fail(
            "CLAUDE_HTTP_USER_AGENT_MISSING", "DAYTRADE_HTTP_USER_AGENT is not set"
        )
    return True


def verify_runtime_profile(environ: Mapping[str, str]) -> None:
    if environ.get("DAYTRADE_RUNTIME_PROFILE") != REQUIRED_RUNTIME_PROFILE:
        raise _fail(
            "CLAUDE_RUNTIME_PROFILE_MISSING",
            "DAYTRADE_RUNTIME_PROFILE must be 'production'",
        )


# ------------------------------------------------------------- artifact ---


def build_runtime_security_document(
    *,
    target_date: str,
    platform_name: str,
    claude_code_version: str,
    git_head_sha: str,
    production_python: str | Path,
    managed_settings_sha256: str,
    managed_runtime_guard_sha256: str,
    source_matrix_path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
    issuer_registry_path: str | Path = DEFAULT_ISSUER_REGISTRY_PATH,
    allowed_domains: Sequence[str],
    checks: Mapping[str, str],
    http_user_agent_present: bool,
) -> dict[str, Any]:
    missing = [name for name in RUNTIME_SECURITY_CHECKS if name not in checks]
    if missing:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"runtime_security checks incomplete: {missing}",
        )
    not_passed = sorted(name for name, value in checks.items() if value != "PASS")
    if not_passed:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_INVALID",
            f"runtime_security checks did not pass: {not_passed}",
        )
    return {
        "schema_version": RUNTIME_SECURITY_SCHEMA_VERSION,
        "runtime": "claude-code",
        "runtime_profile": REQUIRED_RUNTIME_PROFILE,
        "target_date": target_date,
        "platform": platform_name,
        "claude_code_version": claude_code_version,
        "git_head_sha": git_head_sha,
        "production_python": str(production_python),
        "managed_settings_sha256": managed_settings_sha256,
        "managed_runtime_guard_sha256": managed_runtime_guard_sha256,
        "source_matrix_sha256": sha256_file(source_matrix_path),
        "issuer_domain_registry_sha256": sha256_file(issuer_registry_path),
        "allowed_domains": list(allowed_domains),
        "http_user_agent_present": bool(http_user_agent_present),
        "checks": {name: checks[name] for name in RUNTIME_SECURITY_CHECKS},
    }


# --------------------------------------------------- managed policy replace ---
#
# Replacing an already-installed Managed Policy is a different operation from
# installing one, and it is deliberately harder. The installer refuses to touch
# an existing policy at all, because the policy on a host may be an
# organisation's rather than ours. Replacement makes that decision a human's:
# the operator names, by exact SHA256, both the policy they reviewed on the
# host and the candidate they reviewed from the repository. Nothing here infers
# either value, and there is no force switch.

#: Human review attestations are canonical lowercase SHA256 and nothing else.
#: No trimming, no case folding, no prefix stripping, no shortened digests: a
#: value that needs repairing before it matches is a value the operator did not
#: actually copy from the artefact they reviewed.
SHA256_ATTESTATION_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Owner every Managed Policy file and its directory must have in Production.
PRODUCTION_MANAGED_UID = 0

#: Mode the installed policy carries; also the mode staged files are given
#: before they are moved into place.
MANAGED_SETTINGS_MODE = 0o644


def verify_sha256_attestation(value: Any, *, label: str) -> str:
    """A human-supplied SHA256, accepted only in canonical form."""
    if not isinstance(value, str) or not SHA256_ATTESTATION_PATTERN.fullmatch(value):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_ATTESTATION_INVALID",
            f"{label} must be a canonical lowercase 64-character SHA256; "
            f"got {value!r} (it is never trimmed, lowercased or completed)",
        )
    return value


def _open_managed_directory(root: Path, *, expected_uid: int) -> int:
    """A file descriptor for the Managed Policy directory, once verified.

    Every later operation is anchored to this descriptor rather than to the
    path, so a directory swapped underneath us -- or a symlink planted where
    the directory was -- cannot redirect the write somewhere else.
    """
    try:
        info = os.lstat(root)
    except OSError as error:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID",
            f"{root} cannot be inspected: {error}",
        ) from error
    if stat.S_ISLNK(info.st_mode):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID", f"{root} is a symlink"
        )
    if not stat.S_ISDIR(info.st_mode):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID", f"{root} is not a directory"
        )
    if info.st_uid != expected_uid:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID",
            f"{root} is owned by uid {info.st_uid}, expected {expected_uid}",
        )
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID",
            f"{root} is group- or world-writable (mode {info.st_mode & 0o777:04o})",
        )
    try:
        return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DIRECTORY_INVALID",
            f"{root} cannot be opened as a directory: {error}",
        ) from error


def _verify_managed_file(
    dir_fd: int,
    filename: str,
    *,
    expected_uid: int,
    code: str,
) -> os.stat_result:
    try:
        info = os.lstat(filename, dir_fd=dir_fd)
    except OSError as error:
        raise _fail(code, f"{filename} cannot be inspected: {error}") from error
    if stat.S_ISLNK(info.st_mode):
        raise _fail(code, f"{filename} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise _fail(code, f"{filename} is not a regular file")
    if info.st_uid != expected_uid:
        raise _fail(
            code, f"{filename} is owned by uid {info.st_uid}, expected {expected_uid}"
        )
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _fail(
            code,
            f"{filename} is group- or world-writable (mode {info.st_mode & 0o777:04o})",
        )
    return info


def _read_managed_file(dir_fd: int, filename: str, *, code: str) -> bytes:
    try:
        handle = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as error:
        raise _fail(code, f"{filename} cannot be read: {error}") from error
    try:
        with os.fdopen(handle, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise _fail(code, f"{filename} cannot be read: {error}") from error


def _verified_installed_policy(
    dir_fd: int, *, expected_uid: int
) -> tuple[bytes, str, dict]:
    """The installed policy's bytes, hash and parsed document.

    A target we cannot safely read, or that is not valid JSON, is an error in
    its own right -- never something to be folded into "stale".
    """
    _verify_managed_file(
        dir_fd,
        MANAGED_SETTINGS_FILENAME,
        expected_uid=expected_uid,
        code="CLAUDE_MANAGED_POLICY_TARGET_INVALID",
    )
    payload = _read_managed_file(
        dir_fd, MANAGED_SETTINGS_FILENAME, code="CLAUDE_MANAGED_POLICY_TARGET_INVALID"
    )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_TARGET_INVALID",
            f"{MANAGED_SETTINGS_FILENAME} is not valid JSON: {error}",
        ) from error
    if not isinstance(document, dict):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_TARGET_INVALID",
            f"{MANAGED_SETTINGS_FILENAME} is not a JSON object",
        )
    return payload, sha256_bytes(payload), document


def _verify_installed_guard(
    dir_fd: int,
    *,
    guard_source_path: str | Path,
    expected_uid: int,
) -> str:
    """The installed guard must already equal the repository's canonical one.

    Replacing the policy while the guard is a different program would install a
    policy whose hook points at code nobody reviewed together with it. This
    Work Order does not update the guard as a side effect: it stops instead.
    """
    guard_sha = sha256_bytes(Path(guard_source_path).read_bytes())
    _verify_managed_file(
        dir_fd,
        MANAGED_GUARD_FILENAME,
        expected_uid=expected_uid,
        code="CLAUDE_MANAGED_POLICY_GUARD_MISMATCH",
    )
    installed = _read_managed_file(
        dir_fd, MANAGED_GUARD_FILENAME, code="CLAUDE_MANAGED_POLICY_GUARD_MISMATCH"
    )
    installed_sha = sha256_bytes(installed)
    if installed_sha != guard_sha:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_GUARD_MISMATCH",
            f"installed {MANAGED_GUARD_FILENAME} is {installed_sha}, repository "
            f"canonical guard is {guard_sha}; replace the guard under human "
            "review before replacing the policy",
        )
    return installed_sha


def inspect_managed_policy_replacement(
    *,
    target_root: str | Path,
    rendered_settings: Mapping[str, Any],
    guard_source_path: str | Path = RUNTIME_GUARD_SOURCE_PATH,
    expected_uid: int = PRODUCTION_MANAGED_UID,
) -> dict[str, str]:
    """Read-only readiness report. Writes nothing, anywhere.

    Discovering whether a replacement is needed must not require attempting
    one, so this renders the candidate, hashes both sides and reports. Anything
    it cannot verify raises rather than being reported as merely "stale".
    """
    root = Path(target_root)
    dir_fd = _open_managed_directory(root, expected_uid=expected_uid)
    try:
        _, installed_sha, installed_document = _verified_installed_policy(
            dir_fd, expected_uid=expected_uid
        )
        guard_sha = _verify_installed_guard(
            dir_fd, guard_source_path=guard_source_path, expected_uid=expected_uid
        )
    finally:
        os.close(dir_fd)

    candidate_bytes = serialize_managed_settings(rendered_settings)
    candidate_sha = sha256_bytes(candidate_bytes)
    current = installed_document == json.loads(candidate_bytes.decode("utf-8"))

    return {
        "installed_policy_sha256": installed_sha,
        "rendered_policy_sha256": candidate_sha,
        "installed_policy_status": "CURRENT" if current else "STALE",
        "runtime_guard_status": "CURRENT",
        "runtime_guard_sha256": guard_sha,
        "replacement_ready": "false" if current else "true",
    }


def replace_managed_policy(
    *,
    target_root: str | Path,
    rendered_settings: Mapping[str, Any],
    expected_installed_sha256: str,
    expected_rendered_sha256: str,
    guard_source_path: str | Path = RUNTIME_GUARD_SOURCE_PATH,
    expected_uid: int = PRODUCTION_MANAGED_UID,
) -> dict[str, str]:
    """Replace a reviewed Managed Policy with a reviewed candidate.

    Compare-and-swap, twice over. The installed hash is checked when the
    operation starts *and* again immediately before the rename, so a policy
    that changed while the candidate was being staged is never silently
    adopted as the new baseline. The candidate is always rendered here from
    the repository -- there is no interface for installing a JSON file someone
    edited by hand.

    The old file is never truncated or edited: the candidate is staged beside
    it in the same directory and moved over it in one atomic rename. Any
    failure before that rename leaves the installed bytes exactly as they were.
    """
    expected_installed_sha256 = verify_sha256_attestation(
        expected_installed_sha256, label="expected installed policy SHA256"
    )
    expected_rendered_sha256 = verify_sha256_attestation(
        expected_rendered_sha256, label="expected rendered policy SHA256"
    )

    expected_domains = (
        ((rendered_settings.get("sandbox") or {}).get("network") or {}).get(
            "allowedDomains"
        )
        or []
    )
    verify_managed_settings_contract(rendered_settings, expected_domains=expected_domains)

    candidate_bytes = serialize_managed_settings(rendered_settings)
    candidate_sha = sha256_bytes(candidate_bytes)
    if candidate_sha != expected_rendered_sha256:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_CANDIDATE_SHA_MISMATCH",
            f"the candidate rendered from this repository is {candidate_sha}, "
            f"but {expected_rendered_sha256} was reviewed; nothing was written",
        )

    root = Path(target_root)
    dir_fd = _open_managed_directory(root, expected_uid=expected_uid)
    staged_name: str | None = None
    try:
        installed_info = _verify_managed_file(
            dir_fd,
            MANAGED_SETTINGS_FILENAME,
            expected_uid=expected_uid,
            code="CLAUDE_MANAGED_POLICY_TARGET_INVALID",
        )
        _, installed_sha, _ = _verified_installed_policy(
            dir_fd, expected_uid=expected_uid
        )
        if installed_sha != expected_installed_sha256:
            raise _fail(
                "CLAUDE_MANAGED_POLICY_INSTALLED_SHA_MISMATCH",
                f"the installed policy is {installed_sha}, but "
                f"{expected_installed_sha256} was reviewed; nothing was written",
            )
        _verify_installed_guard(
            dir_fd, guard_source_path=guard_source_path, expected_uid=expected_uid
        )

        staged_name = _stage_candidate(
            dir_fd,
            candidate_bytes,
            candidate_sha=candidate_sha,
            owner=(installed_info.st_uid, installed_info.st_gid),
        )

        # Commit point. Re-verify the target one last time: everything above
        # took time, and the policy we are about to overwrite must still be
        # the exact one the operator reviewed.
        _verify_managed_file(
            dir_fd,
            MANAGED_SETTINGS_FILENAME,
            expected_uid=expected_uid,
            code="CLAUDE_MANAGED_POLICY_TARGET_INVALID",
        )
        _, commit_sha, _ = _verified_installed_policy(dir_fd, expected_uid=expected_uid)
        if commit_sha != expected_installed_sha256:
            raise _fail(
                "CLAUDE_MANAGED_POLICY_INSTALLED_SHA_MISMATCH",
                f"the installed policy changed to {commit_sha} while the "
                f"candidate was staged; {expected_installed_sha256} was "
                "reviewed and the change is not adopted as a new baseline",
            )

        try:
            os.replace(
                staged_name,
                MANAGED_SETTINGS_FILENAME,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
        except OSError as error:
            raise _fail(
                "CLAUDE_MANAGED_POLICY_REPLACE_FAILED",
                f"the atomic replacement failed: {error}",
            ) from error
        staged_name = None
        _fsync_directory(dir_fd)

        _verify_replacement(
            dir_fd,
            expected_uid=expected_uid,
            candidate_sha=candidate_sha,
            rendered_settings=rendered_settings,
        )
    finally:
        if staged_name is not None:
            # Never left behind as a candidate, and never kept as a backup.
            try:
                os.unlink(staged_name, dir_fd=dir_fd)
            except OSError:
                pass
        os.close(dir_fd)

    return {
        "managed_settings_path": str(root / MANAGED_SETTINGS_FILENAME),
        "replaced_policy_sha256": expected_installed_sha256,
        "managed_settings_sha256": candidate_sha,
    }


def _stage_candidate(
    dir_fd: int,
    candidate_bytes: bytes,
    *,
    candidate_sha: str,
    owner: tuple[int, int],
) -> str:
    """Write the candidate beside the target, fully, before anything moves.

    Exclusive creation in the target's own directory: same filesystem, so the
    rename that follows is atomic, and no pre-existing file can be adopted as
    the staged candidate.
    """
    staged_name = f".{MANAGED_SETTINGS_FILENAME}.replace.{os.getpid()}.{os.urandom(8).hex()}"
    try:
        handle = os.open(
            staged_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            MANAGED_SETTINGS_MODE,
            dir_fd=dir_fd,
        )
    except OSError as error:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_STAGING_FAILED",
            f"the staging file could not be created: {error}",
        ) from error

    try:
        with os.fdopen(handle, "wb") as stream:
            # Contents and final metadata are both settled before the fsync, so
            # what is made durable is the file exactly as it will be renamed
            # into place -- not a correct file that still has to be chmod'd
            # afterwards.
            _write_staged_bytes(stream, candidate_bytes)
            os.fchmod(stream.fileno(), MANAGED_SETTINGS_MODE)
            os.fchown(stream.fileno(), owner[0], owner[1])
            os.fsync(stream.fileno())
        staged = _read_managed_file(
            dir_fd, staged_name, code="CLAUDE_MANAGED_POLICY_STAGING_FAILED"
        )
        if sha256_bytes(staged) != candidate_sha:
            raise _fail(
                "CLAUDE_MANAGED_POLICY_STAGING_FAILED",
                "the staged candidate does not hash to the reviewed candidate",
            )
    except RuntimeSecurityError:
        _discard_staged(dir_fd, staged_name)
        raise
    except OSError as error:
        _discard_staged(dir_fd, staged_name)
        raise _fail(
            "CLAUDE_MANAGED_POLICY_STAGING_FAILED",
            f"the candidate could not be staged: {error}",
        ) from error
    return staged_name


#: Errnos that mean "this filesystem does not implement fsync on a directory",
#: not "the write may be lost". Enumerated deliberately: an unknown errno is a
#: real I/O failure until proven otherwise, and treating it as unsupported
#: would turn a lost rename into a reported success.
DIRECTORY_FSYNC_UNSUPPORTED_ERRNOS = frozenset(
    {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}
)


def _fsync_directory(dir_fd: int) -> None:
    """Make the rename durable, or fail.

    The rename already happened, so there is nothing to roll back to and this
    Work Order keeps no backup. What must not happen is reporting success for a
    replacement whose directory entry may not survive a power loss: an EIO or
    ENOSPC here is a failure the operator has to look at.
    """
    try:
        os.fsync(dir_fd)
    except OSError as error:
        if error.errno in DIRECTORY_FSYNC_UNSUPPORTED_ERRNOS:
            return
        raise _fail(
            "CLAUDE_MANAGED_POLICY_DURABILITY_FAILED",
            f"the replacement was written but the directory could not be "
            f"flushed ({error}); no backup is kept and no rollback is "
            "attempted: human inspection required",
        ) from error


def _write_staged_bytes(stream: Any, payload: bytes) -> None:
    """Write the candidate into the staged file and flush Python's buffer."""
    stream.write(payload)
    stream.flush()


def _discard_staged(dir_fd: int, staged_name: str) -> None:
    try:
        os.unlink(staged_name, dir_fd=dir_fd)
    except OSError:
        pass


def _verify_replacement(
    dir_fd: int,
    *,
    expected_uid: int,
    candidate_sha: str,
    rendered_settings: Mapping[str, Any],
) -> None:
    """Confirm what actually landed. A failure here is not recoverable here.

    The replacement already happened, and this Work Order keeps no backup to
    roll back to, so the honest outcome is a failure that a human inspects --
    never a success reported by relaxing the check that just failed.
    """
    try:
        _verify_managed_file(
            dir_fd,
            MANAGED_SETTINGS_FILENAME,
            expected_uid=expected_uid,
            code="CLAUDE_MANAGED_POLICY_POST_VERIFY_FAILED",
        )
        final = _read_managed_file(
            dir_fd,
            MANAGED_SETTINGS_FILENAME,
            code="CLAUDE_MANAGED_POLICY_POST_VERIFY_FAILED",
        )
    except RuntimeSecurityError as error:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_POST_VERIFY_FAILED",
            f"{error.message}; the replacement already happened and no backup "
            "is kept: human inspection required",
        ) from error

    final_sha = sha256_bytes(final)
    if final_sha != candidate_sha:
        raise _fail(
            "CLAUDE_MANAGED_POLICY_POST_VERIFY_FAILED",
            f"the installed policy is {final_sha} after replacement, expected "
            f"{candidate_sha}; human inspection required",
        )
    if json.loads(final.decode("utf-8")) != json.loads(
        serialize_managed_settings(rendered_settings).decode("utf-8")
    ):
        raise _fail(
            "CLAUDE_MANAGED_POLICY_POST_VERIFY_FAILED",
            "the installed policy is not semantically the rendered policy; "
            "human inspection required",
        )


# --------------------------------------------------------------- deploy ---


def deploy_managed_policy(
    *,
    target_root: str | Path,
    rendered_settings: Mapping[str, Any],
    guard_source_path: str | Path = RUNTIME_GUARD_SOURCE_PATH,
    dry_run: bool = False,
) -> dict[str, str]:
    """Testable core of ``scripts/deploy-claude-managed-policy``.

    Refuses -- always, with no ``--force`` escape hatch -- to overwrite an
    existing managed policy or a divergent installed guard. Merging with an
    existing organisational policy is a human review task, not an automated
    one.
    """
    root = Path(target_root)
    settings_target = root / MANAGED_SETTINGS_FILENAME
    guard_target = root / MANAGED_GUARD_FILENAME

    guard_bytes = Path(guard_source_path).read_bytes()
    guard_sha = sha256_bytes(guard_bytes)

    if settings_target.exists():
        raise _fail(
            "EXISTING_MANAGED_POLICY_PRESENT",
            f"{settings_target} already exists; human review required "
            "(no automatic merge, backup or overwrite)",
        )
    if guard_target.exists() and sha256_file(guard_target) != guard_sha:
        raise _fail(
            "EXISTING_MANAGED_POLICY_PRESENT",
            f"{guard_target} already exists with different content; "
            "human review required",
        )

    expected_domains = (
        ((rendered_settings.get("sandbox") or {}).get("network") or {}).get(
            "allowedDomains"
        )
        or []
    )
    verify_managed_settings_contract(rendered_settings, expected_domains=expected_domains)

    settings_bytes = serialize_managed_settings(rendered_settings)

    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
        settings_target.write_bytes(settings_bytes)
        guard_target.write_bytes(guard_bytes)
        os.chmod(settings_target, 0o644)
        os.chmod(guard_target, 0o755)

    return {
        "managed_settings_path": str(settings_target),
        "managed_settings_sha256": sha256_bytes(settings_bytes),
        "managed_runtime_guard_path": str(guard_target),
        "managed_runtime_guard_sha256": guard_sha,
    }
