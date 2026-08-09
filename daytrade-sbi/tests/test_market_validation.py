from dataclasses import replace

from src.market import validate_source_ledger
from src.market.validation import validate_market_data
from tests.factories import make_market_record


def test_complete_sourced_market_data_is_valid_for_trade():
    result = validate_market_data(make_market_record())

    assert result.valid_for_trade is True
    assert result.errors == ()


def test_missing_required_market_data_is_rejected():
    record = replace(make_market_record(), previous_high=None)

    result = validate_market_data(record)

    assert result.valid_for_trade is False
    assert "Missing required market data: previous_high" in result.errors


def test_conflicting_source_value_is_rejected():
    record = make_market_record()
    sources = list(record.sources)
    sources.append(replace(sources[0], value="391"))

    result = validate_market_data(replace(record, sources=tuple(sources)))

    assert result.valid_for_trade is False
    assert "Conflicting source values for market data field: open" in result.errors


def test_source_ledger_rejects_embedded_source_not_in_canonical_ledger():
    record = make_market_record()
    ledger = {
        "target_date": "2026-08-10",
        "sources": [source.as_dict() for source in record.sources[:-1]],
    }

    result = validate_source_ledger("2026-08-10", [record], ledger)

    assert result.valid is False
    assert "ticker=1234" in result.errors[0]


def test_source_ledger_treats_numeric_string_and_number_as_same_value():
    record = make_market_record()
    ledger_sources = [source.as_dict() for source in record.sources]
    ledger_sources[0]["value"] = 390

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {"target_date": "2026-08-10", "sources": ledger_sources},
    )

    assert result.valid is True


def test_market_date_requires_extended_iso_format():
    record = replace(make_market_record(), trading_date="20260807")

    result = validate_market_data(record)

    assert result.valid_for_trade is False
    assert "trading_date must use YYYY-MM-DD" in result.errors
