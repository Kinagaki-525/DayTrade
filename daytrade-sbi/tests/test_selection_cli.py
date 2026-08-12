from __future__ import annotations

import copy
import hashlib
import json

import yaml

from src import cli
from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, strategy_config_sha256
from src.contracts import RUN_ARTIFACT_ALLOWLIST

from tests.factories import make_complete_ranking_payload


def _write_json_file(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _enabled_selection_config_path(tmp_path):
    config = copy.deepcopy(load_strategy_config())
    config["selection"] = {
        "enabled": True,
        "version": "selection-v1",
        "candidate_policy": "rank1_only",
        "fallback_policy": "none",
        "rule_logic": "all",
        "missing_data_policy": "fail_closed",
        "rules": {
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": 1000},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1},
            },
        },
    }
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, config


def test_cli_build_selection_selected(tmp_path):
    config_path, config = _enabled_selection_config_path(tmp_path)
    ranking = make_complete_ranking_payload(
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        strategy_snapshot_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
    )
    ranking_path = tmp_path / "ranking.json"
    _write_json_file(ranking_path, ranking)
    output_path = tmp_path / "selection.json"

    result = cli.main(
        [
            "build-selection",
            "--ranking",
            str(ranking_path),
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["selection_status"] == "SELECTED"
    assert payload["selected_ticker"] == "1234"
    assert "selection.json" in RUN_ARTIFACT_ALLOWLIST


def test_cli_build_selection_disabled_config_produces_no_output(tmp_path):
    ranking = make_complete_ranking_payload(
        strategy_version=load_strategy_config()["strategy_version"],
        config_sha256=strategy_config_sha256(load_strategy_config()),
        strategy_snapshot_sha256=hashlib.sha256(DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
    )
    ranking_path = tmp_path / "ranking.json"
    _write_json_file(ranking_path, ranking)
    output_path = tmp_path / "selection.json"

    raised = False
    try:
        cli.main(
            [
                "build-selection",
                "--ranking",
                str(ranking_path),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--output",
                str(output_path),
            ]
        )
    except ValueError as exc:
        raised = True
        assert "SELECTION_CONFIG_DISABLED" in str(exc)
    assert raised
    assert not output_path.exists()


def test_cli_build_selection_no_partial_output_on_hard_error(tmp_path):
    config_path, config = _enabled_selection_config_path(tmp_path)
    ranking = make_complete_ranking_payload(
        strategy_version=config["strategy_version"],
        config_sha256="0" * 64,  # wrong: will trigger hash chain / config mismatch
        strategy_snapshot_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
    )
    ranking_path = tmp_path / "ranking.json"
    _write_json_file(ranking_path, ranking)
    output_path = tmp_path / "selection.json"

    raised = False
    try:
        cli.main(
            [
                "build-selection",
                "--ranking",
                str(ranking_path),
                "--config",
                str(config_path),
                "--output",
                str(output_path),
            ]
        )
    except ValueError:
        raised = True
    assert raised
    assert not output_path.exists()
