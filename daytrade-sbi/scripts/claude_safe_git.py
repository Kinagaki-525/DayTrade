#!/usr/bin/env python3
"""Shared helpers for Development-only Safe Git wrappers.

These helpers are a workflow control, not a Production security boundary.
They deliberately expose only the exact repository and ref operations needed by
claude-safe-sync-main and claude-safe-start. They never repair divergent state.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

CANONICAL_ORIGIN_URL = "https://github.com/Kinagaki-525/DayTrade.git"
REMOTE = "origin"
MAIN_BRANCH = "main"
MAIN_LOCAL_REF = "refs/heads/main"
MAIN_REMOTE_REF = "refs/remotes/origin/main"
MAIN_FETCH_REFSPEC = f"refs/heads/{MAIN_BRANCH}:{MAIN_REMOTE_REF}"
BRANCH_PREFIX = "claude/"


class SafeGitError(Exception):
    """Validation failed. The wrapper must stop without auto-repair."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def git_result(*args: str, cwd: str | None = None) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def git(*args: str, cwd: str | None = None) -> str:
    code, out, _ = git_result(*args, cwd=cwd)
    if code != 0:
        # stderr may contain helper/authentication details. Never echo it from
        # the wrapper; the command shape and exit code are enough to fail closed.
        raise SafeGitError(f"git {' '.join(args)} failed (exit {code})")
    return out


def repository_root() -> str:
    try:
        return git("rev-parse", "--show-toplevel")
    except SafeGitError as error:
        raise SafeGitError(f"not inside a git repository: {error}") from error


def _single_url(args: tuple[str, ...], *, label: str, root: str) -> str:
    code, out, _ = git_result(*args, cwd=root)
    if code != 0:
        raise SafeGitError(f"could not resolve {label} (git exit {code})")
    urls = [line.strip() for line in out.splitlines() if line.strip()]
    if len(urls) != 1:
        raise SafeGitError(f"{label} must resolve to exactly one URL; got {len(urls)}")
    return urls[0]


def verify_canonical_origin(root: str) -> None:
    remotes = git("remote", cwd=root).splitlines()
    if REMOTE not in remotes:
        raise SafeGitError(f"no {REMOTE!r} remote; refusing network Git operation")

    fetch_url = _single_url(
        ("remote", "get-url", "--all", REMOTE),
        label=f"{REMOTE} fetch URL",
        root=root,
    )
    if fetch_url != CANONICAL_ORIGIN_URL:
        # Do not echo a non-canonical URL: it may contain embedded credentials.
        raise SafeGitError(f"{REMOTE} fetch URL is not canonical; refusing")

    push_url = _single_url(
        ("remote", "get-url", "--push", "--all", REMOTE),
        label=f"{REMOTE} push URL",
        root=root,
    )
    if push_url != CANONICAL_ORIGIN_URL:
        raise SafeGitError(f"{REMOTE} push URL is not canonical; refusing")

    code, value, _ = git_result("config", "--get", f"remote.{REMOTE}.mirror", cwd=root)
    if code == 1:
        return
    if code != 0:
        raise SafeGitError(
            f"could not read remote.{REMOTE}.mirror (git exit {code})"
        )
    if value.strip().lower() not in ("false", "no", "off", "0", ""):
        raise SafeGitError(f"remote.{REMOTE}.mirror is enabled; refusing")


def verify_working_tree_clean(root: str) -> None:
    status = git("status", "--porcelain", "--untracked-files=all", cwd=root)
    if status:
        raise SafeGitError("working tree is not clean; refusing")


def _git_path(root: str, name: str) -> Path:
    raw = git("rev-parse", "--git-path", name, cwd=root)
    path = Path(raw)
    if not path.is_absolute():
        path = Path(root) / path
    return path


def verify_no_git_operation(root: str) -> None:
    markers = (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "BISECT_START",
        "rebase-apply",
        "rebase-merge",
    )
    active = [marker for marker in markers if _git_path(root, marker).exists()]
    if active:
        raise SafeGitError("git operation is already in progress: " + ", ".join(active))


def current_branch(root: str) -> str:
    code, out, _ = git_result("symbolic-ref", "--quiet", "HEAD", cwd=root)
    if code != 0 or not out.startswith("refs/heads/"):
        raise SafeGitError("HEAD is detached or is not a local branch ref")
    branch = out[len("refs/heads/") :]
    if not branch:
        raise SafeGitError("could not determine current branch")
    return branch


