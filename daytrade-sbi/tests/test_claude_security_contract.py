"""The Claude Code security bootstrap is part of the product, so it is tested
like the product: settings, rules file, and the PreToolUse network guard."""

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

ALLOWED_DOMAINS = {
    "www.jpx.co.jp",
    "finance.yahoo.co.jp",
    "kabutan.jp",
    "www.release.tdnet.info",
}


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
        "Bash(git fetch:*)",
        "Bash(git pull:*)",
        "Bash(git push:*)",
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
    assert set(sandbox["network"]["allowedDomains"]) == ALLOWED_DOMAINS


def test_sandbox_allowlist_is_exactly_the_approved_host_set():
    """The allowlist is derived security policy, not a wishlist: it must equal
    the Source Matrix template hosts plus every human-approved issuer host --
    no extra host, no missing host."""
    from src.network_policy import required_sandbox_domains, sandbox_allowed_domains
    from src.source_matrix import load_source_matrix

    required = set(required_sandbox_domains(load_source_matrix()))
    assert set(sandbox_allowed_domains(SETTINGS)) == required


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
        "git fetch origin",
        "git pull --rebase",
        "git push -u origin main",
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
