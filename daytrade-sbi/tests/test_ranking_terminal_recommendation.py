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
# CLI-level tests -- full trust-chain re-verification (mirrors Case C /
# Calibration's genuine-hash-chain-via-real-CLI approach).
# ---------------------------------------------------------------------------

from src.config import DEFAULT_CONFIG_PATH
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH
from tests.ranking_terminal_recommendation_fixtures import build_terminal_recommendation_run_dir

_SPECS = [{"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}]


def _terminal_args(run_dir, output_path, *, config_path=None):
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
        str(config_path or (run_dir / "strategy_snapshot.yaml")),
        "--output",
        str(output_path),
    ]


def test_cli_build_ranking_terminal_recommendation_case_a(tmp_path):
    run_dir = build_terminal_recommendation_run_dir(
        tmp_path / "runs", _SPECS, data_unavailable=True
    )
    ranking = json.loads((run_dir / "ranking.json").read_text(encoding="utf-8"))
    output_path = tmp_path / "recommendation.json"

    result = cli.main(_terminal_args(run_dir, output_path))

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["decision"] == "DATA_UNAVAILABLE"
    assert payload["selection_reasons"] == ranking["reason_codes"]


def test_cli_build_ranking_terminal_recommendation_case_b(tmp_path):
    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    output_path = tmp_path / "recommendation.json"

    result = cli.main(_terminal_args(run_dir, output_path))

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["decision"] == "NO_TRADE"
    assert payload["selection_reasons"] == ["SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"]


def test_cli_build_ranking_terminal_recommendation_wrong_tool_hard_error(tmp_path):
    """The genuine wrong-tool check: a COMPLETE ranking whose config
    legitimately matches (selection.enabled=true, hashes consistent) must
    raise RANKING_TERMINAL_SELECTION_REQUIRED, not some unrelated hash
    mismatch."""
    import yaml

    from src.config import strategy_config_sha256

    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)

    config = load_strategy_config(run_dir / "strategy_snapshot.yaml")
    config = copy.deepcopy(config)
    config["selection"]["enabled"] = True
    config["selection"]["rules"]["minimum_turnover_yen"]["threshold_yen"] = 1000
    config["selection"]["rules"]["maximum_relative_tick_size"]["threshold_ratio"] = {
        "numerator": 1,
        "denominator": 1,
    }
    # Overwrite the run's strategy_snapshot.yaml itself (rather than using a
    # side file) so ranking.config_sha256 -- which was computed against the
    # ORIGINAL snapshot -- would no longer match; instead, regenerate a
    # ranking against this new selection-enabled config via the real
    # build-ranking CLI so the wrong-tool check is exercised on a ranking
    # that genuinely, fully validates.
    enabled_config_path = run_dir / "strategy_snapshot.yaml"
    enabled_config_path.write_bytes(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
    )

    from src.config import strategy_config_sha256 as _strategy_config_sha256

    new_config_sha = _strategy_config_sha256(config)

    candidates_path = run_dir / "candidates.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates["config_sha256"] = new_config_sha
    candidates_path.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    candidate_pipeline_path = run_dir / "candidate_pipeline.json"
    candidate_pipeline = json.loads(candidate_pipeline_path.read_text(encoding="utf-8"))
    candidate_pipeline["config_sha256"] = new_config_sha
    candidate_pipeline_path.write_text(
        json.dumps(candidate_pipeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    event_gate = json.loads((run_dir / "event_gate.json").read_text(encoding="utf-8"))
    event_gate["config_sha256"] = new_config_sha
    event_gate["input_hashes"]["candidates_sha256"] = __import__("hashlib").sha256(
        candidates_path.read_bytes()
    ).hexdigest()
    event_gate["input_hashes"]["strategy_snapshot_sha256"] = __import__("hashlib").sha256(
        enabled_config_path.read_bytes()
    ).hexdigest()
    (run_dir / "event_gate.json").write_text(
        json.dumps(event_gate, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ranking_path = run_dir / "ranking.json"
    result = cli.main(
        [
            "build-ranking",
            "--event-gate",
            str(run_dir / "event_gate.json"),
            "--candidates",
            str(run_dir / "candidates.json"),
            "--market-data",
            str(run_dir / "market_data.json"),
            "--sources",
            str(run_dir / "sources.json"),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--config",
            str(enabled_config_path),
            "--output",
            str(ranking_path),
        ]
    )
    assert result == 0

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError, match="RANKING_TERMINAL_SELECTION_REQUIRED"):
        cli.main(_terminal_args(run_dir, output_path))
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
def test_cli_build_ranking_terminal_recommendation_raw_byte_mutation_is_hash_mismatch(
    tmp_path, filename
):
    """Mutating any of ranking's claimed upstream inputs after the fact --
    without regenerating ranking.json -- must trip the input_hashes check,
    proving the CLI verifies raw bytes rather than trusting the file path."""
    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    output_path = tmp_path / "recommendation.json"

    target = run_dir / filename
    original = target.read_bytes()
    # Whitespace-only appended byte: does not change parsed JSON/YAML
    # semantics but does change the raw SHA256, proving the hash check is
    # byte-provenance-based, not semantic-equivalence-based.
    target.write_bytes(original + b"\n")

    with pytest.raises(ValueError, match="RANKING_TERMINAL_INPUT_HASHES_MISMATCH"):
        cli.main(_terminal_args(run_dir, output_path))
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_source_matrix_mutation_is_hash_mismatch(tmp_path):
    import yaml

    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    output_path = tmp_path / "recommendation.json"

    payload = yaml.safe_load(DEFAULT_SOURCE_MATRIX_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["source_name"] = payload["sources"][0]["source_name"] + " (tampered)"
    tampered_path = tmp_path / "tampered_source_matrix.yaml"
    tampered_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    args = _terminal_args(run_dir, output_path)
    args[args.index("--source-matrix") + 1] = str(tampered_path)

    with pytest.raises(ValueError, match="RANKING_TERMINAL_INPUT_HASHES_MISMATCH"):
        cli.main(args)
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_tampered_ranking_json_is_hard_error(tmp_path):
    """A schema-valid but hand-edited ranking.json (reason_codes flipped)
    while every upstream artifact stays genuine must be rejected by
    validate_ranking_preconditions / validate_ranking_output_contract."""
    run_dir = build_terminal_recommendation_run_dir(
        tmp_path / "runs", _SPECS, data_unavailable=True
    )
    ranking_path = run_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    ranking["reason_codes"] = ["TOTALLY_MADE_UP_REASON_CODE"]
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2), encoding="utf-8")

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError):
        cli.main(_terminal_args(run_dir, output_path))
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_previous_trading_day_mismatch_is_hard_error(
    tmp_path,
):
    """target_date matches everywhere, but research_window.previous_trading_day
    disagrees with ranking.previous_trading_day -- must be a Hard Error even
    though target_date alone would look consistent."""
    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    research_window_path = run_dir / "research_window.json"
    research_window = json.loads(research_window_path.read_text(encoding="utf-8"))
    research_window["previous_trading_day"] = "1999-01-01"
    research_window_path.write_text(
        json.dumps(research_window, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError, match="RANKING_TERMINAL_PREVIOUS_TRADING_DAY_MISMATCH"):
        cli.main(_terminal_args(run_dir, output_path))
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_pipeline_not_complete_is_hard_error(tmp_path):
    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    candidate_pipeline_path = run_dir / "candidate_pipeline.json"
    candidate_pipeline = json.loads(candidate_pipeline_path.read_text(encoding="utf-8"))
    candidate_pipeline["summary"]["pipeline_complete"] = False
    candidate_pipeline_path.write_text(
        json.dumps(candidate_pipeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError, match="RANKING_TERMINAL_PIPELINE_NOT_COMPLETE"):
        cli.main(_terminal_args(run_dir, output_path))
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_screening_not_complete_is_hard_error(tmp_path):
    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    candidate_pipeline_path = run_dir / "candidate_pipeline.json"
    candidate_pipeline = json.loads(candidate_pipeline_path.read_text(encoding="utf-8"))
    candidate_pipeline["summary"]["screening_complete"] = False
    candidate_pipeline_path.write_text(
        json.dumps(candidate_pipeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError, match="RANKING_TERMINAL_SCREENING_NOT_COMPLETE"):
        cli.main(_terminal_args(run_dir, output_path))
    assert not output_path.exists()


def test_cli_build_ranking_terminal_recommendation_no_partial_output_on_hard_error(tmp_path):
    """A Hard Error anywhere in the trust chain must leave a pre-existing
    output file completely byte-for-byte untouched."""
    run_dir = build_terminal_recommendation_run_dir(tmp_path / "runs", _SPECS)
    candidate_pipeline_path = run_dir / "candidate_pipeline.json"
    candidate_pipeline = json.loads(candidate_pipeline_path.read_text(encoding="utf-8"))
    candidate_pipeline["summary"]["pipeline_complete"] = False
    candidate_pipeline_path.write_text(
        json.dumps(candidate_pipeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    output_path = tmp_path / "recommendation.json"
    output_path.write_text("sentinel", encoding="utf-8")

    with pytest.raises(ValueError, match="RANKING_TERMINAL_PIPELINE_NOT_COMPLETE"):
        cli.main(_terminal_args(run_dir, output_path))
    assert output_path.read_bytes() == b"sentinel"
