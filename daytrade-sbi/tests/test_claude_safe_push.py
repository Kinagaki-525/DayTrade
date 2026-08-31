"""Development Claude Safe Push: the one push path, and its refusals.

The script is the only way Development Claude Code may reach GitHub, so what
matters is not that a happy path works but that every other path is refused:
another remote, another branch, a dirty tree, an argument, a force flag.

No test touches the network. Each one builds a real git repository under
``tmp_path`` -- so the validations run against real git, not a mock -- and puts
a ``git`` shim first on ``PATH`` that delegates everything except ``push``.
``push`` records its argv to a file and exits 0, which lets the refspec and the
absence of force options be asserted on the command that was actually built.

The wrapper is a Development control, not a security boundary (it lives in the
repository and Claude can edit it), so these tests pin its behaviour and the
fact that Production was not taught about it -- not any immutability claim.
"""

from __future__ import annotations

import importlib.machinery
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent

SAFE_PUSH = PROJECT_ROOT / "scripts" / "claude-safe-push"
DEV_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
NETWORK_GUARD = REPO_ROOT / ".claude" / "hooks" / "network_guard.py"

CANONICAL_ORIGIN_URL = "https://github.com/Kinagaki-525/DayTrade.git"

#: Written by the git shim; one JSON line per intercepted push.
PUSH_LOG = "push_argv.log"


GIT_SHIM = """#!/usr/bin/env python3
import json, os, subprocess, sys

argv = sys.argv[1:]
if argv and argv[0] == "push":
    with open(os.environ["SAFE_PUSH_TEST_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\\n")
    sys.exit(0)
sys.exit(subprocess.run([os.environ["SAFE_PUSH_REAL_GIT"], *argv]).returncode)
"""


@pytest.fixture
def repo(tmp_path):
    """A real repository on a claude/* branch with a clean tree and canonical origin."""
    work = tmp_path / "repo"
    work.mkdir()
    real_git = subprocess.run(
        ["which", "git"], capture_output=True, text=True, check=True
    ).stdout.strip()

    def git(*args):
        subprocess.run(["git", *args], cwd=work, check=True, capture_output=True)

    git("init", "--initial-branch=claude/example")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "seed")
    git("remote", "add", "origin", CANONICAL_ORIGIN_URL)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text(GIT_SHIM, encoding="utf-8")
    shim.chmod(0o755)

    log = tmp_path / PUSH_LOG
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["SAFE_PUSH_REAL_GIT"] = real_git
    env["SAFE_PUSH_TEST_LOG"] = str(log)

    return {"work": work, "env": env, "log": log, "git": git}


def run_safe_push(repo, *args):
    return subprocess.run(
        [sys.executable, str(SAFE_PUSH), *args],
        cwd=repo["work"],
        env=repo["env"],
        capture_output=True,
        text=True,
        check=False,
    )


def pushes(repo):
    if not repo["log"].exists():
        return []
    return [
        json.loads(line)
        for line in repo["log"].read_text(encoding="utf-8").splitlines()
        if line
    ]


# ----------------------------------------------------------- accepted path ---


def test_safe_push_001_claude_branch_canonical_origin_clean_tree_is_accepted(repo):
    result = run_safe_push(repo)
    assert result.returncode == 0, result.stderr
    assert "push success" in result.stdout
    assert len(pushes(repo)) == 1


# -------------------------------------------------------------- refusals -----


@pytest.mark.parametrize(
    "branch",
    [
        pytest.param("main", id="SAFE-PUSH-002"),
        pytest.param("master", id="SAFE-PUSH-003"),
        pytest.param("feature/foo", id="SAFE-PUSH-004a"),
        pytest.param("develop", id="SAFE-PUSH-004b"),
        pytest.param("release/1.0", id="SAFE-PUSH-004c"),
    ],
)
def test_only_claude_prefixed_branches_are_pushed(repo, branch):
    repo["git"]("checkout", "-b", branch)
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert pushes(repo) == []


def test_safe_push_005_detached_head_is_refused(repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo["work"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    repo["git"]("checkout", "--detach", head)
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "HEAD is not a branch ref" in result.stderr
    assert pushes(repo) == []


def test_safe_push_006_missing_origin_is_refused(repo):
    repo["git"]("remote", "remove", "origin")
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "no 'origin' remote" in result.stderr
    assert pushes(repo) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/attacker/DayTrade.git",
        "https://github.com/Kinagaki-525/Other.git",
        "git@github.com:Kinagaki-525/DayTrade.git",
        "https://github.com/Kinagaki-525/DayTrade",
    ],
)
def test_safe_push_007_non_canonical_origin_is_refused(repo, url):
    repo["git"]("remote", "set-url", "origin", url)
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "not the canonical" in result.stderr
    assert pushes(repo) == []


def test_safe_push_008_unstaged_change_is_refused(repo):
    (repo["work"] / "README.md").write_text("edited\n", encoding="utf-8")
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    assert pushes(repo) == []


