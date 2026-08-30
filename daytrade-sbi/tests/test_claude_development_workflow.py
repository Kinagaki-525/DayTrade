"""Development Claude git workflow: the launcher, and the guard's git parsing.

Two real failures are pinned here.

1. Started from ``daytrade-sbi/``, Development Claude Code gets a sandbox that
   cannot write the repository-root ``.git``, so ``git add`` fails to create
   ``.git/index.lock``. ``scripts/claude-development`` fixes that by starting
   Claude at the repository root -- with no sandbox change of any kind -- so the
   test that matters is that the ``chdir`` before ``exec`` really lands there no
   matter which subdirectory the human called it from.

2. ``.claude/hooks/network_guard.py`` re-parsed every quoted argument that
   contained a space as a command of its own, so
   ``git commit -m "chore: development git metadata acceptance"`` was refused as
   a raw ``git metadata`` invocation. A commit message is data; a shell ``-c``
   string is not. Both halves of that distinction are tested, together with the
   narrow ``git restore --staged -- <path>`` unstage allowance.

Two contracts found in review are pinned alongside them.

3. The launcher starts a session that commits, so it must refuse to start
   anywhere but a ``claude/*`` branch -- ``main``, any other branch, a detached
   HEAD and an unresolvable HEAD all fail closed.
4. ``git commit`` may only turn the already-staged index into a commit. Every
   form that stages more (``-a``, ``--only``, ``--include``, a pathspec) or
   rewrites history (``--amend``, ``--fixup``, ``--squash``) is refused.

Neither the launcher nor the guard is a security boundary; the Production
boundary stays the OS Managed Policy and the OS Managed Runtime Guard, and the
last group of tests pins that this work did not reach into it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest
from _pytest.outcomes import Failed

from src import claude_development_launcher as launcher


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent

SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import claude_safe_git as safe_git  # noqa: E402

LAUNCHER_SCRIPT = PROJECT_ROOT / "scripts" / "claude-development"
PRODUCTION_SCRIPT = PROJECT_ROOT / "scripts" / "claude-production"
NETWORK_GUARD = REPO_ROOT / ".claude" / "hooks" / "network_guard.py"

DEV_SETTINGS = REPO_ROOT / ".claude" / "settings.json"


@pytest.fixture
def isolated_development_host(tmp_path):
    """Every Production signal the launcher reads, pinned absent for one test.

    ``verify_not_production`` defaults to the real ``/etc`` locations and
    ``preflight`` falls back to ``os.environ``, so a test that passed neither
    was reading the *host's* security state. On a developer machine that state
    is empty and everything passes; on a Production WSL, on a host where an OS
    Managed Policy has been deployed, or in a shell that happens to export
    ``DAYTRADE_RUNTIME_PROFILE`` or a ``GIT_*`` routing variable, the same
    tests would fail for a reason unrelated to what they assert.

    The paths live under this test's own ``tmp_path`` and are never created, so
    "absent" is a fact about this test rather than an assumption about the
    repository. Deliberately not autouse: a test that wants a signal present
    must say so, and one that forgets the fixture fails loudly rather than
    silently reading the host again.
    """
    production_marker_path = tmp_path / "absent-production-marker"
    managed_settings_path = tmp_path / "absent-managed-settings.json"

    assert not production_marker_path.exists()
    assert not managed_settings_path.exists()

    return {
        "environ": {},
        "production_marker_path": production_marker_path,
        "managed_settings_path": managed_settings_path,
    }


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


#: A branch the launcher accepts, injected so these tests do not depend on the
#: checkout they happen to run in (CI checks out a PR at a detached HEAD).
ALLOWED_BRANCH = "claude/example"


@pytest.mark.parametrize(
    "start_dir",
    [
        pytest.param(PROJECT_ROOT, id="DEV-LAUNCH-001"),
        pytest.param(PROJECT_ROOT / "scripts", id="DEV-LAUNCH-002"),
        pytest.param(REPO_ROOT, id="DEV-LAUNCH-003"),
    ],
)
def test_claude_starts_at_the_repository_root_from_any_subdirectory(
    monkeypatch, exec_recorder, start_dir, isolated_development_host
):
    """The whole point: cwd at exec time is the repository root, not $PWD."""
    monkeypatch.chdir(start_dir)

    assert (
        launcher.main(
            [], current_branch=ALLOWED_BRANCH, **isolated_development_host
        )
        == 0
    )

    assert exec_recorder["cwd"] == REPO_ROOT
    assert exec_recorder["file"] == "claude"
    assert exec_recorder["argv"] == ["claude"]


def test_dev_launch_004_the_exec_environment_marks_the_development_profile(
    monkeypatch, exec_recorder, isolated_development_host
):
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.delenv(launcher.RUNTIME_PROFILE_ENV, raising=False)

    assert (
        launcher.main(
            [], current_branch=ALLOWED_BRANCH, **isolated_development_host
        )
        == 0
    )

    env = exec_recorder["env"]
    assert env[launcher.RUNTIME_PROFILE_ENV] == "development"


def test_dev_launch_005_dry_run_reports_the_root_without_starting_claude(
    monkeypatch, exec_recorder, capsys, isolated_development_host
):
    monkeypatch.chdir(PROJECT_ROOT)

    assert (
        launcher.main(
            ["--dry-run"], current_branch=ALLOWED_BRANCH, **isolated_development_host
        )
        == 0
    )

    assert "file" not in exec_recorder, "--dry-run must not exec claude"
    out = capsys.readouterr().out
    assert str(REPO_ROOT) in out
    assert f"current_branch: {ALLOWED_BRANCH}" in out


def test_dev_launch_006_the_script_delegates_to_the_tested_core():
    """A thin entry point; the checks live where they can be unit-tested."""
    text = LAUNCHER_SCRIPT.read_text(encoding="utf-8")
    assert "from src.claude_development_launcher import main" in text
    assert os.access(LAUNCHER_SCRIPT, os.X_OK)


#: Runs the real wrapper source with the launcher's inputs pinned inside the
#: child. The wrapper is executed, never re-implemented: ``runpy.run_path``
#: loads ``scripts/claude-development`` itself, so this still tests the file a
#: human invokes. Only ``launcher.main`` is bound to fixed inputs beforehand,
#: which is what makes the outcome the same on a Development host, on a
#: Production host, and in CI at a detached HEAD.
_WRAPPER_CHILD = """
import functools
import runpy
import sys

import src.claude_development_launcher as launcher

script, marker, managed, branch = sys.argv[1:5]

launcher.main = functools.partial(
    launcher.main,
    environ={},
    production_marker_path=marker,
    managed_settings_path=managed,
    current_branch=branch,
)

