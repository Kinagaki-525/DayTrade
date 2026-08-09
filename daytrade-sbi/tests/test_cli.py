from pathlib import Path

import pytest

import src.cli as cli
from src.config import load_strategy_config, strategy_config_sha256
from src.contracts import validate_json_document
from src.market import SourceLedgerValidationResult
from tests.factories import make_market_record


def config_metadata():
    config = load_strategy_config()
    return config["strategy_version"], strategy_config_sha256(config)


def capture_validated_payload(captured):
    def capture(path, payload, schema):
        validate_json_document(payload, schema)
        captured.update(payload)

    return capture


def test_screen_market_command_generates_candidate_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli,
        "_load_market_bundle",
        lambda market, sources: (
            "2026-08-10",
            [make_market_record()],
            SourceLedgerValidationResult(True, ()),
            {"target_date": "2026-08-10", "sources": []},
        ),
    )
    monkeypatch.setattr(
        cli,
        "_write_json",
        capture_validated_payload(captured),
    )

    result = cli.main(
        [
            "screen-market",
            "--market-data",
            "unused.json",
            "--sources",
            "unused-sources.json",
            "--output",
            "unused-output.json",
        ]
    )

    assert result == 0
    assert captured["candidates"][0]["status"] == "ELIGIBLE"
    assert captured["strategy_version"] == "v1"
    assert len(captured["config_sha256"]) == 64


def test_risk_check_command_generates_pass_payload(monkeypatch):
    captured = {}
    strategy_version, config_sha256 = config_metadata()
    recommendation = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "decision": "TRADE",
        "ticker": "1234",
        "company_name": "Example Co.",
        "strategy_type": "previous_day_high_breakout",
        "previous_high": "400",
        "tick_size": "1",
        "entry_trigger": "401",
        "entry_limit": "402",
        "take_profit": "410",
        "stop_loss": "397",
        "shares": 100,
        "selection_reasons": ["test reason"],
        "source_urls": ["https://example.test/source"],
        "notes": None,
    }
    candidates = {
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "candidates": [{"ticker": "1234", "status": "ELIGIBLE"}],
    }
    monkeypatch.setattr(
        cli,
        "load_json_document",
        lambda path, schema: recommendation
        if schema == "recommendation.schema.json"
        else candidates,
    )
    monkeypatch.setattr(
        cli,
        "_load_market_bundle",
        lambda market, sources: (
            "2026-08-10",
            [make_market_record()],
            SourceLedgerValidationResult(True, ()),
            {
                "target_date": "2026-08-10",
                "sources": [
                    {"source_url": "https://example.test/source"},
                ],
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "_write_json",
        capture_validated_payload(captured),
    )

    result = cli.main(
        [
            "risk-check",
            "--recommendation",
            "recommendation.json",
            "--candidates",
            "candidates.json",
            "--market-data",
            "market_data.json",
            "--sources",
            "sources.json",
            "--output",
            "risk_result.json",
            "--current-positions",
            "0",
            "--trades-today",
            "0",
        ]
    )

    assert result == 0
    assert captured["status"] == "PASS"
    assert captured["expected_loss_yen"] == "500"
    assert captured["config_sha256"] == config_sha256


def test_risk_check_rejects_schema_invalid_recommendation(monkeypatch):
    def reject_recommendation(path, schema):
        raise ValueError("recommendation.schema.json validation failed")

    monkeypatch.setattr(cli, "load_json_document", reject_recommendation)

    with pytest.raises(ValueError, match="recommendation.schema.json"):
        cli.main(
            [
                "risk-check",
                "--recommendation",
                str(Path("recommendation.json")),
                "--candidates",
                str(Path("candidates.json")),
                "--market-data",
                str(Path("market_data.json")),
                "--sources",
                str(Path("sources.json")),
                "--output",
                str(Path("risk_result.json")),
                "--current-positions",
                "0",
                "--trades-today",
                "0",
            ]
        )


def test_validate_execution_command_prints_preview(monkeypatch, capsys):
    row = {
        "trade_date": "2026-08-10",
        "ticker": "1234",
        "actual_entry": "402",
    }
    monkeypatch.setattr(cli, "_load_execution_row", lambda *args: row)

    result = cli.main(
        [
            "validate-execution",
            "--execution",
            "execution.json",
            "--recommendation",
            "recommendation.json",
            "--risk-result",
            "risk-result.json",
            "--market-data",
            "market-data.json",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert '"status": "VALID"' in output
    assert '"actual_entry": "402"' in output


def test_record_execution_command_reports_idempotent_result(monkeypatch, capsys):
    row = {"trade_date": "2026-08-10", "ticker": "1234"}
    monkeypatch.setattr(cli, "_load_execution_row", lambda *args: row)
    monkeypatch.setattr(cli, "append_trade", lambda *args: False)

    result = cli.main(
        [
            "record-execution",
            "--execution",
            "execution.json",
            "--recommendation",
            "recommendation.json",
            "--risk-result",
            "risk-result.json",
            "--market-data",
            "market-data.json",
        ]
    )

    assert result == 0
    assert '"status": "ALREADY_RECORDED"' in capsys.readouterr().out


def test_calculate_metrics_command_preserves_unknown_values(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        cli,
        "calculate_metrics_from_csv",
        lambda path: {"total_trades": 1, "win_rate": None},
    )

    result = cli.main(["calculate-metrics"])

    assert result == 0
    output = capsys.readouterr().out
    assert '"total_trades": 1' in output
    assert '"win_rate": null' in output
