from src.config import load_strategy_config
from src.risk import OrderProposal, evaluate_order


def proposal(**overrides):
    values = {
        "target_date": "2026-08-10",
        "ticker": "1234",
        "company_name": "Example Co.",
        "strategy_type": "previous_day_high_breakout",
        "previous_high": "398",
        "tick_size": "1",
        "entry_trigger": "399",
        "entry_limit": "400",
        "take_profit": "408",
        "stop_loss": "395",
        "shares": 100,
    }
    values.update(overrides)
    return OrderProposal.from_dict(values)


def evaluate(item, *, current_positions=0, trades_today=0):
    return evaluate_order(
        item,
        load_strategy_config(),
        market_data_valid=True,
        market_target_date="2026-08-10",
        market_previous_high=item.previous_high,
        market_tick_size=item.tick_size,
        current_positions=current_positions,
        trades_today=trades_today,
    )


def test_entry_limit_500_for_100_shares_passes():
    result = evaluate(
        proposal(
            previous_high="498",
            entry_trigger="499",
            entry_limit="500",
            take_profit="508",
            stop_loss="495",
        )
    )

    assert result.status == "PASS"
    assert str(result.required_capital_yen) == "50000"


def test_entry_limit_500_5_for_100_shares_is_rejected():
    result = evaluate(
        proposal(
            previous_high="499.5",
            tick_size="0.5",
            entry_trigger="500.0",
            entry_limit="500.5",
            take_profit="508.5",
            stop_loss="495.5",
        )
    )

    assert result.status == "REJECTED"
    assert "Required capital exceeds capital.total_yen" in result.violations


def test_expected_loss_500_passes():
    result = evaluate(proposal())

    assert result.status == "PASS"
    assert str(result.expected_loss_yen) == "500"


def test_expected_loss_600_is_rejected_without_correction():
    result = evaluate(proposal(stop_loss="394"))

    assert result.status == "REJECTED"
    assert "Expected loss exceeds risk.max_loss_per_trade_yen" in result.violations
    assert "stop_loss does not match strategy calculation" in result.violations


def test_missing_required_data_is_rejected():
    result = evaluate(proposal(entry_limit=None))

    assert result.status == "REJECTED"
    assert "Missing or invalid required field: entry_limit" in result.violations


def test_existing_position_is_rejected():
    result = evaluate(proposal(), current_positions=1)

    assert result.status == "REJECTED"
    assert "max_positions would be exceeded" in result.violations


def test_target_date_must_match_validated_market_data():
    result = evaluate(proposal(target_date="2026-08-11"))

    assert result.status == "REJECTED"
    assert "target_date does not match validated market data" in result.violations
