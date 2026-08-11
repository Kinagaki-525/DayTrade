from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.contracts import validate_json_document
from src.source_matrix import DISCOVERY_SOURCE_IDS, DISCOVERY_TYPES, source_by_id
from src.source_checks import (
    INCOMPLETE_SOURCE_STATUSES,
    list_text,
    source_check_contract_errors,
)
from src.stage1 import (
    source_attempt_ids_from_payload,
    source_backed_stage1_reject,
    source_refs_from_payload,
    stage1_contract_errors,
)


DISCOVERY_ORDER = ("VOLUME_RANKING", "PRICE_GAIN_RANKING")
BLOCKING_DATA_STATUSES = {
    "DATA_UNAVAILABLE",
    "CONFLICT",
    "SINGLE_SOURCE_ONLY",
    "SOURCE_POLICY_UNDEFINED",
}
EXTERNAL_SOURCE_FAILURE_STATUSES = {
    "NOT_FOUND",
    "NOT_YET_AVAILABLE",
    "ACCESS_FAILED",
    "PARSE_FAILED",
    "STALE",
    "CONFLICT",
    "SINGLE_SOURCE_ONLY",
    "SOURCE_POLICY_UNDEFINED",
}
@dataclass(frozen=True)
class MarketResearchValidationResult:
    valid: bool
    discovery_complete: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "discovery_complete": self.discovery_complete,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MarketDataResearchAlignmentResult:
    valid: bool
    global_errors: tuple[str, ...]
    errors_by_ticker: dict[str, tuple[str, ...]]


