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


# ------------------------------------------- the guard still denies raw git --


def _guard_verdict(command: str):
    """Run the PreToolUse hook exactly as Claude Code does, over a Bash command."""
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
        pytest.param("git push", id="SAFE-PUSH-014"),
        pytest.param("git push origin main", id="SAFE-PUSH-015"),
        pytest.param("git push --force origin claude/example", id="SAFE-PUSH-016a"),
        pytest.param("git push -f", id="SAFE-PUSH-016b"),
        pytest.param("git fetch origin", id="SAFE-PUSH-016c"),
        pytest.param("git pull", id="SAFE-PUSH-016d"),
        # The executable may be spelled as a path, and global options may sit
        # between it and the subcommand. Neither may be relied on to identify
        # the command, or github.com becomes reachable around the wrapper.
        pytest.param(
            "/usr/bin/git push origin claude/example", id="SAFE-PUSH-025"
        ),
        pytest.param(
            "git -c core.askPass=true push origin claude/example",
            id="SAFE-PUSH-026",
        ),
        pytest.param(
            "/usr/bin/git -c core.askPass=true push origin claude/example",
            id="SAFE-PUSH-027",
        ),
        pytest.param(
            "git send-pack https://github.com/Kinagaki-525/DayTrade.git HEAD",
            id="SAFE-PUSH-028",
        ),
        pytest.param(
            "/usr/bin/git send-pack https://github.com/Kinagaki-525/DayTrade.git HEAD",
            id="SAFE-PUSH-029",
        ),
        pytest.param(
            "git clone https://github.com/Kinagaki-525/DayTrade.git",
            id="SAFE-PUSH-030",
        ),
        pytest.param(
            "git ls-remote https://github.com/Kinagaki-525/DayTrade.git",
            id="SAFE-PUSH-031",
        ),
        # Git ships each subcommand as its own executable too.
        pytest.param(
            "/usr/lib/git-core/git-send-pack https://github.com/x/y.git HEAD",
            id="SAFE-PUSH-029b",
        ),
        pytest.param(
            "git -c credential.helper=/tmp/x push origin claude/foo",
            id="SAFE-PUSH-026b",
        ),
        pytest.param(
            "git -C /home/daytrade/DayTrade push origin claude/foo",
            id="SAFE-PUSH-026c",
        ),
    ],
)
def test_the_network_guard_still_blocks_raw_git_network_commands(command):
    """Safe Push must not have been built by deleting the raw git push block."""
    assert _guard_verdict(command).returncode != 0, f"guard allowed: {command}"


# ------------------------------------------------ aliases defeat a blacklist --


@pytest.mark.parametrize(
    "command",
    [
        # Git lets any subcommand be renamed, so the command string need never
        # contain the word "push" for a push to happen.
        pytest.param(
            "git -c alias.ship=push ship origin claude/example", id="SAFE-PUSH-033"
        ),
        pytest.param("git config alias.ship push", id="SAFE-PUSH-034"),
        pytest.param("git config --global alias.ship push", id="SAFE-PUSH-035"),
        pytest.param("git config --local alias.ship push", id="SAFE-PUSH-035b"),
        # An alias configured earlier leaves nothing to recognise at all, which
        # is why an unknown subcommand has to be refused on sight.
        pytest.param("git ship origin claude/example", id="SAFE-PUSH-036"),
        pytest.param("git some-unknown-command", id="SAFE-PUSH-037"),
        # A safe first command does not license the second.
        pytest.param(
            "git status && git ship origin claude/example", id="SAFE-PUSH-044"
        ),
        pytest.param(
            "git diff ; git ship origin claude/example", id="SAFE-PUSH-044b"
        ),
        pytest.param(
            "git status | git ship origin claude/example", id="SAFE-PUSH-044c"
        ),
        pytest.param(
            "/usr/bin/git -c alias.ship=push ship origin claude/example",
            id="SAFE-PUSH-045",
        ),
        # An alias body can carry the push itself.
        pytest.param(
            "git config alias.ship '!git push origin'", id="SAFE-PUSH-034b"
        ),
        pytest.param("git --config-env=alias.ship=X ship", id="SAFE-PUSH-033b"),
        # A quoted argument can carry a whole command of its own.
        pytest.param(
            'bash -c "git ship origin claude/example"', id="SAFE-PUSH-044d"
        ),
        # `git remote update` fetches, so `remote` is not a local-only command.
        pytest.param("git remote update", id="SAFE-PUSH-037b"),
    ],
)
def test_the_network_guard_fails_closed_on_raw_git_aliases(command):
    """A blacklist of known network subcommands cannot see through an alias."""
    assert _guard_verdict(command).returncode != 0, f"guard allowed: {command}"


