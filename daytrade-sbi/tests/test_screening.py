from dataclasses import replace

from src.config import load_strategy_config
from src.screening import screen_market_record
from tests.factories import make_market_record


def test_fixed_screening_accepts_affordable_sourced_record():
    result = screen_market_record(make_market_record(), load_strategy_config())

    assert result.status == "ELIGIBLE"
    assert result.screening_status == "PASS"
    assert result.order_plan is not None
    assert "minimum_volume" in result.unresolved_screening
    assert result.features["previous_day_volume"]["value"] == 1000000
    assert result.features["daily_range_yen"]["value"] == 15
    assert result.features["estimated_turnover"]["estimated"] is True


def test_fixed_screening_accepts_old_over_50000_band():
    result = screen_market_record(
        make_market_record(previous_high="600", tick_size="1"),
        load_strategy_config(),
    )

    assert result.status == "ELIGIBLE"
    assert result.screening_status == "PASS"
    assert result.order_plan is not None
    assert str(result.order_plan.estimated_purchase_amount) == "60200"


def test_fixed_screening_rejects_unaffordable_position():
    result = screen_market_record(
        make_market_record(previous_high="1000", tick_size="1"),
        load_strategy_config(),
    )

    assert result.status == "REJECTED"
    assert result.screening_status == "REJECT"
    assert "entry_limit multiplied by shares exceeds capital" in result.reasons


def test_disabled_screening_rule_value_is_not_applied():
    config = load_strategy_config()
    config["screening"]["minimum_volume"]["threshold"] = 2000000

    result = screen_market_record(make_market_record(), config)

    assert result.status == "ELIGIBLE"
    assert result.screening_status == "PASS"


def test_enabled_minimum_volume_accepts_boundary_value():
    config = load_strategy_config()
    config["screening"]["minimum_volume"]["enabled"] = True
    config["screening"]["minimum_volume"]["threshold"] = 1000000

    result = screen_market_record(make_market_record(), config)

    assert result.status == "ELIGIBLE"
    assert result.screening_status == "PASS"
    assert result.passed_rules == ("minimum_volume",)


def test_enabled_minimum_volume_rejects_below_threshold_with_provenance():
    config = load_strategy_config()
    config["screening"]["minimum_volume"]["enabled"] = True
    config["screening"]["minimum_volume"]["threshold"] = 1000001

    result = screen_market_record(make_market_record(), config)

    assert result.status == "REJECTED"
    assert result.screening_status == "REJECT"
    assert result.failed_rules == ("minimum_volume",)
    assert result.reason_codes == ("VOLUME_BELOW_MINIMUM",)
    evaluation = next(
        item for item in result.rule_evaluations if item["rule_id"] == "minimum_volume"
    )
    assert evaluation["source_refs"]
    assert evaluation["source_urls"]


def test_enabled_trade_critical_rule_missing_input_is_data_unavailable():
    config = load_strategy_config()
    config["screening"]["minimum_turnover"]["enabled"] = True
    config["screening"]["minimum_turnover"]["threshold"] = 1000000

    result = screen_market_record(make_market_record(), config)

    assert result.status == "DATA_UNAVAILABLE"
    assert result.screening_status == "DATA_UNAVAILABLE"
    assert result.unavailable_rules == ("minimum_turnover",)


def test_feature_only_values_do_not_reject_candidates():
    result = screen_market_record(make_market_record(), load_strategy_config())

    assert result.status == "ELIGIBLE"
    assert result.features["daily_range_pct"]["value"] is not None
    assert result.features["previous_day_change_pct"]["value"] is not None
    assert not result.failed_rules


def test_entry_execution_and_event_rules_are_not_hard_screening_filters():
    config = load_strategy_config()
    config["screening"]["maximum_gap_percent"]["enabled"] = True
    config["screening"]["maximum_gap_percent"]["threshold"] = 1
    config["screening"]["maximum_spread"]["enabled"] = True
    config["screening"]["maximum_spread"]["threshold"] = 1
    config["screening"]["exclude_earnings"]["enabled"] = True
    config["screening"]["exclude_earnings"]["threshold"] = False
    config["screening"]["exclude_special_disclosures"]["enabled"] = True
    config["screening"]["exclude_special_disclosures"]["threshold"] = False
    record = replace(
        make_market_record(),
        spread=10,
        earnings_scheduled=True,
        special_disclosures=True,
    )

    result = screen_market_record(record, config)

    assert result.status == "ELIGIBLE"
    assert result.screening_status == "PASS"
    skipped = {
        item["rule_id"]: item["result"]
        for item in result.rule_evaluations
        if item["enabled"]
    }
    assert skipped == {
        "maximum_gap_percent": "SKIPPED_PHASE",
        "maximum_spread": "SKIPPED_PHASE",
        "exclude_earnings": "SKIPPED_PHASE",
        "exclude_special_disclosures": "SKIPPED_PHASE",
    }
