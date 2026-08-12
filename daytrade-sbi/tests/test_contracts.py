from pathlib import Path
import shutil

import pytest

from src.contracts import (
    validate_run_artifact_allowlist,
    validate_candidate_pipeline_inputs,
    validate_performance_inputs,
    validate_recommendation_candidate_link,
    validate_recommendation_pipeline_link,
    validate_recommendation_risk_link,
    validate_recommendation_sources,
)
from src.config import load_strategy_config, strategy_config_sha256


METADATA = {
    "target_date": "2026-08-10",
    "strategy_version": "v1",
    "config_sha256": "a" * 64,
}


def complete_summary(**overrides):
    summary = {
        "discovered": 1,
        "research_complete": 1,
        "research_incomplete": 0,
        "data_unavailable": 0,
        "screened": 1,
        "eligible": 1,
        "rejected": 0,
        "pipeline_complete": True,
        "stage2_target_count": 1,
        "stage2_completed_count": 1,
        "stage2_unavailable_count": 0,
        "stage2_incomplete_count": 0,
        "coverage_rate": 1,
        "research_incomplete_reason_counts": {},
        "screening_input_count": 1,
        "screening_pass_count": 1,
        "screening_reject_count": 0,
        "screening_data_unavailable_count": 0,
        "screening_incomplete_count": 0,
        "screening_complete": True,
        "candidate_count_consistent": True,
        "all_enabled_rules_evaluated": True,
        "screening_rule_counts": [],
        "ranking_complete": False,
    }
    summary.update(overrides)
    return summary


def test_trade_recommendation_must_reference_an_eligible_candidate():
    recommendation = {**METADATA, "decision": "TRADE", "ticker": "1234"}
    candidates = {
        **METADATA,
        "candidates": [{"ticker": "1234", "status": "REJECTED"}],
    }

    with pytest.raises(ValueError, match="ELIGIBLE"):
        validate_recommendation_candidate_link(recommendation, candidates)


def test_data_unavailable_recommendation_does_not_require_eligible_candidate():
    recommendation = {**METADATA, "decision": "DATA_UNAVAILABLE", "ticker": None}
    candidates = {
        **METADATA,
        "candidates": [{"ticker": "1234", "status": "DATA_UNAVAILABLE"}],
    }

    validate_recommendation_candidate_link(recommendation, candidates)


def test_recommendation_pipeline_link_rejects_incomplete_pipeline():
    recommendation = {
        **METADATA,
        "decision": "NO_TRADE",
        "ticker": None,
        "pipeline_summary": complete_summary(
            research_complete=0,
            research_incomplete=1,
            screened=0,
            eligible=0,
            screening_input_count=0,
            screening_pass_count=0,
        ),
    }
    candidate_pipeline = {
        **METADATA,
        "summary": complete_summary(
            research_complete=0,
            research_incomplete=1,
            screened=0,
            eligible=0,
            pipeline_complete=False,
            screening_input_count=0,
            screening_pass_count=0,
        ),
    }

    with pytest.raises(ValueError, match="not complete"):
        validate_recommendation_pipeline_link(recommendation, candidate_pipeline)


def test_recommendation_pipeline_link_requires_summary_counts_to_match():
    recommendation = {
        **METADATA,
        "decision": "NO_TRADE",
        "ticker": None,
        "pipeline_summary": complete_summary(eligible=0, rejected=1),
    }
    candidate_pipeline = {
        **METADATA,
        "summary": complete_summary(),
    }

    with pytest.raises(ValueError, match="eligible does not match"):
        validate_recommendation_pipeline_link(recommendation, candidate_pipeline)


def test_trade_recommendation_allowed_with_pipeline_ranking_complete_false():
    # candidate_pipeline.json is upstream of Ranking and its schema always
    # carries summary.ranking_complete == false; Ranking completion is
    # tracked in ranking.json (and selection.json), not candidate_pipeline.
    recommendation = {
        **METADATA,
        "decision": "TRADE",
        "ticker": "1234",
        "pipeline_summary": complete_summary(),
    }
    candidate_pipeline = {**METADATA, "summary": complete_summary()}

    validate_recommendation_pipeline_link(recommendation, candidate_pipeline)


