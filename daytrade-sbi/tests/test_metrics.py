from decimal import Decimal
from pathlib import Path

import pytest

from src.metrics import calculate_metrics, load_trades


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_metrics_for_empty_rows():
    metrics = calculate_metrics([])

    assert metrics["total_trades"] == 0
    assert metrics["win_trades"] == 0
    assert metrics["loss_trades"] == 0
    assert metrics["win_rate"] is None
    assert metrics["cumulative_profit_loss_yen"] == Decimal("0")
    assert metrics["average_profit"] is None
    assert metrics["average_loss"] is None
    assert metrics["average_profit_loss_per_trade"] is None
    assert metrics["max_profit"] is None
    assert metrics["max_loss"] is None
    assert metrics["take_profit_count"] == 0
    assert metrics["stop_loss_count"] == 0
    assert metrics["end_of_day_count"] == 0


def test_metrics_for_completed_trades():
    rows = [
        {"profit_loss_yen": "800", "exit_reason": "take_profit"},
        {"profit_loss_yen": "-500", "exit_reason": "stop_loss"},
        {"profit_loss_yen": "0", "exit_reason": "end_of_day"},
    ]

    metrics = calculate_metrics(rows)

    assert metrics["total_trades"] == 3
    assert metrics["calculable_trades"] == 3
    assert metrics["uncalculable_trades"] == 0
    assert metrics["win_trades"] == 1
    assert metrics["loss_trades"] == 1
    assert metrics["win_rate"] == pytest.approx(1 / 3)
    assert metrics["cumulative_profit_loss_yen"] == Decimal("300")
    assert metrics["average_profit"] == Decimal("800")
    assert metrics["average_loss"] == Decimal("-500")
    assert metrics["average_profit_loss_per_trade"] == Decimal("100")
    assert metrics["max_profit"] == Decimal("800")
    assert metrics["max_loss"] == Decimal("-500")
    assert metrics["take_profit_count"] == 1
    assert metrics["stop_loss_count"] == 1
    assert metrics["end_of_day_count"] == 1


def test_metrics_do_not_infer_missing_profit_loss():
    rows = [
        {"profit_loss_yen": "", "exit_reason": "take_profit"},
        {"profit_loss_yen": "120", "exit_reason": "other"},
    ]

    metrics = calculate_metrics(rows)

    assert metrics["total_trades"] == 2
    assert metrics["calculable_trades"] == 1
    assert metrics["uncalculable_trades"] == 1
    assert metrics["cumulative_profit_loss_yen"] == Decimal("120")
    assert metrics["take_profit_count"] == 1


def test_max_profit_and_loss_only_use_matching_trade_results():
    loss_metrics = calculate_metrics(
        [{"profit_loss_yen": "-100", "exit_reason": "stop_loss"}]
    )
    win_metrics = calculate_metrics(
        [{"profit_loss_yen": "100", "exit_reason": "take_profit"}]
    )

    assert loss_metrics["max_profit"] is None
    assert loss_metrics["max_loss"] == Decimal("-100")
    assert win_metrics["max_profit"] == Decimal("100")
    assert win_metrics["max_loss"] is None


def test_metrics_do_not_accept_non_finite_profit_loss():
    metrics = calculate_metrics(
        [{"profit_loss_yen": "NaN", "exit_reason": "other"}]
    )

    assert metrics["calculable_trades"] == 0
    assert metrics["uncalculable_trades"] == 1


def test_load_trades_rejects_missing_required_columns():
    with pytest.raises(ValueError, match="Missing required CSV columns"):
        load_trades(FIXTURES_DIR / "trades_missing_columns.csv")


def test_load_trades_accepts_header_only_file():
    assert load_trades(FIXTURES_DIR / "trades_header_only.csv") == []


def test_load_trades_rejects_unknown_exit_reason():
    with pytest.raises(ValueError, match="Invalid exit_reason"):
        load_trades(FIXTURES_DIR / "trades_invalid_exit_reason.csv")
