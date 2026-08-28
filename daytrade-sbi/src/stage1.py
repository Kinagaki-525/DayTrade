from __future__ import annotations

from typing import Any, Iterable


ALLOWED_STAGE1_REJECTS = {
    "universe": {"UNIVERSE_OUT_OF_SCOPE"},
    "security_type": {"SECURITY_TYPE_UNSUPPORTED"},
    "share_unit": {"SHARE_UNIT_NOT_100"},
    "capital_limit": {"CAPITAL_LIMIT_EXCEEDED"},
}
STAGE1_CHECK_REQUIRED_SOURCE_IDS = {
    "security_type": {"JPX_LISTED_COMPANY"},
    "share_unit": {"JPX_TRADING_UNIT"},
}

POST_STAGE1_ACTIVE_STATUSES = {"IN_PROGRESS", "COMPLETE", "DATA_UNAVAILABLE"}


def source_refs_from_payload(source_payload: dict[str, Any]) -> set[str]:
    return {
        item
        for source in source_payload.get("sources", [])
        if isinstance(source, dict)
        for item in _text_items([source.get("source_ref")])
    }


def source_attempt_ids_from_payload(source_payload: dict[str, Any]) -> set[str]:
    return {
        item
        for attempt in source_payload.get("source_attempts", [])
        if isinstance(attempt, dict)
        for item in _text_items([attempt.get("attempt_id")])
    }


def source_ids_by_evidence_id_from_payload(
    source_payload: dict[str, Any],
) -> dict[str, str]:
    source_ids: dict[str, str] = {}
    for collection_name, evidence_field in (
        ("sources", "source_ref"),
        ("source_attempts", "attempt_id"),
    ):
        for item in source_payload.get(collection_name, []):
            if not isinstance(item, dict):
                continue
            evidence_id = _text(item.get(evidence_field))
            source_id = _text(item.get("source_id"))
            if evidence_id and source_id:
                source_ids[evidence_id] = source_id
    return source_ids


def source_backed_stage1_reject(
    research: dict[str, Any],
    *,
    valid_source_refs: set[str] | None = None,
    valid_source_attempt_ids: set[str] | None = None,
    source_ids_by_evidence_id: dict[str, str] | None = None,
) -> bool:
    if research.get("stage1_status") != "REJECTED":
        return False
    return bool(
        rejected_stage1_check_ids(
            research,
            valid_source_refs=valid_source_refs,
            valid_source_attempt_ids=valid_source_attempt_ids,
            source_ids_by_evidence_id=source_ids_by_evidence_id,
        )
    )


def rejected_stage1_check_ids(
    research: dict[str, Any] | None,
    *,
    valid_source_refs: set[str] | None = None,
    valid_source_attempt_ids: set[str] | None = None,
    source_ids_by_evidence_id: dict[str, str] | None = None,
) -> list[str]:
    if research is None:
        return []
    if research.get("stage1_status") != "REJECTED":
        return []
    failed: list[str] = []
    for check in research.get("stage1_checks", []):
        if not isinstance(check, dict) or check.get("status") != "REJECTED":
            continue
        if _has_backing(
            check,
            valid_source_refs,
            valid_source_attempt_ids,
            source_ids_by_evidence_id=source_ids_by_evidence_id,
        ):
            failed.append(f"stage1:{check.get('check_id', 'unknown')}")
    return _unique(failed)


def stage1_reject_evidence_error(
    research: dict[str, Any],
    *,
    valid_source_refs: set[str],
    valid_source_attempt_ids: set[str],
    source_ids_by_evidence_id: dict[str, str] | None = None,
) -> str | None:
    if research.get("stage1_status") != "REJECTED":
        return None
    if source_backed_stage1_reject(
        research,
        valid_source_refs=valid_source_refs,
        valid_source_attempt_ids=valid_source_attempt_ids,
        source_ids_by_evidence_id=source_ids_by_evidence_id,
    ):
        return None
    ticker = str(research.get("ticker", "")).strip() or "<unknown>"
    return (
        f"{ticker} has stage1_status=REJECTED without source-backed "
        "stage1_checks in sources.json/source_attempts"
    )


