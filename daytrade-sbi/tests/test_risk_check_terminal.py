"""Tests for risk-check's terminal_driven_v6 trust-chain re-verification
(Config v6, recommendation.schema_version == 1, decision in
{NO_TRADE, DATA_UNAVAILABLE} -- i.e. Case A/B, produced by
build-ranking-terminal-recommendation, not the Selection-driven Case C path).
"""

from __future__ import annotations

import json

import pytest

from src import cli
from src.config import load_strategy_config
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH
from tests.ranking_terminal_recommendation_fixtures import (
    build_terminal_recommendation_run_dir,
)

_SPECS = [{"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}]


def _terminal_build_args(run_dir, output_path):
    return [
        "build-ranking-terminal-recommendation",
        "--ranking",
        str(run_dir / "ranking.json"),
        "--event-gate",
        str(run_dir / "event_gate.json"),
        "--candidates",
        str(run_dir / "candidates.json"),
        "--candidate-pipeline",
        str(run_dir / "candidate_pipeline.json"),
        "--market-data",
        str(run_dir / "market_data.json"),
        "--research-window",
        str(run_dir / "research_window.json"),
        "--sources",
        str(run_dir / "sources.json"),
        "--source-matrix",
        str(DEFAULT_SOURCE_MATRIX_PATH),
        "--config",
        str(run_dir / "strategy_snapshot.yaml"),
        "--output",
        str(output_path),
    ]


def _risk_check_args(run_dir, recommendation_path, output_path):
    return [
        "risk-check",
        "--recommendation",
        str(recommendation_path),
        "--candidates",
        str(run_dir / "candidates.json"),
        "--candidate-pipeline",
        str(run_dir / "candidate_pipeline.json"),
        "--market-data",
        str(run_dir / "market_data.json"),
        "--sources",
        str(run_dir / "sources.json"),
        "--source-matrix",
        str(DEFAULT_SOURCE_MATRIX_PATH),
        "--config",
        str(run_dir / "strategy_snapshot.yaml"),
        "--ranking",
        str(run_dir / "ranking.json"),
        "--event-gate",
        str(run_dir / "event_gate.json"),
        "--research-window",
        str(run_dir / "research_window.json"),
        "--output",
        str(output_path),
    ]


def _build_case(tmp_path, *, data_unavailable):
    run_dir = build_terminal_recommendation_run_dir(
        tmp_path / "runs", _SPECS, data_unavailable=data_unavailable
    )
    recommendation_path = tmp_path / "recommendation.json"
    assert cli.main(_terminal_build_args(run_dir, recommendation_path)) == 0
    return run_dir, recommendation_path


def test_case_a_full_chain_risk_check_is_not_applicable(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=True)
    recommendation = json.loads(recommendation_path.read_text(encoding="utf-8"))
    assert recommendation["decision"] == "DATA_UNAVAILABLE"

    output_path = tmp_path / "risk_result.json"
    result = cli.main(_risk_check_args(run_dir, recommendation_path, output_path))

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "DATA_UNAVAILABLE"
    assert payload["status"] == "NOT_APPLICABLE"
    assert payload["ticker"] is None


def test_case_b_full_chain_risk_check_is_not_applicable(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)
    recommendation = json.loads(recommendation_path.read_text(encoding="utf-8"))
    assert recommendation["decision"] == "NO_TRADE"

    output_path = tmp_path / "risk_result.json"
    result = cli.main(_risk_check_args(run_dir, recommendation_path, output_path))

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "NO_TRADE"
    assert payload["status"] == "NOT_APPLICABLE"
    assert payload["ticker"] is None


def test_forged_terminal_recommendation_is_hard_error_not_not_applicable(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)
    recommendation = json.loads(recommendation_path.read_text(encoding="utf-8"))
    # Schema-valid tamper: change selection_reasons to a fabricated value
    # that build_ranking_terminal_recommendation would never emit for this
    # genuine Case B ranking.
    recommendation["selection_reasons"] = ["SOME_FAKE_REASON"]
    recommendation_path.write_text(
        json.dumps(recommendation, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    output_path = tmp_path / "risk_result.json"
    with pytest.raises(ValueError, match="RISK_TERMINAL_RECOMMENDATION_CONTRACT_MISMATCH"):
        cli.main(_risk_check_args(run_dir, recommendation_path, output_path))
    assert not output_path.exists()


def test_case_a_swap_forgery_is_hard_error_not_not_applicable(tmp_path):
    """Genuine Case A (DATA_UNAVAILABLE) ranking/upstream chain, but a
    hand-crafted schema-valid recommendation.json claiming a Case-B-shaped
    decision/selection_reasons. The recomputed Terminal Recommendation from
    the genuine Case A inputs will never match this forgery."""
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=True)
    recommendation = json.loads(recommendation_path.read_text(encoding="utf-8"))
    recommendation["decision"] = "NO_TRADE"
    recommendation["selection_reasons"] = ["SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"]
    recommendation_path.write_text(
        json.dumps(recommendation, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    output_path = tmp_path / "risk_result.json"
    with pytest.raises(ValueError):
        cli.main(_risk_check_args(run_dir, recommendation_path, output_path))
    assert not output_path.exists()


@pytest.mark.parametrize(
    "filename",
    [
        "event_gate.json",
        "candidates.json",
        "market_data.json",
        "sources.json",
        "strategy_snapshot.yaml",
    ],
)
def test_hash_mutation_of_upstream_file_is_hard_error(tmp_path, filename):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)

    target = run_dir / filename
    original = target.read_bytes()
    target.write_bytes(original + b"\n")

    output_path = tmp_path / "risk_result.json"
    with pytest.raises(ValueError, match="RISK_TERMINAL_INPUT_HASHES_MISMATCH"):
        cli.main(_risk_check_args(run_dir, recommendation_path, output_path))
    assert not output_path.exists()


def test_source_matrix_mutation_is_hard_error(tmp_path):
    import yaml

    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)

    payload = yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (tampered)"
    tampered_path = tmp_path / "tampered_source_matrix.yaml"
    tampered_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    output_path = tmp_path / "risk_result.json"
    args = _risk_check_args(run_dir, recommendation_path, output_path)
    args[args.index("--source-matrix") + 1] = str(tampered_path)

    with pytest.raises(ValueError, match="RISK_TERMINAL_INPUT_HASHES_MISMATCH"):
        cli.main(args)
    assert not output_path.exists()


def test_ranking_forgery_is_hard_error(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)

    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    # Schema-valid tamper that does not change the raw bytes hash check
    # relies on (ranking.json's own bytes are what get hashed for
    # RISK_TERMINAL_INPUT_HASHES_MISMATCH purposes, but ranking.json is not
    # itself one of the hashed upstream inputs -- so this exercises the
    # reused Ranking Contract functions instead).
    ranking["reason_codes"] = list(ranking.get("reason_codes") or []) + ["FORGED_REASON_CODE"]
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    output_path = tmp_path / "risk_result.json"
    with pytest.raises(ValueError):
        cli.main(_risk_check_args(run_dir, recommendation_path, output_path))
    assert not output_path.exists()


def test_atomic_write_leaves_preexisting_output_untouched_on_hard_error(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)

    target = run_dir / "candidates.json"
    target.write_bytes(target.read_bytes() + b"\n")

    output_path = tmp_path / "risk_result.json"
    sentinel = b"sentinel-bytes-do-not-touch"
    output_path.write_bytes(sentinel)

    with pytest.raises(ValueError, match="RISK_TERMINAL_INPUT_HASHES_MISMATCH"):
        cli.main(_risk_check_args(run_dir, recommendation_path, output_path))

    assert output_path.read_bytes() == sentinel


def test_missing_ranking_arg_is_hard_error(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)
    output_path = tmp_path / "risk_result.json"
    args = _risk_check_args(run_dir, recommendation_path, output_path)
    idx = args.index("--ranking")
    args = args[:idx] + args[idx + 2 :]

    with pytest.raises(ValueError, match="RISK_TERMINAL_RANKING_REQUIRED"):
        cli.main(args)
    assert not output_path.exists()


def test_missing_event_gate_arg_is_hard_error(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)
    output_path = tmp_path / "risk_result.json"
    args = _risk_check_args(run_dir, recommendation_path, output_path)
    idx = args.index("--event-gate")
    args = args[:idx] + args[idx + 2 :]

    with pytest.raises(ValueError, match="RISK_TERMINAL_EVENT_GATE_REQUIRED"):
        cli.main(args)
    assert not output_path.exists()


def test_missing_research_window_arg_is_hard_error(tmp_path):
    run_dir, recommendation_path = _build_case(tmp_path, data_unavailable=False)
    output_path = tmp_path / "risk_result.json"
    args = _risk_check_args(run_dir, recommendation_path, output_path)
    idx = args.index("--research-window")
    args = args[:idx] + args[idx + 2 :]

    with pytest.raises(ValueError, match="RISK_TERMINAL_RESEARCH_WINDOW_REQUIRED"):
        cli.main(args)
    assert not output_path.exists()


def test_pre_v6_terminal_recommendation_does_not_require_new_args(tmp_path, monkeypatch):
    """config_schema_version < 6: risk-check behavior for a NO_TRADE/
    DATA_UNAVAILABLE recommendation stays exactly as before this change --
    --ranking/--event-gate/--research-window are NOT required."""
    import copy

    import yaml

    from src.config import strategy_config_sha256

    config = copy.deepcopy(load_strategy_config())
    config["config_schema_version"] = 5
    config_path = tmp_path / "strategy.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    config_sha256 = strategy_config_sha256(config)
    strategy_version = config["strategy_version"]

    recommendation = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "decision": "DATA_UNAVAILABLE",
        "ticker": None,
        "company_name": None,
        "strategy_type": None,
        "previous_high": None,
        "tick_size": None,
        "entry_trigger": None,
        "entry_limit": None,
        "take_profit": None,
        "stop_loss": None,
        "shares": None,
        "selection_reasons": ["secondary OHLCV source missing"],
        "research_cutoff": "2026-08-09T21:00:00+00:00",
        "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
        "source_statuses": [],
        "source_urls": [],
        "notes": None,
    }
    candidates = {
        "target_date": "2026-08-10",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "candidates": [{"ticker": "1234", "status": "DATA_UNAVAILABLE"}],
    }
    from tests.test_cli import complete_pipeline_summary

    candidate_pipeline = {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "generated_at": "2026-08-09T21:30:00+00:00",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "summary": complete_pipeline_summary(
            research_complete=0,
            data_unavailable=1,
            screened=0,
            eligible=0,
            stage2_completed_count=0,
            stage2_unavailable_count=1,
        ),
        "candidates": [],
    }
    recommendation["pipeline_summary"] = candidate_pipeline["summary"]

    from src.market import SourceLedgerValidationResult
    from src.source_matrix import load_source_matrix

    def load_document(path, schema):
        if schema == "recommendation.schema.json":
            return recommendation
        if schema == "candidate_pipeline.schema.json":
            return candidate_pipeline
        return candidates

    monkeypatch.setattr(cli, "load_json_document", load_document)
    monkeypatch.setattr(
        cli,
        "_load_market_bundle",
        lambda market, sources, source_matrix: (
            "2026-08-10",
            [],
            SourceLedgerValidationResult(True, ()),
            {"target_date": "2026-08-10", "sources": []},
            load_source_matrix(),
        ),
    )

    output_path = tmp_path / "risk_result.json"
    result = cli.main(
        [
            "risk-check",
            "--recommendation",
            "recommendation.json",
            "--candidates",
            "candidates.json",
            "--candidate-pipeline",
            "candidate_pipeline.json",
            "--market-data",
            "market_data.json",
            "--sources",
            "sources.json",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ]
    )
    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "DATA_UNAVAILABLE"
    assert payload["status"] == "NOT_APPLICABLE"
