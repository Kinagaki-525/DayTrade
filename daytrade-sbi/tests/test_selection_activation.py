from __future__ import annotations

from pathlib import Path

import pytest

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config
from src.selection_activation import (
    HUMAN_ACTION_STATUS,
    MUTABLE_CONFIG_PATHS,
    SelectionActivationError,
    activate_selection_config,
    check_activation_preconditions,
    check_calibration_trust,
    find_threshold_pair,
    human_action_required_report,
)
from src.selection_calibration import (
    compute_calibration_context_sha256,
    sha256_raw_bytes,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH


PAIR_ID = "a" * 64
SOURCE_MATRIX = DEFAULT_SOURCE_MATRIX_PATH


def _cohort(config, source_matrix_path=SOURCE_MATRIX, **overrides) -> dict:
    """The calibration cohort identity that matches ``config`` exactly."""
    cohort = {
        "cohort_id": "0" * 64,
        "strategy_version": config["strategy_version"],
        "ranking_version": "ranking-v1",
        "ranking_schema_version": 1,
        "selection_version": config["selection"]["version"],
        "calibration_context_sha256": compute_calibration_context_sha256(config),
        "source_matrix_raw_sha256": sha256_raw_bytes(
            Path(source_matrix_path).read_bytes()
        ),
    }
    cohort.update(overrides)
    return cohort


def _calibration(
    status: str = "COMPLETE",
    pair_id: str = PAIR_ID,
    config: dict | None = None,
    cohort: dict | None = None,
    pairs: list | None = None,
) -> dict:
    effective_config = config if config is not None else load_strategy_config(
        DEFAULT_CONFIG_PATH
    )
    return {
        "calibration_status": status,
        "cohort": cohort if cohort is not None else _cohort(effective_config),
        "evaluated_threshold_pairs": pairs
        if pairs is not None
        else [
            {
                "pair_id": pair_id,
                "minimum_turnover_yen": 500000000,
                "maximum_relative_tick_size": {"numerator": 1, "denominator": 2000},
            }
        ],
    }


def _activate(config_path, calibration=None, pair_id=PAIR_ID, source_matrix=None):
    return activate_selection_config(
        config_path=config_path,
        calibration=calibration if calibration is not None else _calibration(),
        pair_id=pair_id,
        source_matrix_path=source_matrix or SOURCE_MATRIX,
    )


@pytest.fixture()
def config_path(tmp_path):
    target = tmp_path / "strategy.yaml"
    target.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_activation_applies_exactly_the_human_chosen_pair(config_path):
    summary = _activate(config_path)
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
    _activate(config_path)
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
        _activate(config_path, _calibration(status="DATA_UNAVAILABLE"))
    assert exc_info.value.code == "CALIBRATION_NOT_COMPLETE"


def test_unknown_pair_id_is_rejected(config_path):
    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, pair_id="b" * 64)
    assert exc_info.value.code == "THRESHOLD_PAIR_NOT_FOUND"


def test_activation_is_refused_when_selection_is_already_enabled(config_path):
    _activate(config_path)
    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path)
    assert exc_info.value.code in {
        "SELECTION_ALREADY_ENABLED",
        "SELECTION_THRESHOLDS_ALREADY_SET",
    }


def test_failed_activation_leaves_the_previous_config_untouched(config_path):
    original = config_path.read_text(encoding="utf-8")
    with pytest.raises(SelectionActivationError):
        _activate(config_path, _calibration(status="DATA_UNAVAILABLE"))
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


# --------------------------------------------- FIX-011: Calibration Trust ---


def test_activation_requires_a_source_matrix_argument():
    """--source-matrix is required: activation must be pinned to the exact
    Source Matrix bytes the calibration was produced under."""
    from src import cli

    parser = cli.build_parser()
    subparser = parser._subparsers._group_actions[0].choices[  # noqa: SLF001
        "activate-selection-config"
    ]
    action = next(
        item for item in subparser._actions if "--source-matrix" in item.option_strings
    )
    assert action.required is True

    with pytest.raises(SystemExit):
        cli.main(
            [
                "activate-selection-config",
                "--calibration", "c.json",
                "--pair-id", PAIR_ID,
            ]
        )


