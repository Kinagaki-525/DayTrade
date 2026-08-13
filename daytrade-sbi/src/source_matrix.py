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
    "YAHOO_JP_QUOTE",
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

    yahoo_jp_quote = source_by_id.get("YAHOO_JP_QUOTE")
    if yahoo_jp_quote is None:
        errors.append("source_matrix is missing required source_id(s): YAHOO_JP_QUOTE")
    else:
        if yahoo_jp_quote["role"] != "PRIMARY":
            errors.append("YAHOO_JP_QUOTE must be PRIMARY role")
        if yahoo_jp_quote["criticality"] != "TRADE_CRITICAL":
            errors.append("YAHOO_JP_QUOTE must be TRADE_CRITICAL criticality")
        if yahoo_jp_quote["information_type"] != "TURNOVER":
            errors.append("YAHOO_JP_QUOTE must have information_type TURNOVER")

    ir_disclosure = source_by_id.get("COMPANY_IR_DISCLOSURE")
    if ir_disclosure is None:
        errors.append("source_matrix is missing required source_id(s): COMPANY_IR_DISCLOSURE")

    if payload["source_change_policy"]["runtime_substitution_allowed"]:
        errors.append("runtime Source Matrix substitution must remain disabled")

    errors.extend(_acquisition_errors(payload))

    return SourceMatrixValidationResult(not errors, tuple(errors))


#: The only source ids whose already-fetched local raw page an AI agent may
#: read and classify. Every other source -- JPX_TDNET included -- is fully
#: deterministic: its disclosure index is parsed by code, never summarized.
AI_CLASSIFICATION_SOURCE_IDS = frozenset(
    {
        "COMPANY_IR",
        "COMPANY_IR_DISCLOSURE",
        "YAHOO_JP_NEWS",
        "KABUTAN_NEWS",
    }
)

#: Every source acquired during the EVENT stage, deterministic or otherwise.
EVENT_SOURCE_IDS = frozenset(
    {
        "JPX_TDNET",
        "JPX_EARNINGS_SCHEDULE",
        "COMPANY_IR",
        "COMPANY_IR_DISCLOSURE",
        "YAHOO_JP_NEWS",
        "KABUTAN_NEWS",
    }
)

#: Sources whose host comes from the human-approved issuer domain registry.
ISSUER_SCOPED_SOURCE_IDS = frozenset({"COMPANY_IR", "COMPANY_IR_DISCLOSURE"})

#: The public Source Matrix v3 contract vocabulary. These strings appear
#: verbatim in config/source_matrix.yaml and in the schema; internal Python
#: names may differ, the YAML/schema-facing terms may not.
ACQUISITION_MODES = frozenset(
    {"DETERMINISTIC", "DETERMINISTIC_THEN_AI_CLASSIFICATION"}
)
ACQUISITION_TRANSPORTS = frozenset({"CURL_GET"})
MATRIX_TEMPLATE_HOST_SCOPE = "MATRIX_TEMPLATE_HOST"
VERIFIED_ISSUER_DOMAIN_SCOPE = "VERIFIED_ISSUER_DOMAIN"
NETWORK_SCOPES = frozenset({MATRIX_TEMPLATE_HOST_SCOPE, VERIFIED_ISSUER_DOMAIN_SCOPE})


def is_v3(payload: dict[str, Any]) -> bool:
    return payload.get("source_matrix_schema_version") == 3


def acquisition_block(payload: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    definition = source_by_id(payload).get(source_id)
    if definition is None:
        return None
    acquisition = definition.get("acquisition")
    return acquisition if isinstance(acquisition, dict) else None


def _acquisition_errors(payload: dict[str, Any]) -> list[str]:
    """Source Matrix v3 rules.

    v2 documents are untouched (backward compatible): they carry no
    acquisition block and remain valid, which keeps every historical fixture
    loadable and every historical regression reproducible.
    """
    if not is_v3(payload):
        return []

    from src.source_parsers.registry import (  # local import: avoids an import cycle
        ParserRegistryError,
        verify_source_parser_binding,
    )

    errors: list[str] = []
    for definition in payload["sources"]:
        source_id = definition.get("source_id")
        acquisition = definition.get("acquisition")
        if not isinstance(acquisition, dict):
            errors.append(f"{source_id}: Source Matrix v3 requires an acquisition block")
            continue
        mode = acquisition.get("mode")
        if mode not in ACQUISITION_MODES:
            errors.append(f"{source_id}: unknown acquisition.mode {mode!r}")
        if acquisition.get("transport") not in ACQUISITION_TRANSPORTS:
            errors.append(
                f"{source_id}: unknown acquisition.transport "
                f"{acquisition.get('transport')!r}"
            )
        scope = acquisition.get("network_scope")
        if scope not in NETWORK_SCOPES:
            errors.append(f"{source_id}: invalid acquisition.network_scope {scope!r}")
        elif (scope == VERIFIED_ISSUER_DOMAIN_SCOPE) != (
            source_id in ISSUER_SCOPED_SOURCE_IDS
        ):
            errors.append(
                f"{source_id}: acquisition.network_scope {scope} does not match the "
                "issuer-scoped source list"
            )
        if mode == "DETERMINISTIC_THEN_AI_CLASSIFICATION" and (
            source_id not in AI_CLASSIFICATION_SOURCE_IDS
        ):
            errors.append(
                f"{source_id}: AI classification is not permitted for this source"
            )
        if mode == "DETERMINISTIC" and source_id in AI_CLASSIFICATION_SOURCE_IDS:
            errors.append(
                f"{source_id}: event source must declare "
                "DETERMINISTIC_THEN_AI_CLASSIFICATION"
            )
        try:
            verify_source_parser_binding(definition)
        except ParserRegistryError as exc:
            errors.append(str(exc))
    return errors


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
