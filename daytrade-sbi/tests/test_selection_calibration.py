from __future__ import annotations

import json

import pytest
import yaml

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config
from src.contracts import validate_json_document
from src.selection_calibration import (
    SelectionCalibrationHardError,
    build_selection_calibration_report,
    collect_run_dates,
    compute_calibration_context_sha256,
    evaluate_calibration_against_thresholds,
    resolve_historical_source_matrix_path,
    sha256_raw_bytes,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

from tests.selection_calibration_fixtures import build_calibration_run_dir as _build_calibration_run_dir


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


def test_build_selection_calibration_report_data_unavailable_no_runs(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )

    assert report["calibration_status"] == "DATA_UNAVAILABLE"
    assert report["dataset"]["observations"] == []
    assert report["dataset"]["observation_count"] == 0
    assert report["candidate_space"]["evaluated_pair_count"] == 0
    assert report["evaluated_threshold_pairs"] == []
    assert report["readiness"] == {"status": "NOT_ASSESSED", "policy_version": None}
    assert "generated_at" not in report
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
    dataset = report["dataset"]
    assert dataset["observation_count"] == 1
    assert dataset["excluded_count"] == 0
    assert dataset["observations"][0]["rank1"]["ticker"] == "AA01"
    assert dataset["observations"][0]["cohort_id"] == report["cohort"]["cohort_id"]
    assert report["candidate_space"]["evaluated_pair_count"] == (
        report["candidate_space"]["turnover_candidate_count"]
        * report["candidate_space"]["relative_tick_candidate_count"]
    )
    assert len(report["evaluated_threshold_pairs"]) == report["candidate_space"]["evaluated_pair_count"]


def test_build_selection_calibration_report_data_unavailable_ranking_is_excluded(tmp_path):
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
    dataset = report["dataset"]
    assert dataset["observation_count"] == 0
    assert dataset["excluded_count"] == 1
    excluded = dataset["excluded_observations"][0]
    assert excluded["disposition"] == "TRUSTED_NOT_ELIGIBLE"
    assert excluded["reason_code"] == "RANKING_DATA_UNAVAILABLE"
    assert report["calibration_status"] == "DATA_UNAVAILABLE"


def test_build_selection_calibration_report_corrupted_data_unavailable_is_hard_error(tmp_path):
    """A ranking.json claiming DATA_UNAVAILABLE whose upstream artifacts
    actually support a COMPLETE ranking must be rejected by full Ranking
    Trust Chain re-verification, not silently counted."""
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "CC01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    ranking["ranking_status"] = "DATA_UNAVAILABLE"
    ranking["ranking_complete"] = False
    for candidate in ranking["candidates"]:
        candidate["final_rank"] = None
        candidate["rank_points"] = None
        candidate["feature_ranks"] = None
    ranking["summary"]["ranked_count"] = 0
    ranking["summary"]["top_ranked_ticker"] = None
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    output_before = _snapshot_output(tmp_path)
    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_RANKING_CONTRACT_VIOLATION"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )
    assert _snapshot_output(tmp_path) == output_before


def test_build_selection_calibration_report_semantic_tamper_is_hard_error(tmp_path):
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

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_RANKING_CONTRACT_VIOLATION"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
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


def test_build_selection_calibration_report_target_date_mismatch_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "HH01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    # tests/test_ranking.TARGET_DATE-named directory; corrupt target_date field
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    ranking["target_date"] = "2099-01-01"
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(
        SelectionCalibrationHardError, match="CALIBRATION_RANKING_TARGET_DATE_MISMATCH"
    ):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


def test_build_selection_calibration_report_missing_ranking_is_unverified_exclusion(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "2026-08-05").mkdir(parents=True)
    (runs_dir / "scratch").mkdir(parents=True)

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )

    validate_json_document(report, "selection_calibration.schema.json")
    dataset = report["dataset"]
    assert dataset["excluded_count"] == 1
    assert dataset["excluded_observations"][0]["disposition"] == "UNVERIFIED"
    assert dataset["excluded_observations"][0]["reason_code"] == "RANKING_ARTIFACT_MISSING"


# --- Trust-before-Cohort ordering: the security-critical tests -------------


def test_trust_before_cohort_config_sha_header_tamper_is_hard_error_not_cohort_split(tmp_path):
    """A tampered ranking.config_sha256 header must never silently become a
    cohort-mismatch exclusion -- Trust re-verification must fail first,
    always as a Hard Error (spec section 47)."""
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "II01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    ranking["config_sha256"] = "9" * 64
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(SelectionCalibrationHardError, match="CALIBRATION_RANKING_CONTRACT_VIOLATION"):
        build_selection_calibration_report(
            runs_dir=runs_dir,
            config_path=DEFAULT_CONFIG_PATH,
            source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        )


def test_trust_before_cohort_source_matrix_header_poisoning_is_not_cohort_skip(tmp_path):
    """A bogus ranking.input_hashes.source_matrix_sha256 must never cause a
    pre-Trust cohort skip; it is caught by historical Source Matrix
    resolution failing UNVERIFIED (spec section 48)."""
    runs_dir = tmp_path / "runs"
    run_dir = _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "JJ01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    ranking["input_hashes"]["source_matrix_sha256"] = "8" * 64
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )
    dataset = report["dataset"]
    assert dataset["observation_count"] == 0
    assert dataset["excluded_count"] == 1
    excluded = dataset["excluded_observations"][0]
    assert excluded["disposition"] == "UNVERIFIED"
    assert excluded["reason_code"] == "SOURCE_MATRIX_BYTES_UNAVAILABLE"


