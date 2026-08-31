"""Development Claude workflow: the convenience launcher and the guard.

DTWO-2026-026 split what this file tests in two, and kept only one half.

*Gone*: the raw-Git authority model. The launcher no longer refuses branches,
runtime profiles, ``/etc`` markers or inherited ``GIT_*`` variables, and the
guard no longer parses git subcommands, options, pathspecs or commit messages.
Those were Local Operational Governance on a personally owned machine, and
enforcing them cost more than the mistakes they prevented. Ordinary Git is the
standard Development workflow now.

*Kept, and still the point*: market numbers may only enter through the Source
Acquisition CLI. A command that fetches a page directly -- curl, ``wget``,
``python -c`` with ``requests``, ``node -e`` -- bypasses the raw-bytes-plus-
SHA256-plus-deterministic-parser path that every DayTrade figure is supposed to
come from, and is refused here.

The launcher keeps exactly one job: start Claude at the repository root, where
the sandbox can write ``.git``. Started from ``daytrade-sbi/`` instead, the
sandbox grants write access to that subdirectory only, ``.git`` is read-only,
and ``git add`` cannot even create ``.git/index.lock``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src import claude_development_launcher as launcher


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent

LAUNCHER_SCRIPT = PROJECT_ROOT / "scripts" / "claude-development"
PRODUCTION_SCRIPT = PROJECT_ROOT / "scripts" / "claude-production"
NETWORK_GUARD = REPO_ROOT / ".claude" / "hooks" / "network_guard.py"
DEV_SETTINGS = REPO_ROOT / ".claude" / "settings.json"


# ------------------------------------------------------------- launcher ----


@pytest.fixture
def exec_recorder(monkeypatch):
    """Capture the chdir/exec the launcher would perform, without performing it."""
    recorded: dict[str, object] = {}
    real_chdir = os.chdir

    def fake_chdir(path):
        # Still performed, so pytest's own chdir bookkeeping stays honest.
        real_chdir(path)
        recorded["cwd"] = Path(path)

    def fake_execvpe(file, args, env):
        recorded["file"] = file
        recorded["argv"] = list(args)
        recorded["env"] = dict(env)

    monkeypatch.setattr(os, "chdir", fake_chdir)
    monkeypatch.setattr(os, "execvpe", fake_execvpe)
    return recorded


@pytest.mark.parametrize(
    "start_dir",
    [
        pytest.param(PROJECT_ROOT, id="DEV-LAUNCH-001"),
        pytest.param(PROJECT_ROOT / "scripts", id="DEV-LAUNCH-002"),
        pytest.param(REPO_ROOT, id="DEV-LAUNCH-003"),
    ],
)
def test_claude_starts_at_the_repository_root_from_any_subdirectory(
    monkeypatch, exec_recorder, start_dir
):
    """The whole point: cwd at exec time is the repository root, not $PWD."""
    monkeypatch.chdir(start_dir)

    assert launcher.main([], git_toplevel=str(REPO_ROOT)) == 0

    assert exec_recorder["cwd"] == REPO_ROOT
    assert exec_recorder["file"] == "claude"
    assert exec_recorder["argv"] == ["claude"]


def test_the_exec_environment_marks_the_development_profile(
    monkeypatch, exec_recorder
):
    monkeypatch.chdir(REPO_ROOT)

    assert launcher.main([], git_toplevel=str(REPO_ROOT)) == 0

    env = exec_recorder["env"]
    assert env["DAYTRADE_RUNTIME_PROFILE"] == launcher.DEVELOPMENT_RUNTIME_PROFILE


def test_dry_run_reports_the_root_without_starting_claude(
    capsys, exec_recorder
):
    assert launcher.main(["--dry-run"], git_toplevel=str(REPO_ROOT)) == 0

    assert "file" not in exec_recorder, "claude was started during a dry run"
    out = capsys.readouterr().out
    assert f"repository_root: {REPO_ROOT}" in out
    assert "PASS" in out


@pytest.mark.parametrize(
    "branch",
    [
        pytest.param("main", id="TC-05-main"),
        pytest.param("claude/example", id="TC-05-claude"),
        pytest.param("feature/example", id="TC-05-other"),
    ],
)
def test_any_branch_may_start_a_development_session(
    monkeypatch, exec_recorder, tmp_path, branch
):
    """TC-05 / AC-07: the branch is no longer the launcher's business.

    A Development session that starts on ``main`` commits to ``main`` only if
    the human tells it to, and ``main`` is protected where it matters -- at the
    push, and at the Human Merge.
    """
    repository = tmp_path / "repo"
    (repository / "daytrade-sbi" / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(repository)], check=True)
    monkeypatch.chdir(repository)

    resolved = launcher.resolve_repository_root(
        project_root=repository / "daytrade-sbi",
        expected_root=repository,
        git_toplevel=str(repository),
    )

    assert resolved == repository.resolve()


def test_the_launcher_never_consults_the_retired_production_assets():
    """TC-05 / AC-03: no ``/etc`` path, marker, policy or guard is read."""
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    for retired in (
        "/etc/",
        "managed-settings",
        "daytrade-runtime-guard",
        "daytrade-production-runtime",
        "daytrade-seccomp-verified",
        "REQUIRED_CLAUDE_VERSION",
    ):
        assert retired not in source, retired


def test_a_disagreeing_git_top_level_fails_closed():
    """The one refusal left: never guess where the repository is."""
    with pytest.raises(launcher.DevelopmentLauncherError) as error:
        launcher.resolve_repository_root(git_toplevel="/somewhere/else")
    assert error.value.code == "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED"


def test_a_tree_without_a_dot_git_fails_closed(tmp_path):
    with pytest.raises(launcher.DevelopmentLauncherError) as error:
        launcher.resolve_repository_root(
            project_root=tmp_path, expected_root=tmp_path, git_toplevel=str(tmp_path)
        )
    assert error.value.code == "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED"


def test_main_returns_two_and_starts_nothing_when_the_root_is_unresolved(
    exec_recorder, capsys
):
    assert launcher.main([], git_toplevel="/somewhere/else") == 2
    assert "file" not in exec_recorder
    assert "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED" in capsys.readouterr().err


def test_the_script_delegates_to_the_tested_core():
    """The entry point stays thin: no second copy of the logic."""
    source = LAUNCHER_SCRIPT.read_text(encoding="utf-8")
    assert "from src.claude_development_launcher import main" in source
    assert os.access(LAUNCHER_SCRIPT, os.X_OK)


def test_the_script_runs_from_a_subdirectory(tmp_path):
    """End to end, with a real subprocess: ``--dry-run`` resolves the root."""
    completed = subprocess.run(
        [sys.executable, "-B", str(LAUNCHER_SCRIPT), "--dry-run"],
        cwd=str(PROJECT_ROOT / "scripts"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"repository_root: {REPO_ROOT}" in completed.stdout


def test_the_development_launcher_is_not_the_production_launcher():
    development = LAUNCHER_SCRIPT.read_text(encoding="utf-8")
    production = PRODUCTION_SCRIPT.read_text(encoding="utf-8")
    assert "claude_development_launcher" in development
    assert "claude_production_launcher" in production
    assert "--target-date" not in development


@pytest.mark.parametrize(
    "forbidden",
    [
        "allowUnsandboxedCommands",
        "chmod",
        "chown",
        "sandbox",
        "sudo",
    ],
)
def test_the_launcher_relaxes_nothing(forbidden):
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert forbidden not in source


def test_the_launcher_writes_no_file_at_all():
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    for writing in ("write_text(", "write_bytes(", "open(", "mkdir("):
        assert writing not in source, writing


# --------------------------------------------------------- network_guard ----


def _guard_verdict(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, "-B", str(NETWORK_GUARD)],
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "command",
    [
        # TC-06: the ordinary Git workflow, including its network half.
        pytest.param("git status", id="DEV-GIT-001"),
        pytest.param("git diff --check", id="DEV-GIT-002"),
        pytest.param("git add -- daytrade-sbi/src/cli.py", id="DEV-GIT-003"),
        pytest.param('git commit -m "normal message"', id="DEV-GIT-004"),
        pytest.param("git fetch origin", id="DEV-GIT-005"),
        pytest.param("git pull --ff-only origin main", id="DEV-GIT-006"),
        pytest.param("git switch main", id="DEV-GIT-007"),
        pytest.param("git switch -c claude/example", id="DEV-GIT-008"),
        pytest.param("git checkout main", id="DEV-GIT-009"),
        pytest.param(
            "git push -u origin claude/dtwo-2026-026-operational-governance",
            id="DEV-GIT-010",
        ),
        pytest.param("git log --oneline -5", id="DEV-GIT-011"),
        # A commit message is data, even when it names something forbidden.
        pytest.param('git commit -m "fix git push regression"', id="DEV-GIT-012"),
        # The legacy wrappers still work; they are simply no longer required.
        pytest.param("daytrade-sbi/scripts/claude-safe-push", id="DEV-GIT-013"),
        pytest.param("daytrade-sbi/scripts/claude-development", id="DEV-GIT-014"),
        # Running the test suite is not a network operation.
        pytest.param(
            "cd daytrade-sbi && .venv/bin/python -B -m pytest -q", id="DEV-GIT-015"
        ),
    ],
)
def test_the_guard_allows_the_ordinary_development_loop(command):
    result = _guard_verdict(command)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        # TC-06: every direct route to a market page.
        pytest.param("curl https://finance.yahoo.co.jp/", id="BYPASS-001"),
        pytest.param("wget https://kabutan.jp/", id="BYPASS-002"),
        pytest.param("echo x | curl -s https://www.jpx.co.jp/", id="BYPASS-003"),
        pytest.param(
            'python -c "import requests; requests.get(\'https://x\')"', id="BYPASS-004"
        ),
        pytest.param('python3 -c "print(1)"', id="BYPASS-005"),
        pytest.param('node -e "fetch(\'https://x\')"', id="BYPASS-006"),
        pytest.param("nc example.com 80", id="BYPASS-007"),
        pytest.param("telnet example.com 80", id="BYPASS-008"),
        pytest.param("ssh host", id="BYPASS-009"),
        pytest.param("scp file host:/tmp", id="BYPASS-010"),
        pytest.param("pip install requests", id="BYPASS-011"),
        pytest.param("npm install axios", id="BYPASS-012"),
        pytest.param("gh pr create", id="BYPASS-013"),
        pytest.param("Invoke-WebRequest https://x", id="BYPASS-014"),
    ],
)
def test_the_guard_denies_every_evidence_bypass(command):
    result = _guard_verdict(command)
    assert result.returncode == 2, result.stdout
    assert "network_guard" in result.stderr.decode("utf-8")


def test_an_unparseable_payload_fails_closed():
    completed = subprocess.run(
        [sys.executable, "-B", str(NETWORK_GUARD)],
        input=b"{ not json",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2


def test_the_guard_no_longer_parses_git_at_all():
    """AC-10: the git authority model is gone, not merely widened."""
    source = NETWORK_GUARD.read_text(encoding="utf-8")
    for retired in (
        "_LOCAL_GIT_SUBCOMMANDS",
        "_GIT_NETWORK_SUBCOMMANDS",
        "_path_reason",
        "_add_reason",
        "_commit_reason",
        "_branch_reason",
        "_restore_reason",
    ):
        assert retired not in source, retired


# ---------------------------------------------------------- settings.json ---


def _settings() -> dict:
    return json.loads(DEV_SETTINGS.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "rule",
    ["Bash(git fetch:*)", "Bash(git pull:*)", "Bash(git push:*)"],
)
def test_git_traffic_is_no_longer_denied_by_the_repository_policy(rule):
    """TC-07 / AC-08."""
    assert rule not in _settings()["permissions"]["deny"]


@pytest.mark.parametrize(
    "rule",
    [
        "WebSearch",
        "WebFetch",
        "Bash(curl:*)",
        "Bash(wget:*)",
        "Bash(python -c:*)",
        "Bash(node -e:*)",
        "Bash(gh:*)",
        "Bash(sudo:*)",
        "Bash(pip install:*)",
        "Bash(npm install:*)",
    ],
)
def test_the_evidence_bypass_denies_are_all_still_there(rule):
    """TC-07 / AC-10: relaxing Git relaxed nothing else."""
    assert rule in _settings()["permissions"]["deny"]


# ---------------------------------------------------------- documentation ---


def test_the_workflow_document_states_the_ordinary_git_flow():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    for command in (
        "git switch main",
        "git pull --ff-only origin main",
        "git switch -c claude/",
        "git add -- ",
        "git commit -m",
        "git push -u origin claude/",
    ):
        assert command in text, command


@pytest.mark.parametrize(
    "document",
    [
        PROJECT_ROOT / "docs" / "development-workflow.md",
        REPO_ROOT / "CLAUDE.md",
    ],
)
def test_the_documents_still_prohibit_force_push_and_main_push(document):
    """AC-09: what Git relaxation did *not* include."""
    text = document.read_text(encoding="utf-8")
    assert "--force" in text
    assert "force push" in text
    assert "git push origin main" in text
