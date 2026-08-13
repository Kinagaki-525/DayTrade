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


def test_permission_escalation_modes_are_disabled():
    settings = _settings()
    assert settings["disableBypassPermissionsMode"] == "disable"
    assert settings["disableAutoMode"] == "disable"


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
