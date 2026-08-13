"""Production run verification.

This module **reuses** the existing validators rather than reimplementing any
business logic: schema validation, Source Ledger validation, Market Data
validation, the Ranking Trust Chain, Event Gate integrity and the
Recommendation / Risk contracts all come from the modules the production CLIs
themselves use. If a rule changes there, it changes here for free.

What this module adds is *completeness*. A run is not verified because two
artifacts happen to parse: it is verified when the **whole chain** is present,
schema-valid, internally consistent and cross-consistent -- from
strategy_snapshot.yaml and the raw bytes on disk all the way to
risk_result.json.

The status codes it emits are *diagnostic only*. They are returned to the
operator and never written into a business artifact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import load_strategy_config, strategy_config_sha256
from src.contracts import (
    load_json_document,
    validate_json_document,
    validate_recommendation_risk_link,
    validate_run_artifact_allowlist,
)
from src.event_gate import validate_event_gate_integrity
from src.market import (
    load_market_data,
    validate_market_data,
    validate_source_ledger,
)
from src.ranking_trust import load_and_verify_ranking_trust_chain
from src.source_fetch import SourceFetchError, verify_source_page
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH, load_source_matrix


# Diagnostic-only statuses. Never persisted into artifacts.
VERIFIED_CASE_A = "VERIFIED_CASE_A"
VERIFIED_CASE_B = "VERIFIED_CASE_B"
VERIFIED_CASE_C_NO_TRADE = "VERIFIED_CASE_C_NO_TRADE"
VERIFIED_CASE_C_TRADE_RISK_PASS = "VERIFIED_CASE_C_TRADE_RISK_PASS"
VERIFIED_CASE_C_TRADE_RISK_REJECTED = "VERIFIED_CASE_C_TRADE_RISK_REJECTED"
INVALID_RUN = "INVALID_RUN"

#: Every terminal status that represents a correctly completed run.
VERIFIED_STATUSES = frozenset(
    {
        VERIFIED_CASE_A,
        VERIFIED_CASE_B,
        VERIFIED_CASE_C_NO_TRADE,
        VERIFIED_CASE_C_TRADE_RISK_PASS,
        VERIFIED_CASE_C_TRADE_RISK_REJECTED,
    }
)

SELECTION_NOT_ACTIVE_REASON = "SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"

#: Artifacts every completed run must carry, whatever its terminal case.
REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "strategy_snapshot.yaml",
    "research_window.json",
    "sources.json",
    "market_data.json",
    "candidates.json",
    "candidate_pipeline.json",
    "event_research.json",
    "event_gate.json",
    "ranking.json",
    "recommendation.json",
    "risk_result.json",
)

ARTIFACT_SCHEMAS = {
    "research_window.json": "research_window.schema.json",
    "sources.json": "sources.schema.json",
    "market_data.json": "market_data.schema.json",
    "candidates.json": "candidates.schema.json",
    "candidate_pipeline.json": "candidate_pipeline.schema.json",
    "event_research.json": "event_research.schema.json",
    "event_gate.json": "event_gate.schema.json",
    "ranking.json": "ranking.schema.json",
    "selection.json": "selection.schema.json",
    "recommendation.json": "recommendation.schema.json",
    "risk_result.json": "risk_result.schema.json",
}

#: Fields that must agree wherever they appear.
CROSS_ARTIFACT_FIELDS = (
    "target_date",
    "previous_trading_day",
    "strategy_version",
    "config_sha256",
)


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Network audit (FIX-013)
# ---------------------------------------------------------------------------


def network_audit(source_payload: dict[str, Any]) -> dict[str, Any]:
    """Network audit, sourced **only** from sources.json.source_attempts.

    The number of *attempts* is not the number of *requests*: a globally
    shared page (one JPX_TDNET index) yields one candidate-scoped attempt per
    candidate off a single GET. ``cache_status`` is what distinguishes them --
    MISS is a real network request, HIT is a reuse of already-stored bytes --
    so the audit counts MISS attempts, never ``len(source_attempts)``.
    """
    attempts = [
        attempt
        for attempt in source_payload.get("source_attempts", [])
        if isinstance(attempt, dict)
    ]
    network_attempts = [
        attempt for attempt in attempts if attempt.get("cache_status") != "HIT"
    ]

    hosts: dict[str, int] = {}
    for attempt in network_attempts:
        url = str(attempt.get("url", ""))
        host = url.split("//", 1)[-1].split("/", 1)[0] if "//" in url else ""
        if host:
            hosts[host] = hosts.get(host, 0) + 1

    statuses: dict[str, int] = {}
    for attempt in attempts:
        status = str(attempt.get("status"))
        statuses[status] = statuses.get(status, 0) + 1

    duplicate_ids = _duplicates([str(a.get("attempt_id")) for a in attempts])

    # A second real GET of a URL already fetched in this run is a Request
    # Budget violation, whatever the attempt ids say. Only an explicit
    # ``cache_status: MISS`` asserts that a GET was issued; an attempt with no
    # cache_status (a pre-v3 ledger) makes no such claim and is not evidence
    # of a duplicate request.
    seen_urls: set[str] = set()
    duplicate_requests: list[str] = []
    for attempt in network_attempts:
        if attempt.get("cache_status") != "MISS":
            continue
        url = str(attempt.get("url", ""))
        if url in seen_urls and url not in duplicate_requests:
            duplicate_requests.append(url)
        seen_urls.add(url)

    return {
        "attempt_count": len(attempts),
        "request_count": len(network_attempts),
        "cache_hit_count": len(attempts) - len(network_attempts),
        "hosts": hosts,
        "statuses": statuses,
        "duplicate_attempt_ids": duplicate_ids,
        "duplicate_requested_urls": duplicate_requests,
        "request_budget_respected": not duplicate_ids and not duplicate_requests,
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_production_run(
    run_dir: Path,
    *,
    source_matrix_path: Path = DEFAULT_SOURCE_MATRIX_PATH,
) -> VerificationReport:
    """Verify one run directory's **whole** artifact chain."""
    run_dir = Path(run_dir)
    report = VerificationReport(status=INVALID_RUN, run_dir=str(run_dir))

    if not run_dir.is_dir():
        report.record("run_dir_exists", False, f"{run_dir} is not a directory")
        return report
    report.record("run_dir_exists", True)

    # ---- 1. every required artifact is present ----------------------------
    missing = [name for name in REQUIRED_ARTIFACTS if not (run_dir / name).is_file()]
    report.record(
        "required_artifacts_present",
        not missing,
        "missing artifact(s): " + ", ".join(missing) if missing else "",
    )
    if missing:
        return report

    # ---- 2. every present artifact is schema-valid ------------------------
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

    try:
        config = load_strategy_config(run_dir / "strategy_snapshot.yaml")
        report.record("schema:strategy_snapshot.yaml", True)
    except (ValueError, OSError) as exc:
        report.record("schema:strategy_snapshot.yaml", False, str(exc))
        return report

    if report.errors:
        return report

    source_payload = payloads["sources.json"]
    recommendation = payloads["recommendation.json"]
    risk_result = payloads["risk_result.json"]

    # ---- 3. run artifact allowlist ----------------------------------------
    unexpected = validate_run_artifact_allowlist(run_dir)
    report.record(
        "run_artifact_allowlist",
        not unexpected,
        "unexpected artifact(s): " + ", ".join(unexpected) if unexpected else "",
    )

    # ---- 4. network audit / Request Budget --------------------------------
    report.network_audit = network_audit(source_payload)
    if not report.network_audit["request_budget_respected"]:
        report.record(
            "request_budget",
            False,
            "duplicate attempt ids: "
            + ", ".join(report.network_audit["duplicate_attempt_ids"])
            + "; duplicate requested urls: "
            + ", ".join(report.network_audit["duplicate_requested_urls"]),
        )
    else:
        report.record("request_budget", True)

    # ---- 5. the raw evidence on disk still hashes to what was recorded -----
    verify_raw_evidence(run_dir, source_payload, report)

    # ---- 6. Market Data + Source Ledger -----------------------------------
    source_matrix = load_source_matrix(source_matrix_path)
    try:
        target_date, records = load_market_data(run_dir / "market_data.json")
        market_errors: list[str] = []
        for record in records:
            market_errors.extend(validate_market_data(record).errors)
        report.record("market_data", not market_errors, "; ".join(market_errors))

        ledger_result = validate_source_ledger(
            target_date,
            records,
            source_payload,
            source_matrix,
            source_base_dir=run_dir,
        )
        report.record(
            "source_ledger", ledger_result.valid, "; ".join(ledger_result.errors)
        )
    except ValueError as exc:
        report.record("market_data", False, str(exc))

    # ---- 7. Event Gate integrity ------------------------------------------
    gate_errors = validate_event_gate_integrity(
        payloads["event_gate.json"], source_payload
    )
    report.record("event_gate_integrity", not gate_errors, "; ".join(gate_errors))

    # ---- 8. Ranking Trust Chain (hash-chained back to the raw evidence) ----
    try:
        load_and_verify_ranking_trust_chain(
            ranking_path=run_dir / "ranking.json",
            event_gate_path=run_dir / "event_gate.json",
            candidates_path=run_dir / "candidates.json",
            market_data_path=run_dir / "market_data.json",
            sources_path=run_dir / "sources.json",
            source_matrix_path=Path(source_matrix_path),
            config_path=run_dir / "strategy_snapshot.yaml",
        )
        report.record("ranking_trust_chain", True)
    except (ValueError, OSError) as exc:
        report.record("ranking_trust_chain", False, str(exc))

    # ---- 9. cross-artifact consistency ------------------------------------
    _verify_cross_artifact_consistency(payloads, config, report)

    # ---- 10. Recommendation <-> Risk link ---------------------------------
    try:
        validate_recommendation_risk_link(recommendation, risk_result)
        report.record("recommendation_risk_link", True)
    except ValueError as exc:
        report.record("recommendation_risk_link", False, str(exc))

    # ---- 11. terminal case -------------------------------------------------
    report.status = _terminal_status(run_dir, payloads, config, report)
    return report


