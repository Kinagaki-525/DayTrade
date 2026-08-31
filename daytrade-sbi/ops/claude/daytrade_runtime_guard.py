#!/usr/bin/env python3
"""DayTrade Production Runtime Guard (FIX-R2-004).

Canonical repository source of the OS-managed Claude Code hook that is
deployed to ``/etc/claude-code/daytrade-runtime-guard.py``.

Contract
--------
* stdlib only -- no ``src.*`` import, no third-party dependency.
* no subprocess, no socket, no HTTP, no file write.
* reads one hook payload as JSON on stdin and returns a decision:
  exit 0 = allow, exit 2 = block (exit 1 is never used).
* fail closed: anything unexpected is a denial.

The hook runs OUTSIDE the sandbox with user privileges, which is exactly why
it must stay this small and this boring.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

# --------------------------------------------------------------- codes ---

CODE_PROFILE_MISSING = "CLAUDE_RUNTIME_PROFILE_MISSING"
CODE_BASH_DENIED = "CLAUDE_PRODUCTION_BASH_DENIED"
CODE_WRITE_DENIED = "CLAUDE_PRODUCTION_WRITE_DENIED"
CODE_NOT_CANONICAL = "CLAUDE_PRODUCTION_COMMAND_NOT_CANONICAL"
CODE_PATH_OUTSIDE_RUN = "CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN"

REQUIRED_RUNTIME_PROFILE = "production"

# ------------------------------------------------------------ contract ---

#: Subcommands the Production Nightly Run is allowed to invoke. Derived from
#: ``src/cli.py`` ``build_parser()`` and ``docs/canonical-pipeline.md``; every
#: entry below exists in the CLI. Nothing is added speculatively.
APPROVED_SUBCOMMANDS = frozenset(
    {
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
        "validate-market-research",
        "validate-market",
        "audit-official-ohlcv",
        "screen-market",
        "build-candidate-pipeline",
        "build-performance",
        "render-research",
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
        "render-report",
        "render-daily-report",
        "validate-run-artifacts",
        "verify-production-run",
        "verify-production-happy-path",
    }
)

#: Human / calibration / record operations. Never part of a nightly run.
FORBIDDEN_SUBCOMMANDS = frozenset(
    {
        "activate-selection-config",
        "build-selection-calibration",
        "evaluate-selection-thresholds",
        "record-execution",
        "record-recommendation",
    }
)

#: Exact argparse contract per approved subcommand (option strings only).
SUBCOMMAND_FLAGS = {
    "snapshot-config": {"--config", "--output"},
    "validate-source-matrix": {"--output", "--source-matrix"},
    "resolve-research-window": {
        "--output",
        "--previous-trading-day",
        "--runs-dir",
        "--source-matrix",
        "--target-date",
    },
    "acquire-discovery": {
        "--output",
        "--research-cutoff",
        "--research-window",
        "--run-dir",
        "--source-matrix",
        "--sources",
        "--target-date",
        "--trading-date",
    },
    "init-candidate-research": {"--market-research", "--output"},
    "acquire-stage1-sources": {
        "--output",
        "--research-cutoff",
        "--run-dir",
        "--source-matrix",
        "--sources",
        "--target-date",
        "--trading-date",
    },
    "apply-stage1": {
        "--config",
        "--market-data",
        "--market-research",
        "--output",
        "--sources",
    },
    "plan-stage2-batches": {
        "--agent-name",
        "--batch-size",
        "--market-research",
        "--output",
    },
    "acquire-stage2-market-sources": {
        "--output",
        "--research-cutoff",
        "--run-dir",
        "--source-matrix",
        "--sources",
        "--target-date",
        "--trading-date",
    },
    "acquire-actual-turnover": {
        "--output",
        "--research-cutoff",
        "--run-dir",
        "--source-matrix",
        "--sources",
        "--target-date",
        "--trading-date",
    },
    "validate-market": {
        "--market-data",
        "--market-research",
        "--output",
        "--source-matrix",
        "--sources",
    },
    "screen-market": {
        "--config",
        "--market-data",
        "--market-research",
        "--output",
        "--source-matrix",
        "--sources",
    },
    "build-candidate-pipeline": {
        "--candidates",
        "--config",
        "--market-data",
        "--market-research",
        "--output",
        "--sources",
    },
    "acquire-event-sources": {
        "--output",
        "--research-cutoff",
        "--run-dir",
        "--source-matrix",
        "--sources",
        "--target-date",
        "--trading-date",
    },
    "merge-event-source-extraction": {
        "--extraction",
        "--output",
        "--run-dir",
        "--sources",
    },
    "init-event-research": {
        "--candidate-pipeline",
        "--candidates",
        "--config",
        "--output",
        "--previous-trading-day",
    },
    "complete-event-research": {
        "--event-gate-as-of",
        "--event-research",
        "--extraction",
        "--output",
        "--sources",
    },
    "validate-event-research": {
        "--candidate-pipeline",
        "--candidates",
        "--config",
        "--event-research",
        "--output",
        "--sources",
    },
    "build-event-gate": {
        "--candidate-pipeline",
        "--candidates",
        "--config",
        "--event-research",
        "--output",
        "--sources",
    },
    "build-ranking": {
        "--candidates",
        "--config",
        "--event-gate",
        "--market-data",
        "--output",
        "--source-matrix",
        "--sources",
    },
    "build-selection": {
        "--candidates",
        "--config",
        "--event-gate",
        "--market-data",
        "--output",
        "--ranking",
        "--source-matrix",
        "--sources",
    },
    "build-ranking-terminal-recommendation": {
        "--candidate-pipeline",
        "--candidates",
        "--config",
        "--event-gate",
        "--market-data",
        "--output",
        "--ranking",
        "--research-window",
        "--source-matrix",
        "--sources",
    },
    "build-selection-recommendation": {
        "--candidate-pipeline",
        "--candidates",
        "--config",
        "--event-gate",
        "--market-data",
        "--output",
        "--ranking",
        "--research-window",
        "--selection",
        "--source-matrix",
        "--sources",
    },
    "risk-check": {
        "--candidate-pipeline",
        "--candidates",
        "--config",
        "--current-positions",
        "--event-gate",
        "--market-data",
        "--market-research",
        "--output",
        "--ranking",
        "--recommendation",
        "--research-window",
        "--selection",
        "--source-matrix",
        "--sources",
        "--trades-today",
    },
    # Reporting / validation stages. Each flag set is the subset of the CLI's
    # argparse contract the canonical nightly actually uses -- deliberately not
    # the whole parser. ``build-performance --timings`` and
    # ``validate-run-artifacts --output`` exist in the CLI and are omitted:
    # the nightly never passes them, so approving them would widen the
    # boundary for capability nobody runs.
    "validate-market-research": {
        "--market-research",
        "--output",
        "--research-window",
        "--source-matrix",
        "--sources",
    },
    "audit-official-ohlcv": {
        "--market-data",
        "--output",
        "--source-matrix",
        "--sources",
    },
    "build-performance": {
        "--candidate-pipeline",
        "--market-research",
        "--output",
        "--sources",
    },
    "render-research": {
        "--candidate-pipeline",
        "--market-research",
        "--output",
        "--performance",
        "--sources",
    },
    "render-report": {"--output", "--recommendation", "--risk-result"},
    "render-daily-report": {
        "--candidate-pipeline",
        "--market-research",
        "--output",
        "--performance",
        "--recommendation",
        "--risk-result",
        "--sources",
    },
    "validate-run-artifacts": {"--run-dir"},
    "verify-production-run": {"--output", "--run-dir", "--source-matrix"},
    "verify-production-happy-path": {"--output", "--run-dir", "--source-matrix"},
}

#: Flags whose value is a run-artifact path (must live under DAYTRADE_RUN_DIR).
RUN_ARTIFACT_FLAGS = frozenset(
    {
        "--sources",
        "--market-data",
        "--market-research",
        "--research-window",
        "--candidate-pipeline",
        "--candidates",
        "--event-research",
        "--event-gate",
        "--ranking",
        "--selection",
        "--recommendation",
        "--extraction",
        "--performance",
        "--risk-result",
    }
)

#: Flags whose value is not a filesystem path at all.
NON_PATH_FLAGS = frozenset(
    {
        "--target-date",
        "--trading-date",
        "--research-cutoff",
        "--previous-trading-day",
        "--agent-name",
        "--batch-size",
        "--current-positions",
        "--trades-today",
        "--event-gate-as-of",
    }
)

#: Every flag the guard knows how to reason about. An approved subcommand may
#: not carry a flag outside this union even if argparse would accept it.
KNOWN_PATH_FLAGS = frozenset(
    {"--output", "--run-dir", "--runs-dir", "--config", "--source-matrix"}
) | RUN_ARTIFACT_FLAGS

SHELL_METACHARACTERS = (
    ";",
    "&&",
    "||",
    "|&",
    "|",
    "&",
    "\n",
    "\r",
    ">>",
    ">",
    "<<",
    "<",
    "$(",
    "`",
    ">(",
    "<(",
)

EVENT_EXTRACTION_RELATIVE = ("working", "event_source_extraction.json")

class GuardDenied(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


# ------------------------------------------------------------- helpers ---


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise GuardDenied(
            CODE_PROFILE_MISSING, f"required environment variable {name} is not set"
        )
    return value


def _resolved(value: str) -> Path:
    return Path(value).resolve()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_runtime_profile() -> None:
    if os.environ.get("DAYTRADE_RUNTIME_PROFILE") != REQUIRED_RUNTIME_PROFILE:
        raise GuardDenied(
            CODE_PROFILE_MISSING,
            "DAYTRADE_RUNTIME_PROFILE must be 'production'",
        )


# ---------------------------------------------------------------- bash ---


def _reject_shell_metacharacters(command: str) -> None:
    for token in SHELL_METACHARACTERS:
        if token in command:
            raise GuardDenied(
                CODE_BASH_DENIED,
                f"shell metacharacter {token!r} is not permitted in production",
            )


def _tokenize(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as error:  # unbalanced quoting
        raise GuardDenied(CODE_BASH_DENIED, f"unparseable command: {error}") from None
    if not tokens:
        raise GuardDenied(CODE_BASH_DENIED, "empty command")
    for token in tokens:
        for meta in SHELL_METACHARACTERS:
            if meta in token:
                raise GuardDenied(
                    CODE_BASH_DENIED,
                    f"shell metacharacter {meta!r} survived tokenisation",
                )
    return tokens


def _validate_path_flag(
    *,
    subcommand: str,
    flag: str,
    value: str,
    run_dir: Path,
    daytrade_root: Path,
) -> None:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise GuardDenied(
            CODE_PATH_OUTSIDE_RUN,
            f"{flag} must be an absolute path in production",
        )
    resolved = candidate.resolve()

    if flag == "--run-dir":
        if resolved != run_dir:
            raise GuardDenied(
                CODE_PATH_OUTSIDE_RUN, "--run-dir must be exactly DAYTRADE_RUN_DIR"
            )
        return

    if flag == "--runs-dir":
        if resolved != (daytrade_root / "runs").resolve():
            raise GuardDenied(
                CODE_PATH_OUTSIDE_RUN, "--runs-dir must be DAYTRADE_ROOT/runs"
            )
        return

    if flag == "--config":
        if subcommand == "snapshot-config":
            expected = (daytrade_root / "config" / "strategy.yaml").resolve()
            if resolved != expected:
                raise GuardDenied(
                    CODE_PATH_OUTSIDE_RUN,
                    "snapshot-config --config must be DAYTRADE_ROOT/config/strategy.yaml",
                )
            return
        expected = (run_dir / "strategy_snapshot.yaml").resolve()
        if resolved != expected:
            raise GuardDenied(
                CODE_PATH_OUTSIDE_RUN,
                "--config must be the run-dir strategy_snapshot.yaml",
            )
        return

    if flag == "--source-matrix":
        allowed = {(daytrade_root / "config" / "source_matrix.yaml").resolve()}
        registry = (daytrade_root / "config" / "source_matrix_registry").resolve()
        if resolved not in allowed and not _is_within(resolved, registry):
            raise GuardDenied(
                CODE_PATH_OUTSIDE_RUN,
                "--source-matrix must be the repository Source Matrix",
            )
        return

    if flag == "--output":
        if subcommand == "snapshot-config":
            expected = (run_dir / "strategy_snapshot.yaml").resolve()
            if resolved != expected:
                raise GuardDenied(
                    CODE_PATH_OUTSIDE_RUN,
                    "snapshot-config --output must be run-dir strategy_snapshot.yaml",
                )
            return
        if not _is_within(resolved, run_dir):
            raise GuardDenied(
                CODE_PATH_OUTSIDE_RUN, "--output must stay inside DAYTRADE_RUN_DIR"
            )
        return

    if flag in RUN_ARTIFACT_FLAGS:
        if not _is_within(resolved, run_dir):
            raise GuardDenied(
                CODE_PATH_OUTSIDE_RUN, f"{flag} must stay inside DAYTRADE_RUN_DIR"
            )
        return

    raise GuardDenied(CODE_BASH_DENIED, f"unhandled flag {flag}")


def _validate_bash(tool_input: dict) -> None:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        raise GuardDenied(CODE_BASH_DENIED, "Bash tool_input.command is missing")

    _reject_shell_metacharacters(command)
    tokens = _tokenize(command)

    production_python = _env("DAYTRADE_PRODUCTION_PYTHON")
    run_dir = _resolved(_env("DAYTRADE_RUN_DIR"))
    daytrade_root = _resolved(_env("DAYTRADE_ROOT"))

    if tokens[0] != production_python:
        raise GuardDenied(
            CODE_BASH_DENIED,
            "only the production Python interpreter may be invoked",
        )
    if tokens[1:4] != ["-B", "-m", "src.cli"]:
        raise GuardDenied(
            CODE_BASH_DENIED,
            "the canonical invocation is '<production python> -B -m src.cli ...'",
        )
    if len(tokens) < 5:
        raise GuardDenied(CODE_NOT_CANONICAL, "no src.cli subcommand was given")

    subcommand = tokens[4]
    if subcommand in FORBIDDEN_SUBCOMMANDS:
        raise GuardDenied(
            CODE_NOT_CANONICAL,
            f"{subcommand} is a human/calibration command, not a nightly step",
        )
    if subcommand not in APPROVED_SUBCOMMANDS:
        raise GuardDenied(
            CODE_NOT_CANONICAL, f"{subcommand} is not an approved nightly subcommand"
        )

    allowed_flags = SUBCOMMAND_FLAGS[subcommand]
    rest = tokens[5:]
    index = 0
    while index < len(rest):
        token = rest[index]
        if not token.startswith("--"):
            raise GuardDenied(
                CODE_NOT_CANONICAL, f"unexpected positional argument {token!r}"
            )
        if "=" in token:
            flag, value = token.split("=", 1)
            index += 1
        else:
            flag = token
            if index + 1 >= len(rest):
                raise GuardDenied(CODE_NOT_CANONICAL, f"{flag} has no value")
            value = rest[index + 1]
            index += 2
        if flag not in allowed_flags:
            raise GuardDenied(
                CODE_NOT_CANONICAL,
                f"{flag} is not part of the {subcommand} argparse contract",
            )
        if flag in NON_PATH_FLAGS:
            continue
        if flag not in KNOWN_PATH_FLAGS:
            raise GuardDenied(CODE_NOT_CANONICAL, f"{flag} is not a recognised flag")
        _validate_path_flag(
            subcommand=subcommand,
            flag=flag,
            value=value,
            run_dir=run_dir,
            daytrade_root=daytrade_root,
        )


# ------------------------------------------------------------ write ---


def _validate_write(tool_name: str, tool_input: dict) -> None:
    raw = tool_input.get("file_path")
    if raw is None and tool_name == "NotebookEdit":
        raw = tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw.strip():
        raise GuardDenied(CODE_WRITE_DENIED, f"{tool_name} tool_input.file_path missing")

    run_dir = _resolved(_env("DAYTRADE_RUN_DIR"))
    expected = run_dir.joinpath(*EVENT_EXTRACTION_RELATIVE)

    candidate = Path(raw)
    if not candidate.is_absolute():
        raise GuardDenied(
            CODE_WRITE_DENIED, "production writes must use an absolute path"
        )
    if candidate.resolve() != expected.resolve():
        raise GuardDenied(
            CODE_WRITE_DENIED,
            "the only writable artifact is runs/<date>/working/"
            "event_source_extraction.json",
        )


# ---------------------------------------------------------------- main ---


def evaluate(payload: object) -> None:
    """Allow a PreToolUse call, or raise :class:`GuardDenied`.

    The guard has one job: enforce the production boundary before a tool runs.
    It returns nothing -- an allowed call is silent, and every other outcome is
    a denial.
    """
    _require_runtime_profile()

    if not isinstance(payload, dict):
        raise GuardDenied(CODE_BASH_DENIED, "hook payload must be a JSON object")

    event = payload.get("hook_event_name")
    if event != "PreToolUse":
        raise GuardDenied(CODE_BASH_DENIED, f"unsupported hook event {event!r}")

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise GuardDenied(CODE_BASH_DENIED, "tool_name is missing")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise GuardDenied(CODE_BASH_DENIED, "tool_input is missing")

    if tool_name == "Bash":
        _validate_bash(tool_input)
        return None
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        _validate_write(tool_name, tool_input)
        return None
    raise GuardDenied(CODE_BASH_DENIED, f"tool {tool_name} is not permitted")


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        if not raw.strip():
            raise GuardDenied(CODE_BASH_DENIED, "empty stdin")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise GuardDenied(CODE_BASH_DENIED, "stdin is not valid JSON") from None
        evaluate(payload)
    except GuardDenied as denial:
        sys.stderr.write(f"{denial.code}: {denial.reason}\n")
        return 2
    except Exception as error:  # fail closed on anything unexpected
        sys.stderr.write(f"{CODE_BASH_DENIED}: unexpected guard failure: {error}\n")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
