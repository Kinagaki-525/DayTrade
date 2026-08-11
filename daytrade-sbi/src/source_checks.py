from __future__ import annotations

from typing import Any


STANDARD_SOURCE_CHECK_IDS = (
    "listed_company",
    "trading_unit",
    "primary_ohlcv",
    "secondary_ohlcv",
    "tick_size",
    "topix500_membership",
    "earnings_schedule",
    "tdnet",
    "news_context",
)

INCOMPLETE_SOURCE_STATUSES = {
    "NOT_STARTED",
    "DEPENDENCY_NOT_READY",
    "EXECUTION_FAILED",
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


def normalize_source_checks(research: dict[str, Any] | None) -> list[dict[str, Any]]:
    if research is None:
        return []
    checks: list[dict[str, Any]] = []
    for check in research.get("source_checks", []):
        if not isinstance(check, dict):
            continue
        check_id = str(check.get("check_id", "")).strip()
        if check_id not in STANDARD_SOURCE_CHECK_IDS:
            continue
        checks.append(
            {
                "check_id": check_id,
                "status": str(check.get("status", "NOT_STARTED")).strip()
                or "NOT_STARTED",
                "source_id": optional_text(check.get("source_id")),
                "information_type": optional_text(check.get("information_type")),
                "reason_code": optional_text(check.get("reason_code")),
                "source_refs": list_text(check.get("source_refs")),
                "source_attempt_ids": list_text(check.get("source_attempt_ids")),
            }
        )
    return checks


def source_check_contract_errors(research: dict[str, Any]) -> list[str]:
    ticker = str(research.get("ticker", "")).strip() or "<unknown>"
    checks = normalize_source_checks(research)
    check_ids = [check["check_id"] for check in checks]
    missing = [
        check_id
        for check_id in STANDARD_SOURCE_CHECK_IDS
        if check_id not in check_ids
    ]
    duplicates = sorted(
        {
            check_id
            for check_id in check_ids
            if check_ids.count(check_id) > 1
        }
    )
    errors: list[str] = []
    if missing:
        errors.append(
            f"{ticker} source_checks missing check_id(s): " + ", ".join(missing)
        )
    if duplicates:
        errors.append(
            f"{ticker} source_checks duplicate check_id(s): " + ", ".join(duplicates)
        )
    return errors


def has_data_unavailable_evidence(
    research: dict[str, Any],
    *,
    valid_source_refs: set[str] | None,
    source_attempt_statuses: dict[str, str] | None,
) -> bool:
    top_level_attempts = list_text(research.get("source_attempt_ids"))
    if _has_failed_attempt(top_level_attempts, source_attempt_statuses):
        return True
    for check in normalize_source_checks(research):
        if check["status"] not in EXTERNAL_SOURCE_FAILURE_STATUSES:
            continue
        if _has_recorded_ref(check["source_refs"], valid_source_refs):
            return True
        if _has_failed_attempt(check["source_attempt_ids"], source_attempt_statuses):
            return True
    return False


def source_attempt_statuses_from_payload(
    source_payload: dict[str, Any],
) -> dict[str, str]:
    return {
        attempt_id: status
        for attempt in source_payload.get("source_attempts", [])
        if isinstance(attempt, dict)
        for attempt_id in _text_items([attempt.get("attempt_id")])
        for status in _text_items([attempt.get("status")])
    }


def list_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _has_recorded_ref(items: list[str], valid_items: set[str] | None) -> bool:
    if valid_items is None:
        return bool(items)
    return any(item in valid_items for item in items)


def _has_failed_attempt(
    attempt_ids: list[str],
    source_attempt_statuses: dict[str, str] | None,
) -> bool:
    if source_attempt_statuses is None:
        return bool(attempt_ids)
    return any(
        source_attempt_statuses.get(attempt_id) in EXTERNAL_SOURCE_FAILURE_STATUSES
        for attempt_id in attempt_ids
    )


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := optional_text(item))]