def verify_raw_evidence(
    run_dir: Path,
    source_payload: dict[str, Any],
    report: VerificationReport,
) -> None:
    """Re-hash every stored source page. A single tampered byte fails the run."""
    errors: list[str] = []
    checked = 0
    for attempt in source_payload.get("source_attempts", []):
        if not isinstance(attempt, dict):
            continue
        path = attempt.get("source_page_path")
        digest = attempt.get("source_page_sha256")
        if not path or not digest:
            continue
        try:
            verify_source_page(run_dir, str(path), str(digest))
            checked += 1
        except SourceFetchError as exc:
            errors.append(f"{exc.code}: {exc.message}")
    report.record(
        "raw_source_pages",
        not errors,
        "; ".join(errors) if errors else f"{checked} page(s) re-hashed",
    )


def _verify_cross_artifact_consistency(
    payloads: dict[str, dict[str, Any]],
    config: dict[str, Any],
    report: VerificationReport,
) -> None:
    """target_date / previous_trading_day / strategy_version / config SHA must
    agree everywhere they appear, and must agree with the snapshot config."""
    errors: list[str] = []

    expected_config_sha = strategy_config_sha256(config)
    expected_strategy_version = config.get("strategy_version")

    for field_name in CROSS_ARTIFACT_FIELDS:
        seen: dict[str, list[str]] = {}
        for name, payload in payloads.items():
            value = payload.get(field_name)
            if value is None:
                continue
            seen.setdefault(str(value), []).append(name)
        if len(seen) > 1:
            errors.append(
                f"{field_name} disagrees across artifacts: "
                + "; ".join(
                    f"{value} in {', '.join(names)}" for value, names in seen.items()
                )
            )

    for name, payload in payloads.items():
        if payload.get("config_sha256") not in (None, expected_config_sha):
            errors.append(f"{name}.config_sha256 does not match strategy_snapshot.yaml")
        if payload.get("strategy_version") not in (None, expected_strategy_version):
            errors.append(
                f"{name}.strategy_version does not match strategy_snapshot.yaml"
            )

    report.record("cross_artifact_consistency", not errors, "; ".join(errors))