def test_safe_push_009_staged_change_is_refused(repo):
    (repo["work"] / "README.md").write_text("edited\n", encoding="utf-8")
    repo["git"]("add", "README.md")
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    assert pushes(repo) == []


def test_safe_push_010_untracked_file_is_refused(repo):
    (repo["work"] / "scratch.txt").write_text("x\n", encoding="utf-8")
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    assert pushes(repo) == []


@pytest.mark.parametrize(
    "args",
    [
        ["--force"],
        ["-f"],
        ["origin"],
        ["claude/other"],
        ["origin", "main"],
        ["--force-with-lease"],
    ],
)
def test_safe_push_011_any_argument_is_refused(repo, args):
    result = run_safe_push(repo, *args)
    assert result.returncode == 2
    assert "takes no arguments" in result.stderr
    assert pushes(repo) == []


def test_safe_push_020_an_edited_wrapper_or_guard_cannot_ride_along(repo):
    """Tampering is caught, whichever rule catches it.

    An edited-but-uncommitted wrapper or guard leaves the tree dirty, so the
    clean-tree rule already refuses the push -- there is no separate check to
    keep in sync. Committing the edit would pass, which is exactly why this is
    a Development control and not a security boundary.
    """
    for relative in ("scripts/claude-safe-push", ".claude/hooks/network_guard.py"):
        path = repo["work"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("tampered\n", encoding="utf-8")
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    assert pushes(repo) == []


# -------------------------------------------------- the command it builds ----


def test_safe_push_012_pushes_exactly_one_same_name_refspec(repo):
    run_safe_push(repo)
    (argv,) = pushes(repo)
    assert argv == ["push", "origin", "HEAD:refs/heads/claude/example"]


def test_safe_push_013_no_force_delete_or_tag_option_is_ever_built(repo):
    import importlib.util

    spec = importlib.util.spec_from_loader(
        "claude_safe_push",
        importlib.machinery.SourceFileLoader("claude_safe_push", str(SAFE_PUSH)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for branch in ("claude/example", "claude/nested/name", "claude/x"):
        argv = module.push_argv(branch)
        assert argv == ["git", "push", "origin", f"HEAD:refs/heads/{branch}"]
        for forbidden in ("--force", "--force-with-lease", "-f", "--delete", "--tags"):
            assert forbidden not in argv
        refspec = argv[-1]
        assert not refspec.startswith("+"), "a + refspec is a force push"
        assert refspec.count(":") == 1, "no delete or multi refspec"
        assert "refs/tags/" not in refspec


# ------------------------------------------------ where the push really goes --


def test_safe_push_021_a_redirected_pushurl_is_refused(repo):
    """`remote.origin.url` is only the fetch URL; `pushurl` overrides it."""
    repo["git"](
        "remote", "set-url", "--push", "origin",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "attacker" in result.stderr
    assert pushes(repo) == []


def test_safe_push_022_multiple_pushurls_are_refused(repo):
    """Several pushurls make one `git push` write to several repositories."""
    repo["git"]("remote", "set-url", "--push", "origin", CANONICAL_ORIGIN_URL)
    repo["git"](
        "remote", "set-url", "--push", "--add", "origin",
        "https://github.com/attacker/DayTrade.git",
    )
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "2 push URLs" in result.stderr
    assert pushes(repo) == []


def test_safe_push_023_a_canonical_pushurl_is_accepted(repo):
    """An explicit pushurl equal to the canonical URL is the one allowed case."""
    repo["git"]("remote", "set-url", "--push", "origin", CANONICAL_ORIGIN_URL)
    result = run_safe_push(repo)
    assert result.returncode == 0, result.stderr
    assert len(pushes(repo)) == 1


def test_safe_push_024_mirror_remote_is_refused(repo):
    """`remote.origin.mirror` makes every push a --mirror push."""
    repo["git"]("config", "remote.origin.mirror", "true")
    result = run_safe_push(repo)
    assert result.returncode == 1
    assert "mirror" in result.stderr
    assert pushes(repo) == []
    # The setting is refused, never rewritten.
    still_set = subprocess.run(
        ["git", "config", "--get", "remote.origin.mirror"],
        cwd=repo["work"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert still_set.stdout.strip() == "true"


# ------------------------------------------- legacy compatibility status ----
#
# DTWO-2026-026 demoted this wrapper. Ordinary ``git push`` is the standard
# Development workflow now, and the repository policy no longer denies it, so
# the wrapper is a convenience that still refuses the two pushes nobody should
# make -- not a boundary anything depends on.


def test_the_wrapper_is_documented_as_optional_legacy():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    assert "claude-safe-push" in text
    assert "legacy" in text.lower()


def test_the_repository_policy_no_longer_denies_raw_git_push():
    settings = json.loads(DEV_SETTINGS.read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    assert "Bash(git push:*)" not in deny
    # The sandbox still has to be able to reach GitHub for any push at all.
    assert "github.com" in settings["sandbox"]["network"]["allowedDomains"]
    assert settings["sandbox"]["failIfUnavailable"] is True
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False
