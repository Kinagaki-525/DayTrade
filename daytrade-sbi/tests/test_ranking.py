from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from src import cli
from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, strategy_config_sha256
from src.contracts import (
    RUN_ARTIFACT_ALLOWLIST,
    validate_ranking_output_contract,
    validate_ranking_preconditions,
)
from src.ranking import (
    RankingHardError,
    build_ranking,
    canonical_turnover_yen,
    parse_raw_turnover_thousand_yen,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH, load_source_matrix
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
        "coverage_status": None,
        "covered_dates": None,
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
        attempt["coverage_status"] = "COMPLETE"
        attempt["covered_dates"] = [PREVIOUS_TRADING_DAY]
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
                "source_refs": [f"src-{ticker}-turnover{source_ref_suffix}"] if canonical_turnover is not None else [],
                "source_urls": [],
                "estimated": False,
            },
        },
        "order_plan": _order_plan(previous_high, tick_size),
    }


def _tick_size_provenance(ticker: str, tick_size: str) -> tuple[list[dict], dict]:
    """Minimal tick_size sources + field_provenance sufficient for
    ranking.py's own tick_size provenance contract check (not the full
    validate_market_data() contract, which is only exercised at the CLI
    layer -- see _full_market_sources for that)."""
    tick_source = {
        "source_ref": f"tick-{ticker}",
        "source_id": "JPX_TICK_SIZE",
        "source_role": "PRIMARY",
        "information_type": "TICK_SIZE",
        "source_status": "FOUND",
        "source_name": "JPX Tick Size",
        "source_url": "https://www.jpx.co.jp/equities/trading/domestic/07.html",
        "retrieved_at": "2026-08-09T20:00:00+09:00",
        "trading_date": PREVIOUS_TRADING_DAY,
        "ticker": ticker,
        "field_name": "tick_size",
        "value": tick_size,
    }
    provenance = {
        "field_name": "tick_size",
        "status": "VERIFIED",
        "verified_value": tick_size,
        "verified_at": "2026-08-09T20:01:00+09:00",
        "source_refs": [tick_source["source_ref"]],
    }
    return [tick_source], provenance


def _market_record(*, ticker: str, previous_high: str, tick_size: str, turnover: str | None) -> dict:
    sources, tick_provenance = _tick_size_provenance(ticker, tick_size)
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
        "sources": sources,
        "field_provenance": [tick_provenance],
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


_EVENT_RULE_IDS = (
    "earnings_on_target_date",
    "earnings_on_previous_trading_day",
    "tdnet_disclosure_in_event_window",
    "dangerous_news_with_primary_confirmation",
)


def _pass_rule_evaluations() -> list[dict]:
    """Four PASS rule_evaluations matching event_gate.py's EVENT_RULE_IDS
    exactly, so gate_status/summary/ranking_ready recompute consistently
    under the CRITICAL Event Gate re-verification in contracts.py."""
    return [
        {
            "rule_id": rule_id,
            "result": "PASS",
            "reason_codes": [],
            "source_ids": [],
            "source_attempt_ids": [],
            "evidence_ids": [],
        }
        for rule_id in _EVENT_RULE_IDS
    ]


def _event_gate_summary(*, pass_count: int, reject_count: int = 0, data_unavailable_count: int = 0) -> dict:
    input_count = pass_count + reject_count + data_unavailable_count
    return {
        "input_count": input_count,
        "pass_count": pass_count,
        "reject_count": reject_count,
        "data_unavailable_count": data_unavailable_count,
        "rule_counts": [
            {
                "rule_id": rule_id,
                "pass_count": input_count,
                "reject_count": 0,
                "data_unavailable_count": 0,
            }
            for rule_id in _EVENT_RULE_IDS
        ],
    }