def test_trust_before_cohort_genuine_different_cohort_is_out_of_cohort(tmp_path):
    """A genuinely different, fully Trust-verified cohort (here: a
    different but genuinely-used Source Matrix) is a normal OUT_OF_COHORT
    exclusion, not a Hard Error (spec section 49)."""
    other_source_matrix_path = tmp_path / "other_source_matrix.yaml"
    payload = yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (other cohort)"
    other_source_matrix_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    runs_dir = tmp_path / "runs"
    _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "KK01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
        source_matrix_path=other_source_matrix_path,
    )

    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    registry_dir.joinpath(
        sha256_raw_bytes(other_source_matrix_path.read_bytes()) + ".yaml"
    ).write_bytes(other_source_matrix_path.read_bytes())

    report = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        source_matrix_registry_dir=registry_dir,
    )
    dataset = report["dataset"]
    assert dataset["observation_count"] == 0
    assert dataset["excluded_count"] == 1
    excluded = dataset["excluded_observations"][0]
    assert excluded["disposition"] == "OUT_OF_COHORT"
    assert excluded["reason_code"] == "COHORT_IDENTITY_MISMATCH"


def test_threshold_only_config_difference_preserves_cohort(tmp_path):
    """Two configs differing only in selection.enabled / thresholds must
    produce the same calibration_context_sha256 / cohort_id (spec section
    50) even though config_sha256 differs."""
    base_config = load_strategy_config(DEFAULT_CONFIG_PATH)
    enabled_config = json.loads(json.dumps(base_config))
    enabled_config["selection"]["enabled"] = True
    enabled_config["selection"]["rules"]["minimum_turnover_yen"]["threshold_yen"] = 10_000_000
    enabled_config["selection"]["rules"]["maximum_relative_tick_size"]["threshold_ratio"] = {
        "numerator": 1,
        "denominator": 1000,
    }

    assert compute_calibration_context_sha256(base_config) == compute_calibration_context_sha256(
        enabled_config
    )


# --- Historical Source Matrix resolution ------------------------------------


def test_resolve_historical_source_matrix_path_target_matches(tmp_path):
    expected = sha256_raw_bytes(DEFAULT_SOURCE_MATRIX_PATH.read_bytes())
    result = resolve_historical_source_matrix_path(
        expected_source_matrix_sha256=expected,
        target_source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        source_matrix_registry_dir=None,
    )
    assert result.status == "RESOLVED"
    assert result.path == DEFAULT_SOURCE_MATRIX_PATH


def test_resolve_historical_source_matrix_path_found_in_registry(tmp_path):
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    payload = yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (historical)"
    historical_bytes = yaml.safe_dump(payload, sort_keys=False).encode("utf-8")
    expected_sha = sha256_raw_bytes(historical_bytes)
    (registry_dir / f"{expected_sha}.yaml").write_bytes(historical_bytes)

    result = resolve_historical_source_matrix_path(
        expected_source_matrix_sha256=expected_sha,
        target_source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        source_matrix_registry_dir=registry_dir,
    )
    assert result.status == "RESOLVED"
    assert result.path == registry_dir / f"{expected_sha}.yaml"


def test_resolve_historical_source_matrix_path_missing_from_registry_is_unverified(tmp_path):
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()

    result = resolve_historical_source_matrix_path(
        expected_source_matrix_sha256="7" * 64,
        target_source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
        source_matrix_registry_dir=registry_dir,
    )
    assert result.status == "UNVERIFIED"
    assert result.reason_code == "SOURCE_MATRIX_BYTES_UNAVAILABLE"


def test_resolve_historical_source_matrix_path_registry_file_hash_mismatch_is_hard_error(tmp_path):
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    expected_sha = "6" * 64
    (registry_dir / f"{expected_sha}.yaml").write_bytes(b"not the expected bytes")

    with pytest.raises(
        SelectionCalibrationHardError, match="CALIBRATION_SOURCE_MATRIX_HASH_MISMATCH"
    ):
        resolve_historical_source_matrix_path(
            expected_source_matrix_sha256=expected_sha,
            target_source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
            source_matrix_registry_dir=registry_dir,
        )


# --- Determinism -------------------------------------------------------------


def test_build_selection_calibration_report_is_byte_deterministic(tmp_path):
    runs_dir = tmp_path / "runs"
    _build_calibration_run_dir(
        runs_dir,
        [{"ticker": "LL01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )

    report_a = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )
    report_b = build_selection_calibration_report(
        runs_dir=runs_dir,
        config_path=DEFAULT_CONFIG_PATH,
        source_matrix_path=DEFAULT_SOURCE_MATRIX_PATH,
    )
    bytes_a = json.dumps(report_a, sort_keys=True).encode("utf-8")
    bytes_b = json.dumps(report_b, sort_keys=True).encode("utf-8")
    assert bytes_a == bytes_b


# --- Threshold evaluation (Schema v2 nested dataset.observations) ---------


def _calibration_report_with_two_observations():
    return {
        "calibration_status": "COMPLETE",
        "dataset": {
            "observations": [
                {
                    "target_date": "2026-08-08",
                    "rank1": {
                        "ticker": "1111",
                        "turnover_yen": 10000000,
                        "relative_tick_size": {"numerator": 1, "denominator": 1000},
                    },
                },
                {
                    "target_date": "2026-08-09",
                    "rank1": {
                        "ticker": "2222",
                        "turnover_yen": 20000000,
                        "relative_tick_size": {"numerator": 1, "denominator": 500},
                    },
                },
            ]
        },
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
            calibration_report={"calibration_status": "DATA_UNAVAILABLE", "dataset": {"observations": []}},
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


def _snapshot_output(tmp_path):
    output_path = tmp_path / "calibration.json"
    return output_path.read_bytes() if output_path.is_file() else None
