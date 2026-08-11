from dataclasses import replace
from decimal import Decimal
import logging

from src.config import load_strategy_config
from src.screening import screen_market_record
from tests.factories import make_market_record
from tests.factories import make_source_record


def test_turnover_feature_and_minimum_turnover_passes():
    config = load_strategy_config()
    config["screening"]["minimum_turnover"]["enabled"] = True
    config["screening"]["minimum_turnover"]["threshold"] = 1000000

    base = make_market_record()
    turnover_source = make_source_record(
        field_name="turnover",
        value="2000000",
        source_id="YAHOO_JP_HISTORY",
        source_role="PRIMARY",
        information_type="OHLCV",
    )
    record = replace(
        base,
        turnover=Decimal("2000000"),
        sources=tuple(list(base.sources) + [turnover_source]),
    )

    result = screen_market_record(record, config)

    assert result.status == "ELIGIBLE"
    assert result.screening_status == "PASS"
    assert "minimum_turnover" in result.passed_rules


def test_turnover_missing_is_data_unavailable():
    config = load_strategy_config()
    config["screening"]["minimum_turnover"]["enabled"] = True
    config["screening"]["minimum_turnover"]["threshold"] = 1000000

    # Use default factory which has turnover=None and no turnover source
    record = make_market_record()

    result = screen_market_record(record, config)

    assert result.status == "DATA_UNAVAILABLE"
    assert result.screening_status == "DATA_UNAVAILABLE"
    assert result.unavailable_rules == ("minimum_turnover",)
