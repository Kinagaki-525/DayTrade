"""The Production Context Launcher: what it checks, and what it refuses to.

DTWO-2026-026 turned this launcher from a fail-closed security preflight into a
context resolver. The tests come in two halves, and the second half is the point
of the change.

*What it checks* -- that this checkout is a state a nightly may run from. A real
``--target-date``, ``main``, no half-finished git operation, no uncommitted
tracked file, a resolvable HEAD. Each of those failures means the run would be
attributed to a commit that does not describe the code about to execute.

*What it must never check again* -- the local Claude executor's configuration.
An ``/etc`` marker, an OS managed policy, a runtime guard, a seccomp
attestation, an exact provider version, sandbox binaries, MCP or Remote Control
state, network reachability. Every fixture here is built without a single one of
them present, so a launcher that started reading the host again would fail these
tests rather than quietly pass on the developer's machine and refuse on someone
else's.

The launcher also writes nothing, ever -- ``--preflight-only`` leaves no
``runtime_security.json`` and no file at all under ``runs/``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src import claude_production_launcher as launcher
from src.production_context import ProductionContextError


TARGET_DATE = "2026-09-01"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SCRIPT = PROJECT_ROOT / "scripts" / "claude-production"


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repository),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repository(tmp_path):
    """A real git repository on ``main`` with a clean tree and one commit.

    Real git, not a fake: the launcher's questions ("is a rebase open?", "is
    HEAD detached?") are questions about git's own on-disk state, and a stub
    that answered them would be testing the stub.
    """
    root = tmp_path / "DayTrade"
    (root / "daytrade-sbi" / "src").mkdir(parents=True)
    (root / "daytrade-sbi" / "runs").mkdir()
    (root / "daytrade-sbi" / "README.md").write_text("x\n", encoding="utf-8")

    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "--", "daytrade-sbi/README.md")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _preflight(repository: Path, target_date: str = TARGET_DATE, **overrides):
    return launcher.preflight(
        target_date=target_date,
        daytrade_root=repository / "daytrade-sbi",
        **overrides,
    )


def _git_dir(repository: Path) -> Path:
    return repository / ".git"


def _launcher_code() -> str:
    """The launcher source with its module docstring removed."""
    import ast

    source = Path(launcher.__file__).read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source))
    return source.replace(docstring, "") if docstring else source


# ------------------------------------------------------- TC-01 happy path ---


def test_a_clean_main_checkout_passes_with_no_os_security_assets(repository):
    """TC-01 / AC-01: nothing under ``/etc`` is required, or consulted."""
    result = _preflight(repository)

    assert result["target_date"] == TARGET_DATE
    assert result["current_branch"] == "main"
    assert result["daytrade_root"] == (repository / "daytrade-sbi").resolve()
    assert result["project_root"] == repository.resolve()
    assert result["run_dir"] == (repository / "daytrade-sbi" / "runs" / TARGET_DATE)
    assert len(result["git_head_sha"]) == 40


def test_an_untracked_file_does_not_refuse_the_launch(repository):
    """AC-02: tracked cleanliness only.

    A scratch file in the checkout says nothing about which committed code is
    about to run, and run directories are untracked by design.
    """
    (repository / "daytrade-sbi" / "scratch.txt").write_text("x", encoding="utf-8")
    (repository / "daytrade-sbi" / "runs" / TARGET_DATE).mkdir(parents=True)

    assert _preflight(repository)["current_branch"] == "main"


def test_the_environment_carries_the_run_context(repository):
    result = _preflight(repository)

    env = launcher.build_environment(result, {"EXISTING": "kept"})

    assert env["EXISTING"] == "kept"
    assert env["DAYTRADE_RUNTIME_PROFILE"] == "production"
    assert env["DAYTRADE_PROJECT_ROOT"] == str(repository.resolve())
    assert env["DAYTRADE_ROOT"] == str((repository / "daytrade-sbi").resolve())
    assert env["DAYTRADE_RUN_DIR"] == str(result["run_dir"])
    assert env["DAYTRADE_TARGET_DATE"] == TARGET_DATE
    assert env["DAYTRADE_GIT_HEAD_SHA"] == result["git_head_sha"]


# ------------------------------------- TC-02 no runtime security dependency ---


@pytest.mark.parametrize(
    "retired",
    [
        "/etc/daytrade-production-runtime",
        "/etc/daytrade-seccomp-verified",
        "/etc/claude-code/managed-settings.json",
        "/etc/claude-code/daytrade-runtime-guard.py",
        "managed-settings",
        "daytrade-runtime-guard",
        "seccomp",
        "bwrap",
        "socat",
        "REQUIRED_CLAUDE_VERSION",
        "2.1.251",
        "claude --version",
        "runtime_security",
        "mcp",
        "remote",
    ],
)
def test_the_launcher_names_no_retired_runtime_security_asset(retired):
    """TC-02 / AC-03: the dependency is gone from the code, not just unused.

    The module docstring is excluded on purpose -- it says what the launcher no
    longer does, and that sentence is worth keeping.
    """
    source = _launcher_code()
    assert retired not in source, retired


def test_the_launcher_runs_no_network_or_remote_git_command(repository):
    """TC-02 / AC-04: every git command is a local inspection."""
    seen: list[list[str]] = []

    def recording_run(argv, cwd):
        seen.append(list(argv))
        return launcher.default_run_command(argv, cwd)

    _preflight(repository, run_command=recording_run)

    assert seen, "the launcher ran no git command at all"
    for argv in seen:
        assert argv[0] == "git", argv
        assert argv[1] not in {
            "fetch",
            "pull",
            "push",
            "clone",
            "ls-remote",
            "remote",
        }, argv


# ------------------------------------------------------- TC-03 fail cases ---


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2026-13-01", id="impossible-month"),
        pytest.param("2026-02-30", id="impossible-day"),
        pytest.param("2026-9-1", id="not-padded"),
        pytest.param(" 2026-09-01", id="leading-space"),
        pytest.param("2026-09-01/../..", id="path-segment"),
        pytest.param("", id="empty"),
    ],
)
def test_an_invalid_target_date_is_refused(repository, value):
    with pytest.raises(ProductionContextError) as error:
        _preflight(repository, target_date=value)
    assert error.value.code == "CLAUDE_TARGET_DATE_INVALID"


def test_a_feature_branch_is_refused(repository):
    _git(repository, "switch", "-q", "-c", "claude/example")

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository)
    assert error.value.code == "CLAUDE_PRODUCTION_BRANCH_NOT_MAIN"


def test_a_detached_head_is_refused(repository):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repository),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(repository, "checkout", "-q", "--detach", head)

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository)
    assert error.value.code == "CLAUDE_PRODUCTION_BRANCH_NOT_MAIN"


@pytest.mark.parametrize(
    "marker",
    [
        "MERGE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
    ],
)
def test_an_open_git_operation_is_refused(repository, marker):
    """A half-finished merge or rebase is a transient state nobody reviewed."""
    target = _git_dir(repository) / marker
    if marker.startswith("rebase-"):
        target.mkdir()
    else:
        target.write_text("x\n", encoding="utf-8")

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository)
    assert error.value.code == "CLAUDE_PRODUCTION_GIT_OPERATION_IN_PROGRESS"


def test_a_modified_tracked_file_is_refused(repository):
    (repository / "daytrade-sbi" / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository)
    assert error.value.code == "CLAUDE_PRODUCTION_WORKING_TREE_DIRTY"


def test_a_staged_change_is_refused(repository):
    (repository / "daytrade-sbi" / "README.md").write_text("changed\n", encoding="utf-8")
    _git(repository, "add", "--", "daytrade-sbi/README.md")

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository)
    assert error.value.code == "CLAUDE_PRODUCTION_WORKING_TREE_DIRTY"


def test_an_unresolvable_head_is_refused(repository):
    def failing_head(argv, cwd):
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            raise ProductionContextError(
                "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED", "no HEAD"
            )
        return launcher.default_run_command(argv, cwd)

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository, run_command=failing_head)
    assert error.value.code == "CLAUDE_PRODUCTION_HEAD_UNRESOLVED"


def test_a_short_head_sha_is_refused(repository):
    def short_head(argv, cwd):
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return "abc1234\n"
        return launcher.default_run_command(argv, cwd)

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository, run_command=short_head)
    assert error.value.code == "CLAUDE_PRODUCTION_HEAD_UNRESOLVED"


def test_a_disagreeing_repository_root_is_refused(repository, tmp_path):
    with pytest.raises(ProductionContextError) as error:
        _preflight(repository, project_root=tmp_path / "elsewhere")
    assert error.value.code == "CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED"


def test_a_run_directory_outside_the_runs_root_is_refused(repository):
    """Containment: ``runs/<target-date>`` must stay under this checkout."""
    runs = repository / "daytrade-sbi" / "runs"
    escape = repository.parent / "outside"
    escape.mkdir()
    (runs / TARGET_DATE).symlink_to(escape, target_is_directory=True)

    with pytest.raises(ProductionContextError) as error:
        _preflight(repository)
    assert error.value.code == "CLAUDE_TARGET_DATE_INVALID"


def test_main_returns_two_and_starts_nothing_on_a_refusal(
    repository, capsys, monkeypatch
):
    started: list[str] = []
    monkeypatch.setattr(os, "execvpe", lambda *a, **k: started.append("started"))

    code = launcher.main(
        ["--target-date", "not-a-date"],
        daytrade_root=repository / "daytrade-sbi",
    )

    assert code == 2
    assert not started
    assert "CLAUDE_TARGET_DATE_INVALID" in capsys.readouterr().err


# ------------------------------------------------- TC-04 no artifact write ---


def test_preflight_only_writes_no_file_at_all(repository, capsys):
    """TC-04 / AC-06: no attestation, no ``runtime_security.json``, nothing."""
    before = {
        path.relative_to(repository).as_posix()
        for path in repository.rglob("*")
        if path.is_file()
    }

    code = launcher.main(
        ["--target-date", TARGET_DATE, "--preflight-only"],
        daytrade_root=repository / "daytrade-sbi",
    )

    assert code == 0
    assert "PASS" in capsys.readouterr().out
    after = {
        path.relative_to(repository).as_posix()
        for path in repository.rglob("*")
        if path.is_file()
    }
    assert after == before, f"the launcher wrote {sorted(after - before)}"
    assert not (repository / "daytrade-sbi" / "runs" / TARGET_DATE).exists()


def test_the_launcher_source_writes_nothing():
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    for writing in ("write_text(", "write_bytes(", "atomic_write", "mkdir("):
        assert writing not in source, writing


def test_the_script_delegates_to_the_tested_core():
    source = LAUNCHER_SCRIPT.read_text(encoding="utf-8")
    assert "from src.claude_production_launcher import main" in source
    assert "HUMAN-ONLY" in source
    assert os.access(LAUNCHER_SCRIPT, os.X_OK)