def stage1_contract_errors(research: dict[str, Any]) -> list[str]:
    ticker = str(research.get("ticker", "")).strip() or "<unknown>"
    errors: list[str] = []
    universe_status = research.get("universe_status")
    stage1_status = research.get("stage1_status")
    stage2_status = research.get("stage2_status")
    context_status = research.get("context_research_status")

    if (
        stage1_status in {"PASSED", "REJECTED"}
        and universe_status not in {"PASSED", "REJECTED"}
    ):
        errors.append(
            f"{ticker} has stage1_status={stage1_status} before a completed "
            "universe_status"
        )
    if (
        stage1_status == "PASSED"
        and universe_status is not None
        and universe_status != "PASSED"
    ):
        errors.append(
            f"{ticker} has stage1_status=PASSED but universe_status="
            f"{universe_status}"
        )

    for field_name, status in (
        ("stage2_status", stage2_status),
        ("context_research_status", context_status),
    ):
        if status not in POST_STAGE1_ACTIVE_STATUSES:
            continue
        if stage1_status != "PASSED":
            errors.append(
                f"{ticker} has {field_name}={status} before "
                "stage1_status=PASSED"
            )
        if universe_status != "PASSED":
            errors.append(
                f"{ticker} has {field_name}={status} before "
                "universe_status=PASSED"
            )

    if stage1_status == "REJECTED":
        for field_name, status in (
            ("stage2_status", stage2_status),
            ("context_research_status", context_status),
        ):
            if status in POST_STAGE1_ACTIVE_STATUSES:
                errors.append(
                    f"{ticker} has stage1_status=REJECTED but "
                    f"{field_name}={status}"
                )

    for check in research.get("stage1_checks", []):
        if not isinstance(check, dict) or check.get("status") != "REJECTED":
            continue
        check_id = _text(check.get("check_id"))
        reason_code = _text(check.get("reason_code"))
        if not reason_code:
            errors.append(
                f"{ticker} has stage1 check {check_id or '<unknown>'} "
                "without reason_code"
            )
            continue
        allowed_reason_codes = ALLOWED_STAGE1_REJECTS.get(check_id)
        if allowed_reason_codes is None:
            errors.append(f"{ticker} has unapproved Stage 1 check_id: {check_id}")
        elif reason_code not in allowed_reason_codes:
            errors.append(
                f"{ticker} has unapproved Stage 1 reason_code for "
                f"{check_id}: {reason_code}"
            )
    return errors


def _has_backing(
    check: dict[str, Any],
    valid_source_refs: set[str] | None,
    valid_source_attempt_ids: set[str] | None,
    *,
    source_ids_by_evidence_id: dict[str, str] | None = None,
) -> bool:
    if not (
        _has_matching_item(check.get("source_refs"), valid_source_refs)
        or _has_matching_item(check.get("source_attempt_ids"), valid_source_attempt_ids)
    ):
        return False
    required_source_ids = STAGE1_CHECK_REQUIRED_SOURCE_IDS.get(
        _text(check.get("check_id"))
    )
    if required_source_ids is None:
        return True
    if source_ids_by_evidence_id is None:
        return True
    return _has_required_stage1_source(
        check,
        required_source_ids,
        source_ids_by_evidence_id=source_ids_by_evidence_id,
    )


def _has_required_stage1_source(
    check: dict[str, Any],
    required_source_ids: set[str],
    *,
    source_ids_by_evidence_id: dict[str, str],
) -> bool:
    return any(
        source_ids_by_evidence_id.get(item) in required_source_ids
        for item in (
            _text_items(check.get("source_refs"))
            + _text_items(check.get("source_attempt_ids"))
        )
    )


def _has_matching_item(value: Any, valid_items: set[str] | None) -> bool:
    items = _text_items(value)
    if valid_items is None:
        return bool(items)
    return any(item in valid_items for item in items)


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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