def test_recommendation_and_risk_result_must_use_same_config():
    recommendation = {**METADATA, "decision": "NO_TRADE", "ticker": None}
    risk_result = {
        **METADATA,
        "decision": "NO_TRADE",
        "ticker": None,
        "config_sha256": "b" * 64,
    }

    with pytest.raises(ValueError, match="config_sha256"):
        validate_recommendation_risk_link(recommendation, risk_result)


def test_recommendation_urls_must_exist_in_source_ledger():
    recommendation = {
        **METADATA,
        "source_urls": ["https://example.test/not-recorded"],
    }
    source_payload = {
        "target_date": "2026-08-10",
        "sources": [{"source_url": "https://example.test/recorded"}],
    }

    with pytest.raises(ValueError, match="missing from sources.json"):
        validate_recommendation_sources(recommendation, source_payload)


def test_recommendation_source_statuses_may_reference_source_attempts():
    recommendation = {
        **METADATA,
        "source_urls": [],
        "source_statuses": [
            {
                "source_id": "JPX_TDNET",
                "status": "PARSE_FAILED",
                "url": "https://example.test/tdnet",
            }
        ],
    }
    source_payload = {
        "target_date": "2026-08-10",
        "sources": [],
        "source_attempts": [{"url": "https://example.test/tdnet"}],
    }

    validate_recommendation_sources(recommendation, source_payload)


def test_candidate_pipeline_inputs_must_use_same_target_date():
    config = load_strategy_config()
    config_sha256 = strategy_config_sha256(config)

    with pytest.raises(ValueError, match="target_date"):
        validate_candidate_pipeline_inputs(
            market_research={"target_date": "2026-08-10"},
            market_target_date="2026-08-11",
            candidates={
                "target_date": "2026-08-10",
                "strategy_version": config["strategy_version"],
                "config_sha256": config_sha256,
            },
            source_payload={"target_date": "2026-08-10"},
            config=config,
        )


def test_candidate_pipeline_inputs_must_use_same_config():
    config = load_strategy_config()

    with pytest.raises(ValueError, match="config_sha256"):
        validate_candidate_pipeline_inputs(
            market_research={"target_date": "2026-08-10"},
            market_target_date="2026-08-10",
            candidates={
                "target_date": "2026-08-10",
                "strategy_version": config["strategy_version"],
                "config_sha256": "0" * 64,
            },
            source_payload={"target_date": "2026-08-10"},
            config=config,
        )


def test_candidate_pipeline_inputs_rejects_unrecorded_stage1_source_refs():
    config = load_strategy_config()
    config_sha256 = strategy_config_sha256(config)

    with pytest.raises(ValueError, match="source-backed stage1_checks"):
        validate_candidate_pipeline_inputs(
            market_research={
                "target_date": "2026-08-10",
                "candidate_research": [
                    {
                        "ticker": "1234",
                        "universe_status": "PASSED",
                        "stage1_status": "REJECTED",
                        "stage1_checks": [
                            {
                                "check_id": "share_unit",
                                "status": "REJECTED",
                                "reason_code": "SHARE_UNIT_NOT_100",
                                "source_refs": [
                                    "JPX_TRADING_UNIT:1234:share_unit"
                                ],
                                "source_attempt_ids": [],
                            }
                        ],
                    }
                ],
            },
            market_target_date="2026-08-10",
            candidates={
                "target_date": "2026-08-10",
                "strategy_version": config["strategy_version"],
                "config_sha256": config_sha256,
            },
            source_payload={
                "target_date": "2026-08-10",
                "sources": [],
                "source_attempts": [],
            },
            config=config,
        )


def test_performance_inputs_must_use_same_target_date():
    with pytest.raises(ValueError, match="target_date"):
        validate_performance_inputs(
            market_research={"target_date": "2026-08-10"},
            candidate_pipeline={"target_date": "2026-08-11"},
            source_payload={"target_date": "2026-08-10"},
        )


def test_run_artifact_allowlist_detects_temporary_files():
    run_dir = Path("tests/.tmp/run_allowlist")
    if run_dir.exists():
        shutil.rmtree(run_dir)
    try:
        run_dir.mkdir(parents=True)
        (run_dir / "market_research.json").write_text("{}", encoding="utf-8")
        (run_dir / "tmp_pydeps").mkdir()
        (run_dir / "parser.py").write_text("# temp", encoding="utf-8")

        unexpected = validate_run_artifact_allowlist(run_dir)

        assert unexpected == ("parser.py", "tmp_pydeps")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
