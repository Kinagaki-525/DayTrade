import json
from pathlib import Path

import pytest

import src.cli as cli
from src.config import load_strategy_config, strategy_config_sha256
from src.contracts import RUN_ARTIFACT_ALLOWLIST, validate_json_document
from src.market import SourceLedgerValidationResult
from src.research import merge_discovery_candidates
from src.source_matrix import load_source_matrix
from tests.factories import (
    make_candidate_research,
    make_event_research,
    make_market_record,
    make_source_attempt,
)
from tests.test_market_research import complete_candidate_research, market_research_payload


def config_metadata():
    config = load_strategy_config()
    return config["strategy_version"], strategy_config_sha256(config)


def complete_pipeline_summary(**overrides):
    summary = {
        "discovered": 1,
        "research_complete": 1,
        "research_incomplete": 0,
        "data_unavailable": 0,
        "screened": 1,
        "eligible": 1,
        "rejected": 0,
        "pipeline_complete": True,
        "stage2_target_count": 1,
        "stage2_completed_count": 1,
        "stage2_unavailable_count": 0,
        "stage2_incomplete_count": 0,
        "coverage_rate": 1,
        "research_incomplete_reason_counts": {},
        "screening_input_count": 1,
        "screening_pass_count": 1,
        "screening_reject_count": 0,
        "screening_data_unavailable_count": 0,
        "screening_incomplete_count": 0,
        "screening_complete": True,
        "candidate_count_consistent": True,
        "all_enabled_rules_evaluated": True,
        "screening_rule_counts": [],
        "ranking_complete": False,
    }
    summary.update(overrides)
    return summary


def complete_candidate_pipeline(strategy_version, config_sha256, **summary_overrides):
    return {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "generated_at": "2026-08-09T00:00:00+00:00",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "summary": complete_pipeline_summary(**summary_overrides),
        "candidates": [],
    }


def capture_validated_payload(captured):
    def capture(path, payload, schema):
        validate_json_document(payload, schema)
        captured.update(payload)

    return capture


def market_research_with_candidates(tickers):
    payload = market_research_payload()
    payload["discovery"][0]["items"] = [
        {
            "ticker": ticker,
            "company_name": f"Example {ticker}",
            "market": "TSE Prime",
            "rank": index + 1,
            "display_value": f"volume-{index + 1}",
            "disclosure_datetime": None,
            "title": None,
            "source_url": f"https://example.test/volume/{ticker}",
            "retrieved_at": "2026-08-07T20:15:00+09:00",
        }
        for index, ticker in enumerate(tickers)
    ]
    payload["discovery"][0]["result_count"] = len(tickers)
    payload["discovery"][1]["items"] = []
    payload["discovery"][1]["result_count"] = 0
    payload["discovery_candidates"] = merge_discovery_candidates(payload["discovery"])
    payload["candidate_research"] = []
    payload["overall_status"] = "PIPELINE_INCOMPLETE"
    return payload


def not_started_candidate_research(ticker="1234"):
    return {
        **make_candidate_research(
            ticker,
            data_status="NOT_STARTED",
            stage1_status="NOT_STARTED",
            stage2_status="NOT_STARTED",
            context_research_status="NOT_STARTED",
        ),
        "required_capital_yen": None,
    }


def source_payload_from_record(record, *, include_share_unit=True):
    return {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "sources": [
            source.as_dict()
            for source in record.sources
            if include_share_unit or source.field_name != "share_unit"
        ],
        "source_attempts": [],
    }


def run_apply_stage1_command(
    monkeypatch,
    captured,
    *,
    record,
    source_payload=None,
):
    market_research = market_research_with_candidates([record.ticker])
    market_research["candidate_research"] = [
        not_started_candidate_research(record.ticker)
    ]
    source_payload = source_payload or source_payload_from_record(record)

    def load_document(path, schema):
        if schema == "market_research.schema.json":
            return market_research
        if schema == "sources.schema.json":
            return source_payload
        return {"schema_version": 1, "target_date": "2026-08-10", "records": []}

    monkeypatch.setattr(cli, "load_json_document", load_document)
    monkeypatch.setattr(cli, "load_market_data", lambda path: ("2026-08-10", [record]))
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    return cli.main(
        [
            "apply-stage1",
            "--market-research",
            "market_research.json",
            "--market-data",
            "market_data.json",
            "--sources",
            "sources.json",
            "--output",
            "market_research.json",
        ]
    )


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


