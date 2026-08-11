from dataclasses import replace
from pathlib import Path

from src.market import audit_official_ohlcv, validate_market_data, validate_source_ledger
from src.source_matrix import load_source_matrix
from tests.factories import make_market_record, make_source_attempt, make_source_record


def test_complete_sourced_market_data_is_valid_for_trade():
    result = validate_market_data(make_market_record())

    assert result.valid_for_trade is True
    assert result.errors == ()


def test_source_matrix_source_ids_are_accepted_for_market_sources():
    source_matrix = load_source_matrix()
    record = make_market_record()
    matrix_sources = [
        make_source_record(
            field_name="special_disclosures",
            value="false",
            source_id=source["source_id"],
            source_role=source["role"],
            information_type=source["information_type"],
        )
        for source in source_matrix["sources"]
    ]

    result = validate_market_data(
        replace(record, sources=(*record.sources, *matrix_sources)),
        source_matrix,
    )

    assert result.valid_for_trade is True
    assert not any("source_matrix.yaml" in error for error in result.errors)


def test_missing_required_market_data_is_rejected():
    record = replace(make_market_record(), previous_high=None)

    result = validate_market_data(record)

    assert result.valid_for_trade is False
    assert "Missing required market data: previous_high" in result.errors


def test_conflicting_source_value_is_rejected():
    record = make_market_record()
    sources = list(record.sources)
    open_source = next(source for source in sources if source.field_name == "open")
    sources.append(replace(open_source, source_ref="CONFLICT:1234:open", value="391"))

    result = validate_market_data(replace(record, sources=tuple(sources)))

    assert result.valid_for_trade is False
    assert "Conflicting source values for market data field: open" in result.errors


def test_single_source_ohlcv_is_data_unavailable():
    record = make_market_record(include_secondary_ohlcv=False)

    result = validate_market_data(record)

    assert result.valid_for_trade is False
    assert "SINGLE_SOURCE_ONLY for OHLCV field: open" in result.errors


def test_source_ledger_rejects_embedded_source_not_in_canonical_ledger():
    record = make_market_record()
    ledger = {
        "target_date": "2026-08-10",
        "sources": [source.as_dict() for source in record.sources[:-1]],
    }

    result = validate_source_ledger("2026-08-10", [record], ledger)

    assert result.valid is False
    assert "ticker=1234" in result.errors[0]


def test_source_ledger_rejects_source_matrix_outside_source_id_in_sources():
    record = make_market_record()
    ledger_sources = [source.as_dict() for source in record.sources]
    unknown_source = dict(ledger_sources[0])
    unknown_source["source_ref"] = "UNKNOWN_SOURCE:1234:open"
    unknown_source["source_id"] = "UNKNOWN_SOURCE"
    ledger_sources.append(unknown_source)

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": ledger_sources,
            "source_attempts": [],
        },
    )

    assert result.valid is False
    assert any("sources[" in error and "source_matrix.yaml" in error for error in result.errors)


def test_source_ledger_rejects_source_matrix_outside_source_id_in_attempts():
    record = make_market_record()

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [
                make_source_attempt(
                    source_id="UNKNOWN_SOURCE",
                    source_role="PRIMARY",
                    criticality="CONTEXT",
                    information_type="NEWS",
                )
            ],
        },
    )

    assert result.valid is False
    assert any(
        "source_attempts[0].source_id is not defined" in error
        for error in result.errors
    )


def test_source_ledger_rejects_attempt_fields_that_do_not_match_source_matrix():
    record = make_market_record()

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [
                make_source_attempt(
                    source_id="YAHOO_JP_HISTORY",
                    source_role="SECONDARY",
                    criticality="CONTEXT",
                    information_type="NEWS",
                )
            ],
        },
    )

    assert result.valid is False
    assert any("source_role does not match" in error for error in result.errors)
    assert any("criticality does not match" in error for error in result.errors)
    assert any("information_type does not match" in error for error in result.errors)