sys.argv = [script, "--dry-run"]
runpy.run_path(script, run_name="__main__")
"""


def _sanitized_child_environment() -> dict[str, str]:
    """The parent environment minus everything the launcher refuses to inherit.

    ``environ={}`` is handed to ``launcher.main`` in the child, but
    ``resolve_repository_root`` shells out to git, and that subprocess inherits
    the child's OS environment. A ``GIT_*`` routing variable exported in the
    developer's shell would therefore still reach git. PATH and the rest of the
    ordinary environment are kept so Python and git start normally.
    """
    env = os.environ.copy()
    env.pop(launcher.RUNTIME_PROFILE_ENV, None)
    for name in launcher.GIT_ENVIRONMENT_OVERRIDES:
        env.pop(name, None)
    return env


def test_dev_launch_007_the_script_runs_from_a_subdirectory(tmp_path):
    """End to end, as a human would call it: `cd daytrade-sbi && scripts/...`.

    The wrapper runs for real; its Production signals are isolated inside the
    child process. The expected result is therefore fixed -- it does not change
    with the host's ``/etc`` state or with the branch this checkout happens to
    be on.
    """
    marker = tmp_path / "absent-production-marker"
    managed = tmp_path / "absent-managed-settings.json"
    assert not marker.exists()
    assert not managed.exists()

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            _WRAPPER_CHILD,
            str(LAUNCHER_SCRIPT),
            str(marker),
            str(managed),
            ALLOWED_BRANCH,
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=_sanitized_child_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert f"repository_root: {REPO_ROOT}" in completed.stdout
    assert f"current_branch: {ALLOWED_BRANCH}" in completed.stdout
    assert "development launcher preflight PASS" in completed.stdout


def test_dev_launch_008_the_child_environment_carries_no_routing_variable():
    """The sanitizer must remove every variable the launcher refuses.

    Pinned against the launcher's own list, so a variable added there cannot
    quietly keep reaching git in the subprocess test above.
    """
    env = _sanitized_child_environment()
    assert launcher.RUNTIME_PROFILE_ENV not in env
    for name in launcher.GIT_ENVIRONMENT_OVERRIDES:
        assert name not in env, f"{name} would still reach the child's git"
    assert "PATH" in env, "the child still needs a normal PATH"


# ------------------------------------------- launcher: production refusal ---
#
# These drive the real check by injecting a signal that IS present. Every other
# launcher test pins both signals absent, so none of them can pass or fail
# because of what happens to be installed under /etc on the host.


def test_dev_launch_016_the_default_etc_paths_are_never_consulted(
    monkeypatch, isolated_development_host
):
    """Behaviour-level guard: a fall back to the real /etc paths fails here.

    Asserting on the fixture's dictionary would only restate the fixture. This
    watches ``Path.exists`` instead, so a launcher call that quietly loses its
    injected paths -- or a new check that reads the module defaults directly --
    is caught even on a machine where those files do not exist and nothing
    would otherwise go wrong.
    """
    original_exists = Path.exists
    forbidden = {
        Path(launcher.PRODUCTION_MARKER_PATH),
        Path(launcher.MANAGED_SETTINGS_PATH),
    }

    def guarded_exists(path):
        if path in forbidden:
            pytest.fail(f"real host Production path consulted: {path}")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", guarded_exists)

    result = launcher.preflight(
        current_branch=ALLOWED_BRANCH, **isolated_development_host
    )

    assert result["current_branch"] == ALLOWED_BRANCH
    assert result["repository_root"] == REPO_ROOT


def test_dev_launch_017_the_guard_itself_detects_a_default_path_read(monkeypatch):
    """The guard above is only worth having if it actually fires."""
    original_exists = Path.exists
    forbidden = {
        Path(launcher.PRODUCTION_MARKER_PATH),
        Path(launcher.MANAGED_SETTINGS_PATH),
    }

    def guarded_exists(path):
        if path in forbidden:
            pytest.fail(f"real host Production path consulted: {path}")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", guarded_exists)

    with pytest.raises(Failed):
        # Exactly what a lost injection would do: the module default is read.
        Path(launcher.PRODUCTION_MARKER_PATH).exists()


def test_dev_launch_010_a_production_runtime_profile_is_refused(
    isolated_development_host,
):
    """Only the runtime profile is set; both paths stay absent."""
    inputs = dict(isolated_development_host)
    inputs["environ"] = {launcher.RUNTIME_PROFILE_ENV: "production"}

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(**inputs)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME"


def test_dev_launch_011_the_production_marker_is_refused(
    tmp_path, isolated_development_host
):
    """Only the marker is present; the profile and the policy stay absent."""
    marker = tmp_path / "daytrade-production-runtime"
    marker.write_text("DAYTRADE_PRODUCTION_RUNTIME_V1\n", encoding="utf-8")
    inputs = dict(isolated_development_host)
    inputs["production_marker_path"] = marker

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(**inputs)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME"


def test_dev_launch_012_an_installed_managed_policy_is_refused(
    tmp_path, isolated_development_host
):
    """Only the policy is present; the profile and the marker stay absent."""
    managed = tmp_path / "managed-settings.json"
    managed.write_text("{}", encoding="utf-8")
    inputs = dict(isolated_development_host)
    inputs["managed_settings_path"] = managed

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(**inputs)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME"


def test_dev_launch_013_main_returns_two_and_starts_nothing_on_a_production_host(
    monkeypatch, exec_recorder, tmp_path, capsys, isolated_development_host
):
    """main() on a Production host: the marker is the only signal set."""
    marker = tmp_path / "daytrade-production-runtime"
    marker.write_text("DAYTRADE_PRODUCTION_RUNTIME_V1\n", encoding="utf-8")
    monkeypatch.chdir(PROJECT_ROOT)
    inputs = dict(isolated_development_host)
    inputs["production_marker_path"] = marker

    assert launcher.main([], current_branch=ALLOWED_BRANCH, **inputs) == 2

    assert "file" not in exec_recorder, "claude must not be started"
    assert "CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME" in capsys.readouterr().err


def test_dev_launch_014_a_disagreeing_git_top_level_fails_closed(
    tmp_path, isolated_development_host
):
    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(git_toplevel=str(tmp_path), **isolated_development_host)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED"


def test_dev_launch_015_a_tree_without_a_dot_git_fails_closed(
    tmp_path, isolated_development_host
):
    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(
            project_root=tmp_path,
            expected_root=tmp_path,
            git_toplevel=str(tmp_path),
            **isolated_development_host,
        )
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED"


# ------------------------------ launcher: inherited git environment (FIX-DEV-GIT-011) ---


@pytest.mark.parametrize(
    "variable",
    [
        pytest.param("GIT_DIR", id="DEV-LAUNCH-040"),
        pytest.param("GIT_WORK_TREE", id="DEV-LAUNCH-041"),
        pytest.param("GIT_INDEX_FILE", id="DEV-LAUNCH-042"),
        pytest.param("GIT_COMMON_DIR", id="DEV-LAUNCH-043"),
        pytest.param("GIT_NAMESPACE", id="DEV-LAUNCH-044"),
        pytest.param("GIT_OBJECT_DIRECTORY", id="DEV-LAUNCH-045"),
        pytest.param("GIT_ALTERNATE_OBJECT_DIRECTORIES", id="DEV-LAUNCH-046"),
        pytest.param("GIT_CEILING_DIRECTORIES", id="DEV-LAUNCH-047"),
    ],
)
def test_the_launcher_refuses_an_inherited_git_routing_variable(
    variable, isolated_development_host
):
    """The guard cannot see the environment; the launcher refuses to inherit it."""
    inputs = dict(isolated_development_host)
    inputs["environ"] = {variable: "/tmp/elsewhere"}

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(current_branch=ALLOWED_BRANCH, **inputs)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE"
    assert variable in excinfo.value.message


def test_dev_launch_048_an_empty_git_routing_variable_still_fails_closed(
    isolated_development_host,
):
    """Presence is the test: an empty GIT_DIR is not a normal shell either."""
    inputs = dict(isolated_development_host)
    inputs["environ"] = {"GIT_DIR": ""}

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(current_branch=ALLOWED_BRANCH, **inputs)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE"


def test_dev_launch_049_a_plain_environment_passes_on_a_claude_branch(
    isolated_development_host,
):
    """The ordinary case: no GIT_* routing, a claude/* branch, PASS."""
    result = launcher.preflight(
        current_branch=ALLOWED_BRANCH, **isolated_development_host
    )
    assert result["current_branch"] == ALLOWED_BRANCH
    assert result["repository_root"] == REPO_ROOT


def test_dev_launch_050_main_refuses_and_starts_nothing_with_git_dir_set(
    monkeypatch, exec_recorder, capsys, isolated_development_host
):
    """The routing variable is injected, not exported into the real process."""
    monkeypatch.chdir(PROJECT_ROOT)
    inputs = dict(isolated_development_host)
    inputs["environ"] = {"GIT_WORK_TREE": "/etc"}

    assert launcher.main([], current_branch=ALLOWED_BRANCH, **inputs) == 2

    assert "file" not in exec_recorder, "claude must not be started"
    assert "CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE" in capsys.readouterr().err


def test_dev_launch_051_the_environment_is_refused_not_repaired():
    """Fail closed rather than unsetting: a silent repair hides the cause."""
    environ = {"GIT_DIR": "/tmp/x"}
    with pytest.raises(launcher.DevelopmentLauncherError):
        launcher.verify_git_environment_clean(environ=environ)
    assert environ == {"GIT_DIR": "/tmp/x"}, "the launcher mutated the environment"


# ------------------------------------------- launcher: claude/* branch only ---


def _init_repo(path: Path, branch: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> None:
        completed = subprocess.run(
            ["git", *args], cwd=str(path), capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr

    run("init", f"--initial-branch={branch}")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "Test")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-m", "seed")
    return path


def test_dev_launch_030_a_claude_branch_is_allowed(isolated_development_host):
    """ALLOW: the one branch namespace a Development session may commit on."""
    result = launcher.preflight(
        current_branch="claude/example", **isolated_development_host
    )
    assert result["current_branch"] == "claude/example"


@pytest.mark.parametrize(
    "branch",
    [
        pytest.param("main", id="DEV-LAUNCH-031"),
        pytest.param("master", id="DEV-LAUNCH-031b"),
        pytest.param("feature/example", id="DEV-LAUNCH-032"),
        # The prefix must be a real path segment, not a name that starts alike.
        pytest.param("claude", id="DEV-LAUNCH-032b"),
        pytest.param("claudex/example", id="DEV-LAUNCH-032c"),
        pytest.param("claude/", id="DEV-LAUNCH-032d"),
    ],
)
def test_the_launcher_refuses_every_branch_outside_the_claude_namespace(
    branch, isolated_development_host
):
    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.preflight(current_branch=branch, **isolated_development_host)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED"


def test_dev_launch_033_a_detached_head_fails_closed(tmp_path):
    repo = _init_repo(tmp_path / "detached", "claude/example")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "--detach", head],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.resolve_current_branch(repo)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED"


def test_dev_launch_034_a_branch_resolution_failure_fails_closed(tmp_path):
    """No repository at all: an unanswerable HEAD is a refusal, not a default."""
    outside = tmp_path / "not-a-repository"
    outside.mkdir()

    with pytest.raises(launcher.DevelopmentLauncherError) as excinfo:
        launcher.resolve_current_branch(outside)
    assert excinfo.value.code == "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED"


def test_dev_launch_035_a_real_claude_branch_checkout_resolves(tmp_path):
    repo = _init_repo(tmp_path / "on-branch", "claude/example")
    assert launcher.resolve_current_branch(repo) == "claude/example"


def test_dev_launch_036_main_refuses_and_starts_nothing(
    monkeypatch, exec_recorder, capsys, isolated_development_host
):
    monkeypatch.chdir(PROJECT_ROOT)

    assert launcher.main([], current_branch="main", **isolated_development_host) == 2

    assert "file" not in exec_recorder, "claude must not be started on main"
    assert "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED" in capsys.readouterr().err


def test_dev_launch_037_the_branch_prefix_matches_the_safe_git_contract():
    """One contract, two users: the prefix is not re-invented here.

    ``claude_safe_git`` lives under ``scripts/`` and importing it from ``src/``
    would invert the dependency, so the shared constant is pinned by test
    instead. The launcher's *rule* is deliberately stricter: the Safe Git helper
    also accepts ``main`` because Safe Sync Main must run there.
    """
    assert launcher.BRANCH_PREFIX == safe_git.BRANCH_PREFIX

    safe_git.verify_current_branch_allowed("main")  # allowed there...
    with pytest.raises(launcher.DevelopmentLauncherError):  # ...never here.
        launcher.verify_current_branch_allowed("main")


# --------------------------------------- launcher: Production non-regression ---


def _executable_code(path: Path) -> str:
    """The file with comments and string literals removed.

    Prose *about* a forbidden construct ("sets no allowUnsandboxedCommands") is
    not the construct, so the scans below look at code only.
    """
    with path.open("rb") as handle:
        tokens = tokenize.tokenize(handle.readline)
        return " ".join(
            token.string
            for token in tokens
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )


LAUNCHER_SOURCES = (
    LAUNCHER_SCRIPT,
    PROJECT_ROOT / "src" / "claude_development_launcher.py",
)


@pytest.mark.parametrize(
    "forbidden",
    [
        "allowUnsandboxedCommands",
        "dangerouslySkipPermissions",
        "dangerously",
        "bypassPermissions",
        "chmod",
        "chown",
        "sudo",
    ],
)
def test_dev_launch_020_the_launcher_relaxes_nothing(forbidden):
    """No sandbox switch, no permission widening, no .git ownership change."""
    code = " ".join(_executable_code(path) for path in LAUNCHER_SOURCES)
    assert forbidden not in code, f"{forbidden!r} must not appear in code"


@pytest.mark.parametrize(
    "forbidden", ["write_text", "write_bytes", "mkdir", "unlink", "shutil"]
)
def test_dev_launch_021_the_launcher_writes_no_file_at_all(forbidden):
    code = " ".join(_executable_code(path) for path in LAUNCHER_SOURCES)
    assert forbidden not in code, f"{forbidden!r} must not appear in code"


def test_dev_launch_022_a_dry_run_leaves_development_settings_byte_identical():
    before = hashlib.sha256(DEV_SETTINGS.read_bytes()).hexdigest()
    subprocess.run(
        [sys.executable, "-B", str(LAUNCHER_SCRIPT), "--dry-run"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        check=False,
    )
    assert hashlib.sha256(DEV_SETTINGS.read_bytes()).hexdigest() == before


def test_dev_launch_023_the_development_launcher_is_not_the_production_launcher():
    development = LAUNCHER_SCRIPT.read_text(encoding="utf-8")
    production = PRODUCTION_SCRIPT.read_text(encoding="utf-8")
    assert "DEVELOPMENT-ONLY" in development
    assert "claude_production_launcher" not in development
    # The Production entry point was not taught about the Development one.
    assert "claude_development_launcher" not in production
    assert "claude-development" not in production


def test_dev_launch_024_raw_git_push_stays_denied_in_development_settings():
    settings = json.loads(DEV_SETTINGS.read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    for rule in ("Bash(git push:*)", "Bash(git fetch:*)", "Bash(git pull:*)"):
        assert rule in deny
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["allowUnsandboxedCommands"] is False


# ---------------------------------------------------------- documentation ---


def test_dev_doc_001_the_workflow_document_states_the_official_launch_order():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "scripts/claude-development",
        "git restore --staged -- <path>",
        "git commit",
        "scripts/claude-safe-push",
    ):
        assert required in text, f"{required!r} is not documented"


def test_dev_doc_002_the_two_launchers_are_documented_as_separate():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    assert "scripts/claude-production" in text
    assert "完全に別物" in text


def test_dev_doc_003_claude_md_documents_the_development_launcher():
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "daytrade-sbi/scripts/claude-development" in text
    assert "git restore --staged -- <path>" in text


# ------------------------------- documentation: repository-root-relative paths ---
#
# Development Claude Code's cwd is the repository root, so a documented command
# must be runnable from there. ``scripts/claude-safe-push`` is not: it resolves
# only from ``daytrade-sbi/``.

WRAPPER_COMMANDS = (
    "daytrade-sbi/scripts/claude-development",
    "daytrade-sbi/scripts/claude-safe-sync-main",
    "daytrade-sbi/scripts/claude-safe-start",
    "daytrade-sbi/scripts/claude-safe-push",
)

#: A wrapper path written without the ``daytrade-sbi/`` prefix.
_BARE_WRAPPER = re.compile(
    r"(?<!daytrade-sbi/)scripts/claude-(?:development|safe-sync-main|safe-start|safe-push)"
)


@pytest.mark.parametrize("command", WRAPPER_COMMANDS)
def test_dev_doc_010_claude_md_documents_wrappers_from_the_repository_root(command):
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert command in text, f"{command!r} is not documented"


@pytest.mark.parametrize("command", WRAPPER_COMMANDS)
def test_dev_doc_011_every_documented_wrapper_exists_and_is_executable(command):
    path = REPO_ROOT / command
    assert path.is_file(), f"{command!r} is documented but does not exist"
    assert os.access(path, os.X_OK), f"{command!r} is documented but not executable"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(REPO_ROOT / "CLAUDE.md", id="DEV-DOC-012"),
        pytest.param(
            PROJECT_ROOT / "docs" / "development-workflow.md", id="DEV-DOC-013"
        ),
    ],
)
def test_no_wrapper_is_documented_as_a_repository_root_command_without_its_prefix(
    document,
):
    text = document.read_text(encoding="utf-8")
    bare = sorted({match.group(0) for match in _BARE_WRAPPER.finditer(text)})
    assert not bare, f"{document.name} documents {bare} without 'daytrade-sbi/'"


def test_dev_doc_014_the_safe_push_command_runs_from_the_repository_root():
    """The one wrapper Claude itself runs: pinned as a real path from the root."""
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "daytrade-sbi/scripts/claude-safe-push"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (REPO_ROOT / "daytrade-sbi" / "scripts" / "claude-safe-push").is_file()


# ---------------------------- documentation: what the launcher does to Production ---


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(REPO_ROOT / "CLAUDE.md", id="DEV-DOC-020"),
        pytest.param(
            PROJECT_ROOT / "docs" / "development-workflow.md", id="DEV-DOC-021"
        ),
    ],
)
def test_the_documents_do_not_claim_production_assets_are_never_read(document):
    """The launcher *reads* the marker and the Managed Policy.

    Claiming it never looks at them is simply false; what must hold is that it
    writes to neither.
    """
    text = document.read_text(encoding="utf-8")
    assert "参照も変更もしない" not in text
    assert "一切参照・変更しない" not in text
    assert "read-only" in text


@pytest.mark.parametrize(
    "required",
    [
        # installed Production state: never touched from Development.
        "installed OS Managed PolicyをDevelopment Claudeがdeploy・変更しない",
        "Production Runtime Guard",
        # repository-side source: changeable only inside an explicitly
        # authorised Work Order, and never by self-authorisation.
        "Production Security Boundary Changeを明示認可",
        "自己許可してはならない",
    ],
)
def test_dev_doc_022_claude_md_states_the_production_asset_contract(required):
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert required in text


def test_dev_doc_022b_claude_md_separates_installed_state_from_repository_source():
    """The old wording did not say which "Managed Policy" it meant.

    Deploying to /etc and editing the template in this repository are different
    acts with different authorities, and conflating them made every legitimate
    policy evolution look forbidden.
    """
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "installed Production state" in text
    assert "repository-side Production Security source" in text
    assert "installed Production stateへの" in text
    assert "Human-only" in text


def test_dev_doc_023_the_launcher_only_reads_the_production_assets():
    """Both Production paths appear in the code as existence checks, nothing more."""
    source = (PROJECT_ROOT / "src" / "claude_development_launcher.py").read_text(
        encoding="utf-8"
    )
    assert "PRODUCTION_MARKER_PATH" in source
    assert "MANAGED_SETTINGS_PATH" in source
    code = _executable_code(PROJECT_ROOT / "src" / "claude_development_launcher.py")
    for writer in ("write_text", "write_bytes", "unlink", "rename", "touch"):
        assert writer not in code


def test_dev_doc_024_the_workflow_document_states_the_branch_contract():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    assert "CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED" in text
    assert "detached HEAD" in text


def test_dev_doc_025_the_workflow_document_states_the_commit_contract():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "git commit --amend",
        "git commit -a",
        "git commit <path>",
        "git commit -F -",
    ):
        assert required in text, f"{required!r} is not documented as refused"


def test_dev_doc_026_the_workflow_document_states_the_add_and_branch_contracts():
    text = (PROJECT_ROOT / "docs" / "development-workflow.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "git add -- <explicit-path>",
        "git add -A",
        "git add .",
        "git branch --show-current",
        "git branch -D",
        "claude-safe-start",
    ):
        assert required in text, f"{required!r} is not documented"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(REPO_ROOT / "CLAUDE.md", id="DEV-DOC-030"),
        pytest.param(
            PROJECT_ROOT / "docs" / "development-workflow.md", id="DEV-DOC-031"
        ),
    ],
)
def test_both_documents_fix_the_canonical_raw_git_execution_context(document):
    """FIX-DEV-GIT-011 is a contract, so it has to be written down as one."""
    text = document.read_text(encoding="utf-8")
    for required in (
        "1 Bash call = 1 direct git command",
        "git -C <dir>",
        "--work-tree",
        "--git-dir",
        "GIT_WORK_TREE=/etc git add -- passwd",
        "GIT_DIR=/tmp/x git status",
        "cd daytrade-sbi && git add --",
        'bash -c "git add -- ',
        "CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
    ):
        assert required in text, f"{required!r} is not documented in {document.name}"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(REPO_ROOT / "CLAUDE.md", id="DEV-DOC-032"),
        pytest.param(
            PROJECT_ROOT / "docs" / "development-workflow.md", id="DEV-DOC-033"
        ),
    ],
)
def test_both_documents_state_the_untracked_nonexistent_path_refusal(document):
    """FIX-DEV-GIT-012: zero index entries is a refusal, not a pass."""
    text = document.read_text(encoding="utf-8")
    assert "git ls-files" in text
    assert "0件" in text, f"{document.name} does not state the zero-entry refusal"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(REPO_ROOT / "CLAUDE.md", id="DEV-DOC-027"),
        pytest.param(
            PROJECT_ROOT / "docs" / "development-workflow.md", id="DEV-DOC-028"
        ),
    ],
)
def test_the_official_step_order_spells_out_both_git_commands(document):
    """The documented steps must be the allowed shapes, not the bare commands.

    A procedure that says "git add" and "git commit" reads as an instruction to
    run exactly that, and both bare forms are now refused.
    """
    text = document.read_text(encoding="utf-8")
    assert "git add -- " in text
    assert 'git commit -m "' in text
    # A step is a line of its own in these documents (a flow block entry, or a
    # numbered item). None of them may hand over a refused shape.
    refused = {"git add", "git commit", "git add .", "git add -A", "→ git add"}
    for line in text.splitlines():
        stripped = line.strip().lstrip("→ ").strip()
        assert stripped not in refused, f"{document.name} lists a refused step: {line}"


# --------------------------------------------------------- network_guard ----


def _guard_verdict(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, "-B", str(NETWORK_GUARD)],
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
        # Pathspec operands are judged relative to the repository root, exactly
        # as Claude Code exports it; without this the guard would resolve them
        # against whatever directory pytest happens to run from.
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )


@pytest.mark.parametrize(
    "command",
    [
        # The local loop the Development workflow is made of.
        pytest.param("git status", id="DEV-GIT-001"),
        pytest.param("git diff", id="DEV-GIT-002"),
        pytest.param("git diff --check", id="DEV-GIT-003"),
        pytest.param("git add -- daytrade-sbi/src/cli.py", id="DEV-GIT-004"),
        pytest.param(
            "git add -- daytrade-sbi/src/cli.py daytrade-sbi/docs/development-workflow.md",
            id="DEV-GIT-004a",
        ),
        # Inspecting where we are is not changing where we are.
        pytest.param("git branch --show-current", id="DEV-GIT-004b"),
        pytest.param("git branch --list", id="DEV-GIT-004c"),
        pytest.param('git commit -m "normal message"', id="DEV-GIT-005"),
        # The message that started this: "git metadata" was read as a raw git
        # subcommand named "metadata".
        pytest.param(
            'git commit -m "chore: development git metadata acceptance"',
            id="DEV-GIT-006",
        ),
        # A commit message is data even when it names a forbidden command.
        pytest.param('git commit -m "fix git push regression"', id="DEV-GIT-007"),
        pytest.param('git commit -m "note about git fetch"', id="DEV-GIT-008"),
        pytest.param("git commit -m 'git clone notes'", id="DEV-GIT-009"),
        # Was `-am` here before FIX-DEV-GIT-002; the message is still data, but
        # the flag that stages everything is not allowed to carry it.
        pytest.param('git commit -m "wip: git push retry"', id="DEV-GIT-010"),
        pytest.param('git commit --message="git ls-remote note"', id="DEV-GIT-011"),
        pytest.param('git commit --message "separate token"', id="DEV-GIT-011a"),
        # The message glued to its flag.
        pytest.param("git commit -mglued", id="DEV-GIT-012b"),
        # The unstage form this change exists to allow.
        pytest.param(
            "git restore --staged -- daytrade-sbi/src/cli.py", id="DEV-GIT-013"
        ),
        pytest.param(
            "git restore --staged -- daytrade-sbi/src/cli.py CLAUDE.md",
            id="DEV-GIT-014",
        ),
        # FIX-DEV-GIT-008: tightening the pathspec rules must not cost us the
        # ordinary staging the workflow is made of -- a file at the repository
        # root, a file in a subdirectory, several files at once, and a tracked
        # file that no longer exists on disk because it was deleted.
        pytest.param("git add -- CLAUDE.md", id="DEV-GIT-120"),
        pytest.param("git add -- daytrade-sbi/docs/development-workflow.md", id="DEV-GIT-121"),
        pytest.param(
            "git add -- CLAUDE.md daytrade-sbi/src/cli.py", id="DEV-GIT-122"
        ),
        pytest.param("git restore --staged -- CLAUDE.md", id="DEV-GIT-123"),
        pytest.param('git commit -m "normal message"', id="DEV-GIT-124"),
        pytest.param('git commit -m "fix git push regression"', id="DEV-GIT-125"),
        pytest.param("scripts/claude-safe-push", id="DEV-GIT-015"),
        pytest.param("scripts/claude-development", id="DEV-GIT-016"),
    ],
)
def test_the_guard_allows_the_local_development_git_loop(command):
    result = _guard_verdict(command)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        # restore: anything that could write the working tree.
        pytest.param("git restore daytrade-sbi/src/cli.py", id="DEV-GIT-030"),
        pytest.param("git restore --worktree -- src/cli.py", id="DEV-GIT-031"),
        pytest.param("git restore -W src/cli.py", id="DEV-GIT-032"),
        pytest.param("git restore --staged --worktree -- src/cli.py", id="DEV-GIT-033"),
        pytest.param("git restore -S -W -- src/cli.py", id="DEV-GIT-034"),
        # Ambiguous shapes fail closed rather than being guessed at.
        pytest.param("git restore --staged src/cli.py", id="DEV-GIT-035"),
        pytest.param("git restore --staged --", id="DEV-GIT-036"),
        pytest.param("git restore --staged .", id="DEV-GIT-037"),
        pytest.param(
            "git restore --staged --source=HEAD~1 -- src/cli.py", id="DEV-GIT-038"
        ),
        pytest.param("git restore --staged --patch -- src/cli.py", id="DEV-GIT-039"),
        # add: anything that stages more than the paths written in the command.
        pytest.param("git add .", id="DEV-GIT-080"),
        pytest.param("git add -A", id="DEV-GIT-081"),
        pytest.param("git add --all", id="DEV-GIT-082"),
        pytest.param("git add -u", id="DEV-GIT-083"),
        pytest.param("git add --update", id="DEV-GIT-084"),
        pytest.param("git add -p", id="DEV-GIT-085"),
        pytest.param("git add --patch", id="DEV-GIT-086"),
        pytest.param("git add -i", id="DEV-GIT-087"),
        pytest.param("git add --interactive", id="DEV-GIT-088"),
        pytest.param("git add --intent-to-add src/cli.py", id="DEV-GIT-089"),
        pytest.param("git add -N src/cli.py", id="DEV-GIT-090"),
        pytest.param("git add *", id="DEV-GIT-091"),
        # No '--' separator: ambiguous, so it fails closed.
        pytest.param("git add src/cli.py", id="DEV-GIT-092"),
        pytest.param("git add -A -- src/cli.py", id="DEV-GIT-093"),
        pytest.param("git add --", id="DEV-GIT-094"),
        # A whole tree or a glob is not an explicit path, '--' or not.
        pytest.param("git add -- .", id="DEV-GIT-095"),
        pytest.param("git add -- *", id="DEV-GIT-096"),
        pytest.param("git add -- daytrade-sbi/src/*.py", id="DEV-GIT-097"),
        # FIX-DEV-GIT-008: a pathspec is a language, not a file name. ':/' is the
        # whole repository, ':' is the current prefix, and '(top)'/'(exclude)'
        # are magic prefixes -- none of them name one explicit file.
        pytest.param("git add -- :/", id="DEV-GIT-130"),
        pytest.param("git add -- :", id="DEV-GIT-131"),
        pytest.param("git add -- :(top)", id="DEV-GIT-132"),
        pytest.param("git add -- :(exclude)foo", id="DEV-GIT-133"),
        pytest.param("git add -- :!foo", id="DEV-GIT-134"),
        pytest.param("git add -- :(glob)**/*.py", id="DEV-GIT-135"),
        # Path normalisation widens an operand just as far as magic does.
        pytest.param("git add -- src/..", id="DEV-GIT-136"),
        pytest.param("git add -- src/../", id="DEV-GIT-137"),
        pytest.param("git add -- src/.", id="DEV-GIT-138"),
        pytest.param("git add -- ../outside.txt", id="DEV-GIT-139"),
        pytest.param("git add -- /etc/passwd", id="DEV-GIT-140"),
        pytest.param("git add -- daytrade-sbi/../CLAUDE.md", id="DEV-GIT-141"),
        # A directory stages a whole tree, so the contract is file-by-file.
        pytest.param("git add -- daytrade-sbi", id="DEV-GIT-142"),
        pytest.param("git add -- daytrade-sbi/", id="DEV-GIT-143"),
        pytest.param("git add -- daytrade-sbi/src", id="DEV-GIT-144"),
        # The same validator guards the unstage form.
        pytest.param("git restore --staged -- :/", id="DEV-GIT-145"),
        pytest.param("git restore --staged -- :", id="DEV-GIT-146"),
        pytest.param("git restore --staged -- :(top)", id="DEV-GIT-147"),
        pytest.param("git restore --staged -- src/..", id="DEV-GIT-148"),
        pytest.param("git restore --staged -- /etc/passwd", id="DEV-GIT-149"),
        pytest.param("git restore --staged -- daytrade-sbi", id="DEV-GIT-150"),
        # FIX-DEV-GIT-009: the shell rewrites these after the guard has read
        # them, so the inspected argument is not the one git receives. Reading
        # inside the substitution and allowing a harmless-looking body does not
        # work -- printf and cat contain nothing forbidden and still produce a
        # pathspec and a file's contents.
        pytest.param("git add -- \"$(printf ':/' )\"", id="DEV-GIT-160"),
        pytest.param('git add -- "$PATH"', id="DEV-GIT-161"),
        pytest.param('git add -- "${HOME}/x"', id="DEV-GIT-162"),
        pytest.param("git add -- ~/x", id="DEV-GIT-163"),
        pytest.param('git commit -m "$(cat /etc/passwd)"', id="DEV-GIT-164"),
        pytest.param('git commit -m "$HOME"', id="DEV-GIT-165"),
        pytest.param("git commit -m *", id="DEV-GIT-166"),
        pytest.param("git commit -m `cat file`", id="DEV-GIT-167"),
        pytest.param('git commit --message="$(id)"', id="DEV-GIT-168"),
        # FIX-DEV-GIT-010: -F/--file reads the message out of any readable file.
        pytest.param("git commit -F file", id="DEV-GIT-170"),
        pytest.param("git commit -F /etc/passwd", id="DEV-GIT-171"),
        pytest.param("git commit --file=file", id="DEV-GIT-172"),
        pytest.param("git commit --file=/absolute/path", id="DEV-GIT-173"),
        pytest.param("git commit --file file", id="DEV-GIT-174"),
        pytest.param("git commit -F .git/COMMIT_EDITMSG", id="DEV-GIT-012"),
        pytest.param("git commit --file=.git/COMMIT_EDITMSG", id="DEV-GIT-012a"),
        # branch: inspection only. Every writing form belongs to Safe Start.
        pytest.param("git branch new", id="DEV-GIT-100"),
        pytest.param("git branch new-branch", id="DEV-GIT-101"),
        pytest.param("git branch -d old", id="DEV-GIT-102"),
        pytest.param("git branch -D old", id="DEV-GIT-103"),
        pytest.param("git branch -f main HEAD", id="DEV-GIT-104"),
        pytest.param("git branch --force main HEAD", id="DEV-GIT-105"),
        pytest.param("git branch -m old new", id="DEV-GIT-106"),
        pytest.param("git branch -M old new", id="DEV-GIT-107"),
        pytest.param("git branch --move old new", id="DEV-GIT-108"),
        pytest.param("git branch --copy old new", id="DEV-GIT-109"),
        pytest.param("git branch -c old new", id="DEV-GIT-110"),
        pytest.param("git branch -C old new", id="DEV-GIT-111"),
        # Ambiguous shapes fail closed rather than being guessed at.
        pytest.param("git branch", id="DEV-GIT-112"),
        pytest.param("git branch --list claude/x", id="DEV-GIT-113"),
        pytest.param("git branch --set-upstream-to=origin/main", id="DEV-GIT-114"),
        # commit: anything that stages more than the index already holds, or
        # rewrites a commit that already exists.
        pytest.param('git commit -am "message"', id="DEV-GIT-060"),
        pytest.param('git commit -a -m "message"', id="DEV-GIT-061"),
        pytest.param('git commit --all -m "message"', id="DEV-GIT-062"),
        pytest.param('git commit --amend -m "message"', id="DEV-GIT-063"),
        pytest.param("git commit --amend --no-edit", id="DEV-GIT-064"),
        pytest.param("git commit --fixup=HEAD", id="DEV-GIT-065"),
        pytest.param("git commit --squash=HEAD", id="DEV-GIT-066"),
        pytest.param("git commit -- src/cli.py", id="DEV-GIT-067"),
        pytest.param("git commit src/cli.py", id="DEV-GIT-068"),
        pytest.param('git commit --only -m "m" -- src/cli.py', id="DEV-GIT-069"),
        pytest.param('git commit -o -m "m" src/cli.py', id="DEV-GIT-070"),
        pytest.param('git commit --include -m "m" src/cli.py', id="DEV-GIT-071"),
        pytest.param('git commit -i -m "m" src/cli.py', id="DEV-GIT-072"),
        # Not needed by the workflow, so not allowlisted: unknown options fail
        # closed rather than being assumed harmless.
        pytest.param('git commit --allow-empty -m "m"', id="DEV-GIT-073"),
        pytest.param('git commit --no-verify -m "m"', id="DEV-GIT-074"),
        pytest.param("git commit -C HEAD", id="DEV-GIT-075"),
        pytest.param("git-commit --amend", id="DEV-GIT-076"),
        # A message the session cannot supply: the editor, and stdin.
        pytest.param("git commit", id="DEV-GIT-077"),
        pytest.param("git commit -F -", id="DEV-GIT-078"),
        pytest.param("git commit --file=-", id="DEV-GIT-078a"),
        pytest.param("git commit -m", id="DEV-GIT-079"),
        # The network paths stay shut.
        pytest.param("git push", id="DEV-GIT-040"),
        pytest.param("git push origin claude/example", id="DEV-GIT-041"),
        pytest.param("/usr/bin/git push", id="DEV-GIT-042"),
        pytest.param("git fetch", id="DEV-GIT-043"),
        pytest.param("git pull", id="DEV-GIT-044"),
        pytest.param("git clone https://github.com/Kinagaki-525/DayTrade.git", id="DEV-GIT-045"),
        pytest.param("git ls-remote origin", id="DEV-GIT-046"),
        pytest.param("git -c alias.ship=push ship origin claude/x", id="DEV-GIT-047"),
        pytest.param("git some-unknown-command", id="DEV-GIT-048"),
        # A shell -c operand really is a command, and is still re-parsed.
        pytest.param('bash -c "git push origin claude/x"', id="DEV-GIT-049"),
        pytest.param('sh -c "git fetch origin"', id="DEV-GIT-050"),
        pytest.param("/bin/bash -lc 'git push'", id="DEV-GIT-051"),
        pytest.param('sh -c "curl https://evil.example.com"', id="DEV-GIT-052"),
        pytest.param('bash -c "git ship origin claude/x"', id="DEV-GIT-053"),
        # So is a command substitution, wherever it appears -- a commit message
        # included, because the shell expands it before git ever sees it.
        pytest.param('git commit -m "$(git push origin claude/x)"', id="DEV-GIT-054"),
        pytest.param("git commit -m `git push`", id="DEV-GIT-055"),
        pytest.param('git add "$(curl https://evil.example.com)"', id="DEV-GIT-056"),
        # Chaining without spaces is not a way past the parse.
        pytest.param("git status|git push", id="DEV-GIT-057"),
        pytest.param("git status && git push origin main", id="DEV-GIT-058"),
        # FIX-DEV-GIT-011: a global option can move the repository, the work
        # tree or the directory operands are resolved in, so the path this
        # guard validated is not the path git would write.
        pytest.param("git -C /tmp status", id="DEV-GIT-180"),
        pytest.param("git -C /tmp add -- file", id="DEV-GIT-181"),
        pytest.param("git --git-dir=/tmp/x status", id="DEV-GIT-182"),
        pytest.param("git --git-dir /tmp/x status", id="DEV-GIT-182a"),
        pytest.param(
            "git --git-dir=/home/daytrade/DayTrade/.git --work-tree=/etc "
            "add -- passwd",
            id="DEV-GIT-183",
        ),
        pytest.param("git --work-tree=/etc add -- passwd", id="DEV-GIT-184"),
        pytest.param("git --work-tree /etc add -- passwd", id="DEV-GIT-184a"),
        pytest.param("git --namespace=x status", id="DEV-GIT-185"),
        pytest.param("git --exec-path=/tmp status", id="DEV-GIT-186"),
        pytest.param("git --super-prefix=x status", id="DEV-GIT-187"),
        pytest.param("git --attr-source=HEAD status", id="DEV-GIT-188"),
        # The canonical form has no global option at all, so even the harmless
        # ones fail closed rather than being carved out one by one.
        pytest.param("git --no-pager diff", id="DEV-GIT-189"),
        pytest.param("git --literal-pathspecs add -- CLAUDE.md", id="DEV-GIT-190"),
        # The same context, moved from outside git: an environment assignment,
        # `env`, or a `cd` in front of the command.
        pytest.param("GIT_WORK_TREE=/etc git add -- passwd", id="DEV-GIT-191"),
        pytest.param("GIT_DIR=/tmp/x git status", id="DEV-GIT-192"),
        pytest.param("GIT_INDEX_FILE=/tmp/i git add -- CLAUDE.md", id="DEV-GIT-193"),
        pytest.param("env GIT_WORK_TREE=/etc git add -- passwd", id="DEV-GIT-194"),
        pytest.param("cd daytrade-sbi && git add -- src/cli.py", id="DEV-GIT-195"),
        pytest.param("cd /tmp && git status", id="DEV-GIT-196"),
        pytest.param("cd daytrade-sbi && git commit -m \"m\"", id="DEV-GIT-197"),
        # A shell -c string is not a direct git command, whatever it contains.
        pytest.param('bash -c "git add -- CLAUDE.md"', id="DEV-GIT-198"),
        pytest.param('sh -c "git commit -m message"', id="DEV-GIT-199"),
        pytest.param('bash -c "git status"', id="DEV-GIT-200"),
        # The split executable is not the canonical `git <subcommand>` form.
        pytest.param("git-add -- CLAUDE.md", id="DEV-GIT-201"),
        pytest.param("/usr/lib/git-core/git-status", id="DEV-GIT-202"),
        # FIX-DEV-GIT-012: neither on disk nor in the index is not one file.
        pytest.param(
            "git add -- definitely-not-existent-development-file",
            id="DEV-GIT-210",
        ),
        pytest.param(
            "git restore --staged -- definitely-not-existent-development-file",
            id="DEV-GIT-211",
        ),
        pytest.param(
            "git add -- CLAUDE.md definitely-not-existent-development-file",
            id="DEV-GIT-212",
        ),
    ],
)
def test_the_guard_denies_every_non_local_git_form(command):
    result = _guard_verdict(command)
    assert result.returncode == 2, f"guard allowed: {command}"
    assert b"network_guard" in result.stderr