def test_init_candidate_research_command_adds_every_discovery_candidate(monkeypatch):
    captured = {}
    market_research = market_research_with_candidates(["1234", "5678"])

    monkeypatch.setattr(
        cli,
        "load_json_document",
        lambda path, schema: market_research,
    )
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    result = cli.main(
        [
            "init-candidate-research",
            "--market-research",
            "market_research.json",
            "--output",
            "market_research.json",
        ]
    )

    assert result == 0
    assert [item["ticker"] for item in captured["candidate_research"]] == [
        "1234",
        "5678",
    ]
    assert all(
        item["stage1_status"] == "NOT_STARTED"
        for item in captured["candidate_research"]
    )
    assert captured["overall_status"] == "PIPELINE_INCOMPLETE"


def test_apply_stage1_command_classifies_capital_boundary(monkeypatch):
    captured = {}
    record = make_market_record(previous_high="998", tick_size="1")

    result = run_apply_stage1_command(monkeypatch, captured, record=record)

    assert result == 0
    research = captured["candidate_research"][0]
    assert research["stage1_status"] == "PASSED"
    assert research["required_capital_yen"] == "100000"


def test_apply_stage1_command_requires_source_backed_share_unit(monkeypatch):
    captured = {}
    record = make_market_record(previous_high="998", tick_size="1")
    source_payload = source_payload_from_record(record, include_share_unit=False)

    result = run_apply_stage1_command(
        monkeypatch,
        captured,
        record=record,
        source_payload=source_payload,
    )

    assert result == 0
    research = captured["candidate_research"][0]
    assert research["stage1_status"] == "NOT_STARTED"
    assert research["required_capital_yen"] is None


def test_apply_stage1_command_rejects_over_capital_limit(monkeypatch):
    captured = {}
    record = make_market_record(previous_high="999", tick_size="1")

    result = run_apply_stage1_command(monkeypatch, captured, record=record)

    assert result == 0
    research = captured["candidate_research"][0]
    assert research["stage1_status"] == "REJECTED"
    assert research["reason_codes"] == ["CAPITAL_LIMIT_EXCEEDED"]
    assert research["required_capital_yen"] == "100100"


def test_plan_stage2_batches_command_assigns_unique_batches(monkeypatch):
    captured = {}
    market_research = market_research_with_candidates([f"{1000 + index}" for index in range(17)])
    market_research["candidate_research"] = [
        make_candidate_research(
            f"{1000 + index}",
            data_status="NOT_STARTED",
            stage2_status="NOT_STARTED",
            context_research_status="NOT_STARTED",
        )
        for index in range(17)
    ]

    monkeypatch.setattr(cli, "load_json_document", lambda path, schema: market_research)
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    result = cli.main(
        [
            "plan-stage2-batches",
            "--market-research",
            "market_research.json",
            "--output",
            "market_research.json",
        ]
    )

    assert result == 0
    assert [len(batch["candidate_codes"]) for batch in captured["subagent_batches"]] == [
        8,
        8,
        1,
    ]
    assigned = [
        ticker
        for batch in captured["subagent_batches"]
        for ticker in batch["candidate_codes"]
    ]
    assert len(assigned) == len(set(assigned)) == 17


def test_plan_stage2_batches_command_rejects_duplicate_assignment(monkeypatch):
    market_research = market_research_with_candidates(["1234"])
    market_research["candidate_research"] = [
        make_candidate_research(
            "1234",
            data_status="NOT_STARTED",
            stage2_status="NOT_STARTED",
            context_research_status="NOT_STARTED",
        )
    ]
    market_research["subagent_batches"] = [
        {
            "batch_id": "stage2-a",
            "agent_name": "market_researcher",
            "lifecycle_status": "REQUESTED",
            "candidate_codes": ["1234"],
            "returned_candidate_codes": [],
            "merged_candidate_codes": [],
        },
        {
            "batch_id": "stage2-b",
            "agent_name": "market_researcher",
            "lifecycle_status": "REQUESTED",
            "candidate_codes": ["1234"],
            "returned_candidate_codes": [],
            "merged_candidate_codes": [],
        },
    ]

    monkeypatch.setattr(cli, "load_json_document", lambda path, schema: market_research)

    with pytest.raises(ValueError, match="multiple Stage 2 batches"):
        cli.main(
            [
                "plan-stage2-batches",
                "--market-research",
                "market_research.json",
                "--output",
                "market_research.json",
            ]
        )


