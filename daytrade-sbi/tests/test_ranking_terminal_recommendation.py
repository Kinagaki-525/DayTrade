from __future__ import annotations

import copy
import json

import pytest

from src import cli
from src.config import load_strategy_config, strategy_config_sha256
from src.ranking_terminal_recommendation import (
    RankingTerminalRecommendationHardError,
    build_ranking_terminal_recommendation,
    determine_ranking_terminal_case,
)
from tests.factories import make_complete_ranking_payload, make_data_unavailable_ranking_payload
from tests.test_cli import complete_pipeline_summary


def _write_json_file(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


CONFIG = load_strategy_config()
CONFIG_SHA = strategy_config_sha256(CONFIG)
STRATEGY_VERSION = CONFIG["strategy_version"]


def _pipeline_summary():
    return complete_pipeline_summary()


def _research_window(target_date="2026-08-12", previous_trading_day="2026-08-11"):
    return {
        "schema_version": 1,
        "target_date": target_date,
        "previous_trading_day": previous_trading_day,
        "research_cutoff": "2026-08-11T21:00:00+00:00",
        "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
        "research_window": {
            "run_type": "FIRST_RUN",
            "window_start": "2026-08-11T00:00:00+00:00",
            "window_end": "2026-08-11T21:00:00+00:00",
            "previous_research_cutoff": None,
            "previous_run_date": None,
            "bootstrap_lookback_days": 5,
        },
    }


def _candidate_pipeline(target_date="2026-08-12"):
    return {
        "schema_version": 1,
        "target_date": target_date,
        "generated_at": "2026-08-11T21:30:00+00:00",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "summary": _pipeline_summary(),
        "candidates": [],
    }


def _candidates(target_date="2026-08-12"):
    return {
        "schema_version": 1,
        "target_date": target_date,
        "generated_at": "2026-08-11T21:00:00+00:00",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": CONFIG_SHA,
        "candidates": [],
    }


def _sources(target_date="2026-08-12"):
    return {
        "schema_version": 1,
        "target_date": target_date,
        "sources": [],
        "source_attempts": [],
    }


# ---------------------------------------------------------------------------
# Pure function: determine_ranking_terminal_case / build_ranking_terminal_recommendation.
# ---------------------------------------------------------------------------


def test_determine_case_a_for_data_unavailable_ranking():
    ranking = make_data_unavailable_ranking_payload(
        strategy_version=STRATEGY_VERSION, config_sha256=CONFIG_SHA
    )
    assert determine_ranking_terminal_case(ranking, CONFIG) == "A"


def test_determine_case_b_for_complete_ranking_selection_disabled():
    ranking = make_complete_ranking_payload(
        strategy_version=STRATEGY_VERSION, config_sha256=CONFIG_SHA
    )
    assert determine_ranking_terminal_case(ranking, CONFIG) == "B"


def test_determine_case_complete_selection_enabled_is_wrong_tool_error():
    ranking = make_complete_ranking_payload(
        strategy_version=STRATEGY_VERSION, config_sha256=CONFIG_SHA
    )
    config = copy.deepcopy(CONFIG)
    config["selection"] = {"enabled": True}
    with pytest.raises(RankingTerminalRecommendationHardError, match="RANKING_TERMINAL_SELECTION_REQUIRED"):
        determine_ranking_terminal_case(ranking, config)


def test_determine_case_unsupported_combination_is_hard_error():
    ranking = make_data_unavailable_ranking_payload(
        strategy_version=STRATEGY_VERSION, config_sha256=CONFIG_SHA
    )
    ranking["ranking_complete"] = True  # contradicts DATA_UNAVAILABLE
    with pytest.raises(RankingTerminalRecommendationHardError, match="RANKING_TERMINAL_STATE_UNSUPPORTED"):
        determine_ranking_terminal_case(ranking, CONFIG)


def test_build_case_a_happy_path():
    ranking = make_data_unavailable_ranking_payload(
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
        reason_codes=["TURNOVER_SOURCE_NOT_FOUND"],
    )
    payload = build_ranking_terminal_recommendation(
        ranking=ranking,
        candidate_pipeline={"summary": _pipeline_summary()},
        research_window=_research_window(),
        config=CONFIG,
    )
    assert payload["schema_version"] == 1
    assert payload["decision"] == "DATA_UNAVAILABLE"
    assert payload["selection_reasons"] == ["TURNOVER_SOURCE_NOT_FOUND"]
    for field in (
        "strategy_type",
        "ticker",
        "company_name",
        "previous_high",
        "tick_size",
        "entry_trigger",
        "entry_limit",
        "take_profit",
        "stop_loss",
        "shares",
    ):
        assert payload[field] is None
    assert payload["source_urls"] == []
    assert payload["source_statuses"] == []
    assert payload["notes"] is None
    assert payload["pipeline_summary"] == _pipeline_summary()


def test_build_case_b_happy_path():
    ranking = make_complete_ranking_payload(
        strategy_version=STRATEGY_VERSION, config_sha256=CONFIG_SHA
    )
    payload = build_ranking_terminal_recommendation(
        ranking=ranking,
        candidate_pipeline={"summary": _pipeline_summary()},
        research_window=_research_window(),
        config=CONFIG,
    )
    assert payload["schema_version"] == 1
    assert payload["decision"] == "NO_TRADE"
    assert payload["selection_reasons"] == ["SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"]
    for field in (
        "strategy_type",
        "ticker",
        "company_name",
        "previous_high",
        "tick_size",
        "entry_trigger",
        "entry_limit",
        "take_profit",
        "stop_loss",
        "shares",
    ):
        assert payload[field] is None


def test_build_case_a_empty_reason_codes_is_hard_error():
    ranking = make_data_unavailable_ranking_payload(
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
        reason_codes=[],
    )
    # Schema requires ranking.reason_codes to mirror candidate reason_codes
    # for this fixture; force the artifact-contradiction scenario directly.
    ranking["reason_codes"] = []
    with pytest.raises(RankingTerminalRecommendationHardError, match="RANKING_TERMINAL_REASON_CODES_EMPTY"):
        build_ranking_terminal_recommendation(
            ranking=ranking,
            candidate_pipeline={"summary": _pipeline_summary()},
            research_window=_research_window(),
            config=CONFIG,
        )


def test_build_case_b_selection_enabled_is_wrong_tool_error():
    ranking = make_complete_ranking_payload(
        strategy_version=STRATEGY_VERSION, config_sha256=CONFIG_SHA
    )
    config = copy.deepcopy(CONFIG)
    config["selection"] = {"enabled": True}
    with pytest.raises(RankingTerminalRecommendationHardError, match="RANKING_TERMINAL_SELECTION_REQUIRED"):
        build_ranking_terminal_recommendation(
            ranking=ranking,
            candidate_pipeline={"summary": _pipeline_summary()},
            research_window=_research_window(),
            config=config,
        )


# ---------------------------------------------------------------------------
# CLI-level tests.
# ---------------------------------------------------------------------------


def test_cli_build_ranking_terminal_recommendation_case_a(tmp_path):
    ranking = make_data_unavailable_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    _write_json_file(ranking_path, ranking)
    _write_json_file(candidates_path, _candidates())
    _write_json_file(candidate_pipeline_path, _candidate_pipeline())
    _write_json_file(research_window_path, _research_window())
    _write_json_file(sources_path, _sources())

    result = cli.main(
        [
            "build-ranking-terminal-recommendation",
            "--ranking",
            str(ranking_path),
            "--candidates",
            str(candidates_path),
            "--candidate-pipeline",
            str(candidate_pipeline_path),
            "--research-window",
            str(research_window_path),
            "--sources",
            str(sources_path),
            "--config",
            str(cli.DEFAULT_CONFIG_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["decision"] == "DATA_UNAVAILABLE"
    assert payload["selection_reasons"] == ranking["reason_codes"]


def test_cli_build_ranking_terminal_recommendation_case_b(tmp_path):
    ranking = make_complete_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    _write_json_file(ranking_path, ranking)
    _write_json_file(candidates_path, _candidates())
    _write_json_file(candidate_pipeline_path, _candidate_pipeline())
    _write_json_file(research_window_path, _research_window())
    _write_json_file(sources_path, _sources())

    result = cli.main(
        [
            "build-ranking-terminal-recommendation",
            "--ranking",
            str(ranking_path),
            "--candidates",
            str(candidates_path),
            "--candidate-pipeline",
            str(candidate_pipeline_path),
            "--research-window",
            str(research_window_path),
            "--sources",
            str(sources_path),
            "--config",
            str(cli.DEFAULT_CONFIG_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["decision"] == "NO_TRADE"
    assert payload["selection_reasons"] == ["SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"]


def test_cli_build_ranking_terminal_recommendation_case_b_selection_enabled_is_hard_error(tmp_path):
    ranking = make_complete_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    _write_json_file(ranking_path, ranking)
    _write_json_file(candidates_path, _candidates())
    _write_json_file(candidate_pipeline_path, _candidate_pipeline())
    _write_json_file(research_window_path, _research_window())
    _write_json_file(sources_path, _sources())

    import yaml

    config = copy.deepcopy(CONFIG)
    config["selection"]["enabled"] = True
    config["selection"]["rules"]["minimum_turnover_yen"]["threshold_yen"] = 1000
    config["selection"]["rules"]["maximum_relative_tick_size"]["threshold_ratio"] = {
        "numerator": 1,
        "denominator": 1,
    }
    enabled_config_path = tmp_path / "strategy.yaml"
    enabled_config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ranking.config_sha256 must match --config for the earlier
    # RANKING_TERMINAL_CONFIG_SHA256_MISMATCH check to not preempt the
    # wrong-tool check under test.
    ranking["config_sha256"] = strategy_config_sha256(config)
    _write_json_file(ranking_path, ranking)

    with pytest.raises(ValueError, match="RANKING_TERMINAL_SELECTION_REQUIRED"):
        cli.main(
            [
                "build-ranking-terminal-recommendation",
                "--ranking",
                str(ranking_path),
                "--candidates",
                str(candidates_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(sources_path),
                "--config",
                str(enabled_config_path),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_target_date_mismatch_is_hard_error(tmp_path):
    ranking = make_data_unavailable_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    _write_json_file(ranking_path, ranking)
    _write_json_file(candidates_path, _candidates())
    _write_json_file(candidate_pipeline_path, _candidate_pipeline())
    # research_window carries a different target_date than ranking.json.
    _write_json_file(research_window_path, _research_window(target_date="2026-08-13"))
    _write_json_file(sources_path, _sources())

    with pytest.raises(ValueError, match="RANKING_TERMINAL_TARGET_DATE_MISMATCH"):
        cli.main(
            [
                "build-ranking-terminal-recommendation",
                "--ranking",
                str(ranking_path),
                "--candidates",
                str(candidates_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(sources_path),
                "--config",
                str(cli.DEFAULT_CONFIG_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_candidate_pipeline_target_date_mismatch_is_hard_error(
    tmp_path,
):
    ranking = make_data_unavailable_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
    )
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    _write_json_file(ranking_path, ranking)
    _write_json_file(candidates_path, _candidates())
    # candidate_pipeline carries a different target_date than ranking.json.
    _write_json_file(candidate_pipeline_path, _candidate_pipeline(target_date="2026-08-13"))
    _write_json_file(research_window_path, _research_window())
    _write_json_file(sources_path, _sources())

    with pytest.raises(ValueError, match="target_date"):
        cli.main(
            [
                "build-ranking-terminal-recommendation",
                "--ranking",
                str(ranking_path),
                "--candidates",
                str(candidates_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(sources_path),
                "--config",
                str(cli.DEFAULT_CONFIG_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_no_partial_output_on_hard_error(tmp_path):
    ranking = make_data_unavailable_ranking_payload(
        target_date="2026-08-12",
        previous_trading_day="2026-08-11",
        strategy_version=STRATEGY_VERSION,
        config_sha256=CONFIG_SHA,
        reason_codes=[],
    )
    ranking["reason_codes"] = []
    ranking_path = tmp_path / "ranking.json"
    candidates_path = tmp_path / "candidates.json"
    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    research_window_path = tmp_path / "research_window.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "recommendation.json"
    output_path.write_text("sentinel", encoding="utf-8")
    _write_json_file(ranking_path, ranking)
    _write_json_file(candidates_path, _candidates())
    _write_json_file(candidate_pipeline_path, _candidate_pipeline())
    _write_json_file(research_window_path, _research_window())
    _write_json_file(sources_path, _sources())

    with pytest.raises(ValueError, match="RANKING_TERMINAL_REASON_CODES_EMPTY"):
        cli.main(
            [
                "build-ranking-terminal-recommendation",
                "--ranking",
                str(ranking_path),
                "--candidates",
                str(candidates_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(sources_path),
                "--config",
                str(cli.DEFAULT_CONFIG_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert output_path.read_text(encoding="utf-8") == "sentinel"
