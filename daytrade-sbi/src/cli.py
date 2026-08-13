from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.config import (
    DEFAULT_CONFIG_PATH,
    load_strategy_config,
    snapshot_strategy_config,
    strategy_config_sha256,
)
from src.candidate_pipeline import build_candidate_pipeline
from src.candidate_research import (
    apply_stage1_payload,
    init_candidate_research_payload,
    plan_stage2_batches_payload,
)
from src.contracts import (
    load_json_document,
    validate_candidate_pipeline_inputs,
    validate_json_document,
    validate_daily_report_inputs,
    validate_event_gate_inputs,
    validate_event_gate_output_contract,
    validate_event_research_contract,
    validate_event_research_inputs,
    validate_performance_inputs,
    validate_ranking_output_contract,
    validate_ranking_preconditions,
    validate_ranking_terminal_recommendation_output_contract,
    validate_ranking_terminal_recommendation_preconditions,
    validate_recommendation_candidate_link,
    validate_recommendation_pipeline_link,
    validate_recommendation_risk_link,
    validate_recommendation_sources,
    validate_research_report_inputs,
    validate_recommendation_selection_link,
    validate_run_artifact_allowlist,
    validate_selection_output_contract,
    validate_selection_preconditions,
    validate_selection_recommendation_output_contract,
    validate_selection_recommendation_preconditions,
)
from src.execution import append_trade, build_trade_row
from src.file_io import atomic_write_text
from src.market import (
    MarketDataRecord,
    SourceLedgerValidationResult,
    audit_official_ohlcv,
    load_market_data,
    validate_market_data,
    validate_source_ledger,
)
from src.metrics import DEFAULT_TRADES_PATH, calculate_metrics_from_csv
from src.performance import build_performance_payload
from src.ranking import build_ranking
from src.ranking_trust import load_and_verify_ranking_trust_chain
from src.selection import build_selection
from src.recommendations import append_recommendation, recommendation_to_row
from src.reports import render_daily_report, render_research_report, render_sbi_report
from src.event_gate import build_event_gate
from src.event_research import init_event_research_payload
from src.research import (
    MarketDataResearchAlignmentResult,
    MarketResearchValidationResult,
    validate_market_records_against_research,
    validate_market_research,
    validate_market_research_window_link,
)
from src.research_window import resolve_research_window
from src.risk import OrderProposal, evaluate_order, not_applicable_result
from src.screening import screen_market_record
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH, load_source_matrix, validate_source_matrix


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="daytrade-sbi v2 offline tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot-config")
    snapshot_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    snapshot_parser.add_argument("--output", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate-market")
    validate_parser.add_argument("--market-data", required=True, type=Path)
    validate_parser.add_argument("--sources", required=True, type=Path)
    validate_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    validate_parser.add_argument("--market-research", type=Path)
    validate_parser.add_argument("--output", required=True, type=Path)

    source_matrix_parser = subparsers.add_parser("validate-source-matrix")
    source_matrix_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    source_matrix_parser.add_argument("--output", type=Path)

    market_research_parser = subparsers.add_parser("validate-market-research")
    market_research_parser.add_argument("--market-research", required=True, type=Path)
    market_research_parser.add_argument("--research-window", required=True, type=Path)
    market_research_parser.add_argument("--sources", type=Path)
    market_research_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    market_research_parser.add_argument("--output", required=True, type=Path)

    init_candidate_research_parser = subparsers.add_parser("init-candidate-research")
    init_candidate_research_parser.add_argument(
        "--market-research",
        required=True,
        type=Path,
    )
    init_candidate_research_parser.add_argument("--output", required=True, type=Path)

    stage1_parser = subparsers.add_parser("apply-stage1")
    stage1_parser.add_argument("--market-research", required=True, type=Path)
    stage1_parser.add_argument("--market-data", required=True, type=Path)
    stage1_parser.add_argument("--sources", required=True, type=Path)
    stage1_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    stage1_parser.add_argument("--output", required=True, type=Path)

    stage2_batches_parser = subparsers.add_parser("plan-stage2-batches")
    stage2_batches_parser.add_argument("--market-research", required=True, type=Path)
    stage2_batches_parser.add_argument("--output", required=True, type=Path)
    stage2_batches_parser.add_argument("--batch-size", type=int, default=8)
    stage2_batches_parser.add_argument("--agent-name", default="market_researcher")

    research_window_parser = subparsers.add_parser("resolve-research-window")
    research_window_parser.add_argument("--target-date", required=True)
    research_window_parser.add_argument("--previous-trading-day", required=True)
    research_window_parser.add_argument("--runs-dir", required=True, type=Path)
    research_window_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    research_window_parser.add_argument("--output", required=True, type=Path)

    screen_parser = subparsers.add_parser("screen-market")
    screen_parser.add_argument("--market-data", required=True, type=Path)
    screen_parser.add_argument("--sources", required=True, type=Path)
    screen_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    screen_parser.add_argument("--market-research", type=Path)
    screen_parser.add_argument("--output", required=True, type=Path)
    screen_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    pipeline_parser = subparsers.add_parser("build-candidate-pipeline")
    pipeline_parser.add_argument("--market-research", required=True, type=Path)
    pipeline_parser.add_argument("--market-data", required=True, type=Path)
    pipeline_parser.add_argument("--candidates", required=True, type=Path)
    pipeline_parser.add_argument("--sources", required=True, type=Path)
    pipeline_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    pipeline_parser.add_argument("--output", required=True, type=Path)

    init_event_research_parser = subparsers.add_parser("init-event-research")
    init_event_research_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    init_event_research_parser.add_argument("--candidates", required=True, type=Path)
    init_event_research_parser.add_argument("--previous-trading-day", required=True)
    init_event_research_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    init_event_research_parser.add_argument("--output", required=True, type=Path)

    complete_event_research_parser = subparsers.add_parser("complete-event-research")
    complete_event_research_parser.add_argument(
        "--event-research", required=True, type=Path
    )
    complete_event_research_parser.add_argument("--sources", required=True, type=Path)
    complete_event_research_parser.add_argument(
        "--event-gate-as-of",
        required=True,
        help="the run's research cutoff; attempts requested after it are invisible",
    )
    complete_event_research_parser.add_argument("--extraction", type=Path)
    complete_event_research_parser.add_argument("--output", required=True, type=Path)

    validate_event_research_parser = subparsers.add_parser("validate-event-research")
    validate_event_research_parser.add_argument("--event-research", required=True, type=Path)
    validate_event_research_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    validate_event_research_parser.add_argument("--candidates", required=True, type=Path)
    validate_event_research_parser.add_argument("--sources", required=True, type=Path)
    validate_event_research_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    validate_event_research_parser.add_argument("--output", type=Path)

    event_gate_parser = subparsers.add_parser("build-event-gate")
    event_gate_parser.add_argument("--event-research", required=True, type=Path)
    event_gate_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    event_gate_parser.add_argument("--candidates", required=True, type=Path)
    event_gate_parser.add_argument("--sources", required=True, type=Path)
    event_gate_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    event_gate_parser.add_argument("--output", required=True, type=Path)

    ranking_parser = subparsers.add_parser("build-ranking")
    ranking_parser.add_argument("--event-gate", required=True, type=Path)
    ranking_parser.add_argument("--candidates", required=True, type=Path)
    ranking_parser.add_argument("--market-data", required=True, type=Path)
    ranking_parser.add_argument("--sources", required=True, type=Path)
    # Every build-ranking input is explicit: no implicit config or
    # source-matrix fallback, so the recorded input_hashes always describe
    # files the operator named.
    ranking_parser.add_argument("--source-matrix", required=True, type=Path)
    ranking_parser.add_argument("--config", required=True, type=Path)
    ranking_parser.add_argument("--output", required=True, type=Path)

    selection_parser = subparsers.add_parser("build-selection")
    selection_parser.add_argument("--ranking", required=True, type=Path)
    selection_parser.add_argument("--event-gate", required=True, type=Path)
    selection_parser.add_argument("--candidates", required=True, type=Path)
    selection_parser.add_argument("--market-data", required=True, type=Path)
    selection_parser.add_argument("--sources", required=True, type=Path)
    selection_parser.add_argument("--source-matrix", required=True, type=Path)
    selection_parser.add_argument("--config", required=True, type=Path)
    selection_parser.add_argument("--output", required=True, type=Path)

    selection_calibration_parser = subparsers.add_parser("build-selection-calibration")
    selection_calibration_parser.add_argument("--runs-dir", required=True, type=Path)
    selection_calibration_parser.add_argument("--config", required=True, type=Path)
    selection_calibration_parser.add_argument("--source-matrix", required=True, type=Path)
    selection_calibration_parser.add_argument(
        "--source-matrix-registry",
        required=False,
        type=Path,
        default=None,
        help=(
            "Immutable historical Source Matrix storage directory, used to "
            "re-verify old rankings' Trust Chains when --source-matrix's "
            "current bytes no longer match a historical run's recorded SHA."
        ),
    )
    selection_calibration_parser.add_argument("--start-date", required=False, type=str, default=None)
    selection_calibration_parser.add_argument("--end-date", required=False, type=str, default=None)
    selection_calibration_parser.add_argument("--output", required=True, type=Path)

    threshold_evaluation_parser = subparsers.add_parser("evaluate-selection-thresholds")
    threshold_evaluation_parser.add_argument("--calibration-report", required=True, type=Path)
    threshold_evaluation_parser.add_argument(
        "--minimum-turnover-yen", required=True, type=int
    )
    threshold_evaluation_parser.add_argument(
        "--maximum-relative-tick-numerator", required=True, type=int
    )
    threshold_evaluation_parser.add_argument(
        "--maximum-relative-tick-denominator", required=True, type=int
    )
    threshold_evaluation_parser.add_argument("--output", required=True, type=Path)

    selection_recommendation_parser = subparsers.add_parser("build-selection-recommendation")
    selection_recommendation_parser.add_argument("--ranking", required=True, type=Path)
    selection_recommendation_parser.add_argument("--selection", required=True, type=Path)
    selection_recommendation_parser.add_argument("--event-gate", required=True, type=Path)
    selection_recommendation_parser.add_argument("--candidates", required=True, type=Path)
    selection_recommendation_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    selection_recommendation_parser.add_argument("--market-data", required=True, type=Path)
    selection_recommendation_parser.add_argument("--research-window", required=True, type=Path)
    selection_recommendation_parser.add_argument("--sources", required=True, type=Path)
    selection_recommendation_parser.add_argument("--source-matrix", required=True, type=Path)
    selection_recommendation_parser.add_argument("--config", required=True, type=Path)
    selection_recommendation_parser.add_argument("--output", required=True, type=Path)

    ranking_terminal_recommendation_parser = subparsers.add_parser(
        "build-ranking-terminal-recommendation"
    )
    ranking_terminal_recommendation_parser.add_argument("--ranking", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument("--event-gate", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument("--candidates", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument(
        "--candidate-pipeline", required=True, type=Path
    )
    ranking_terminal_recommendation_parser.add_argument("--market-data", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument(
        "--research-window", required=True, type=Path
    )
    ranking_terminal_recommendation_parser.add_argument("--sources", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument("--source-matrix", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument("--config", required=True, type=Path)
    ranking_terminal_recommendation_parser.add_argument("--output", required=True, type=Path)

    performance_parser = subparsers.add_parser("build-performance")
    performance_parser.add_argument("--market-research", required=True, type=Path)
    performance_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    performance_parser.add_argument("--sources", required=True, type=Path)
    performance_parser.add_argument("--output", required=True, type=Path)
    performance_parser.add_argument("--timings", type=Path)

    risk_parser = subparsers.add_parser("risk-check")
    risk_parser.add_argument("--recommendation", required=True, type=Path)
    risk_parser.add_argument("--candidates", required=True, type=Path)
    risk_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    risk_parser.add_argument("--market-data", required=True, type=Path)
    risk_parser.add_argument("--sources", required=True, type=Path)
    risk_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    risk_parser.add_argument("--market-research", type=Path)
    risk_parser.add_argument("--output", required=True, type=Path)
    risk_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    risk_parser.add_argument("--current-positions", type=int)
    risk_parser.add_argument("--trades-today", type=int)
    risk_parser.add_argument("--selection", type=Path)
    risk_parser.add_argument("--ranking", type=Path)
    risk_parser.add_argument("--event-gate", type=Path)
    risk_parser.add_argument("--research-window", type=Path)

    audit_parser = subparsers.add_parser("audit-official-ohlcv")
    audit_parser.add_argument("--market-data", required=True, type=Path)
    audit_parser.add_argument("--sources", required=True, type=Path)
    audit_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    audit_parser.add_argument("--output", required=True, type=Path)

    report_parser = subparsers.add_parser("render-report")
    report_parser.add_argument("--recommendation", required=True, type=Path)
    report_parser.add_argument("--risk-result", required=True, type=Path)
    report_parser.add_argument("--output", required=True, type=Path)

    research_report_parser = subparsers.add_parser("render-research")
    research_report_parser.add_argument("--market-research", required=True, type=Path)
    research_report_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    research_report_parser.add_argument("--sources", required=True, type=Path)
    research_report_parser.add_argument("--performance", required=True, type=Path)
    research_report_parser.add_argument("--output", required=True, type=Path)

    daily_report_parser = subparsers.add_parser("render-daily-report")
    daily_report_parser.add_argument("--market-research", required=True, type=Path)
    daily_report_parser.add_argument("--candidate-pipeline", required=True, type=Path)
    daily_report_parser.add_argument("--sources", required=True, type=Path)
    daily_report_parser.add_argument("--performance", required=True, type=Path)
    daily_report_parser.add_argument("--recommendation", required=True, type=Path)
    daily_report_parser.add_argument("--risk-result", required=True, type=Path)
    daily_report_parser.add_argument("--output", required=True, type=Path)

    record_parser = subparsers.add_parser("record-recommendation")
    record_parser.add_argument("--recommendation", required=True, type=Path)
    record_parser.add_argument("--risk-result", required=True, type=Path)
    record_parser.add_argument("--csv", type=Path)

    execution_parser = subparsers.add_parser("validate-execution")
    _add_execution_arguments(execution_parser)
    execution_parser.add_argument("--output", type=Path)

    record_execution_parser = subparsers.add_parser("record-execution")
    _add_execution_arguments(record_execution_parser)
    record_execution_parser.add_argument("--csv", type=Path)

    metrics_parser = subparsers.add_parser("calculate-metrics")
    metrics_parser.add_argument("--csv", type=Path, default=DEFAULT_TRADES_PATH)
    metrics_parser.add_argument("--output", type=Path)

    run_artifacts_parser = subparsers.add_parser("validate-run-artifacts")
    run_artifacts_parser.add_argument("--run-dir", required=True, type=Path)
    run_artifacts_parser.add_argument("--output", type=Path)

    # --- Source Acquisition (deterministic HTTP, no AI in the numeric path) --
    #
    # There is deliberately NO --ticker option on any acquisition command.
    # Which tickers get network access is derived from the artifacts already
    # on disk (market_research.json / candidates.json / candidate_pipeline.json),
    # so an agent cannot inject or widen the candidate set.
    for command in (
        "acquire-discovery",
        "acquire-stage1-sources",
        "acquire-stage2-market-sources",
        "acquire-actual-turnover",
        "acquire-event-sources",
    ):
        acquire_parser = subparsers.add_parser(command)
        acquire_parser.add_argument("--target-date", required=True)
        acquire_parser.add_argument("--trading-date", required=True)
        acquire_parser.add_argument("--research-cutoff", required=True)
        acquire_parser.add_argument("--run-dir", required=True, type=Path)
        acquire_parser.add_argument("--sources", required=True, type=Path)
        acquire_parser.add_argument(
            "--source-matrix", type=Path, default=DEFAULT_SOURCE_MATRIX_PATH
        )
        if command == "acquire-discovery":
            acquire_parser.add_argument("--research-window", required=True, type=Path)
        acquire_parser.add_argument("--output", type=Path)

    merge_event_parser = subparsers.add_parser("merge-event-source-extraction")
    merge_event_parser.add_argument("--extraction", required=True, type=Path)
    merge_event_parser.add_argument("--sources", required=True, type=Path)
    merge_event_parser.add_argument("--run-dir", type=Path)
    merge_event_parser.add_argument("--output", type=Path)

    verify_run_parser = subparsers.add_parser("verify-production-run")
    verify_run_parser.add_argument("--run-dir", required=True, type=Path)
    verify_run_parser.add_argument(
        "--source-matrix", type=Path, default=DEFAULT_SOURCE_MATRIX_PATH
    )
    verify_run_parser.add_argument("--output", type=Path)

    verify_happy_parser = subparsers.add_parser("verify-production-happy-path")
    verify_happy_parser.add_argument("--run-dir", required=True, type=Path)
    verify_happy_parser.add_argument(
        "--source-matrix", type=Path, default=DEFAULT_SOURCE_MATRIX_PATH
    )
    verify_happy_parser.add_argument("--output", type=Path)

    activate_parser = subparsers.add_parser("activate-selection-config")
    activate_parser.add_argument("--calibration", required=True, type=Path)
    activate_parser.add_argument(
        "--pair-id",
        required=True,
        help="the pair_id a HUMAN chose; the agent must never pick one",
    )
    activate_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    # Required, not defaulted: activation must be pinned to the exact Source
    # Matrix bytes the calibration was produced under.
    activate_parser.add_argument("--source-matrix", required=True, type=Path)
    activate_parser.add_argument("--output", type=Path)
    return parser


#: acquisition CLI command -> acquisition stage
ACQUIRE_COMMAND_STAGES = {
    "acquire-discovery": "DISCOVERY",
    "acquire-stage1-sources": "STAGE1",
    "acquire-stage2-market-sources": "STAGE2",
    "acquire-actual-turnover": "TURNOVER",
    "acquire-event-sources": "EVENT",
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot-config":
        snapshot_strategy_config(args.output, args.config)
        return 0
    if args.command == "validate-market":
        return _validate_market(
            args.market_data,
            args.sources,
            args.output,
            args.source_matrix,
            args.market_research,
        )
    if args.command == "validate-source-matrix":
        return _validate_source_matrix(args.source_matrix, args.output)
    if args.command == "validate-market-research":
        return _validate_market_research(
            args.market_research,
            args.research_window,
            args.sources,
            args.source_matrix,
            args.output,
        )
    if args.command == "init-candidate-research":
        return _init_candidate_research(args.market_research, args.output)
    if args.command == "apply-stage1":
        return _apply_stage1(
            args.market_research,
            args.market_data,
            args.sources,
            args.config,
            args.output,
        )
    if args.command == "plan-stage2-batches":
        return _plan_stage2_batches(
            args.market_research,
            args.output,
            args.batch_size,
            args.agent_name,
        )
    if args.command == "resolve-research-window":
        return _resolve_research_window(
            args.target_date,
            args.previous_trading_day,
            args.runs_dir,
            args.source_matrix,
            args.output,
        )
    if args.command == "screen-market":
        return _screen_market(
            args.market_data,
            args.sources,
            args.output,
            args.config,
            args.source_matrix,
            args.market_research,
        )
    if args.command == "build-candidate-pipeline":
        return _build_candidate_pipeline(
            args.market_research,
            args.market_data,
            args.candidates,
            args.sources,
            args.config,
            args.output,
        )
    if args.command == "init-event-research":
        return _init_event_research(
            args.candidate_pipeline,
            args.candidates,
            args.previous_trading_day,
            args.config,
            args.output,
        )
    if args.command == "complete-event-research":
        return _complete_event_research(
            args.event_research,
            args.sources,
            args.event_gate_as_of,
            args.extraction,
            args.output,
        )
    if args.command == "validate-event-research":
        return _validate_event_research(
            args.event_research,
            args.candidate_pipeline,
            args.candidates,
            args.sources,
            args.config,
            args.output,
        )
    if args.command == "build-event-gate":
        return _build_event_gate(
            args.event_research,
            args.candidate_pipeline,
            args.candidates,
            args.sources,
            args.config,
            args.output,
        )
    if args.command == "build-ranking":
        return _build_ranking(
            args.event_gate,
            args.candidates,
            args.market_data,
            args.sources,
            args.source_matrix,
            args.config,
            args.output,
        )
    if args.command == "build-selection":
        return _build_selection(
            args.ranking,
            args.event_gate,
            args.candidates,
            args.market_data,
            args.sources,
            args.source_matrix,
            args.config,
            args.output,
        )
    if args.command == "build-selection-calibration":
        return _build_selection_calibration(
            args.runs_dir,
            args.config,
            args.source_matrix,
            args.source_matrix_registry,
            args.start_date,
            args.end_date,
            args.output,
        )
    if args.command == "evaluate-selection-thresholds":
        return _evaluate_selection_thresholds(
            args.calibration_report,
            args.minimum_turnover_yen,
            args.maximum_relative_tick_numerator,
            args.maximum_relative_tick_denominator,
            args.output,
        )
    if args.command == "build-selection-recommendation":
        return _build_selection_recommendation(
            args.ranking,
            args.selection,
            args.event_gate,
            args.candidates,
            args.candidate_pipeline,
            args.market_data,
            args.research_window,
            args.sources,
            args.source_matrix,
            args.config,
            args.output,
        )
    if args.command == "build-ranking-terminal-recommendation":
        return _build_ranking_terminal_recommendation(
            args.ranking,
            args.event_gate,
            args.candidates,
            args.candidate_pipeline,
            args.market_data,
            args.research_window,
            args.sources,
            args.source_matrix,
            args.config,
            args.output,
        )
    if args.command == "build-performance":
        return _build_performance(
            args.market_research,
            args.candidate_pipeline,
            args.sources,
            args.output,
            args.timings,
        )
    if args.command == "risk-check":
        return _risk_check(
            args.recommendation,
            args.candidates,
            args.candidate_pipeline,
            args.market_data,
            args.sources,
            args.output,
            args.config,
            args.current_positions,
            args.trades_today,
            args.source_matrix,
            args.market_research,
            args.selection,
            args.ranking,
            args.event_gate,
            args.research_window,
        )
    if args.command == "audit-official-ohlcv":
        return _audit_official_ohlcv(
            args.market_data,
            args.sources,
            args.output,
            args.source_matrix,
        )
    if args.command == "render-report":
        return _render_report(args.recommendation, args.risk_result, args.output)
    if args.command == "render-research":
        return _render_research(
            args.market_research,
            args.candidate_pipeline,
            args.sources,
            args.performance,
            args.output,
        )
    if args.command == "render-daily-report":
        return _render_daily_report(
            args.market_research,
            args.candidate_pipeline,
            args.sources,
            args.performance,
            args.recommendation,
            args.risk_result,
            args.output,
        )
    if args.command == "record-recommendation":
        return _record_recommendation(args.recommendation, args.risk_result, args.csv)
    if args.command == "validate-execution":
        row = _load_execution_row(
            args.execution,
            args.recommendation,
            args.risk_result,
            args.market_data,
        )
        _emit_json({"status": "VALID", "trade_row": row}, args.output)
        return 0
    if args.command == "record-execution":
        row = _load_execution_row(
            args.execution,
            args.recommendation,
            args.risk_result,
            args.market_data,
        )
        appended = append_trade(row) if args.csv is None else append_trade(row, args.csv)
        _emit_json(
            {
                "status": "RECORDED" if appended else "ALREADY_RECORDED",
                "trade_date": row["trade_date"],
                "ticker": row["ticker"],
            }
        )
        return 0
    if args.command == "calculate-metrics":
        _emit_json(calculate_metrics_from_csv(args.csv), args.output)
        return 0
    if args.command == "validate-run-artifacts":
        unexpected = validate_run_artifact_allowlist(args.run_dir)
        payload = {
            "status": "VALID" if not unexpected else "INVALID",
            "unexpected_files": list(unexpected),
        }
        _emit_json(payload, args.output)
        if unexpected:
            raise ValueError(
                "run directory contains unexpected artifact(s): "
                + ", ".join(unexpected)
            )
        return 0
    if args.command in ACQUIRE_COMMAND_STAGES:
        return _acquire_sources(ACQUIRE_COMMAND_STAGES[args.command], args)
    if args.command == "merge-event-source-extraction":
        return _merge_event_source_extraction(
            args.extraction, args.sources, args.run_dir, args.output
        )
    if args.command == "verify-production-run":
        return _verify_production_run(args.run_dir, args.source_matrix, args.output)
    if args.command == "verify-production-happy-path":
        return _verify_production_happy_path(
            args.run_dir, args.source_matrix, args.output
        )
    if args.command == "activate-selection-config":
        return _activate_selection_config(
            args.calibration,
            args.pair_id,
            args.config,
            args.source_matrix,
            args.output,
        )
    raise ValueError(f"Unknown command: {args.command}")


def _complete_event_research(
    event_research_path: Path,
    sources_path: Path,
    event_gate_as_of: str,
    extraction_path: Path | None,
    output_path: Path,
) -> int:
    """Fill in canonical attempt ids / event_gate_as_of / news classifications.

    All three are derived from artifacts on disk with the same selection the
    Event Gate itself uses; the agent supplies none of them.
    """
    from src.event_research_completion import complete_event_research
    from src.event_source_extraction import staged_news_classifications

    event_research = load_json_document(
        event_research_path, "event_research.schema.json"
    )
    source_payload = load_json_document(sources_path, "sources.schema.json")
    classifications: dict[str, list[dict[str, Any]]] = {}
    if extraction_path is not None:
        extraction = load_json_document(
            extraction_path, "event_source_extraction.schema.json"
        )
        classifications = staged_news_classifications(extraction)

    completed = complete_event_research(
        event_research=event_research,
        source_payload=source_payload,
        event_gate_as_of=event_gate_as_of,
        news_classifications=classifications,
    )
    _write_json(output_path, completed, "event_research.schema.json")
    return 0


def _acquire_sources(
    stage: str,
    args: argparse.Namespace,
    transport: Any = None,
) -> int:
    """Stage-aware Source Acquisition, wired end to end.

    Contract: derive the candidate set from the artifacts already on disk
    (never from a CLI argument), fetch the stage's sources, store the raw
    evidence, parse it deterministically, merge Source Ledger v3 attempts into
    ``--sources`` **and** reflect the parsed values into the business artifact
    the stage feeds (market_research.json for Discovery, market_data.json for
    Stage1 / Stage2 / Turnover).
    """
    from src.source_acquisition import (
        acquire_stage,
        curl_transport,
        load_ledger,
        merge_ledger,
        write_ledger,
    )
    from src.stage_wiring import (
        build_market_research,
        reflect_market_data,
        resolve_stage_candidates,
        write_market_data,
        write_market_research,
    )

    run_dir = Path(args.run_dir)
    source_matrix = load_source_matrix(args.source_matrix)
    existing = load_ledger(args.sources)
    tickers = resolve_stage_candidates(stage, run_dir)

    result = acquire_stage(
        stage,
        target_date=args.target_date,
        trading_date=args.trading_date,
        research_cutoff=args.research_cutoff,
        tickers=tickers,
        run_dir=run_dir,
        source_matrix=source_matrix,
        existing_ledger=existing,
        transport=transport if transport is not None else curl_transport,
    )

    merged = merge_ledger(existing, result.as_ledger())

    artifact: str | None = None
    if stage == "DISCOVERY":
        research_window = load_json_document(
            args.research_window, "research_window.schema.json"
        )
        market_research = build_market_research(
            result,
            research_window=research_window,
            source_matrix=source_matrix,
            research_executed_at=_utc_now(),
        )
        write_market_research(run_dir / "market_research.json", market_research)
        artifact = "market_research.json"
    elif stage in {"STAGE1", "STAGE2", "TURNOVER"}:
        market_data_path = run_dir / "market_data.json"
        current = (
            json.loads(market_data_path.read_text(encoding="utf-8"))
            if market_data_path.is_file()
            else None
        )
        updated = reflect_market_data(
            stage,
            result,
            existing=current,
            trading_date=args.trading_date,
        )
        # Some Attempt Values are fetched once per run (candidate_code=None,
        # e.g. JPX_TICK_SIZE's band table) and then fanned out by
        # reflect_market_data into a per-candidate market_data.sources entry
        # (same source_ref, ticker now set). acquire_stage's own ledger only
        # ever records the un-fanned, ticker-less value (AcquisitionResult
        # only keeps values whose ParsedValue carried a ticker), so that
        # fanned-out copy is otherwise absent from sources.json. Reconcile
        # them here -- never inventing a source_ref, only including entries
        # this stage's own attempts already parsed (real Attempt Values),
        # so Source Ledger Integrity (every source_ref traces to a real
        # source_attempts[].values[] entry) still holds.
        this_stage_value_refs = {
            str(value["source_ref"]) for value in result.values
        } | {
            str(value.get("source_ref"))
            for attempt in result.attempts
            for value in (attempt.get("values") or [])
            if isinstance(value, dict) and value.get("source_ref")
        }
        ledger_values = {value["source_ref"]: value for value in merged.get("sources", [])}
        for record in updated.get("records", []):
            for source in record.get("sources", []):
                if source["source_ref"] in this_stage_value_refs:
                    ledger_values.setdefault(source["source_ref"], source)
        merged["sources"] = list(ledger_values.values())
        write_market_data(market_data_path, updated)
        artifact = "market_data.json"

    validate_json_document(merged, "sources.schema.json")
    write_ledger(args.sources, merged)

    _emit_json(
        {
            "status": result.gate_status,
            "stage": stage,
            "reason_codes": result.gate_reason_codes,
            "candidate_count": len(tickers),
            "attempt_count": len(result.attempts),
            "value_count": len(result.values),
            "network_request_count": sum(
                1
                for attempt in result.attempts
                if attempt.get("cache_status") == "MISS"
            ),
            "artifact": artifact,
        },
        args.output,
    )
    return 0 if result.gate_status != "CLOSED" else 1


def _merge_event_source_extraction(
    extraction_path: Path,
    sources_path: Path,
    run_dir: Path | None,
    output_path: Path | None,
) -> int:
    from src.event_source_extraction import merge_event_source_extraction_files

    merged = merge_event_source_extraction_files(
        extraction_path=extraction_path,
        sources_path=sources_path,
        run_dir=run_dir,
    )
    _emit_json(
        {
            "status": "MERGED",
            "value_count": len(merged["sources"]),
            "attempt_count": len(merged["source_attempts"]),
        },
        output_path,
    )
    return 0


def _verify_production_run(
    run_dir: Path,
    source_matrix_path: Path,
    output_path: Path | None,
) -> int:
    from src.production_verify import INVALID_RUN, verify_production_run

    report = verify_production_run(run_dir, source_matrix_path=source_matrix_path)
    _emit_json(report.as_dict(), output_path)
    return 1 if report.status == INVALID_RUN else 0


def _verify_production_happy_path(
    run_dir: Path,
    source_matrix_path: Path,
    output_path: Path | None,
) -> int:
    from src.production_verify import (
        VERIFIED_STATUSES,
        verify_production_happy_path,
    )

    report = verify_production_happy_path(
        run_dir, source_matrix_path=source_matrix_path
    )
    _emit_json(report.as_dict(), output_path)
    # A correct termination is the success condition -- including Case C
    # NO_TRADE. A forced TRADE is not required and must never be.
    return 0 if report.status in VERIFIED_STATUSES else 1


def _activate_selection_config(
    calibration_path: Path,
    pair_id: str,
    config_path: Path,
    source_matrix_path: Path,
    output_path: Path | None,
) -> int:
    """Apply the human-chosen threshold pair. Never chooses one itself."""
    from src.selection_activation import activate_selection_config

    calibration = load_json_document(
        calibration_path, "selection_calibration.schema.json"
    )
    summary = activate_selection_config(
        config_path=config_path,
        calibration=calibration,
        pair_id=pair_id,
        source_matrix_path=source_matrix_path,
    )
    _emit_json(summary, output_path)
    return 0


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execution", required=True, type=Path)
    parser.add_argument("--recommendation", required=True, type=Path)
    parser.add_argument("--risk-result", required=True, type=Path)
    parser.add_argument("--market-data", required=True, type=Path)


def _init_candidate_research(
    market_research_path: Path,
    output_path: Path,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    payload = init_candidate_research_payload(market_research)
    _write_json(output_path, payload, "market_research.schema.json")
    return 0


def _apply_stage1(
    market_research_path: Path,
    market_data_path: Path,
    sources_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    load_json_document(market_data_path, "market_data.schema.json")
    _, records = load_market_data(market_data_path)
    source_payload = load_json_document(sources_path, "sources.schema.json")
    config = load_strategy_config(config_path)
    payload = apply_stage1_payload(
        market_research=market_research,
        market_records=records,
        source_payload=source_payload,
        config=config,
    )
    _write_json(output_path, payload, "market_research.schema.json")
    return 0


def _plan_stage2_batches(
    market_research_path: Path,
    output_path: Path,
    batch_size: int,
    agent_name: str,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    payload = plan_stage2_batches_payload(
        market_research=market_research,
        batch_size=batch_size,
        agent_name=agent_name,
    )
    _write_json(output_path, payload, "market_research.schema.json")
    return 0


def _validate_market(
    market_data_path: Path,
    sources_path: Path,
    output_path: Path,
    source_matrix_path: Path,
    market_research_path: Path | None,
) -> int:
    (
        target_date,
        records,
        ledger_result,
        source_payload,
        source_matrix,
    ) = _load_market_bundle(market_data_path, sources_path, source_matrix_path)
    alignment_result = _load_market_research_alignment(
        records,
        market_research_path,
        source_matrix,
        source_payload,
    )
    global_errors = (
        list(alignment_result.global_errors) if alignment_result is not None else []
    )
    results = []
    for record in records:
        record_result = validate_market_data(record, source_matrix)
        alignment_errors = (
            list(alignment_result.errors_by_ticker.get(record.ticker or "<missing>", ()))
            if alignment_result is not None
            else []
        )
        errors = [
            *ledger_result.errors,
            *global_errors,
            *record_result.errors,
            *alignment_errors,
        ]
        results.append(
            {
                "ticker": record.ticker,
                "data_status": record.data_status,
                "valid_for_trade": not errors,
                "errors": errors,
                "warnings": list(record_result.warnings),
            }
        )
    _write_json(
        output_path,
        {
            "schema_version": 1,
            "target_date": target_date,
            "source_ledger": ledger_result.as_dict(),
            "results": results,
        },
        "market_validation.schema.json",
    )
    return 0


def _screen_market(
    market_data_path: Path,
    sources_path: Path,
    output_path: Path,
    config_path: Path,
    source_matrix_path: Path,
    market_research_path: Path | None,
) -> int:
    (
        target_date,
        records,
        ledger_result,
        source_payload,
        source_matrix,
    ) = _load_market_bundle(market_data_path, sources_path, source_matrix_path)
    if not ledger_result.valid:
        raise ValueError("Source ledger validation failed: " + "; ".join(ledger_result.errors))
    alignment_result = _load_market_research_alignment(
        records,
        market_research_path,
        source_matrix,
        source_payload,
    )
    if alignment_result is not None and not alignment_result.valid:
        raise ValueError(
            "Market research alignment failed: "
            + "; ".join(_alignment_error_messages(alignment_result))
        )
    config = load_strategy_config(config_path)
    results = [
        screen_market_record(record, config, source_matrix).as_dict()
        for record in records
    ]
    _write_json(
        output_path,
        {
            "schema_version": 1,
            "target_date": target_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "candidates": results,
        },
        "candidates.schema.json",
    )
    return 0


def _risk_check(
    recommendation_path: Path,
    candidates_path: Path,
    candidate_pipeline_path: Path,
    market_data_path: Path,
    sources_path: Path,
    output_path: Path,
    config_path: Path,
    current_positions: int | None,
    trades_today: int | None,
    source_matrix_path: Path,
    market_research_path: Path | None,
    selection_path: Path | None = None,
    ranking_path: Path | None = None,
    event_gate_path: Path | None = None,
    research_window_path: Path | None = None,
) -> int:
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    candidate_pipeline = load_json_document(
        candidate_pipeline_path,
        "candidate_pipeline.schema.json",
    )
    validate_recommendation_candidate_link(recommendation, candidates)
    validate_recommendation_pipeline_link(recommendation, candidate_pipeline)
    decision = recommendation.get("decision")
    if decision == "TRADE" and (current_positions is None or trades_today is None):
        raise ValueError(
            "current_positions and trades_today are required when "
            "recommendation decision is TRADE"
        )

    config = load_strategy_config(config_path)
    config_digest = strategy_config_sha256(config)
    if recommendation["strategy_version"] != config["strategy_version"]:
        raise ValueError("recommendation strategy_version does not match --config")
    if recommendation["config_sha256"] != config_digest:
        raise ValueError("recommendation config_sha256 does not match --config")

    # Config v6 introduces Selection as the gate between Ranking and
    # Recommendation. A v6 TRADE recommendation must be Selection-driven
    # (Recommendation v2, schema_version == 2) -- a v1 (non-Selection-driven)
    # TRADE is never accepted under v6, regardless of whether --selection was
    # supplied, so this check runs first and unconditionally.
    config_schema_version = config.get("config_schema_version")
    recommendation_schema_version = recommendation.get("schema_version")
    if (
        config_schema_version == 6
        and decision == "TRADE"
        and recommendation_schema_version != 2
    ):
        raise ValueError(
            "RISK_SELECTION_RECOMMENDATION_VERSION_INVALID: a TRADE recommendation built "
            "under a config_schema_version 6 config must be Selection-driven "
            f"(recommendation.schema_version == 2); got schema_version={recommendation_schema_version!r}"
        )

    # For a config_schema_version 6 Selection-driven recommendation
    # (schema_version == 2, covering both TRADE and NO_TRADE outcomes),
    # --selection and --ranking are both required and Risk independently
    # recomputes the real Selection trust chain: raw-byte SHA256 of
    # ranking.json/selection.json/the config snapshot,
    # validate_selection_preconditions, validate_selection_output_contract
    # (recompute-and-compare against a forged selection.json), and
    # validate_recommendation_selection_link using the ACTUAL selection.json
    # SHA256 (never the string recorded inside recommendation.json). Risk
    # never re-derives a decision from Ranking on its own, and a Risk
    # REJECTED never falls back to any other candidate (Selection already
    # only ever considered Rank 1, and Risk cannot second-guess that).
    selection_driven_v6 = config_schema_version == 6 and recommendation_schema_version == 2
    selection: dict[str, Any] | None = None
    if selection_driven_v6:
        if selection_path is None:
            raise ValueError(
                "RISK_SELECTION_REQUIRED: --selection is required for a config_schema_version 6 "
                "Selection-driven (recommendation.schema_version == 2) recommendation"
            )
        if ranking_path is None:
            raise ValueError(
                "RISK_RANKING_REQUIRED: --ranking is required for a config_schema_version 6 "
                "Selection-driven (recommendation.schema_version == 2) recommendation"
            )
        if event_gate_path is None:
            raise ValueError(
                "RISK_SELECTION_EVENT_GATE_REQUIRED: --event-gate is required for a "
                "config_schema_version 6 Selection-driven (recommendation.schema_version == 2) "
                "recommendation"
            )
        if research_window_path is None:
            raise ValueError(
                "RISK_SELECTION_RESEARCH_WINDOW_REQUIRED: --research-window is required for a "
                "config_schema_version 6 Selection-driven (recommendation.schema_version == 2) "
                "recommendation"
            )

        # Full re-verification of the Ranking Trust Chain BEFORE any of
        # Selection's own business logic is trusted -- mirrors
        # build-selection/build-selection-recommendation exactly, via the
        # SAME shared helper, so Risk can never be tricked by a forged
        # ranking.json whose downstream selection/recommendation hashes were
        # also faked to stay self-consistent.
        bundle = load_and_verify_ranking_trust_chain(
            ranking_path=ranking_path,
            event_gate_path=event_gate_path,
            candidates_path=candidates_path,
            market_data_path=market_data_path,
            sources_path=sources_path,
            source_matrix_path=source_matrix_path,
            config_path=config_path,
        )
        ranking = bundle.ranking

        ranking_sha256 = _sha256_bytes(ranking_path.read_bytes())
        selection_sha256 = _sha256_bytes(selection_path.read_bytes())
        strategy_snapshot_sha256 = bundle.actual_ranking_input_hashes["strategy_snapshot_sha256"]

        selection = load_json_document(selection_path, "selection.schema.json")

        actual_selection_input_hashes = {
            "ranking_sha256": ranking_sha256,
            "strategy_snapshot_sha256": strategy_snapshot_sha256,
        }
        validate_selection_preconditions(
            ranking=ranking,
            config=config,
            input_hashes=actual_selection_input_hashes,
        )
        validate_selection_output_contract(
            selection=selection,
            ranking=ranking,
            config=config,
            input_hashes=actual_selection_input_hashes,
        )
        validate_recommendation_selection_link(
            recommendation=recommendation,
            selection=selection,
            selection_sha256=selection_sha256,
        )

        research_window = load_json_document(
            research_window_path, "research_window.schema.json"
        )
        selection_candidates = load_json_document(candidates_path, "candidates.schema.json")
        selection_candidate_pipeline = load_json_document(
            candidate_pipeline_path, "candidate_pipeline.schema.json"
        )
        selection_market_data = load_json_document(market_data_path, "market_data.schema.json")
        selection_source_payload = load_json_document(sources_path, "sources.schema.json")

        # Broad cross-artifact consistency check across ALL of
        # build-selection-recommendation's inputs (not just what feeds the
        # recompute-and-compare below): catches mismatches like
        # research_window.previous_trading_day/target_date not matching
        # selection.json, or a candidate_pipeline that is not actually
        # complete -- fields build_selection_recommendation() itself never
        # consumes, so the recompute-and-compare check below cannot see them.
        validate_selection_recommendation_preconditions(
            selection=selection,
            ranking=ranking,
            candidates=selection_candidates,
            candidate_pipeline=selection_candidate_pipeline,
            market_data=selection_market_data,
            research_window=research_window,
            source_payload=selection_source_payload,
            config=config,
        )

        # Recompute-and-compare the entire Selection Recommendation itself:
        # catches a Recommendation whose Selection-link fields are each
        # individually correct but where some OTHER field (notes,
        # source_urls, order values, selection_reasons, ...) was
        # independently hand-tampered. Runs for BOTH TRADE and NO_TRADE
        # Selection-driven recommendations -- proving the NO_TRADE
        # recommendation itself is genuine before Risk even says
        # NOT_APPLICABLE.
        validate_selection_recommendation_output_contract(
            recommendation=recommendation,
            selection=selection,
            candidates=selection_candidates,
            candidate_pipeline=selection_candidate_pipeline,
            market_data=selection_market_data,
            research_window=research_window,
            source_payload=selection_source_payload,
            config=config,
            selection_sha256=selection_sha256,
        )
    elif selection_path is not None:
        # Non-Selection-driven caller (pre-v6 config, or a v6 recommendation
        # that is not schema_version 2 -- already rejected above for TRADE)
        # still passed --selection: keep the previous, superficial
        # cross-checks for backward compatibility.
        selection = load_json_document(selection_path, "selection.schema.json")
        if decision == "TRADE":
            if selection.get("selection_status") != "SELECTED":
                raise ValueError(
                    "RISK_SELECTION_STATUS_MISMATCH: --selection must be SELECTED for a TRADE recommendation"
                )
            if selection.get("selected_ticker") != recommendation.get("ticker"):
                raise ValueError(
                    "RISK_SELECTION_TICKER_MISMATCH: --selection selected_ticker does not match recommendation.ticker"
                )
            if selection.get("config_sha256") != recommendation.get("config_sha256"):
                raise ValueError(
                    "RISK_SELECTION_CONFIG_SHA256_MISMATCH: --selection config_sha256 does not match recommendation"
                )
        else:
            if selection.get("selection_status") == "SELECTED":
                raise ValueError(
                    "RISK_SELECTION_STATUS_MISMATCH: --selection is SELECTED but recommendation decision is not TRADE"
                )

    # Config v6 Case A/B (Terminal Recommendation) trust chain: a v6
    # recommendation.schema_version == 1 whose decision is NO_TRADE or
    # DATA_UNAVAILABLE was produced by build-ranking-terminal-recommendation,
    # NOT the Selection-driven Case C path. Risk independently re-derives and
    # re-verifies the ENTIRE Ranking trust chain here -- raw-byte hashes of
    # every upstream artifact, the same Ranking Contract functions
    # build-ranking/build-ranking-terminal-recommendation themselves call,
    # and a deterministic recomputation of the Terminal Recommendation itself
    # -- so a hand-written, merely schema-valid recommendation.json can never
    # bypass Risk without a genuine Ranking -> Terminal-Builder chain having
    # actually run. Mirrors build-ranking-terminal-recommendation's exact
    # fixed processing order.
    terminal_driven_v6 = (
        config_schema_version == 6
        and recommendation_schema_version == 1
        and decision in ("NO_TRADE", "DATA_UNAVAILABLE")
    )
    if terminal_driven_v6:
        if ranking_path is None:
            raise ValueError(
                "RISK_TERMINAL_RANKING_REQUIRED: --ranking is required for a config_schema_version 6 "
                "Terminal (Case A/B, recommendation.schema_version == 1, "
                "decision in {NO_TRADE, DATA_UNAVAILABLE}) recommendation"
            )
        if event_gate_path is None:
            raise ValueError(
                "RISK_TERMINAL_EVENT_GATE_REQUIRED: --event-gate is required for a config_schema_version 6 "
                "Terminal (Case A/B) recommendation"
            )
        if research_window_path is None:
            raise ValueError(
                "RISK_TERMINAL_RESEARCH_WINDOW_REQUIRED: --research-window is required for a "
                "config_schema_version 6 Terminal (Case A/B) recommendation"
            )

        actual_ranking_input_hashes = {
            "event_gate_sha256": _sha256_bytes(event_gate_path.read_bytes()),
            "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
            "market_data_sha256": _sha256_bytes(market_data_path.read_bytes()),
            "sources_sha256": _sha256_bytes(sources_path.read_bytes()),
            "source_matrix_sha256": _sha256_bytes(source_matrix_path.read_bytes()),
            "strategy_snapshot_sha256": _sha256_bytes(config_path.read_bytes()),
        }

        ranking = load_json_document(ranking_path, "ranking.schema.json")
        event_gate = load_json_document(event_gate_path, "event_gate.schema.json")
        terminal_market_data = load_json_document(market_data_path, "market_data.schema.json")
        research_window = load_json_document(
            research_window_path, "research_window.schema.json"
        )
        terminal_source_payload = load_json_document(sources_path, "sources.schema.json")
        terminal_source_matrix = load_source_matrix(source_matrix_path)
        validate_source_matrix(terminal_source_matrix)

        if ranking.get("input_hashes") != actual_ranking_input_hashes:
            mismatched_keys = sorted(
                key
                for key in set(actual_ranking_input_hashes)
                | set(ranking.get("input_hashes") or {})
                if (ranking.get("input_hashes") or {}).get(key)
                != actual_ranking_input_hashes.get(key)
            )
            raise ValueError(
                "RISK_TERMINAL_INPUT_HASHES_MISMATCH: ranking.input_hashes does not match the "
                "actual SHA256 of the --event-gate/--candidates/--market-data/--sources/"
                "--source-matrix/--config files supplied here; mismatched keys: "
                + ", ".join(mismatched_keys)
            )

        validate_ranking_preconditions(
            event_gate=event_gate,
            candidates=candidates,
            market_data=terminal_market_data,
            source_payload=terminal_source_payload,
            source_matrix=terminal_source_matrix,
            config=config,
            input_hashes=actual_ranking_input_hashes,
            source_base_dir=sources_path.parent,
        )
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=terminal_market_data,
            source_payload=terminal_source_payload,
            config=config,
        )
        validate_ranking_terminal_recommendation_preconditions(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            candidate_pipeline=candidate_pipeline,
            market_data=terminal_market_data,
            research_window=research_window,
            source_payload=terminal_source_payload,
            config=config,
        )
        validate_ranking_terminal_recommendation_output_contract(
            recommendation=recommendation,
            ranking=ranking,
            candidate_pipeline=candidate_pipeline,
            research_window=research_window,
            config=config,
        )

    (
        market_target_date,
        records,
        ledger_result,
        source_payload,
        source_matrix,
    ) = _load_market_bundle(market_data_path, sources_path, source_matrix_path)
    alignment_result = _load_market_research_alignment(
        records,
        market_research_path,
        source_matrix,
        source_payload,
    )
    validate_recommendation_sources(recommendation, source_payload)

    if decision != "TRADE":
        result = not_applicable_result(None)
    else:
        ticker = str(recommendation.get("ticker", "")).strip()
        matches = [record for record in records if record.ticker == ticker]
        market_record = matches[0] if len(matches) == 1 else None
        alignment_valid = True
        if alignment_result is not None:
            alignment_valid = (
                not alignment_result.global_errors
                and not alignment_result.errors_by_ticker.get(ticker)
            )
        market_valid = (
            ledger_result.valid
            and alignment_valid
            and validate_market_data(market_record, source_matrix).valid_for_trade
            if market_record is not None
            else False
        )
        proposal = OrderProposal.from_dict(recommendation)
        result = evaluate_order(
            proposal,
            config,
            market_data_valid=market_valid,
            market_target_date=market_target_date,
            market_previous_high=(market_record.previous_high if market_record else None),
            market_tick_size=(market_record.tick_size if market_record else None),
            current_positions=current_positions,
            trades_today=trades_today,
        )
    payload = {
        "schema_version": 1,
        "target_date": recommendation.get("target_date"),
        "decision": decision,
        "strategy_version": recommendation["strategy_version"],
        "config_sha256": recommendation["config_sha256"],
        **result.as_dict(),
    }
    _write_json(output_path, payload, "risk_result.schema.json")
    return 0


def _build_candidate_pipeline(
    market_research_path: Path,
    market_data_path: Path,
    candidates_path: Path,
    sources_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    load_json_document(market_data_path, "market_data.schema.json")
    market_target_date, records = load_market_data(market_data_path)
    assert market_target_date is not None
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    sources = load_json_document(sources_path, "sources.schema.json")
    config = load_strategy_config(config_path)
    validate_candidate_pipeline_inputs(
        market_research=market_research,
        market_target_date=market_target_date,
        candidates=candidates,
        source_payload=sources,
        config=config,
    )
    payload = build_candidate_pipeline(
        market_research=market_research,
        market_records=records,
        candidates_payload=candidates,
        source_payload=sources,
        config=config,
    )
    _write_json(output_path, payload, "candidate_pipeline.schema.json")
    return 0


def _build_performance(
    market_research_path: Path,
    candidate_pipeline_path: Path,
    sources_path: Path,
    output_path: Path,
    timings_path: Path | None,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    candidate_pipeline = load_json_document(
        candidate_pipeline_path,
        "candidate_pipeline.schema.json",
    )
    sources = load_json_document(sources_path, "sources.schema.json")
    validate_performance_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=sources,
    )
    timings = _load_unvalidated_json_object(timings_path) if timings_path else None
    payload = build_performance_payload(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=sources,
        timings_payload=timings,
    )
    _write_json(output_path, payload, "performance.schema.json")
    return 0


def _resolve_research_window(
    target_date: str,
    previous_trading_day: str,
    runs_dir: Path,
    source_matrix_path: Path,
    output_path: Path | None,
) -> int:
    source_matrix = load_source_matrix(source_matrix_path)
    payload = resolve_research_window(
        target_date=target_date,
        previous_trading_day=previous_trading_day,
        runs_dir=runs_dir,
        source_matrix=source_matrix,
    )
    _write_json(output_path, payload.as_dict(), "research_window.schema.json")
    return 0


def _render_report(
    recommendation_path: Path,
    risk_result_path: Path,
    output_path: Path,
) -> int:
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    risk_result = load_json_document(risk_result_path, "risk_result.schema.json")
    validate_recommendation_risk_link(recommendation, risk_result)
    atomic_write_text(
        output_path,
        render_sbi_report(recommendation, risk_result),
    )
    return 0


def _render_research(
    market_research_path: Path,
    candidate_pipeline_path: Path,
    sources_path: Path,
    performance_path: Path,
    output_path: Path,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    candidate_pipeline = load_json_document(
        candidate_pipeline_path,
        "candidate_pipeline.schema.json",
    )
    sources = load_json_document(sources_path, "sources.schema.json")
    performance = load_json_document(performance_path, "performance.schema.json")
    validate_research_report_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=sources,
        performance=performance,
    )
    atomic_write_text(
        output_path,
        render_research_report(
            market_research,
            candidate_pipeline,
            sources,
            performance,
        ),
    )
    return 0


def _render_daily_report(
    market_research_path: Path,
    candidate_pipeline_path: Path,
    sources_path: Path,
    performance_path: Path,
    recommendation_path: Path,
    risk_result_path: Path,
    output_path: Path,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    candidate_pipeline = load_json_document(
        candidate_pipeline_path,
        "candidate_pipeline.schema.json",
    )
    sources = load_json_document(sources_path, "sources.schema.json")
    performance = load_json_document(performance_path, "performance.schema.json")
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    risk_result = load_json_document(risk_result_path, "risk_result.schema.json")
    validate_daily_report_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=sources,
        performance=performance,
        recommendation=recommendation,
        risk_result=risk_result,
    )
    atomic_write_text(
        output_path,
        render_daily_report(
            market_research,
            candidate_pipeline,
            sources,
            performance,
            recommendation,
            risk_result,
        ),
    )
    return 0


def _validate_source_matrix(
    source_matrix_path: Path,
    output_path: Path | None,
) -> int:
    from src.config import load_yaml

    payload = load_yaml(source_matrix_path)
    result = validate_source_matrix(payload)
    _emit_json(result.as_dict(), output_path)
    if not result.valid:
        raise ValueError("Source matrix validation failed: " + "; ".join(result.errors))
    return 0


def _validate_market_research(
    market_research_path: Path,
    research_window_path: Path,
    sources_path: Path | None,
    source_matrix_path: Path,
    output_path: Path,
) -> int:
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    research_window = load_json_document(
        research_window_path,
        "research_window.schema.json",
    )
    source_payload = (
        load_json_document(sources_path, "sources.schema.json")
        if sources_path is not None
        else None
    )
    source_matrix = load_source_matrix(source_matrix_path)
    result = validate_market_research(market_research, source_matrix, source_payload)
    window_link_errors = validate_market_research_window_link(
        market_research,
        research_window,
    )
    if window_link_errors:
        result = MarketResearchValidationResult(
            False,
            result.discovery_complete,
            (*result.errors, *window_link_errors),
            result.warnings,
        )
    _write_json(
        output_path,
        result.as_dict(),
        "market_research_validation.schema.json",
    )
    if not result.valid:
        raise ValueError("Market research validation failed: " + "; ".join(result.errors))
    return 0


def _audit_official_ohlcv(
    market_data_path: Path,
    sources_path: Path,
    output_path: Path,
    source_matrix_path: Path,
) -> int:
    target_date, records, _, source_payload, _ = _load_market_bundle(
        market_data_path,
        sources_path,
        source_matrix_path,
    )
    _write_json(
        output_path,
        {
            "schema_version": 1,
            "target_date": target_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "results": audit_official_ohlcv(records, source_payload),
        },
        "official_ohlcv_audit.schema.json",
    )
    return 0


def _record_recommendation(
    recommendation_path: Path,
    risk_result_path: Path,
    csv_path: Path | None,
) -> int:
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    risk_result = load_json_document(risk_result_path, "risk_result.schema.json")
    validate_recommendation_risk_link(recommendation, risk_result)
    row = recommendation_to_row(recommendation, risk_result)
    if csv_path is None:
        append_recommendation(row)
    else:
        append_recommendation(row, csv_path)
    return 0


def _load_execution_row(
    execution_path: Path,
    recommendation_path: Path,
    risk_result_path: Path,
    market_data_path: Path,
) -> dict[str, str]:
    execution = load_json_document(execution_path, "execution_result.schema.json")
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    risk_result = load_json_document(risk_result_path, "risk_result.schema.json")
    load_json_document(market_data_path, "market_data.schema.json")
    market_target_date, market_records = load_market_data(market_data_path)
    assert market_target_date is not None
    return build_trade_row(
        execution,
        recommendation,
        risk_result,
        market_target_date,
        market_records,
    )


def _load_market_bundle(
    market_data_path: Path,
    sources_path: Path,
    source_matrix_path: Path = DEFAULT_SOURCE_MATRIX_PATH,
) -> tuple[
    str,
    list[MarketDataRecord],
    SourceLedgerValidationResult,
    dict[str, Any],
    dict[str, Any],
]:
    source_matrix = load_source_matrix(source_matrix_path)
    load_json_document(market_data_path, "market_data.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")
    target_date, records = load_market_data(market_data_path)
    assert target_date is not None
    ledger_result = validate_source_ledger(
        target_date,
        records,
        source_payload,
        source_matrix,
        source_base_dir=sources_path.parent,
    )
    return target_date, records, ledger_result, source_payload, source_matrix


def _load_unvalidated_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as json_file:
        payload = json.load(json_file)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def _load_market_research_alignment(
    records: list[MarketDataRecord],
    market_research_path: Path | None,
    source_matrix: dict[str, Any],
    source_payload: dict[str, Any] | None = None,
) -> MarketDataResearchAlignmentResult | None:
    if market_research_path is None:
        return None
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    research_result = validate_market_research(
        market_research,
        source_matrix,
        source_payload,
    )
    alignment_result = validate_market_records_against_research(records, market_research)
    return MarketDataResearchAlignmentResult(
        research_result.valid and alignment_result.valid,
        (*research_result.errors, *alignment_result.global_errors),
        alignment_result.errors_by_ticker,
    )


def _alignment_error_messages(
    result: MarketDataResearchAlignmentResult,
) -> list[str]:
    messages = list(result.global_errors)
    for ticker, errors in sorted(result.errors_by_ticker.items()):
        messages.extend(f"{ticker}: {error}" for error in errors)
    return messages


def _init_event_research(
    candidate_pipeline_path: Path,
    candidates_path: Path,
    previous_trading_day: str,
    config_path: Path,
    output_path: Path,
) -> int:
    candidate_pipeline = load_json_document(candidate_pipeline_path, "candidate_pipeline.schema.json")
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    config = load_strategy_config(config_path)
    validate_event_research_inputs(
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        config=config,
    )
    input_hashes = {
        "candidate_pipeline_sha256": _sha256_bytes(candidate_pipeline_path.read_bytes()),
        "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
        "strategy_snapshot_sha256": _sha256_bytes(config_path.read_bytes()),
    }
    payload = init_event_research_payload(
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        config=config,
        previous_trading_day=previous_trading_day,
        input_hashes=input_hashes,
    )
    _write_json(output_path, payload, "event_research.schema.json")
    return 0


def _validate_event_research(
    event_research_path: Path,
    candidate_pipeline_path: Path,
    candidates_path: Path,
    sources_path: Path,
    config_path: Path,
    output_path: Path | None,
) -> int:
    event_research = load_json_document(event_research_path, "event_research.schema.json")
    candidate_pipeline = load_json_document(candidate_pipeline_path, "candidate_pipeline.schema.json")
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")
    config = load_strategy_config(config_path)
    validate_event_research_inputs(
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        config=config,
    )
    validate_event_research_contract(event_research=event_research, source_payload=source_payload)
    _emit_json({"status": "VALID"}, output_path)
    return 0


def _build_event_gate(
    event_research_path: Path,
    candidate_pipeline_path: Path,
    candidates_path: Path,
    sources_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    event_research = load_json_document(event_research_path, "event_research.schema.json")
    candidate_pipeline = load_json_document(candidate_pipeline_path, "candidate_pipeline.schema.json")
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")
    config = load_strategy_config(config_path)
    input_hashes = {
        "event_research_sha256": _sha256_bytes(event_research_path.read_bytes()),
        "candidate_pipeline_sha256": _sha256_bytes(candidate_pipeline_path.read_bytes()),
        "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
        "sources_sha256": _sha256_bytes(sources_path.read_bytes()),
        "strategy_snapshot_sha256": _sha256_bytes(config_path.read_bytes()),
    }
    validate_event_gate_inputs(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=input_hashes,
    )
    payload = build_event_gate(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=input_hashes,
    )
    validate_json_document(payload, "event_gate.schema.json")
    validate_event_gate_output_contract(event_gate=payload, event_research=event_research)
    _write_json(output_path, payload, "event_gate.schema.json")
    return 0


def _build_ranking(
    event_gate_path: Path,
    candidates_path: Path,
    market_data_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    # Fixed processing order: hash inputs -> load/validate schemas ->
    # validate source ledger -> validate_ranking_preconditions ->
    # build_ranking -> validate against ranking.schema.json ->
    # validate_ranking_output_contract -> atomic write.
    input_hashes = {
        "event_gate_sha256": _sha256_bytes(event_gate_path.read_bytes()),
        "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
        "market_data_sha256": _sha256_bytes(market_data_path.read_bytes()),
        "sources_sha256": _sha256_bytes(sources_path.read_bytes()),
        "source_matrix_sha256": _sha256_bytes(source_matrix_path.read_bytes()),
        "strategy_snapshot_sha256": _sha256_bytes(config_path.read_bytes()),
    }

    event_gate = load_json_document(event_gate_path, "event_gate.schema.json")
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    market_data = load_json_document(market_data_path, "market_data.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")
    source_matrix = load_source_matrix(source_matrix_path)
    config = load_strategy_config(config_path)

    validate_ranking_preconditions(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        source_matrix=source_matrix,
        config=config,
        input_hashes=input_hashes,
        source_base_dir=sources_path.parent,
    )

    payload = build_ranking(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
        input_hashes=input_hashes,
    )
    validate_json_document(payload, "ranking.schema.json")
    validate_ranking_output_contract(
        ranking=payload,
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
    )
    # Never overwrite an existing ranking.json on hard error: this point is
    # only reached for the two valid business outcomes (COMPLETE or
    # DATA_UNAVAILABLE); any exception above aborts before this write.
    _write_json(output_path, payload, "ranking.schema.json")
    return 0


def _build_selection(
    ranking_path: Path,
    event_gate_path: Path,
    candidates_path: Path,
    market_data_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    # Fixed processing order:
    #  1. hash ranking.json's raw bytes (this is Selection's own
    #     input_hashes.ranking_sha256 -- logically separate from the
    #     Ranking-input-hash-chain re-verified in step 2)
    #  2. load_and_verify_ranking_trust_chain(): full re-verification that
    #     ranking.json was genuinely produced from its own claimed upstream
    #     artifacts (raw-byte hashes, schema-validating loads,
    #     validate_ranking_preconditions, validate_ranking_output_contract).
    #     Any failure here is a Hard Error -- selection.json is never
    #     written past this point.
    #  3. hash the strategy config snapshot bytes (reused from the bundle)
    #  4. build Selection's input_hashes = {ranking_sha256, strategy_snapshot_sha256}
    #     (unchanged 2-key shape -- no new keys added)
    #  5. validate_selection_preconditions (config schema v6, selection block present, hash key set)
    #  6. build_selection (Rank-1-only, no fallback)
    #  7. schema-validate the resulting payload
    #  8. validate_selection_output_contract (recompute-and-compare)
    #  9. atomic write only on full success
    #
    # Selection-disabled nightly runs never reach build_selection's success
    # path (SelectionHardError SELECTION_CONFIG_DISABLED aborts first) and
    # therefore never produce a selection.json file.
    ranking_sha256 = _sha256_bytes(ranking_path.read_bytes())

    bundle = load_and_verify_ranking_trust_chain(
        ranking_path=ranking_path,
        event_gate_path=event_gate_path,
        candidates_path=candidates_path,
        market_data_path=market_data_path,
        sources_path=sources_path,
        source_matrix_path=source_matrix_path,
        config_path=config_path,
    )

    input_hashes = {
        "ranking_sha256": ranking_sha256,
        "strategy_snapshot_sha256": bundle.actual_ranking_input_hashes["strategy_snapshot_sha256"],
    }

    ranking = bundle.ranking
    config = bundle.config

    validate_selection_preconditions(
        ranking=ranking,
        config=config,
        input_hashes=input_hashes,
    )

    payload = build_selection(
        ranking=ranking,
        config=config,
        input_hashes=input_hashes,
    )
    validate_json_document(payload, "selection.schema.json")
    validate_selection_output_contract(
        selection=payload,
        ranking=ranking,
        config=config,
        input_hashes=input_hashes,
    )
    _write_json(output_path, payload, "selection.schema.json")
    return 0


def _build_selection_calibration(
    runs_dir: Path,
    config_path: Path,
    source_matrix_path: Path,
    source_matrix_registry_dir: Path | None,
    start_date: str | None,
    end_date: str | None,
    output_path: Path,
) -> int:
    from src.selection_calibration import build_selection_calibration_report

    resolved_output = output_path.resolve()
    if {"runs", "trades"} & set(resolved_output.parts):
        raise ValueError(
            "CALIBRATION_OUTPUT_LOCATION_FORBIDDEN: calibration output must not be "
            "written under a 'runs/' or 'trades/' directory"
        )

    payload = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=config_path,
        source_matrix_path=source_matrix_path,
        source_matrix_registry_dir=source_matrix_registry_dir,
        start_date=start_date,
        end_date=end_date,
    )
    _write_json(output_path, payload, "selection_calibration.schema.json")
    return 0


def _evaluate_selection_thresholds(
    calibration_report_path: Path,
    minimum_turnover_yen: int,
    maximum_relative_tick_numerator: int,
    maximum_relative_tick_denominator: int,
    output_path: Path,
) -> int:
    from src.selection_calibration import evaluate_calibration_against_thresholds

    calibration_bytes = calibration_report_path.read_bytes()
    source_calibration_sha256 = _sha256_bytes(calibration_bytes)
    calibration_report = load_json_document(
        calibration_report_path, "selection_calibration.schema.json"
    )

    payload = evaluate_calibration_against_thresholds(
        calibration_report=calibration_report,
        source_calibration_sha256=source_calibration_sha256,
        minimum_turnover_yen=minimum_turnover_yen,
        maximum_relative_tick_numerator=maximum_relative_tick_numerator,
        maximum_relative_tick_denominator=maximum_relative_tick_denominator,
    )
    _write_json(output_path, payload, "selection_threshold_evaluation.schema.json")
    return 0


def _build_selection_recommendation(
    ranking_path: Path,
    selection_path: Path,
    event_gate_path: Path,
    candidates_path: Path,
    candidate_pipeline_path: Path,
    market_data_path: Path,
    research_window_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    from src.selection_recommendation import build_selection_recommendation

    # Fixed processing order (closes the trust-chain bypass):
    #  1. hash selection.json's raw bytes (Selection's own input_hashes-chain
    #     hash AND recommendation.selection_sha256 -- kept separate)
    #  2. load_and_verify_ranking_trust_chain(): full re-verification that
    #     ranking.json was genuinely produced from its own claimed upstream
    #     artifacts. This MUST run before Selection's own hash-chain check --
    #     a forged ranking.json that an attacker also made selection.json
    #     self-consistent with must never slip past Ranking verification.
    #  3. schema-validate selection.json
    #  4. build the ACTUAL Selection input_hashes = {ranking_sha256,
    #     strategy_snapshot_sha256} (ranking_sha256 computed independently,
    #     strategy_snapshot_sha256 reused from the bundle)
    #  5. assert selection.input_hashes matches exactly
    #  6. validate_selection_preconditions
    #  7. validate_selection_output_contract (recompute-and-compare, catches
    #     a hand-forged selection.json even with self-consistent hashes)
    #  8. load candidate_pipeline.json / research_window.json
    #  9. validate_selection_recommendation_preconditions (broad
    #     cross-artifact check)
    #  10. build_selection_recommendation (pure function)
    #  11. schema-validate the resulting payload
    #  12. validate_recommendation_selection_link
    #  13. atomic write only on full success
    selection_sha256 = _sha256_bytes(selection_path.read_bytes())

    bundle = load_and_verify_ranking_trust_chain(
        ranking_path=ranking_path,
        event_gate_path=event_gate_path,
        candidates_path=candidates_path,
        market_data_path=market_data_path,
        sources_path=sources_path,
        source_matrix_path=source_matrix_path,
        config_path=config_path,
    )
    ranking = bundle.ranking
    config = bundle.config

    selection = load_json_document(selection_path, "selection.schema.json")

    actual_input_hashes = {
        "ranking_sha256": _sha256_bytes(ranking_path.read_bytes()),
        "strategy_snapshot_sha256": bundle.actual_ranking_input_hashes["strategy_snapshot_sha256"],
    }
    if selection.get("input_hashes") != actual_input_hashes:
        raise ValueError(
            "SELECTION_RECOMMENDATION_INPUT_HASHES_MISMATCH: selection.input_hashes does not "
            "match the actual SHA256 of the --ranking file / --config snapshot supplied here"
        )

    validate_selection_preconditions(
        ranking=ranking,
        config=config,
        input_hashes=actual_input_hashes,
    )
    validate_selection_output_contract(
        selection=selection,
        ranking=ranking,
        config=config,
        input_hashes=actual_input_hashes,
    )

    candidates = load_json_document(candidates_path, "candidates.schema.json")
    candidate_pipeline = load_json_document(
        candidate_pipeline_path, "candidate_pipeline.schema.json"
    )
    market_data = load_json_document(market_data_path, "market_data.schema.json")
    research_window = load_json_document(research_window_path, "research_window.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")

    if selection.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("selection strategy_version does not match --config")
    if selection.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("selection config_sha256 does not match --config")

    # P1-4: broad cross-artifact consistency check across ALL inputs (not
    # just selection/ranking), run after the P0-2 checks and before
    # build_selection_recommendation() is invoked.
    validate_selection_recommendation_preconditions(
        selection=selection,
        ranking=ranking,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        research_window=research_window,
        source_payload=source_payload,
        config=config,
    )

    payload = build_selection_recommendation(
        selection=selection,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        research_window=research_window,
        source_payload=source_payload,
        config=config,
        selection_sha256=selection_sha256,
    )
    validate_json_document(payload, "recommendation.schema.json")
    validate_recommendation_selection_link(
        recommendation=payload,
        selection=selection,
        selection_sha256=selection_sha256,
    )
    validate_selection_recommendation_output_contract(
        recommendation=payload,
        selection=selection,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        research_window=research_window,
        source_payload=source_payload,
        config=config,
        selection_sha256=selection_sha256,
    )
    _write_json(output_path, payload, "recommendation.schema.json")
    return 0


def _build_ranking_terminal_recommendation(
    ranking_path: Path,
    event_gate_path: Path,
    candidates_path: Path,
    candidate_pipeline_path: Path,
    market_data_path: Path,
    research_window_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    from src.ranking_terminal_recommendation import build_ranking_terminal_recommendation

    # Fixed 13-step processing order -- brings Case A/B up to the same
    # trust-chain rigor as Case C (build-selection-recommendation):
    #  1. raw-byte SHA256 of every claimed Ranking input (never re-serialize)
    #  2. schema-validating load of every JSON artifact
    #  3. load+validate source_matrix.yaml
    #  4. load the strategy config snapshot
    #  5. assert ranking.input_hashes == the actual hashes computed in step 1
    #     (full dict equality, not a subset check)
    #  6. validate_ranking_preconditions (the SAME function build-ranking
    #     itself calls, with source_base_dir set so Source Ledger file
    #     existence is re-checked)
    #  7. validate_ranking_output_contract (catches a schema-valid but
    #     hand-edited ranking.json)
    #  8. validate_ranking_terminal_recommendation_preconditions (P1-4-style
    #     broad cross-artifact check: target_date, previous_trading_day,
    #     strategy_version, config_sha256, pipeline/screening completeness)
    #  9. determine_ranking_terminal_case (Case A / Case B / wrong-tool) --
    #     only reached after the full Ranking Contract is re-verified, so a
    #     forged ranking_status can never be trusted before its provenance
    #     is proven
    #  10. build_ranking_terminal_recommendation() (pure function)
    #  11. schema-validate the resulting payload
    #  12. validate_recommendation_candidate_link / _pipeline_link / _sources
    #  13. atomic write only on full success
    actual_ranking_input_hashes = {
        "event_gate_sha256": _sha256_bytes(event_gate_path.read_bytes()),
        "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
        "market_data_sha256": _sha256_bytes(market_data_path.read_bytes()),
        "sources_sha256": _sha256_bytes(sources_path.read_bytes()),
        "source_matrix_sha256": _sha256_bytes(source_matrix_path.read_bytes()),
        "strategy_snapshot_sha256": _sha256_bytes(config_path.read_bytes()),
    }

    ranking = load_json_document(ranking_path, "ranking.schema.json")
    event_gate = load_json_document(event_gate_path, "event_gate.schema.json")
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    candidate_pipeline = load_json_document(
        candidate_pipeline_path, "candidate_pipeline.schema.json"
    )
    market_data = load_json_document(market_data_path, "market_data.schema.json")
    research_window = load_json_document(research_window_path, "research_window.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")

    source_matrix = load_source_matrix(source_matrix_path)
    validate_source_matrix(source_matrix)

    config = load_strategy_config(config_path)

    if ranking.get("input_hashes") != actual_ranking_input_hashes:
        mismatched_keys = sorted(
            key
            for key in set(actual_ranking_input_hashes) | set(ranking.get("input_hashes") or {})
            if (ranking.get("input_hashes") or {}).get(key) != actual_ranking_input_hashes.get(key)
        )
        raise ValueError(
            "RANKING_TERMINAL_INPUT_HASHES_MISMATCH: ranking.input_hashes does not match the "
            "actual SHA256 of the --event-gate/--candidates/--market-data/--sources/"
            "--source-matrix/--config files supplied here; mismatched keys: "
            + ", ".join(mismatched_keys)
        )

    validate_ranking_preconditions(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        source_matrix=source_matrix,
        config=config,
        input_hashes=actual_ranking_input_hashes,
        source_base_dir=sources_path.parent,
    )
    validate_ranking_output_contract(
        ranking=ranking,
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
    )
    validate_ranking_terminal_recommendation_preconditions(
        ranking=ranking,
        event_gate=event_gate,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        research_window=research_window,
        source_payload=source_payload,
        config=config,
    )

    payload = build_ranking_terminal_recommendation(
        ranking=ranking,
        candidate_pipeline=candidate_pipeline,
        research_window=research_window,
        config=config,
    )
    validate_json_document(payload, "recommendation.schema.json")
    # Reuse the existing, generic recommendation contract-validation
    # functions rather than duplicating them: these also transitively
    # re-verify candidate_pipeline.target_date and candidates.target_date /
    # sources.target_date against ranking.target_date (since payload's
    # target_date is copied verbatim from ranking).
    validate_recommendation_candidate_link(payload, candidates)
    validate_recommendation_pipeline_link(payload, candidate_pipeline)
    validate_recommendation_sources(payload, source_payload)
    _write_json(output_path, payload, "recommendation.schema.json")
    return 0


def _write_json(
    path: Path,
    payload: dict[str, Any],
    schema_name: str,
) -> None:
    validate_json_document(payload, schema_name)
    atomic_write_text(
        path,
        json.dumps(_json_ready(payload), ensure_ascii=False, indent=2) + "\n",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _emit_json(payload: dict[str, Any], output_path: Path | None = None) -> None:
    content = json.dumps(_json_ready(payload), ensure_ascii=False, indent=2) + "\n"
    if output_path is None:
        print(content, end="")
    else:
        atomic_write_text(output_path, content)


def _sha256_bytes(data: bytes) -> str:
    return __import__("hashlib").sha256(data).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