def test_build_candidate_pipeline_command_writes_payload(monkeypatch):
    captured = {}
    strategy_version, config_sha256 = config_metadata()
    payload = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "generated_at": "2026-08-09T00:00:00+00:00",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "summary": complete_pipeline_summary(
            discovered=0,
            research_complete=0,
            screened=0,
            eligible=0,
            stage2_target_count=0,
            stage2_completed_count=0,
            coverage_rate=None,
            screening_input_count=0,
            screening_pass_count=0,
        ),
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
            "cache_hit_count": 0,
            "discovery_candidate_count": 0,
            "stage1_candidate_count": 0,
                "stage1_rejected_count": 0,
                "stage2_candidate_count": 0,
                "stage2_completed_count": 0,
                "stage2_unavailable_count": 0,
                "stage2_incomplete_count": 0,
                "research_coverage_rate": None,
                "context_research_candidate_count": 0,
                "screening_source_request_count": 0,
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


def test_render_research_command_writes_structured_report(monkeypatch):
    captured = {}
    strategy_version, config_sha256 = config_metadata()
    market_research = {
        "target_date": "2026-08-10",
        "previous_trading_day": "2026-08-07",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "discovery": [],
    }
    candidate_pipeline = complete_candidate_pipeline(strategy_version, config_sha256)
    sources = {"target_date": "2026-08-10", "sources": [], "source_attempts": []}
    performance = {
        "target_date": "2026-08-10",
        "counts": {
            "source_request_count": 0,
            "adopted_source_count": 0,
            "cache_hit_count": 0,
        },
    }

    def load_document(path, schema):
        if schema == "market_research.schema.json":
            return market_research
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        if schema == "sources.schema.json":
            return sources
        if schema == "performance.schema.json":
            return performance
        raise AssertionError(schema)

    monkeypatch.setattr(cli, "load_json_document", load_document)
    monkeypatch.setattr(
        cli,
        "atomic_write_text",
        lambda path, text: captured.update({"path": path, "text": text}),
    )

    result = cli.main(
        [
            "render-research",
            "--market-research",
            "market_research.json",
            "--candidate-pipeline",
            "candidate_pipeline.json",
            "--sources",
            "sources.json",
            "--performance",
            "performance.json",
            "--output",
            "research.md",
        ]
    )

    assert result == 0
    assert captured["path"] == Path("research.md")
    assert "市場調査レポート" in captured["text"]


def test_render_research_rejects_mismatched_performance_date(monkeypatch):
    strategy_version, config_sha256 = config_metadata()
    market_research = {
        "target_date": "2026-08-10",
        "previous_trading_day": "2026-08-07",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "discovery": [],
    }
    candidate_pipeline = complete_candidate_pipeline(strategy_version, config_sha256)
    sources = {"target_date": "2026-08-10", "sources": [], "source_attempts": []}
    performance = {"target_date": "2026-08-11", "counts": {}}

    def load_document(path, schema):
        if schema == "market_research.schema.json":
            return market_research
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        if schema == "sources.schema.json":
            return sources
        if schema == "performance.schema.json":
            return performance
        raise AssertionError(schema)

    monkeypatch.setattr(cli, "load_json_document", load_document)

    with pytest.raises(ValueError, match="market_research/performance"):
        cli.main(
            [
                "render-research",
                "--market-research",
                "market_research.json",
                "--candidate-pipeline",
                "candidate_pipeline.json",
                "--sources",
                "sources.json",
                "--performance",
                "performance.json",
                "--output",
                "research.md",
            ]
        )


