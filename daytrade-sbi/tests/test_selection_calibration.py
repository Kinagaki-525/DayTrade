from __future__ import annotations

import hashlib
import json

import pytest

from src.contracts import validate_json_document
from src.selection_calibration import (
    SelectionCalibrationHardError,
    build_selection_calibration_report,
    collect_run_dates,
    evaluate_calibration_against_thresholds,
)

from tests.factories import make_complete_ranking_payload, make_ranking_candidate


STRATEGY_VERSION = "v1"
CONFIG_SHA = "a" * 64
SOURCE_MATRIX_SHA = "9" * 64


def _write_ranking(run_dir, target_date, **overrides):
    run_dir.mkdir(parents=True, exist_ok=True)
    ranking = make_complete_ranking_payload(
        target_date=target_date,
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    ranking.update(overrides)
    (run_dir / "ranking.json").write_text(
        json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ranking


def test_collect_run_dates_ignores_non_date_entries(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "2026-08-10").mkdir(parents=True)
    (runs_dir / "2026-08-09").mkdir(parents=True)
    (runs_dir / "not-a-date").mkdir(parents=True)
    (runs_dir / "README.md").write_text("x", encoding="utf-8")

    dates, ignored = collect_run_dates(runs_dir)

    assert dates == ["2026-08-09", "2026-08-10"]
    assert ignored == 2


def test_build_selection_calibration_report_no_observations(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        cohort_strategy_version=STRATEGY_VERSION,
        cohort_config_sha256=CONFIG_SHA,
        source_matrix_sha256=SOURCE_MATRIX_SHA,
        validate_json_document=validate_json_document,
    )

    assert report["calibration_status"] == "NO_OBSERVATIONS"
    assert report["observations"] == []
    assert report["minimum_turnover_sensitivity"] == []
    assert report["maximum_relative_tick_size_sensitivity"] == []
    validate_json_document(report, "selection_calibration.schema.json")


def test_build_selection_calibration_report_counts_and_observations(tmp_path):
    runs_dir = tmp_path / "runs"

    # Missing ranking.json.
    (runs_dir / "2026-08-05").mkdir(parents=True)

    # Non-date entry, ignored.
    (runs_dir / "scratch").mkdir(parents=True)

    # config_sha256 mismatch.
    _write_ranking(runs_dir / "2026-08-06", "2026-08-06", config_sha256="b" * 64)

    # DATA_UNAVAILABLE.
    _write_ranking(
        runs_dir / "2026-08-07",
        "2026-08-07",
        ranking_status="DATA_UNAVAILABLE",
        ranking_complete=False,
    )

    # Two valid COMPLETE observations with distinct turnover/tick values.
    candidate_a = make_ranking_candidate(
        ticker="1111", turnover_yen="10000000", tick_size_yen="1", entry_trigger_yen="1000"
    )
    _write_ranking(
        runs_dir / "2026-08-08",
        "2026-08-08",
        candidates=[candidate_a],
        input_candidate_tickers=["1111"],
        summary={
            "input_count": 1,
            "valid_input_count": 1,
            "data_unavailable_count": 0,
            "ranked_count": 1,
            "top_ranked_ticker": "1111",
        },
    )
    candidate_b = make_ranking_candidate(
        ticker="2222", turnover_yen="20000000", tick_size_yen="1", entry_trigger_yen="2000"
    )
    _write_ranking(
        runs_dir / "2026-08-09",
        "2026-08-09",
        candidates=[candidate_b],
        input_candidate_tickers=["2222"],
        summary={
            "input_count": 1,
            "valid_input_count": 1,
            "data_unavailable_count": 0,
            "ranked_count": 1,
            "top_ranked_ticker": "2222",
        },
    )

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        cohort_strategy_version=STRATEGY_VERSION,
        cohort_config_sha256=CONFIG_SHA,
        source_matrix_sha256=SOURCE_MATRIX_SHA,
        validate_json_document=validate_json_document,
    )

    validate_json_document(report, "selection_calibration.schema.json")
    summary = report["summary"]
    assert summary["run_directories_scanned"] == 5
    assert summary["ignored_non_date_entries"] == 1
    assert summary["missing_ranking_count"] == 1
    assert summary["config_mismatch_count"] == 1
    assert summary["ranking_data_unavailable_count"] == 1
    assert summary["matching_complete_observations"] == 2

    dates = [obs["target_date"] for obs in report["observations"]]
    assert dates == ["2026-08-08", "2026-08-09"]

    turnover_thresholds = [e["threshold_yen"] for e in report["minimum_turnover_sensitivity"]]
    assert turnover_thresholds == [10000000, 20000000]
    assert report["minimum_turnover_sensitivity"][0]["pass_count"] == 2
    assert report["minimum_turnover_sensitivity"][1]["pass_count"] == 1

    tick_ratios = [
        (e["threshold_ratio"]["numerator"], e["threshold_ratio"]["denominator"])
        for e in report["maximum_relative_tick_size_sensitivity"]
    ]
    # 1/1000 < 1/2000 is false: 1/2000 < 1/1000 numerically, so ascending
    # order is 1/2000 then 1/1000.
    assert tick_ratios == [(1, 2000), (1, 1000)]


def test_build_selection_calibration_report_target_date_mismatch_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "2026-08-08"
    ranking = make_complete_ranking_payload(
        target_date="2026-08-09",  # mismatched vs directory name
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    run_dir.mkdir(parents=True)
    (run_dir / "ranking.json").write_text(json.dumps(ranking), encoding="utf-8")

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_RANKING_TARGET_DATE_MISMATCH"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            cohort_strategy_version=STRATEGY_VERSION,
            cohort_config_sha256=CONFIG_SHA,
            source_matrix_sha256=SOURCE_MATRIX_SHA,
            validate_json_document=validate_json_document,
        )


def test_build_selection_calibration_report_schema_invalid_ranking_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "2026-08-08"
    run_dir.mkdir(parents=True)
    (run_dir / "ranking.json").write_text(json.dumps({"not": "valid"}), encoding="utf-8")

    with pytest.raises(ValueError):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            cohort_strategy_version=STRATEGY_VERSION,
            cohort_config_sha256=CONFIG_SHA,
            source_matrix_sha256=SOURCE_MATRIX_SHA,
            validate_json_document=validate_json_document,
        )


