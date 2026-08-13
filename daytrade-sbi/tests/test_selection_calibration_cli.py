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
    assert payload["schema_version"] == 2
    assert payload["calibration_status"] == "COMPLETE"
    assert payload["dataset"]["observation_count"] == 1
    assert "generated_at" not in payload
    assert "selection_calibration.json" not in RUN_ARTIFACT_ALLOWLIST


def test_cli_build_selection_calibration_source_matrix_header_poisoning_is_unverified(tmp_path):
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
    assert report["dataset"]["excluded_count"] == 1
    assert report["dataset"]["excluded_observations"][0]["disposition"] == "UNVERIFIED"
    assert report["dataset"]["observation_count"] == 0
    assert report["calibration_status"] == "DATA_UNAVAILABLE"


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


def test_cli_build_selection_calibration_has_no_forbidden_flags():
    """Spec section 58: build-selection-calibration must never grow flags
    that write config, activate selection, or auto-select a threshold."""
    parser = cli.build_parser()
    subparsers_actions = [
        action
        for action in parser._subparsers._group_actions  # type: ignore[attr-defined]
        if hasattr(action, "choices")
    ]
    build_parser_obj = None
    for action in subparsers_actions:
        if "build-selection-calibration" in action.choices:
            build_parser_obj = action.choices["build-selection-calibration"]
    assert build_parser_obj is not None
    option_strings = {
        option
        for action in build_parser_obj._actions  # type: ignore[attr-defined]
        for option in action.option_strings
    }
    forbidden = {
        "--apply",
        "--activate",
        "--write-config",
        "--write-threshold",
        "--enable-selection",
        "--recommend",
        "--best",
        "--optimize",
        "--auto-select",
    }
    assert not (forbidden & option_strings)
    assert "--source-matrix-registry" in option_strings


