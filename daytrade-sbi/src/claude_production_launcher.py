"""DTWO-2026-026: the unit-testable core of ``scripts/claude-production``.

This is a **Production Context Launcher**, not a security gate. It answers one
question -- "is this checkout in a state a nightly may be run from, and what
are the paths and the exact commit that run will be attributed to?" -- and then
``exec``s Claude Code with that context in the environment.

What it deliberately does *not* do (DTWO-2026-026 section 8.1): it reads no
``/etc`` marker, no OS managed policy, no runtime guard, no seccomp
attestation; it does not pin the Claude Code version, look for sandbox
binaries, inspect MCP or Remote Control state, or touch the network. Those
belonged to Layer C (Local Operational Governance) and are not what makes a
DayTrade business result trustworthy -- Raw Evidence, SHA256, the Source
Ledger, the Trust Chain and the Risk Engine are, and every one of them still
fails closed inside the pipeline itself.

A launcher failure is therefore an *operational start failure*. It is never
converted into ``NO_TRADE``, ``DATA_UNAVAILABLE`` or ``REJECTED``: no business
decision has been reached, because no business stage has run.

The launcher writes nothing. No attestation artifact, no policy candidate, no
file anywhere under ``runs/``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from src.production_context import (
    ProductionContextError,
    resolve_run_dir,
    validate_target_date,
)

DAYTRADE_ROOT = Path(__file__).resolve().parents[1]

#: The only branch a production nightly may run from. A nightly is attributed
#: to an exact commit, and a feature branch is not the reviewed line of
#: history that commit is supposed to come from.
PRODUCTION_BRANCH = "main"

#: In-progress git operations, by the marker git itself leaves in the git
#: directory. A half-finished merge or rebase means the working tree is a
#: transient state nobody reviewed, whatever ``git status`` says about it.
GIT_OPERATION_MARKERS = (
    ("merge", "MERGE_HEAD"),
    ("rebase", "rebase-merge"),
    ("rebase", "rebase-apply"),
    ("cherry-pick", "CHERRY_PICK_HEAD"),
    ("revert", "REVERT_HEAD"),
    ("bisect", "BISECT_LOG"),
)

ERROR_CODES = (
    "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED",
    "CLAUDE_PRODUCTION_BRANCH_NOT_MAIN",
    "CLAUDE_PRODUCTION_GIT_OPERATION_IN_PROGRESS",
    "CLAUDE_PRODUCTION_WORKING_TREE_DIRTY",
    "CLAUDE_PRODUCTION_HEAD_UNRESOLVED",
    "CLAUDE_PRODUCTION_EXEC_FAILED",
)

#: A resolved commit is exactly forty lowercase hex characters.
_HEX40 = frozenset("0123456789abcdef")


class ProductionLauncherError(ProductionContextError):
    """A fail-closed refusal. Claude is never started."""


def _fail(code: str, message: str) -> ProductionLauncherError:
    assert code in ERROR_CODES, code
    return ProductionLauncherError(code, message)


def default_run_command(argv: list[str], cwd: Path) -> str:
    """Run a git command and return stdout, or raise with its stderr."""
    completed = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise _fail(
            "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED",
            f"command failed: {' '.join(argv)}: {completed.stderr.strip()}",
        )
    return completed.stdout


def resolve_repository_root(
    *,
    daytrade_root: Path,
    expected_root: Path,
    run_command: Callable[[list[str], Path], str],
) -> Path:
    """The repository root, derived from the source tree and confirmed by git.

    The layout fixes the answer without running anything; git is then asked
    independently and the two must agree. A disagreement -- a stray checkout, a
    nested repository, a copied source tree -- fails closed rather than picking
    one of them.
    """
    if not daytrade_root.is_dir():
        raise _fail(
            "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED",
            f"{str(daytrade_root)!r} is not a directory",
        )
    reported = run_command(
        ["git", "rev-parse", "--show-toplevel"], daytrade_root
    ).strip()
    if not reported:
        raise _fail(
            "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED",
            "git reported no repository top level",
        )
    resolved = Path(reported).resolve()
    if resolved != expected_root.resolve():
        raise _fail(
            "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED",
            f"git reports the repository root as {reported!r}, not "
            f"{str(expected_root)!r}; refusing to guess",
        )
    return resolved


def verify_on_production_branch(
    *, repository_root: Path, run_command: Callable[[list[str], Path], str]
) -> str:
    """Only ``main``; a detached HEAD is refused with everything else.

    ``symbolic-ref --quiet HEAD`` answers only for a real branch, so a detached
    HEAD is a non-zero exit rather than a name to compare.
    """
    try:
        reference = run_command(
            ["git", "symbolic-ref", "--quiet", "HEAD"], repository_root
        ).strip()
    except ProductionContextError:
        raise _fail(
            "CLAUDE_PRODUCTION_BRANCH_NOT_MAIN",
            "HEAD is detached or the current branch could not be resolved; "
            f"a production nightly runs from {PRODUCTION_BRANCH!r}",
        ) from None
    if not reference.startswith("refs/heads/"):
        raise _fail(
            "CLAUDE_PRODUCTION_BRANCH_NOT_MAIN",
            f"HEAD points at {reference!r}, which is not a local branch",
        )
    branch = reference[len("refs/heads/") :]
    if branch != PRODUCTION_BRANCH:
        raise _fail(
            "CLAUDE_PRODUCTION_BRANCH_NOT_MAIN",
            f"current branch is {branch!r}; a production nightly runs from "
            f"{PRODUCTION_BRANCH!r}",
        )
    return branch


def verify_no_git_operation(
    *, repository_root: Path, run_command: Callable[[list[str], Path], str]
) -> None:
    """Refuse while a merge, rebase, cherry-pick, revert or bisect is open."""
    git_dir = Path(
        run_command(["git", "rev-parse", "--absolute-git-dir"], repository_root).strip()
    )
    in_progress = sorted(
        {name for name, marker in GIT_OPERATION_MARKERS if (git_dir / marker).exists()}
    )
    if in_progress:
        raise _fail(
            "CLAUDE_PRODUCTION_GIT_OPERATION_IN_PROGRESS",
            f"a git operation is in progress: {', '.join(in_progress)}; "
            "finish or abort it before starting a production session",
        )


def verify_tracked_tree_clean(porcelain_output: str) -> None:
    """Every *tracked* change refuses the launch; untracked files never do.

    The nightly is attributed to ``HEAD``, so an edited tracked file means the
    code that runs is not the code that commit names. Untracked files are a
    different thing entirely -- a scratch file in the checkout says nothing
    about which committed code is about to execute -- and never block a start.
    """
    dirty = set()
    for line in porcelain_output.splitlines():
        if not line.strip():
            continue
        entry = line[3:].strip().strip('"')
        # A rename is reported as "old -> new"; the file that is actually
        # different from HEAD is the destination.
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        dirty.add(entry)
    if dirty:
        raise _fail(
            "CLAUDE_PRODUCTION_WORKING_TREE_DIRTY",
            f"tracked files have uncommitted changes: {sorted(dirty)}",
        )


def resolve_head_sha(
    *, repository_root: Path, run_command: Callable[[list[str], Path], str]
) -> str:
    """The exact commit this nightly is attributed to."""
    try:
        head = run_command(["git", "rev-parse", "HEAD"], repository_root).strip()
    except ProductionContextError:
        raise _fail(
            "CLAUDE_PRODUCTION_HEAD_UNRESOLVED", "git rev-parse HEAD failed"
        ) from None
    if len(head) != 40 or not set(head) <= _HEX40:
        raise _fail(
            "CLAUDE_PRODUCTION_HEAD_UNRESOLVED",
            f"git rev-parse HEAD did not return a 40-character sha: {head!r}",
        )
    return head


def preflight(
    *,
    target_date: str,
    daytrade_root: str | Path = DAYTRADE_ROOT,
    project_root: str | Path | None = None,
    run_command: Callable[[list[str], Path], str] = default_run_command,
) -> dict[str, Any]:
    """Every local operational precondition, in order.

    Raises :class:`ProductionContextError` on the first failure -- the shared
    base class, because the target-date validator is shared with the Archive
    and raises it directly. Nothing is written to disk in either outcome.
    """
    # 1. untrusted human input, before any filesystem or git work at all.
    target_date = validate_target_date(target_date)

    # 2-3. the two roots, derived from this file and confirmed by git.
    daytrade_root = Path(daytrade_root).resolve()
    expected_root = (
        Path(project_root).resolve()
        if project_root is not None
        else daytrade_root.parent
    )
    repository_root = resolve_repository_root(
        daytrade_root=daytrade_root,
        expected_root=expected_root,
        run_command=run_command,
    )

    # 4-5. branch, which also rejects a detached HEAD.
    branch = verify_on_production_branch(
        repository_root=repository_root, run_command=run_command
    )

    # 6. no half-finished git operation.
    verify_no_git_operation(
        repository_root=repository_root, run_command=run_command
    )

    # 7. tracked cleanliness only -- untracked files are not a refusal.
    verify_tracked_tree_clean(
        run_command(
            ["git", "status", "--porcelain", "--untracked-files=no"], repository_root
        )
    )

    # 8. the commit this run is attributed to.
    head_sha = resolve_head_sha(
        repository_root=repository_root, run_command=run_command
    )

    # 9. containment: runs/<target-date> under this checkout's runs root.
    run_dir = resolve_run_dir(daytrade_root, target_date)

    return {
        "target_date": target_date,
        "run_dir": run_dir,
        "project_root": repository_root,
        "daytrade_root": daytrade_root,
        "current_branch": branch,
        "git_head_sha": head_sha,
    }


def build_environment(
    result: Mapping[str, Any], environ: Mapping[str, str]
) -> dict[str, str]:
    """The production context every later stage reads."""
    env = dict(environ)
    env.update(
        {
            "DAYTRADE_RUNTIME_PROFILE": "production",
            "DAYTRADE_PROJECT_ROOT": str(result["project_root"]),
            "DAYTRADE_ROOT": str(result["daytrade_root"]),
            "DAYTRADE_RUN_DIR": str(result["run_dir"]),
            "DAYTRADE_TARGET_DATE": str(result["target_date"]),
            "DAYTRADE_GIT_HEAD_SHA": str(result["git_head_sha"]),
        }
    )
    return env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-production",
        description=(
            "HUMAN-ONLY DayTrade production Claude Code launcher. Resolves the "
            "run context and starts Claude; not a security gate."
        ),
    )
    parser.add_argument("--target-date", required=True)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="run the local checks and exit without starting Claude",
    )
    return parser


def main(argv: list[str] | None = None, **overrides: Any) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = preflight(target_date=args.target_date, **overrides)
    except ProductionContextError as error:
        sys.stderr.write(f"{error}\n")
        return 2

    sys.stdout.write(f"target_date: {result['target_date']}\n")
    sys.stdout.write(f"daytrade_root: {result['daytrade_root']}\n")
    sys.stdout.write(f"run_dir: {result['run_dir']}\n")
    sys.stdout.write(f"branch: {result['current_branch']}\n")
    sys.stdout.write(f"git_head_sha: {result['git_head_sha']}\n")

    if args.preflight_only:
        sys.stdout.write("production context preflight PASS\n")
        return 0

    env = build_environment(result, os.environ)
    sys.stdout.flush()
    os.chdir(result["daytrade_root"])
    try:
        os.execvpe("claude", ["claude"], env)
    except OSError as error:
        # Reached only if the exec itself fails -- claude not on PATH, not
        # executable. That is an operational start failure like any other, so
        # it exits with a code and a reason rather than a traceback.
        sys.stderr.write(f"CLAUDE_PRODUCTION_EXEC_FAILED: {error}\n")
        return 2
    return 0  # pragma: no cover - execvpe replaced the process
