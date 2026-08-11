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


def list_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