def test_render_daily_report_command_writes_structured_report(monkeypatch):
    captured = {}
    strategy_version, config_sha256 = config_metadata()
    market_research = {
        "target_date": "2026-08-10",
        "previous_trading_day": "2026-08-07",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "discovery": [],
    }
    candidate_pipeline = complete_candidate_pipeline(strategy_version, config_sha256)
    sources = {"target_date": "2026-08-10", "sources": [], "source_attempts": []}
    performance = {
        "target_date": "2026-08-10",
        "counts": {
            "source_request_count": 0,
            "adopted_source_count": 0,
            "cache_hit_count": 0,
        },
    }
    recommendation = {
        "target_date": "2026-08-10",
        "decision": "NO_TRADE",
        "ticker": None,
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "selection_reasons": ["該当候補なし"],
        "source_urls": [],
        "pipeline_summary": candidate_pipeline["summary"],
    }
    risk_result = {
        "target_date": "2026-08-10",
        "decision": "NO_TRADE",
        "ticker": None,
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "status": "NOT_APPLICABLE",
    }

    def load_document(path, schema):
        if schema == "market_research.schema.json":
            return market_research
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        if schema == "sources.schema.json":
            return sources
        if schema == "performance.schema.json":
            return performance
        if schema == "recommendation.schema.json":
            return recommendation
        if schema == "risk_result.schema.json":
            return risk_result
        raise AssertionError(schema)

    monkeypatch.setattr(cli, "load_json_document", load_document)
    monkeypatch.setattr(
        cli,
        "atomic_write_text",
        lambda path, text: captured.update({"path": path, "text": text}),
    )

    result = cli.main(
        [
            "render-daily-report",
            "--market-research",
            "market_research.json",
            "--candidate-pipeline",
            "candidate_pipeline.json",
            "--sources",
            "sources.json",
            "--performance",
            "performance.json",
            "--recommendation",
            "recommendation.json",
            "--risk-result",
            "risk_result.json",
            "--output",
            "report.md",
        ]
    )

    assert result == 0
    assert captured["path"] == Path("report.md")
    assert "日次デイトレ計画レポート" in captured["text"]


def test_validate_run_artifacts_command_rejects_unexpected_files(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli,
        "validate_run_artifact_allowlist",
        lambda run_dir: ("tmp_pydeps",),
    )
    monkeypatch.setattr(
        cli,
        "_emit_json",
        lambda payload, output_path=None: captured.update(payload),
    )

    with pytest.raises(ValueError, match="unexpected artifact"):
        cli.main(["validate-run-artifacts", "--run-dir", "runs/2026-08-10"])

    assert captured == {
        "status": "INVALID",
        "unexpected_files": ["tmp_pydeps"],
    }


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
                "post_cutoff_information_status": "OUT_OF_SCOPE",
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


