#!/usr/bin/env python3
"""PreToolUse hook: fail-closed network / escalation guard for Bash commands.

The hook reads the Claude Code PreToolUse payload from stdin, inspects the
*entire* Bash command string (not just its first token), and rejects any
invocation that could perform network access outside the repository's Source
Acquisition CLI, install packages, or move git history.

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


# Git's network subcommands are the one write path to GitHub that the
# Development sandbox can now reach, so they cannot be matched as the literal
# string "git push": the executable may be spelled as a path
# (``/usr/bin/git push``), and any number of global options may sit between it
# and the subcommand (``git -c credential.helper=... push``). Both spellings
# reach the network without passing through scripts/claude-safe-push, so the
# executable and the subcommand are matched independently of each other.
#
# The wrapper itself stays allowed: "scripts/claude-safe-push" contains no
# ``git`` token, and the ``git push`` it runs is a child process, which a
# PreToolUse hook never sees. Raw ``git push`` is never allowlisted.
_GIT_EXE = r"(?<![\w.-])(?:[\w./-]*/)?git(?![\w.-])"
_GIT_GLOBAL_OPTS = r"(?:\s+(?:-c\s*\S+|--[\w-]+(?:=\S+)?|-[a-z]+))*"

# Git also ships each subcommand as its own executable (``git-send-pack``),
# which is a network path that never spells the subcommand as an argument.
_GIT_NETWORK_SUBCOMMANDS = ("push", "fetch", "pull", "clone", "ls-remote", "send-pack")


def _git_network_pattern(subcommand: str) -> str:
    """Match ``git <subcommand>`` however the executable and options are written."""
    return (
        rf"(?:{_GIT_EXE}{_GIT_GLOBAL_OPTS}\s+{subcommand}(?![\w-])"
        rf"|(?<![\w.-])(?:[\w./-]*/)?git-{subcommand}(?![\w-]))"
    )


# Each entry is a (label, compiled pattern) pair. Patterns are matched against
# the lower-cased full command string. Word boundaries keep innocuous
# substrings (e.g. "concurrent" containing "nc") from tripping the guard,
# while still catching pipes, subshells, env prefixes and `&&` chains.
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
    *(
        (f"git {subcommand}", _git_network_pattern(subcommand))
        for subcommand in _GIT_NETWORK_SUBCOMMANDS
    ),
    ("pip install", r"(?<![\w./-])(pip|pip3)(?![\w.-])(\s+[\w.=/-]+)*\s+install(?![\w-])"),
    ("npm install", r"(?<![\w./-])npm(?![\w.-])(\s+[\w.=/-]+)*\s+(install|i|add)(?![\w-])"),
)

FORBIDDEN_PATTERNS = tuple(
    (label, re.compile(pattern)) for label, pattern in _FORBIDDEN
)

DENY_EXIT_CODE = 2


def forbidden_reason(command: str) -> str | None:
    """Return the label of the first forbidden construct found, else None."""
    if not isinstance(command, str):
        return None
    haystack = command.lower()
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(haystack):
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

    command = command_from_payload(payload)
    reason = forbidden_reason(command)
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
