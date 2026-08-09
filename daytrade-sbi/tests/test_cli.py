from pathlib import Path

import pytest

import src.cli as cli
from src.config import load_strategy_config, strategy_config_sha256
from src.contracts import validate_json_document
from src.market import SourceLedgerValidationResult
from src.source_matrix import load_source_matrix
from tests.factories import make_market_record
from tests.test_market_research import complete_candidate_research, market_research_payload


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
        lambda market, sources, source_matrix: (
            "2026-08-10",
            [make_market_record()],
            SourceLedgerValidationResult(True, ()),
            {"target_date": "2026-08-10", "sources": []},
            load_source_matrix(),
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


def test_build_candidate_pipeline_command_writes_payload(monkeypatch):
    captured = {}
    strategy_version, config_sha256 = config_metadata()
    payload = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "generated_at": "2026-08-09T00:00:00+00:00",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "summary": {
            "discovered": 0,
            "research_complete": 0,
            "research_incomplete": 0,
            "data_unavailable": 0,
            "screened": 0,
            "eligible": 0,
            "rejected": 0,
        },
        "candidates": [],
    }

    def load_stub(path, schema):
        if schema == "market_research.schema.json":
            return {"target_date": "2026-08-10", "discovery_candidates": []}
        if schema == "candidates.schema.json":
            return {
                "target_date": "2026-08-10",
                "strategy_version": strategy_version,
                "config_sha256": config_sha256,
                "candidates": [],
            }
        if schema == "sources.schema.json":
            return {"target_date": "2026-08-10", "sources": [], "source_attempts": []}
        return {}

    monkeypatch.setattr(cli, "load_json_document", load_stub)
    monkeypatch.setattr(cli, "load_market_data", lambda path: ("2026-08-10", []))
    monkeypatch.setattr(cli, "build_candidate_pipeline", lambda **kwargs: payload)
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    result = cli.main(
        [
            "build-candidate-pipeline",
            "--market-research",
            "market_research.json",
            "--market-data",
            "market_data.json",
            "--candidates",
            "candidates.json",
            "--sources",
            "sources.json",
            "--output",
            "candidate_pipeline.json",
        ]
    )

    assert result == 0
    assert captured["schema_version"] == 1
    assert captured["summary"]["discovered"] == 0


def test_build_performance_command_writes_payload(monkeypatch):
    captured = {}
    payload = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "generated_at": "2026-08-09T00:00:00+00:00",
        "counts": {
            "source_request_count": 0,
            "adopted_source_count": 0,
            "duplicate_source_request_count": 0,
            "discovery_candidate_count": 0,
            "stage1_candidate_count": 0,
            "stage1_rejected_count": 0,
            "stage2_candidate_count": 0,
            "context_research_candidate_count": 0,
            "candidate_status_counts": {},
        },
        "timings": {
            "total": None,
            "calendar_check": None,
            "discovery": None,
            "candidate_stage1": None,
            "candidate_stage2": None,
            "source_audit": None,
            "market_validation": None,
            "screening": None,
            "ranking": None,
        },
    }

    def load_stub(path, schema):
        if schema == "market_research.schema.json":
            return {"target_date": "2026-08-10"}
        if schema == "candidate_pipeline.schema.json":
            return {"target_date": "2026-08-10", "candidates": []}
        if schema == "sources.schema.json":
            return {"target_date": "2026-08-10", "sources": [], "source_attempts": []}
        return {}

    monkeypatch.setattr(cli, "load_json_document", load_stub)
    monkeypatch.setattr(cli, "build_performance_payload", lambda **kwargs: payload)
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    result = cli.main(
        [
            "build-performance",
            "--market-research",
            "market_research.json",
            "--candidate-pipeline",
            "candidate_pipeline.json",
            "--sources",
            "sources.json",
            "--output",
            "performance.json",
        ]
    )

    assert result == 0
    assert captured["counts"]["source_request_count"] == 0