def validate_market_research(
    payload: dict[str, Any],
    source_matrix: dict[str, Any],
    source_payload: dict[str, Any] | None = None,
) -> MarketResearchValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        validate_json_document(payload, "market_research.schema.json")
    except ValueError as exc:
        errors.append(str(exc))
        return MarketResearchValidationResult(False, False, tuple(errors), tuple(warnings))

    source_definitions = source_by_id(source_matrix)
    valid_source_refs = (
        source_refs_from_payload(source_payload) if source_payload is not None else None
    )
    valid_source_attempt_ids = (
        source_attempt_ids_from_payload(source_payload)
        if source_payload is not None
        else None
    )
    target_date = _date(payload["target_date"])
    previous_trading_day = _date(payload["previous_trading_day"])
    if previous_trading_day >= target_date:
        errors.append("previous_trading_day must be before target_date")
    if _date_time(payload["research_cutoff"]).date() != previous_trading_day.date():
        errors.append("research_cutoff must be on previous_trading_day")
    errors.extend(
        _research_window_errors(
            payload["research_window"],
            payload["research_cutoff"],
            target_date,
        )
    )

    routes = payload["discovery"]
    route_types = [route["discovery_type"] for route in routes]
    if sorted(route_types) != sorted(DISCOVERY_TYPES):
        errors.append("discovery must contain exactly the two Yahoo ranking routes")

    discovery_complete = True
    for route in routes:
        discovery_type = route["discovery_type"]
        expected_source_id = DISCOVERY_SOURCE_IDS[discovery_type]
        source_id = route["source_id"]
        if source_id != expected_source_id:
            errors.append(f"{discovery_type} must use source_id {expected_source_id}")
        if source_id not in source_definitions:
            errors.append(f"market_research references undefined source_id: {source_id}")
        if route["status"] != "FOUND":
            discovery_complete = False
        if route["status"] == "FOUND" and route["result_count"] != len(route["items"]):
            errors.append(f"{discovery_type} result_count must equal items length")
        if (
            discovery_type in {"VOLUME_RANKING", "PRICE_GAIN_RANKING"}
            and route["status"] == "FOUND"
            and route["result_count"] != 50
        ):
            errors.append(f"{discovery_type} must record TOP50 when FOUND")

    if not discovery_complete and payload["overall_status"] == "COMPLETE":
        errors.append("overall_status cannot be COMPLETE when Discovery is incomplete")
    if not discovery_complete and payload["overall_status"] != "DISCOVERY_INCOMPLETE":
        warnings.append("Discovery source failure should normally set overall_status to DISCOVERY_INCOMPLETE")

    expected_candidates = _normalized_discovery_candidates(
        merge_discovery_candidates(routes)
    )
    actual_candidates = _normalized_discovery_candidates(payload["discovery_candidates"])
    if actual_candidates != expected_candidates:
        expected_tickers = {candidate["ticker"] for candidate in expected_candidates}
        actual_tickers = {candidate["ticker"] for candidate in actual_candidates}
        missing_tickers = sorted(expected_tickers - actual_tickers)
        extra_tickers = sorted(actual_tickers - expected_tickers)
        if missing_tickers:
            errors.append(
                "discovery_candidates is missing ticker(s) from Discovery union: "
                + ", ".join(missing_tickers)
            )
        if extra_tickers:
            errors.append(
                "discovery_candidates contains ticker(s) outside Discovery union: "
                + ", ".join(extra_tickers)
            )
        if not missing_tickers and not extra_tickers:
            errors.append("discovery_candidates must exactly match Discovery union")

    for candidate in payload["discovery_candidates"]:
        reason_types = {
            reason["discovery_type"] for reason in candidate["discovery_reasons"]
        }
        if set(candidate["discovered_by"]) != reason_types:
            errors.append(f"discovered_by does not match discovery_reasons for {candidate['ticker']}")

    expected_research_tickers = {candidate["ticker"] for candidate in expected_candidates}
    research_tickers = [research["ticker"] for research in payload["candidate_research"]]
    duplicate_research_count = len(research_tickers) - len(set(research_tickers))
    if duplicate_research_count:
        errors.append(
            f"candidate_research contains {duplicate_research_count} duplicate ticker(s)"
        )
    researched_tickers = set(research_tickers)
    unexpected_research = sorted(researched_tickers - expected_research_tickers)
    missing_research = sorted(expected_research_tickers - researched_tickers)
    if unexpected_research:
        errors.append(
            "candidate_research contains ticker(s) outside Discovery candidates: "
            + ", ".join(unexpected_research)
        )
    if missing_research:
        message = (
            "candidate_research is missing Discovery candidate ticker(s): "
            + ", ".join(missing_research)
        )
        if discovery_complete and payload["overall_status"] != "PIPELINE_INCOMPLETE":
            errors.append(message)
        else:
            warnings.append(message)

    unmerged_subagent_tickers = _unmerged_subagent_tickers(payload)
    if unmerged_subagent_tickers and payload["overall_status"] != "PIPELINE_INCOMPLETE":
        errors.append(
            "subagent results were returned but not merged for ticker(s): "
            + ", ".join(unmerged_subagent_tickers)
        )

    for research in payload["candidate_research"]:
        errors.extend(stage1_contract_errors(research))
        errors.extend(source_check_contract_errors(research))
        errors.extend(
            _source_check_reference_errors(
                research,
                valid_source_refs=valid_source_refs,
                valid_source_attempt_ids=valid_source_attempt_ids,
            )
        )
        if _research_is_incomplete(research):
            ticker = str(research.get("ticker", "")).strip() or "<unknown>"
            if payload["overall_status"] != "PIPELINE_INCOMPLETE":
                errors.append(
                    f"{ticker} has incomplete Candidate Research but "
                    "overall_status is not PIPELINE_INCOMPLETE"
                )
        source_policy_status = research.get("source_policy_status")
        if (
            source_policy_status == "SOURCE_POLICY_UNDEFINED"
            and research["data_status"] != "SOURCE_POLICY_UNDEFINED"
        ):
            errors.append(
                f"{research['ticker']} has SOURCE_POLICY_UNDEFINED without matching data_status"
            )
        if (
            research.get("stage1_status") == "REJECTED"
            and not source_backed_stage1_reject(
                research,
                valid_source_refs=valid_source_refs,
                valid_source_attempt_ids=valid_source_attempt_ids,
            )
        ):
            errors.append(
                f"{research['ticker']} has stage1_status=REJECTED without "
                "source-backed stage1_checks"
            )
        if (
            research.get("data_status") in BLOCKING_DATA_STATUSES
            and research.get("stage1_status") != "REJECTED"
            and not _has_data_unavailable_evidence(research)
        ):
            ticker = str(research.get("ticker", "")).strip() or "<unknown>"
            errors.append(
                f"{ticker} has data_status={research.get('data_status')} without "
                "attempted source_checks or source_attempt_ids"
            )

    return MarketResearchValidationResult(
        not errors,
        discovery_complete,
        tuple(errors),
        tuple(warnings),
    )