def test_calibration_trust_accepts_a_matching_cohort(config_path):
    config = load_strategy_config(config_path)
    identity = check_calibration_trust(_calibration(config=config), config, SOURCE_MATRIX)
    assert identity["strategy_version"] == config["strategy_version"]
    assert identity["selection_version"] == config["selection"]["version"]
    assert identity["calibration_context_sha256"] == compute_calibration_context_sha256(
        config
    )


def test_stale_calibration_cannot_activate_config(config_path):
    """A calibration whose context hash no longer describes this config is
    stale: its threshold pair was derived from a world that no longer exists."""
    config = load_strategy_config(config_path)
    original = config_path.read_text(encoding="utf-8")
    stale = _calibration(
        config=config,
        cohort=_cohort(config, calibration_context_sha256="f" * 64),
    )

    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, stale)
    assert exc_info.value.code == "CALIBRATION_CONTEXT_MISMATCH"
    assert config_path.read_text(encoding="utf-8") == original


def test_source_matrix_mismatch_cannot_activate_config(config_path, tmp_path):
    """Thresholds calibrated against a different Source Matrix may not be
    activated: the candidate universe itself may have changed."""
    config = load_strategy_config(config_path)
    original = config_path.read_text(encoding="utf-8")

    other_matrix = tmp_path / "other_source_matrix.yaml"
    other_matrix.write_text(
        SOURCE_MATRIX.read_text(encoding="utf-8") + "\n# a different byte\n",
        encoding="utf-8",
    )

    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, _calibration(config=config), source_matrix=other_matrix)
    assert exc_info.value.code == "CALIBRATION_SOURCE_MATRIX_MISMATCH"
    assert config_path.read_text(encoding="utf-8") == original


def test_different_strategy_cannot_activate_config(config_path):
    config = load_strategy_config(config_path)
    original = config_path.read_text(encoding="utf-8")
    other = _calibration(config=config, cohort=_cohort(config, strategy_version="v9"))

    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, other)
    assert exc_info.value.code == "CALIBRATION_STRATEGY_VERSION_MISMATCH"
    assert config_path.read_text(encoding="utf-8") == original


def test_different_selection_version_cannot_activate_config(config_path):
    config = load_strategy_config(config_path)
    other = _calibration(
        config=config, cohort=_cohort(config, selection_version="selection-v2")
    )
    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, other)
    assert exc_info.value.code == "CALIBRATION_SELECTION_VERSION_MISMATCH"


def test_calibration_without_a_cohort_is_rejected(config_path):
    config = load_strategy_config(config_path)
    calibration = _calibration(config=config)
    calibration.pop("cohort")
    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, calibration)
    assert exc_info.value.code == "CALIBRATION_COHORT_MISSING"


def test_missing_source_matrix_file_is_rejected(config_path, tmp_path):
    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, source_matrix=tmp_path / "nope.yaml")
    assert exc_info.value.code == "SOURCE_MATRIX_NOT_FOUND"


def test_unknown_pair_id_leaves_the_config_byte_identical(config_path):
    original = config_path.read_text(encoding="utf-8")
    with pytest.raises(SelectionActivationError):
        _activate(config_path, pair_id="b" * 64)
    assert config_path.read_text(encoding="utf-8") == original


def test_duplicate_pair_id_leaves_the_config_byte_identical(config_path):
    config = load_strategy_config(config_path)
    calibration = _calibration(config=config)
    calibration["evaluated_threshold_pairs"] *= 2
    original = config_path.read_text(encoding="utf-8")

    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, calibration)
    assert exc_info.value.code == "THRESHOLD_PAIR_AMBIGUOUS"
    assert config_path.read_text(encoding="utf-8") == original


def test_already_activated_config_cannot_be_activated_twice(config_path):
    _activate(config_path)
    activated = config_path.read_text(encoding="utf-8")

    config = load_strategy_config(config_path)
    with pytest.raises(SelectionActivationError) as exc_info:
        _activate(config_path, _calibration(config=config))
    assert exc_info.value.code in {
        "SELECTION_ALREADY_ENABLED",
        "SELECTION_THRESHOLDS_ALREADY_SET",
    }
    assert config_path.read_text(encoding="utf-8") == activated


def test_activation_reuses_the_existing_calibration_helpers():
    """Guard against a second, drifting copy of the identity computation."""
    import inspect

    from src import selection_activation

    source = inspect.getsource(selection_activation)
    assert "compute_calibration_context_sha256" in source
    assert "sha256_raw_bytes" in source
    assert "def compute_calibration_context" not in source