def test_validate_market_research_command_rejects_unrecorded_stage1_source_ref(monkeypatch):
    captured = {}
    market_research = complete_candidate_research(market_research_payload())
    market_research["candidate_research"][0].update(
        {
            "stage1_status": "REJECTED",
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": ["JPX_TRADING_UNIT:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )
    resolved_window = {
        "schema_version": 1,
        "target_date": market_research["target_date"],
        "previous_trading_day": market_research["previous_trading_day"],
        "research_cutoff": market_research["research_cutoff"],
        "research_window": market_research["research_window"],
        "post_cutoff_information_status": "OUT_OF_SCOPE",
    }

    def load_document(path, schema):
        if schema == "market_research.schema.json":
            return market_research
        if schema == "research_window.schema.json":
            return resolved_window
        if schema == "sources.schema.json":
            return {
                "target_date": market_research["target_date"],
                "sources": [],
                "source_attempts": [],
            }
        raise AssertionError(schema)

    monkeypatch.setattr(cli, "load_json_document", load_document)
    monkeypatch.setattr(cli, "_write_json", capture_validated_payload(captured))

    with pytest.raises(ValueError, match="source-backed stage1_checks"):
        cli.main(
            [
                "validate-market-research",
                "--market-research",
                "market_research.json",
                "--research-window",
                "research_window.json",
                "--sources",
                "sources.json",
                "--output",
                "market_research_validation.json",
            ]
        )

    assert captured["valid"] is False
    assert any("source-backed stage1_checks" in error for error in captured["errors"])


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
    candidate_pipeline = complete_candidate_pipeline(
        strategy_version,
        config_sha256,
        research_complete=0,
        data_unavailable=1,
        screened=0,
        eligible=0,
        stage2_completed_count=0,
        stage2_unavailable_count=1,
    )
    recommendation["pipeline_summary"] = candidate_pipeline["summary"]

    def load_document(path, schema):
        if schema == "recommendation.schema.json":
            return recommendation
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        return candidates

    monkeypatch.setattr(
        cli,
        "load_json_document",
        load_document,
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
            "--candidate-pipeline",
            "candidate_pipeline.json",
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


def test_risk_check_rejects_incomplete_candidate_pipeline(monkeypatch):
    strategy_version, config_sha256 = config_metadata()
    recommendation = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "decision": "NO_TRADE",
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
        "selection_reasons": ["pipeline incomplete"],
        "source_urls": [],
        "notes": None,
    }
    candidates = {
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "candidates": [],
    }
    candidate_pipeline = complete_candidate_pipeline(
        strategy_version,
        config_sha256,
        research_complete=0,
        research_incomplete=1,
        screened=0,
        eligible=0,
        pipeline_complete=False,
        stage2_completed_count=0,
        stage2_incomplete_count=1,
        coverage_rate=0,
        research_incomplete_reason_counts={"NOT_STARTED": 1},
    )

    def load_document(path, schema):
        if schema == "recommendation.schema.json":
            return recommendation
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        return candidates

    monkeypatch.setattr(cli, "load_json_document", load_document)

    with pytest.raises(ValueError, match="candidate_pipeline is not complete"):
        cli.main(
            [
                "risk-check",
                "--recommendation",
                "recommendation.json",
                "--candidates",
                "candidates.json",
                "--candidate-pipeline",
                "candidate_pipeline.json",
                "--market-data",
                "market_data.json",
                "--sources",
                "sources.json",
                "--output",
                "risk_result.json",
            ]
        )


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
    candidate_pipeline = complete_candidate_pipeline(
        strategy_version,
        config_sha256,
        ranking_complete=True,
    )
    recommendation["pipeline_summary"] = candidate_pipeline["summary"]

    def load_document(path, schema):
        if schema == "recommendation.schema.json":
            return recommendation
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        return candidates

    monkeypatch.setattr(
        cli,
        "load_json_document",
        load_document,
    )

    with pytest.raises(ValueError, match="current_positions and trades_today"):
        cli.main(
            [
                "risk-check",
                "--recommendation",
                "recommendation.json",
                "--candidates",
                "candidates.json",
                "--candidate-pipeline",
                "candidate_pipeline.json",
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
                    "--candidate-pipeline",
                    str(Path("candidate_pipeline.json")),
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


def _write_json_file(path: Path, payload) -> None:
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


def _event_gate_fixture_paths(tmp_path: Path):
    strategy_version, config_sha = config_metadata()
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    candidates_path = tmp_path / "candidates.json"
    sources_path = tmp_path / "sources.json"
    _write_json_file(
        candidate_pipeline_path,
        complete_candidate_pipeline(strategy_version, config_sha),
    )
    _write_json_file(
        candidates_path,
        {
            "schema_version": 1,
            "target_date": "2026-08-10",
            "generated_at": "2026-08-09T00:00:00+00:00",
            "strategy_version": strategy_version,
            "config_sha256": config_sha,
            "candidates": [
                {
                    "ticker": "1234",
                    "status": "ELIGIBLE",
                    "screening_status": "PASS",
                    "reasons": [],
                    "unresolved_screening": [],
                    "passed_rules": [],
                    "failed_rules": [],
                    "unavailable_rules": [],
                    "reason_codes": [],
                    "source_refs": [],
                    "rule_evaluations": [],
                    "features": {
                        feature_name: {
                            "value": None,
                            "source_refs": [],
                            "source_urls": [],
                            "estimated": False,
                        }
                        for feature_name in (
                            "previous_day_volume",
                            "required_capital_yen",
                            "daily_range_yen",
                            "daily_range_pct",
                            "previous_day_change_pct",
                            "estimated_turnover",
                        )
                    },
                    "order_plan": {
                        "strategy_version": strategy_version,
                        "validation_status": "unvalidated",
                        "entry_trigger": "401",
                        "entry_limit": "402",
                        "affordable": True,
                        "estimated_purchase_amount": "40200",
                        "expected_loss_yen": "400",
                        "shares": 100,
                        "take_profit_price": "409",
                        "stop_loss_price": "398",
                    },
                }
            ],
        },
    )
    _write_json_file(
        sources_path,
        {
            "schema_version": 1,
            "target_date": "2026-08-10",
            "sources": [],
            "source_attempts": [
                make_source_attempt(
                    source_id=source_id,
                    source_role="PRIMARY",
                    criticality="TRADE_CRITICAL",
                    information_type="EVENT",
                    candidate_code="1234",
                    target_date="2026-08-10",
                )
                | {
                    "values": [],
                    "coverage_status": "COMPLETE",
                    "coverage_start": "2026-08-07T00:00:00+09:00",
                    "coverage_end": "2026-08-09T21:00:00+09:00",
                    "requested_at": "2026-08-09T20:00:00+09:00",
                    "result_count": 0,
                }
                for source_id in (
                    "JPX_EARNINGS_SCHEDULE",
                    "COMPANY_IR",
                    "JPX_TDNET",
                    "COMPANY_IR_DISCLOSURE",
                    "YAHOO_JP_NEWS",
                    "KABUTAN_NEWS",
                )
            ],
        },
    )
    return candidate_pipeline_path, candidates_path, sources_path


def test_init_event_research_command_writes_skeleton(tmp_path):
    candidate_pipeline_path, candidates_path, _ = _event_gate_fixture_paths(tmp_path)
    output_path = tmp_path / "event_research.json"

    result = cli.main(
        [
            "init-event-research",
            "--candidate-pipeline",
            str(candidate_pipeline_path),
            "--candidates",
            str(candidates_path),
            "--previous-trading-day",
            "2026-08-07",
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["event_gate_input_tickers"] == ["1234"]
    assert payload["event_gate_as_of"] is None


def test_build_event_gate_command_produces_ranking_ready_payload(tmp_path):
    candidate_pipeline_path, candidates_path, sources_path = _event_gate_fixture_paths(tmp_path)
    event_research_path = tmp_path / "event_research.json"
    output_path = tmp_path / "event_gate.json"
    strategy_version, config_sha = config_metadata()
    _write_json_file(
        event_research_path,
        make_event_research(
            target_date="2026-08-10",
            previous_trading_day="2026-08-07",
            event_gate_as_of="2026-08-09T21:00:00+09:00",
            strategy_version=strategy_version,
            config_sha256=config_sha,
            tickers=["1234"],
        ),
    )

    result = cli.main(
        [
            "build-event-gate",
            "--event-research",
            str(event_research_path),
            "--candidate-pipeline",
            str(candidate_pipeline_path),
            "--candidates",
            str(candidates_path),
            "--sources",
            str(sources_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["candidates"][0]["gate_status"] == "PASS"
    assert payload["ranking_ready"] is True


def test_validate_event_research_command_reports_valid(tmp_path):
    candidate_pipeline_path, candidates_path, sources_path = _event_gate_fixture_paths(tmp_path)
    event_research_path = tmp_path / "event_research.json"
    strategy_version, config_sha = config_metadata()
    _write_json_file(
        event_research_path,
        make_event_research(
            target_date="2026-08-10",
            previous_trading_day="2026-08-07",
            event_gate_as_of="2026-08-09T21:00:00+09:00",
            strategy_version=strategy_version,
            config_sha256=config_sha,
            tickers=["1234"],
        ),
    )

    result = cli.main(
        [
            "validate-event-research",
            "--event-research",
            str(event_research_path),
            "--candidate-pipeline",
            str(candidate_pipeline_path),
            "--candidates",
            str(candidates_path),
            "--sources",
            str(sources_path),
        ]
    )

    assert result == 0


def test_build_event_gate_command_rejects_pipeline_incomplete(tmp_path):
    candidate_pipeline_path, candidates_path, sources_path = _event_gate_fixture_paths(tmp_path)
    strategy_version, config_sha = config_metadata()
    _write_json_file(
        candidate_pipeline_path,
        complete_candidate_pipeline(strategy_version, config_sha, pipeline_complete=False),
    )
    event_research_path = tmp_path / "event_research.json"
    _write_json_file(
        event_research_path,
        make_event_research(
            target_date="2026-08-10",
            previous_trading_day="2026-08-07",
            event_gate_as_of="2026-08-09T21:00:00+09:00",
            strategy_version=strategy_version,
            config_sha256=config_sha,
            tickers=["1234"],
        ),
    )
    output_path = tmp_path / "event_gate.json"

    with pytest.raises(ValueError, match="not complete"):
        cli.main(
            [
                "build-event-gate",
                "--event-research",
                str(event_research_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--candidates",
                str(candidates_path),
                "--sources",
                str(sources_path),
                "--output",
                str(output_path),
            ]
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["build-event-gate", "--help"],
        ["init-event-research", "--help"],
        ["validate-event-research", "--help"],
        ["build-ranking-terminal-recommendation", "--help"],
    ],
)
def test_cli_help_is_importable_and_does_not_crash(argv):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == 0


def test_build_ranking_terminal_recommendation_requires_all_arguments():
    for missing in (
        "--ranking",
        "--candidates",
        "--candidate-pipeline",
        "--research-window",
        "--sources",
        "--config",
        "--output",
    ):
        full_args = [
            "--ranking",
            "ranking.json",
            "--candidates",
            "candidates.json",
            "--candidate-pipeline",
            "candidate_pipeline.json",
            "--research-window",
            "research_window.json",
            "--sources",
            "sources.json",
            "--config",
            "strategy.yaml",
            "--output",
            "recommendation.json",
        ]
        missing_index = full_args.index(missing)
        args_without = full_args[:missing_index] + full_args[missing_index + 2 :]
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                ["build-ranking-terminal-recommendation", *args_without]
            )


def test_build_ranking_terminal_recommendation_cli_writes_output(tmp_path):
    from src.config import DEFAULT_CONFIG_PATH, strategy_config_sha256
    from tests.factories import make_data_unavailable_ranking_payload

    config = load_strategy_config()
    config_sha = strategy_config_sha256(config)

    def _write(path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ranking = make_data_unavailable_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=config["strategy_version"],
        config_sha256=config_sha,
    )
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    _write(ranking_path, ranking)
    _write(
        candidates_path,
        {
            "schema_version": 1,
            "target_date": "2026-08-12",
            "generated_at": "2026-08-11T21:00:00+00:00",
            "strategy_version": config["strategy_version"],
            "config_sha256": config_sha,
            "candidates": [],
        },
    )
    _write(
        candidate_pipeline_path,
        {
            "schema_version": 1,
            "target_date": "2026-08-12",
            "generated_at": "2026-08-11T21:30:00+00:00",
            "strategy_version": config["strategy_version"],
            "config_sha256": config_sha,
            "summary": complete_pipeline_summary(),
            "candidates": [],
        },
    )
    _write(
        research_window_path,
        {
            "schema_version": 1,
            "target_date": "2026-08-12",
            "previous_trading_day": "2026-08-11",
            "research_cutoff": "2026-08-11T21:00:00+00:00",
            "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
            "research_window": {
                "run_type": "FIRST_RUN",
                "window_start": "2026-08-11T00:00:00+00:00",
                "window_end": "2026-08-11T21:00:00+00:00",
                "previous_research_cutoff": None,
                "previous_run_date": None,
                "bootstrap_lookback_days": 5,
            },
        },
    )
    _write(
        sources_path,
        {"schema_version": 1, "target_date": "2026-08-12", "sources": [], "source_attempts": []},
    )

    result = cli.main(
        [
            "build-ranking-terminal-recommendation",
            "--ranking",
            str(ranking_path),
            "--candidates",
            str(candidates_path),
            "--candidate-pipeline",
            str(candidate_pipeline_path),
            "--research-window",
            str(research_window_path),
            "--sources",
            str(sources_path),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["decision"] == "DATA_UNAVAILABLE"
    assert "recommendation.json" in RUN_ARTIFACT_ALLOWLIST


def test_risk_check_rejects_v1_trade_recommendation_under_v6_config(monkeypatch):
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
    candidate_pipeline = complete_candidate_pipeline(
        strategy_version, config_sha256, ranking_complete=True
    )
    recommendation["pipeline_summary"] = candidate_pipeline["summary"]

    def load_document(path, schema):
        if schema == "recommendation.schema.json":
            return recommendation
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        return candidates

    monkeypatch.setattr(cli, "load_json_document", load_document)

    with pytest.raises(ValueError, match="RISK_SELECTION_RECOMMENDATION_VERSION_INVALID"):
        cli.main(
            [
                "risk-check",
                "--recommendation",
                "recommendation.json",
                "--candidates",
                "candidates.json",
                "--candidate-pipeline",
                "candidate_pipeline.json",
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
