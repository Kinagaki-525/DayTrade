"""DEVELOPMENT-ONLY: the testable core of ``scripts/claude-development``.

A convenience wrapper, and nothing more. Development Claude Code runs best with
the **git repository root** as its current working directory: started from
``daytrade-sbi/``, the Claude Sandbox grants write access to that subdirectory
only, so the repository-root ``.git`` is read-only and ``git add`` cannot even
create ``.git/index.lock``. This launcher resolves that root from its own
source location, confirms git agrees, ``chdir``s there and ``exec``s Claude.

DTWO-2026-026 removed everything else it used to do. It no longer looks for a
Production marker or an OS managed policy, no longer refuses a runtime profile,
no longer polices ``GIT_*`` environment variables, and no longer requires a
``claude/*`` branch: those were Layer C (Local Operational Governance) checks on
a personally owned machine, and none of them is what makes a DayTrade result
trustworthy.

Starting raw ``claude`` from the repository root is equally official. This
wrapper only spares the human from remembering where to stand.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent

DEVELOPMENT_RUNTIME_PROFILE = "development"
RUNTIME_PROFILE_ENV = "DAYTRADE_RUNTIME_PROFILE"

ERROR_CODES = ("CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED",)


class DevelopmentLauncherError(Exception):
    """A refusal to guess where the repository is. Claude is not started."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> DevelopmentLauncherError:
    assert code in ERROR_CODES, code
    return DevelopmentLauncherError(code, message)


def _git_toplevel(project_root: Path) -> str:
    """``git rev-parse --show-toplevel``, run from a fixed directory.

    ``cwd`` is the source tree, never the caller's directory, so the answer does
    not depend on where a human invoked the launcher from.
    """
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise _fail(
            "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED",
            f"git rev-parse --show-toplevel failed (exit {completed.returncode})",
        )
    return completed.stdout.strip()


def resolve_repository_root(
    *,
    project_root: str | Path = PROJECT_ROOT,
    expected_root: str | Path = REPOSITORY_ROOT,
    git_toplevel: str | None = None,
) -> Path:
    """The repository root, derived from this file and confirmed by git.

    The layout (``<root>/daytrade-sbi/src/claude_development_launcher.py``) fixes
    the answer without running anything; git is then asked independently and the
    two must agree. A disagreement -- a stray checkout, a nested repository, a
    copied source tree -- fails closed instead of picking one of them, because
    the wrong answer would put Claude's cwd outside the repository.
    """
    project = Path(project_root).resolve()
    expected = Path(expected_root).resolve()

    if not (expected / ".git").exists():
        raise _fail(
            "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED",
            f"{str(expected)!r} has no .git; this is not the repository root",
        )

    reported = git_toplevel if git_toplevel is not None else _git_toplevel(project)
    if not reported:
        raise _fail(
            "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED",
            "git reported no repository top level",
        )
    if Path(reported).resolve() != expected:
        raise _fail(
            "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED",
            f"git reports the repository root as {reported!r}, not "
            f"{str(expected)!r}; refusing to guess",
        )
    return expected


def preflight(
    *,
    project_root: str | Path = PROJECT_ROOT,
    expected_root: str | Path = REPOSITORY_ROOT,
    git_toplevel: str | None = None,
) -> dict[str, Any]:
    """Resolve the root. That is the whole preflight."""
    root = resolve_repository_root(
        project_root=project_root,
        expected_root=expected_root,
        git_toplevel=git_toplevel,
    )
    return {
        "repository_root": root,
        "project_root": Path(project_root).resolve(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-development",
        description=(
            "DEVELOPMENT-ONLY convenience launcher: start Claude Code from the "
            "git repository root. Not a security boundary."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the root and print it without starting Claude",
    )
    return parser


def main(argv: list[str] | None = None, **overrides: Any) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = preflight(**overrides)
    except DevelopmentLauncherError as error:
        sys.stderr.write(f"claude-development: {error}\n")
        return 2

    root = result["repository_root"]
    sys.stdout.write(f"repository_root: {root}\n")
    if args.dry_run:
        sys.stdout.write("development launcher preflight PASS\n")
        return 0

    env = dict(os.environ)
    env[RUNTIME_PROFILE_ENV] = DEVELOPMENT_RUNTIME_PROFILE
    sys.stdout.flush()
    # chdir immediately before exec: Claude inherits the repository root as its
    # cwd no matter which subdirectory the human called this launcher from.
    os.chdir(root)
    os.execvpe("claude", ["claude"], env)  # pragma: no cover - replaces process
    return 0  # pragma: no cover