@pytest.mark.parametrize(
    "command",
    [
        # FIX-DEV-GIT-011. -C was once allowed here because it is not -c: it
        # changes a directory rather than injecting configuration. But a
        # directory is exactly what the pathspec validator resolves operands
        # against, so `-C` breaks the explicit-file contract just as thoroughly
        # as `--work-tree` does. The canonical form has no global option at all,
        # so --no-pager goes with it rather than being carved out by name.
        pytest.param("git -C /home/daytrade/DayTrade status", id="SAFE-PUSH-043a"),
        pytest.param("git --no-pager diff", id="SAFE-PUSH-043b"),
    ],
)
def test_safe_push_the_guard_fixes_the_git_execution_context(command):
    """Raw git is `git <subcommand>` from the repository root, nothing before it."""
    assert _guard_verdict(command).returncode != 0, f"guard allowed: {command}"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("scripts/claude-safe-push", id="SAFE-PUSH-032"),
        pytest.param(
            "cd daytrade-sbi && scripts/claude-safe-push", id="SAFE-PUSH-046"
        ),
        # Closing the raw git network paths must not cost us read-only git.
        pytest.param("git status --short", id="SAFE-PUSH-032a"),
        pytest.param("git log --oneline -5", id="SAFE-PUSH-032b"),
        pytest.param("git ls-files .claude", id="SAFE-PUSH-032c"),
        pytest.param("git diff --check", id="SAFE-PUSH-032d"),
        # The local-only work the allowlist exists to keep possible.
        pytest.param("git status", id="SAFE-PUSH-038"),
        pytest.param("git diff", id="SAFE-PUSH-039"),
        pytest.param("git log -1 --oneline", id="SAFE-PUSH-040"),
        pytest.param("git branch --show-current", id="SAFE-PUSH-041"),
        # Explicit paths after '--' only; see _add_reason in the guard.
        pytest.param("git add -- CLAUDE.md", id="SAFE-PUSH-042"),
        pytest.param('git commit -m "test"', id="SAFE-PUSH-043"),
        pytest.param("git rev-parse HEAD", id="SAFE-PUSH-043c"),
        # Read-only config forms cannot define an alias.
        pytest.param("git config --get remote.origin.mirror", id="SAFE-PUSH-043d"),
        pytest.param("git config --list", id="SAFE-PUSH-043e"),
        pytest.param(
            "git status --short && git log --oneline -3", id="SAFE-PUSH-043f"
        ),
    ],
)
def test_the_network_guard_lets_the_safe_push_wrapper_through(command):
    """The wrapper is not a git push string, so no guard exception was needed."""
    result = _guard_verdict(command)
    assert result.returncode == 0, result.stderr


# --------------------------------------------- Production non-regression ----


def test_safe_push_017_development_settings_allow_github_for_the_wrapper():
    """The Development sandbox needs github.com; raw git push stays denied."""
    settings = json.loads(DEV_SETTINGS.read_text(encoding="utf-8"))
    allowed = settings["sandbox"]["network"]["allowedDomains"]
    assert "github.com" in allowed, (
        "Development sandbox cannot reach GitHub, so claude-safe-push cannot work"
    )
    deny = settings["permissions"]["deny"]
    assert "Bash(git push:*)" in deny, "raw git push must stay denied"
    assert settings["sandbox"]["failIfUnavailable"] is True
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False


def test_safe_push_018_production_expected_domains_do_not_include_github():
    """Production's allowlist is derived from the matrix and issuer registry only."""
    from src.claude_runtime_security import derive_expected_domains

    domains = derive_expected_domains()
    assert domains, "the Production allowlist derivation returned nothing"
    assert "github.com" not in domains, (
        "github.com leaked into the Production expected domain set"
    )


def test_safe_push_019_production_policy_was_not_taught_about_safe_push():
    """Production denies push entirely; the wrapper must be unknown to it."""
    for path in (
        PROJECT_ROOT / "ops" / "claude" / "managed-settings.template.json",
        PROJECT_ROOT / "ops" / "claude" / "daytrade_runtime_guard.py",
        PROJECT_ROOT / "src" / "claude_runtime_security.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert "claude-safe-push" not in text, f"{path.name} references safe push"
        assert "github.com" not in text, f"{path.name} allows github.com"
