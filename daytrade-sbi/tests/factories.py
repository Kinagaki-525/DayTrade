from decimal import Decimal

from src.market import MarketDataRecord, SourceRecord


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
            source_id="JPX_LISTED_COMPANY",
            source_role="PRIMARY",
            information_type="LISTED_COMPANY",
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
        source_url=f"https://example.test/{source_id.lower()}/1234/{field_name}",
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
        "url": f"https://example.test/{source_id.lower()}",
        "status": status,
        "values": {},
        "result_count": 1 if status == "FOUND" else None,
        "notes": [],
    }
