import pytest

from src.candidate_pipeline import build_candidate_pipeline
from src.config import load_strategy_config
from src.contracts import validate_json_document
from tests.factories import make_market_record, make_source_attempt


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
                {
                    "ticker": "1234",
                    "data_status": "DATA_UNAVAILABLE",
                    "status_reasons": ["secondary OHLCV source missing"],
                    "source_policy_status": "SINGLE_SOURCE_ONLY",
                }
            ],
        },
        market_records=[],
        candidates_payload={"candidates": []},
        source_payload={
            "sources": [],
            "source_attempts": [
                make_source_attempt(
                    source_id="JPX_TDNET",
                    source_role="PRIMARY",
                    criticality="DISCOVERY_CRITICAL",
                    information_type="TIMELY_DISCLOSURE",
                    candidate_code=None,
                    status="PARSE_FAILED",
                )
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


def test_pipeline_requires_explicit_source_attempt_id():
    attempt = make_source_attempt(
        source_id="JPX_TDNET",
        source_role="PRIMARY",
        criticality="DISCOVERY_CRITICAL",
        information_type="TIMELY_DISCLOSURE",
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
                                "discovery_type": "TIMELY_DISCLOSURE",
                                "source_id": "JPX_TDNET",
                                "source_url": "https://example.test/tdnet",
                                "rank": None,
                                "display_value": None,
                                "title": "Disclosure",
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
            {
                "ticker": "1234",
                "data_status": "VERIFIED",
                "status_reasons": [],
                "source_policy_status": "FOUND",
            }
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
                            "discovery_type": "TIMELY_DISCLOSURE",
                            "source_id": "JPX_TDNET",
                            "source_url": "https://example.test/tdnet",
                            "rank": None,
                            "display_value": None,
                            "title": "Disclosure",
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
