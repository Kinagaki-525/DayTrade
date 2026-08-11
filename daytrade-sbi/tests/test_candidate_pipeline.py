import pytest

from src.candidate_pipeline import build_candidate_pipeline
from src.config import load_strategy_config
from src.contracts import validate_json_document
from tests.factories import (
    make_candidate_research,
    make_market_record,
    make_source_attempt,
    make_standard_source_checks,
)


def test_pipeline_keeps_discovery_candidate_without_market_data():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                            "rank": 1,
                            "display_value": "1000000",
                            "title": None,
                        }
                    ],
                }
            ],
            "candidate_research": [
                make_candidate_research(
                    "1234",
                    data_status="DATA_UNAVAILABLE",
                    status_reasons=["secondary OHLCV source missing"],
                    source_policy_status="SINGLE_SOURCE_ONLY",
                    source_checks=make_standard_source_checks(
                        secondary_ohlcv={
                            "status": "SINGLE_SOURCE_ONLY",
                            "source_attempt_ids": [
                                "kabutan_history-1234-single_source_only"
                            ],
                        }
                    ),
                )
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={
            "sources": [],
            "source_attempts": [
                make_source_attempt(
                    source_id="YAHOO_JP_VOLUME_RANKING",
                    source_role="PRIMARY",
                    criticality="DISCOVERY_CRITICAL",
                    information_type="VOLUME_RANKING",
                    candidate_code=None,
                    status="PARSE_FAILED",
                ),
                make_source_attempt(
                    source_id="KABUTAN_HISTORY",
                    source_role="SECONDARY",
                    criticality="TRADE_CRITICAL",
                    information_type="OHLCV",
                    candidate_code="1234",
                    status="SINGLE_SOURCE_ONLY",
                ),
            ],
        },
        config=load_strategy_config(),
    )

    validate_json_document(payload, "candidate_pipeline.schema.json")
    assert payload["summary"]["discovered"] == 1
    assert payload["summary"]["data_unavailable"] == 1
    assert payload["candidates"][0]["pipeline_status"] == "DATA_UNAVAILABLE"
    assert "secondary_ohlcv" in payload["candidates"][0]["missing_requirements"]
    assert payload["candidates"][0]["source_attempt_ids"]


def test_pipeline_prefers_structured_research_reasons_and_missing_requirements():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                            "rank": 1,
                            "display_value": "1000000",
                            "title": None,
                        }
                    ],
                }
            ],
            "candidate_research": [
                make_candidate_research(
                    "1234",
                    data_status="DATA_UNAVAILABLE",
                    status_reasons=["long human-readable fallback"],
                    reason_codes=["SECONDARY_OHLCV_MISSING"],
                    missing_requirements=["secondary_ohlcv"],
                    source_checks=make_standard_source_checks(
                        secondary_ohlcv={
                            "status": "ACCESS_FAILED",
                            "source_attempt_ids": [
                                "kabutan_history-1234-access_failed"
                            ],
                        }
                    ),
                )
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={
            "sources": [],
            "source_attempts": [
                make_source_attempt(
                    source_id="KABUTAN_HISTORY",
                    source_role="SECONDARY",
                    criticality="TRADE_CRITICAL",
                    information_type="OHLCV",
                    candidate_code="1234",
                    status="ACCESS_FAILED",
                )
            ],
        },
        config=load_strategy_config(),
    )

    candidate = payload["candidates"][0]
    assert candidate["reason_codes"] == ["SECONDARY_OHLCV_MISSING"]
    assert candidate["missing_requirements"] == [
        "secondary_ohlcv",
        "market_data",
    ]


def test_pipeline_requires_explicit_source_attempt_id():
    attempt = make_source_attempt(
        source_id="YAHOO_JP_VOLUME_RANKING",
        source_role="PRIMARY",
        criticality="DISCOVERY_CRITICAL",
        information_type="VOLUME_RANKING",
        candidate_code=None,
        status="PARSE_FAILED",
    )
    del attempt["attempt_id"]

    with pytest.raises(ValueError, match="attempt_id"):
        build_candidate_pipeline(
            market_research={
                "target_date": "2026-08-10",
                "discovery_candidates": [
                    {
                        "ticker": "1234",
                        "company_name": "Example Co.",
                        "market": "TSE Prime",
                        "discovery_reasons": [
                            {
                                "discovery_type": "VOLUME_RANKING",
                                "source_id": "YAHOO_JP_VOLUME_RANKING",
                                "source_url": "https://example.test/volume",
                                "rank": 1,
                                "display_value": "1000000",
                                "title": None,
                            }
                        ],
                    }
                ],
                "candidate_research": [],
            },
            market_records=[],
            candidates_payload={"candidates": []},
            source_payload={"sources": [], "source_attempts": [attempt]},
            config=load_strategy_config(),
        )


