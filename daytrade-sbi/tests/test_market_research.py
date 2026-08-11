from copy import deepcopy
import json
from pathlib import Path

from src.research import (
    merge_discovery_candidates,
    validate_market_records_against_research,
    validate_market_research,
    validate_market_research_window_link,
)
from src.source_matrix import load_source_matrix
from tests.factories import (
    make_candidate_research,
    make_market_record,
    make_standard_source_checks,
)


def market_research_payload():
    return {
        "schema_version": 2,
        "target_date": "2026-08-10",
        "previous_trading_day": "2026-08-07",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "research_executed_at": "2026-08-07T20:15:00+09:00",
        "research_window": {
            "run_type": "FIRST_RUN",
            "window_start": "2026-08-06T20:00:00+09:00",
            "window_end": "2026-08-07T20:00:00+09:00",
            "previous_research_cutoff": None,
            "previous_run_date": None,
            "bootstrap_lookback_days": 1,
        },
        "market_filter": "ALL_MARKETS",
        "overall_status": "COMPLETE",
        "discovery": [
            discovery_route("VOLUME_RANKING", "YAHOO_JP_VOLUME_RANKING", "volume"),
            discovery_route("PRICE_GAIN_RANKING", "YAHOO_JP_GAIN_RANKING", "gain"),
        ],
        "discovery_candidates": [],
        "candidate_research": [],
    }


def complete_candidate_research(payload):
    payload["discovery_candidates"] = merge_discovery_candidates(payload["discovery"])
    payload["candidate_research"] = [
        make_candidate_research(candidate["ticker"])
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


def test_market_research_accepts_two_yahoo_top50_routes():
    payload = complete_candidate_research(market_research_payload())

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True
    assert result.discovery_complete is True


def test_market_research_requires_all_standard_source_checks():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0]["source_checks"].pop()

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("source_checks" in error for error in result.errors)


def test_market_research_accepts_100_candidates_from_two_yahoo_routes():
    payload = market_research_payload()
    gain_route = payload["discovery"][1]
    for index, item in enumerate(gain_route["items"]):
        ticker = f"{2000 + index}"
        item["ticker"] = ticker
        item["company_name"] = f"Example {ticker}"
        item["source_url"] = f"https://example.test/yahoo_jp_gain_ranking/{ticker}"
    payload = complete_candidate_research(payload)

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True
    assert len(payload["discovery_candidates"]) == 100


def test_market_research_rejects_tdnet_discovery_route():
    payload = market_research_payload()
    payload["discovery"].append(
        {
            "discovery_type": "TIMELY_DISCLOSURE",
            "source_id": "JPX_TDNET",
            "status": "FOUND",
            "source_url": "https://example.test/tdnet",
            "retrieved_at": "2026-08-07T20:15:00+09:00",
            "result_count": 0,
            "items": [],
        }
    )

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("TIMELY_DISCLOSURE" in error for error in result.errors)


def test_market_research_rejects_missing_research_window():
    payload = complete_candidate_research(market_research_payload())
    del payload["research_window"]

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("research_window" in error for error in result.errors)


def test_market_research_rejects_window_end_that_differs_from_cutoff():
    payload = complete_candidate_research(market_research_payload())
    payload["research_window"]["window_end"] = "2026-08-07T19:59:00+09:00"

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert "research_window.window_end must equal research_cutoff" in result.errors


def test_market_research_accepts_normal_run_window():
    payload = complete_candidate_research(market_research_payload())
    payload["research_window"] = {
        "run_type": "NORMAL_RUN",
        "window_start": "2026-08-06T20:00:00+09:00",
        "window_end": "2026-08-07T20:00:00+09:00",
        "previous_research_cutoff": "2026-08-06T20:00:00+09:00",
        "previous_run_date": "2026-08-07",
        "bootstrap_lookback_days": None,
    }

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True


def test_market_research_rejects_normal_run_window_start_mismatch():
    payload = complete_candidate_research(market_research_payload())
    payload["research_window"] = {
        "run_type": "NORMAL_RUN",
        "window_start": "2026-08-06T21:00:00+09:00",
        "window_end": "2026-08-07T20:00:00+09:00",
        "previous_research_cutoff": "2026-08-06T20:00:00+09:00",
        "previous_run_date": "2026-08-07",
        "bootstrap_lookback_days": None,
    }

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("window_start" in error for error in result.errors)


def test_market_research_window_link_rejects_mismatch():
    payload = complete_candidate_research(market_research_payload())
    resolved_window = {
        "schema_version": 1,
        "target_date": payload["target_date"],
        "previous_trading_day": payload["previous_trading_day"],
        "research_cutoff": payload["research_cutoff"],
        "research_window": deepcopy(payload["research_window"]),
    }
    resolved_window["research_window"]["window_start"] = "2026-08-06T21:00:00+09:00"

    errors = validate_market_research_window_link(payload, resolved_window)

    assert errors == (
        "market_research.research_window must match research_window.json",
    )


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
    payload["discovery"][0]["result_count"] = None
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
        make_candidate_research("9999")
    )

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("outside Discovery candidates" in error for error in result.errors)