def test_source_ledger_checks_source_specific_url_patterns():
    trading_unit = make_source_record(
        field_name="share_unit",
        value="100",
        source_id="JPX_TRADING_UNIT",
        source_role="PRIMARY",
        information_type="TRADING_UNIT",
    ).as_dict()
    trading_unit["source_url"] = "https://www.jpx.co.jp/listing/co-search/"

    topix_attempt = make_source_attempt(
        source_id="JPX_TOPIX500",
        source_role="PRIMARY",
        criticality="TRADE_CRITICAL",
        information_type="TOPIX500_MEMBERSHIP",
    )
    topix_attempt["url"] = "https://finance.yahoo.co.jp/quote/1234.T/history"

    result = validate_source_ledger(
        "2026-08-10",
        [],
        {
            "target_date": "2026-08-10",
            "sources": [trading_unit],
            "source_attempts": [topix_attempt],
        },
    )

    assert result.valid is False
    assert any("JPX_TRADING_UNIT" in error for error in result.errors)
    assert any("JPX_TOPIX500" in error for error in result.errors)


def test_source_ledger_accepts_tdnet_found_zero_results():
    record = make_market_record()
    attempt = make_source_attempt(
        source_id="JPX_TDNET",
        source_role="PRIMARY",
        criticality="RULE_DEPENDENT",
        information_type="TIMELY_DISCLOSURE",
        status="FOUND",
    )
    attempt["values"] = []
    attempt["result_count"] = 0

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [attempt],
        },
    )

    assert result.valid is True


def test_source_ledger_rejects_non_found_attempt_result_count_zero():
    record = make_market_record()
    attempt = make_source_attempt(
        source_id="YAHOO_JP_HISTORY",
        source_role="PRIMARY",
        criticality="TRADE_CRITICAL",
        information_type="OHLCV",
        status="PARSE_FAILED",
    )
    attempt["result_count"] = 0

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [attempt],
        },
    )

    assert result.valid is False
    assert any("result_count must be null" in error for error in result.errors)


def test_source_ledger_requires_attempt_id():
    record = make_market_record()
    attempt = make_source_attempt(
        source_id="YAHOO_JP_HISTORY",
        source_role="PRIMARY",
        criticality="TRADE_CRITICAL",
        information_type="OHLCV",
    )
    del attempt["attempt_id"]

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [attempt],
        },
    )

    assert result.valid is False
    assert any("attempt_id is required" in error for error in result.errors)


def test_source_ledger_rejects_missing_source_page_path():
    record = make_market_record()
    attempt = make_source_attempt(
        source_id="YAHOO_JP_HISTORY",
        source_role="PRIMARY",
        criticality="TRADE_CRITICAL",
        information_type="OHLCV",
    )
    attempt["source_page_path"] = "source_pages/yahoo_history.html"

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [attempt],
        },
        source_base_dir=Path("tests/fixtures"),
    )

    assert result.valid is False
    assert any("source_page_path does not exist" in error for error in result.errors)


def test_source_ledger_accepts_existing_source_page_path():
    record = make_market_record()
    attempt = make_source_attempt(
        source_id="YAHOO_JP_VOLUME_RANKING",
        source_role="PRIMARY",
        criticality="DISCOVERY_CRITICAL",
        information_type="VOLUME_RANKING",
    )
    attempt["source_page_path"] = "source_pages/yahoo_volume.html"

    result = validate_source_ledger(
        "2026-08-10",
        [record],
        {
            "target_date": "2026-08-10",
            "sources": [source.as_dict() for source in record.sources],
            "source_attempts": [attempt],
        },
        source_base_dir=Path("regression/2026-08-10-baseline/runs/2026-08-10"),
    )

    assert result.valid is True


def test_source_ledger_treats_numeric_string_and_number_as_same_value():
    record = make_market_record()
    ledger_sources = [source.as_dict() for source in record.sources]
    open_index = next(
        index
        for index, source in enumerate(ledger_sources)
        if source["field_name"] == "open"
    )
    ledger_sources[open_index]["value"] = 390

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


def test_field_provenance_accepts_verified_ohlcv_primary_and_secondary():
    record = make_market_record()
    primary = next(
        source
        for source in record.sources
        if source.field_name == "open" and source.source_role == "PRIMARY"
    )
    secondary = next(
        source
        for source in record.sources
        if source.field_name == "open" and source.source_role == "SECONDARY"
    )

    result = validate_market_data(
        replace(
            record,
            field_provenance=(
                {
                    "field_name": "open",
                    "status": "VERIFIED",
                    "verified_value": "390",
                    "primary_source_ref": primary.source_ref,
                    "secondary_source_ref": secondary.source_ref,
                    "source_refs": [primary.source_ref, secondary.source_ref],
                    "verified_at": "2026-08-09T20:01:00+09:00",
                },
                *record.field_provenance,
            ),
        )
    )

    assert result.valid_for_trade is True


