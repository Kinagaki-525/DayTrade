from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import load_yaml
from src.contracts import validate_json_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_MATRIX_PATH = PROJECT_ROOT / "config" / "source_matrix.yaml"

SOURCE_STATUSES = {
    "NOT_STARTED",
    "FOUND",
    "NOT_FOUND",
    "NOT_YET_AVAILABLE",
    "ACCESS_FAILED",
    "PARSE_FAILED",
    "STALE",
    "CONFLICT",
    "SINGLE_SOURCE_ONLY",
    "SOURCE_POLICY_UNDEFINED",
    "NOT_REQUIRED",
    "DEPENDENCY_NOT_READY",
    "EXECUTION_FAILED",
}
SOURCE_ROLES = {"PRIMARY", "SECONDARY", "AUDIT", "CONTEXT"}
CRITICALITIES = {
    "TRADE_CRITICAL",
    "DISCOVERY_CRITICAL",
    "RULE_DEPENDENT",
    "CONTEXT",
}
DISCOVERY_TYPES = {
    "VOLUME_RANKING",
    "PRICE_GAIN_RANKING",
}

DISCOVERY_SOURCE_IDS = {
    "VOLUME_RANKING": "YAHOO_JP_VOLUME_RANKING",
    "PRICE_GAIN_RANKING": "YAHOO_JP_GAIN_RANKING",
}

REQUIRED_SOURCE_IDS = {
    "JPX_CALENDAR",
    "JPX_LISTED_COMPANY",
    "JPX_TRADING_UNIT",
    "YAHOO_JP_VOLUME_RANKING",
    "YAHOO_JP_GAIN_RANKING",
    "YAHOO_JP_HISTORY",
    "KABUTAN_HISTORY",
    "JPX_DAILY_REPORT",
    "JPX_TICK_SIZE",
    "JPX_TOPIX500",
    "JPX_TDNET",
    "JPX_EARNINGS_SCHEDULE",
    "COMPANY_IR",
    "COMPANY_IR_DISCLOSURE",
    "YAHOO_JP_NEWS",
    "KABUTAN_NEWS",
}


@dataclass(frozen=True)
class SourceMatrixValidationResult:
    valid: bool
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors)}


def load_source_matrix(
    path: str | Path = DEFAULT_SOURCE_MATRIX_PATH,
) -> dict[str, Any]:
    payload = load_yaml(path)
    result = validate_source_matrix(payload)
    if not result.valid:
        raise ValueError("Source matrix validation failed: " + "; ".join(result.errors))
    return payload


def validate_source_matrix(payload: dict[str, Any]) -> SourceMatrixValidationResult:
    errors: list[str] = []
    try:
        validate_json_document(payload, "source_matrix.schema.json")
    except ValueError as exc:
        errors.append(str(exc))
        return SourceMatrixValidationResult(False, tuple(errors))

    sources = payload["sources"]
    source_ids = [source["source_id"] for source in sources]
    duplicate_count = len(source_ids) - len(set(source_ids))
    if duplicate_count:
        errors.append(f"source_matrix contains {duplicate_count} duplicate source_id(s)")

    missing_required = sorted(REQUIRED_SOURCE_IDS.difference(source_ids))
    if missing_required:
        errors.append(
            "source_matrix is missing required source_id(s): "
            + ", ".join(missing_required)
        )

    source_by_id = {source["source_id"]: source for source in sources}
    for discovery_type, source_id in DISCOVERY_SOURCE_IDS.items():
        source = source_by_id.get(source_id)
        if source is None:
            continue
        if source["criticality"] != "DISCOVERY_CRITICAL":
            errors.append(f"{source_id} must be DISCOVERY_CRITICAL for {discovery_type}")
        if source["role"] != "PRIMARY":
            errors.append(f"{source_id} must be PRIMARY for {discovery_type}")

    tdnet = source_by_id.get("JPX_TDNET")
    if tdnet is not None:
        if tdnet["criticality"] != "RULE_DEPENDENT":
            errors.append("JPX_TDNET must be RULE_DEPENDENT criticality")
        if tdnet["role"] != "PRIMARY":
            errors.append("JPX_TDNET must be PRIMARY role")

    ir_disclosure = source_by_id.get("COMPANY_IR_DISCLOSURE")
    if ir_disclosure is None:
        errors.append("source_matrix is missing required source_id(s): COMPANY_IR_DISCLOSURE")

    if payload["source_change_policy"]["runtime_substitution_allowed"]:
        errors.append("runtime Source Matrix substitution must remain disabled")

    return SourceMatrixValidationResult(not errors, tuple(errors))


def source_ids(payload: dict[str, Any]) -> set[str]:
    return {source["source_id"] for source in payload["sources"]}


def source_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {source["source_id"]: source for source in payload["sources"]}


def source_definition_errors(
    *,
    source_id: str | None,
    source_role: str | None,
    information_type: str | None,
    criticality: str | None = None,
    source_matrix: dict[str, Any],
    prefix: str,
) -> list[str]:
    errors: list[str] = []
    if source_id is None:
        return errors

    definition = source_by_id(source_matrix).get(source_id)
    if definition is None:
        errors.append(f"{prefix}.source_id is not defined in source_matrix.yaml")
        return errors

    if source_role is not None and source_role != definition["role"]:
        errors.append(f"{prefix}.source_role does not match source_matrix.yaml")
    if (
        information_type is not None
        and information_type != definition["information_type"]
    ):
        errors.append(f"{prefix}.information_type does not match source_matrix.yaml")
    if criticality is not None and criticality != definition["criticality"]:
        errors.append(f"{prefix}.criticality does not match source_matrix.yaml")
    return errors
