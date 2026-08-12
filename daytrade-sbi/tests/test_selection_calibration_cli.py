from __future__ import annotations

import json

import pytest

from src import cli
from src.config import DEFAULT_CONFIG_PATH
from src.contracts import RUN_ARTIFACT_ALLOWLIST

from tests.factories import make_complete_ranking_payload


def _write_ranking(run_dir, target_date, **overrides):
    run_dir.mkdir(parents=True, exist_ok=True)
    ranking = make_complete_ranking_payload(target_date=target_date, **overrides)
    (run_dir / "ranking.json").write_text(json.dumps(ranking), encoding="utf-8")
    return ranking


def test_cli_build_selection_calibration_writes_report(tmp_path):
    from src.config import load_strategy_config, strategy_config_sha256
    from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

    config = load_strategy_config(DEFAULT_CONFIG_PATH)
    strategy_version = config["strategy_version"]
    config_sha256 = strategy_config_sha256(config)

    runs_dir = tmp_path / "runs"
    _write_ranking(
        runs_dir / "2026-08-08",
        "2026-08-08",
        strategy_version=strategy_version,
        config_sha256=config_sha256,
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
    assert "selection_calibration.json" not in RUN_ARTIFACT_ALLOWLIST


def test_cli_build_selection_calibration_rejects_output_under_runs(tmp_path):
    from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

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
    from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

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
    from src.config import load_strategy_config, strategy_config_sha256
    from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

    config = load_strategy_config(DEFAULT_CONFIG_PATH)
    strategy_version = config["strategy_version"]
    config_sha256 = strategy_config_sha256(config)

    runs_dir = tmp_path / "runs"
    _write_ranking(
        runs_dir / "2026-08-08",
        "2026-08-08",
        strategy_version=strategy_version,
        config_sha256=config_sha256,
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
