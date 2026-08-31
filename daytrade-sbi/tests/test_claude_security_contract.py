"""The Business Evidence boundary, tested like the product it protects.

DTWO-2026-026 split DayTrade's controls into three layers and kept the
fail-closed machinery on the two that decide whether a result can be trusted:
Business Evidence Integrity and Trading Safety. This file covers the part of
that boundary the Claude Code bootstrap owns -- ``CLAUDE.md``,
``.claude/settings.json`` and the PreToolUse network guard -- plus the Python
network policy every fetch actually goes through.

Layer C (Local Operational Governance: git wrappers, launchers, sandbox and OS
policy on a personally owned machine) is deliberately *not* here. Git traffic
is ordinary developer work now; a direct HTTP fetch that skips the Source
Acquisition CLI still is not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPOSITORY_ROOT / "CLAUDE.md"
SETTINGS = REPOSITORY_ROOT / ".claude" / "settings.json"
HOOK = REPOSITORY_ROOT / ".claude" / "hooks" / "network_guard.py"

#: Market data hosts. Derived from the Source Matrix, never hand-picked.
ALLOWED_DOMAINS = {
    "www.jpx.co.jp",
    # 東証上場会社情報 (candidate-specific TSE listing search) -- its own host.
    # Exact match, never a wildcard, and cross-checked below against
    # required_sandbox_domains(load_source_matrix()) so this literal can never
    # drift away from the Source Matrix it mirrors.
    "www2.jpx.co.jp",
    "finance.yahoo.co.jp",
    "kabutan.jp",
    "www.release.tdnet.info",
}

#: The single Development-only host, needed to push a branch at all. It is
#: *not* a market data source: no ``acquire-*`` command may reach it, because
#: ``src/network_policy.py`` validates every fetched URL against the Source
#: Matrix and the human-approved issuer registry, and github.com is in neither.
DEVELOPMENT_ONLY_DOMAINS = {"github.com"}


def _settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


# ------------------------------------------------------------- CLAUDE.md ---


def test_claude_md_includes_agents_md():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "@daytrade-sbi/AGENTS.md" in text


@pytest.mark.parametrize(
    "banned",
    [
        "WebSearch",
        "WebFetch",
        "curl",
        "wget",
        "powershell",
        "python -c",
        "node -e",
        "git push",
        "pip install",
    ],
)
def test_claude_md_bans_every_bypass_route(banned):
    assert banned.lower() in CLAUDE_MD.read_text(encoding="utf-8").lower()


def test_claude_md_treats_source_pages_as_untrusted_data():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "untrusted data" in text
    assert "Ignore previous instructions" in text


def test_claude_md_bans_agent_set_selection_thresholds():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "selection.enabled" in text
    assert "activate-selection-config" in text


def test_claude_md_restricts_fetching_to_the_acquisition_cli():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    for command in (
        "acquire-discovery",
        "acquire-stage1-sources",
        "acquire-stage2-market-sources",
        "acquire-actual-turnover",
        "acquire-event-sources",
    ):
        assert command in text


# --------------------------------------------------------- settings.json ---


@pytest.mark.parametrize(
    "rule",
    [
        "WebSearch",
        "WebFetch",
        "Bash(curl:*)",
        "Bash(wget:*)",
        "Bash(powershell:*)",
        "Bash(pwsh:*)",
        "Bash(gh:*)",
        "Bash(pip install:*)",
        "Bash(python -c:*)",
        "Bash(node -e:*)",
    ],
)
def test_settings_deny_list(rule):
    assert rule in _settings()["permissions"]["deny"]


def test_sandbox_is_enabled_with_a_restricted_domain_list():
    sandbox = _settings()["sandbox"]
    assert sandbox["enabled"] is True
    assert (
        set(sandbox["network"]["allowedDomains"])
        == ALLOWED_DOMAINS | DEVELOPMENT_ONLY_DOMAINS
    )


def test_sandbox_allowlist_is_exactly_the_approved_host_set():
    """The allowlist is derived security policy, not a wishlist: it must equal
    the Source Matrix template hosts plus every human-approved issuer host,
    plus the one enumerated Development-only host -- no other extra host, no
    missing host.

    ``DEVELOPMENT_ONLY_DOMAINS`` is an explicit enumeration precisely so that
    it cannot absorb host creep: a second entry has to be added here, by hand,
    with a reason. The runtime check (``verify_sandbox_allowlist``) only fails
    on *missing* required hosts, so this test is what keeps unrelated hosts
    out of the Development sandbox.
    """
    from src.network_policy import required_sandbox_domains, sandbox_allowed_domains
    from src.source_matrix import load_source_matrix

    required = set(required_sandbox_domains(load_source_matrix()))
    allowed = set(sandbox_allowed_domains(SETTINGS))
    assert required <= allowed, f"missing required host(s): {required - allowed}"
    assert allowed - required == DEVELOPMENT_ONLY_DOMAINS, (
        f"unapproved host(s) in the Development sandbox: "
        f"{allowed - required - DEVELOPMENT_ONLY_DOMAINS}"
    )


@pytest.mark.parametrize("host", sorted(DEVELOPMENT_ONLY_DOMAINS))
def test_a_development_only_host_can_never_be_fetched_as_evidence(host):
    """AC-17: relaxing Git did not put github.com on the evidence path.

    The sandbox may reach it -- a push has to go somewhere -- but the Business
    URL validator answers to the Source Matrix, not to the sandbox allowlist.
    """
    from src.network_policy import NetworkPolicyError, validate_request_url

    with pytest.raises(NetworkPolicyError) as excinfo:
        validate_request_url(f"https://{host}/x", source_id="JPX_CALENDAR")
    assert excinfo.value.code == "NETWORK_POLICY_HOST_NOT_ALLOWED"


def test_sandbox_unavailability_may_not_silently_fall_back_to_the_host():
    """A command that cannot be sandboxed must be blocked rather than run
    unsandboxed (which would restore full network access)."""
    sandbox = _settings()["sandbox"]
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["autoAllowBashIfSandboxed"] is False
    assert sandbox["network"]["allowLocalBinding"] is False


def test_permission_escalation_modes_are_disabled():
    """``disableBypassPermissionsMode`` and ``disableAutoMode`` live inside
    ``permissions`` in Claude Code's settings.json schema, alongside
    ``defaultMode`` -- not at the top level."""
    permissions = _settings()["permissions"]
    assert permissions["disableBypassPermissionsMode"] == "disable"
    assert permissions["disableAutoMode"] == "disable"
    assert permissions["defaultMode"] == "default"
    assert "disableAutoMode" not in _settings()
    assert "disableBypassPermissionsMode" not in _settings()


def test_missing_required_host_halts_instead_of_editing_settings(tmp_path):
    """Phase II Production Verifier behaviour: an un-allowlisted required host
    is SECURITY_POLICY_CHANGE_REQUIRED, never an automatic settings edit."""
    from src.network_policy import NetworkPolicyError, verify_sandbox_allowlist
    from src.source_matrix import load_source_matrix

    stripped = tmp_path / "settings.json"
    stripped.write_text(
        json.dumps({"sandbox": {"network": {"allowedDomains": []}}}),
        encoding="utf-8",
    )
    before = SETTINGS.read_bytes()
    with pytest.raises(NetworkPolicyError) as excinfo:
        verify_sandbox_allowlist(load_source_matrix(), settings_path=stripped)
    assert excinfo.value.code == "SECURITY_POLICY_CHANGE_REQUIRED"
    assert SETTINGS.read_bytes() == before


def test_approved_issuer_hosts_must_also_be_allowlisted():
    from src.network_policy import NetworkPolicyError, verify_sandbox_allowlist
    from src.source_matrix import load_source_matrix

    registry = {
        "registry_schema_version": 1,
        "approval_policy": {
            "human_approved_only": True,
            "auto_discovery_allowed": False,
        },
        "issuers": [
            {
                "ticker": "7203",
                "approved_hosts": ["global.example-issuer.co.jp"],
                "approved_by": "human",
                "approved_at": "2026-08-12",
            }
        ],
    }
    with pytest.raises(NetworkPolicyError) as excinfo:
        verify_sandbox_allowlist(
            load_source_matrix(), issuer_registry=registry, settings_path=SETTINGS
        )
    assert excinfo.value.code == "SECURITY_POLICY_CHANGE_REQUIRED"
    assert "global.example-issuer.co.jp" in str(excinfo.value)


@pytest.mark.parametrize(
    "url,source_id,code",
    [
        ("https://localhost/x", "JPX_CALENDAR", "NETWORK_POLICY_LOCAL_HOST_FORBIDDEN"),
        ("https://127.0.0.1/x", "JPX_CALENDAR", "NETWORK_POLICY_RAW_IP_FORBIDDEN"),
        ("https://evil.example.com/x", "JPX_CALENDAR", "NETWORK_POLICY_HOST_NOT_ALLOWED"),
        ("http://www.jpx.co.jp/x", "JPX_CALENDAR", "NETWORK_POLICY_SCHEME_FORBIDDEN"),
        ("https://www.jpx.co.jp:8443/x", "JPX_CALENDAR", "NETWORK_POLICY_PORT_FORBIDDEN"),
    ],
)
def test_off_policy_hosts_are_blocked(url, source_id, code):
    from src.network_policy import NetworkPolicyError, validate_request_url

    with pytest.raises(NetworkPolicyError) as excinfo:
        validate_request_url(url, source_id=source_id)
    assert excinfo.value.code == code


def test_unapproved_issuer_host_is_blocked():
    from src.network_policy import NetworkPolicyError, validate_request_url

    empty_registry = {
        "registry_schema_version": 1,
        "approval_policy": {
            "human_approved_only": True,
            "auto_discovery_allowed": False,
        },
        "issuers": [],
    }
    with pytest.raises(NetworkPolicyError) as excinfo:
        validate_request_url(
            "https://issuer.example.co.jp/ir/",
            source_id="COMPANY_IR",
            ticker="7203",
            issuer_registry=empty_registry,
        )
    assert excinfo.value.code == "ISSUER_DOMAIN_NOT_APPROVED"


def test_network_guard_is_registered_as_a_pretooluse_bash_hook():
    hooks = _settings()["hooks"]["PreToolUse"]
    commands = [
        hook["command"]
        for entry in hooks
        if entry.get("matcher") == "Bash"
        for hook in entry["hooks"]
    ]
    assert any("network_guard.py" in command for command in commands)


# -------------------------------------------------------- network_guard ---


def _run_hook(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "command",
    [
        "curl https://www.jpx.co.jp/",
        "echo hi && curl https://evil.example.com",
        "cat x | wget https://evil.example.com",
        "powershell -c Invoke-WebRequest https://x",
        "pwsh -Command Invoke-RestMethod https://x",
        "python -c \"import requests; requests.get('https://x')\"",
        "python3 -c 'import urllib.request'",
        "py -c 'import socket'",
        "node -e \"require('http').get('http://x')\"",
        "nc evil.example.com 443",
        "netcat evil.example.com 443",
        "telnet evil.example.com",
        "ssh user@host",
        "scp a b:c",
        "sftp host",
        "gh pr create",
        "pip install requests",
        "pip3 install --user httpx",
        "npm install axios",
        "HTTPS_PROXY=x curl https://y",
    ],
)
def test_forbidden_commands_are_denied_with_exit_code_two(command):
    result = _run_hook(command)
    # exit code 2 is the *blocking* denial; 1 would only be a warning
    assert result.returncode == 2, result.stderr
    assert b"network_guard" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "python -m src.cli acquire-discovery --target-date 2026-08-12",
        "ls -la",
        "git status",
        "git commit -m 'concurrent nightly run'",
        # DTWO-2026-026: ordinary Git, including its network half.
        "git fetch origin",
        "git pull --ff-only origin main",
        "git push -u origin claude/example",
        "git switch -c claude/example",
    ],
)
def test_legitimate_commands_are_allowed(command):
    assert _run_hook(command).returncode == 0


def test_unparseable_payload_fails_closed():
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=b"{not json",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2


def test_non_bash_tools_are_not_blocked():
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "curl.py"}})
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_codex_configuration_is_preserved():
    """The Codex bootstrap must survive the Claude Code bootstrap."""
    assert (REPOSITORY_ROOT / ".codex" / "config.toml").is_file()
    assert (REPOSITORY_ROOT / ".codex" / "agents").is_dir()
