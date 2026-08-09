from copy import deepcopy

from src.research import (
    merge_discovery_candidates,
    validate_market_records_against_research,
    validate_market_research,
)
from src.source_matrix import load_source_matrix
from tests.factories import make_market_record


def market_research_payload():
    return {
        "schema_version": 1,
        "target_date": "2026-08-10",
        "previous_trading_day": "2026-08-07",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "research_executed_at": "2026-08-07T20:15:00+09:00",
        "market_filter": "ALL_MARKETS",
        "overall_status": "COMPLETE",
        "discovery": [
            discovery_route("VOLUME_RANKING", "YAHOO_JP_VOLUME_RANKING", "volume"),
            discovery_route("PRICE_GAIN_RANKING", "YAHOO_JP_GAIN_RANKING", "gain"),
            {
                "discovery_type": "TIMELY_DISCLOSURE",
                "source_id": "JPX_TDNET",
                "status": "FOUND",
                "source_url": "https://example.test/tdnet",
                "retrieved_at": "2026-08-07T20:15:00+09:00",
                "result_count": 0,
                "items": [],
            },
        ],
        "discovery_candidates": [],
        "candidate_research": [],
    }


def complete_candidate_research(payload):
    payload["discovery_candidates"] = merge_discovery_candidates(payload["discovery"])
    payload["candidate_research"] = [
        {
            "ticker": candidate["ticker"],
            "data_status": "VERIFIED",
            "status_reasons": [],
            "source_policy_status": "FOUND",
        }
        for candidate in payload["discovery_candidates"]
    ]
    return payload


def discovery_route(discovery_type, source_id, value_label):
    items = []
    for index in range(50):
        ticker = f"{1000 + index}"
        items.append(
            {
                "ticker": ticker,
                "company_name": f"Example {ticker}",
                "market": "TSE Prime",
                "rank": index + 1,
                "display_value": f"{value_label}-{index + 1}",
                "disclosure_datetime": None,
                "title": None,
                "source_url": f"https://example.test/{source_id.lower()}/{ticker}",
                "retrieved_at": "2026-08-07T20:15:00+09:00",
            }
        )
    return {
        "discovery_type": discovery_type,
        "source_id": source_id,
        "status": "FOUND",
        "source_url": f"https://example.test/{source_id.lower()}",
        "retrieved_at": "2026-08-07T20:15:00+09:00",
        "result_count": 50,
        "items": items,
    }


def test_market_research_accepts_top50_and_tdnet_zero_results():
    payload = complete_candidate_research(market_research_payload())

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True
    assert result.discovery_complete is True


def test_discovery_union_keeps_multiple_reasons_for_same_ticker():
    routes = [
        {
            "discovery_type": "VOLUME_RANKING",
            "source_id": "YAHOO_JP_VOLUME_RANKING",
            "status": "FOUND",
            "items": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "rank": 1,
                    "display_value": "1000000",
                    "source_url": "https://example.test/volume",
                }
            ],
        },
        {
            "discovery_type": "PRICE_GAIN_RANKING",
            "source_id": "YAHOO_JP_GAIN_RANKING",
            "status": "FOUND",
            "items": [
                {
                    "ticker": "1234",
                    "company_name": "Example Co.",
                    "market": "TSE Prime",
                    "rank": 2,
                    "display_value": "12.3%",
                    "source_url": "https://example.test/gain",
                }
            ],
        },
    ]

    candidates = merge_discovery_candidates(routes)

    assert len(candidates) == 1
    assert candidates[0]["discovered_by"] == [
        "VOLUME_RANKING",
        "PRICE_GAIN_RANKING",
    ]
    assert len(candidates[0]["discovery_reasons"]) == 2


def test_discovery_source_failure_is_not_complete():
    payload = market_research_payload()
    payload["overall_status"] = "DISCOVERY_INCOMPLETE"
    payload["discovery"][0]["status"] = "ACCESS_FAILED"
    payload["discovery"][0]["items"] = []
    payload["discovery"][0]["result_count"] = 0
    payload = complete_candidate_research(payload)

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True
    assert result.discovery_complete is False


def test_market_research_rejects_source_matrix_outside_source_id():
    payload = complete_candidate_research(market_research_payload())
    payload["discovery"][0]["source_id"] = "UNKNOWN_SOURCE"

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("undefined source_id" in error for error in result.errors)


def test_market_research_requires_discovery_candidate_union():
    payload = market_research_payload()

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("missing ticker(s) from Discovery union" in error for error in result.errors)


def test_market_research_rejects_candidate_research_outside_discovery():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"].append(
        {
            "ticker": "9999",
            "data_status": "VERIFIED",
            "status_reasons": [],
            "source_policy_status": "FOUND",
        }
    )

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("outside Discovery candidates" in error for error in result.errors)


def test_market_research_rejects_missing_candidate_research():
    payload = complete_candidate_research(market_research_payload())
    missing_ticker = payload["candidate_research"].pop()["ticker"]

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any(missing_ticker in error for error in result.errors)


def test_market_data_research_alignment_requires_matching_statuses():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"] = [
        {
            "ticker": "1234",
            "data_status": "DATA_UNAVAILABLE",
            "status_reasons": ["missing secondary source"],
            "source_policy_status": "SINGLE_SOURCE_ONLY",
        }
    ]

    result = validate_market_records_against_research([make_market_record()], payload)

    assert result.valid is False
    assert any(
        "data_status does not match" in error
        for error in result.errors_by_ticker["1234"]
    )
