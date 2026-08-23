"""追加のDevelopment Safe Gitセキュリティ回帰テスト。"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import claude_safe_git as safe_git  # noqa: E402


SAFE_WRAPPERS = (
    SCRIPTS / "claude-safe-sync-main",
    SCRIPTS / "claude-safe-start",
    SCRIPTS / "claude-safe-push",
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "test@example.invalid")
    _git(work, "config", "user.name", "Test")
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "seed")
    _git(work, "remote", "add", "origin", safe_git.CANONICAL_ORIGIN_URL)
    return work


def test_safe_git_security_001_noncanonical_fetch_url_never_echoes_credentials(repo):
    secret = "TOP_SECRET_FETCH_TOKEN"
    _git(
        repo,
        "remote",
        "set-url",
        "origin",
        f"https://{secret}@github.com/Kinagaki-525/DayTrade.git",
    )

    with pytest.raises(safe_git.SafeGitError) as caught:
        safe_git.verify_canonical_origin(str(repo))

    message = str(caught.value)
    assert secret not in message
    assert "not canonical" in message


def test_safe_git_security_002_noncanonical_push_url_never_echoes_credentials(repo):
    secret = "TOP_SECRET_PUSH_TOKEN"
    _git(
        repo,
        "remote",
        "set-url",
        "--push",
        "origin",
        f"https://{secret}@github.com/Kinagaki-525/DayTrade.git",
    )

    with pytest.raises(safe_git.SafeGitError) as caught:
        safe_git.verify_canonical_origin(str(repo))

    message = str(caught.value)
    assert secret not in message
    assert "not canonical" in message


def test_safe_git_security_003_bisect_start_marker_is_fail_closed(repo):
    git_dir = _git(repo, "rev-parse", "--git-dir").stdout.strip()
    marker = repo / git_dir / "BISECT_START"
    marker.write_text(
        _git(repo, "rev-parse", "HEAD").stdout.strip() + "\n",
        encoding="utf-8",
    )

    with pytest.raises(safe_git.SafeGitError) as caught:
        safe_git.verify_no_git_operation(str(repo))

    assert "BISECT_START" in str(caught.value)


def test_safe_git_security_004_wrappers_are_committed_executable():
    for wrapper in SAFE_WRAPPERS:
        mode = wrapper.stat().st_mode
        assert mode & stat.S_IXUSR, f"owner execute bit missing: {wrapper}"
        assert os.access(wrapper, os.X_OK), f"wrapper is not executable: {wrapper}"


def test_safe_git_security_005_remote_vcs_helper_is_fail_closed(repo):
    _git(repo, "config", "remote.origin.vcs", "evil-helper")

    with pytest.raises(safe_git.SafeGitError) as caught:
        safe_git.verify_canonical_origin(str(repo))

    message = str(caught.value)
    assert "remote.origin.vcs" in message
    assert "custom remote helpers are not allowed" in message
