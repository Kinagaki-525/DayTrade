"""Network access policy for deterministic source acquisition.

Every outbound request in this repository must pass through
:func:`validate_request_url` before any transport is invoked. The policy is
fail-closed: anything not explicitly permitted is rejected with a stable
``NETWORK_POLICY_*`` reason code.

No AI component may relax this module at runtime: the allowed-host set and the
issuer domain registry are static configuration reviewed by a human.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.config import load_yaml
from src.contracts import validate_json_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ISSUER_DOMAIN_REGISTRY_PATH = (
    PROJECT_ROOT / "config" / "issuer_domain_registry.yaml"
)

#: Hosts reachable without an issuer-domain registry entry. Exact match only:
#: no subdomain wildcards, no suffix matching.
ALLOWED_HOSTS = frozenset(
    {
        "www.jpx.co.jp",
        "finance.yahoo.co.jp",
        "kabutan.jp",
        "www.release.tdnet.info",
    }
)

#: Source ids whose host is resolved through the issuer domain registry
#: instead of :data:`ALLOWED_HOSTS`.
ISSUER_SOURCE_IDS = frozenset({"COMPANY_IR", "COMPANY_IR_DISCLOSURE"})

ALLOWED_SCHEMES = frozenset({"https"})

_HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

LOCAL_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


class NetworkPolicyError(ValueError):
    """Raised when a requested URL violates the network access policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedRequest:
    """A URL that passed policy validation, in its canonical request form."""

    url: str
    host: str
    scheme: str
    source_id: str


def _fail(code: str, message: str) -> NetworkPolicyError:
    return NetworkPolicyError(code, message)


#: Environment override for the registry *location* only. It selects which
#: human-approved file to read; it can never introduce an approval, because
#: the file it points at is still schema-validated and still requires explicit
#: human approval metadata per entry.
ISSUER_DOMAIN_REGISTRY_PATH_ENV_VAR = "DAYTRADE_ISSUER_DOMAIN_REGISTRY"


def issuer_domain_registry_path() -> Path:
    import os

    override = os.environ.get(ISSUER_DOMAIN_REGISTRY_PATH_ENV_VAR)
    if override and override.strip():
        return Path(override.strip())
    return DEFAULT_ISSUER_DOMAIN_REGISTRY_PATH


def load_issuer_domain_registry(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Load and schema-validate the human-approved issuer domain registry."""
    if path is None:
        path = issuer_domain_registry_path()
    payload = load_yaml(path)
    validate_json_document(payload, "issuer_domain_registry.schema.json")
    entries = payload["issuers"]
    tickers = [entry["ticker"] for entry in entries]
    if len(tickers) != len(set(tickers)):
        raise ValueError(
            "issuer_domain_registry.yaml contains duplicate ticker entries"
        )
    return payload


def issuer_domains_by_ticker(registry: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        entry["ticker"]: tuple(entry["approved_hosts"])
        for entry in registry["issuers"]
    }


def approved_issuer_hosts(
    ticker: str,
    registry: dict[str, Any],
) -> tuple[str, ...]:
    """Return the approved hosts for ``ticker``.

    Raises ``ISSUER_DOMAIN_NOT_APPROVED`` when the ticker has no
    human-approved registry entry. There is no auto-discovery fallback.
    """
    hosts = issuer_domains_by_ticker(registry).get(ticker)
    if not hosts:
        raise _fail(
            "ISSUER_DOMAIN_NOT_APPROVED",
            f"ticker {ticker!r} has no human-approved issuer domain entry",
        )
    return hosts


def canonical_request_url(url: str) -> str:
    """Strip the fragment; everything else is preserved byte-for-byte."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _host_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _fail(
            "NETWORK_POLICY_USERINFO_FORBIDDEN",
            "URL must not carry userinfo credentials",
        )
    if parts.port is not None:
        raise _fail(
            "NETWORK_POLICY_PORT_FORBIDDEN",
            "URL must not specify an explicit port",
        )
    host = (parts.hostname or "").lower()
    if not host:
        raise _fail("NETWORK_POLICY_HOST_MISSING", "URL has no host component")
    if host in LOCAL_HOSTNAMES:
        raise _fail(
            "NETWORK_POLICY_LOCAL_HOST_FORBIDDEN",
            f"loopback host is forbidden: {host}",
        )
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise _fail(
            "NETWORK_POLICY_RAW_IP_FORBIDDEN",
            f"raw IP address hosts are forbidden: {host}",
        )
    if not _HOST_PATTERN.match(host):
        raise _fail(
            "NETWORK_POLICY_HOST_MALFORMED",
            f"host is not a well-formed domain name: {host}",
        )
    return host