def test_market_research_rejects_stage1_reject_without_source_backed_check():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "stage1_status": "REJECTED",
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": [],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("source-backed stage1_checks" in error for error in result.errors)


def test_market_research_rejects_stage1_reject_with_unrecorded_source_ref():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "stage1_status": "REJECTED",
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": ["JPX_TRADING_UNIT:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {"sources": [], "source_attempts": []},
    )

    assert result.valid is False
    assert any("source-backed stage1_checks" in error for error in result.errors)


def test_market_research_accepts_source_backed_stage1_reject():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
            {
                "universe_status": "PASSED",
                "stage1_status": "REJECTED",
                "stage2_status": "SKIPPED",
                "context_research_status": "SKIPPED",
                "reason_codes": ["SHARE_UNIT_NOT_100"],
                "missing_requirements": [],
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": ["JPX_TRADING_UNIT:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "JPX_TRADING_UNIT:1000:share_unit",
                    "source_id": "JPX_TRADING_UNIT",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is True


def test_market_research_rejects_share_unit_stage1_reject_with_listed_source():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "REJECTED",
            "stage2_status": "SKIPPED",
            "context_research_status": "SKIPPED",
            "reason_codes": ["SHARE_UNIT_NOT_100"],
            "missing_requirements": [],
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": ["JPX_LISTED_COMPANY:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "JPX_LISTED_COMPANY:1000:share_unit",
                    "source_id": "JPX_LISTED_COMPANY",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is False
    assert any("source-backed stage1_checks" in error for error in result.errors)


def test_market_research_accepts_generic_capital_limit_stage1_reject():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
            {
                "universe_status": "PASSED",
                "stage1_status": "REJECTED",
                "stage2_status": "SKIPPED",
                "context_research_status": "SKIPPED",
                "reason_codes": ["CAPITAL_LIMIT_EXCEEDED"],
                "missing_requirements": [],
            "stage1_checks": [
                {
                    "check_id": "capital_limit",
                    "status": "REJECTED",
                    "reason_code": "CAPITAL_LIMIT_EXCEEDED",
                    "source_refs": ["YAHOO_JP_HISTORY:1000:previous_high"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "YAHOO_JP_HISTORY:1000:previous_high",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is True


def test_market_research_rejects_unapproved_stage1_check_id():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "REJECTED",
            "stage1_checks": [
                {
                    "check_id": "low_volume",
                    "status": "REJECTED",
                    "reason_code": "LOW_VOLUME",
                    "source_refs": ["YAHOO_JP_HISTORY:1000:volume"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "YAHOO_JP_HISTORY:1000:volume",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is False
    assert any("unapproved Stage 1 check_id" in error for error in result.errors)


def test_market_research_rejects_unapproved_stage1_reason_code():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "REJECTED",
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "LOW_VOLUME",
                    "source_refs": ["JPX_TRADING_UNIT:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "JPX_TRADING_UNIT:1000:share_unit",
                    "source_id": "JPX_TRADING_UNIT",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is False
    assert any("unapproved Stage 1 reason_code" in error for error in result.errors)


def test_market_research_rejects_stage1_reject_without_reason_code():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "REJECTED",
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": None,
                    "source_refs": ["JPX_TRADING_UNIT:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "JPX_TRADING_UNIT:1000:share_unit",
                    "source_id": "JPX_TRADING_UNIT",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is False
    assert any("without reason_code" in error for error in result.errors)


def test_market_research_rejects_stage2_after_stage1_reject():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "REJECTED",
            "stage2_status": "COMPLETE",
            "context_research_status": "SKIPPED",
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": ["JPX_TRADING_UNIT:1000:share_unit"],
                    "source_attempt_ids": [],
                }
            ],
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [
                {
                    "source_ref": "JPX_TRADING_UNIT:1000:share_unit",
                    "source_id": "JPX_TRADING_UNIT",
                }
            ],
            "source_attempts": [],
        },
    )

    assert result.valid is False
    assert any("stage1_status=REJECTED" in error for error in result.errors)


def test_market_research_rejects_stage2_without_stage1_pass():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "NOT_STARTED",
            "stage2_status": "COMPLETE",
        }
    )

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("before stage1_status=PASSED" in error for error in result.errors)


def test_market_research_rejects_missing_candidate_research():
    payload = complete_candidate_research(market_research_payload())
    missing_ticker = payload["candidate_research"].pop()["ticker"]

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any(missing_ticker in error for error in result.errors)


def test_market_research_allows_missing_candidate_research_only_as_pipeline_incomplete():
    payload = complete_candidate_research(market_research_payload())
    missing_ticker = payload["candidate_research"].pop()["ticker"]
    payload["overall_status"] = "PIPELINE_INCOMPLETE"

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True
    assert any(missing_ticker in warning for warning in result.warnings)


def test_market_research_rejects_missing_candidate_research_as_data_unavailable():
    payload = complete_candidate_research(market_research_payload())
    missing_ticker = payload["candidate_research"].pop()["ticker"]
    payload["overall_status"] = "DATA_UNAVAILABLE"

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any(missing_ticker in error for error in result.errors)


def test_market_research_rejects_data_unavailable_without_attempted_source_check():
    payload = complete_candidate_research(market_research_payload())
    payload["overall_status"] = "DATA_UNAVAILABLE"
    payload["candidate_research"][0].update(
        {
            "data_status": "DATA_UNAVAILABLE",
            "status_reasons": ["secondary OHLCV source missing"],
            "source_policy_status": "SINGLE_SOURCE_ONLY",
        }
    )

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("without attempted source_checks" in error for error in result.errors)


def test_market_research_accepts_data_unavailable_with_external_source_check():
    payload = complete_candidate_research(market_research_payload())
    payload["overall_status"] = "DATA_UNAVAILABLE"
    payload["candidate_research"][0].update(
        {
            "data_status": "DATA_UNAVAILABLE",
            "status_reasons": ["secondary OHLCV source access failed"],
            "source_policy_status": "ACCESS_FAILED",
            "source_checks": make_standard_source_checks(
                secondary_ohlcv={
                    "status": "ACCESS_FAILED",
                    "source_id": "KABUTAN_HISTORY",
                    "information_type": "OHLCV",
                    "reason_code": "SECONDARY_OHLCV_ACCESS_FAILED",
                    "source_attempt_ids": ["kabutan-1000-access-failed"],
                }
            ),
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [],
            "source_attempts": [
                {
                    "attempt_id": "kabutan-1000-access-failed",
                    "source_id": "KABUTAN_HISTORY",
                    "status": "ACCESS_FAILED",
                }
            ],
        },
    )

    assert result.valid is True


def test_market_research_rejects_data_unavailable_with_successful_attempt_only():
    payload = complete_candidate_research(market_research_payload())
    payload["overall_status"] = "DATA_UNAVAILABLE"
    payload["candidate_research"][0].update(
        {
            "data_status": "DATA_UNAVAILABLE",
            "status_reasons": ["secondary OHLCV source access failed"],
            "source_policy_status": "ACCESS_FAILED",
            "source_checks": make_standard_source_checks(
                secondary_ohlcv={
                    "status": "ACCESS_FAILED",
                    "source_id": "KABUTAN_HISTORY",
                    "information_type": "OHLCV",
                    "reason_code": "SECONDARY_OHLCV_ACCESS_FAILED",
                    "source_attempt_ids": ["kabutan-1000-found"],
                }
            ),
        }
    )

    result = validate_market_research(
        payload,
        load_source_matrix(),
        {
            "sources": [],
            "source_attempts": [
                {
                    "attempt_id": "kabutan-1000-found",
                    "source_id": "KABUTAN_HISTORY",
                    "status": "FOUND",
                }
            ],
        },
    )

    assert result.valid is False
    assert any("without attempted source_checks" in error for error in result.errors)


def test_2026_08_12_regression_detects_old_data_unavailable_as_incomplete():
    path = Path("regression/2026-08-12-data-unavailable/runs/2026-08-12/market_research.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any(
        "candidate_research is missing Discovery candidate" in error
        or "required property" in error
        for error in result.errors
    )


def test_market_research_rejects_returned_unmerged_subagent_result():
    payload = complete_candidate_research(market_research_payload())
    payload["subagent_batches"] = [
        {
            "batch_id": "stage2-a",
            "agent_name": "market-researcher",
            "lifecycle_status": "RETURNED",
            "candidate_codes": ["1000"],
            "returned_candidate_codes": ["1000"],
            "merged_candidate_codes": [],
        }
    ]

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is False
    assert any("not merged" in error for error in result.errors)


def test_market_research_allows_returned_unmerged_subagent_only_as_pipeline_incomplete():
    payload = complete_candidate_research(market_research_payload())
    payload["overall_status"] = "PIPELINE_INCOMPLETE"
    payload["subagent_batches"] = [
        {
            "batch_id": "stage2-a",
            "agent_name": "market-researcher",
            "lifecycle_status": "RETURNED",
            "candidate_codes": ["1000"],
            "returned_candidate_codes": ["1000"],
            "merged_candidate_codes": [],
        }
    ]

    result = validate_market_research(payload, load_source_matrix())

    assert result.valid is True


def test_market_data_research_alignment_requires_matching_statuses():
    payload = complete_candidate_research(market_research_payload())
    payload["candidate_research"] = [
        make_candidate_research(
            "1234",
            data_status="DATA_UNAVAILABLE",
            status_reasons=["missing secondary source"],
            source_policy_status="SINGLE_SOURCE_ONLY",
        )
    ]

    result = validate_market_records_against_research([make_market_record()], payload)

    assert result.valid is False
    assert any(
        "data_status does not match" in error
        for error in result.errors_by_ticker["1234"]
    )