def _full_event_gate(tickers: list[str]) -> dict:
    """A schema-conformant, internally-consistent event_gate.json payload
    (for CLI-level tests) -- every PASS candidate carries real PASS
    rule_evaluations so the CRITICAL Event Gate re-verification in
    contracts.py recomputes the same gate_status/summary/ranking_ready."""
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
        "summary": _event_gate_summary(pass_count=len(tickers)),
        "candidates": [
            {
                "ticker": ticker,
                "gate_status": "PASS",
                "reason_codes": [],
                "rule_evaluations": _pass_rule_evaluations(),
            }
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
    candidates = {"target_date": TARGET_DATE, "candidates": candidates_list}
    market_data = {"target_date": TARGET_DATE, "records": market_records}
    source_payload = {"sources": source_records, "source_attempts": attempts}
    return event_gate, candidates, market_data, source_payload


def _sha256_of_json(payload: dict) -> str:
    """Matches cli._write_json_file / cli._sha256_bytes serialization exactly,
    so tests exercise the real hash-chain check rather than bypassing it."""
    import hashlib

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_OHLCV_VALUES = {
    "open": "390",
    "high": "400",
    "low": "385",
    "close": "395",
    "volume": "1000000",
}


def _full_source(
    *,
    ticker: str,
    field_name: str,
    value: str,
    source_id: str,
    source_role: str,
    information_type: str,
    url: str,
    ref_suffix: str = "",
) -> dict:
    return {
        "source_ref": f"{source_id}-{ticker}-{field_name}{ref_suffix}",
        "source_id": source_id,
        "source_role": source_role,
        "information_type": information_type,
        "source_status": "FOUND",
        "source_name": source_id.replace("_", " ").title(),
        "source_url": url,
        "retrieved_at": "2026-08-09T20:00:00+09:00",
        "trading_date": PREVIOUS_TRADING_DAY,
        "ticker": ticker,
        "field_name": field_name,
        "value": value,
    }


def _full_market_sources(
    *, ticker: str, previous_high: str, tick_size: str, turnover_canonical: str
) -> tuple[list[dict], dict]:
    """Build a market_data.json 'sources' array + tick_size field_provenance
    that satisfies the full validate_market_data()/validate_source_ledger()
    contract exercised at the CLI layer by validate_ranking_preconditions
    (CRITICAL-1)."""
    sources: list[dict] = [
        _full_source(
            ticker=ticker,
            field_name="company_name",
            value="Example",
            source_id="JPX_LISTED_COMPANY",
            source_role="PRIMARY",
            information_type="LISTED_COMPANY",
            url="https://www.jpx.co.jp/listing/co-search/",
        ),
        _full_source(
            ticker=ticker,
            field_name="market",
            value="TSE Prime",
            source_id="JPX_LISTED_COMPANY",
            source_role="PRIMARY",
            information_type="LISTED_COMPANY",
            url="https://www.jpx.co.jp/listing/co-search/",
        ),
        _full_source(
            ticker=ticker,
            field_name="share_unit",
            value="100",
            source_id="JPX_TRADING_UNIT",
            source_role="PRIMARY",
            information_type="TRADING_UNIT",
            url="https://www.jpx.co.jp/equities/trading/domestic/03.html",
        ),
    ]
    values = dict(_OHLCV_VALUES)
    values["previous_close"] = "390"
    values["previous_high"] = previous_high
    for field_name, value in values.items():
        sources.append(
            _full_source(
                ticker=ticker,
                field_name=field_name,
                value=value,
                source_id="YAHOO_JP_HISTORY",
                source_role="PRIMARY",
                information_type="OHLCV",
                url=f"https://finance.yahoo.co.jp/quote/{ticker}.T/history",
            )
        )
        sources.append(
            _full_source(
                ticker=ticker,
                field_name=field_name,
                value=value,
                source_id="KABUTAN_HISTORY",
                source_role="SECONDARY",
                information_type="OHLCV",
                url=f"https://kabutan.jp/stock/kabuka?code={ticker}",
            )
        )
    tick_source = _full_source(
        ticker=ticker,
        field_name="tick_size",
        value=tick_size,
        source_id="JPX_TICK_SIZE",
        source_role="PRIMARY",
        information_type="TICK_SIZE",
        url="https://www.jpx.co.jp/equities/trading/domestic/07.html",
    )
    sources.append(tick_source)
    topix_source = _full_source(
        ticker=ticker,
        field_name="topix500_membership",
        value="true",
        source_id="JPX_TOPIX500",
        source_role="PRIMARY",
        information_type="TOPIX500_MEMBERSHIP",
        url="https://www.jpx.co.jp/markets/indices/topix/",
    )
    sources.append(topix_source)
    price_source = next(s for s in sources if s["field_name"] == "previous_high")
    # Reuse _source_record exactly (byte-for-byte) so this turnover source
    # canonically matches the sources.json ledger entry produced by
    # _build_case's _source_record for the same ticker/value.
    turnover_source = _source_record(ticker=ticker, canonical_value=turnover_canonical)
    sources.append(turnover_source)
    tick_provenance = {
        "field_name": "tick_size",
        "status": "VERIFIED",
        "verified_value": tick_size,
        "verified_at": "2026-08-09T20:01:00+09:00",
        "primary_source_ref": tick_source["source_ref"],
        "secondary_source_ref": topix_source["source_ref"],
        "source_refs": [
            tick_source["source_ref"],
            topix_source["source_ref"],
            price_source["source_ref"],
        ],
    }
    return sources, tick_provenance


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

    # Replace each market record's minimal tick_size-only sources with the
    # full CRITICAL-1 source ledger contract (OHLCV primary+secondary,
    # tick_size/topix500 provenance, and a matching turnover source), and
    # extend sources.json with the same records so validate_source_ledger()
    # finds every market_data.json source in the ledger.
    full_market_records = []
    full_ledger_sources = list(source_payload["sources"])
    for record, spec in zip(market_data["records"], specs):
        ticker = spec["ticker"]
        canonical = str(canonical_turnover_yen(spec["raw_value"]))
        full_sources, tick_provenance = _full_market_sources(
            ticker=ticker,
            previous_high=spec["previous_high"],
            tick_size=spec["tick_size"],
            turnover_canonical=canonical,
        )
        new_record = dict(record)
        new_record["sources"] = full_sources
        new_record["field_provenance"] = [tick_provenance]
        full_market_records.append(new_record)
        full_ledger_sources.extend(
            source for source in full_sources if source["field_name"] != "turnover"
        )

    market_data_full = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "records": full_market_records,
    }
    sources_full = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "sources": full_ledger_sources,
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


