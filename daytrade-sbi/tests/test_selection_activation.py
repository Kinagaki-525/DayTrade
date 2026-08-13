from __future__ import annotations

import pytest

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config
from src.selection_activation import (
    HUMAN_ACTION_STATUS,
    MUTABLE_CONFIG_PATHS,
    SelectionActivationError,
    activate_selection_config,
    check_activation_preconditions,
    find_threshold_pair,
    human_action_required_report,
)


PAIR_ID = "a" * 64


def _calibration(status: str = "COMPLETE", pair_id: str = PAIR_ID) -> dict:
    return {
        "calibration_status": status,
        "evaluated_threshold_pairs": [
            {
                "pair_id": pair_id,
                "minimum_turnover_yen": 500000000,
                "maximum_relative_tick_size": {"numerator": 1, "denominator": 2000},
            }
        ],
    }


@pytest.fixture()
def config_path(tmp_path):
    target = tmp_path / "strategy.yaml"
    target.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_activation_applies_exactly_the_human_chosen_pair(config_path):
    summary = activate_selection_config(
        config_path=config_path, calibration=_calibration(), pair_id=PAIR_ID
    )
    assert summary["status"] == "SELECTION_CONFIG_ACTIVATED"
    assert summary["pair_id"] == PAIR_ID
    assert summary["downstream_invalidated"] is True
    assert summary["mutated_paths"] == list(MUTABLE_CONFIG_PATHS)

    config = load_strategy_config(config_path)
    selection = config["selection"]
    assert selection["enabled"] is True
    assert selection["rules"]["minimum_turnover_yen"]["threshold_yen"] == 500000000
    assert selection["rules"]["maximum_relative_tick_size"]["threshold_ratio"] == {
        "numerator": 1,
        "denominator": 2000,
    }


def test_activation_changes_nothing_else_in_the_config(config_path):
    before = load_strategy_config(config_path)
    activate_selection_config(
        config_path=config_path, calibration=_calibration(), pair_id=PAIR_ID
    )
    after = load_strategy_config(config_path)

    for key in before:
        if key == "selection":
            continue
        assert before[key] == after[key], f"{key} must not change during activation"
    # frozen strategy values in particular
    assert after["capital"]["total_yen"] == before["capital"]["total_yen"]
    assert after["selection"]["version"] == "selection-v1"
    assert after["selection"]["candidate_policy"] == "rank1_only"
    assert after["selection"]["fallback_policy"] == "none"


def test_incomplete_calibration_is_rejected(config_path):
    with pytest.raises(SelectionActivationError) as exc_info:
        activate_selection_config(
            config_path=config_path,
            calibration=_calibration(status="DATA_UNAVAILABLE"),
            pair_id=PAIR_ID,
        )
    assert exc_info.value.code == "CALIBRATION_NOT_COMPLETE"


def test_unknown_pair_id_is_rejected(config_path):
    with pytest.raises(SelectionActivationError) as exc_info:
        activate_selection_config(
            config_path=config_path, calibration=_calibration(), pair_id="b" * 64
        )
    assert exc_info.value.code == "THRESHOLD_PAIR_NOT_FOUND"


def test_activation_is_refused_when_selection_is_already_enabled(config_path):
    activate_selection_config(
        config_path=config_path, calibration=_calibration(), pair_id=PAIR_ID
    )
    with pytest.raises(SelectionActivationError) as exc_info:
        activate_selection_config(
            config_path=config_path, calibration=_calibration(), pair_id=PAIR_ID
        )
    assert exc_info.value.code in {
        "SELECTION_ALREADY_ENABLED",
        "SELECTION_THRESHOLDS_ALREADY_SET",
    }


def test_failed_activation_leaves_the_previous_config_untouched(config_path):
    original = config_path.read_text(encoding="utf-8")
    with pytest.raises(SelectionActivationError):
        activate_selection_config(
            config_path=config_path,
            calibration=_calibration(status="DATA_UNAVAILABLE"),
            pair_id=PAIR_ID,
        )
    assert config_path.read_text(encoding="utf-8") == original
    assert not list(config_path.parent.glob(".*activation.tmp"))


def test_shipped_production_config_is_still_uncalibrated():
    """The repository must ship with Selection off and thresholds null."""
    config = load_strategy_config()
    selection = config["selection"]
    assert selection["enabled"] is False
    assert selection["rules"]["minimum_turnover_yen"]["threshold_yen"] is None
    assert selection["rules"]["maximum_relative_tick_size"]["threshold_ratio"] is None
    check_activation_preconditions(config)


def test_agent_must_report_human_action_instead_of_choosing():
    report = human_action_required_report(_calibration())
    assert report["status"] == HUMAN_ACTION_STATUS
    assert report["available_pair_ids"] == [PAIR_ID]
    # deliberately no "recommended_pair_id" / "best_pair" key anywhere
    assert not any("recommend" in key or "best" in key for key in report)


def test_find_threshold_pair_rejects_duplicates():
    calibration = _calibration()
    calibration["evaluated_threshold_pairs"] *= 2
    with pytest.raises(SelectionActivationError) as exc_info:
        find_threshold_pair(calibration, PAIR_ID)
    assert exc_info.value.code == "THRESHOLD_PAIR_AMBIGUOUS"