def test_pipeline_links_only_matching_discovery_source_attempts():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                            "rank": 1,
                            "display_value": "1000000",
                            "title": None,
                        }
                    ],
                }
            ],
            "candidate_research": [],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={
            "sources": [],
            "source_attempts": [
                make_source_attempt(
                    source_id="YAHOO_JP_VOLUME_RANKING",
                    source_role="PRIMARY",
                    criticality="DISCOVERY_CRITICAL",
                    information_type="VOLUME_RANKING",
                    candidate_code=None,
                ),
                make_source_attempt(
                    source_id="YAHOO_JP_GAIN_RANKING",
                    source_role="PRIMARY",
                    criticality="DISCOVERY_CRITICAL",
                    information_type="PRICE_GAIN_RANKING",
                    candidate_code=None,
                ),
            ],
        },
        config=load_strategy_config(),
    )

    assert payload["candidates"][0]["source_attempt_ids"] == [
        "yahoo_jp_volume_ranking-discovery-found"
    ]


def test_stage1_reject_requires_source_backed_check():
    base_market_research = {
        "target_date": "2026-08-10",
        "discovery_candidates": [
            {
                "ticker": "1234",
                "company_name": "Example Co.",
                "market": "TSE Prime",
                "discovery_reasons": [
                    {
                        "discovery_type": "VOLUME_RANKING",
                        "source_id": "YAHOO_JP_VOLUME_RANKING",
                        "source_url": "https://example.test/volume",
                        "rank": 1,
                        "display_value": "1000000",
                        "title": None,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="source-backed stage1_checks"):
        build_candidate_pipeline(
            market_research={
                **base_market_research,
                "candidate_research": [
                    make_candidate_research(
                        "1234",
                        stage1_status="REJECTED",
                        stage2_status="SKIPPED",
                        context_research_status="SKIPPED",
                        stage1_checks=[
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
                    )
                ],
            },
            market_records=[],
            candidates_payload={"candidates": []},
            source_payload={"sources": [], "source_attempts": []},
            config=load_strategy_config(),
        )

    supported = build_candidate_pipeline(
        market_research={
            **base_market_research,
            "candidate_research": [
                make_candidate_research(
                    "1234",
                    stage1_status="REJECTED",
                    stage2_status="SKIPPED",
                    context_research_status="SKIPPED",
                    stage1_checks=[
                        {
                            "check_id": "share_unit",
                            "status": "REJECTED",
                            "reason_code": "SHARE_UNIT_NOT_100",
                            "source_refs": ["JPX_TRADING_UNIT:1234:share_unit"],
                            "source_attempt_ids": [],
                        }
                    ],
                )
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={
            "sources": [
                {
                    "source_ref": "JPX_TRADING_UNIT:1234:share_unit",
                    "source_id": "JPX_TRADING_UNIT",
                }
            ],
            "source_attempts": [],
        },
        config=load_strategy_config(),
    )

    candidate = supported["candidates"][0]
    assert candidate["pipeline_status"] == "REJECTED"
    assert candidate["reason_codes"] == ["SHARE_UNIT_NOT_100"]
    assert candidate["failed_checks"] == ["stage1:share_unit"]
    assert candidate["source_refs"] == ["JPX_TRADING_UNIT:1234:share_unit"]


def test_pipeline_distinguishes_screening_results():
    record = make_market_record()
    base_research = {
        "target_date": "2026-08-10",
        "discovery_candidates": [
            {
                "ticker": "1234",
                "company_name": "Example Co.",
                "market": "TSE Prime",
                "discovery_reasons": [
                    {
                        "discovery_type": "PRICE_GAIN_RANKING",
                        "source_id": "YAHOO_JP_GAIN_RANKING",
                        "source_url": "https://example.test/gain",
                        "rank": 1,
                        "display_value": "10%",
                        "title": None,
                    }
                ],
            }
        ],
        "candidate_research": [
            make_candidate_research("1234")
        ],
    }

    eligible = build_candidate_pipeline(
        market_research=base_research,
        market_records=[record],
        candidates_payload={
            "candidates": [
                {
                    "ticker": "1234",
                    "status": "ELIGIBLE",
                    "reasons": [],
                    "unresolved_screening": [],
                    "order_plan": {"dummy": "not inspected"},
                }
            ]
        },
        source_payload={"sources": [source.as_dict() for source in record.sources], "source_attempts": []},
        config=load_strategy_config(),
    )
    assert eligible["candidates"][0]["pipeline_status"] == "ELIGIBLE"
    assert eligible["summary"]["eligible"] == 1

    rejected = build_candidate_pipeline(
        market_research=base_research,
        market_records=[record],
        candidates_payload={
            "candidates": [
                {
                    "ticker": "1234",
                    "status": "REJECTED",
                    "reasons": ["entry_limit multiplied by shares exceeds capital"],
                    "unresolved_screening": [],
                    "order_plan": None,
                }
            ]
        },
        source_payload={"sources": [source.as_dict() for source in record.sources], "source_attempts": []},
        config=load_strategy_config(),
    )
    assert rejected["candidates"][0]["pipeline_status"] == "REJECTED"
    assert rejected["summary"]["rejected"] == 1


def test_pipeline_distinguishes_discovery_zero_from_research_incomplete():
    empty = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [],
            "candidate_research": [],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={"sources": [], "source_attempts": []},
        config=load_strategy_config(),
    )
    assert empty["summary"]["discovered"] == 0
    assert empty["summary"]["research_incomplete"] == 0

    incomplete = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                            "rank": 1,
                            "display_value": "1000000",
                            "title": None,
                        }
                    ],
                }
            ],
            "candidate_research": [],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={"sources": [], "source_attempts": []},
        config=load_strategy_config(),
    )
    assert incomplete["summary"]["discovered"] == 1
    assert incomplete["summary"]["research_incomplete"] == 1
    assert incomplete["candidates"][0]["pipeline_status"] == "DISCOVERED"


def test_pipeline_treats_missing_standard_source_checks_as_incomplete():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                        }
                    ],
                }
            ],
            "candidate_research": [
                make_candidate_research("1234", source_checks=[])
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={"sources": [], "source_attempts": []},
        config=load_strategy_config(),
    )

    candidate = payload["candidates"][0]
    assert candidate["pipeline_status"] == "RESEARCH_IN_PROGRESS"
    assert candidate["research_incomplete_reason"] == "NOT_STARTED"
    assert payload["summary"]["pipeline_complete"] is False


