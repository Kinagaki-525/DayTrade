"""DEVELOPMENT-ONLY: the testable core of ``scripts/claude-development``.

Development Claude Code must run with the **git repository root** as its
current working directory. Started from ``daytrade-sbi/``, the Claude Sandbox
grants write access to that subdirectory only, so the repository-root ``.git``
is read-only and ``git add`` cannot even create ``.git/index.lock``. Started
from the repository root, ``git add`` and ``git commit`` work with the sandbox
fully enabled -- no sandbox flag, no permission widening, no chmod on ``.git``.

This module therefore does four things: prove we are on a Development runtime,
prove no inherited ``GIT_*`` variable reroutes what "the repository" means,
resolve the repository root from the source tree itself (never from ``$PWD``),
and prove the checkout is on a ``claude/*`` branch -- so a session started here
can only ever ``git add`` / ``git commit`` onto a feature branch, never onto
local ``main``. The launcher ``chdir``s to the resolved root immediately before
``exec claude``.

What this module is *not*:

* It is not a security boundary. It lives in the repository and Development
  Claude Code can edit it, exactly like ``scripts/claude-safe-push``. The
  Production boundary remains the OS Managed Policy
  (``/etc/claude-code/managed-settings.json``) and the OS Managed Runtime Guard.
* It is not a Production entry point. ``scripts/claude-production`` is a wholly
  separate launcher with its own fail-closed preflight; this one refuses to
  start at all on a Production runtime.
* It does not change any setting. It never writes to ``/etc``, never edits
  ``.claude/settings.json``, never disables the sandbox, never sets
  ``allowUnsandboxedCommands``, and never touches ``.git`` permissions. The
  Production runtime marker and the OS Managed Policy are *read* -- their mere
  existence is what stops this launcher -- and neither is created, deployed,
  edited, or removed here.

It also grants no new git capability: raw ``git push`` stays denied by
``.claude/settings.json`` and ``.claude/hooks/network_guard.py``, and the only
push path remains ``scripts/claude-safe-push``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from src.claude_runtime_security import (
    MANAGED_SETTINGS_PATH,
    PRODUCTION_MARKER_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent

#: The value ``scripts/claude-production`` exports. Seeing it means a Production
#: session is already in charge, and this launcher must not start.
PRODUCTION_RUNTIME_PROFILE = "production"
DEVELOPMENT_RUNTIME_PROFILE = "development"

RUNTIME_PROFILE_ENV = "DAYTRADE_RUNTIME_PROFILE"

#: The only branch namespace a Development session may be started on. This is
#: the same prefix ``scripts/claude_safe_git.py`` fixes for Safe Start and Safe
#: Push; that module lives under ``scripts/`` and importing it from ``src/``
#: would invert the dependency, so the two constants are instead pinned equal by
#: test (DEV-LAUNCH-034). The rule here is deliberately *stricter* than
#: ``claude_safe_git.verify_current_branch_allowed``: that helper also accepts
#: ``main`` because Safe Sync Main must run there, whereas a session that can
#: commit must never start on ``main``.
BRANCH_PREFIX = "claude/"

#: Environment variables that change what "the repository" means to every git
#: process started by the session (FIX-DEV-GIT-011). ``.claude/hooks/network_guard.py``
#: validates a staged path against the repository root and refuses the git
#: global options that would move it, but it cannot see an environment inherited
#: from before Claude started: with ``GIT_WORK_TREE=/etc`` exported, a plain
#: ``git add -- passwd`` the guard reads as repository-relative writes somewhere
#: else entirely. The launcher therefore refuses to start rather than unsetting
#: them silently -- an inherited routing variable means the shell this session
#: is being started from is not the plain repository-root shell the workflow
#: assumes, and that is for the human to resolve.
GIT_ENVIRONMENT_OVERRIDES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
)

ERROR_CODES = (
    "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME",
    "CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE",
    "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED",
    "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED",
)


class DevelopmentLauncherError(Exception):
    """A fail-closed refusal. Claude is never started."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> DevelopmentLauncherError:
    assert code in ERROR_CODES, code
    return DevelopmentLauncherError(code, message)