def _null_turnover_residuals(candidates: dict, market_data: dict, ticker: str) -> None:
    """Helper for failure-status fixtures: null out the stale turnover
    residuals that CRITICAL-2 forbids from surviving alongside a
    failure-status canonical attempt."""
    for record in market_data["records"]:
        if record["ticker"] == ticker:
            record["turnover"] = None
    for candidate in candidates["candidates"]:
        if candidate["ticker"] == ticker:
            candidate["features"]["turnover"]["value"] = None
            candidate["features"]["turnover"]["source_refs"] = []


# --- Raw value parsing --------------------------------------------------


def test_parse_raw_turnover_valid_examples():
    assert parse_raw_turnover_thousand_yen("0") == Decimal(0)
    assert parse_raw_turnover_thousand_yen("123") == Decimal(123)
    assert parse_raw_turnover_thousand_yen("1234") == Decimal(1234)
    assert parse_raw_turnover_thousand_yen("123456789") == Decimal(123456789)
    assert parse_raw_turnover_thousand_yen("1,234") == Decimal(1234)
    assert parse_raw_turnover_thousand_yen("12,345") == Decimal(12345)
    assert parse_raw_turnover_thousand_yen("1,234,567") == Decimal(1234567)
    assert parse_raw_turnover_thousand_yen("121,401,288") == Decimal(121401288)
    assert canonical_turnover_yen("12,345") == Decimal(12345000)


@pytest.mark.parametrize(
    "raw_value",
    ["1,23", "1,,000", "1,000,", "100千円", "-1", "1.5", "", "12,3456", "12,34", "abc", "1,234,56", "12,345 "],
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
    assert result["summary"]["ranked_count"] == 2
    assert result["summary"]["input_count"] == 2
    assert result["summary"]["valid_input_count"] == 2
    assert result["summary"]["data_unavailable_count"] == 0
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    assert result["summary"]["top_ranked_ticker"] == "1001"
    assert by_ticker["1001"]["final_rank"] == 1
    assert by_ticker["1002"]["final_rank"] == 2
    assert by_ticker["1001"]["feature_ranks"]["turnover_rank"] == 1
    assert by_ticker["1002"]["feature_ranks"]["turnover_rank"] == 2
    assert result["previous_trading_day"] == PREVIOUS_TRADING_DAY
    assert result["event_gate_as_of"] == EVENT_GATE_AS_OF
    assert result["input_candidate_tickers"] == ["1001", "1002"]


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
        "'OK'",
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
            attempt["coverage_status"] = None
            attempt["covered_dates"] = None
    # CRITICAL-2: a failure-status canonical attempt must not coexist with a
    # stale turnover value left behind in market_data.json/candidates.json.
    _null_turnover_residuals(candidates, market_data, "6002")

    result = _run(event_gate, candidates, market_data, source_payload)
    assert result["ranking_status"] == "DATA_UNAVAILABLE"
    assert result["ranking_complete"] is False
    assert result["summary"]["ranked_count"] == 0
    assert result["summary"]["top_ranked_ticker"] is None
    for candidate in result["candidates"]:
        assert candidate["final_rank"] is None
        assert candidate["rank_points"] is None
        assert candidate["feature_ranks"] is None
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    assert by_ticker["6002"]["input_status"] == "DATA_UNAVAILABLE"
    # The other, valid candidate's known feature value is still persisted.
    assert by_ticker["6001"]["feature_values"]["turnover_yen"] == "50000000"


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


def test_turnover_attempt_wrong_role_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7004", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["source_role"] = "SECONDARY"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION"):
        _run(event_gate, candidates, market_data, source_payload)


def test_turnover_attempt_wrong_criticality_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7005", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["criticality"] = "CONTEXT"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION"):
        _run(event_gate, candidates, market_data, source_payload)


def test_turnover_attempt_wrong_information_type_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7006", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["information_type"] = "OHLCV"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION"):
        _run(event_gate, candidates, market_data, source_payload)


def test_turnover_source_record_wrong_role_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7007", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["sources"][0]["source_role"] = "SECONDARY"
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_SOURCE_RECORD_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_missing_tick_size_provenance_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7008", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["field_provenance"] = []
    with pytest.raises(RankingHardError, match="RANKING_TICK_SIZE_PROVENANCE_MISSING"):
        _run(event_gate, candidates, market_data, source_payload)


def test_invalid_tick_size_provenance_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "7009", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["field_provenance"][0]["status"] = "CONFLICT"
    with pytest.raises(RankingHardError, match="RANKING_TICK_SIZE_PROVENANCE_INVALID"):
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


# --- Hard errors: CRITICAL-2 residual-value contradiction ------------------


@pytest.mark.parametrize(
    "status",
    ["NOT_FOUND", "NOT_YET_AVAILABLE", "ACCESS_FAILED", "PARSE_FAILED", "STALE", "CONFLICT"],
)
def test_failure_attempt_with_stale_market_data_turnover_is_hard_error(status):
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8101", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    for attempt in source_payload["source_attempts"]:
        attempt["status"] = status
        attempt["values"] = None
        attempt["result_count"] = None
        attempt["coverage_status"] = None
        attempt["covered_dates"] = None
    # market_data.turnover is deliberately left stale (not nulled).
    candidates["candidates"][0]["features"]["turnover"]["value"] = None
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_RESIDUAL_VALUE_CONTRADICTION"):
        _run(event_gate, candidates, market_data, source_payload)


