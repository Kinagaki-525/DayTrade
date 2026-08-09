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
    load_market_data,
    validate_market_data,
    validate_source_ledger,
)
from src.metrics import DEFAULT_TRADES_PATH, calculate_metrics_from_csv
from src.recommendations import append_recommendation, recommendation_to_row
from src.reports import render_sbi_report
from src.risk import OrderProposal, evaluate_order, not_applicable_result
from src.screening import screen_market_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="daytrade-sbi v2 offline tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot-config")
    snapshot_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    snapshot_parser.add_argument("--output", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate-market")
    validate_parser.add_argument("--market-data", required=True, type=Path)
    validate_parser.add_argument("--sources", required=True, type=Path)
    validate_parser.add_argument("--output", required=True, type=Path)

    screen_parser = subparsers.add_parser("screen-market")
    screen_parser.add_argument("--market-data", required=True, type=Path)
    screen_parser.add_argument("--sources", required=True, type=Path)
    screen_parser.add_argument("--output", required=True, type=Path)
    screen_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    risk_parser = subparsers.add_parser("risk-check")
    risk_parser.add_argument("--recommendation", required=True, type=Path)
    risk_parser.add_argument("--candidates", required=True, type=Path)
    risk_parser.add_argument("--market-data", required=True, type=Path)
    risk_parser.add_argument("--sources", required=True, type=Path)
    risk_parser.add_argument("--output", required=True, type=Path)
    risk_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    risk_parser.add_argument("--current-positions", type=int, required=True)
    risk_parser.add_argument("--trades-today", type=int, required=True)

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
        return _validate_market(args.market_data, args.sources, args.output)
    if args.command == "screen-market":
        return _screen_market(args.market_data, args.sources, args.output, args.config)
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
) -> int:
    target_date, records, ledger_result, _ = _load_market_bundle(
        market_data_path,
        sources_path,
    )
    results = []
    for record in records:
        record_result = validate_market_data(record)
        errors = [*ledger_result.errors, *record_result.errors]
        results.append(
            {
                "ticker": record.ticker,
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
) -> int:
    target_date, records, ledger_result, _ = _load_market_bundle(
        market_data_path,
        sources_path,
    )
    if not ledger_result.valid:
        raise ValueError("Source ledger validation failed: " + "; ".join(ledger_result.errors))
    config = load_strategy_config(config_path)
    results = [screen_market_record(record, config).as_dict() for record in records]
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
    current_positions: int,
    trades_today: int,
) -> int:
    recommendation = load_json_document(
        recommendation_path,
        "recommendation.schema.json",
    )
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    validate_recommendation_candidate_link(recommendation, candidates)
    market_target_date, records, ledger_result, source_payload = _load_market_bundle(
        market_data_path,
        sources_path,
    )
    validate_recommendation_sources(recommendation, source_payload)
    config = load_strategy_config(config_path)
    config_digest = strategy_config_sha256(config)
    if recommendation["strategy_version"] != config["strategy_version"]:
        raise ValueError("recommendation strategy_version does not match --config")
    if recommendation["config_sha256"] != config_digest:
        raise ValueError("recommendation config_sha256 does not match --config")

    decision = recommendation.get("decision")
    if decision == "NO_TRADE":
        result = not_applicable_result(recommendation.get("ticker"))
    else:
        ticker = str(recommendation.get("ticker", "")).strip()
        matches = [record for record in records if record.ticker == ticker]
        market_record = matches[0] if len(matches) == 1 else None
        market_valid = (
            ledger_result.valid and validate_market_data(market_record).valid_for_trade
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
) -> tuple[
    str,
    list[MarketDataRecord],
    SourceLedgerValidationResult,
    dict[str, Any],
]:
    load_json_document(market_data_path, "market_data.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")
    target_date, records = load_market_data(market_data_path)
    assert target_date is not None
    ledger_result = validate_source_ledger(target_date, records, source_payload)
    return target_date, records, ledger_result, source_payload


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