def test_cli_build_selection_calibration_invalid_start_date_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = tmp_path / "calibration.json"
    with pytest.raises(Exception, match="CALIBRATION_DATE_RANGE_INVALID"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--start-date",
                "not-a-date",
                "--end-date",
                "2026-08-01",
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_selection_calibration_start_after_end_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = tmp_path / "calibration.json"
    with pytest.raises(Exception, match="CALIBRATION_DATE_RANGE_INVALID"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--start-date",
                "2026-08-10",
                "--end-date",
                "2026-08-01",
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_selection_calibration_nonexistent_registry_is_hard_error(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = tmp_path / "calibration.json"
    registry_dir = tmp_path / "does-not-exist"
    with pytest.raises(Exception, match="CALIBRATION_SOURCE_MATRIX_REGISTRY_INVALID"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--source-matrix-registry",
                str(registry_dir),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def _build_two_day_calibration_runs_dir(tmp_path, monkeypatch):
    """Build a runs_dir containing two genuinely distinct, fully
    hash-consistent run directories (different target_dates), so
    Candidate/Pair generation produces more than one candidate/pair --
    build_calibration_run_dir only ever writes a single fixed TARGET_DATE
    directory, so the underlying TARGET_DATE global (in both
    tests.test_ranking and tests.selection_calibration_fixtures, which
    imports it by name) is monkeypatched per call."""
    import tests.selection_calibration_fixtures as fixtures_module
    import tests.test_ranking as test_ranking_module

    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(test_ranking_module, "TARGET_DATE", "2026-08-08")
    monkeypatch.setattr(fixtures_module, "TARGET_DATE", "2026-08-08")
    build_calibration_run_dir(
        runs_dir,
        [{"ticker": "SS01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    monkeypatch.setattr(test_ranking_module, "TARGET_DATE", "2026-08-09")
    monkeypatch.setattr(fixtures_module, "TARGET_DATE", "2026-08-09")
    build_calibration_run_dir(
        runs_dir,
        [{"ticker": "SS02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}],
    )
    return runs_dir


def test_cli_build_selection_calibration_is_byte_deterministic(tmp_path, monkeypatch):
    """Real CLI-level output-byte Determinism test: drives cli.main(...)
    twice, writing to the SAME output path both times, and asserts full
    byte equality of output_path.read_bytes() -- proves atomic-replace
    determinism, not just pure-function output equality."""
    runs_dir = _build_two_day_calibration_runs_dir(tmp_path, monkeypatch)

    output_path = tmp_path / "selection_calibration.json"
    args = [
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

    result_first = cli.main(args)
    bytes_first = output_path.read_bytes()
    result_second = cli.main(args)
    bytes_second = output_path.read_bytes()

    assert result_first == 0
    assert result_second == 0
    assert bytes_first == bytes_second
    assert b"generated_at" not in bytes_first

    payload = json.loads(bytes_first)
    assert payload["dataset"]["observation_count"] == 2
    assert payload["candidate_space"]["evaluated_pair_count"] >= 1


def test_cli_hard_error_preserves_existing_output_bytes_ranking_trust(tmp_path):
    """TC-ATOM-001: a Ranking Trust Hard Error (tampered upstream artifact)
    must leave a pre-existing output file byte-identical."""
    runs_dir = tmp_path / "runs"
    run_dir = build_calibration_run_dir(
        runs_dir,
        [{"ticker": "TT01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    (run_dir / "sources.json").write_bytes(
        (run_dir / "sources.json").read_bytes() + b"\n# tampered\n"
    )

    output_path = tmp_path / "calibration.json"
    sentinel = b'{"existing":"must survive"}\n'
    output_path.write_bytes(sentinel)

    with pytest.raises(Exception, match="CALIBRATION_RANKING_CONTRACT_VIOLATION"):
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
    assert output_path.read_bytes() == sentinel


def test_cli_hard_error_preserves_existing_output_bytes_registry_hash_mismatch(tmp_path):
    """TC-ATOM-002: a Source Matrix Registry hash mismatch (a registry file
    named <sha>.yaml whose actual bytes don't match that SHA) must leave a
    pre-existing output file byte-identical."""
    runs_dir = tmp_path / "runs"
    other_source_matrix_path = tmp_path / "other_source_matrix.yaml"
    import yaml as _yaml

    payload = _yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (atom-002)"
    other_source_matrix_path.write_text(_yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    build_calibration_run_dir(
        runs_dir,
        [{"ticker": "UU01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
        source_matrix_path=other_source_matrix_path,
    )

    from src.selection_calibration import sha256_raw_bytes

    expected_sha = sha256_raw_bytes(other_source_matrix_path.read_bytes())
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    (registry_dir / f"{expected_sha}.yaml").write_bytes(b"not the expected bytes")

    output_path = tmp_path / "calibration.json"
    sentinel = b'{"existing":"must survive"}\n'
    output_path.write_bytes(sentinel)

    with pytest.raises(Exception, match="CALIBRATION_SOURCE_MATRIX_HASH_MISMATCH"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--source-matrix-registry",
                str(registry_dir),
                "--output",
                str(output_path),
            ]
        )
    assert output_path.read_bytes() == sentinel


def test_cli_hard_error_preserves_existing_output_bytes_invalid_date_range(tmp_path):
    """TC-ATOM-003: an Invalid Date Range Hard Error must leave a
    pre-existing output file byte-identical."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    output_path = tmp_path / "calibration.json"
    sentinel = b'{"existing":"must survive"}\n'
    output_path.write_bytes(sentinel)

    with pytest.raises(Exception, match="CALIBRATION_DATE_RANGE_INVALID"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--start-date",
                "not-a-date",
                "--end-date",
                "2026-08-01",
                "--output",
                str(output_path),
            ]
        )
    assert output_path.read_bytes() == sentinel


def test_cli_hard_error_preserves_existing_output_bytes_schema_validation(tmp_path, monkeypatch):
    """TC-ATOM-004: an Output Schema Validation failure must leave a
    pre-existing output file byte-identical. Triggered by monkeypatching
    ``build_selection_calibration_report`` (imported locally inside
    ``cli._build_selection_calibration``) to return a schema-invalid
    payload, exercising the real ``_write_json``: validate_json_document ->
    atomic_write_text ordering -- the same shared write path used for every
    other command -- without touching Selection/Ranking/Risk business
    logic."""
    import src.selection_calibration as selection_calibration_module

    def _schema_invalid_report(**kwargs):
        return {"schema_version": 2}  # missing required fields

    monkeypatch.setattr(
        selection_calibration_module,
        "build_selection_calibration_report",
        _schema_invalid_report,
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = tmp_path / "calibration.json"
    sentinel = b'{"existing":"must survive"}\n'
    output_path.write_bytes(sentinel)

    with pytest.raises(Exception):
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
    assert output_path.read_bytes() == sentinel


def test_cli_build_selection_calibration_date_range_requires_both(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    output_path = tmp_path / "calibration.json"
    with pytest.raises(Exception, match="CALIBRATION_DATE_RANGE_INVALID"):
        cli.main(
            [
                "build-selection-calibration",
                "--runs-dir",
                str(runs_dir),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--start-date",
                "2026-08-01",
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()