def _verify_selection_hash_link(
    run_dir: Path,
    selection: dict[str, Any],
    report: VerificationReport,
) -> None:
    """selection.json must be pinned to the exact ranking.json bytes it read."""
    recorded = (selection.get("input_hashes") or {}).get("ranking_sha256")
    actual = _sha256_bytes((run_dir / "ranking.json").read_bytes())
    report.record(
        "selection_ranking_link",
        recorded == actual,
        f"selection.input_hashes.ranking_sha256 {recorded} != ranking.json {actual}",
    )


def _terminal_status(
    run_dir: Path,
    payloads: dict[str, dict[str, Any]],
    config: dict[str, Any],
    report: VerificationReport,
) -> str:
    """Classify the run's terminal case.

    A NO_TRADE outcome is a normal, correct termination -- not a failure. The
    verifier's job is to confirm the run reached its terminal state *through a
    sound chain*, never to insist that the terminal state be a TRADE.
    """
    if report.errors:
        return INVALID_RUN

    ranking = payloads["ranking.json"]
    recommendation = payloads["recommendation.json"]
    risk_result = payloads["risk_result.json"]
    selection = payloads.get("selection.json")

    ranking_status = ranking.get("ranking_status")
    decision = recommendation.get("decision")
    risk_status = risk_result.get("status")
    selection_enabled = bool((config.get("selection") or {}).get("enabled"))

    # ---- Case A: no tradable data at all ----------------------------------
    if ranking_status == "DATA_UNAVAILABLE":
        if decision != "DATA_UNAVAILABLE":
            report.record(
                "case_a_decision",
                False,
                "ranking DATA_UNAVAILABLE requires recommendation "
                f"DATA_UNAVAILABLE, got {decision}",
            )
            return INVALID_RUN
        if risk_status != "NOT_APPLICABLE":
            report.record(
                "case_a_risk",
                False,
                f"Case A requires risk NOT_APPLICABLE, got {risk_status}",
            )
            return INVALID_RUN
        return VERIFIED_CASE_A

    if ranking_status != "COMPLETE":
        report.record(
            "ranking_status", False, f"unexpected ranking_status {ranking_status!r}"
        )
        return INVALID_RUN

    # ---- Case B: ranking complete, Selection not yet activated ------------
    if not selection_enabled:
        if selection is not None:
            report.record(
                "case_b_selection",
                False,
                "selection.json exists but selection.enabled is false",
            )
            return INVALID_RUN
        if decision != "NO_TRADE":
            report.record(
                "case_b_decision",
                False,
                f"Case B requires recommendation NO_TRADE, got {decision}",
            )
            return INVALID_RUN
        reasons = recommendation.get("selection_reasons") or []
        if SELECTION_NOT_ACTIVE_REASON not in reasons:
            report.record(
                "case_b_reason",
                False,
                f"Case B requires reason {SELECTION_NOT_ACTIVE_REASON}, got {reasons}",
            )
            return INVALID_RUN
        if risk_status != "NOT_APPLICABLE":
            report.record(
                "case_b_risk",
                False,
                f"Case B requires risk NOT_APPLICABLE, got {risk_status}",
            )
            return INVALID_RUN
        return VERIFIED_CASE_B

    # ---- Case C: Selection is active --------------------------------------
    if selection is None:
        report.record(
            "case_c_selection",
            False,
            "selection is enabled but the run has no selection.json",
        )
        return INVALID_RUN

    _verify_selection_hash_link(run_dir, selection, report)
    if report.errors:
        return INVALID_RUN

    selection_status = selection.get("selection_status")

    if selection_status == "NO_TRADE":
        # A normal happy path: Selection evaluated Rank 1 and it did not clear
        # the thresholds. There is deliberately no Rank-2 fallback.
        if decision != "NO_TRADE":
            report.record(
                "case_c_no_trade_decision",
                False,
                f"selection NO_TRADE requires recommendation NO_TRADE, got {decision}",
            )
            return INVALID_RUN
        if risk_status != "NOT_APPLICABLE":
            report.record(
                "case_c_no_trade_risk",
                False,
                f"Case C NO_TRADE requires risk NOT_APPLICABLE, got {risk_status}",
            )
            return INVALID_RUN
        return VERIFIED_CASE_C_NO_TRADE

    if selection_status == "SELECTED":
        if decision != "TRADE":
            report.record(
                "case_c_trade_decision",
                False,
                f"selection SELECTED requires recommendation TRADE, got {decision}",
            )
            return INVALID_RUN
        if selection.get("selected_ticker") != recommendation.get("ticker"):
            report.record(
                "case_c_trade_ticker",
                False,
                "selection.selected_ticker does not match recommendation.ticker",
            )
            return INVALID_RUN
        if risk_status == "PASS":
            return VERIFIED_CASE_C_TRADE_RISK_PASS
        if risk_status == "REJECTED":
            return VERIFIED_CASE_C_TRADE_RISK_REJECTED
        report.record(
            "case_c_trade_risk",
            False,
            f"Case C TRADE requires risk PASS or REJECTED, got {risk_status}",
        )
        return INVALID_RUN

    report.record(
        "case_c_selection_status",
        False,
        f"unexpected selection_status {selection_status!r}",
    )
    return INVALID_RUN


