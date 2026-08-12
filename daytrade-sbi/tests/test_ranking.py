from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from src import cli
from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, strategy_config_sha256
from src.contracts import RUN_ARTIFACT_ALLOWLIST
from src.ranking import (
    RankingHardError,
    build_ranking,
    canonical_turnover_yen,
    parse_raw_turnover_thousand_yen,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH
from src.strategy import build_order_plan


CONFIG = load_strategy_config()
CONFIG_SHA = strategy_config_sha256(CONFIG)
TARGET_DATE = "2026-08-10"
PREVIOUS_TRADING_DAY = "2026-08-07"
EVENT_GATE_AS_OF = "2026-08-09T21:00:00+09:00"
REQUESTED_AT = "2026-08-09T20:00:00+09:00"

INPUT_HASHES = {
    "event_gate_sha256": "a" * 64,
    "candidates_sha256": "b" * 64,
    "market_data_sha256": "c" * 64,
    "sources_sha256": "d" * 64,
    "source_matrix_sha256": "e" * 64,
    "strategy_snapshot_sha256": "f" * 64,
}


def _order_plan(previous_high: str, tick_size: str) -> dict:
    plan = build_order_plan(previous_high, tick_size, config=CONFIG)
    return {key: str(value) if isinstance(value, Decimal) else value for key, value in plan.as_dict().items()}


def _turnover_attempt(
    *,
    ticker: str,
    raw_value: str,
    status: str = "FOUND",
    canonical_override: str | None = None,
    source_ref_suffix: str = "",
) -> dict:
    attempt = {
        "attempt_id": f"attempt-{ticker}-turnover{source_ref_suffix}",
        "source_id": "YAHOO_JP_QUOTE",
        "source_role": "PRIMARY",
        "criticality": "TRADE_CRITICAL",
        "information_type": "TURNOVER",
        "candidate_code": ticker,
        "target_date": TARGET_DATE,
        "research_cutoff": "2026-08-09T20:00:00+09:00",
        "requested_at": REQUESTED_AT,
        "url": f"https://finance.yahoo.co.jp/quote/{ticker}.T",
        "status": status,
        "values": None,
        "result_count": None,
    }
    if status == "FOUND":
        canonical = canonical_override
        if canonical is None:
            canonical = str(canonical_turnover_yen(raw_value))
        source_ref = f"src-{ticker}-turnover{source_ref_suffix}"
        attempt["values"] = [
            {
                "field_name": "turnover",
                "trading_date": PREVIOUS_TRADING_DAY,
                "raw_value": raw_value,
                "raw_unit": "THOUSAND_YEN",
                "canonical_value_yen": canonical,
                "source_ref": source_ref,
            }
        ]
        attempt["result_count"] = 1
    return attempt


def _source_record(*, ticker: str, canonical_value: str, source_ref_suffix: str = "") -> dict:
    return {
        "source_ref": f"src-{ticker}-turnover{source_ref_suffix}",
        "source_id": "YAHOO_JP_QUOTE",
        "source_role": "PRIMARY",
        "information_type": "TURNOVER",
        "source_status": "FOUND",
        "source_name": "Yahoo! Finance Japan Stock Quote",
        "source_url": f"https://finance.yahoo.co.jp/quote/{ticker}.T",
        "retrieved_at": "2026-08-09T20:00:00+09:00",
        "trading_date": PREVIOUS_TRADING_DAY,
        "ticker": ticker,
        "field_name": "turnover",
        "value": canonical_value,
    }


def _candidate(
    *,
    ticker: str,
    previous_high: str,
    tick_size: str,
    canonical_turnover: str,
    source_ref_suffix: str = "",
) -> dict:
    return {
        "ticker": ticker,
        "status": "ELIGIBLE",
        "screening_status": "PASS",
        "reasons": [],
        "unresolved_screening": [],
        "passed_rules": [],
        "failed_rules": [],
        "unavailable_rules": [],
        "reason_codes": [],
        "source_refs": [],
        "rule_evaluations": [],
        "features": {
            "previous_day_volume": {"value": None, "source_refs": [], "source_urls": [], "estimated": False},
            "required_capital_yen": {"value": None, "source_refs": [], "source_urls": [], "estimated": False},
            "daily_range_yen": {"value": None, "source_refs": [], "source_urls": [], "estimated": False},
            "daily_range_pct": {"value": None, "source_refs": [], "source_urls": [], "estimated": False},
            "previous_day_change_pct": {"value": None, "source_refs": [], "source_urls": [], "estimated": False},
            "estimated_turnover": {"value": None, "source_refs": [], "source_urls": [], "estimated": False},
            "turnover": {
                "value": canonical_turnover,
                "source_refs": [f"src-{ticker}-turnover{source_ref_suffix}"],
                "source_urls": [],
                "estimated": False,
            },
        },
        "order_plan": _order_plan(previous_high, tick_size),
    }


def _market_record(*, ticker: str, previous_high: str, tick_size: str, turnover: str) -> dict:
    return {
        "ticker": ticker,
        "company_name": "Example",
        "market": "TSE Prime",
        "share_unit": 100,
        "data_status": "VERIFIED",
        "trading_date": PREVIOUS_TRADING_DAY,
        "open": "390",
        "high": "400",
        "low": "385",
        "close": "395",
        "volume": "1000000",
        "previous_close": "390",
        "previous_high": previous_high,
        "tick_size": tick_size,
        "turnover": turnover,
        "sources": [],
    }


def _event_gate(tickers: list[str]) -> dict:
    return {
        "target_date": TARGET_DATE,
        "previous_trading_day": PREVIOUS_TRADING_DAY,
        "event_gate_as_of": EVENT_GATE_AS_OF,
        "strategy_version": CONFIG["strategy_version"],
        "config_sha256": CONFIG_SHA,
        "event_gate_complete": True,
        "ranking_ready": True,
        "ranking_block_reasons": [],
        "candidates": [{"ticker": ticker, "gate_status": "PASS"} for ticker in tickers],
    }


def _full_event_gate(tickers: list[str]) -> dict:
    """A schema-conformant event_gate.json payload (for CLI-level tests)."""
    return {
        "schema_version": 2,
        "event_gate_version": "event-gate-v1",
        "target_date": TARGET_DATE,
        "previous_trading_day": PREVIOUS_TRADING_DAY,
        "event_window_start": f"{PREVIOUS_TRADING_DAY}T00:00:00+09:00",
        "event_gate_as_of": EVENT_GATE_AS_OF,
        "generated_at": "2026-08-09T21:00:00+00:00",
        "strategy_version": CONFIG["strategy_version"],
        "config_sha256": CONFIG_SHA,
        "input_hashes": {
            "event_research_sha256": "0" * 64,
            "candidate_pipeline_sha256": "0" * 64,
            "candidates_sha256": "0" * 64,
            "sources_sha256": "0" * 64,
            "strategy_snapshot_sha256": "0" * 64,
        },
        "upstream_candidate_tickers": sorted(tickers),
        "event_gate_input_tickers": sorted(tickers),
        "event_gate_complete": True,
        "ranking_ready": True,
        "ranking_block_reasons": [],
        "summary": {
            "input_count": len(tickers),
            "pass_count": len(tickers),
            "reject_count": 0,
            "data_unavailable_count": 0,
            "rule_counts": [],
        },
        "candidates": [
            {"ticker": ticker, "gate_status": "PASS", "reason_codes": [], "rule_evaluations": []}
            for ticker in tickers
        ],
    }


def _build_case(
    specs: list[dict],
) -> tuple[dict, dict, dict, dict]:
    """specs: list of dicts with keys ticker, previous_high, tick_size, raw_value."""
    candidates_list = []
    market_records = []
    attempts = []
    source_records = []
    tickers = []
    for spec in specs:
        ticker = spec["ticker"]
        tickers.append(ticker)
        canonical = str(canonical_turnover_yen(spec["raw_value"]))
        candidates_list.append(
            _candidate(
                ticker=ticker,
                previous_high=spec["previous_high"],
                tick_size=spec["tick_size"],
                canonical_turnover=canonical,
            )
        )
        market_records.append(
            _market_record(
                ticker=ticker,
                previous_high=spec["previous_high"],
                tick_size=spec["tick_size"],
                turnover=canonical,
            )
        )
        attempts.append(_turnover_attempt(ticker=ticker, raw_value=spec["raw_value"]))
        source_records.append(_source_record(ticker=ticker, canonical_value=canonical))

    event_gate = _event_gate(tickers)
    candidates = {"candidates": candidates_list}
    market_data = {"records": market_records}
    source_payload = {"sources": source_records, "source_attempts": attempts}
    return event_gate, candidates, market_data, source_payload


def _sha256_of_json(payload: dict) -> str:
    """Matches cli._write_json_file / cli._sha256_bytes serialization exactly,
    so tests exercise the real hash-chain check rather than bypassing it."""
    import hashlib

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_full_case(specs: list[dict]) -> tuple[dict, dict, dict, dict]:
    event_gate, candidates, market_data, source_payload = _build_case(specs)
    tickers = [spec["ticker"] for spec in specs]
    full_event_gate = _full_event_gate(tickers)
    candidates_full = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "generated_at": "2026-08-09T21:00:00+00:00",
        "strategy_version": CONFIG["strategy_version"],
        "config_sha256": CONFIG_SHA,
        "candidates": candidates["candidates"],
    }
    market_data_full = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "records": market_data["records"],
    }
    sources_full = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "sources": source_payload["sources"],
        "source_attempts": source_payload["source_attempts"],
    }
    # The hash-chain check requires event_gate.input_hashes to be the real
    # hashes of the candidates.json/sources.json/strategy-snapshot files that
    # will actually be fed into build-ranking (same bytes Event Gate used).
    config_path_text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    strategy_snapshot_sha256 = __import__("hashlib").sha256(
        config_path_text.encode("utf-8")
    ).hexdigest()
    full_event_gate["input_hashes"]["candidates_sha256"] = _sha256_of_json(candidates_full)
    full_event_gate["input_hashes"]["sources_sha256"] = _sha256_of_json(sources_full)
    full_event_gate["input_hashes"]["strategy_snapshot_sha256"] = strategy_snapshot_sha256
    return full_event_gate, candidates_full, market_data_full, sources_full


