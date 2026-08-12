from decimal import Decimal

from src.market import MarketDataRecord, SourceRecord
from src.source_checks import STANDARD_SOURCE_CHECK_IDS


def make_market_record(
    *,
    previous_high: str = "400",
    tick_size: str = "1",
    include_secondary_ohlcv: bool = True,
) -> MarketDataRecord:
    values = {
        "open": "390",
        "high": "400",
        "low": "385",
        "close": "395",
        "volume": "1000000",
        "previous_close": "390",
        "previous_high": previous_high,
    }
    sources = [
        make_source_record(
            field_name="company_name",
            value="Example Co.",
            source_id="JPX_LISTED_COMPANY",
            source_role="PRIMARY",
            information_type="LISTED_COMPANY",
        ),
        make_source_record(
            field_name="market",
            value="TSE Prime",
            source_id="JPX_LISTED_COMPANY",
            source_role="PRIMARY",
            information_type="LISTED_COMPANY",
        ),
        make_source_record(
            field_name="share_unit",
            value="100",
            source_id="JPX_TRADING_UNIT",
            source_role="PRIMARY",
            information_type="TRADING_UNIT",
        ),
    ]
    for field_name, value in values.items():
        sources.append(
            make_source_record(
                field_name=field_name,
                value=value,
                source_id="YAHOO_JP_HISTORY",
                source_role="PRIMARY",
                information_type="OHLCV",
            )
        )
        if include_secondary_ohlcv:
            sources.append(
                make_source_record(
                    field_name=field_name,
                    value=value,
                    source_id="KABUTAN_HISTORY",
                    source_role="SECONDARY",
                    information_type="OHLCV",
                )
            )
    sources.append(
        make_source_record(
            field_name="tick_size",
            value=tick_size,
            source_id="JPX_TICK_SIZE",
            source_role="PRIMARY",
            information_type="TICK_SIZE",
        )
    )
    sources.append(
        make_source_record(
            field_name="topix500_membership",
            value="true",
            source_id="JPX_TOPIX500",
            source_role="PRIMARY",
            information_type="TOPIX500_MEMBERSHIP",
        )
    )
    tick_source = next(source for source in sources if source.source_id == "JPX_TICK_SIZE")
    topix_source = next(source for source in sources if source.source_id == "JPX_TOPIX500")
    price_source = next(source for source in sources if source.field_name == "previous_high")
    return MarketDataRecord(
        ticker="1234",
        company_name="Example Co.",
        market="TSE Prime",
        share_unit=100,
        security_type="COMMON_STOCK",
        source_policy_status="FOUND",
        data_status="VERIFIED",
        data_status_reasons=(),
        trading_date="2026-08-07",
        open=Decimal("390"),
        high=Decimal("400"),
        low=Decimal("385"),
        close=Decimal("395"),
        volume=1000000,
        previous_close=Decimal("390"),
        previous_high=Decimal(previous_high),
        tick_size=Decimal(tick_size),
        turnover=None,
        spread=None,
        earnings_scheduled=None,
        special_disclosures=None,
        sources=tuple(sources),
        field_provenance=(
            {
                "field_name": "tick_size",
                "status": "VERIFIED",
                "verified_value": tick_size,
                "primary_source_ref": tick_source.source_ref,
                "secondary_source_ref": topix_source.source_ref,
                "source_refs": [
                    tick_source.source_ref,
                    topix_source.source_ref,
                    price_source.source_ref,
                ],
                "verified_at": "2026-08-09T20:01:00+09:00",
            },
        ),
    )


def make_source_record(
    *,
    field_name: str,
    value: str,
    source_id: str,
    source_role: str,
    information_type: str,
) -> SourceRecord:
    return SourceRecord(
        source_ref=f"{source_id}:1234:{field_name}",
        source_id=source_id,
        source_role=source_role,
        information_type=information_type,
        source_status="FOUND",
        source_name=source_id.replace("_", " ").title(),
        source_url=_source_url(source_id, field_name),
        retrieved_at="2026-08-09T20:00:00+09:00",
        trading_date="2026-08-07",
        ticker="1234",
        field_name=field_name,
        value=value,
    )