def validate_market_research_window_link(
    market_research: dict[str, Any],
    resolved_window: dict[str, Any],
) -> tuple[str, ...]:
    errors: list[str] = []
    for field_name in ("target_date", "previous_trading_day", "research_cutoff"):
        if market_research.get(field_name) != resolved_window.get(field_name):
            errors.append(
                f"market_research.{field_name} must match research_window.json"
            )
    if market_research.get("research_window") != resolved_window.get("research_window"):
        errors.append("market_research.research_window must match research_window.json")
    return tuple(errors)


def validate_market_records_against_research(
    records: list[Any],
    market_research: dict[str, Any],
) -> MarketDataResearchAlignmentResult:
    global_errors: list[str] = []
    errors_by_ticker: dict[str, list[str]] = {}
    research_by_ticker: dict[str, dict[str, Any]] = {}
    for research in market_research["candidate_research"]:
        ticker = str(research["ticker"]).strip()
        if ticker in research_by_ticker:
            global_errors.append(f"candidate_research contains duplicate ticker: {ticker}")
        research_by_ticker[ticker] = research

    for record in records:
        ticker = _text(getattr(record, "ticker", None))
        key = ticker or "<missing>"
        record_errors = errors_by_ticker.setdefault(key, [])
        if ticker is None:
            record_errors.append("market_data record ticker is required for research alignment")
            continue
        research = research_by_ticker.get(ticker)
        if research is None:
            record_errors.append(
                f"market_data ticker is missing from candidate_research: {ticker}"
            )
            continue
        record_data_status = _text(getattr(record, "data_status", None))
        if record_data_status != research["data_status"]:
            record_errors.append(
                f"market_data data_status does not match candidate_research for {ticker}"
            )
        research_source_policy = research.get("source_policy_status")
        record_source_policy = _text(getattr(record, "source_policy_status", None))
        if (
            research_source_policy is not None
            and record_source_policy != research_source_policy
        ):
            record_errors.append(
                "market_data source_policy_status does not match "
                f"candidate_research for {ticker}"
            )

    frozen_errors = {
        ticker: tuple(errors)
        for ticker, errors in errors_by_ticker.items()
        if errors
    }
    return MarketDataResearchAlignmentResult(
        not global_errors and not frozen_errors,
        tuple(global_errors),
        frozen_errors,
    )


def merge_discovery_candidates(discovery_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route in discovery_routes:
        discovery_type = route["discovery_type"]
        if route.get("status") != "FOUND":
            continue
        for item in route.get("items", []):
            ticker = str(item["ticker"]).strip()
            if ticker not in merged:
                merged[ticker] = {
                    "ticker": ticker,
                    "company_name": item["company_name"],
                    "market": item.get("market"),
                    "discovered_by": [],
                    "discovery_reasons": [],
                }
            candidate = merged[ticker]
            if not candidate.get("market") and item.get("market"):
                candidate["market"] = item["market"]
            if discovery_type not in candidate["discovered_by"]:
                candidate["discovered_by"].append(discovery_type)
            candidate["discovery_reasons"].append(
                {
                    "discovery_type": discovery_type,
                    "source_id": route["source_id"],
                    "source_url": item["source_url"],
                    "rank": item.get("rank"),
                    "display_value": item.get("display_value"),
                    "title": item.get("title"),
                }
            )

    order = {name: index for index, name in enumerate(DISCOVERY_ORDER)}
    for candidate in merged.values():
        candidate["discovered_by"].sort(key=lambda value: order[value])
        candidate["discovery_reasons"].sort(
            key=lambda reason: (order[reason["discovery_type"]], reason.get("rank") or 0)
        )
    return [merged[ticker] for ticker in sorted(merged)]


def _normalized_discovery_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        (_normalized_discovery_candidate(candidate) for candidate in candidates),
        key=lambda candidate: candidate["ticker"],
    )