# ------------------------------------------ FIX-DEV-GIT-008: deleted files --


def _guard_verdict_in(project_dir, command: str) -> subprocess.CompletedProcess:
    """The hook as Claude Code runs it, with pathspecs resolved against a repo."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, "-B", str(NETWORK_GUARD)],
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)},
    )


@pytest.fixture
def repo_with_a_deleted_file(tmp_path):
    """A real repository where a tracked file and a tracked tree are gone."""
    work = tmp_path / "repo"
    (work / "pkg").mkdir(parents=True)
    (work / "kept.md").write_text("kept\n", encoding="utf-8")
    (work / "gone.md").write_text("gone\n", encoding="utf-8")
    (work / "pkg" / "a.py").write_text("a\n", encoding="utf-8")
    (work / "pkg" / "b.py").write_text("b\n", encoding="utf-8")
    for argv in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "seed"],
    ):
        subprocess.run(["git", "-C", str(work), *argv], check=True)
    (work / "gone.md").unlink()
    for name in ("a.py", "b.py"):
        (work / "pkg" / name).unlink()
    (work / "pkg").rmdir()
    return work


def test_dev_git_008_a_deleted_tracked_file_can_still_be_staged(
    repo_with_a_deleted_file,
):
    """"Not on disk" is not a refusal: staging a deletion is ordinary work."""
    result = _guard_verdict_in(repo_with_a_deleted_file, "git add -- gone.md")
    assert result.returncode == 0, result.stderr
    unstage = _guard_verdict_in(
        repo_with_a_deleted_file, "git restore --staged -- gone.md"
    )
    assert unstage.returncode == 0, unstage.stderr


def test_dev_git_008_a_deleted_tracked_directory_is_still_a_whole_tree(
    repo_with_a_deleted_file,
):
    """A directory that was deleted wholesale is not on disk to be recognised.

    Only the index can tell it apart from a deleted file, which is why the
    guard consults ``git ls-files`` rather than the filesystem alone.
    """
    result = _guard_verdict_in(repo_with_a_deleted_file, "git add -- pkg")
    assert result.returncode == 2, "a deleted tree was staged as if it were a file"


def test_dev_git_012_an_untracked_nonexistent_path_is_refused(
    repo_with_a_deleted_file,
):
    """Zero index entries and nothing on disk is not "one explicit file".

    Pinned against a real repository, next to the deleted-file acceptance it
    must not weaken: the two cases differ only in what ``git ls-files`` answers.
    """
    for command in (
        "git add -- never-existed.md",
        "git add -- pkg/never-existed.py",
        "git restore --staged -- never-existed.md",
        "git add -- kept.md never-existed.md",
    ):
        result = _guard_verdict_in(repo_with_a_deleted_file, command)
        assert result.returncode == 2, f"guard allowed: {command}"

    # ...while the file that is really there stays stageable.
    allowed = _guard_verdict_in(repo_with_a_deleted_file, "git add -- kept.md")
    assert allowed.returncode == 0, allowed.stderr


# --------------------------------------- FIX-DEV-GIT-013: staged deletions --


def _git(repo, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True,
        check=True,
        text=True,
    )


def test_dev_git_013_a_staged_deletion_can_be_unstaged(repo_with_a_deleted_file):
    """The whole round trip, driven through real git rather than a mock.

    Staging a deletion takes the entry *out* of the index, so the index check
    that makes ``git add -- <deleted file>`` work says "zero entries" the moment
    it has worked. Unstaging is part of the same contract, so it must survive
    that state -- which only HEAD can attest to.
    """
    repo = repo_with_a_deleted_file

    staging = _guard_verdict_in(repo, "git add -- gone.md")
    assert staging.returncode == 0, staging.stderr
    _git(repo, "add", "--", "gone.md")

    tracked = _git(repo, "ls-files", "--", "gone.md")
    assert tracked.stdout == "", "the staged deletion is still in the index"

    unstage = _guard_verdict_in(repo, "git restore --staged -- gone.md")
    assert unstage.returncode == 0, unstage.stderr
    _git(repo, "restore", "--staged", "--", "gone.md")

    status = _git(repo, "status", "--short", "--", "gone.md").stdout
    assert status == " D gone.md\n", f"deletion did not return to unstaged: {status!r}"


def test_dev_git_013_the_head_fallback_is_still_one_explicit_file(
    repo_with_a_deleted_file,
):
    """HEAD widens *which* files are reachable, never how many an operand names."""
    repo = repo_with_a_deleted_file
    _git(repo, "rm", "-q", "-r", "--", "pkg")  # the deleted tree, staged

    assert _git(repo, "ls-files", "--", "pkg").stdout == ""
    assert _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", "pkg").stdout == (
        "pkg/a.py\npkg/b.py\n"
    )

    for command in (
        "git restore --staged -- pkg",  # several entries in HEAD: a whole tree
        "git restore --staged -- never-existed.md",  # in neither index nor HEAD
        "git restore --staged -- pkg/never-existed.py",
        "git restore --staged -- :/",  # pathspec magic, ahead of any lookup
        "git restore --staged -- pkg/",
        "git restore --staged -- pkg/../gone.md",
    ):
        result = _guard_verdict_in(repo, command)
        assert result.returncode == 2, f"guard allowed: {command}"

    # ...while a single file that only HEAD still knows about is unstageable.
    allowed = _guard_verdict_in(repo, "git restore --staged -- pkg/a.py")
    assert allowed.returncode == 0, allowed.stderr


def test_dev_git_013_git_add_keeps_judging_against_the_index(
    repo_with_a_deleted_file,
):
    """The HEAD fallback belongs to the unstage only.

    ``git add`` stages against the index; letting HEAD vouch for its operands
    would accept a path that the index has nothing to say about.
    """
    repo = repo_with_a_deleted_file
    _git(repo, "add", "--", "gone.md")

    result = _guard_verdict_in(repo, "git add -- gone.md")
    assert result.returncode == 2, "git add accepted a path only HEAD knows about"
