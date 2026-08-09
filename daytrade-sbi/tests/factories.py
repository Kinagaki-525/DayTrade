from decimal import Decimal

from src.market.models import MarketDataRecord, SourceRecord


def make_market_record(
    *,
    previous_high: str = "400",
    tick_size: str = "1",
) -> MarketDataRecord:
    values = {
        "open": "390",
        "high": "400",
        "low": "385",
        "close": "395",
        "volume": "1000000",
        "previous_close": "390",
        "previous_high": previous_high,
        "tick_size": tick_size,
    }
    sources = tuple(
        SourceRecord(
            source_name="Test Exchange",
            source_url=f"https://example.test/1234/{field_name}",
            retrieved_at="2026-08-09T20:00:00+09:00",
            trading_date="2026-08-07",
            ticker="1234",
            field_name=field_name,
            value=value,
        )
        for field_name, value in values.items()
    )
    return MarketDataRecord(
        ticker="1234",
        company_name="Example Co.",
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
        sources=sources,
    )