def verify_production_happy_path(
    run_dir: Path,
    *,
    source_matrix_path: Path = DEFAULT_SOURCE_MATRIX_PATH,
) -> VerificationReport:
    """Confirm the run terminated correctly through a sound chain.

    The goal is a **correct** termination, not a TRADE. Case C NO_TRADE is a
    valid happy path, and so are Case A and Case B: demanding a TRADE would
    mean the verifier could only be satisfied by weakening Selection or Risk.
    """
    report = verify_production_run(run_dir, source_matrix_path=source_matrix_path)
    if report.status not in VERIFIED_STATUSES:
        report.record(
            "happy_path",
            False,
            f"run terminal status is {report.status}, expected one of "
            + ", ".join(sorted(VERIFIED_STATUSES)),
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
    "REQUIRED_ARTIFACTS",
    "SELECTION_NOT_ACTIVE_REASON",
    "VERIFIED_CASE_A",
    "VERIFIED_CASE_B",
    "VERIFIED_CASE_C_NO_TRADE",
    "VERIFIED_CASE_C_TRADE_RISK_PASS",
    "VERIFIED_CASE_C_TRADE_RISK_REJECTED",
    "VERIFIED_STATUSES",
    "VerificationReport",
    "network_audit",
    "verify_raw_evidence",
    "validate_json_document",
    "verify_production_happy_path",
    "verify_production_run",
]
