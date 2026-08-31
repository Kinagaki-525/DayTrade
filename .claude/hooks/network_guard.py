#!/usr/bin/env python3
"""PreToolUse hook: Business Evidence Acquisition Bypass Guard.

DayTrade's market numbers must come from exactly one path: the repository's
Source Acquisition CLI (``acquire-*``), which fetches with a deterministic curl
subprocess, stores the raw bytes with their SHA256, and lets only the
deterministic parsers in ``src/source_parsers/`` turn those bytes into values.
An agent that fetches a page by itself -- with curl, with ``python -c`` and
``requests``, with ``node -e`` -- and then reads a number off it bypasses every
one of those controls. That is the failure this hook exists to prevent, and it
is the only thing it judges.

DTWO-2026-026 removed this hook's second job. It used to enforce a raw-Git
authority model (which subcommands, which options, which pathspecs, which
commit-message forms) so that pushes could only happen through
``scripts/claude-safe-push``. That was Layer C -- Local Operational Governance
on a personally owned machine -- not Business Evidence integrity, and ordinary
Git is now the standard Development workflow. Git traffic is no longer
inspected here at all.

Still refused (a partial second layer; ``.claude/settings.json`` carries the
matching permission denies): direct HTTP clients, network-capable one-liner
interpreters, raw sockets, remote shells, and package installs.

Exit codes:
  0 -- allow
  2 -- deny (blocking error fed back to the agent)

Exit code 2 (never 1) is what Claude Code treats as a blocking denial; a
non-2 non-zero exit is only a non-blocking warning, which would silently let
a forbidden command through.
"""

from __future__ import annotations

import json
import re
import sys


# Each entry is a (label, pattern) pair, matched against the lower-cased full
# command string -- not just its first token, so pipes, subshells, env prefixes
# and ``&&`` chains are covered. Word boundaries keep innocuous substrings
# (e.g. "concurrent" containing "nc") from tripping the guard.
_FORBIDDEN = (
    ("curl", r"(?<![\w./-])curl(?![\w.-])"),
    ("wget", r"(?<![\w./-])wget(?![\w.-])"),
    ("Invoke-WebRequest", r"invoke-webrequest"),
    ("Invoke-RestMethod", r"invoke-restmethod"),
    ("requests.get", r"requests\s*\.\s*get"),
    ("httpx", r"(?<![\w.])httpx(?![\w])"),
    ("urllib.request", r"urllib\s*\.\s*request"),
    ("socket.connect", r"socket[\w.]*\s*\.\s*connect"),
    ("python -c", r"(?<![\w./-])(python|python3)(?![\w.-])\s+(-[a-z]*\s+)*-c(?![\w-])"),
    ("py -c", r"(?<![\w./-])py(?![\w.-])\s+(-[a-z]*\s+)*-c(?![\w-])"),
    ("node -e", r"(?<![\w./-])node(?![\w.-])\s+(-[a-z]*\s+)*-e(?![\w-])"),
    ("nc", r"(?<![\w./-])nc(?![\w.-])"),
    ("netcat", r"(?<![\w./-])netcat(?![\w.-])"),
    ("telnet", r"(?<![\w./-])telnet(?![\w.-])"),
    ("ssh", r"(?<![\w./-])ssh(?![\w.-])"),
    ("scp", r"(?<![\w./-])scp(?![\w.-])"),
    ("ftp", r"(?<![\w./-])s?ftp(?![\w.-])"),
    ("gh", r"(?<![\w./-])gh(?![\w.-])"),
    ("pip install", r"(?<![\w./-])(pip|pip3)(?![\w.-])(\s+[\w.=/-]+)*\s+install(?![\w-])"),
    ("npm install", r"(?<![\w./-])npm(?![\w.-])(\s+[\w.=/-]+)*\s+(install|i|add)(?![\w-])"),
)

FORBIDDEN_PATTERNS = tuple((label, re.compile(pattern)) for label, pattern in _FORBIDDEN)

DENY_EXIT_CODE = 2


def forbidden_reason(command: str) -> str | None:
    """Return the label of the first forbidden construct found, else None.

    The whole command string is searched, lower-cased. The Source Acquisition
    CLI is unaffected: the curl subprocess ``src/source_fetch.py`` starts is a
    *child* process, and a PreToolUse hook only ever sees the command Claude
    typed.
    """
    if not isinstance(command, str):
        return None
    text = command.lower()
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(text):
            return label
    return None


def command_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Fail closed: an unparseable hook payload must not become an allow.
        print("network_guard: unparseable PreToolUse payload", file=sys.stderr)
        return DENY_EXIT_CODE

    tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
    if tool_name not in (None, "Bash"):
        return 0

    reason = forbidden_reason(command_from_payload(payload))
    if reason is None:
        return 0

    print(
        "network_guard: blocked forbidden construct "
        f"'{reason}'. External fetching is only allowed through the repository "
        "Source Acquisition CLI (acquire-*). See CLAUDE.md.",
        file=sys.stderr,
    )
    return DENY_EXIT_CODE


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