def validate_request_url(
    url: Any,
    *,
    source_id: str,
    ticker: str | None = None,
    issuer_registry: dict[str, Any] | None = None,
) -> ValidatedRequest:
    """Validate ``url`` against the network access policy.

    ``source_id`` selects the host allowlist: issuer sources resolve their
    approved hosts from the human-approved issuer domain registry keyed by
    ``ticker``; every other source must target one of :data:`ALLOWED_HOSTS`.
    """
    if not isinstance(url, str) or not url.strip():
        raise _fail("NETWORK_POLICY_URL_MALFORMED", f"URL must be a non-empty string: {url!r}")
    if url != url.strip():
        raise _fail(
            "NETWORK_POLICY_URL_MALFORMED",
            "URL must not carry leading/trailing whitespace",
        )
    if any(ch.isspace() for ch in url):
        raise _fail("NETWORK_POLICY_URL_MALFORMED", "URL must not contain whitespace")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise _fail(
            "NETWORK_POLICY_SCHEME_FORBIDDEN",
            f"scheme must be https, got {parts.scheme!r}",
        )

    host = _host_of(url)

    if source_id in ISSUER_SOURCE_IDS:
        if not ticker:
            raise _fail(
                "NETWORK_POLICY_TICKER_REQUIRED",
                f"{source_id} requires a ticker to resolve its approved issuer domain",
            )
        registry = (
            issuer_registry
            if issuer_registry is not None
            else load_issuer_domain_registry()
        )
        approved = approved_issuer_hosts(ticker, registry)
        if host not in approved:
            raise _fail(
                "ISSUER_DOMAIN_HOST_MISMATCH",
                f"host {host} is not an approved issuer domain for ticker {ticker}",
            )
    elif host not in ALLOWED_HOSTS:
        raise _fail(
            "NETWORK_POLICY_HOST_NOT_ALLOWED",
            f"host {host} is not in the allowed host list",
        )

    return ValidatedRequest(
        url=canonical_request_url(url),
        host=host,
        scheme=scheme,
        source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Claude Code sandbox allowlist reconciliation (FIX-001).
#
# The sandbox host allowlist in ``.claude/settings.json`` is *human-approved
# security policy*. Nothing in this repository may edit it: the only supported
# behaviour when a required host is missing is to HALT with
# SECURITY_POLICY_CHANGE_REQUIRED and let a human make the change.
# ---------------------------------------------------------------------------

CLAUDE_SETTINGS_PATH = PROJECT_ROOT.parent / ".claude" / "settings.json"

SECURITY_POLICY_CHANGE_REQUIRED = "SECURITY_POLICY_CHANGE_REQUIRED"


def matrix_template_hosts(source_matrix: dict[str, Any]) -> tuple[str, ...]:
    """Every fixed host that appears in a Source Matrix ``url_template``.

    Issuer templates (``https://{issuer_domain}/...``) contribute no host:
    their hosts come from the human-approved issuer domain registry.
    """
    hosts: set[str] = set()
    for definition in source_matrix.get("sources", []):
        template = str(definition.get("url_template", ""))
        host = urlsplit(template).hostname or ""
        if not host or "{" in host:
            continue
        hosts.add(host.lower())
    return tuple(sorted(hosts))


def required_sandbox_domains(
    source_matrix: dict[str, Any],
    issuer_registry: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """The exact host allowlist the sandbox must carry for this Source Matrix.

    = Source Matrix template hosts + every human-approved issuer host. An
    empty issuer registry contributes nothing, which is the current state.
    """
    hosts = set(matrix_template_hosts(source_matrix))
    registry = (
        issuer_registry
        if issuer_registry is not None
        else load_issuer_domain_registry()
    )
    for entry in registry.get("issuers", []):
        for host in entry.get("approved_hosts", []):
            hosts.add(str(host).lower())
    return tuple(sorted(hosts))


def sandbox_allowed_domains(
    settings_path: str | Path = CLAUDE_SETTINGS_PATH,
) -> tuple[str, ...]:
    import json

    payload = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    sandbox = payload.get("sandbox") or {}
    network = sandbox.get("network") or {}
    return tuple(
        sorted(str(host).lower() for host in network.get("allowedDomains", []))
    )


def verify_sandbox_allowlist(
    source_matrix: dict[str, Any],
    *,
    issuer_registry: dict[str, Any] | None = None,
    settings_path: str | Path = CLAUDE_SETTINGS_PATH,
) -> tuple[str, ...]:
    """Halt with SECURITY_POLICY_CHANGE_REQUIRED if a required host is absent.

    This never edits ``settings.json``: changing the sandbox allowlist is a
    human security decision, not an automated remediation.
    """
    required = required_sandbox_domains(source_matrix, issuer_registry)
    allowed = set(sandbox_allowed_domains(settings_path))
    missing = [host for host in required if host not in allowed]
    if missing:
        raise _fail(
            SECURITY_POLICY_CHANGE_REQUIRED,
            "the Claude Code sandbox host allowlist does not cover required "
            "host(s): " + ", ".join(missing) + ". A HUMAN must approve and add "
            "them to .claude/settings.json; this pipeline never edits it.",
        )
    return required