def test_field_provenance_compares_ohlcv_sources_as_numbers():
    record = make_market_record()
    primary = next(
        source
        for source in record.sources
        if source.field_name == "open" and source.source_role == "PRIMARY"
    )
    secondary = next(
        source
        for source in record.sources
        if source.field_name == "open" and source.source_role == "SECONDARY"
    )
    numeric_primary = replace(primary, value=390)
    mixed_sources = tuple(
        numeric_primary if source.source_ref == primary.source_ref else source
        for source in record.sources
    )

    result = validate_market_data(
        replace(
            record,
            sources=mixed_sources,
            field_provenance=(
                {
                    "field_name": "open",
                    "status": "VERIFIED",
                    "verified_value": "390",
                    "primary_source_ref": primary.source_ref,
                    "secondary_source_ref": secondary.source_ref,
                    "source_refs": [primary.source_ref, secondary.source_ref],
                    "verified_at": "2026-08-09T20:01:00+09:00",
                },
                *record.field_provenance,
            ),
        )
    )

    assert result.valid_for_trade is True


def test_tick_size_provenance_requires_topix500_membership_source():
    record = make_market_record()
    provenance = dict(record.field_provenance[0])
    provenance["source_refs"] = [
        source_ref
        for source_ref in provenance["source_refs"]
        if not str(source_ref).startswith("JPX_TOPIX500:")
    ]

    result = validate_market_data(replace(record, field_provenance=(provenance,)))

    assert result.valid_for_trade is False
    assert any("JPX_TOPIX500 primary membership" in error for error in result.errors)


def test_tick_size_audit_only_provenance_is_not_tradable():
    record = make_market_record()
    provenance = dict(record.field_provenance[0])
    provenance["status"] = "SINGLE_SOURCE_ONLY"

    result = validate_market_data(replace(record, field_provenance=(provenance,)))

    assert result.valid_for_trade is False
    assert any("status must be VERIFIED" in error for error in result.errors)


def test_field_provenance_rejects_conflict_with_verified_value():
    record = make_market_record()

    result = validate_market_data(
        replace(
            record,
            field_provenance=(
                {
                    "field_name": "open",
                    "status": "CONFLICT",
                    "verified_value": "390",
                    "primary_source_ref": None,
                    "secondary_source_ref": None,
                    "source_refs": [],
                    "verified_at": None,
                },
            ),
        )
    )

    assert result.valid_for_trade is False
    assert any(
        "verified_value must be null when CONFLICT" in error
        for error in result.errors
    )


def test_official_ohlcv_audit_treats_missing_jpx_report_as_not_yet_available():
    record = make_market_record()

    results = audit_official_ohlcv(
        [record],
        {"target_date": "2026-08-10", "sources": [source.as_dict() for source in record.sources]},
    )

    assert results[0]["audit_status"] == "NOT_YET_AVAILABLE"
    assert results[0]["differences"] == []


def test_official_ohlcv_audit_detects_conflict_without_rewriting_saved_value():
    record = make_market_record()
    ledger_sources = [source.as_dict() for source in record.sources]
    for field_name, value in {
        "open": "390",
        "high": "401",
        "low": "385",
        "close": "395",
        "volume": "1000000",
    }.items():
        ledger_sources.append(
            make_source_record(
                field_name=field_name,
                value=value,
                source_id="JPX_DAILY_REPORT",
                source_role="AUDIT",
                information_type="OHLCV",
            ).as_dict()
        )

    results = audit_official_ohlcv(
        [record],
        {"target_date": "2026-08-10", "sources": ledger_sources},
    )

    assert results[0]["audit_status"] == "CONFLICT"
    assert results[0]["differences"] == [
        {"field_name": "high", "saved_value": record.high, "official_value": "401"}
    ]
    assert record.high == 400
