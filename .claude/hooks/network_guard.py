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
import shlex
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


# --------------------------------------------------------- raw git allowlist --
#
# Blacklisting Git's network subcommands cannot hold on its own, because Git
# lets any subcommand be renamed:
#
#     git -c alias.ship=push ship origin claude/example
#     git config alias.ship push  &&  git ship origin claude/example
#
# The command string says "ship", so no list of *known* network subcommands can
# recognise it, and no amount of adding names to that list ever will. Raw Git is
# therefore judged the other way round: only the local-only subcommands needed
# for development work are allowed, and everything else -- unknown subcommands,
# aliases, and the network subcommands alike -- is refused. The blacklist above
# is kept as a second layer, not as the decision.
#
# scripts/claude-safe-push stays allowed: it is not a git token, and the push it
# runs is a child process, which a PreToolUse hook never sees. Nothing raw is
# allowlisted in its place.
#
# This is a Development workflow control. The Production Security Boundary
# remains the OS Managed Policy and the OS Managed Runtime Guard.

_LOCAL_GIT_SUBCOMMANDS = frozenset(
    {
        # Inspection.
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "branch",
        "ls-files",
        "diff-index",
        "diff-tree",
        "merge-base",
        "cat-file",
        "check-ref-format",
        # Verifying .git/info/exclude, which is what keeps the tree clean enough
        # for claude-safe-push to run at all.
        "check-ignore",
        # Recording work.
        "add",
        "commit",
        # Read-only forms only; see _config_reason.
        "config",
    }
)

# `git config` can create an alias, so it is allowed only in the forms that
# cannot write anything.
_CONFIG_READ_ONLY_FLAGS = frozenset(
    {
        "--get",
        "--get-all",
        "--get-regexp",
        "--get-urlmatch",
        "--list",
        "-l",
        "--show-origin",
        "--show-scope",
    }
)

# Global options that consume the next token, which would otherwise be mistaken
# for the subcommand.
_GIT_VALUE_OPTIONS = frozenset(
    {
        "-C",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--exec-path",
        "--super-prefix",
        "--attr-source",
    }
)

_GIT_EXE_TOKEN = re.compile(r"^(?:[\w.\-/]*/)?git$")
_GIT_DASHED_TOKEN = re.compile(r"^(?:[\w.\-/]*/)?git-([\w-]+)$")
_GIT_MENTION = re.compile(r"(?<![\w.-])(?:[\w./-]*/)?git(?![\w.-])")

_MAX_NESTING = 3


def _config_reason(args: list[str]) -> str | None:
    """`git config` is allowed only where it provably cannot write."""
    if any(arg == "alias" or arg.startswith("alias.") for arg in args):
        return "git config touching alias.*"
    if not any(arg in _CONFIG_READ_ONLY_FLAGS for arg in args):
        return "git config without a read-only flag (a write can define an alias)"
    return None


def _subcommand_reason(subcommand: str, args: list[str]) -> str | None:
    name = subcommand.lower()
    if name not in _LOCAL_GIT_SUBCOMMANDS:
        return (
            f"raw git {name!r}, which is not one of the local-only git "
            "subcommands Development Claude may run"
        )
    if name == "config":
        return _config_reason(args)
    return None


def _invocation_reason(rest: list[str]) -> str | None:
    """Skip Git's global options, then judge the subcommand they precede."""
    index = 0
    while index < len(rest):
        token = rest[index]
        # -c injects configuration for one command, which is enough to define an
        # alias (and to reach credential.helper, core.pager and friends). It is
        # never needed for local development work, so it is refused outright.
        # -C (upper case) is a directory change and stays allowed, which is why
        # this parse is case-sensitive.
        if token == "-c" or (token.startswith("-c") and len(token) > 2):
            return "git -c (inline configuration, which can define an alias)"
        if token == "--config-env" or token.startswith("--config-env="):
            return "git --config-env (inline configuration, which can define an alias)"
        if token in _GIT_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return _subcommand_reason(token, rest[index + 1 :])
    return "a git invocation with no recognisable subcommand"


def _scan_tokens(tokens: list[str], depth: int = 0) -> str | None:
    """Judge every git invocation in a token list, including chained ones."""
    for index, token in enumerate(tokens):
        dashed = _GIT_DASHED_TOKEN.match(token)
        if dashed:
            # Git ships each subcommand as its own executable (git-send-pack).
            reason = _subcommand_reason(dashed.group(1), list(tokens[index + 1 :]))
            if reason:
                return reason
            continue
        if _GIT_EXE_TOKEN.match(token):
            reason = _invocation_reason(list(tokens[index + 1 :]))
            if reason:
                return reason
            continue
        # A quoted argument can carry a whole command of its own, as in
        # `bash -c "git ship ..."`.
        if depth < _MAX_NESTING and any(char.isspace() for char in token):
            try:
                nested = shlex.split(token)
            except ValueError:
                if _GIT_MENTION.search(token):
                    return "an unparsable quoted git command"
                continue
            reason = _scan_tokens(nested, depth + 1)
            if reason:
                return reason
    return None


def raw_git_reason(command: str) -> str | None:
    """Refuse any raw git invocation outside the local-only allowlist."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        # An unparsable command is only refused when git is actually in it;
        # everything else stays with the pattern list above.
        if _GIT_MENTION.search(command):
            return "an unparsable git command"
        return None
    return _scan_tokens(tokens)


def forbidden_reason(command: str) -> str | None:
    """Return the label of the first forbidden construct found, else None."""
    if not isinstance(command, str):
        return None
    haystack = command.lower()
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(haystack):
            return label
    # Case-sensitive on purpose: `git -c` and `git -C` mean different things.
    return raw_git_reason(command)


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
