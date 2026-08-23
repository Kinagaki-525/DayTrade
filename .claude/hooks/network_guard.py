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
import os
import re
import shlex
import subprocess
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
        # Read-only forms only; see _branch_reason.
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
        # Recording work. Explicit-path staging only (_add_reason) and a
        # message-carrying commit of the index only (_commit_reason).
        "add",
        "commit",
        # Index-only unstage form only; see _restore_reason.
        "restore",
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

# ------------------------------------------------- git execution context ----
#
# FIX-DEV-GIT-011. The pathspec validator below judges an operand against the
# repository root (``CLAUDE_PROJECT_DIR``). Git, however, does not have to agree
# with that: a global option can move the repository, the work tree, or the
# directory the operand is resolved in, and then the path the guard inspected
# and the path git writes are two different things:
#
#     git --git-dir=<root>/.git --work-tree=/etc add -- passwd
#     git -C /other/repo add -- file
#
# No allowlist of pathspecs can survive that, so the execution context is fixed
# instead of being validated: Development raw git is ``git <subcommand>``, run
# from the repository root, with nothing between the executable and the
# subcommand. The official Development workflow needs no git global option at
# all, so all of them fail closed rather than a named few -- an option added by
# a future git release is refused on sight instead of being assumed harmless.
#
# These are the ones that move the context, named only to say so in the refusal.
_GIT_CONTEXT_OPTIONS = frozenset(
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

# The same context can be moved from outside git: by running it somewhere else
# (``cd daytrade-sbi && git add -- src/cli.py``), by an environment assignment
# that reroutes it (``GIT_WORK_TREE=/etc git add -- passwd``, with or without
# ``env``), or by burying it in a string a shell evaluates
# (``bash -c "git add -- CLAUDE.md"``). One Bash call is one direct git command
# from the repository root, so each of those shapes fails closed too.
_DIRECTORY_CHANGE_TOKENS = frozenset({"cd", "pushd", "popd", "chdir"})
_ENV_COMMAND_TOKEN = re.compile(r"^(?:[\w.\-/]*/)?env$")
_ASSIGNMENT_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")

# ``git commit`` option allowlist. Only an inline message survives; see
# _commit_reason. ``-F``/``--file`` was removed by FIX-DEV-GIT-010: it reads the
# commit message out of any readable file (``-F /etc/passwd``), and the reason it
# was once kept -- working around a commit-message false positive in this guard
# -- no longer exists. The option takes a value, either glued (``-mtext``,
# ``--message=text``) or as the following token.
_COMMIT_LONG_OPTIONS = frozenset({"--message"})
#: Short letters, all of which take a value (glued or as the next token).
_COMMIT_SHORT_OPTIONS = frozenset({"m"})

# ``git branch`` is inspection only: creating, deleting, renaming, copying or
# force-moving a branch is Safe Start's job, never Development Claude's. Both
# forms below only print.
_BRANCH_READ_ONLY_OPTIONS = frozenset({"--show-current", "--list"})

_GIT_EXE_TOKEN = re.compile(r"^(?:[\w.\-/]*/)?git$")
_GIT_DASHED_TOKEN = re.compile(r"^(?:[\w.\-/]*/)?git-([\w-]+)$")
_GIT_MENTION = re.compile(r"(?<![\w.-])(?:[\w./-]*/)?git(?![\w.-])")

_MAX_NESTING = 3

# ------------------------------------------------------ command positions --
#
# An earlier revision re-parsed *every* quoted argument that contained a space
# as though it were a command of its own. A shell does not work that way, and
# the guard consequently read
#
#     git commit -m "chore: development git metadata acceptance"
#
# as an invocation of a git subcommand named "metadata". A token is re-parsed in
# exactly one place: the string a shell runs for -c (``bash -c "git push ..."``).
# A token carrying a command substitution is not re-parsed either -- it is
# refused outright; see the shell expansion note below.
#
# Every other token is literal data handed to the program being run, and is
# judged as data: it can still be *searched* by the pattern list, but it is
# never treated as a git invocation.

_SHELL_TOKEN = re.compile(r"^(?:[\w.\-/]*/)?(?:sh|bash|zsh|ksh|dash|ash|busybox)$")
_SHELL_COMMAND_FLAG = re.compile(r"^-[a-zA-Z]*c$")
_COMMAND_SUBSTITUTION = re.compile(r"\$\(|`|<\(")

# ------------------------------------------------------- shell expansion ----
#
# This guard sees the command *string*; the shell runs it afterwards. Every
# expansion that happens in between -- command substitution, parameter
# expansion, globbing, tilde and brace expansion -- changes the argv git
# actually receives after the guard has finished looking at it, so what was
# inspected and what runs are not the same command:
#
#     git add -- "$(printf ':/')"     inspected: a literal; run: pathspec magic
#     git add -- "$PATH"              inspected: a literal; run: whatever $PATH is
#     git commit -m "$(cat /etc/…)"   inspected: a literal; run: file contents
#     git commit -m *                 inspected: a literal; run: file names
#
# An earlier revision tried to *read inside* command substitutions and allow the
# ones whose body looked harmless. That cannot work: the body need not contain a
# forbidden pattern to produce a forbidden argument, and the two examples above
# with printf and cat both pass such a check. Substitution is therefore refused
# outright wherever it appears, and every other expansion metacharacter is
# refused inside a raw git invocation, where the argv is what the whole
# allowlist is judging. Quoting does not rescue these forms: shlex has already
# removed the quotes by the time the tokens are judged, so a quoted '*' and a
# bare '*' are indistinguishable here and both fail closed.
#
# The cost is that a commit message cannot contain '$', '`', '*', '?', '[', ']',
# '{' or '}', and cannot begin with '~'. That is the canonical message form the
# Development workflow now uses; rephrasing a message is cheap, and a message is
# the one operand a caller is free to reword.
_SHELL_EXPANSION_CHARACTERS = "$`*?[]{}"

# Operator tokens end one command and begin the next, so a subcommand parse
# must never read across them.
_SEGMENT_BREAKS = frozenset(
    {"|", "||", "&", "&&", ";", ";;", "(", ")", "<", ">", ">>", "<<", "\n"}
)

# ``git commit`` message bodies. These are the arguments the shell hands to git
# as data; nothing in them is ever executed (unless the token also carries a
# command substitution, which is checked separately).
_COMMIT_MESSAGE_FLAGS = frozenset({"-m", "--message"})
_SHORT_MESSAGE_FLAG = re.compile(r"^-[a-zA-Z]*m(?P<value>.*)$")
_LONG_MESSAGE_FLAG = "--message="
_REDACTED_MESSAGE = "<commit-message>"


def _tokenize(command: str) -> list[str]:
    """Split like a shell, keeping ``|``, ``&&`` and friends as their own tokens.

    ``shlex.split`` would fold ``git status|git push`` into one word; with
    ``punctuation_chars`` the operators separate, so a second command chained
    without surrounding spaces is still seen.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # A '#' inside a command string is not a comment for our purposes; dropping
    # the remainder of the line would hide whatever follows it.
    lexer.commenters = ""
    return list(lexer)


def _config_reason(args: list[str]) -> str | None:
    """`git config` is allowed only where it provably cannot write."""
    if any(arg == "alias" or arg.startswith("alias.") for arg in args):
        return "git config touching alias.*"
    if not any(arg in _CONFIG_READ_ONLY_FLAGS for arg in args):
        return "git config without a read-only flag (a write can define an alias)"
    return None


# ------------------------------------------------------- pathspec operands --
#
# Git pathspecs are a language, not file names. Anything after ``--`` may carry
# "magic" (``:/`` = the whole repository from its root, ``:`` = the current
# prefix, ``:(top)``, ``:(exclude)…``, ``:(glob)…``), and ordinary path syntax
# can widen an operand just as far: ``src/..`` is the parent tree, ``src/.`` is
# the subtree, a trailing slash is the directory, and a bare directory name
# stages it recursively. The Development contract stages *files*, named one by
# one, so every operand is judged against that shape and anything else fails
# closed.
#
# Deleted tracked files must stay stageable, so "does not exist on disk" is not
# a refusal on its own: such an operand is checked against the index with a
# read-only ``git ls-files`` instead, and survives only when the index holds
# exactly one entry spelled exactly as written. A deleted tree matches several
# entries; a path that was never there matches none. Both are refused.
#
# ``git restore --staged`` needs one more step (FIX-DEV-GIT-013). Staging a
# deletion removes the entry from the index, so the very file whose deletion was
# just staged is absent from disk *and* from ``git ls-files`` -- and unstaging
# it, which the contract allows, would fail closed against the index check
# alone. So the restore operands fall back to the HEAD tree, under the same
# "exactly one entry, spelled exactly as written" rule. ``git add`` keeps the
# index-only judgement: HEAD is not what it stages against.

_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_GLOB_CHARACTERS = "*?[]"


def _base_directory() -> str:
    """The directory git pathspecs are resolved against (the repository root)."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _tracked_entries(base: str, path: str) -> list[str] | None:
    """What the index holds under ``path``; ``None`` when git could not answer."""
    try:
        result = subprocess.run(
            ["git", "-C", base, "ls-files", "-z", "--", path],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [entry for entry in result.stdout.decode("utf-8", "replace").split("\0") if entry]


def _head_entries(base: str, path: str) -> list[str] | None:
    """What HEAD holds under ``path``; ``None`` when git could not answer."""
    try:
        result = subprocess.run(
            ["git", "-C", base, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", path],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [entry for entry in result.stdout.decode("utf-8", "replace").split("\0") if entry]


def _path_reason(context: str, path: str, *, head_fallback: bool = False) -> str | None:
    """Refuse anything that is not one explicit, in-repository file path.

    ``head_fallback`` additionally accepts an operand that is absent from disk
    and from the index but names exactly one file in HEAD -- the staged deletion
    that ``git restore --staged`` exists to undo.
    """
    if not path:
        return f"{context} with an empty path"
    if path.startswith(":"):
        return (
            f"{context} of {path!r}, which is git pathspec magic (':/' is the "
            "whole repository); only plain file paths are allowed"
        )
    if path.startswith("-"):
        return f"{context} of {path!r}, which reads as an option rather than a file"
    if path.startswith("/") or path.startswith("\\") or _DRIVE_ABSOLUTE.match(path):
        return f"{context} of the absolute path {path!r}; paths are repository-relative"
    if "\\" in path:
        return (
            f"{context} of {path!r}; a backslash is not a repository path "
            "separator, so the path cannot be judged component by component"
        )
    if any(character in path for character in _GLOB_CHARACTERS):
        return (
            f"{context} of the glob {path!r}; the Development contract stages "
            "explicit files"
        )
    components = path.split("/")
    if any(component == "" for component in components):
        return (
            f"{context} of {path!r}; a leading, doubled or trailing '/' names a "
            "directory rather than one explicit file"
        )
    if any(component in (".", "..") for component in components):
        return (
            f"{context} of {path!r}; a '.' or '..' component names a tree rather "
            "than one explicit file"
        )

    base = _base_directory()
    target = os.path.join(base, path)
    root = os.path.realpath(base)
    resolved = os.path.realpath(target)
    if resolved != root and not resolved.startswith(root + os.sep):
        return f"{context} of {path!r}, which resolves outside the repository"
    if os.path.isdir(target):
        return (
            f"{context} of the directory {path!r}, which stages a whole tree; "
            "the Development contract stages explicit files"
        )
    if os.path.exists(target):
        return None

    # Not on disk: either a path that was never there, or a tracked file that was
    # deleted -- which must stay stageable. Only the index can tell the two apart
    # from a tracked *directory* that was deleted wholesale.
    entries = _tracked_entries(base, path)
    if entries is None:
        return (
            f"{context} of {path!r}, which is neither on disk nor checkable "
            "against the index"
        )
    # FIX-DEV-GIT-012. Exactly one tracked entry, spelled exactly as written, is
    # the deleted-file case. Zero entries is a path that is neither on disk nor
    # in the index -- a typo, or a file that was never created -- which is not
    # "one explicit file" and so is refused rather than passed to git.
    if len(entries) == 1 and entries[0] == path:
        return None
    if not head_fallback:
        return (
            f"{context} of {path!r}, which is not on disk and matches "
            f"{len(entries)} tracked files rather than exactly one"
        )

    # FIX-DEV-GIT-013. Neither on disk nor in the index. For an unstage that is
    # the staged-deletion case, so HEAD gets the last word -- again only when it
    # names exactly one file, spelled exactly as written. A wholesale-deleted
    # directory matches several; a path that never existed matches none.
    head = _head_entries(base, path)
    if head is None:
        return (
            f"{context} of {path!r}, which is neither on disk nor checkable "
            "against the index or HEAD"
        )
    if len(head) != 1 or head[0] != path:
        return (
            f"{context} of {path!r}, which is on neither disk nor index and "
            f"matches {len(head)} files in HEAD rather than exactly one"
        )
    return None


def _paths_reason(
    context: str, paths: list[str], *, head_fallback: bool = False
) -> str | None:
    for path in paths:
        reason = _path_reason(context, path, head_fallback=head_fallback)
        if reason:
            return reason
    return None


def _restore_reason(args: list[str]) -> str | None:
    """``git restore`` is allowed in exactly one shape: an index-only unstage.

        git restore --staged -- <path> [<path> ...]

    That form rewrites the index back to HEAD and touches nothing else. Every
    other shape of the command writes the working tree (``--worktree``/``-W``,
    or the default when ``--staged`` is absent), or restores from some source
    other than HEAD (``--source``), or is interactive (``--patch``). Anything
    that is not literally the shape above is refused, so an unrecognised option
    fails closed rather than being assumed harmless. The operands go through the
    same ``_path_reason`` validator as ``git add``: ``--staged -- :/`` would
    otherwise unstage the whole repository. The one difference is the HEAD
    fallback, which keeps a staged deletion unstageable-back (FIX-DEV-GIT-013).
    """
    arguments = _arguments_before_a_break(args)
    if "--staged" not in arguments:
        return (
            "git restore without --staged, which would overwrite working tree "
            "files"
        )
    if "--" not in arguments:
        return "git restore without a '--' path separator (an ambiguous form)"
    separator = arguments.index("--")
    options = arguments[:separator]
    paths = arguments[separator + 1 :]
    unexpected = [option for option in options if option != "--staged"]
    if unexpected:
        return (
            f"git restore with {unexpected[0]!r}; only "
            "'git restore --staged -- <path>' is allowed"
        )
    if not paths:
        return "git restore with no path after '--'"
    return _paths_reason("git restore --staged", paths, head_fallback=True)


def _arguments_before_a_break(args: list[str]) -> list[str]:
    """The arguments belonging to this command, stopping at the next one."""
    for index, token in enumerate(args):
        if token in _SEGMENT_BREAKS:
            return args[:index]
    return list(args)


def _commit_value_reason(option: str, value: str | None) -> str | None:
    if value is None:
        return f"git commit with {option!r} but no value"
    return None


def _commit_reason(args: list[str]) -> str | None:
    """``git commit`` is allowed only as "record what is already staged".

    The Development contract is: stage explicit paths with ``git add``, then
    turn the index into a commit with a message given on the command line.
    ``git commit`` must therefore not be able to pick up anything the index does
    not already hold (``-a``/``--all``, ``--only``/``-o``, ``--include``/``-i``,
    a pathspec, ``--``), must not rewrite history that already exists
    (``--amend``, ``--fixup``, ``--squash``), and must not block: a bare
    ``git commit`` opens an editor, which a non-interactive session cannot
    answer.

    Options are judged by allowlist, so an unrecognised one fails closed instead
    of being assumed harmless. Only an inline message survives: ``-m`` and
    ``--message``. ``-F``/``--file`` is refused outright -- it would read the
    message out of any readable file, and ``-F -`` would wait on stdin. The
    message operand is data: the caller-supplied body is never parsed as a
    command here (see ``_commit_message_indices``), though a body carrying a
    shell expansion is refused before it ever reaches this function (see
    ``_shell_expansion_reason``).
    """
    arguments = _arguments_before_a_break(args)

    messages = 0
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return (
                "git commit with a '--' pathspec separator, which commits "
                "paths rather than the staged index"
            )
        if token.startswith("--"):
            name, separator, glued = token.partition("=")
            if name not in _COMMIT_LONG_OPTIONS:
                return (
                    f"git commit with {name!r}; only an inline message "
                    "(--message=<message>) is allowed on top of the staged index"
                )
            if separator:
                value: str | None = glued
                index += 1
            else:
                value = arguments[index + 1] if index + 1 < len(arguments) else None
                index += 2
            reason = _commit_value_reason(name, value)
            if reason:
                return reason
            messages += 1
            continue
        if token.startswith("-") and len(token) > 1:
            # A short cluster. Every allowed letter takes a value, so only the
            # first letter can be an option: the rest is its glued operand.
            letter = token[1]
            if letter not in _COMMIT_SHORT_OPTIONS:
                return (
                    f"git commit with '-{letter}'; only an inline message "
                    "(-m <message>) is allowed on top of the staged index"
                )
            glued = token[2:]
            if glued:
                value = glued
                index += 1
            else:
                value = arguments[index + 1] if index + 1 < len(arguments) else None
                index += 2
            reason = _commit_value_reason(letter, value)
            if reason:
                return reason
            messages += 1
            continue
        return (
            f"git commit with the pathspec {token!r}; commit records the index, "
            "so paths are staged with 'git add' instead"
        )
    if messages == 0:
        return (
            "git commit with no message option; a bare 'git commit' opens an "
            "editor, so the message is given inline with -m/--message"
        )
    return None


def _add_reason(args: list[str]) -> str | None:
    """``git add`` is allowed in exactly one shape: explicit files after ``--``.

        git add -- <file> [<file> ...]

    Everything that widens what gets staged beyond the files written in the
    command is refused: ``.``, ``-A``/``--all``, ``-u``/``--update``, a glob,
    a directory, and the interactive forms (``-p``, ``-i``). The ``--``
    separator is required rather than optional, so ``git add <path>`` -- where a
    leading dash or a future option name would change the meaning -- fails
    closed too. Each operand is then judged by ``_path_reason``.
    """
    arguments = _arguments_before_a_break(args)
    if "--" not in arguments:
        return (
            "git add without a '--' path separator; only "
            "'git add -- <file> [<file> ...]' is allowed"
        )
    separator = arguments.index("--")
    options = arguments[:separator]
    paths = arguments[separator + 1 :]
    if options:
        return (
            f"git add with {options[0]!r}; only "
            "'git add -- <file> [<file> ...]' is allowed"
        )
    if not paths:
        return "git add with no path after '--'"
    return _paths_reason("git add", paths)


def _branch_reason(args: list[str]) -> str | None:
    """``git branch`` is allowed only where it inspects.

    Creating, deleting, renaming, copying or force-moving a branch is Safe
    Start's job. Only the two printing forms are allowed, with no operand: a
    positional argument to ``git branch`` creates a branch.
    """
    arguments = _arguments_before_a_break(args)
    if not arguments:
        return (
            "bare 'git branch'; use 'git branch --show-current' or "
            "'git branch --list'"
        )
    for token in arguments:
        if token not in _BRANCH_READ_ONLY_OPTIONS:
            return (
                f"git branch with {token!r}; only the read-only forms "
                "'--show-current' and '--list' are allowed (branches are "
                "created by scripts/claude-safe-start)"
            )
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
    if name == "restore":
        return _restore_reason(args)
    if name == "commit":
        return _commit_reason(args)
    if name == "add":
        return _add_reason(args)
    if name == "branch":
        return _branch_reason(args)
    return None


def _invocation_reason(rest: list[str]) -> str | None:
    """The token after the git executable must be the subcommand itself.

    Canonical Development raw git is ``git <subcommand> ...``. A global option
    in between can move git's repository, work tree or directory
    (``--git-dir``, ``--work-tree``, ``-C``, ``--namespace``), inject
    configuration that defines an alias (``-c``, ``--config-env``), or simply be
    an option this guard has never heard of. None of them is needed by the
    Development workflow, so the position is fixed rather than filtered.
    """
    if not rest or rest[0] in _SEGMENT_BREAKS:
        return "a git invocation with no subcommand"
    token = rest[0]
    if token.startswith("-"):
        name = token.partition("=")[0]
        if name in _GIT_CONTEXT_OPTIONS:
            return (
                f"the git global option {token!r}, which moves the repository, "
                "work tree or directory git operates on, so the path this guard "
                "validated is not the path git would write"
            )
        if name in ("-c", "--config-env") or (
            token.startswith("-c") and len(token) > 2
        ):
            return f"the git global option {token!r} (inline configuration, which can define an alias)"
        return (
            f"the git global option {token!r}; Development raw git is "
            "'git <subcommand>' run from the repository root, with nothing "
            "between the executable and the subcommand"
        )
    return _subcommand_reason(token, rest[1:])


def _subcommand_index(tokens: list[str], start: int) -> int | None:
    """Index of the subcommand token, which directly follows the executable."""
    if start >= len(tokens):
        return None
    token = tokens[start]
    if token in _SEGMENT_BREAKS or token.startswith("-"):
        return None
    return start


def _commit_message_indices(tokens: list[str]) -> frozenset[int]:
    """Indices holding a literal ``git commit`` message body.

    Every message that reaches this function is literal: a body carrying a
    command substitution or any other shell expansion is refused in
    ``_scan_tokens`` before the message positions are consulted.
    """
    literal: set[int] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        dashed = _GIT_DASHED_TOKEN.match(token)
        if dashed and dashed.group(1).lower() == "commit":
            cursor = index + 1
        elif not dashed and _GIT_EXE_TOKEN.match(token):
            subcommand_at = _subcommand_index(tokens, index + 1)
            if subcommand_at is None or tokens[subcommand_at].lower() != "commit":
                index += 1
                continue
            cursor = subcommand_at + 1
        else:
            index += 1
            continue

        while cursor < len(tokens) and tokens[cursor] not in _SEGMENT_BREAKS:
            argument = tokens[cursor]
            if argument in _COMMIT_MESSAGE_FLAGS:
                # The body is the next token.
                if cursor + 1 < len(tokens):
                    literal.add(cursor + 1)
                cursor += 2
                continue
            if argument.startswith(_LONG_MESSAGE_FLAG):
                literal.add(cursor)
                cursor += 1
                continue
            short = _SHORT_MESSAGE_FLAG.match(argument)
            if short:
                if short.group("value"):
                    # The body is glued to the flag: `-mmessage`.
                    literal.add(cursor)
                    cursor += 1
                    continue
                if cursor + 1 < len(tokens):
                    literal.add(cursor + 1)
                cursor += 2
                continue
            cursor += 1
        index = cursor
    return frozenset(literal)


def _shell_command_index(tokens: list[str], index: int) -> int | None:
    """Index of the command string a shell at ``index`` would evaluate for -c."""
    if not _SHELL_TOKEN.match(tokens[index]):
        return None
    cursor = index + 1
    while cursor < len(tokens):
        token = tokens[cursor]
        if token in _SEGMENT_BREAKS:
            return None
        if _SHELL_COMMAND_FLAG.match(token):
            return cursor + 1 if cursor + 1 < len(tokens) else None
        if token.startswith("-"):
            cursor += 1
            continue
        # The first operand of a shell without -c is a script path, not a
        # command string.
        return None
    return None


def _shell_expansion_reason(tokens: list[str]) -> str | None:
    """Refuse a git invocation whose argv the shell would still rewrite."""
    for token in tokens:
        if token in _SEGMENT_BREAKS:
            continue
        if token.startswith("~"):
            return (
                f"a git argument beginning with '~' ({token!r}); tilde expansion "
                "would change the path git receives after this guard read it"
            )
        for character in _SHELL_EXPANSION_CHARACTERS:
            if character in token:
                return (
                    f"a git argument carrying the shell metacharacter "
                    f"{character!r} ({token!r}); the shell would expand it after "
                    "this guard read it, so the inspected argument and the one "
                    "git receives are not the same"
                )
    return None


def _segment_starts(tokens: list[str]) -> frozenset[int]:
    """Indices where a new command begins (the start, and after each operator)."""
    starts = {0}
    for index, token in enumerate(tokens):
        if token in _SEGMENT_BREAKS:
            starts.add(index + 1)
    return frozenset(starts)


def _directory_change_reason(
    tokens: list[str], starts: frozenset[int], literal: frozenset[int]
) -> str | None:
    """Refuse a command that moves the shell before git runs.

    ``cd daytrade-sbi && git add -- src/cli.py`` stages a *different* file from
    the one this guard resolved against the repository root. The check looks at
    command positions only, so the word "cd" inside a commit message is data.
    """
    for index in sorted(starts):
        if index >= len(tokens) or index in literal:
            continue
        if tokens[index] in _DIRECTORY_CHANGE_TOKENS:
            return (
                f"a {tokens[index]!r} before a git command; raw git runs from "
                "the repository root, so a path this guard resolved there is "
                "not the path git would receive after the directory changed"
            )
    return None


def _git_position_reason(tokens: list[str], index: int, starts: frozenset[int]) -> str | None:
    """Refuse a git invocation that anything is prefixed to."""
    if index in starts:
        return None
    previous = tokens[index - 1]
    if _ASSIGNMENT_TOKEN.match(previous):
        return (
            f"the environment assignment {previous!r} in front of git; "
            "GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE and friends reroute the "
            "repository git writes, so raw git is run with no environment prefix"
        )
    if _ENV_COMMAND_TOKEN.match(previous):
        return (
            f"{previous!r} in front of git, which can set GIT_DIR / "
            "GIT_WORK_TREE for the invocation and reroute the repository"
        )
    return (
        f"{previous!r} in front of git; one Bash call is one direct git "
        "command run from the repository root"
    )


def _scan_tokens(tokens: list[str], depth: int = 0) -> str | None:
    """Judge every git invocation in a token list, including chained ones."""
    literal = _commit_message_indices(tokens)
    starts = _segment_starts(tokens)
    directory_change = _directory_change_reason(tokens, starts, literal)
    for index, token in enumerate(tokens):
        # A command substitution runs wherever it appears -- a commit message
        # included -- and its output becomes the argument. Its body is not read
        # and judged: a substitution that contains nothing forbidden can still
        # produce a forbidden argument, so the form itself is refused.
        if _COMMAND_SUBSTITUTION.search(token):
            return (
                f"a command substitution ({token!r}); the shell runs it and "
                "substitutes its output, so what this guard inspected is not "
                "what would run"
            )
        if index in literal:
            continue
        dashed = _GIT_DASHED_TOKEN.match(token)
        if dashed:
            # Git ships each subcommand as its own executable (git-send-pack).
            # That spelling is not the canonical ``git <subcommand>`` form, and
            # the Development workflow never needs it.
            return (
                f"the split git executable {token!r}; Development raw git is "
                "'git <subcommand>' run directly from the repository root"
            )
        if _GIT_EXE_TOKEN.match(token):
            if depth > 0:
                return (
                    f"a git invocation inside a shell -c string ({token!r}); "
                    "raw git is run as its own Bash call from the repository "
                    "root, not through a nested shell"
                )
            reason = (
                _git_position_reason(tokens, index, starts)
                or directory_change
            )
            if reason:
                return reason
            arguments = _arguments_before_a_break(list(tokens[index + 1 :]))
            reason = _shell_expansion_reason(arguments) or _invocation_reason(
                list(tokens[index + 1 :])
            )
            if reason:
                return reason
            continue
        # `bash -c "git ship ..."`: the -c operand really is a command.
        nested = _shell_command_index(tokens, index)
        if nested is not None:
            reason = _analyse(tokens[nested], depth + 1)
            if reason:
                return reason
    return None


def _scan_text(tokens: list[str]) -> str:
    """The token stream as one string, with literal commit messages redacted.

    The pattern list below searches text, so a commit message mentioning
    "git push" would otherwise read as a push. The message is data; redacting
    it removes the false positive without weakening any pattern, because every
    other token is kept exactly as the shell would pass it.
    """
    literal = _commit_message_indices(tokens)
    return " ".join(
        _REDACTED_MESSAGE if index in literal else token
        for index, token in enumerate(tokens)
    )


def _pattern_reason(text: str) -> str | None:
    haystack = text.lower()
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(haystack):
            return label
    return None


def _analyse(command: str, depth: int = 0) -> str | None:
    if depth > _MAX_NESTING:
        return "a command nested deeper than this guard will parse"
    try:
        tokens = _tokenize(command)
    except ValueError:
        # An unparsable command is only refused outright when git is actually
        # in it; everything else still faces the pattern list.
        if _GIT_MENTION.search(command):
            return "an unparsable git command"
        return _pattern_reason(command)
    # Case-sensitive on purpose: `git -c` and `git -C` mean different things.
    reason = _scan_tokens(tokens, depth)
    if reason:
        return reason
    return _pattern_reason(_scan_text(tokens))


def raw_git_reason(command: str) -> str | None:
    """Refuse any raw git invocation outside the local-only allowlist."""
    try:
        tokens = _tokenize(command)
    except ValueError:
        if _GIT_MENTION.search(command):
            return "an unparsable git command"
        return None
    return _scan_tokens(tokens)


def forbidden_reason(command: str) -> str | None:
    """Return the label of the first forbidden construct found, else None."""
    if not isinstance(command, str):
        return None
    return _analyse(command)


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

    # Pathspec operands are judged relative to the repository root. Claude Code
    # normally exports it; the payload's cwd is the fallback.
    if isinstance(payload, dict) and not os.environ.get("CLAUDE_PROJECT_DIR"):
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            os.environ["CLAUDE_PROJECT_DIR"] = cwd

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
