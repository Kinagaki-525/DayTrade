from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from src.market import MarketDataRecord
from src.security_type import (
    is_supported_security_type,
    resolve_security_type_evidence,
)
from src.source_checks import STANDARD_SOURCE_CHECK_IDS
from src.stage1 import source_ids_by_evidence_id_from_payload, source_refs_from_payload
from src.strategy import build_order_plan


STAGE2_BATCH_AGENT = "market_researcher"
TRADING_UNIT_SOURCE_ID = "JPX_TRADING_UNIT"


def init_candidate_research_payload(
    market_research: dict[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(market_research)
    discovery_candidates = payload.get("discovery_candidates", [])
    existing_research = payload.get("candidate_research", [])
    research_by_ticker = _research_by_ticker(existing_research)
    discovery_tickers = [
        str(candidate["ticker"]).strip()
        for candidate in discovery_candidates
        if str(candidate.get("ticker", "")).strip()
    ]
    extra_tickers = sorted(set(research_by_ticker) - set(discovery_tickers))
    if extra_tickers:
        raise ValueError(
            "candidate_research contains ticker(s) outside Discovery candidates: "
            + ", ".join(extra_tickers)
        )

    payload["candidate_research"] = [
        research_by_ticker.get(ticker) or _not_started_candidate_research(ticker)
        for ticker in discovery_tickers
    ]
    if any(
        research.get("stage1_status") == "NOT_STARTED"
        for research in payload["candidate_research"]
    ):
        payload["overall_status"] = "PIPELINE_INCOMPLETE"
    return payload


def apply_stage1_payload(
    *,
    market_research: dict[str, Any],
    market_records: Iterable[MarketDataRecord],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(market_research)
    records_by_ticker = _records_by_ticker(market_records)
    valid_source_refs = source_refs_from_payload(source_payload)
    source_ids_by_ref = source_ids_by_evidence_id_from_payload(source_payload)
    updated_research: list[dict[str, Any]] = []
    for research in payload.get("candidate_research", []):
        ticker = str(research["ticker"]).strip()
        if research.get("stage1_status") not in {None, "NOT_STARTED"}:
            updated_research.append(research)
            continue
        record = records_by_ticker.get(ticker)
        if record is None:
            updated_research.append(research)
            continue
        updated_research.append(
            _apply_stage1_to_research(
                research,
                record,
                valid_source_refs=valid_source_refs,
                source_ids_by_ref=source_ids_by_ref,
                source_values=source_payload.get("sources", []),
                config=config,
            )
        )
    payload["candidate_research"] = updated_research
    if any(_candidate_research_incomplete(research) for research in updated_research):
        payload["overall_status"] = "PIPELINE_INCOMPLETE"
    return payload


def plan_stage2_batches_payload(
    *,
    market_research: dict[str, Any],
    batch_size: int = 8,
    agent_name: str = STAGE2_BATCH_AGENT,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    payload = deepcopy(market_research)
    targets = sorted(
        str(research["ticker"]).strip()
        for research in payload.get("candidate_research", [])
        if research.get("stage1_status") == "PASSED"
        and research.get("stage2_status") in {"NOT_STARTED", "IN_PROGRESS"}
    )
    target_set = set(targets)
    existing_batches = payload.get("subagent_batches", [])
    _validate_existing_batches(existing_batches, target_set)

    already_assigned = {
        str(code).strip()
        for batch in existing_batches
        for code in batch.get("candidate_codes", [])
        if str(code).strip()
    }
    unassigned_targets = [ticker for ticker in targets if ticker not in already_assigned]
    batches = list(existing_batches)
    next_index = len(batches) + 1
    for start in range(0, len(unassigned_targets), batch_size):
        candidate_codes = unassigned_targets[start : start + batch_size]
        batches.append(
            {
                "batch_id": _next_batch_id(batches, next_index),
                "agent_name": agent_name,
                "lifecycle_status": "REQUESTED",
                "candidate_codes": candidate_codes,
                "returned_candidate_codes": [],
                "merged_candidate_codes": [],
                "notes": [],
            }
        )
        next_index += 1
    if batches:
        payload["subagent_batches"] = batches
    return payload


def _not_started_candidate_research(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "data_status": "NOT_STARTED",
        "status_reasons": ["Candidate Research not started"],
        "reason_codes": ["RESEARCH_NOT_STARTED"],
        "missing_requirements": list(STANDARD_SOURCE_CHECK_IDS),
        "source_policy_status": "NOT_STARTED",
        "universe_status": "NOT_STARTED",
        "stage1_status": "NOT_STARTED",
        "stage1_reasons": [],
        "stage1_checks": [],
        "stage2_status": "NOT_STARTED",
        "stage2_reasons": [],
        "context_research_status": "NOT_STARTED",
        "source_checks": [
            {
                "check_id": check_id,
                "status": "NOT_STARTED",
                "source_id": None,
                "information_type": None,
                "reason_code": None,
                "source_refs": [],
                "source_attempt_ids": [],
            }
            for check_id in STANDARD_SOURCE_CHECK_IDS
        ],
        "source_attempt_ids": [],
        "required_capital_yen": None,
    }


def _apply_stage1_to_research(
    research: dict[str, Any],
    record: MarketDataRecord,
    *,
    valid_source_refs: set[str],
    source_ids_by_ref: dict[str, str],
    source_values: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    updated = deepcopy(research)
    # Stage 1 eligibility order: security_type -> share_unit -> capital_limit.
    # security_type comes first because it decides whether the domestic
    # trading-unit rule governs this candidate at all.
    #
    # record.security_type is never trusted as a bare string: it is recomputed
    # from the Canonical Source Ledger (sources.json) and must agree exactly.
    # The evidence deliberately does NOT come from record.sources -- those are
    # market_data's own copy, so a tampered market_data would simply agree
    # with itself. A value that no longer follows from the canonical evidence
    # decides nothing: Stage 1 stays open instead.
    evidence = resolve_security_type_evidence(
        source_values, candidate_code=str(record.ticker or "")
    )
    security_type_refs = [
        ref for ref in evidence.source_refs if ref in valid_source_refs
    ]
    if (
        evidence.security_type is None
        or evidence.security_type != record.security_type
        or len(security_type_refs) != len(evidence.source_refs)
    ):
        # Unclassified, tampered, or missing a Source Record the
        # classification depended on: not a PASS and not a REJECT. Stage 1
        # stays open rather than guessing a security type for the candidate.
        return updated
    if not is_supported_security_type(record.security_type):
        return _stage1_rejected(
            updated,
            check_id="security_type",
            reason_code="SECURITY_TYPE_UNSUPPORTED",
            source_refs=security_type_refs,
            status_reason=(
                f"security_type {record.security_type} is not supported by the "
                "current strategy"
            ),
            required_capital_yen=None,
        )

    share_unit_refs = _source_refs_for_fields(
        record,
        ("share_unit",),
        valid_source_refs,
        source_ids_by_ref=source_ids_by_ref,
        source_ids=(TRADING_UNIT_SOURCE_ID,),
    )
    if record.share_unit is None or not share_unit_refs:
        return updated
    if record.share_unit != 100:
        return _stage1_rejected(
            updated,
            check_id="share_unit",
            reason_code="SHARE_UNIT_NOT_100",
            source_refs=share_unit_refs,
            status_reason="share_unit is not 100",
            required_capital_yen=None,
        )

    previous_high_refs = _source_refs_for_fields(
        record,
        ("previous_high",),
        valid_source_refs,
        source_ids_by_ref=source_ids_by_ref,
    )
    tick_size_refs = _source_refs_for_fields(
        record,
        ("tick_size",),
        valid_source_refs,
        source_ids_by_ref=source_ids_by_ref,
    )
    if (
        record.previous_high is None
        or record.tick_size is None
        or not previous_high_refs
        or not tick_size_refs
    ):
        return updated

    try:
        order_plan = build_order_plan(record.previous_high, record.tick_size, config=config)
    except ValueError:
        return updated

    capital_refs = _unique(previous_high_refs + tick_size_refs)
    required_capital_yen = str(order_plan.estimated_purchase_amount)
    if not order_plan.affordable:
        return _stage1_rejected(
            updated,
            check_id="capital_limit",
            reason_code="CAPITAL_LIMIT_EXCEEDED",
            source_refs=capital_refs,
            status_reason="entry_limit multiplied by shares exceeds capital.total_yen",
            required_capital_yen=required_capital_yen,
        )

    updated["data_status"] = "NOT_STARTED"
    updated["status_reasons"] = []
    updated["reason_codes"] = []
    updated["missing_requirements"] = list(STANDARD_SOURCE_CHECK_IDS)
    updated["source_policy_status"] = record.source_policy_status or "FOUND"
    updated["universe_status"] = "PASSED"
    updated["stage1_status"] = "PASSED"
    updated["stage1_reasons"] = []
    updated["stage1_checks"] = [
        {
            "check_id": "security_type",
            "status": "PASSED",
            "reason_code": None,
            "source_refs": security_type_refs,
            "source_attempt_ids": [],
        },
        {
            "check_id": "share_unit",
            "status": "PASSED",
            "reason_code": None,
            "source_refs": share_unit_refs,
            "source_attempt_ids": [],
        },
        {
            "check_id": "capital_limit",
            "status": "PASSED",
            "reason_code": None,
            "source_refs": capital_refs,
            "source_attempt_ids": [],
        },
    ]
    updated["stage2_status"] = "NOT_STARTED"
    updated["stage2_reasons"] = []
    updated["context_research_status"] = "NOT_STARTED"
    updated["source_checks"] = [
        {**check, "status": "NOT_STARTED"}
        for check in _standard_source_checks(updated)
    ]
    updated["required_capital_yen"] = required_capital_yen
    return updated


def _stage1_rejected(
    research: dict[str, Any],
    *,
    check_id: str,
    reason_code: str,
    source_refs: list[str],
    status_reason: str,
    required_capital_yen: str | None,
) -> dict[str, Any]:
    research["data_status"] = "VERIFIED"
    research["status_reasons"] = [status_reason]
    research["reason_codes"] = [reason_code]
    research["missing_requirements"] = []
    research["source_policy_status"] = "FOUND"
    research["universe_status"] = "PASSED"
    research["stage1_status"] = "REJECTED"
    research["stage1_reasons"] = [status_reason]
    research["stage1_checks"] = [
        {
            "check_id": check_id,
            "status": "REJECTED",
            "reason_code": reason_code,
            "source_refs": source_refs,
            "source_attempt_ids": [],
        }
    ]
    research["stage2_status"] = "SKIPPED"
    research["stage2_reasons"] = []
    research["context_research_status"] = "SKIPPED"
    research["source_checks"] = [
        {
            **check,
            "status": "NOT_REQUIRED",
            "reason_code": reason_code,
            "source_refs": [],
            "source_attempt_ids": [],
        }
        for check in _standard_source_checks(research)
    ]
    research["required_capital_yen"] = required_capital_yen
    return research


def _standard_source_checks(research: dict[str, Any]) -> list[dict[str, Any]]:
    checks_by_id = {
        str(check.get("check_id", "")).strip(): dict(check)
        for check in research.get("source_checks", [])
        if isinstance(check, dict)
    }
    return [
        {
            "check_id": check_id,
            "status": checks_by_id.get(check_id, {}).get("status", "NOT_STARTED"),
            "source_id": checks_by_id.get(check_id, {}).get("source_id"),
            "information_type": checks_by_id.get(check_id, {}).get("information_type"),
            "reason_code": checks_by_id.get(check_id, {}).get("reason_code"),
            "source_refs": checks_by_id.get(check_id, {}).get("source_refs", []),
            "source_attempt_ids": checks_by_id.get(check_id, {}).get(
                "source_attempt_ids",
                [],
            ),
        }
        for check_id in STANDARD_SOURCE_CHECK_IDS
    ]


def _source_refs_for_fields(
    record: MarketDataRecord,
    field_names: tuple[str, ...],
    valid_source_refs: set[str],
    *,
    source_ids_by_ref: dict[str, str],
    source_ids: tuple[str, ...] | None = None,
) -> list[str]:
    allowed_source_ids = set(source_ids) if source_ids is not None else None
    refs = [
        str(source.source_ref)
        for source in record.sources
        if source.source_ref is not None
        and source.field_name in field_names
        and source.source_ref in valid_source_refs
        and _source_id_allowed(
            str(source.source_ref),
            allowed_source_ids,
            source_ids_by_ref,
        )
    ]
    for provenance in record.field_provenance:
        if str(provenance.get("field_name", "")).strip() not in field_names:
            continue
        for source_ref in provenance.get("source_refs", []):
            text = str(source_ref).strip()
            if text in valid_source_refs and _source_id_allowed(
                text,
                allowed_source_ids,
                source_ids_by_ref,
            ):
                refs.append(text)
    return _unique(refs)


def _source_id_allowed(
    source_ref: str,
    allowed_source_ids: set[str] | None,
    source_ids_by_ref: dict[str, str],
) -> bool:
    if allowed_source_ids is None:
        return True
    return source_ids_by_ref.get(source_ref) in allowed_source_ids


def _research_by_ticker(
    candidate_research: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    research_by_ticker: dict[str, dict[str, Any]] = {}
    for research in candidate_research:
        ticker = str(research.get("ticker", "")).strip()
        if not ticker:
            continue
        if ticker in research_by_ticker:
            raise ValueError(f"candidate_research contains duplicate ticker: {ticker}")
        research_by_ticker[ticker] = research
    return research_by_ticker


def _records_by_ticker(
    market_records: Iterable[MarketDataRecord],
) -> dict[str, MarketDataRecord]:
    records: dict[str, MarketDataRecord] = {}
    for record in market_records:
        ticker = str(record.ticker or "").strip()
        if not ticker:
            continue
        if ticker in records:
            raise ValueError(f"market_data contains duplicate ticker: {ticker}")
        records[ticker] = record
    return records


def _validate_existing_batches(
    batches: list[dict[str, Any]],
    stage2_targets: set[str],
) -> None:
    seen_codes: set[str] = set()
    seen_batch_ids: set[str] = set()
    for batch in batches:
        batch_id = str(batch.get("batch_id", "")).strip()
        if batch_id in seen_batch_ids:
            raise ValueError(f"duplicate subagent batch_id: {batch_id}")
        seen_batch_ids.add(batch_id)
        for code in batch.get("candidate_codes", []):
            ticker = str(code).strip()
            if ticker not in stage2_targets:
                raise ValueError(
                    "subagent batch contains non-Stage 2 target ticker: " + ticker
                )
            if ticker in seen_codes:
                raise ValueError(
                    "candidate is assigned to multiple Stage 2 batches: " + ticker
                )
            seen_codes.add(ticker)


def _next_batch_id(existing_batches: list[dict[str, Any]], index: int) -> str:
    existing_ids = {str(batch.get("batch_id", "")).strip() for batch in existing_batches}
    candidate = f"stage2-{index:03d}"
    while candidate in existing_ids:
        index += 1
        candidate = f"stage2-{index:03d}"
    return candidate


def _candidate_research_incomplete(research: dict[str, Any]) -> bool:
    if research.get("stage1_status") == "NOT_STARTED":
        return True
    if research.get("stage1_status") == "PASSED" and research.get(
        "stage2_status"
    ) in {"NOT_STARTED", "IN_PROGRESS"}:
        return True
    if research.get("context_research_status") == "IN_PROGRESS":
        return True
    return any(
        check.get("status") in {"NOT_STARTED", "DEPENDENCY_NOT_READY", "EXECUTION_FAILED"}
        for check in research.get("source_checks", [])
        if isinstance(check, dict)
    )


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
