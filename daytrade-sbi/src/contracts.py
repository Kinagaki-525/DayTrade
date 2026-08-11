from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src.config import strategy_config_sha256
from src.stage1 import (
    source_attempt_ids_from_payload,
    source_ids_by_evidence_id_from_payload,
    source_refs_from_payload,
    stage1_contract_errors,
    stage1_reject_evidence_error,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
RUN_ARTIFACT_ALLOWLIST = {
    "strategy_snapshot.yaml",
    "research_window.json",
    "market_research.json",
    "market_research_validation.json",
    "sources.json",
    "market_data.json",
    "market_validation.json",
    "candidates.json",
    "candidate_pipeline.json",
    "performance.json",
    "research.md",
    "recommendation.json",
    "recommendation.md",
    "risk_result.json",
    "report.md",
    "official_ohlcv_audit.json",
    "execution_result.json",
    "source_pages",
}


def load_json_document(path: str | Path, schema_name: str) -> dict[str, Any]:
    document_path = Path(path)
    with document_path.open("r", encoding="utf-8") as json_file:
        payload = json.load(
            json_file,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_non_standard_number,
        )
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {document_path}")
    validate_json_document(payload, schema_name)
    return payload


def validate_json_document(payload: dict[str, Any], schema_name: str) -> None:
    schema_path = SCHEMAS_DIR / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=_error_sort_key)
    if not errors:
        return

    details = []
    for error in errors:
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        details.append(f"{location}: {error.message}")
    raise ValueError(f"{schema_name} validation failed: {'; '.join(details)}")


def validate_recommendation_candidate_link(
    recommendation: dict[str, Any],
    candidates: dict[str, Any],
) -> None:
    _require_equal(recommendation, candidates, "target_date", "recommendation/candidates")
    _require_equal(
        recommendation,
        candidates,
        "strategy_version",
        "recommendation/candidates",
    )
    _require_equal(
        recommendation,
        candidates,
        "config_sha256",
        "recommendation/candidates",
    )
    if recommendation["decision"] != "TRADE":
        return

    ticker = recommendation["ticker"]
    matches = [
        candidate
        for candidate in candidates["candidates"]
        if candidate["ticker"] == ticker and candidate["status"] == "ELIGIBLE"
    ]
    if len(matches) != 1:
        raise ValueError(
            "TRADE recommendation must reference exactly one ELIGIBLE candidate"
        )


def validate_recommendation_sources(
    recommendation: dict[str, Any],
    source_payload: dict[str, Any],
) -> None:
    _require_equal(
        recommendation,
        source_payload,
        "target_date",
        "recommendation/sources",
    )
    ledger_urls = {source["source_url"] for source in source_payload["sources"]}
    missing_urls = [
        url for url in recommendation["source_urls"] if url not in ledger_urls
    ]
    if missing_urls:
        raise ValueError(
            "recommendation source_urls are missing from sources.json: "
            + ", ".join(missing_urls)
        )
    attempt_urls = {
        attempt["url"]
        for attempt in source_payload.get("source_attempts", [])
        if attempt.get("url")
    }
    known_urls = ledger_urls | attempt_urls
    missing_status_urls = [
        status["url"]
        for status in recommendation.get("source_statuses", [])
        if status.get("url") not in known_urls
    ]
    if missing_status_urls:
        raise ValueError(
            "recommendation source_statuses are missing from sources.json/source_attempts: "
            + ", ".join(missing_status_urls)
        )