@pytest.mark.parametrize(
    "status",
    ["NOT_FOUND", "NOT_YET_AVAILABLE", "ACCESS_FAILED", "PARSE_FAILED", "STALE", "CONFLICT"],
)
def test_failure_attempt_with_stale_candidate_turnover_is_hard_error(status):
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "8102", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    for attempt in source_payload["source_attempts"]:
        attempt["status"] = status
        attempt["values"] = None
        attempt["result_count"] = None
        attempt["coverage_status"] = None
        attempt["covered_dates"] = None
    # market_data.turnover is nulled but candidates.json turnover feature is
    # deliberately left stale.
    market_data["records"][0]["turnover"] = None
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_RESIDUAL_VALUE_CONTRADICTION"):
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


# --- Hard errors: HIGH-1 date integrity ------------------------------------


def test_candidates_target_date_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9201", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["target_date"] = "2026-08-11"
    with pytest.raises(RankingHardError, match="RANKING_TARGET_DATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_market_data_target_date_mismatch_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9202", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["target_date"] = "2026-08-11"
    with pytest.raises(RankingHardError, match="RANKING_TARGET_DATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_stale_market_record_trading_date_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9203", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["trading_date"] = "2026-08-06"
    with pytest.raises(RankingHardError, match="RANKING_TRADING_DATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


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
    assert payload["summary"]["top_ranked_ticker"] == "AA01"


def test_cli_build_ranking_data_unavailable(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [
            {"ticker": "BB01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
        ]
    )
    sources["source_attempts"][0]["status"] = "ACCESS_FAILED"
    sources["source_attempts"][0]["values"] = None
    sources["source_attempts"][0]["result_count"] = None
    sources["source_attempts"][0]["coverage_status"] = None
    sources["source_attempts"][0]["covered_dates"] = None
    for record in market_data["records"]:
        record["turnover"] = None
    for candidate in candidates["candidates"]:
        candidate["features"]["turnover"]["value"] = None
        candidate["features"]["turnover"]["source_refs"] = []

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


def test_cli_build_ranking_invalid_source_ledger_no_output_written(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "EE01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # Remove a market_data.json source from sources.json so
    # validate_source_ledger() fails (CRITICAL-1).
    turnover_ref = f"src-EE01-turnover"
    sources["sources"] = [s for s in sources["sources"] if s["source_ref"] != turnover_ref]

    output_path = tmp_path / "ranking.json"
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )

    with pytest.raises(ValueError, match="RANKING_SOURCE_LEDGER_INVALID"):
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


def test_cli_build_ranking_empty_market_data_sources_is_invalid(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "FF01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # A PASS candidate's market_data.json record with an empty sources array
    # must be rejected -- CRITICAL-1 fixture bug: this must not be treated
    # as a valid fixture for a PASS candidate.
    market_data["records"][0]["sources"] = []

    output_path = tmp_path / "ranking.json"
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )

    with pytest.raises(ValueError, match="RANKING_MARKET_DATA_INVALID"):
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


def test_ranking_json_in_run_artifact_allowlist():
    assert "ranking.json" in RUN_ARTIFACT_ALLOWLIST


# --- Output contract: schema and recomputation checks ----------------------


def _build_output_contract_case(specs):
    event_gate, candidates, market_data, source_payload = _build_case(specs)
    ranking = build_ranking(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=CONFIG,
        input_hashes=INPUT_HASHES,
    )
    return event_gate, candidates, market_data, source_payload, ranking


def test_ranking_schema_missing_required_field_is_rejected():
    from src.contracts import validate_json_document

    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "GG01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    del ranking["summary"]
    with pytest.raises(ValueError, match="ranking.schema.json validation failed"):
        validate_json_document(ranking, "ranking.schema.json")


def test_ranking_schema_rejects_input_status_ok():
    from src.contracts import validate_json_document

    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "GG02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    ranking["candidates"][0]["input_status"] = "OK"
    with pytest.raises(ValueError, match="ranking.schema.json validation failed"):
        validate_json_document(ranking, "ranking.schema.json")


def test_output_contract_summary_recomputation_mismatch_is_rejected():
    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "GG03", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    ranking["summary"]["valid_input_count"] = 999
    with pytest.raises(ValueError, match="ranking.summary does not match recomputed"):
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


def test_output_contract_top_ranked_ticker_mismatch_is_rejected():
    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [
            {"ticker": "GG04", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "GG05", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ]
    )
    ranking["summary"]["top_ranked_ticker"] = "GG05"
    with pytest.raises(ValueError, match="ranking.summary does not match recomputed"):
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


def test_output_contract_input_candidate_tickers_mismatch_is_rejected():
    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "GG06", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    ranking["input_candidate_tickers"] = ["GG06", "GG07"]
    with pytest.raises(ValueError, match="input_candidate_tickers"):
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


# --- CRITICAL: Event Gate internal-consistency re-verification -------------


def _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources):
    output_path = tmp_path / "ranking.json"
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )
    return cli.main(
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
    ), output_path


def test_event_gate_reverify_rejects_false_ranking_ready_with_data_unavailable(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "HH01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # A second candidate is DATA_UNAVAILABLE, but the top-level flags falsely
    # claim ranking_ready=true with no block reasons.
    du_rules = _pass_rule_evaluations()
    du_rules[0]["result"] = "DATA_UNAVAILABLE"
    du_rules[0]["reason_codes"] = ["EARNINGS_SCHEDULE_UNAVAILABLE"]
    event_gate["candidates"].append(
        {"ticker": "HH02", "gate_status": "DATA_UNAVAILABLE", "reason_codes": ["EARNINGS_SCHEDULE_UNAVAILABLE"], "rule_evaluations": du_rules}
    )
    event_gate["event_gate_input_tickers"].append("HH02")
    event_gate["upstream_candidate_tickers"].append("HH02")
    event_gate["summary"] = _event_gate_summary(pass_count=1, data_unavailable_count=1)
    # False claim: still says ranking is ready with no block reasons.
    event_gate["ranking_ready"] = True
    event_gate["ranking_block_reasons"] = []

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


def test_event_gate_reverify_rejects_zero_pass_with_false_ranking_ready(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case([])
    event_gate["candidates"] = [
        {"ticker": "II01", "gate_status": "REJECT", "reason_codes": ["EARNINGS_ON_TARGET_DATE"], "rule_evaluations": [
            {**rule, "result": "REJECT", "reason_codes": ["EARNINGS_ON_TARGET_DATE"], "evidence_ids": ["ev-1"]}
            if rule["rule_id"] == "earnings_on_target_date"
            else rule
            for rule in _pass_rule_evaluations()
        ]}
    ]
    event_gate["event_gate_input_tickers"] = ["II01"]
    event_gate["upstream_candidate_tickers"] = ["II01"]
    event_gate["summary"] = _event_gate_summary(pass_count=0, reject_count=1)
    event_gate["summary"]["rule_counts"][0] = {
        "rule_id": "earnings_on_target_date",
        "pass_count": 0,
        "reject_count": 1,
        "data_unavailable_count": 0,
    }
    # False claim: zero PASS candidates but ranking_ready=true.
    event_gate["ranking_ready"] = True
    event_gate["ranking_block_reasons"] = []

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


def test_event_gate_reverify_rejects_summary_count_mismatch(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "JJ01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # Summary falsely claims 2 PASS when only 1 candidate is recorded.
    event_gate["summary"]["pass_count"] = 2
    event_gate["summary"]["input_count"] = 2

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_SUMMARY_MISMATCH"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


def test_event_gate_reverify_rejects_pass_status_implying_reject(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "KK01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # gate_status says PASS but the earnings rule actually rejected it.
    event_gate["candidates"][0]["rule_evaluations"][0]["result"] = "REJECT"
    event_gate["candidates"][0]["rule_evaluations"][0]["reason_codes"] = ["EARNINGS_ON_TARGET_DATE"]
    event_gate["candidates"][0]["rule_evaluations"][0]["evidence_ids"] = ["ev-1"]

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_STATUS_MISMATCH"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


def test_event_gate_reverify_rejects_pass_status_implying_data_unavailable(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "LL01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # gate_status says PASS but a rule actually is DATA_UNAVAILABLE.
    event_gate["candidates"][0]["rule_evaluations"][1]["result"] = "DATA_UNAVAILABLE"
    event_gate["candidates"][0]["rule_evaluations"][1]["reason_codes"] = ["PREVIOUS_DAY_DISCLOSURE_UNAVAILABLE"]

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_STATUS_MISMATCH"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


def test_event_gate_reverify_rejects_duplicate_ticker_across_statuses(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "MM01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    reject_rules = [
        {**rule, "result": "REJECT", "reason_codes": ["EARNINGS_ON_TARGET_DATE"], "evidence_ids": ["ev-1"]}
        if rule["rule_id"] == "earnings_on_target_date"
        else rule
        for rule in _pass_rule_evaluations()
    ]
    event_gate["candidates"].append(
        {"ticker": "MM01", "gate_status": "REJECT", "reason_codes": ["EARNINGS_ON_TARGET_DATE"], "rule_evaluations": reject_rules}
    )
    event_gate["summary"]["input_count"] = 2
    event_gate["summary"]["reject_count"] = 1

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_DUPLICATE_TICKER"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


def test_event_gate_reverify_rejects_whitespace_ticker(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "NN01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    event_gate["candidates"][0]["ticker"] = " NN01"
    event_gate["event_gate_input_tickers"] = [" NN01"]

    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_TICKER_MALFORMED"):
        _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


# --- MEDIUM: canonical ordering ---------------------------------------------


def test_ranking_data_reason_order_constant_matches_schema_enum():
    from src.ranking import RANKING_DATA_REASON_ORDER

    assert RANKING_DATA_REASON_ORDER == (
        "TURNOVER_SOURCE_NOT_FOUND",
        "TURNOVER_SOURCE_NOT_YET_AVAILABLE",
        "TURNOVER_SOURCE_ACCESS_FAILED",
        "TURNOVER_SOURCE_PARSE_FAILED",
        "TURNOVER_SOURCE_STALE",
        "TURNOVER_SOURCE_CONFLICT",
    )


def test_aggregate_reason_codes_use_canonical_order_not_alphabetical():
    event_gate, candidates, market_data, source_payload = _build_case(
        [
            {"ticker": "OO01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
            {"ticker": "OO02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ]
    )
    # OO01 -> STALE (alphabetically later reason code), OO02 -> NOT_FOUND
    # (alphabetically earlier). Canonical order puts NOT_FOUND before STALE;
    # alphabetical sort would put STALE before NOT_FOUND.
    source_payload["source_attempts"][0]["status"] = "STALE"
    source_payload["source_attempts"][0]["values"] = None
    source_payload["source_attempts"][0]["result_count"] = None
    source_payload["source_attempts"][0]["coverage_status"] = None
    source_payload["source_attempts"][0]["covered_dates"] = None
    market_data["records"][0]["turnover"] = None
    candidates["candidates"][0]["features"]["turnover"]["value"] = None
    candidates["candidates"][0]["features"]["turnover"]["source_refs"] = []

    source_payload["source_attempts"][1]["status"] = "NOT_FOUND"
    source_payload["source_attempts"][1]["values"] = None
    source_payload["source_attempts"][1]["result_count"] = None
    source_payload["source_attempts"][1]["coverage_status"] = None
    source_payload["source_attempts"][1]["covered_dates"] = None
    market_data["records"][1]["turnover"] = None
    candidates["candidates"][1]["features"]["turnover"]["value"] = None
    candidates["candidates"][1]["features"]["turnover"]["source_refs"] = []

    result = _run(event_gate, candidates, market_data, source_payload)
    assert result["ranking_status"] == "DATA_UNAVAILABLE"
    assert result["reason_codes"] == ["TURNOVER_SOURCE_NOT_FOUND", "TURNOVER_SOURCE_STALE"]


def test_tick_size_source_refs_are_sorted_and_deduplicated():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "PP01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    tick_source = market_data["records"][0]["sources"][0]
    duplicate_source = dict(tick_source)
    duplicate_source["source_ref"] = "tick-zzz-PP01"
    market_data["records"][0]["sources"].append(duplicate_source)
    # Out-of-order with a duplicate: should collapse to a sorted, deduped tuple.
    market_data["records"][0]["field_provenance"][0]["source_refs"] = [
        "tick-zzz-PP01",
        tick_source["source_ref"],
        tick_source["source_ref"],
    ]

    result = _run(event_gate, candidates, market_data, source_payload)
    refs = result["candidates"][0]["provenance"]["tick_size_source_refs"]
    assert refs == sorted(set(refs))
    assert refs == sorted({"tick-zzz-PP01", tick_source["source_ref"]})


# --- MEDIUM: output contract semantic field recomputation -------------------


def test_output_contract_previous_trading_day_mismatch_is_rejected():
    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "QQ01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    ranking["previous_trading_day"] = "2099-01-01"
    with pytest.raises(ValueError, match="RANKING_PREVIOUS_TRADING_DAY_MISMATCH"):
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


def test_output_contract_event_gate_as_of_mismatch_is_rejected():
    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "QQ02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    ranking["event_gate_as_of"] = "2099-01-01T00:00:00+09:00"
    with pytest.raises(ValueError, match="RANKING_EVENT_GATE_AS_OF_MISMATCH"):
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


def test_output_contract_reason_codes_order_mismatch_is_rejected():
    event_gate, candidates, market_data, source_payload, ranking = _build_output_contract_case(
        [{"ticker": "QQ03", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # COMPLETE ranking has an empty reason_codes list; corrupt it to a
    # non-empty, wrongly-ordered value and confirm recomputation catches it.
    ranking["reason_codes"] = ["TURNOVER_SOURCE_STALE", "TURNOVER_SOURCE_NOT_FOUND"]
    with pytest.raises(ValueError, match="RANKING_REASON_CODES_RECOMPUTE_MISMATCH"):
        validate_ranking_output_contract(
            ranking=ranking,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


# --- LOW/MEDIUM: CLI source ledger path validation ---------------------------


def test_cli_build_ranking_missing_source_page_file_is_rejected(tmp_path):
    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "RR01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    # Point a source_attempt at a page file that does not exist on disk. The
    # build-ranking CLI command must pass source_base_dir through to
    # validate_source_ledger() (mirroring the market-bundle pattern used
    # elsewhere in cli.py) so this is caught, not silently accepted.
    for attempt in sources["source_attempts"]:
        attempt["source_page_path"] = "source_pages/does-not-exist.html"

    result, output_path = None, None
    with pytest.raises(ValueError, match="RANKING_SOURCE_LEDGER_INVALID"):
        result, output_path = _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)


# --- Mutation coverage: turnover attempt contract fields ---------------------


@pytest.mark.parametrize(
    ("field_name", "corrupt_value"),
    [
        ("coverage_status", "PARTIAL"),
        ("covered_dates", ["2026-08-06"]),
        ("covered_dates", [PREVIOUS_TRADING_DAY, "2026-08-06"]),
        ("result_count", 2),
        ("candidate_code", "9999"),
        ("target_date", "2026-08-11"),
    ],
)
def test_turnover_attempt_field_corruption_is_hard_error(field_name, corrupt_value):
    """Mutation check: corrupting any single Turnover Source Attempt contract
    field must fail the run, never be silently tolerated."""
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9101", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0][field_name] = corrupt_value
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_"):
        _run(event_gate, candidates, market_data, source_payload)


@pytest.mark.parametrize(
    ("field_name", "corrupt_value"),
    [
        ("field_name", "volume"),
        ("trading_date", "2026-08-06"),
        ("raw_unit", "YEN"),
        ("source_ref", None),
        ("source_ref", ""),
        ("source_ref", "src-does-not-exist"),
        ("canonical_value_yen", "10,000,000"),
        ("canonical_value_yen", 10000000),
    ],
)
def test_turnover_attempt_value_field_corruption_is_hard_error(field_name, corrupt_value):
    """Mutation check for the single `values` item of a FOUND turnover
    attempt, including its source_ref (the provenance link Ranking records)."""
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9102", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    source_payload["source_attempts"][0]["values"][0][field_name] = corrupt_value
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_"):
        _run(event_gate, candidates, market_data, source_payload)


def test_turnover_attempt_multiple_values_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9103", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    values = source_payload["source_attempts"][0]["values"]
    values.append(dict(values[0]))
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION"):
        _run(event_gate, candidates, market_data, source_payload)


def test_candidate_turnover_source_refs_missing_canonical_ref_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9104", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["candidates"][0]["features"]["turnover"]["source_refs"] = ["src-other"]
    with pytest.raises(RankingHardError, match="RANKING_TURNOVER_CANDIDATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


# --- Mutation coverage: order_plan, every field ------------------------------


def test_order_plan_recomputation_covers_every_order_plan_field():
    """The set of order_plan fields Ranking re-verifies must be exactly the
    OrderPlan dataclass field set -- no field may escape recomputation."""
    import dataclasses

    from src.strategy import OrderPlan

    plan = build_order_plan("400", "1", config=CONFIG)
    assert set(plan.as_dict()) == {field.name for field in dataclasses.fields(OrderPlan)}


@pytest.mark.parametrize(
    ("field_name", "corrupt_value"),
    [
        ("strategy_version", "v2"),
        ("validation_status", "validated"),
        ("entry_trigger", "999"),
        ("entry_limit", "999"),
        ("affordable", False),
        ("estimated_purchase_amount", "1"),
        ("expected_loss_yen", "1"),
        ("shares", 1),
        ("take_profit_price", "1"),
        ("stop_loss_price", "1"),
    ],
)
def test_order_plan_field_corruption_is_hard_error(field_name, corrupt_value):
    """Mutation check: corrupting any single stored order_plan field must be
    caught by the full recomputation via build_order_plan()."""
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9201", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    stored = candidates["candidates"][0]["order_plan"]
    assert stored[field_name] != corrupt_value
    stored[field_name] = corrupt_value
    with pytest.raises(RankingHardError, match="RANKING_ORDER_PLAN_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_order_plan_missing_field_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9202", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    del candidates["candidates"][0]["order_plan"]["entry_limit"]
    with pytest.raises(RankingHardError, match="RANKING_ORDER_PLAN_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_order_plan_unknown_extra_field_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9203", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    candidates["candidates"][0]["order_plan"]["score"] = "1"
    with pytest.raises(RankingHardError, match="RANKING_ORDER_PLAN_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


# --- Mutation coverage: tick_size provenance ---------------------------------


def test_duplicate_tick_size_provenance_entries_are_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9301", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    provenance = market_data["records"][0]["field_provenance"][0]
    market_data["records"][0]["field_provenance"].append(dict(provenance))
    with pytest.raises(RankingHardError, match="RANKING_TICK_SIZE_PROVENANCE_INVALID"):
        _run(event_gate, candidates, market_data, source_payload)


def test_tick_size_provenance_unknown_source_ref_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9302", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["field_provenance"][0]["source_refs"] = ["tick-unknown"]
    with pytest.raises(RankingHardError, match="RANKING_TICK_SIZE_PROVENANCE_INVALID"):
        _run(event_gate, candidates, market_data, source_payload)


# --- Mutation coverage: Ranking universe ticker hygiene ----------------------


@pytest.mark.parametrize("ticker", [" 9401", "9401 ", "", None, 9401])
def test_build_ranking_rejects_malformed_event_gate_pass_ticker(ticker):
    """build_ranking() is a public pure function: it must reject a malformed
    event_gate PASS ticker outright rather than silently str()/strip()-ing it
    into the ranking universe."""
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9401", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    event_gate["candidates"][0]["ticker"] = ticker
    with pytest.raises(RankingHardError, match="RANKING_TICKER_MALFORMED"):
        _run(event_gate, candidates, market_data, source_payload)


def test_output_contract_rejects_duplicate_ranking_candidate_ticker():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9402", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    payload = _run(event_gate, candidates, market_data, source_payload)
    payload["candidates"].append(copy.deepcopy(payload["candidates"][0]))
    with pytest.raises(ValueError, match="RANKING_DUPLICATE_CANDIDATE_TICKER"):
        validate_ranking_output_contract(
            ranking=payload,
            event_gate=event_gate,
            candidates=candidates,
            market_data=market_data,
            source_payload=source_payload,
            config=CONFIG,
        )


# --- Event Gate integrity validator (public, pure) ---------------------------


def test_validate_event_gate_integrity_accepts_consistent_payload():
    from src.event_gate import validate_event_gate_integrity

    event_gate, _candidates, _market_data, sources = _build_full_case(
        [{"ticker": "9501", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    assert validate_event_gate_integrity(event_gate, sources) == []


def test_validate_event_gate_integrity_is_read_only():
    """The validator must not mutate the artifacts it inspects."""
    from src.event_gate import validate_event_gate_integrity

    event_gate, _candidates, _market_data, sources = _build_full_case(
        [{"ticker": "9502", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    before_event_gate = copy.deepcopy(event_gate)
    before_sources = copy.deepcopy(sources)
    validate_event_gate_integrity(event_gate, sources)
    assert event_gate == before_event_gate
    assert sources == before_sources


def test_validate_event_gate_integrity_reports_summary_mismatch():
    from src.event_gate import validate_event_gate_integrity

    event_gate, _candidates, _market_data, sources = _build_full_case(
        [{"ticker": "9503", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    event_gate["summary"]["pass_count"] = 5
    errors = validate_event_gate_integrity(event_gate, sources)
    assert any(error.startswith("EVENT_GATE_SUMMARY_MISMATCH") for error in errors)


# --- Round-2 hardening: date integrity, entry_trigger, market ticker ---------


def test_missing_candidates_target_date_is_hard_error():
    """A *missing* target_date must be treated as a mismatch, not as
    'nothing to compare'."""
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9601", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    del candidates["target_date"]
    with pytest.raises(RankingHardError, match="RANKING_TARGET_DATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_missing_market_data_target_date_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9602", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    del market_data["target_date"]
    with pytest.raises(RankingHardError, match="RANKING_TARGET_DATE_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_missing_event_gate_target_date_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9603", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    event_gate["target_date"] = None
    with pytest.raises(RankingHardError, match="RANKING_TARGET_DATE_MISSING"):
        _run(event_gate, candidates, market_data, source_payload)


def test_missing_event_gate_previous_trading_day_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9604", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    event_gate["previous_trading_day"] = None
    with pytest.raises(RankingHardError, match="RANKING_PREVIOUS_TRADING_DAY_MISSING"):
        _run(event_gate, candidates, market_data, source_payload)


def test_non_positive_previous_high_is_hard_error():
    """The cross-multiplication comparator requires a strictly positive
    entry_trigger. That holds because build_order_plan() refuses to produce
    a plan from a non-positive previous_high -- and Ranking surfaces that
    refusal as a hard error instead of ranking on a degenerate value."""
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9605", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["previous_high"] = "0"
    with pytest.raises(RankingHardError, match="RANKING_ORDER_PLAN_MISMATCH"):
        _run(event_gate, candidates, market_data, source_payload)


def test_malformed_market_data_record_ticker_is_hard_error():
    event_gate, candidates, market_data, source_payload = _build_case(
        [{"ticker": "9606", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    market_data["records"][0]["ticker"] = " 9606"
    with pytest.raises(RankingHardError, match="RANKING_TICKER_MALFORMED"):
        _run(event_gate, candidates, market_data, source_payload)


# --- Downstream safety: Ranking never mutates upstream artifacts -------------


def test_cli_build_ranking_leaves_all_input_artifacts_byte_identical(tmp_path):
    """Documented invariant: ranking.json never changes event_gate.json,
    candidates.json, market_data.json, sources.json, source_matrix.yaml or
    the strategy snapshot."""
    import hashlib

    event_gate, candidates, market_data, sources = _build_full_case(
        [{"ticker": "PP01", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"}]
    )
    exit_code, output_path = _run_build_ranking_cli(
        tmp_path, event_gate, candidates, market_data, sources
    )
    assert exit_code == 0

    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(tmp_path.iterdir())
        if path != output_path
    }
    assert before  # the input files really are there
    # Re-running must not touch any input either.
    _run_build_ranking_cli(tmp_path, event_gate, candidates, market_data, sources)
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(tmp_path.iterdir())
        if path != output_path
    }
    assert after == before


def test_ranking_never_sets_candidate_pipeline_ranking_complete():
    """Ranking COMPLETE must never be laundered into the TRADE gate: no new
    code path may flip candidate_pipeline.summary.ranking_complete to true."""
    from pathlib import Path as _Path

    ranking_source = _Path("src/ranking.py").read_text(encoding="utf-8")
    assert "ranking_complete" in ranking_source  # its own artifact field
    pipeline_source = _Path("src/candidate_pipeline.py").read_text(encoding="utf-8")
    assert '"ranking_complete": False' in pipeline_source
    assert '"ranking_complete": True' not in pipeline_source