def test_validate_source_matrix_command_reports_valid(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_emit_json", lambda payload, output_path=None: captured.update(payload))

    result = cli.main(["validate-source-matrix"])

    assert result == 0
    assert captured["valid"] is True


def test_resolve_research_window_command_writes_schema_payload(monkeypatch):
    captured = {}

    class StubWindow:
        def as_dict(self):
            return {
                "schema_version": 1,
                "target_date": "2026-08-10",
                "previous_trading_day": "2026-08-07",
                "research_cutoff": "2026-08-07T20:00:00+09:00",
                "research_window": {
                    "run_type": "FIRST_RUN",
                    "window_start": "2026-08-06T20:00:00+09:00",
                    "window_end": "2026-08-07T20:00:00+09:00",
                    "previous_research_cutoff": None,
                    "previous_run_date": None,
                    "bootstrap_lookback_days": 1,
                },
            }

    monkeypatch.setattr(cli, "load_source_matrix", lambda path: {"source": "matrix"})
    monkeypatch.setattr(cli, "resolve_research_window", lambda **kwargs: StubWindow())
    monkeypatch.setattr(
        cli,
        "_write_json",
        lambda path, payload, schema: captured.update(
            {"path": path, "payload": payload, "schema": schema}
        ),
    )

    result = cli.main(
        [
            "resolve-research-window",
            "--target-date",
            "2026-08-10",
            "--previous-trading-day",
            "2026-08-07",
            "--runs-dir",
            "runs",
            "--output",
            "runs/2026-08-10/research_window.json",
        ]
    )

    assert result == 0
    assert captured["schema"] == "research_window.schema.json"
    assert captured["payload"]["research_window"]["run_type"] == "FIRST_RUN"


def test_validate_market_research_command_rejects_window_file_mismatch(monkeypatch):
    captured = {}
    market_research = complete_candidate_research(market_research_payload())
    resolved_window = {
        "schema_version": 1,
        "target_date": market_research["target_date"],
        "previous_trading_day": market_research["previous_trading_day"],
        "research_cutoff": market_research["research_cutoff"],
        "research_window": {
            **market_research["research_window"],
            "window_start": "2026-08-06T21:00:00+09:00",
        },
    }

    def load_document(path, schema):
        if schema == "market_research.schema.json":
            return market_research
        if schema == "research_window.schema.json":
            return resolved_window
        raise AssertionError(schema)

    monkeypatch.setattr(cli, "load_json_document", load_document)
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    with pytest.raises(ValueError, match="research_window.json"):
        cli.main(
            [
                "validate-market-research",
                "--market-research",
                "market_research.json",
                "--research-window",
                "research_window.json",
                "--output",
                "market_research_validation.json",
            ]
        )

    assert captured["valid"] is False
    assert captured["errors"] == [
        "market_research.research_window must match research_window.json",
    ]


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
        lambda market, sources, source_matrix: (
            "2026-08-10",
            [make_market_record()],
            SourceLedgerValidationResult(True, ()),
            {
                "target_date": "2026-08-10",
                "sources": [
                    {"source_url": "https://example.test/source"},
                ],
            },
            load_source_matrix(),
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


def test_risk_check_treats_data_unavailable_as_not_applicable(monkeypatch):
    captured = {}
    strategy_version, config_sha256 = config_metadata()
    recommendation = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "decision": "DATA_UNAVAILABLE",
        "ticker": None,
        "company_name": None,
        "strategy_type": None,
        "previous_high": None,
        "tick_size": None,
        "entry_trigger": None,
        "entry_limit": None,
        "take_profit": None,
        "stop_loss": None,
        "shares": None,
        "selection_reasons": ["secondary OHLCV source missing"],
        "source_urls": [],
        "notes": None,
    }
    candidates = {
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "candidates": [{"ticker": "1234", "status": "DATA_UNAVAILABLE"}],
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
        lambda market, sources, source_matrix: (
            "2026-08-10",
            [],
            SourceLedgerValidationResult(True, ()),
            {"target_date": "2026-08-10", "sources": []},
            load_source_matrix(),
        ),
    )
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

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
        ]
    )

    assert result == 0
    assert captured["decision"] == "DATA_UNAVAILABLE"
    assert captured["status"] == "NOT_APPLICABLE"
    assert captured["ticker"] is None


def test_risk_check_requires_position_inputs_for_trade(monkeypatch):
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

    with pytest.raises(ValueError, match="current_positions and trades_today"):
        cli.main(
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
            ]
        )


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
