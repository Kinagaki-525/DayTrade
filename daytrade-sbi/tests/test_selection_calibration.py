from __future__ import annotations

import json

import pytest
import yaml

from src.config import DEFAULT_CONFIG_PATH
from src.contracts import validate_json_document
from src.selection_calibration import (
    SelectionCalibrationHardError,
    build_selection_calibration_report,
    collect_run_dates,
    evaluate_calibration_against_thresholds,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

from tests.factories import make_complete_ranking_payload, make_ranking_candidate
from tests.selection_calibration_fixtures import build_calibration_run_dir as _build_calibration_run_dir


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


def _tampered_source_matrix_path(tmp_path):
    payload = yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (tampered)"
    tampered_path = tmp_path / "tampered_source_matrix.yaml"
    tampered_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return tampered_path


# --- collect_run_dates -----------------------------------------------------


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
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )

    assert report["calibration_status"] == "NO_OBSERVATIONS"
    assert report["observations"] == []
    assert report["minimum_turnover_sensitivity"] == []
    assert report["maximum_relative_tick_size_sensitivity"] == []
    validate_json_document(report, "selection_calibration.schema.json")


# --- Full-cohort happy path (genuine, hash-consistent artifact chain) -----


def test_build_selection_calibration_report_full_cohort_happy_path(tmp_path):
    runs_dir = tmp_path / "runs"
    _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )

    validate_json_document(report, "selection_calibration.schema.json")
    assert report["calibration_status"] == "COMPLETE"
    summary = report["summary"]
    assert summary["run_directories_scanned"] == 1
    assert summary["ranking_artifacts_found"] == 1
    assert summary["matching_complete_observations"] == 1
    assert summary["ranking_data_unavailable_count"] == 0
    assert summary["config_mismatch_count"] == 0
    assert summary["source_matrix_mismatch_count"] == 0
    assert summary["missing_ranking_count"] == 0
    assert report["observations"][0]["ticker"] == "AA01"


def test_build_selection_calibration_report_data_unavailable_full_contract(tmp_path):
    runs_dir = tmp_path / "runs"
    _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "BB01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
        data_unavailable=True,
    )

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )

    validate_json_document(report, "selection_calibration.schema.json")
    summary = report["summary"]
    assert summary["ranking_data_unavailable_count"] == 1
    assert summary["matching_complete_observations"] == 0
    assert report["observations"] == []


def test_build_selection_calibration_report_corrupted_data_unavailable_is_hard_error(tmp_path):
    """A ranking.json claiming DATA_UNAVAILABLE whose upstream artifacts
    actually support a COMPLETE ranking must be rejected by full Ranking
    Contract re-verification, not silently counted."""
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "CC01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    # Forge ranking_status without regenerating the underlying (still
    # COMPLETE-supporting) upstream artifacts.
    ranking["ranking_status"] = "DATA_UNAVAILABLE"
    ranking["ranking_complete"] = False
    for candidate in ranking["candidates"]:
        candidate["final_rank"] = None
        candidate["rank_points"] = None
        candidate["feature_ranks"] = None
    ranking["summary"]["ranked_count"] = 0
    ranking["summary"]["top_ranked_ticker"] = None
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_RANKING_CONTRACT_VIOLATION"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


def test_build_selection_calibration_report_semantic_tamper_is_hard_error(tmp_path):
    """Hashes all genuinely match (an attacker who also recomputed and
    rewrote every hash consistently) but Rank1 fields inside ranking.json
    were altered inconsistently with the upstream artifacts. Full Ranking
    Contract re-verification (validate_ranking_output_contract's
    recompute-and-compare) must catch this."""
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [
            {"ticker": "DD01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "DD02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ],
    )
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    # Swap final_rank / top_ranked_ticker without touching input_hashes, so
    # every hash check still passes but the semantic content is forged.
    by_ticker = {c["ticker"]: c for c in ranking["candidates"]}
    by_ticker["DD01"]["final_rank"], by_ticker["DD02"]["final_rank"] = (
        by_ticker["DD02"]["final_rank"],
        by_ticker["DD01"]["final_rank"],
    )
    ranking["summary"]["top_ranked_ticker"] = "DD02"
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_RANKING_CONTRACT_VIOLATION"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


@pytest.mark.parametrize(
    "filename",
    ["event_gate.json", "candidates.json", "market_data.json", "sources.json", "strategy_snapshot.yaml"],
)
def test_build_selection_calibration_report_upstream_hash_tamper_is_hard_error(tmp_path, filename):
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "EE01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    target = run_dir / filename
    target.write_bytes(target.read_bytes() + b"\n# tampered\n")

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_INPUT_HASH_MISMATCH"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


def test_build_selection_calibration_report_source_matrix_tamper_is_cohort_split_not_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "FF01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    tampered_source_matrix_path = _tampered_source_matrix_path(tmp_path)

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=tampered_source_matrix_path,
    )

    validate_json_document(report, "selection_calibration.schema.json")
    summary = report["summary"]
    assert summary["source_matrix_mismatch_count"] == 1
    assert summary["matching_complete_observations"] == 0
    assert report["observations"] == []


def test_build_selection_calibration_report_missing_source_matrix_hard_errors_before_scanning(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    invalid_source_matrix_path = tmp_path / "invalid_source_matrix.yaml"
    invalid_source_matrix_path.write_text("not: [valid, source, matrix", encoding="utf-8")

    with pytest.raises(Exception):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=invalid_source_matrix_path,
        )


def test_build_selection_calibration_report_upstream_artifact_missing_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "GG01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    (run_dir / "sources.json").unlink()

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_UPSTREAM_ARTIFACT_MISSING"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


# --- Counters using minimal (non-full-contract) fixtures -------------------


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
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


def test_build_selection_calibration_report_schema_invalid_ranking_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "2026-08-08"
    run_dir.mkdir(parents=True)
    (run_dir / "ranking.json").write_text(json.dumps({"not": "valid"}), encoding="utf-8")

    with pytest.raises(ValueError):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


def test_build_selection_calibration_report_missing_ranking_and_config_mismatch_counters(tmp_path):
    runs_dir = tmp_path / "runs"

    # Missing ranking.json.
    (runs_dir / "2026-08-05").mkdir(parents=True)

    # Non-date entry, ignored.
    (runs_dir / "scratch").mkdir(parents=True)

    # config_sha256 mismatch: never reaches upstream-artifact checks.
    _write_ranking(runs_dir / "2026-08-06", "2026-08-06", config_sha256="b" * 64)

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )

    validate_json_document(report, "selection_calibration.schema.json")
    summary = report["summary"]
    assert summary["run_directories_scanned"] == 2
    assert summary["ignored_non_date_entries"] == 1
    assert summary["missing_ranking_count"] == 1
    assert summary["config_mismatch_count"] == 1
    assert summary["matching_complete_observations"] == 0


# --- Threshold evaluation (unchanged pure logic) ---------------------------


def _calibration_report_with_two_observations():
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
