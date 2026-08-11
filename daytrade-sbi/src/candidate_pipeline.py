from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from src.config import strategy_config_sha256
from src.market import MarketDataRecord
from src.stage1 import (
    rejected_stage1_check_ids,
    source_attempt_ids_from_payload,
    source_backed_stage1_reject,
    source_refs_from_payload,
    stage1_contract_errors,
    stage1_reject_evidence_error,
)


PIPELINE_STATUSES = (
    "DISCOVERED",
    "RESEARCH_IN_PROGRESS",
    "RESEARCH_COMPLETE",
    "DATA_UNAVAILABLE",
    "SCREENED",
    "ELIGIBLE",
    "REJECTED",
)

BLOCKING_DATA_STATUSES = {
    "DATA_UNAVAILABLE",
    "CONFLICT",
    "SINGLE_SOURCE_ONLY",
    "SOURCE_POLICY_UNDEFINED",
}


def build_candidate_pipeline(
    *,
    market_research: dict[str, Any],
    market_records: Iterable[MarketDataRecord],
    candidates_payload: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    records_by_ticker = {
        record.ticker: record
        for record in market_records
        if record.ticker is not None
    }
    screening_by_ticker = {
        str(candidate["ticker"]).strip(): candidate
        for candidate in candidates_payload.get("candidates", [])
        if candidate.get("ticker") is not None
    }
    research_by_ticker = {
        str(research["ticker"]).strip(): research
        for research in market_research.get("candidate_research", [])
    }
    source_attempts = source_payload.get("source_attempts", [])
    sources = source_payload.get("sources", [])
    valid_source_refs = source_refs_from_payload(source_payload)
    valid_source_attempt_ids = source_attempt_ids_from_payload(source_payload)

    pipeline_candidates = []
    for discovery_candidate in market_research.get("discovery_candidates", []):
        ticker = str(discovery_candidate["ticker"]).strip()
        research = research_by_ticker.get(ticker)
        screening = screening_by_ticker.get(ticker)
        record = records_by_ticker.get(ticker)
        if research is not None:
            contract_errors = stage1_contract_errors(research)
            if contract_errors:
                raise ValueError(contract_errors[0])
            evidence_error = stage1_reject_evidence_error(
                research,
                valid_source_refs=valid_source_refs,
                valid_source_attempt_ids=valid_source_attempt_ids,
            )
            if evidence_error is not None:
                raise ValueError(evidence_error)
        status = _pipeline_status(
            research,
            screening,
            valid_source_refs=valid_source_refs,
            valid_source_attempt_ids=valid_source_attempt_ids,
        )
        reasons = _status_reasons(research, screening)
        missing = _missing_requirements(status, research, record, reasons)
        source_attempt_ids = _source_attempt_ids(ticker, research, source_attempts)
        source_refs = _source_refs(ticker, sources, record, research)
        pipeline_candidates.append(
            {
                "ticker": ticker,
                "company_name": discovery_candidate["company_name"],
                "market": discovery_candidate.get("market"),
                "discovery_reasons": discovery_candidate["discovery_reasons"],
                "pipeline_status": status,
                "reason_codes": _reason_codes(status, reasons, missing, research),
                "missing_requirements": missing,
                "completed_checks": _completed_checks(research, record, screening),
                "failed_checks": _failed_checks(
                    status,
                    missing,
                    research,
                    valid_source_refs=valid_source_refs,
                    valid_source_attempt_ids=valid_source_attempt_ids,
                ),
                "source_attempt_ids": source_attempt_ids,
                "source_refs": source_refs,
            }
        )

    summary = _summary(pipeline_candidates, screening_by_ticker)
    return {
        "schema_version": 1,
        "target_date": market_research["target_date"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "summary": summary,
        "candidates": pipeline_candidates,
    }


def _pipeline_status(
    research: dict[str, Any] | None,
    screening: dict[str, Any] | None,
    *,
    valid_source_refs: set[str],
    valid_source_attempt_ids: set[str],
) -> str:
    if screening is not None:
        status = screening["status"]
        if status in {"ELIGIBLE", "REJECTED", "DATA_UNAVAILABLE"}:
            return status
        return "SCREENED"
    if research is None:
        return "DISCOVERED"
    if research.get("stage1_status") == "REJECTED":
        if source_backed_stage1_reject(
            research,
            valid_source_refs=valid_source_refs,
            valid_source_attempt_ids=valid_source_attempt_ids,
        ):
            return "REJECTED"
        return "RESEARCH_IN_PROGRESS"
    if "IN_PROGRESS" in {
        research.get("stage1_status"),
        research.get("stage2_status"),
        research.get("context_research_status"),
    }:
        return "RESEARCH_IN_PROGRESS"
    data_status = research.get("data_status")
    if data_status == "VERIFIED":
        return "RESEARCH_COMPLETE"
    if data_status in BLOCKING_DATA_STATUSES:
        return "DATA_UNAVAILABLE"
    return "RESEARCH_IN_PROGRESS"


def _status_reasons(
    research: dict[str, Any] | None,
    screening: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if research is not None:
        reasons.extend(str(reason) for reason in research.get("status_reasons", []))
        reasons.extend(str(reason) for reason in research.get("stage1_reasons", []))
        reasons.extend(str(reason) for reason in research.get("stage2_reasons", []))
        for check in research.get("stage1_checks", []):
            if not isinstance(check, dict):
                continue
            if check.get("status") == "REJECTED" and check.get("reason_code"):
                reasons.append(str(check["reason_code"]))
    if screening is not None:
        reasons.extend(str(reason) for reason in screening.get("reasons", []))
    return [reason for reason in reasons if reason.strip()]


def _missing_requirements(
    status: str,
    research: dict[str, Any] | None,
    record: MarketDataRecord | None,
    reasons: list[str],
) -> list[str]:
    missing: list[str] = []
    if research is None:
        missing.append("candidate_research")
    elif status in {"DATA_UNAVAILABLE", "RESEARCH_IN_PROGRESS"}:
        explicit_missing = _list_field(research, "missing_requirements")
        missing.extend(
            explicit_missing if explicit_missing else _requirements_from_reasons(reasons)
        )
        if record is None:
            missing.append("market_data")
        source_policy_status = research.get("source_policy_status")
        if source_policy_status and source_policy_status != "FOUND":
            missing.append("source_policy")
    return _unique(missing)


def _requirements_from_reasons(reasons: list[str]) -> list[str]:
    requirements: list[str] = []
    for reason in reasons:
        lowered = reason.lower()
        if "secondary" in lowered or "kabutan" in lowered:
            requirements.append("secondary_ohlcv")
        if "ohlcv" in lowered or "history" in lowered:
            requirements.append("ohlcv")
        if "tick" in lowered or "呼値" in lowered:
            requirements.append("tick_size")
        if "tdnet" in lowered or "timely" in lowered or "disclosure" in lowered:
            requirements.append("timely_disclosure")
        if "listed" in lowered or "company" in lowered or "銘柄基本" in lowered:
            requirements.append("listed_company")
        if "market" in lowered or "市場" in lowered:
            requirements.append("market")
        if "source policy" in lowered or "source_policy" in lowered:
            requirements.append("source_policy")
    return requirements or ["trade_critical_data"]


def _reason_codes(
    status: str,
    reasons: list[str],
    missing: list[str],
    research: dict[str, Any] | None,
) -> list[str]:
    codes = []
    if research is not None:
        codes.extend(_list_field(research, "reason_codes"))
        for check in research.get("stage1_checks", []):
            if not isinstance(check, dict):
                continue
            if check.get("status") == "REJECTED" and check.get("reason_code"):
                codes.append(str(check["reason_code"]))
    if not codes:
        codes = [_code_from_text(reason) for reason in reasons]
    if not codes:
        codes = [f"{item.upper()}_UNAVAILABLE" for item in missing]
    if status == "DISCOVERED":
        codes.append("RESEARCH_NOT_STARTED")
    return _unique(code for code in codes if code)


def _completed_checks(
    research: dict[str, Any] | None,
    record: MarketDataRecord | None,
    screening: dict[str, Any] | None,
) -> list[str]:
    checks = ["discovery"]
    if research is not None:
        if research.get("stage1_status") not in {None, "NOT_STARTED", "SKIPPED"}:
            checks.append("candidate_stage1")
        if research.get("stage2_status") in {
            "IN_PROGRESS",
            "COMPLETE",
            "DATA_UNAVAILABLE",
        }:
            checks.append("candidate_stage2")
        if research.get("context_research_status") in {
            "IN_PROGRESS",
            "COMPLETE",
            "DATA_UNAVAILABLE",
        }:
            checks.append("context_research")
        checks.append(
            "candidate_research"
            if research.get("data_status") == "VERIFIED"
            else "candidate_research_attempted"
        )
    if record is not None:
        checks.append("market_data_recorded")
    if screening is not None:
        checks.append("screening")
    return checks


def _failed_checks(
    status: str,
    missing: list[str],
    research: dict[str, Any] | None,
    *,
    valid_source_refs: set[str],
    valid_source_attempt_ids: set[str],
) -> list[str]:
    failed = rejected_stage1_check_ids(
        research,
        valid_source_refs=valid_source_refs,
        valid_source_attempt_ids=valid_source_attempt_ids,
    )
    if status not in {"DATA_UNAVAILABLE", "REJECTED", "RESEARCH_IN_PROGRESS"}:
        return failed
    failed.extend(f"missing:{requirement}" for requirement in missing)
    return _unique(failed)


def _source_attempt_ids(
    ticker: str,
    research: dict[str, Any] | None,
    source_attempts: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    if research is not None:
        ids.extend(str(item) for item in research.get("source_attempt_ids", []))
        for check in research.get("stage1_checks", []):
            if not isinstance(check, dict):
                continue
            ids.extend(str(item) for item in check.get("source_attempt_ids", []))
    for attempt in source_attempts:
        candidate_code = attempt.get("candidate_code")
        if candidate_code not in {ticker, None}:
            continue
        if candidate_code is None and attempt.get("criticality") != "DISCOVERY_CRITICAL":
            continue
        ids.append(_attempt_id(attempt))
    return _unique(ids)


def _attempt_id(attempt: dict[str, Any]) -> str:
    explicit = attempt.get("attempt_id")
    if explicit:
        return str(explicit)
    raise ValueError("source_attempts[].attempt_id is required")


def _source_refs(
    ticker: str,
    sources: list[dict[str, Any]],
    record: MarketDataRecord | None,
    research: dict[str, Any] | None,
) -> list[str]:
    refs = [
        str(source["source_ref"])
        for source in sources
        if source.get("ticker") == ticker and source.get("source_ref")
    ]
    if research is not None:
        for check in research.get("stage1_checks", []):
            if not isinstance(check, dict):
                continue
            refs.extend(str(item) for item in check.get("source_refs", []))
    if record is not None:
        refs.extend(
            source.source_ref
            for source in record.sources
            if source.source_ref is not None
        )
    return _unique(refs)


def _list_field(mapping: dict[str, Any], field_name: str) -> list[str]:
    value = mapping.get(field_name)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _summary(
    pipeline_candidates: list[dict[str, Any]],
    screening_by_ticker: dict[str, dict[str, Any]],
) -> dict[str, int]:
    status_counts: dict[str, int] = {status: 0 for status in PIPELINE_STATUSES}
    for candidate in pipeline_candidates:
        status_counts[candidate["pipeline_status"]] += 1
    return {
        "discovered": len(pipeline_candidates),
        "research_complete": sum(
            status_counts[status]
            for status in ("RESEARCH_COMPLETE", "SCREENED", "ELIGIBLE", "REJECTED")
        ),
        "research_incomplete": sum(
            status_counts[status] for status in ("DISCOVERED", "RESEARCH_IN_PROGRESS")
        ),
        "data_unavailable": status_counts["DATA_UNAVAILABLE"],
        "screened": len(screening_by_ticker),
        "eligible": status_counts["ELIGIBLE"],
        "rejected": status_counts["REJECTED"],
    }


def _code_from_text(value: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    return code[:80]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
