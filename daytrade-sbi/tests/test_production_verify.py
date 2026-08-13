from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.production_verify import (
    INVALID_RUN,
    VERIFIED_CASE_B,
    VERIFIED_CASE_C,
    network_audit,
    verify_production_happy_path,
    verify_production_run,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELECTION_FIXTURE = PROJECT_ROOT / "regression/2026-08-12-selection-v1-selected/runs/2026-08-12"
NO_TRADE_FIXTURE = PROJECT_ROOT / "regression/2026-08-12-complete-no-trade/runs/2026-08-12"


def _copy(fixture: Path, tmp_path: Path) -> Path:
    target = tmp_path / "run"
    shutil.copytree(fixture, target)
    return target


def test_missing_run_dir_is_invalid(tmp_path):
    report = verify_production_run(tmp_path / "nope")
    assert report.status == INVALID_RUN


def test_run_without_sources_is_invalid(tmp_path):
    (tmp_path / "run").mkdir()
    report = verify_production_run(tmp_path / "run")
    assert report.status == INVALID_RUN
    assert any("sources.json" in error for error in report.errors)


def _minimal_run(tmp_path: Path, decision: str) -> Path:
    """A schema-valid minimal run: sources.json + one recommendation."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "sources.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "target_date": "2026-08-12",
                "sources": [],
                "source_attempts": [],
            }
        ),
        encoding="utf-8",
    )
    recommendation = json.loads(
        (PROJECT_ROOT / "tests/fixtures/recommendation_valid.json").read_text(
            encoding="utf-8"
        )
    )
    recommendation["decision"] = decision
    if decision != "TRADE":
        # a non-TRADE recommendation carries no order values at all
        for key in (
            "strategy_type",
            "ticker",
            "company_name",
            "previous_high",
            "tick_size",
            "entry_trigger",
            "entry_limit",
            "take_profit",
            "stop_loss",
            "shares",
        ):
            recommendation[key] = None
    (run_dir / "recommendation.json").write_text(
        json.dumps(recommendation), encoding="utf-8"
    )
    return run_dir


def test_no_trade_run_verifies_as_case_b(tmp_path):
    report = verify_production_run(_minimal_run(tmp_path, "NO_TRADE"))
    assert report.status == VERIFIED_CASE_B, report.errors


def test_data_unavailable_run_verifies_as_case_a(tmp_path):
    from src.production_verify import VERIFIED_CASE_A

    report = verify_production_run(_minimal_run(tmp_path, "DATA_UNAVAILABLE"))
    assert report.status == VERIFIED_CASE_A, report.errors


def test_trade_without_a_selected_selection_is_invalid(tmp_path):
    """Case C may not be claimed without a SELECTED selection.json."""
    report = verify_production_run(_minimal_run(tmp_path, "TRADE"))
    assert report.status == INVALID_RUN


def test_happy_path_requires_case_c(tmp_path):
    run_dir = _copy(NO_TRADE_FIXTURE, tmp_path)
    report = verify_production_happy_path(run_dir)
    assert report.status == INVALID_RUN


def test_schema_violation_makes_the_run_invalid(tmp_path):
    run_dir = _copy(NO_TRADE_FIXTURE, tmp_path)
    sources = json.loads((run_dir / "sources.json").read_text(encoding="utf-8"))
    sources["schema_version"] = 99
    (run_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    report = verify_production_run(run_dir)
    assert report.status == INVALID_RUN


def test_network_audit_is_derived_only_from_source_attempts():
    payload = {
        "source_attempts": [
            {
                "attempt_id": "att-1",
                "url": "https://www.jpx.co.jp/a",
                "status": "FOUND",
            },
            {
                "attempt_id": "att-2",
                "url": "https://finance.yahoo.co.jp/b",
                "status": "NOT_FOUND",
            },
        ]
    }
    audit = network_audit(payload)
    assert audit["request_count"] == 2
    assert audit["hosts"] == {"www.jpx.co.jp": 1, "finance.yahoo.co.jp": 1}
    assert audit["statuses"] == {"FOUND": 1, "NOT_FOUND": 1}
    assert audit["request_budget_respected"] is True


def test_network_audit_flags_a_request_budget_violation():
    payload = {
        "source_attempts": [
            {"attempt_id": "att-1", "url": "https://www.jpx.co.jp/a", "status": "FOUND"},
            {"attempt_id": "att-1", "url": "https://www.jpx.co.jp/a", "status": "FOUND"},
        ]
    }
    audit = network_audit(payload)
    assert audit["duplicate_attempt_ids"] == ["att-1"]
    assert audit["request_budget_respected"] is False


def test_diagnostic_statuses_are_never_written_into_artifacts(tmp_path):
    run_dir = _copy(NO_TRADE_FIXTURE, tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in run_dir.iterdir()
        if path.is_file()
    }
    verify_production_run(run_dir)
    after = {
        path.name: path.read_bytes()
        for path in run_dir.iterdir()
        if path.is_file()
    }
    assert before == after, "verification must be read-only"
    for payload in after.values():
        assert b"VERIFIED_CASE_" not in payload
        assert b"INVALID_RUN" not in payload


def test_verification_reuses_the_production_validators():
    """Guard against a second, drifting copy of the business rules."""
    import inspect

    from src import production_verify

    source = inspect.getsource(production_verify)
    assert "from src.market import" in source
    assert "validate_source_ledger" in source
    assert "validate_market_data" in source
    # no reimplementation of ranking / selection / risk logic here
    assert "def build_ranking" not in source
    assert "def build_selection" not in source


def test_case_c_requires_a_selected_selection(tmp_path):
    if not SELECTION_FIXTURE.is_dir():  # pragma: no cover - fixture guard
        pytest.skip("selection regression fixture is unavailable")
    run_dir = _copy(SELECTION_FIXTURE, tmp_path)
    report = verify_production_run(run_dir)
    assert report.status in {VERIFIED_CASE_C, INVALID_RUN}
