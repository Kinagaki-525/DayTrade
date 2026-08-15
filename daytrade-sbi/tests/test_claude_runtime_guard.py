"""FIX-R2-004: the OS-managed production runtime guard.

The guard is the last line of the production security boundary, so it is
tested the way it actually runs: as a separate process fed a hook payload on
stdin, with the production environment reproduced inside a tmp directory.

Nothing here touches /etc, starts Claude Code, or opens a socket.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
GUARD = PROJECT_ROOT / "ops" / "claude" / "daytrade_runtime_guard.py"

TARGET_DATE = "2026-08-14"


# ------------------------------------------------------------- fixtures ---


@pytest.fixture()
def production(tmp_path):
    """An absolute-path reproduction of a production runtime environment."""
    daytrade_root = tmp_path / "daytrade-sbi"
    run_dir = daytrade_root / "runs" / TARGET_DATE
    (run_dir / "working").mkdir(parents=True)
    (daytrade_root / "config").mkdir(parents=True)
    (daytrade_root / "config" / "source_matrix_registry").mkdir()
    (daytrade_root / "config" / "strategy.yaml").write_text("x: 1", encoding="utf-8")
    (daytrade_root / "config" / "source_matrix.yaml").write_text("x: 1", encoding="utf-8")
    (daytrade_root / "runs").mkdir(exist_ok=True)
    python_path = str(Path(sys.executable).resolve())
    return {
        "daytrade_root": daytrade_root,
        "run_dir": run_dir,
        "python": python_path,
        "env": {
            "DAYTRADE_RUNTIME_PROFILE": "production",
            "DAYTRADE_PROJECT_ROOT": str(tmp_path),
            "DAYTRADE_ROOT": str(daytrade_root),
            "DAYTRADE_RUN_DIR": str(run_dir),
            "DAYTRADE_TARGET_DATE": TARGET_DATE,
            "DAYTRADE_PRODUCTION_PYTHON": python_path,
            "DAYTRADE_HTTP_USER_AGENT": "daytrade-sbi/1.0",
            "PATH": "/usr/bin:/bin",
        },
    }


def _invoke(production, payload, *, raw: bytes | None = None) -> subprocess.CompletedProcess:
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    return subprocess.run(
        [sys.executable, "-I", "-B", str(GUARD)],
        input=data,
        capture_output=True,
        check=False,
        env=production["env"],
    )


def _bash(production, command: str) -> subprocess.CompletedProcess:
    return _invoke(
        production,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
    )


def _write(production, tool_name: str, file_path: str) -> subprocess.CompletedProcess:
    return _invoke(
        production,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
        },
    )


def _cli(production, rest: str) -> str:
    return f"{production['python']} -B -m src.cli {rest}"


def _artifact(production, name: str) -> str:
    return str(production["run_dir"] / name)


# ------------------------------------------------------------- positive ---


def _canonical_commands(production) -> dict[str, str]:
    run_dir = production["run_dir"]
    root = production["daytrade_root"]
    matrix = str(root / "config" / "source_matrix.yaml")
    snapshot = str(run_dir / "strategy_snapshot.yaml")
    sources = _artifact(production, "sources.json")
    market_data = _artifact(production, "market_data.json")
    market_research = _artifact(production, "market_research.json")
    candidates = _artifact(production, "candidates.json")
    pipeline = _artifact(production, "candidate_pipeline.json")
    event_research = _artifact(production, "event_research.json")
    event_gate = _artifact(production, "event_gate.json")
    ranking = _artifact(production, "ranking.json")
    selection = _artifact(production, "selection.json")
    window = _artifact(production, "research_window.json")
    out = _artifact(production, "working/out.json")
    dates = "--target-date 2026-08-14 --trading-date 2026-08-15 --research-cutoff 20:00:00+09:00"
    acquire = f"{dates} --run-dir {run_dir} --sources {sources} --source-matrix {matrix}"
    return {
        "snapshot-config": (
            f"snapshot-config --config {root / 'config' / 'strategy.yaml'} "
            f"--output {snapshot}"
        ),
        "validate-source-matrix": f"validate-source-matrix --source-matrix {matrix} --output {out}",
        "resolve-research-window": (
            f"resolve-research-window --target-date 2026-08-14 "
            f"--previous-trading-day 2026-08-13 --runs-dir {root / 'runs'} "
            f"--source-matrix {matrix} --output {window}"
        ),
        "acquire-discovery": f"acquire-discovery {acquire} --research-window {window}",
        "init-candidate-research": (
            f"init-candidate-research --market-research {market_research} --output {out}"
        ),
        "acquire-stage1-sources": f"acquire-stage1-sources {acquire}",
        "apply-stage1": (
            f"apply-stage1 --market-research {market_research} --market-data {market_data} "
            f"--sources {sources} --config {snapshot} --output {out}"
        ),
        "plan-stage2-batches": (
            f"plan-stage2-batches --market-research {market_research} --batch-size 8 "
            f"--output {out}"
        ),
        "acquire-stage2-market-sources": f"acquire-stage2-market-sources {acquire}",
        "acquire-actual-turnover": f"acquire-actual-turnover {acquire}",
        "validate-market": (
            f"validate-market --market-data {market_data} --sources {sources} "
            f"--source-matrix {matrix} --output {out}"
        ),
        "screen-market": (
            f"screen-market --market-data {market_data} --market-research {market_research} "
            f"--sources {sources} --source-matrix {matrix} --config {snapshot} --output {out}"
        ),
        "build-candidate-pipeline": (
            f"build-candidate-pipeline --candidates {candidates} --market-data {market_data} "
            f"--market-research {market_research} --sources {sources} --config {snapshot} "
            f"--output {pipeline}"
        ),
        "acquire-event-sources": f"acquire-event-sources {acquire}",
        "merge-event-source-extraction": (
            f"merge-event-source-extraction "
            f"--extraction {_artifact(production, 'working/event_source_extraction.json')} "
            f"--sources {sources} --run-dir {run_dir} --output {out}"
        ),
        "init-event-research": (
            f"init-event-research --candidate-pipeline {pipeline} --candidates {candidates} "
            f"--config {snapshot} --previous-trading-day 2026-08-13 --output {event_research}"
        ),
        "complete-event-research": (
            f"complete-event-research --event-research {event_research} "
            f"--extraction {_artifact(production, 'working/event_source_extraction.json')} "
            f"--sources {sources} --output {out}"
        ),
        "validate-event-research": (
            f"validate-event-research --candidate-pipeline {pipeline} --candidates {candidates} "
            f"--config {snapshot} --event-research {event_research} --sources {sources} "
            f"--output {out}"
        ),
        "build-event-gate": (
            f"build-event-gate --candidate-pipeline {pipeline} --candidates {candidates} "
            f"--config {snapshot} --event-research {event_research} --sources {sources} "
            f"--output {event_gate}"
        ),
        "build-ranking": (
            f"build-ranking --candidates {candidates} --config {snapshot} "
            f"--event-gate {event_gate} --market-data {market_data} "
            f"--source-matrix {matrix} --sources {sources} --output {ranking}"
        ),
        "build-selection": (
            f"build-selection --candidates {candidates} --config {snapshot} "
            f"--event-gate {event_gate} --market-data {market_data} --ranking {ranking} "
            f"--source-matrix {matrix} --sources {sources} --output {selection}"
        ),
        "build-ranking-terminal-recommendation": (
            f"build-ranking-terminal-recommendation --candidate-pipeline {pipeline} "
            f"--candidates {candidates} --config {snapshot} --event-gate {event_gate} "
            f"--market-data {market_data} --ranking {ranking} --research-window {window} "
            f"--source-matrix {matrix} --sources {sources} --output {out}"
        ),
        "build-selection-recommendation": (
            f"build-selection-recommendation --candidate-pipeline {pipeline} "
            f"--candidates {candidates} --config {snapshot} --event-gate {event_gate} "
            f"--market-data {market_data} --ranking {ranking} --selection {selection} "
            f"--research-window {window} --source-matrix {matrix} --sources {sources} "
            f"--output {out}"
        ),
        "risk-check": (
            f"risk-check --candidate-pipeline {pipeline} --candidates {candidates} "
            f"--config {snapshot} --event-gate {event_gate} --market-data {market_data} "
            f"--market-research {market_research} --ranking {ranking} --selection {selection} "
            f"--recommendation {_artifact(production, 'recommendation.json')} "
            f"--research-window {window} --source-matrix {matrix} --sources {sources} "
            f"--current-positions 0 --trades-today 0 --output {out}"
        ),
        "verify-production-run": (
            f"verify-production-run --run-dir {run_dir} --source-matrix {matrix} --output {out}"
        ),
        "verify-production-happy-path": (
            f"verify-production-happy-path --run-dir {run_dir} --source-matrix {matrix} "
            f"--output {out}"
        ),
    }


CANONICAL_SUBCOMMANDS = (
    "snapshot-config",
    "validate-source-matrix",
    "resolve-research-window",
    "acquire-discovery",
    "init-candidate-research",
    "acquire-stage1-sources",
    "apply-stage1",
    "plan-stage2-batches",
    "acquire-stage2-market-sources",
    "acquire-actual-turnover",
    "validate-market",
    "screen-market",
    "build-candidate-pipeline",
    "acquire-event-sources",
    "merge-event-source-extraction",
    "init-event-research",
    "complete-event-research",
    "validate-event-research",
    "build-event-gate",
    "build-ranking",
    "build-selection",
    "build-ranking-terminal-recommendation",
    "build-selection-recommendation",
    "risk-check",
    "verify-production-run",
    "verify-production-happy-path",
)


@pytest.mark.parametrize("subcommand", CANONICAL_SUBCOMMANDS)
def test_canonical_nightly_commands_are_allowed(production, subcommand):
    command = _cli(production, _canonical_commands(production)[subcommand])
    result = _bash(production, command)
    assert result.returncode == 0, result.stderr.decode()


def test_the_guard_allow_list_matches_the_real_cli(production):
    """Section 35: the approved set may not contain an invented subcommand."""
    from src import cli

    choices = set(cli.build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001
    guard = _load_guard_module()
    assert guard.APPROVED_SUBCOMMANDS <= choices
    assert guard.FORBIDDEN_SUBCOMMANDS <= choices
    assert not (guard.APPROVED_SUBCOMMANDS & guard.FORBIDDEN_SUBCOMMANDS)
    assert set(CANONICAL_SUBCOMMANDS) == guard.APPROVED_SUBCOMMANDS
    for name, flags in guard.SUBCOMMAND_FLAGS.items():
        parser = cli.build_parser()._subparsers._group_actions[0].choices[name]  # noqa: SLF001
        real = {
            option
            for action in parser._actions  # noqa: SLF001
            for option in action.option_strings
            if option not in ("-h", "--help")
        }
        assert flags == real, f"{name} flag contract drifted from src/cli.py"


def _load_guard_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("daytrade_runtime_guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_guard_imports_only_the_standard_library():
    text = GUARD.read_text(encoding="utf-8")
    assert "import subprocess" not in text
    assert "import socket" not in text
    assert "import requests" not in text
    assert "from src" not in text
    assert "import src" not in text


# ------------------------------------------------------- bash negatives ---


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com",
        "wget https://example.com/x",
        "gh pr create",
        "git fetch origin",
        "git pull",
        "git push origin main",
        "ssh user@host",
        "scp a b:c",
        "python -c 'print(1)'",
        "python custom.py",
        "python -m http.server",
        "node server.js",
        "node -e \"console.log(1)\"",
        "bash -c 'ls'",
        "sh -c 'ls'",
        "cat /etc/passwd",
        "ls",
        "pwd",
        "echo hi",
        "rm -rf /",
        "cp a b",
        "mv a b",
        "python -m pytest -q",
        "cd /tmp",
        "sudo apt-get install bubblewrap",
    ],
)
def test_every_non_canonical_bash_command_is_denied(production, command):
    result = _bash(production, command)
    assert result.returncode == 2, result.stdout
    assert b"CLAUDE_PRODUCTION_" in result.stderr


@pytest.mark.parametrize(
    "suffix",
    [
        " && curl https://example.com",
        " ; curl https://example.com",
        " | cat",
        " |& cat",
        " & ",
        " > /tmp/x",
        " >> /tmp/x",
        " < /tmp/x",
        " $(id)",
        " `id`",
        "\ncurl https://example.com",
        "\rcurl https://example.com",
        " >(cat)",
        " <(cat)",
    ],
)
def test_compound_and_redirected_commands_are_denied(production, suffix):
    base = _cli(production, _canonical_commands(production)["validate-source-matrix"])
    result = _bash(production, base + suffix)
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_BASH_DENIED" in result.stderr


def test_cd_prefixed_compound_command_is_denied(production):
    base = _cli(production, _canonical_commands(production)["validate-source-matrix"])
    result = _bash(production, f"cd {production['daytrade_root']} && {base}")
    assert result.returncode == 2


@pytest.mark.parametrize(
    "command_template",
    [
        "/usr/bin/other-python -B -m src.cli validate-source-matrix",
        "{python} -m src.cli validate-source-matrix",
        "{python} -B src.cli validate-source-matrix",
        "{python} -B -m src.other_cli validate-source-matrix",
        "{python} -B -m src.cli extra validate-source-matrix",
        "{python} -B -m src.cli totally-made-up-command",
        "{python} -m -B src.cli validate-source-matrix",
    ],
)
def test_non_canonical_python_invocations_are_denied(production, command_template):
    command = command_template.format(python=production["python"])
    result = _bash(production, command)
    assert result.returncode == 2
    assert (
        b"CLAUDE_PRODUCTION_BASH_DENIED" in result.stderr
        or b"CLAUDE_PRODUCTION_COMMAND_NOT_CANONICAL" in result.stderr
    )


@pytest.mark.parametrize(
    "subcommand",
    [
        "activate-selection-config",
        "build-selection-calibration",
        "evaluate-selection-thresholds",
        "record-execution",
        "record-recommendation",
    ],
)
def test_human_only_subcommands_are_denied(production, subcommand):
    result = _bash(production, _cli(production, subcommand))
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_COMMAND_NOT_CANONICAL" in result.stderr


def test_unknown_flag_is_denied(production):
    matrix = production["daytrade_root"] / "config" / "source_matrix.yaml"
    result = _bash(
        production,
        _cli(production, f"validate-source-matrix --source-matrix {matrix} --evil x"),
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_COMMAND_NOT_CANONICAL" in result.stderr


# ------------------------------------------------------- path negatives ---


def test_relative_output_is_denied(production):
    result = _bash(production, _cli(production, "validate-source-matrix --output ../x"))
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


@pytest.mark.parametrize("relative", ["src/x.json", "config/x.yaml"])
def test_repository_output_paths_are_denied(production, relative):
    target = production["daytrade_root"] / relative
    result = _bash(
        production, _cli(production, f"validate-source-matrix --output {target}")
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


def test_output_outside_the_run_dir_is_denied(production, tmp_path):
    result = _bash(
        production, _cli(production, f"validate-source-matrix --output {tmp_path}/x.json")
    )
    assert result.returncode == 2


def test_absolute_tmp_output_is_denied(production):
    result = _bash(
        production, _cli(production, "validate-source-matrix --output /tmp/x.json")
    )
    assert result.returncode == 2


def test_traversal_out_of_the_run_dir_is_denied(production):
    escape = production["run_dir"] / ".." / ".." / "src" / "x.json"
    result = _bash(
        production, _cli(production, f"validate-source-matrix --output {escape}")
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


def test_a_different_run_dir_is_denied(production):
    other = production["daytrade_root"] / "runs" / "2020-01-01"
    other.mkdir(parents=True)
    matrix = production["daytrade_root"] / "config" / "source_matrix.yaml"
    result = _bash(
        production,
        _cli(production, f"verify-production-run --run-dir {other} --source-matrix {matrix}"),
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


def test_sources_from_another_run_is_denied(production):
    other = production["daytrade_root"] / "runs" / "2020-01-01"
    other.mkdir(parents=True)
    matrix = production["daytrade_root"] / "config" / "source_matrix.yaml"
    result = _bash(
        production,
        _cli(
            production,
            f"validate-market --market-data {_artifact(production, 'market_data.json')} "
            f"--sources {other / 'sources.json'} --source-matrix {matrix} "
            f"--output {_artifact(production, 'x.json')}",
        ),
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


def test_live_strategy_config_may_only_be_read_by_snapshot_config(production):
    live = production["daytrade_root"] / "config" / "strategy.yaml"
    result = _bash(
        production,
        _cli(
            production,
            f"screen-market --market-data {_artifact(production, 'market_data.json')} "
            f"--market-research {_artifact(production, 'market_research.json')} "
            f"--sources {_artifact(production, 'sources.json')} --config {live} "
            f"--output {_artifact(production, 'x.json')}",
        ),
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


def test_external_source_matrix_is_denied(production, tmp_path):
    fake = tmp_path / "evil_matrix.yaml"
    fake.write_text("sources: []", encoding="utf-8")
    result = _bash(
        production, _cli(production, f"validate-source-matrix --source-matrix {fake}")
    )
    assert result.returncode == 2


def test_symlink_escape_from_the_run_dir_is_denied(production, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = production["run_dir"] / "escape"
    link.symlink_to(outside, target_is_directory=True)
    result = _bash(
        production,
        _cli(production, f"validate-source-matrix --output {link / 'x.json'}"),
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in result.stderr


# ----------------------------------------------------------- edit/write ---


def _extraction(production) -> str:
    return str(production["run_dir"] / "working" / "event_source_extraction.json")


@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_event_source_extraction_is_writable(production, tool):
    result = _write(production, tool, _extraction(production))
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize(
    "relative",
    [
        "sources.json",
        "market_data.json",
        "candidates.json",
        "candidate_pipeline.json",
        "event_gate.json",
        "ranking.json",
        "selection.json",
        "recommendation.json",
        "risk_result.json",
        "strategy_snapshot.yaml",
        "source_pages/page.html",
        "network_requests/req.json",
    ],
)
def test_business_artifacts_may_not_be_written_directly(production, relative):
    result = _write(production, "Edit", str(production["run_dir"] / relative))
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_WRITE_DENIED" in result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "daytrade-sbi/src/foo.py",
        "daytrade-sbi/config/strategy.yaml",
        "daytrade-sbi/schemas/foo.json",
        "daytrade-sbi/tests/test_foo.py",
        "daytrade-sbi/ops/claude/daytrade_runtime_guard.py",
        "daytrade-sbi/scripts/claude-production",
        ".claude/settings.json",
        "CLAUDE.md",
        "AGENTS.md",
    ],
)
def test_protected_repository_files_may_not_be_written(production, relative, tmp_path):
    result = _write(production, "Write", str(tmp_path / relative))
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_WRITE_DENIED" in result.stderr


def test_notebook_edit_is_denied(production, tmp_path):
    result = _invoke(
        production,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(tmp_path / "x.ipynb")},
        },
    )
    assert result.returncode == 2


def test_symlinked_extraction_path_is_denied(production, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = production["run_dir"] / "working2"
    link.symlink_to(outside, target_is_directory=True)
    result = _write(production, "Write", str(link / "event_source_extraction.json"))
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_WRITE_DENIED" in result.stderr


# --------------------------------------------------------- configchange ---


@pytest.mark.parametrize(
    "source", ["user_settings", "project_settings", "local_settings", "skills"]
)
def test_config_change_sources_are_blocked(production, source):
    result = _invoke(
        production, {"hook_event_name": "ConfigChange", "source": source}
    )
    assert result.returncode == 2
    assert b"CLAUDE_PRODUCTION_CONFIG_CHANGE_DENIED" in result.stderr


def test_policy_settings_change_is_audited_but_not_blocked(production):
    result = _invoke(
        production, {"hook_event_name": "ConfigChange", "source": "policy_settings"}
    )
    assert result.returncode == 0
    assert b"CLAUDE_PRODUCTION_CONFIG_CHANGE_AUDIT" in result.stderr


def test_unknown_config_change_source_is_blocked(production):
    result = _invoke(
        production, {"hook_event_name": "ConfigChange", "source": "mystery"}
    )
    assert result.returncode == 2


# ------------------------------------------------------------ fail-closed ---


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hook_event_name": "PreToolUse"},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash"},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}},
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {}},
        {"hook_event_name": "Mystery", "tool_name": "Bash", "tool_input": {}},
        {"hook_event_name": "PreToolUse", "tool_name": "WebFetch", "tool_input": {"url": "x"}},
        [1, 2, 3],
    ],
)
def test_unexpected_payload_shapes_fail_closed(production, payload):
    assert _invoke(production, payload).returncode == 2


@pytest.mark.parametrize("raw", [b"", b"   ", b"{not json"])
def test_unparseable_stdin_fails_closed(production, raw):
    assert _invoke(production, None, raw=raw).returncode == 2


def test_missing_runtime_profile_denies_everything(production):
    production["env"].pop("DAYTRADE_RUNTIME_PROFILE")
    result = _bash(
        production, _cli(production, _canonical_commands(production)["validate-source-matrix"])
    )
    assert result.returncode == 2
    assert b"CLAUDE_RUNTIME_PROFILE_MISSING" in result.stderr


def test_non_production_runtime_profile_denies_everything(production):
    production["env"]["DAYTRADE_RUNTIME_PROFILE"] = "development"
    assert _bash(production, "ls").returncode == 2


def test_denials_use_exit_code_two_not_one(production):
    result = _bash(production, "curl https://example.com")
    assert result.returncode == 2
    assert result.returncode != 1
