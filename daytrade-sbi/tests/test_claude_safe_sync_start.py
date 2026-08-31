"""Development Safe Sync / Safe Start contracts.

No test reaches GitHub. Network Git commands are intercepted by a git shim and
redirected to a temporary local bare repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
SAFE_SYNC = PROJECT_ROOT / "scripts" / "claude-safe-sync-main"
SAFE_START = PROJECT_ROOT / "scripts" / "claude-safe-start"
NETWORK_GUARD = REPO_ROOT / ".claude" / "hooks" / "network_guard.py"

CANONICAL_ORIGIN_URL = "https://github.com/Kinagaki-525/DayTrade.git"

GIT_SHIM = r"""#!/usr/bin/env python3
import json
import os
import subprocess
import sys

argv = sys.argv[1:]
with open(os.environ["SAFE_GIT_ALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\n")

if argv and argv[0] == "fetch":
    if os.environ.get("SAFE_GIT_FAIL_FETCH") == "1":
        print("simulated fetch failure", file=sys.stderr)
        sys.exit(128)
    argv = [os.environ["SAFE_GIT_REMOTE_PATH"] if item == "origin" else item for item in argv]

if argv and argv[0] == "ls-remote":
    if os.environ.get("SAFE_GIT_FAIL_LS_REMOTE") == "1":
        print("simulated ls-remote failure", file=sys.stderr)
        sys.exit(128)
    argv = [os.environ["SAFE_GIT_REMOTE_PATH"] if item == "origin" else item for item in argv]

sys.exit(subprocess.run([os.environ["SAFE_GIT_REAL"], *argv]).returncode)
"""


def _run(real_git: str, cwd: Path, *args: str, check: bool = True):
    return subprocess.run(
        [real_git, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def _commit(real_git: str, cwd: Path, name: str, text: str) -> str:
    path = cwd / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _run(real_git, cwd, "add", name)
    _run(real_git, cwd, "commit", "-m", f"commit {name}")
    return _run(real_git, cwd, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    real_git = shutil.which("git")
    assert real_git

    bare = tmp_path / "remote.git"
    subprocess.run(
        [real_git, "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )

    seed = tmp_path / "seed"
    seed.mkdir()
    _run(real_git, seed, "init", "--initial-branch=main")
    _run(real_git, seed, "config", "user.email", "test@example.invalid")
    _run(real_git, seed, "config", "user.name", "Test")
    _commit(real_git, seed, "README.md", "seed\n")
    _run(real_git, seed, "remote", "add", "local", str(bare))
    _run(real_git, seed, "push", "local", "main")

    work = tmp_path / "work"
    subprocess.run(
        [real_git, "clone", str(bare), str(work)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run(real_git, work, "config", "user.email", "test@example.invalid")
    _run(real_git, work, "config", "user.name", "Test")
    _run(real_git, work, "remote", "set-url", "origin", CANONICAL_ORIGIN_URL)

    remote_work = tmp_path / "remote-work"
    subprocess.run(
        [real_git, "clone", str(bare), str(remote_work)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run(real_git, remote_work, "config", "user.email", "test@example.invalid")
    _run(real_git, remote_work, "config", "user.name", "Test")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text(GIT_SHIM, encoding="utf-8")
    shim.chmod(0o755)

    all_log = tmp_path / "git-all.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SAFE_GIT_REAL": real_git,
        "SAFE_GIT_REMOTE_PATH": str(bare),
        "SAFE_GIT_ALL_LOG": str(all_log),
    }

    return {
        "real_git": real_git,
        "bare": bare,
        "work": work,
        "remote_work": remote_work,
        "env": env,
        "all_log": all_log,
    }


def run_wrapper(repo, script: Path, *args: str, env_extra=None):
    env = dict(repo["env"])
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=repo["work"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def commands(repo):
    if not repo["all_log"].exists():
        return []
    return [
        json.loads(line)
        for line in repo["all_log"].read_text(encoding="utf-8").splitlines()
        if line
    ]


def advance_remote(repo, filename="remote.txt", text="remote\n"):
    remote = repo["remote_work"]
    real_git = repo["real_git"]
    _run(real_git, remote, "checkout", "main")
    sha = _commit(real_git, remote, filename, text)
    _run(real_git, remote, "push", "origin", "main")
    return sha


def local_commit(repo, filename="local.txt", text="local\n"):
    return _commit(repo["real_git"], repo["work"], filename, text)


def current_branch(repo):
    return _run(
        repo["real_git"], repo["work"], "branch", "--show-current"
    ).stdout.strip()


def rev(repo, ref):
    return _run(repo["real_git"], repo["work"], "rev-parse", ref).stdout.strip()


def test_safe_sync_001_equal_is_already_synced(repo):
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 0, result.stderr
    assert "result: ALREADY_SYNCED" in result.stdout


def test_safe_sync_002_main_fast_forwards(repo):
    remote_sha = advance_remote(repo)
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 0, result.stderr
    assert "result: FAST_FORWARDED" in result.stdout
    assert rev(repo, "main") == remote_sha


def test_safe_sync_003_feature_branch_stays_checked_out_while_main_moves(repo):
    _run(repo["real_git"], repo["work"], "checkout", "-b", "claude/old-work")
    old_feature = rev(repo, "HEAD")
    remote_sha = advance_remote(repo)

    result = run_wrapper(repo, SAFE_SYNC)

    assert result.returncode == 0, result.stderr
    assert current_branch(repo) == "claude/old-work"
    assert rev(repo, "HEAD") == old_feature
    assert rev(repo, "main") == remote_sha


@pytest.mark.parametrize("state", ["ahead", "diverged"])
def test_safe_sync_004_005_ahead_or_diverged_is_refused(repo, state):
    local_commit(repo)
    if state == "diverged":
        advance_remote(repo)
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "refusing" in result.stderr


def test_safe_sync_006_noncanonical_origin_is_refused(repo):
    _run(
        repo["real_git"],
        repo["work"],
        "remote",
        "set-url",
        "origin",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "not canonical" in result.stderr


def test_safe_sync_007_multiple_fetch_urls_are_refused(repo):
    _run(
        repo["real_git"],
        repo["work"],
        "config",
        "--add",
        "remote.origin.url",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "exactly one URL" in result.stderr


def test_safe_sync_008_009_bad_or_multiple_push_urls_are_refused(repo):
    real_git, work = repo["real_git"], repo["work"]
    _run(
        real_git,
        work,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "push URL" in result.stderr

    _run(real_git, work, "remote", "set-url", "--push", "origin", CANONICAL_ORIGIN_URL)
    _run(
        real_git,
        work,
        "remote",
        "set-url",
        "--push",
        "--add",
        "origin",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "exactly one URL" in result.stderr


def test_safe_sync_010_mirror_is_refused(repo):
    _run(repo["real_git"], repo["work"], "config", "remote.origin.mirror", "true")
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "mirror" in result.stderr


@pytest.mark.parametrize("kind", ["unstaged", "staged", "untracked"])
def test_safe_sync_011_to_013_dirty_tree_is_refused(repo, kind):
    work = repo["work"]
    if kind == "untracked":
        (work / "scratch.txt").write_text("x\n", encoding="utf-8")
    else:
        (work / "README.md").write_text("changed\n", encoding="utf-8")
        if kind == "staged":
            _run(repo["real_git"], work, "add", "README.md")
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "not clean" in result.stderr


def test_safe_sync_014_detached_head_is_refused(repo):
    _run(repo["real_git"], repo["work"], "checkout", "--detach", "HEAD")
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "detached" in result.stderr


def test_safe_sync_015_other_branch_is_refused(repo):
    _run(repo["real_git"], repo["work"], "checkout", "-b", "feature/foo")
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "not main or" in result.stderr


def test_safe_sync_016_arguments_are_refused(repo):
    result = run_wrapper(repo, SAFE_SYNC, "main")
    assert result.returncode == 2


def test_safe_sync_017_to_021_fetch_is_exact_and_no_repair_commands(repo):
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 0, result.stderr
    seen = commands(repo)
    fetches = [argv for argv in seen if argv and argv[0] == "fetch"]
    assert fetches == [
        [
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        ]
    ]
    flat_subcommands = [argv[0] for argv in seen if argv]
    for forbidden in ("pull", "reset", "rebase"):
        assert forbidden not in flat_subcommands
    assert "--tags" not in fetches[0]
    assert "--prune" not in fetches[0]


def test_safe_sync_022_git_operation_in_progress_is_refused(repo):
    git_dir = _run(
        repo["real_git"], repo["work"], "rev-parse", "--git-dir"
    ).stdout.strip()
    marker = Path(repo["work"]) / git_dir / "MERGE_HEAD"
    marker.write_text("deadbeef\n", encoding="utf-8")
    result = run_wrapper(repo, SAFE_SYNC)
    assert result.returncode == 1
    assert "operation is already in progress" in result.stderr


def test_safe_sync_023_fetch_failure_is_not_treated_as_missing_main(repo):
    result = run_wrapper(
        repo, SAFE_SYNC, env_extra={"SAFE_GIT_FAIL_FETCH": "1"}
    )
    assert result.returncode == 1
    assert "fetch failed" in result.stderr


def test_safe_start_001_main_to_new_claude_branch(repo):
    main_sha = rev(repo, "main")
    result = run_wrapper(repo, SAFE_START, "claude/example")
    assert result.returncode == 0, result.stderr
    assert current_branch(repo) == "claude/example"
    assert rev(repo, "HEAD") == main_sha
    assert "result: STARTED" in result.stdout


def test_safe_start_002_to_004_from_old_feature_preserves_old_ref_and_allows_squash_shape(repo):
    _run(repo["real_git"], repo["work"], "checkout", "-b", "claude/old-work")
    old_sha = local_commit(repo, "feature.txt", "feature-only\n")
    main_sha = rev(repo, "main")
    assert old_sha != main_sha

    result = run_wrapper(repo, SAFE_START, "claude/new-work")

    assert result.returncode == 0, result.stderr
    assert current_branch(repo) == "claude/new-work"
    assert rev(repo, "claude/old-work") == old_sha
    assert rev(repo, "HEAD") == rev(repo, "main")
    assert rev(repo, "HEAD") != old_sha


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("claude/a", "claude/b"),
        ("main",),
        ("feature/foo",),
        ("refs/heads/claude/foo",),
        ("claude/bad..name",),
    ],
)
def test_safe_start_005_to_009_argument_and_ref_validation(repo, args):
    result = run_wrapper(repo, SAFE_START, *args)
    assert result.returncode != 0


def test_safe_start_010_existing_local_target_is_refused(repo):
    _run(repo["real_git"], repo["work"], "branch", "claude/existing", "main")
    result = run_wrapper(repo, SAFE_START, "claude/existing")
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_safe_start_011_existing_remote_target_is_refused(repo):
    remote = repo["remote_work"]
    _run(repo["real_git"], remote, "checkout", "-b", "claude/existing")
    _run(repo["real_git"], remote, "push", "origin", "claude/existing")
    result = run_wrapper(repo, SAFE_START, "claude/existing")
    assert result.returncode == 1
    assert "remote branch" in result.stderr


def test_safe_start_012_ls_remote_network_error_is_refused(repo):
    result = run_wrapper(
        repo,
        SAFE_START,
        "claude/new",
        env_extra={"SAFE_GIT_FAIL_LS_REMOTE": "1"},
    )
    assert result.returncode == 1
    assert "could not determine whether remote branch" in result.stderr


def test_safe_start_013_dirty_tree_is_refused(repo):
    (repo["work"] / "scratch.txt").write_text("x\n", encoding="utf-8")
    result = run_wrapper(repo, SAFE_START, "claude/new")
    assert result.returncode == 1


def test_safe_start_014_detached_head_is_refused(repo):
    _run(repo["real_git"], repo["work"], "checkout", "--detach", "HEAD")
    result = run_wrapper(repo, SAFE_START, "claude/new")
    assert result.returncode == 1


def test_safe_start_015_bad_origin_is_refused(repo):
    _run(
        repo["real_git"],
        repo["work"],
        "remote",
        "set-url",
        "origin",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_wrapper(repo, SAFE_START, "claude/new")
    assert result.returncode == 1


@pytest.mark.parametrize("state", ["ahead", "diverged"])
def test_safe_start_016_017_bad_main_state_does_not_create_target(repo, state):
    local_commit(repo)
    if state == "diverged":
        advance_remote(repo)
    result = run_wrapper(repo, SAFE_START, "claude/new")
    assert result.returncode == 1
    probe = _run(
        repo["real_git"],
        repo["work"],
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/claude/new",
        check=False,
    )
    assert probe.returncode == 1


def test_safe_start_018_019_new_branch_uses_latest_main_and_no_upstream(repo):
    remote_sha = advance_remote(repo)
    result = run_wrapper(repo, SAFE_START, "claude/new")
    assert result.returncode == 0, result.stderr
    assert rev(repo, "HEAD") == remote_sha
    upstream = _run(
        repo["real_git"],
        repo["work"],
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    assert upstream.returncode != 0


def test_safe_start_020_to_022_never_builds_force_repair_commands(repo):
    result = run_wrapper(repo, SAFE_START, "claude/new")
    assert result.returncode == 0, result.stderr
    seen = commands(repo)
    subcommands = [argv[0] for argv in seen if argv]
    for forbidden in ("reset", "rebase", "pull"):
        assert forbidden not in subcommands
    switches = [argv for argv in seen if argv and argv[0] == "switch"]
    assert switches == [
        ["switch", "--no-track", "-c", "claude/new", "refs/heads/main"]
    ]
    assert all("-C" not in argv for argv in seen)
    assert all("-B" not in argv for argv in seen)


def _guard_verdict(command: str):
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}}
    )
    return subprocess.run(
        [sys.executable, str(NETWORK_GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )


@pytest.mark.parametrize(
    "command",
    [
        # DTWO-2026-026: ordinary Git is the standard workflow, and the
        # wrappers are one more way to run it rather than the only way.
        "scripts/claude-safe-sync-main",
        "scripts/claude-safe-start claude/example",
        "scripts/claude-safe-push",
        "git fetch origin",
        "git pull --ff-only origin main",
        "git switch -c claude/example",
    ],
)
def test_the_guard_allows_both_the_wrappers_and_ordinary_git(command):
    assert _guard_verdict(command).returncode == 0
