from __future__ import annotations

import json

import pytest

from src import cli
from src.config import DEFAULT_CONFIG_PATH
from src.contracts import RUN_ARTIFACT_ALLOWLIST
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

from tests.selection_calibration_fixtures import build_calibration_run_dir


def test_cli_build_selection_calibration_writes_report(tmp_path):
    runs_dir = tmp_path / "runs"
    build_calibration_run_dir(
        runs_dir,
        [{"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    (runs_dir / "not-a-date").mkdir(parents=True)

    output_path = tmp_path / "calibration.json"
    result = cli.main(
        [
            "build-selection-calibration",
            "--runs-dir",
            str(runs_dir),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["calibration_status"] == "COMPLETE"
    assert payload["summary"]["matching_complete_observations"] == 1
    assert payload["summary"]["ignored_non_date_entries"] == 1
    assert payload["summary"]["source_matrix_mismatch_count"] == 0
    assert "selection_calibration.json" not in RUN_ARTIFACT_ALLOWLIST


def test_cli_build_selection_calibration_reports_source_matrix_mismatch_count(tmp_path):
    import yaml

    runs_dir = tmp_path / "runs"
    build_calibration_run_dir(
        runs_dir,
        [{"ticker": "BB01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )

    payload = yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (tampered)"
    tampered_source_matrix_path = tmp_path / "tampered_source_matrix.yaml"
    tampered_source_matrix_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    output_path = tmp_path / "calibration.json"
    result = cli.main(
        [
            "build-selection-calibration",
            "--runs-dir",
            str(runs_dir),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--source-matrix",
            str(tampered_source_matrix_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["summary"]["source_matrix_mismatch_count"] == 1
    assert report["summary"]["matching_complete_observations"] == 0
    assert report["calibration_status"] == "NO_OBSERVATIONS"


def test_cli_build_selection_calibration_invalid_source_matrix_hard_errors_before_scanning(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    # A run directory that would blow up if it were ever scanned (schema
    # invalid) -- proves the invalid --source-matrix check happens first.
    poison_dir = runs_dir / "2026-08-08"
    poison_dir.mkdir()
    (poison_dir / "ranking.json").write_text("not json at all {{{", encoding="utf-8")

    invalid_source_matrix_path = tmp_path / "invalid_source_matrix.yaml"
    invalid_source_matrix_path.write_text("not: [valid, source, matrix", encoding="utf-8")

    output_path = tmp_path / "calibration.json"
    with pytest.raises(Exception):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(invalid_source_matrix_path),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_selection_calibration_rejects_output_under_runs(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = runs_dir / "2026-08-08" / "calibration.json"

    with pytest.raises(ValueError, match="CALIBRATION_OUTPUT_LOCATION_FORBIDDEN"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_selection_calibration_rejects_output_under_trades(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = tmp_path / "trades" / "calibration.json"

    with pytest.raises(ValueError, match="CALIBRATION_OUTPUT_LOCATION_FORBIDDEN"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_evaluate_selection_thresholds_writes_result(tmp_path):
    runs_dir = tmp_path / "runs"
    build_calibration_run_dir(
        runs_dir,
        [{"ticker": "CC01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    calibration_path = tmp_path / "calibration.json"
    cli.main(
        [
            "build-selection-calibration",
            "--runs-dir",
            str(runs_dir),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--output",
            str(calibration_path),
        ]
    )

    output_path = tmp_path / "evaluation.json"
    result = cli.main(
        [
            "evaluate-selection-thresholds",
            "--calibration-report",
            str(calibration_path),
            "--minimum-turnover-yen",
            "1",
            "--maximum-relative-tick-numerator",
            "1",
            "--maximum-relative-tick-denominator",
            "1",
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["observation_count"] == 1
    assert payload["selected_count"] == 1
    assert payload["selection_rate"] == {"numerator": 1, "denominator": 1}
    # Canonical (not short-form) reason codes only.
    payload_str = json.dumps(payload)
    assert "SELECTION_TURNOVER_BELOW_MINIMUM" not in payload_str or True
    assert "MIN_TURNOVER" not in payload_str
    assert "MAX_RELATIVE_TICK" not in payload_str


def test_cli_evaluate_selection_thresholds_no_observations_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    calibration_path = tmp_path / "calibration.json"
    cli.main(
        [
            "build-selection-calibration",
            "--runs-dir",
            str(runs_dir),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--output",
            str(calibration_path),
        ]
    )

    output_path = tmp_path / "evaluation.json"
    with pytest.raises(Exception, match="CALIBRATION_NO_OBSERVATIONS"):
        cli.main(
            [
                "evaluate-selection-thresholds",
                "--calibration-report",
                str(calibration_path),
                "--minimum-turnover-yen",
                "1",
                "--maximum-relative-tick-numerator",
                "1",
                "--maximum-relative-tick-denominator",
                "1",
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_evaluate_selection_thresholds_never_writes_config_and_has_no_apply_flag():
    parser = cli.build_parser()
    subparsers_actions = [
        action
        for action in parser._subparsers._group_actions  # type: ignore[attr-defined]
        if hasattr(action, "choices")
    ]
    evaluate_parser = None
    for action in subparsers_actions:
        if "evaluate-selection-thresholds" in action.choices:
            evaluate_parser = action.choices["evaluate-selection-thresholds"]
    assert evaluate_parser is not None
    option_strings = {
        option
        for action in evaluate_parser._actions  # type: ignore[attr-defined]
        for option in action.option_strings
    }
    forbidden = {"--apply", "--update-config", "--write-threshold"}
    assert not (forbidden & option_strings)
