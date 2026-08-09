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
from src.contracts import (
    load_json_document,
    validate_json_document,
    validate_recommendation_candidate_link,
    validate_recommendation_risk_link,
    validate_recommendation_sources,
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
from src.recommendations import append_recommendation, recommendation_to_row
from src.reports import render_sbi_report
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
    market_research_parser.add_argument(
        "--source-matrix",
        type=Path,
        default=DEFAULT_SOURCE_MATRIX_PATH,
    )
    market_research_parser.add_argument("--output", required=True, type=Path)

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

    risk_parser = subparsers.add_parser("risk-check")
    risk_parser.add_argument("--recommendation", required=True, type=Path)
    risk_parser.add_argument("--candidates", required=True, type=Path)
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
    return parser


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
            args.source_matrix,
            args.output,
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
    if args.command == "risk-check":
        return _risk_check(
            args.recommendation,
            args.candidates,
            args.market_data,
            args.sources,
            args.output,
            args.config,
            args.current_positions,
            args.trades_today,
            args.source_matrix,
            args.market_research,
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
    raise ValueError(f"Unknown command: {args.command}")


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execution", required=True, type=Path)
    parser.add_argument("--recommendation", required=True, type=Path)
    parser.add_argument("--risk-result", required=True, type=Path)
    parser.add_argument("--market-data", required=True, type=Path)


def _validate_market(
    market_data_path: Path,
    sources_path: Path,
    output_path: Path,
    source_matrix_path: Path,
    market_research_path: Path | None,
) -> int:
    target_date, records, ledger_result, _, source_matrix = _load_market_bundle(
        market_data_path,
        sources_path,
        source_matrix_path,
    )
    alignment_result = _load_market_research_alignment(
        records,
        market_research_path,
        source_matrix,
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
    target_date, records, ledger_result, _, source_matrix = _load_market_bundle(
        market_data_path,
        sources_path,
        source_matrix_path,
    )
    if not ledger_result.valid:
        raise ValueError("Source ledger validation failed: " + "; ".join(ledger_result.errors))
    alignment_result = _load_market_research_alignment(
        records,
        market_research_path,
        source_matrix,
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
    market_data_path: Path,
    sources_path: Path,
    output_path: Path,
    config_path: Path,
    current_positions: int | None,
    trades_today: int | None,
    source_matrix_path: Path,
    market_research_path: Path | None,
) -> int:
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    validate_recommendation_candidate_link(recommendation, candidates)
    decision = recommendation.get("decision")
    if decision == "TRADE" and (current_positions is None or trades_today is None):
        raise ValueError(
            "current_positions and trades_today are required when "
            "recommendation decision is TRADE"
        )
    market_target_date, records, ledger_result, source_payload, source_matrix = _load_market_bundle(
        market_data_path,
        sources_path,
        source_matrix_path,
    )
    alignment_result = _load_market_research_alignment(
        records,
        market_research_path,
        source_matrix,
    )
    validate_recommendation_sources(recommendation, source_payload)
    config = load_strategy_config(config_path)
    config_digest = strategy_config_sha256(config)
    if recommendation["strategy_version"] != config["strategy_version"]:
        raise ValueError("recommendation strategy_version does not match --config")
    if recommendation["config_sha256"] != config_digest:
        raise ValueError("recommendation config_sha256 does not match --config")

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
    source_matrix = load_source_matrix(source_matrix_path)
    result = validate_market_research(market_research, source_matrix)
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
    )
    return target_date, records, ledger_result, source_payload, source_matrix


def _load_market_research_alignment(
    records: list[MarketDataRecord],
    market_research_path: Path | None,
    source_matrix: dict[str, Any],
) -> MarketDataResearchAlignmentResult | None:
    if market_research_path is None:
        return None
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    research_result = validate_market_research(market_research, source_matrix)
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


def _emit_json(payload: dict[str, Any], output_path: Path | None = None) -> None:
    content = json.dumps(_json_ready(payload), ensure_ascii=False, indent=2) + "\n"
    if output_path is None:
        print(content, end="")
    else:
        atomic_write_text(output_path, content)


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