def verify_current_branch_allowed(branch: str) -> None:
    if branch == MAIN_BRANCH or branch.startswith(BRANCH_PREFIX):
        return
    raise SafeGitError(
        f"current branch {branch!r} is not main or a {BRANCH_PREFIX}* branch"
    )


def local_ref_exists(root: str, ref: str) -> bool:
    code, _, _ = git_result("show-ref", "--verify", "--quiet", ref, cwd=root)
    if code == 0:
        return True
    if code == 1:
        return False
    raise SafeGitError(f"could not inspect local ref {ref!r}")


def require_local_main(root: str) -> None:
    if not local_ref_exists(root, MAIN_LOCAL_REF):
        raise SafeGitError("local main does not exist; refusing to create it")


def fetch_main(root: str) -> None:
    code, _, _ = git_result(
        "fetch",
        "--no-tags",
        REMOTE,
        MAIN_FETCH_REFSPEC,
        cwd=root,
    )
    if code != 0:
        raise SafeGitError(f"exact main fetch failed (git exit {code})")


def _is_ancestor(root: str, older: str, newer: str) -> bool:
    code, _, _ = git_result("merge-base", "--is-ancestor", older, newer, cwd=root)
    if code == 0:
        return True
    if code == 1:
        return False
    raise SafeGitError(f"could not compare ancestry {older!r} -> {newer!r}")


def sync_main(root: str, branch: str) -> dict[str, str]:
    require_local_main(root)
    old_local = git("rev-parse", MAIN_LOCAL_REF, cwd=root)
    fetch_main(root)
    remote = git("rev-parse", MAIN_REMOTE_REF, cwd=root)

    if old_local == remote:
        return {
            "old_local_main_sha": old_local,
            "origin_main_sha": remote,
            "new_local_main_sha": old_local,
            "result": "ALREADY_SYNCED",
        }

    local_is_ancestor = _is_ancestor(root, old_local, remote)
    remote_is_ancestor = _is_ancestor(root, remote, old_local)

    if local_is_ancestor and not remote_is_ancestor:
        if branch == MAIN_BRANCH:
            git("merge", "--ff-only", MAIN_REMOTE_REF, cwd=root)
        else:
            # mainが別worktreeでcheckout中ならGit自身が拒否する。回避しない。
            git("branch", "-f", MAIN_BRANCH, MAIN_REMOTE_REF, cwd=root)
        new_local = git("rev-parse", MAIN_LOCAL_REF, cwd=root)
        if new_local != remote:
            raise SafeGitError(
                "main fast-forward completed but local main does not equal origin/main"
            )
        verify_working_tree_clean(root)
        return {
            "old_local_main_sha": old_local,
            "origin_main_sha": remote,
            "new_local_main_sha": new_local,
            "result": "FAST_FORWARDED",
        }

    if remote_is_ancestor and not local_is_ancestor:
        raise SafeGitError(
            "local main is ahead of origin/main; refusing reset/rebase/push"
        )

    raise SafeGitError(
        "local main and origin/main have diverged; refusing merge/reset/rebase"
    )


def validate_target_branch(root: str, target: str) -> None:
    if not target.startswith(BRANCH_PREFIX):
        raise SafeGitError(
            f"target branch {target!r} must start with {BRANCH_PREFIX!r}"
        )
    git("check-ref-format", "--branch", target, cwd=root)


def verify_target_local_absent(root: str, target: str) -> None:
    if local_ref_exists(root, f"refs/heads/{target}"):
        raise SafeGitError(f"local branch {target!r} already exists")


def verify_target_remote_absent(root: str, target: str) -> None:
    ref = f"refs/heads/{target}"
    code, _, _ = git_result(
        "ls-remote", "--exit-code", "--heads", REMOTE, ref, cwd=root
    )
    if code == 0:
        raise SafeGitError(f"remote branch {target!r} already exists")
    if code == 2:
        return
    raise SafeGitError(
        f"could not determine whether remote branch {target!r} exists "
        f"(git exit {code})"
    )


def create_target_branch(root: str, target: str) -> str:
    git("switch", "--no-track", "-c", target, MAIN_LOCAL_REF, cwd=root)
    actual = current_branch(root)
    if actual != target:
        raise SafeGitError(
            f"created branch verification failed: expected {target!r}, got {actual!r}"
        )
    head = git("rev-parse", "HEAD", cwd=root)
    main = git("rev-parse", MAIN_LOCAL_REF, cwd=root)
    if head != main:
        raise SafeGitError(
            "created branch HEAD does not equal the synchronized local main"
        )
    verify_working_tree_clean(root)
    return head
