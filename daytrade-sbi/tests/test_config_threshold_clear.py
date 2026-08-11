import logging

from src.config import load_strategy_config, normalize_screening_rules


def test_disabled_rule_threshold_is_cleared_and_logs_warning(caplog):
    config = load_strategy_config()
    # Ensure minimum_volume is present and set to disabled with a threshold
    config["screening"]["minimum_volume"]["enabled"] = False
    config["screening"]["minimum_volume"]["threshold"] = 12345

    with caplog.at_level(logging.WARNING):
        normalized = normalize_screening_rules(config["screening"])

    assert normalized["minimum_volume"]["threshold"] is None
    assert any(
        "screening.minimum_volume: disabled rule supplied threshold; clearing" in rec.getMessage()
        for rec in caplog.records
    )
