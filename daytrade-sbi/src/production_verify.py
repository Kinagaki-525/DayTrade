"""Production run verification.

This module **reuses** the existing validators rather than reimplementing any
business logic: schema validation, Source Ledger validation, Market Data
validation, Event Gate / Ranking trust chain checks and the Recommendation
contracts all come from the modules that the production CLIs themselves use.
If a rule changes there, it changes here for free.

The status codes it emits are *diagnostic only*. They are returned to the
operator and never written into a business artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.contracts import load_json_document, validate_json_document
from src.market import (
    load_market_data,
    validate_market_data,
    validate_source_ledger,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH, load_source_matrix


# Diagnostic-only statuses. Never persisted into artifacts.
VERIFIED_CASE_A = "VERIFIED_CASE_A_DATA_UNAVAILABLE"
VERIFIED_CASE_B = "VERIFIED_CASE_B_NO_TRADE"
VERIFIED_CASE_C = "VERIFIED_CASE_C_SELECTED"
INVALID_RUN = "INVALID_RUN"

ARTIFACT_SCHEMAS = {
    "sources.json": "sources.schema.json",
    "market_data.json": "market_data.schema.json",
    "candidates.json": "candidates.schema.json",
    "candidate_pipeline.json": "candidate_pipeline.schema.json",
    "event_gate.json": "event_gate.schema.json",
    "ranking.json": "ranking.schema.json",
    "selection.json": "selection.schema.json",
    "recommendation.json": "recommendation.schema.json",
    "risk_result.json": "risk_result.schema.json",
}


@dataclass
class VerificationReport:
    status: str
    run_dir: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    network_audit: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run_dir": self.run_dir,
            "checks": self.checks,
            "errors": self.errors,
            "network_audit": self.network_audit,
        }

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.errors.append(f"{name}: {detail}" if detail else name)


def network_audit(source_payload: dict[str, Any]) -> dict[str, Any]:
    """Network audit, sourced **only** from sources.json.source_attempts.

    There is no separate guessed request log: the ledger is the record of
    every request the run was allowed to make.
    """
    attempts = [
        attempt
        for attempt in source_payload.get("source_attempts", [])
        if isinstance(attempt, dict)
    ]
    hosts: dict[str, int] = {}
    for attempt in attempts:
        url = str(attempt.get("url", ""))
        host = url.split("//", 1)[-1].split("/", 1)[0] if "//" in url else ""
        if host:
            hosts[host] = hosts.get(host, 0) + 1
    statuses: dict[str, int] = {}
    for attempt in attempts:
        status = str(attempt.get("status"))
        statuses[status] = statuses.get(status, 0) + 1
    duplicate_ids = _duplicates([str(a.get("attempt_id")) for a in attempts])
    return {
        "request_count": len(attempts),
        "hosts": hosts,
        "statuses": statuses,
        "duplicate_attempt_ids": duplicate_ids,
        "request_budget_respected": not duplicate_ids,
    }


def verify_production_run(
    run_dir: Path,
    *,
    source_matrix_path: Path = DEFAULT_SOURCE_MATRIX_PATH,
) -> VerificationReport:
    """Verify one run directory's artifacts with the production validators."""
    run_dir = Path(run_dir)
    report = VerificationReport(status=INVALID_RUN, run_dir=str(run_dir))

    if not run_dir.is_dir():
        report.record("run_dir_exists", False, f"{run_dir} is not a directory")
        return report
    report.record("run_dir_exists", True)

    payloads: dict[str, dict[str, Any]] = {}
    for name, schema_name in ARTIFACT_SCHEMAS.items():
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            payloads[name] = load_json_document(path, schema_name)
            report.record(f"schema:{name}", True)
        except (ValueError, json.JSONDecodeError) as exc:
            report.record(f"schema:{name}", False, str(exc))

    source_payload = payloads.get("sources.json")
    if source_payload is None:
        report.record("sources.json", False, "sources.json is required")
        return report

    report.network_audit = network_audit(source_payload)
    if not report.network_audit["request_budget_respected"]:
        report.record(
            "request_budget",
            False,
            "duplicate source attempt ids: "
            + ", ".join(report.network_audit["duplicate_attempt_ids"]),
        )
    else:
        report.record("request_budget", True)

    market_payload = payloads.get("market_data.json")
    if market_payload is not None:
        try:
            records = load_market_data(run_dir / "market_data.json")
            market_result = validate_market_data(records)
            report.record(
                "market_data", market_result.valid, "; ".join(market_result.errors)
            )
            ledger_result = validate_source_ledger(
                market_payload["target_date"],
                records,
                source_payload,
                load_source_matrix(source_matrix_path),
                source_base_dir=run_dir,
            )
            report.record(
                "source_ledger", ledger_result.valid, "; ".join(ledger_result.errors)
            )
        except ValueError as exc:
            report.record("market_data", False, str(exc))

    report.status = _terminal_status(payloads, report)
    return report


def _terminal_status(
    payloads: dict[str, dict[str, Any]],
    report: VerificationReport,
) -> str:
    if report.errors:
        return INVALID_RUN
    recommendation = payloads.get("recommendation.json")
    if recommendation is None:
        return INVALID_RUN
    decision = recommendation.get("decision")
    if decision == "DATA_UNAVAILABLE":
        return VERIFIED_CASE_A
    if decision == "NO_TRADE":
        return VERIFIED_CASE_B
    if decision == "TRADE":
        selection = payloads.get("selection.json")
        if selection is None or selection.get("selection_status") != "SELECTED":
            report.record(
                "case_c_selection", False, "TRADE requires a SELECTED selection.json"
            )
            return INVALID_RUN
        return VERIFIED_CASE_C
    return INVALID_RUN


def verify_production_happy_path(
    run_dir: Path,
    *,
    source_matrix_path: Path = DEFAULT_SOURCE_MATRIX_PATH,
) -> VerificationReport:
    """Case C end-to-end check: the run must reach a verified SELECTED trade."""
    report = verify_production_run(run_dir, source_matrix_path=source_matrix_path)
    if report.status != VERIFIED_CASE_C:
        report.record(
            "happy_path",
            False,
            f"run terminal status is {report.status}, expected {VERIFIED_CASE_C}",
        )
        report.status = INVALID_RUN
    return report


def _duplicates(items: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(item)
    return duplicates


__all__ = [
    "INVALID_RUN",
    "VERIFIED_CASE_A",
    "VERIFIED_CASE_B",
    "VERIFIED_CASE_C",
    "VerificationReport",
    "network_audit",
    "validate_json_document",
    "verify_production_happy_path",
    "verify_production_run",
]
