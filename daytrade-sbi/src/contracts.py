from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_ROOT / "schemas"


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
    if recommendation["decision"] == "NO_TRADE":
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