def _normalized_discovery_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    order = {name: index for index, name in enumerate(DISCOVERY_ORDER)}
    reasons = [
        {
            "discovery_type": reason["discovery_type"],
            "source_id": reason["source_id"],
            "source_url": reason["source_url"],
            "rank": reason.get("rank"),
            "display_value": reason.get("display_value"),
            "title": reason.get("title"),
        }
        for reason in candidate["discovery_reasons"]
    ]
    reasons.sort(
        key=lambda reason: (
            order[reason["discovery_type"]],
            reason.get("rank") or 0,
            _stable_json(reason.get("display_value")),
            reason.get("source_url") or "",
            reason.get("title") or "",
        )
    )
    return {
        "ticker": str(candidate["ticker"]).strip(),
        "company_name": candidate["company_name"],
        "market": candidate.get("market"),
        "discovered_by": sorted(
            candidate["discovered_by"],
            key=lambda value: order[value],
        ),
        "discovery_reasons": reasons,
    }


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _research_is_incomplete(research: dict[str, Any]) -> bool:
    if any(
        research.get(field_name) is None
        for field_name in (
            "source_policy_status",
            "stage1_status",
            "stage2_status",
            "context_research_status",
        )
    ):
        return True
    if source_check_contract_errors(research):
        return True
    if research.get("universe_status") == "NOT_STARTED":
        return True
    if research.get("stage1_status") in {"NOT_STARTED"}:
        return True
    if research.get("stage2_status") == "IN_PROGRESS":
        return True
    if (
        research.get("stage1_status") == "PASSED"
        and research.get("stage2_status") == "NOT_STARTED"
    ):
        return True
    if research.get("context_research_status") == "IN_PROGRESS":
        return True
    return any(
        check.get("status") in INCOMPLETE_SOURCE_STATUSES
        for check in research.get("source_checks", [])
        if isinstance(check, dict)
    )


def _has_data_unavailable_evidence(research: dict[str, Any]) -> bool:
    for check in research.get("source_checks", []):
        if not isinstance(check, dict):
            continue
        if check.get("status") not in EXTERNAL_SOURCE_FAILURE_STATUSES:
            continue
        if list_text(check.get("source_attempt_ids")) or list_text(
            check.get("source_refs")
        ):
            return True
    return False


def _source_check_reference_errors(
    research: dict[str, Any],
    *,
    valid_source_refs: set[str] | None,
    valid_source_attempt_ids: set[str] | None,
) -> list[str]:
    if valid_source_refs is None and valid_source_attempt_ids is None:
        return []
    ticker = str(research.get("ticker", "")).strip() or "<unknown>"
    errors: list[str] = []
    for index, check in enumerate(research.get("source_checks", [])):
        if not isinstance(check, dict):
            continue
        missing_refs = [
            item
            for item in list_text(check.get("source_refs"))
            if valid_source_refs is not None and item not in valid_source_refs
        ]
        missing_attempts = [
            item
            for item in list_text(check.get("source_attempt_ids"))
            if valid_source_attempt_ids is not None and item not in valid_source_attempt_ids
        ]
        if missing_refs:
            errors.append(
                f"{ticker} source_checks[{index}].source_refs missing from "
                "sources.json: " + ", ".join(missing_refs)
            )
        if missing_attempts:
            errors.append(
                f"{ticker} source_checks[{index}].source_attempt_ids missing from "
                "sources.json: " + ", ".join(missing_attempts)
            )
    return errors


def _unmerged_subagent_tickers(payload: dict[str, Any]) -> list[str]:
    unmerged: set[str] = set()
    for batch in payload.get("subagent_batches", []):
        if not isinstance(batch, dict):
            continue
        returned = set(list_text(batch.get("returned_candidate_codes")))
        merged = set(list_text(batch.get("merged_candidate_codes")))
        if batch.get("lifecycle_status") in {"RETURNED", "VALIDATED"}:
            unmerged.update(returned - merged)
    return sorted(unmerged)


def _research_window_errors(
    window: dict[str, Any],
    research_cutoff: str,
    target_date: datetime,
) -> list[str]:
    errors: list[str] = []
    window_start = _date_time(window["window_start"])
    window_end = _date_time(window["window_end"])
    current_cutoff = _date_time(research_cutoff)

    if window_end != current_cutoff:
        errors.append("research_window.window_end must equal research_cutoff")
    if window_start >= window_end:
        errors.append("research_window.window_start must be before window_end")

    run_type = window["run_type"]
    if run_type == "FIRST_RUN":
        lookback_days = window["bootstrap_lookback_days"]
        expected_start = window_end - timedelta(days=lookback_days)
        if window_start != expected_start:
            errors.append(
                "FIRST_RUN research_window.window_start must equal "
                "window_end minus bootstrap_lookback_days"
            )
    if run_type == "NORMAL_RUN":
        previous_cutoff = _date_time(window["previous_research_cutoff"])
        previous_run_date = _date(window["previous_run_date"])
        if window_start != previous_cutoff:
            errors.append(
                "NORMAL_RUN research_window.window_start must equal "
                "previous_research_cutoff"
            )
        if previous_run_date >= target_date:
            errors.append("NORMAL_RUN previous_run_date must be before target_date")

    return errors


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _date_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