def _run(event_gate, candidates, market_data, source_payload):
    return build_ranking(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=CONFIG,
        input_hashes=INPUT_HASHES,
    )


# --- Raw value parsing --------------------------------------------------


def test_parse_raw_turnover_valid_examples():
    assert parse_raw_turnover_thousand_yen("0") == Decimal(0)
    assert parse_raw_turnover_thousand_yen("123") == Decimal(123)
    assert parse_raw_turnover_thousand_yen("12,345") == Decimal(12345)
    assert parse_raw_turnover_thousand_yen("1,234,567") == Decimal(1234567)
    assert canonical_turnover_yen("12,345") == Decimal(12345000)


@pytest.mark.parametrize(
    "raw_value",
    ["1234", "12,3456", "12,34", "abc", "1,234,56", "-1", "", "12,345 "],
)
def test_parse_raw_turnover_rejects_malformed(raw_value):
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_RAW_VALUE_MALFORMED"):
        parse_raw_turnover_thousand_yen(raw_value)


# --- Core ranking behaviour ----------------------------------------------


def test_multiple_candidates_ranked_by_turnover_and_relative_tick():
    event_gate, candidates, market_data, source_payload = _build_case(
        [
            {"ticker": "1001", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "1002", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ]
    )
    result = _run(event_gate, candidates, market_data, source_payload)
    assert result["ranking_status"] == "COMPLETE"
    assert result["ranking_complete"] is True
    assert result["ranked_count"] == 2
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    assert by_ticker["1001"]["final_rank"] == 1
    assert by_ticker["1002"]["final_rank"] == 2
    assert by_ticker["1001"]["feature_ranks"]["turnover_rank"] == 1
    assert by_ticker["1002"]["feature_ranks"]["turnover_rank"] == 2


def test_permutation_invariance():
    specs = [
        {"ticker": "2001", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
        {"ticker": "2002", "previous_high": "500", "tick_size": "1", "raw_value": "10,000"},
        {"ticker": "2003", "previous_high": "300", "tick_size": "1", "raw_value": "30,000"},
    ]
    event_gate, candidates, market_data, source_payload = _build_case(specs)
    result_a = _run(event_gate, candidates, market_data, source_payload)

    reversed_specs = list(reversed(specs))
    event_gate2, candidates2, market_data2, source_payload2 = _build_case(reversed_specs)
    result_b = _run(event_gate2, candidates2, market_data2, source_payload2)

    assert result_a["candidates"] == result_b["candidates"]


def test_single_candidate_still_ranked_but_never_trade():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "3001", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    result = _run(event_gate, candidates, market_data, source_payload)
    candidate = result["candidates"][0]
    assert candidate["rank_points"] == 2
    assert candidate["final_rank"] == 1
    # Ranking result never contains a trade/selection decision field.
    assert "decision" not in result
    assert "trade" not in candidate


def test_relative_tick_exact_tie_via_cross_multiplication():
    # 1/100 == 2/200 as ratios -> same relative_tick_size rank.
    specs = [
        {"ticker": "4001", "previous_high": "99", "tick_size": "1", "raw_value": "10,000"},
        {"ticker": "4002", "previous_high": "198", "tick_size": "2", "raw_value": "20,000"},
    ]
    event_gate, candidates, market_data, source_payload = _build_case(specs)
    result = _run(event_gate, candidates, market_data, source_payload)
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    assert (
        by_ticker["4001"]["feature_ranks"]["relative_tick_size_rank"]
        == by_ticker["4002"]["feature_ranks"]["relative_tick_size_rank"]
    )


def test_competition_ranking_is_1_1_3_not_1_1_2():
    # Two candidates tie for turnover (rank 1, rank 1), the third is strictly
    # lower and must receive rank 3 -- not rank 2 (standard/"dense" ranking).
    specs = [
        {"ticker": "6001", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
        {"ticker": "6002", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
        {"ticker": "6003", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
    ]
    event_gate, candidates, market_data, source_payload = _build_case(specs)
    result = _run(event_gate, candidates, market_data, source_payload)
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    turnover_ranks = sorted(
        by_ticker[t]["feature_ranks"]["turnover_rank"] for t in ("6001", "6002", "6003")
    )
    assert turnover_ranks == [1, 1, 3]

    # Same shape for relative_tick_size: two exact ratio ties then a
    # strictly larger third -- must be [1, 1, 3], not [1, 1, 2].
    tick_specs = [
        {"ticker": "6101", "previous_high": "99", "tick_size": "1", "raw_value": "10,000"},
        {"ticker": "6102", "previous_high": "198", "tick_size": "2", "raw_value": "20,000"},
        {"ticker": "6103", "previous_high": "50", "tick_size": "1", "raw_value": "30,000"},
    ]
    event_gate2, candidates2, market_data2, source_payload2 = _build_case(tick_specs)
    result2 = _run(event_gate2, candidates2, market_data2, source_payload2)
    by_ticker2 = {c["ticker"]: c for c in result2["candidates"]}
    tick_ranks = sorted(
        by_ticker2[t]["feature_ranks"]["relative_tick_size_rank"]
        for t in ("6101", "6102", "6103")
    )
    assert tick_ranks == [1, 1, 3]


def test_forbidden_fields_never_present():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "5001", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    result = _run(event_gate, candidates, market_data, source_payload)
    payload_str = str(result)
    for forbidden in (
        "'score'",
        "'weight'",
        "'confidence'",
        "'expected_return'",
        "'momentum'",
        "'atr'",
        "'volume_ratio'",
        "'estimated_turnover'",
    ):
        assert forbidden not in payload_str


# --- Fail-closed DATA_UNAVAILABLE -----------------------------------------


@pytest.mark.parametrize(
    "status",
    ["NOT_FOUND", "NOT_YET_AVAILABLE", "ACCESS_FAILED", "PARSE_FAILED", "STALE", "CONFLICT"],
)
def test_business_failure_statuses_make_whole_run_data_unavailable(status):
    event_gate, candidates, market_data, source_payload = _build_case(
        [
            {"ticker": "6001", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "6002", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ]
    )
    # Break the second candidate's turnover attempt.
    for attempt in source_payload["source_attempts"]:
        if attempt["candidate_code"] == "6002":
            attempt["status"] = status
            attempt["values"] = None
            attempt["result_count"] = None

    result = _run(event_gate, candidates, market_data, source_payload)
    assert result["ranking_status"] == "DATA_UNAVAILABLE"
    assert result["ranking_complete"] is False
    for candidate in result["candidates"]:
        assert candidate["final_rank"] is None
        assert candidate["rank_points"] is None
        assert candidate["feature_ranks"] is None
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    assert by_ticker["6002"]["input_status"] == "DATA_UNAVAILABLE"
    # The other, valid candidate's known feature value is still persisted.
    assert by_ticker["6001"]["feature_values"]["turnover"] == "50000000"


# --- Hard errors: workflow-incomplete / invalid statuses ------------------


@pytest.mark.parametrize("status", ["NOT_STARTED", "DEPENDENCY_NOT_READY", "EXECUTION_FAILED"])
def test_workflow_incomplete_status_is_hard_error(status):
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7001", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["status"] = status
    source_payload["source_attempts"][0]["values"] = None
    source_payload["source_attempts"][0]["result_count"] = None
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_SOURCE_WORKFLOW_INCOMPLETE"):
        _run(event_gate, candidates, market_data, source_payload)


@pytest.mark.parametrize("status", ["SOURCE_POLICY_UNDEFINED", "NOT_REQUIRED", "SINGLE_SOURCE_ONLY"])
def test_invalid_status_is_hard_error(status):
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7002", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["status"] = status
    source_payload["source_attempts"][0]["values"] = None
    source_payload["source_attempts"][0]["result_count"] = None
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_SOURCE_INVALID_STATUS"):
        _run(event_gate, candidates, market_data, source_payload)


def test_missing_canonical_attempt_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7003", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"] = []
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_ATTEMPT_MISSING"):
        _run(event_gate, candidates, market_data, source_payload)


# --- Hard errors: four-way consistency ------------------------------------


def test_canonical_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8001", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["values"][0]["canonical_value_yen"] = "9999999"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_CANONICAL_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_source_record_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8002", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["sources"][0]["value"] = "1"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_SOURCE_RECORD_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_market_data_turnover_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8003", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["turnover"] = "1"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_MARKET_DATA_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_candidate_turnover_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8004", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["candidates"][0]["features"]["turnover"]["value"] = "1"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_CANDIDATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_candidate_turnover_estimated_true_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8005", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["candidates"][0]["features"]["turnover"]["estimated"] = True
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_CANDIDATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


# --- Hard errors: order plan / tick size / eligibility ---------------------


def test_order_plan_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9001", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["candidates"][0]["order_plan"]["entry_trigger"] = "999"
    with pytest.raises(RankingHardError, match="RANKING_ORDER_PLAN_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_tick_size_null_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9002", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["tick_size"] = None
    with pytest.raises(RankingHardError, match="RANKING_TICK_SIZE_INVALID"):
        _run(event_gate, candidates, market_data, source_payload)


def test_candidate_not_eligible_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9003", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["candidates"][0]["order_plan"] = None
    with pytest.raises(RankingHardError, match="RANKING_CANDIDATE_NOT_ELIGIBLE"):
        _run(event_gate, candidates, market_data, source_payload)


def test_event_gate_not_ready_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9004", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    event_gate["ranking_ready"] = False
    with pytest.raises(RankingHardError, match="RANKING_EVENT_GATE_NOT_READY"):
        _run(event_gate, candidates, market_data, source_payload)


def test_reject_candidate_is_excluded_from_ranking_universe():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9101", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # A REJECT-status ticker in event_gate.candidates that has no
    # corresponding candidates/market_data entry must simply be ignored,
    # even if it would otherwise dominate turnover.
    event_gate["candidates"].append({"ticker": "9999", "gate_status": "REJECT"})
    result = _run(event_gate, candidates, market_data, source_payload)
    tickers = {c["ticker"] for c in result["candidates"]}
    assert tickers == {"9101"}


# --- CLI: build-ranking ----------------------------------------------------


def _write_json_file(path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_ranking_inputs(tmp_path, event_gate, candidates, market_data, sources):
    """Writes the four build-ranking input files, re-syncing event_gate's
    recorded input_hashes to the final (post-mutation) candidates.json /
    sources.json bytes first, so tests that intentionally corrupt a field
    after `_build_full_case` still exercise the real hash-chain check
    instead of tripping over it for an unrelated reason."""
    event_gate = copy.deepcopy(event_gate)
    event_gate["input_hashes"]["candidates_sha256"] = _sha256_of_json(candidates)
    event_gate["input_hashes"]["sources_sha256"] = _sha256_of_json(sources)

    event_gate_path = tmp_path / "event_gate.json"
    candidates_path = tmp_path / "candidates.json"
    market_data_path = tmp_path / "market_data.json"
    sources_path = tmp_path / "sources.json"
    _write_json_file(event_gate_path, event_gate)
    _write_json_file(candidates_path, candidates)
    _write_json_file(market_data_path, market_data)
    _write_json_file(sources_path, sources)
    return event_gate_path, candidates_path, market_data_path, sources_path


def test_cli_build_ranking_complete(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [
            {"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "AA02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ]
    )
    event_gate_path = tmp_path / "event_gate.json"
    candidates_path = tmp_path / "candidates.json"
    market_data_path = tmp_path / "market_data.json"
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "ranking.json"
    _write_json_file(event_gate_path, event_gate)
    _write_json_file(candidates_path, candidates)
    _write_json_file(market_data_path, market_data)
    _write_json_file(sources_path, sources)

    result = cli.main(
        [
            "build-ranking",
            "--event-gate",
            str(event_gate_path),
            "--candidates",
            str(candidates_path),
            "--market-data",
            str(market_data_path),
            "--sources",
            str(sources_path),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ranking_status"] == "COMPLETE"
    assert payload["ranking_complete"] is True
    ranked = {c["ticker"]: c["final_rank"] for c in payload["candidates"]}
    assert ranked["AA01"] == 1
    assert ranked["AA02"] == 2


def test_cli_build_ranking_data_unavailable(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [
            {"ticker": "BB01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
        ]
    )
    sources["source_attempts"][0]["status"] = "ACCESS_FAILED"
    sources["source_attempts"][0]["values"] = None
    sources["source_attempts"][0]["result_count"] = None

    output_path = tmp_path / "ranking.json"
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )

    result = cli.main(
        [
            "build-ranking",
            "--event-gate",
            str(event_gate_path),
            "--candidates",
            str(candidates_path),
            "--market-data",
            str(market_data_path),
            "--sources",
            str(sources_path),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ranking_status"] == "DATA_UNAVAILABLE"
    assert payload["ranking_complete"] is False


def test_cli_build_ranking_invalid_input_no_output_written(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "CC01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # Corrupt the candidate order_plan so build_ranking raises a hard error.
    candidates["candidates"][0]["order_plan"]["entry_trigger"] = "999"

    output_path = tmp_path / "ranking.json"
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )

    with pytest.raises(RankingHardError, match="RANKING_ORDER_PLAN_MISMATCH"):
        cli.main(
            [
                "build-ranking",
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_ranking_never_overwrites_existing_output_on_hard_error(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "DD01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # Corrupt the candidate order_plan up front so the written candidates.json
    # is self-consistent with event_gate's recorded hash (the hash-chain
    # check must not be what trips this test; the order-plan recompute check
    # inside build_ranking must be what catches the corruption).
    candidates["candidates"][0]["order_plan"]["entry_trigger"] = "999"

    output_path = tmp_path / "ranking.json"
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )
    output_path.write_text('{"pre_existing": true}', encoding="utf-8")

    with pytest.raises(RankingHardError):
        cli.main(
            [
                "build-ranking",
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(DEFAULT_CONFIG_PATH),
                "--output",
                str(output_path),
            ]
        )
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"pre_existing": True}


def test_ranking_json_in_run_artifact_allowlist():
    assert "ranking.json" in RUN_ARTIFACT_ALLOWLIST
