"""CLI-level contracts for the new commands (transport fully mocked)."""

from __future__ import annotations

import json
import subprocess

import pytest

from src import cli, source_fetch
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
CUTOFF = "2026-08-12T20:00:00+09:00"


@pytest.fixture()
def mocked_curl(monkeypatch):
    """Replace the curl subprocess entirely. No network is ever touched."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        assert kwargs.get("shell") is False
        calls.append(argv)
        body = pages.yahoo_quote_page()
        return subprocess.CompletedProcess(
            argv, 0, body + b"\n200\ntext/html; charset=utf-8", b""
        )

    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "daytrade-test/1.0")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_acquire_actual_turnover_writes_a_v3_ledger(tmp_path, mocked_curl):
    sources = tmp_path / "sources.json"
    output = tmp_path / "result.json"
    code = cli.main(
        [
            "acquire-actual-turnover",
            "--target-date", TARGET_DATE,
            "--trading-date", TARGET_DATE,
            "--research-cutoff", CUTOFF,
            "--run-dir", str(tmp_path),
            "--sources", str(sources),
            "--ticker", "7203",
            "--output", str(output),
        ]
    )
    assert code == 0
    ledger = json.loads(sources.read_text(encoding="utf-8"))
    assert ledger["schema_version"] == 3
    attempt = ledger["source_attempts"][0]
    assert attempt["source_id"] == "YAHOO_JP_QUOTE"
    assert attempt["status"] == "FOUND"
    assert (tmp_path / attempt["source_page_path"]).is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["stage"] == "TURNOVER"
    assert len(mocked_curl) == 1  # request budget: exactly one GET


def test_acquire_stage_merges_into_an_existing_ledger(tmp_path, mocked_curl):
    sources = tmp_path / "sources.json"
    args = [
        "--target-date", TARGET_DATE,
        "--trading-date", TARGET_DATE,
        "--research-cutoff", CUTOFF,
        "--run-dir", str(tmp_path),
        "--sources", str(sources),
        "--ticker", "7203",
    ]
    cli.main(["acquire-actual-turnover", *args])
    first = json.loads(sources.read_text(encoding="utf-8"))
    cli.main(["acquire-actual-turnover", *args])
    second = json.loads(sources.read_text(encoding="utf-8"))
    assert len(second["source_attempts"]) == len(first["source_attempts"])


def test_acquire_stage1_reports_a_closed_listing_gate(tmp_path, mocked_curl):
    """A page that does not list the candidate closes the batch gate."""
    output = tmp_path / "result.json"
    code = cli.main(
        [
            "acquire-stage1-sources",
            "--target-date", TARGET_DATE,
            "--trading-date", TARGET_DATE,
            "--research-cutoff", CUTOFF,
            "--run-dir", str(tmp_path),
            "--sources", str(tmp_path / "sources.json"),
            "--ticker", "7203",
            "--output", str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "CLOSED"
    assert payload["reason_codes"] == ["TSE_LISTING_BATCH_GATE_FAILED"]
    assert code == 1


def test_candidate_tickers_are_required_for_candidate_scoped_stages(tmp_path):
    for command in (
        "acquire-stage1-sources",
        "acquire-stage2-market-sources",
        "acquire-actual-turnover",
        "acquire-event-sources",
    ):
        with pytest.raises(SystemExit):
            cli.main(
                [
                    command,
                    "--target-date", TARGET_DATE,
                    "--trading-date", TARGET_DATE,
                    "--research-cutoff", CUTOFF,
                    "--run-dir", str(tmp_path),
                    "--sources", str(tmp_path / "sources.json"),
                ]
            )


def test_verify_production_run_cli_reports_invalid(tmp_path):
    output = tmp_path / "verify.json"
    code = cli.main(
        ["verify-production-run", "--run-dir", str(tmp_path), "--output", str(output)]
    )
    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "INVALID_RUN"


def test_activate_selection_config_cli_requires_a_pair_id(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["activate-selection-config", "--calibration", str(tmp_path / "c.json")])
