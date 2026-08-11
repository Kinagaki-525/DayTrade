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