def make_source_attempt(
    *,
    source_id: str,
    source_role: str,
    criticality: str,
    information_type: str,
    candidate_code: str | None = "1234",
    target_date: str = "2026-08-10",
    status: str = "FOUND",
) -> dict[str, object]:
    attempt_id = f"{source_id.lower()}-{candidate_code or 'discovery'}-{status.lower()}"
    return {
        "attempt_id": attempt_id,
        "source_id": source_id,
        "source_role": source_role,
        "criticality": criticality,
        "information_type": information_type,
        "candidate_code": candidate_code,
        "target_date": target_date,
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "requested_at": "2026-08-07T20:05:00+09:00",
        "retrieved_at": "2026-08-07T20:06:00+09:00",
        "url": _attempt_url(source_id),
        "status": status,
        "values": {},
        "result_count": 1 if status == "FOUND" else None,
        "notes": [],
    }


def make_source_check(check_id: str, **overrides) -> dict[str, object]:
    check = {
        "check_id": check_id,
        "status": "FOUND",
        "source_refs": [],
        "source_attempt_ids": [],
    }
    check.update(overrides)
    return check


def make_standard_source_checks(**overrides_by_check_id) -> list[dict[str, object]]:
    return [
        make_source_check(check_id, **overrides_by_check_id.get(check_id, {}))
        for check_id in STANDARD_SOURCE_CHECK_IDS
    ]


def make_candidate_research(ticker: str = "1234", **overrides) -> dict[str, object]:
    research = {
        "ticker": ticker,
        "data_status": "VERIFIED",
        "status_reasons": [],
        "universe_status": "PASSED",
        "source_policy_status": "FOUND",
        "stage1_status": "PASSED",
        "stage2_status": "COMPLETE",
        "context_research_status": "SKIPPED",
        "source_checks": make_standard_source_checks(),
        "source_attempt_ids": [],
    }
    research.update(overrides)
    return research


EVENT_RESEARCH_INPUT_HASHES = {
    "candidate_pipeline_sha256": "1" * 64,
    "candidates_sha256": "2" * 64,
    "strategy_snapshot_sha256": "3" * 64,
}

EVENT_GATE_INPUT_HASHES = {
    "event_research_sha256": "4" * 64,
    "candidate_pipeline_sha256": "1" * 64,
    "candidates_sha256": "2" * 64,
    "sources_sha256": "5" * 64,
    "strategy_snapshot_sha256": "3" * 64,
}


def make_event_evidence(
    *,
    evidence_id: str,
    event_type: str,
    event_date: str | None,
    published_at: str = "2026-08-09T18:00:00+09:00",
    ticker: str = "1234",
    title: str = "Disclosure",
    source_url: str = "https://issuer.example.test/ir/notice",
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "event_type": event_type,
        "event_date": event_date,
        "published_at": published_at,
        "title": title,
        "ticker": ticker,
        "source_url": source_url,
    }