def validate_recommendation_pipeline_link(
    recommendation: dict[str, Any],
    candidate_pipeline: dict[str, Any],
) -> None:
    _require_equal(
        recommendation,
        candidate_pipeline,
        "target_date",
        "recommendation/candidate_pipeline",
    )
    _require_equal(
        recommendation,
        candidate_pipeline,
        "strategy_version",
        "recommendation/candidate_pipeline",
    )
    _require_equal(
        recommendation,
        candidate_pipeline,
        "config_sha256",
        "recommendation/candidate_pipeline",
    )
    summary = candidate_pipeline.get("summary", {})
    if summary.get("pipeline_complete") is not True:
        raise ValueError("candidate_pipeline is not complete")
    recommendation_summary = recommendation.get("pipeline_summary")
    if not isinstance(recommendation_summary, dict):
        raise ValueError("recommendation pipeline_summary is required")
    if recommendation_summary != summary:
        for field_name in sorted(set(recommendation_summary) | set(summary)):
            if recommendation_summary.get(field_name) != summary.get(field_name):
                raise ValueError(
                    "recommendation/candidate_pipeline pipeline_summary "
                    f"{field_name} does not match"
                )
        raise ValueError(
            "recommendation/candidate_pipeline pipeline_summary does not match"
        )


def validate_research_report_inputs(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    performance: dict[str, Any],
) -> None:
    validate_performance_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=source_payload,
    )
    _require_equal(
        market_research,
        performance,
        "target_date",
        "market_research/performance",
    )


def validate_daily_report_inputs(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    performance: dict[str, Any],
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> None:
    validate_research_report_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=source_payload,
        performance=performance,
    )
    validate_recommendation_pipeline_link(recommendation, candidate_pipeline)
    validate_recommendation_risk_link(recommendation, risk_result)


def validate_candidate_pipeline_inputs(
    *,
    market_research: dict[str, Any],
    market_target_date: str,
    candidates: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> None:
    expected_target_date = market_research.get("target_date")
    if market_target_date != expected_target_date:
        raise ValueError("market_data target_date does not match market_research")
    _require_equal(
        market_research,
        candidates,
        "target_date",
        "market_research/candidates",
    )
    _require_equal(
        market_research,
        source_payload,
        "target_date",
        "market_research/sources",
    )
    if candidates.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("candidates strategy_version does not match --config")
    if candidates.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("candidates config_sha256 does not match --config")
    valid_source_refs = source_refs_from_payload(source_payload)
    valid_source_attempt_ids = source_attempt_ids_from_payload(source_payload)
    source_ids_by_evidence_id = source_ids_by_evidence_id_from_payload(source_payload)
    for research in market_research.get("candidate_research", []):
        contract_errors = stage1_contract_errors(research)
        if contract_errors:
            raise ValueError(contract_errors[0])
        evidence_error = stage1_reject_evidence_error(
            research,
            valid_source_refs=valid_source_refs,
            valid_source_attempt_ids=valid_source_attempt_ids,
            source_ids_by_evidence_id=source_ids_by_evidence_id,
        )
        if evidence_error is not None:
            raise ValueError(evidence_error)


def validate_performance_inputs(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
) -> None:
    _require_equal(
        market_research,
        candidate_pipeline,
        "target_date",
        "market_research/candidate_pipeline",
    )
    _require_equal(
        market_research,
        source_payload,
        "target_date",
        "market_research/sources",
    )


def validate_run_artifact_allowlist(run_dir: str | Path) -> tuple[str, ...]:
    path = Path(run_dir)
    unexpected: list[str] = []
    for item in path.iterdir():
        if item.name in RUN_ARTIFACT_ALLOWLIST:
            continue
        unexpected.append(item.name)
    return tuple(sorted(unexpected))


def validate_recommendation_risk_link(
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> None:
    for field_name in (
        "target_date",
        "decision",
        "ticker",
        "strategy_version",
        "config_sha256",
    ):
        _require_equal(
            recommendation,
            risk_result,
            field_name,
            "recommendation/risk_result",
        )


def _require_equal(
    left: dict[str, Any],
    right: dict[str, Any],
    field_name: str,
    label: str,
) -> None:
    if left.get(field_name) != right.get(field_name):
        raise ValueError(f"{label} {field_name} does not match")


def _error_sort_key(error: Any) -> tuple[str, str]:
    return (".".join(str(item) for item in error.absolute_path), error.message)


def _reject_non_standard_number(value: str) -> None:
    raise ValueError(f"Non-standard JSON number is not allowed: {value}")