def test_pipeline_does_not_reject_candidate_for_empty_tdnet_context():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                            "rank": 1,
                            "display_value": "1000000",
                            "title": None,
                        }
                    ],
                }
            ],
            "candidate_research": [
                make_candidate_research("1234", context_research_status="COMPLETE")
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={
            "sources": [],
            "source_attempts": [
                {
                    "attempt_id": "tdnet-empty-1234",
                    "source_id": "JPX_TDNET",
                    "source_role": "CONTEXT",
                    "criticality": "CONTEXT",
                    "information_type": "TIMELY_DISCLOSURE",
                    "candidate_code": "1234",
                    "status": "FOUND",
                    "values": [],
                    "result_count": 0,
                }
            ],
        },
        config=load_strategy_config(),
    )

    candidate = payload["candidates"][0]
    assert candidate["pipeline_status"] == "RESEARCH_COMPLETE"
    assert candidate["failed_checks"] == []
    assert "context_research" in candidate["completed_checks"]


def test_pipeline_summary_tracks_stage2_incomplete_counts():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                        }
                    ],
                },
                {
                    "ticker": "5678",
                    "company_name": "Second Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "PRICE_GAIN_RANKING",
                            "source_id": "YAHOO_JP_GAIN_RANKING",
                            "source_url": "https://example.test/gain",
                        }
                    ],
                },
            ],
            "candidate_research": [
                make_candidate_research("1234"),
                make_candidate_research(
                    "5678",
                    stage2_status="NOT_STARTED",
                    context_research_status="NOT_STARTED",
                    source_checks=make_standard_source_checks(
                        secondary_ohlcv={
                            "status": "NOT_STARTED",
                        }
                    ),
                ),
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={"sources": [], "source_attempts": []},
        config=load_strategy_config(),
    )

    validate_json_document(payload, "candidate_pipeline.schema.json")
    assert payload["summary"]["pipeline_complete"] is False
    assert payload["summary"]["stage2_target_count"] == 2
    assert payload["summary"]["stage2_completed_count"] == 1
    assert payload["summary"]["stage2_incomplete_count"] == 1
    assert payload["summary"]["coverage_rate"] == 0.5
    assert payload["summary"]["research_incomplete_reason_counts"] == {
        "NOT_STARTED": 1
    }


def test_pipeline_marks_returned_unmerged_subagent_results_incomplete():
    payload = build_candidate_pipeline(
        market_research={
            "target_date": "2026-08-10",
            "discovery_candidates": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "discovery_reasons": [
                        {
                            "discovery_type": "VOLUME_RANKING",
                            "source_id": "YAHOO_JP_VOLUME_RANKING",
                            "source_url": "https://example.test/volume",
                        }
                    ],
                }
            ],
            "candidate_research": [
                make_candidate_research("1234")
            ],
            "subagent_batches": [
                {
                    "batch_id": "stage2-a",
                    "agent_name": "market-researcher",
                    "lifecycle_status": "RETURNED",
                    "candidate_codes": ["1234"],
                    "returned_candidate_codes": ["1234"],
                    "merged_candidate_codes": [],
                }
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={"sources": [], "source_attempts": []},
        config=load_strategy_config(),
    )

    candidate = payload["candidates"][0]
    assert candidate["pipeline_status"] == "RESEARCH_IN_PROGRESS"
    assert candidate["research_incomplete_reason"] == "SUBAGENT_RESULT_NOT_MERGED"
    assert payload["summary"]["pipeline_complete"] is False
    assert payload["summary"]["research_incomplete_reason_counts"] == {
        "SUBAGENT_RESULT_NOT_MERGED": 1
    }