def _calibration_report_with_two_observations():
    candidate_a = make_ranking_candidate(
        ticker="1111", turnover_yen="10000000", tick_size_yen="1", entry_trigger_yen="1000"
    )
    candidate_b = make_ranking_candidate(
        ticker="2222", turnover_yen="20000000", tick_size_yen="1", entry_trigger_yen="500"
    )
    return {
        "calibration_status": "COMPLETE",
        "observations": [
            {
                "target_date": "2026-08-08",
                "ticker": "1111",
                "turnover_yen": "10000000",
                "relative_tick_size": {"numerator_yen": "1", "denominator_yen": "1000"},
            },
            {
                "target_date": "2026-08-09",
                "ticker": "2222",
                "turnover_yen": "20000000",
                "relative_tick_size": {"numerator_yen": "1", "denominator_yen": "500"},
            },
        ],
    }


def test_evaluate_calibration_against_thresholds_classifies_observations():
    report = _calibration_report_with_two_observations()

    result = evaluate_calibration_against_thresholds(
        calibration_report=report,
        source_calibration_sha256="c" * 64,
        minimum_turnover_yen=15000000,
        maximum_relative_tick_numerator=1,
        maximum_relative_tick_denominator=800,
    )

    assert result["observation_count"] == 2
    # 1111: turnover 10M < 15M (REJECT TURNOVER), tick 1/1000 <= 1/800 (PASS)
    # 2222: turnover 20M >= 15M (PASS), tick 1/500 <= 1/800 is False (REJECT RELATIVE_TICK)
    assert result["selected_count"] == 0
    assert result["no_trade_count"] == 2
    assert result["rejected_turnover_only_count"] == 1
    assert result["rejected_relative_tick_only_count"] == 1
    assert result["rejected_both_count"] == 0
    assert result["selection_rate"] == {"numerator": 0, "denominator": 2}
    validate_json_document(result, "selection_threshold_evaluation.schema.json")


def test_evaluate_calibration_against_thresholds_no_observations_is_hard_error():
    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_NO_OBSERVATIONS"):
        evaluate_calibration_against_thresholds(
            calibration_report={"calibration_status": "NO_OBSERVATIONS", "observations": []},
            source_calibration_sha256="c" * 64,
            minimum_turnover_yen=1,
            maximum_relative_tick_numerator=1,
            maximum_relative_tick_denominator=500,
        )


def test_evaluate_calibration_against_thresholds_rejects_non_reduced_ratio():
    report = _calibration_report_with_two_observations()
    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_THRESHOLD_INVALID"):
        evaluate_calibration_against_thresholds(
            calibration_report=report,
            source_calibration_sha256="c" * 64,
            minimum_turnover_yen=1,
            maximum_relative_tick_numerator=2,
            maximum_relative_tick_denominator=1000,
        )


def test_evaluate_calibration_against_thresholds_rejects_non_positive_threshold():
    report = _calibration_report_with_two_observations()
    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_THRESHOLD_INVALID"):
        evaluate_calibration_against_thresholds(
            calibration_report=report,
            source_calibration_sha256="c" * 64,
            minimum_turnover_yen=0,
            maximum_relative_tick_numerator=1,
            maximum_relative_tick_denominator=500,
        )
