from src.config import load_strategy_config
from src.screening import screen_market_record
from tests.factories import make_market_record


def test_fixed_screening_accepts_affordable_sourced_record():
    result = screen_market_record(make_market_record(), load_strategy_config())

    assert result.status == "ELIGIBLE"
    assert result.order_plan is not None
    assert "minimum_volume" in result.unresolved_screening


def test_fixed_screening_accepts_old_over_50000_band():
    result = screen_market_record(
        make_market_record(previous_high="600", tick_size="1"),
        load_strategy_config(),
    )

    assert result.status == "ELIGIBLE"
    assert result.order_plan is not None
    assert str(result.order_plan.estimated_purchase_amount) == "60200"


def test_fixed_screening_rejects_unaffordable_position():
    result = screen_market_record(
        make_market_record(previous_high="1000", tick_size="1"),
        load_strategy_config(),
    )

    assert result.status == "REJECTED"
    assert "entry_limit multiplied by shares exceeds capital" in result.reasons


def test_unapproved_screening_value_is_not_silently_applied():
    config = load_strategy_config()
    config["screening"]["minimum_volume"] = 100000

    result = screen_market_record(make_market_record(), config)

    assert result.status == "REJECTED"
    assert (
        "screening.minimum_volume has a value but no user-approved filter implementation"
        in result.reasons
    )