def make_event_source_attempt(
    *,
    attempt_id: str,
    source_id: str,
    ticker: str = "1234",
    target_date: str = "2026-08-10",
    requested_at: str = "2026-08-09T20:00:00+09:00",
    status: str = "FOUND",
    coverage_status: str | None = "COMPLETE",
    values: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    values = values if values is not None else []
    return {
        "attempt_id": attempt_id,
        "source_id": source_id,
        "source_role": "PRIMARY",
        "criticality": "TRADE_CRITICAL",
        "information_type": "EVENT",
        "candidate_code": ticker,
        "target_date": target_date,
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "requested_at": requested_at,
        "retrieved_at": requested_at,
        "url": _source_url(source_id, "event"),
        "status": status,
        "values": values,
        "coverage_status": coverage_status,
        "coverage_start": "2026-08-07T00:00:00+09:00",
        "coverage_end": requested_at,
        "covered_dates": None,
        "result_count": len(values) if status == "FOUND" else None,
        "notes": [],
    }


def make_news_classification(
    *,
    news_evidence_id: str,
    source_id: str = "YAHOO_JP_NEWS",
    source_attempt_id: str = "yahoo_jp_news-1234",
    published_at: str = "2026-08-09T18:00:00+09:00",
    signal_type: str = "NON_EVENT",
    subject_status: str = "NOT_SUBJECT",
    confirmation_evidence_ids: list[str] | None = None,
    confirmation_source_attempt_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "news_evidence_id": news_evidence_id,
        "source_id": source_id,
        "source_attempt_id": source_attempt_id,
        "published_at": published_at,
        "signal_type": signal_type,
        "subject_status": subject_status,
        "confirmation_evidence_ids": confirmation_evidence_ids or [],
        "confirmation_source_attempt_ids": confirmation_source_attempt_ids or [],
    }


def make_event_gate_candidate(
    *,
    ticker: str = "1234",
    selected_attempt_ids: dict[str, str | None] | None = None,
    news_classifications: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    default_selected = {
        "earnings_schedule_jpx": None,
        "earnings_schedule_issuer": None,
        "tdnet": None,
        "issuer_disclosure": None,
        "yahoo_news": None,
        "kabutan_news": None,
    }
    if selected_attempt_ids:
        default_selected.update(selected_attempt_ids)
    return {
        "ticker": ticker,
        "selected_attempt_ids": default_selected,
        "news_classifications": news_classifications or [],
    }


def make_event_research(
    *,
    target_date: str = "2026-08-10",
    previous_trading_day: str = "2026-08-07",
    event_gate_as_of: str | None = "2026-08-09T21:00:00+09:00",
    strategy_version: str = "v1",
    config_sha256: str = "0" * 64,
    tickers: list[str] | None = None,
    candidates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    tickers = tickers if tickers is not None else ["1234"]
    return {
        "schema_version": 1,
        "event_research_version": "event-research-v1",
        "target_date": target_date,
        "previous_trading_day": previous_trading_day,
        "event_research_started_at": "2026-08-09T09:00:00+09:00",
        "event_gate_as_of": event_gate_as_of,
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "input_hashes": dict(EVENT_RESEARCH_INPUT_HASHES),
        "event_gate_input_tickers": list(tickers),
        "candidates": candidates if candidates is not None else [
            make_event_gate_candidate(ticker=ticker) for ticker in tickers
        ],
    }


def _source_url(source_id: str, field_name: str) -> str:
    urls = {
        "JPX_LISTED_COMPANY": "https://www.jpx.co.jp/listing/co-search/",
        "JPX_TRADING_UNIT": "https://www.jpx.co.jp/equities/trading/domestic/03.html",
        "JPX_LISTED_COMPANY_AUDIT": "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html",
        "YAHOO_JP_HISTORY": "https://finance.yahoo.co.jp/quote/1234.T/history",
        "KABUTAN_HISTORY": "https://kabutan.jp/stock/kabuka?code=1234",
        "JPX_DAILY_REPORT": "https://www.jpx.co.jp/markets/statistics-equities/daily/",
        "JPX_TICK_SIZE": "https://www.jpx.co.jp/equities/trading/domestic/07.html",
        "JPX_TOPIX500": "https://www.jpx.co.jp/markets/indices/topix/",
        "JPX_TDNET": "https://www.release.tdnet.info/inbs/I_main_00.html",
        "JPX_EARNINGS_SCHEDULE": "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/",
        "YAHOO_JP_VOLUME_RANKING": "https://finance.yahoo.co.jp/stocks/ranking/volume?market=all",
        "YAHOO_JP_GAIN_RANKING": "https://finance.yahoo.co.jp/stocks/ranking/up?market=all",
        "YAHOO_JP_NEWS": "https://finance.yahoo.co.jp/quote/1234.T/news",
        "KABUTAN_NEWS": "https://kabutan.jp/stock/news?code=1234",
    }
    return urls.get(source_id, f"https://issuer.example.test/ir/{field_name}")


def _attempt_url(source_id: str) -> str:
    return _source_url(source_id, "attempt")


# ---------------------------------------------------------------------------
# Selection v1 factories
# ---------------------------------------------------------------------------


def make_ranking_candidate(
    *,
    ticker: str = "1234",
    turnover_yen: str = "50000000",
    tick_size_yen: str = "1",
    entry_trigger_yen: str = "500",
    final_rank: int = 1,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "input_status": "VALID",
        "reason_codes": [],
        "provenance": {
            "turnover_attempt_id": f"ATT-{ticker}",
            "turnover_source_ref": "SRC-1",
            "tick_size_source_refs": ["SRC-2"],
        },
        "feature_values": {
            "turnover_yen": turnover_yen,
            "tick_size_yen": tick_size_yen,
            "entry_trigger_yen": entry_trigger_yen,
            "relative_tick_size": {
                "numerator_yen": tick_size_yen,
                "denominator_yen": entry_trigger_yen,
            },
        },
        "feature_ranks": {"turnover_rank": final_rank, "relative_tick_size_rank": final_rank},
        "rank_points": final_rank * 2,
        "final_rank": final_rank,
    }


def make_complete_ranking_payload(
    *,
    target_date: str = "2026-08-12",
    previous_trading_day: str = "2026-08-11",
    strategy_version: str = "v1",
    config_sha256: str = "a" * 64,
    strategy_snapshot_sha256: str = "b" * 64,
    candidates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    candidates = candidates if candidates is not None else [make_ranking_candidate()]
    tickers = sorted(c["ticker"] for c in candidates)
    top_ticker = next(c["ticker"] for c in candidates if c["final_rank"] == 1)
    return {
        "schema_version": 1,
        "ranking_version": "ranking-v1",
        "ranking_method": "ordinal_rank_sum",
        "target_date": target_date,
        "previous_trading_day": previous_trading_day,
        "event_gate_as_of": "2026-08-11T21:00:00+09:00",
        "generated_at": "2026-08-11T21:30:00+09:00",
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "input_hashes": {
            "event_gate_sha256": "c" * 64,
            "candidates_sha256": "d" * 64,
            "market_data_sha256": "e" * 64,
            "sources_sha256": "f" * 64,
            "source_matrix_sha256": "0" * 64,
            "strategy_snapshot_sha256": strategy_snapshot_sha256,
        },
        "ranking_status": "COMPLETE",
        "ranking_complete": True,
        "reason_codes": [],
        "input_candidate_tickers": tickers,
        "summary": {
            "input_count": len(candidates),
            "valid_input_count": len(candidates),
            "data_unavailable_count": 0,
            "ranked_count": len(candidates),
            "top_ranked_ticker": top_ticker,
        },
        "candidates": candidates,
    }


def make_selection_config(
    *,
    enabled: bool = True,
    threshold_yen: int | None = 10_000_000,
    threshold_numerator: int | None = 1,
    threshold_denominator: int | None = 500,
) -> dict[str, object]:
    return {
        "enabled": enabled,
        "version": "selection-v1",
        "candidate_policy": "rank1_only",
        "fallback_policy": "none",
        "rule_logic": "all",
        "missing_data_policy": "fail_closed",
        "rules": {
            "minimum_turnover_yen": {
                "operator": ">=",
                "threshold_yen": threshold_yen if enabled else None,
            },
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": (
                    {"numerator": threshold_numerator, "denominator": threshold_denominator}
                    if enabled
                    else None
                ),
            },
        },
    }


def make_selection_input_hashes(
    *,
    ranking_sha256: str = "1" * 64,
    strategy_snapshot_sha256: str = "b" * 64,
) -> dict[str, str]:
    return {
        "ranking_sha256": ranking_sha256,
        "strategy_snapshot_sha256": strategy_snapshot_sha256,
    }