def verify_not_production(
    *,
    environ: Mapping[str, str] | None = None,
    production_marker_path: str | Path = PRODUCTION_MARKER_PATH,
    managed_settings_path: str | Path = MANAGED_SETTINGS_PATH,
) -> None:
    """Refuse to start on anything that looks like the Production runtime.

    Each signal is independent and any one of them is enough to stop: the
    root-owned Production marker, an installed OS Managed Policy, and the
    Production runtime profile in the environment. None of them is created or
    removed here -- they are only read.
    """
    environ = os.environ if environ is None else environ

    profile = environ.get(RUNTIME_PROFILE_ENV, "").strip().lower()
    if profile == PRODUCTION_RUNTIME_PROFILE:
        raise _fail(
            "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME",
            f"{RUNTIME_PROFILE_ENV}={profile!r}; this launcher is Development-only",
        )

    marker = Path(production_marker_path)
    if marker.exists():
        raise _fail(
            "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME",
            f"the production runtime marker {str(marker)!r} is present; use "
            "scripts/claude-production on a production host",
        )

    managed = Path(managed_settings_path)
    if managed.exists():
        raise _fail(
            "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME",
            f"an OS Managed Policy is installed at {str(managed)!r}; this host "
            "is not a Development host",
        )


def verify_git_environment_clean(
    *, environ: Mapping[str, str] | None = None
) -> None:
    """Refuse to start with an inherited git routing variable.

    Presence is the test, not the value: an empty ``GIT_DIR`` is still a sign
    that the launching shell is not the ordinary one, and reasoning about which
    values git treats as unset would be guessing. Nothing is removed from the
    environment here -- silently continuing with a repaired environment would
    hide the same misconfiguration from the human and from every later check.
    """
    environ = os.environ if environ is None else environ

    present = [name for name in GIT_ENVIRONMENT_OVERRIDES if name in environ]
    if present:
        raise _fail(
            "CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE",
            f"{', '.join(present)} set in the environment; these reroute the "
            "repository, work tree or index every git command would use. Unset "
            "them and start this launcher from a plain shell",
        )


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
    copied source tree -- fails closed instead of picking one of them.
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


def resolve_current_branch(root: str | Path) -> str:
    """The checked-out branch, or a refusal.

    ``symbolic-ref --quiet HEAD`` answers only for a real branch: it exits 1 on
    a detached HEAD, and non-zero for anything else that went wrong. Neither
    case is guessed at -- an unresolvable HEAD stops the launcher exactly like a
    disallowed branch does.
    """
    completed = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    reference = completed.stdout.strip()
    if completed.returncode != 0 or not reference:
        raise _fail(
            "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED",
            "HEAD is detached or the current branch could not be resolved "
            f"(git symbolic-ref exit {completed.returncode}); start a "
            f"{BRANCH_PREFIX}* branch with scripts/claude-safe-start first",
        )
    if not reference.startswith("refs/heads/"):
        raise _fail(
            "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED",
            f"HEAD points at {reference!r}, which is not a local branch",
        )
    branch = reference[len("refs/heads/") :]
    if not branch:
        raise _fail(
            "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED",
            "git reported an empty branch name",
        )
    return branch


def verify_current_branch_allowed(branch: str) -> None:
    """Only ``claude/*``. ``main`` is refused along with everything else.

    A Development session commits, so starting it anywhere but a feature branch
    would put commits on local ``main``. The prefix must be a real path segment:
    ``claudex`` and a bare ``claude`` are not ``claude/`` branches.
    """
    if not branch.startswith(BRANCH_PREFIX) or branch == BRANCH_PREFIX:
        raise _fail(
            "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED",
            f"current branch {branch!r} is not a {BRANCH_PREFIX}* branch; "
            "Development Claude Code may only commit on a feature branch",
        )


def preflight(
    *,
    environ: Mapping[str, str] | None = None,
    production_marker_path: str | Path = PRODUCTION_MARKER_PATH,
    managed_settings_path: str | Path = MANAGED_SETTINGS_PATH,
    project_root: str | Path = PROJECT_ROOT,
    expected_root: str | Path = REPOSITORY_ROOT,
    git_toplevel: str | None = None,
    current_branch: str | None = None,
) -> dict[str, Any]:
    """Every check, in order. Returns the facts the launcher execs with."""
    verify_not_production(
        environ=environ,
        production_marker_path=production_marker_path,
        managed_settings_path=managed_settings_path,
    )
    verify_git_environment_clean(environ=environ)
    root = resolve_repository_root(
        project_root=project_root,
        expected_root=expected_root,
        git_toplevel=git_toplevel,
    )
    branch = (
        current_branch
        if current_branch is not None
        else resolve_current_branch(root)
    )
    verify_current_branch_allowed(branch)
    return {
        "repository_root": root,
        "project_root": Path(project_root).resolve(),
        "current_branch": branch,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-development",
        description=(
            "DEVELOPMENT-ONLY launcher: start Claude Code from the git "
            "repository root. Not a security boundary; not for Production."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the checks and print the resolved root without starting Claude",
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
    sys.stdout.write(f"current_branch: {result['current_branch']}\n")
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
