from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.contracts import validate_json_document
from src.execution import append_trade, build_trade_row
from src.metrics import load_trades
from tests.factories import make_market_record


METADATA = {
    "strategy_version": "v1",
    "config_sha256": "a" * 64,
}


def make_execution(**overrides):
    payload = {
        "schema_version": 1,
        "trade_date": "2026-08-10",
        **METADATA,
        "ticker": "1234",
        "actual_entry": "402",
        "actual_exit": "410",
        "shares": 100,
        "profit_loss_yen": "800",
        "exit_reason": "take_profit",
        "entry_time": "09:10",
        "exit_time": "10:05:30",
        "notes": None,
    }
    payload.update(overrides)
    return payload


def make_recommendation():
    return {
        "schema_version": 1,
        "target_date": "2026-08-10",
        **METADATA,
        "decision": "TRADE",
        "strategy_type": "previous_day_high_breakout",
        "ticker": "1234",
        "company_name": "Example Co.",
        "previous_high": "400",
        "tick_size": "1",
        "entry_trigger": "401",
        "entry_limit": "402",
        "take_profit": "410",
        "stop_loss": "397",
        "shares": 100,
        "selection_reasons": ["confirmed comparison"],
        "source_urls": ["https://example.test/source"],
        "notes": None,
    }


def make_risk_result(status="PASS"):
    return {
        "schema_version": 1,
        "target_date": "2026-08-10",
        **METADATA,
        "decision": "TRADE",
        "status": status,
        "ticker": "1234",
        "required_capital_yen": "40200",
        "expected_loss_yen": "500",
        "violations": [] if status == "PASS" else ["test violation"],
    }


def build_row(execution=None, risk_result=None):
    return build_trade_row(
        execution or make_execution(),
        make_recommendation(),
        risk_result or make_risk_result(),
        "2026-08-10",
        [make_market_record()],
    )


def test_execution_schema_accepts_confirmed_completed_trade():
    validate_json_document(make_execution(), "execution_result.schema.json")


def test_execution_schema_requires_notes_for_other_exit_reason():
    with pytest.raises(ValueError, match="execution_result.schema.json"):
        validate_json_document(
            make_execution(exit_reason="other", notes=None),
            "execution_result.schema.json",
        )


def test_build_trade_row_uses_planned_context_and_confirmed_execution():
    row = build_row()

    assert row["previous_close"] == "390"
    assert row["entry_trigger"] == "401"
    assert row["actual_entry"] == "402"
    assert row["actual_exit"] == "410"
    assert row["profit_loss_yen"] == "800"
    assert row["entry_time"] == "09:10:00"


def test_build_trade_row_does_not_infer_profit_loss():
    row = build_row(make_execution(profit_loss_yen=None))

    assert row["profit_loss_yen"] == ""


def test_build_trade_row_rejects_partial_fill_until_todo_is_resolved():
    with pytest.raises(ValueError, match="Partial or mismatched fills"):
        build_row(make_execution(shares=50))


def test_build_trade_row_requires_passed_risk_result():
    with pytest.raises(ValueError, match="PASS"):
        build_row(risk_result=make_risk_result("REJECTED"))


def test_build_trade_row_rejects_exit_before_entry():
    with pytest.raises(ValueError, match="exit_time"):
        build_row(make_execution(entry_time="10:00", exit_time="09:59"))


def test_build_trade_row_rejects_company_mismatch():
    with pytest.raises(ValueError, match="company_name"):
        build_trade_row(
            make_execution(),
            make_recommendation(),
            make_risk_result(),
            "2026-08-10",
            [replace(make_market_record(), company_name="Different Co.")],
        )


def test_build_trade_row_revalidates_market_data():
    with pytest.raises(ValueError, match="market data validation failed"):
        build_trade_row(
            make_execution(),
            make_recommendation(),
            make_risk_result(),
            "2026-08-10",
            [replace(make_market_record(), sources=())],
        )


def test_append_trade_is_idempotent(tmp_path):
    path = tmp_path / "trades.csv"
    row = build_row()

    assert append_trade(row, path) is True
    assert append_trade(row, path) is False
    assert load_trades(path) == [row]


def test_append_trade_rejects_conflicting_duplicate(tmp_path):
    path = tmp_path / "trades.csv"
    row = build_row()
    append_trade(row, path)
    conflicting = deepcopy(row)
    conflicting["entry_time"] = "09:11:00"

    with pytest.raises(ValueError, match="conflicting trade record"):
        append_trade(conflicting, path)
