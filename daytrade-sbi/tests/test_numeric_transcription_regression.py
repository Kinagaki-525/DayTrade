"""Regression for the failure this whole change exists to prevent.

An AI agent must never re-type a market number. These tests pin the property
that every numeric value in the ledger is reproducible *from the stored raw
bytes* by the deterministic parser, so a hand-edited (AI-transcribed) value is
detectable rather than trusted.
"""

from __future__ import annotations

import pytest

from src.ranking import canonical_turnover_yen
from src.source_acquisition import acquire_stage
from src.source_fetch import TransportResult
from src.source_matrix import load_source_matrix, source_by_id
from src.source_parsers.base import ParseContext
from src.source_parsers.registry import parse_source_page
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
CUTOFF = "2026-08-12T20:00:00+09:00"
MATRIX = load_source_matrix()
DEFINITIONS = source_by_id(MATRIX)


def _transport(body: bytes):
    def _run(url: str) -> TransportResult:
        return TransportResult(0, 200, "text/html; charset=utf-8", body)

    return _run


def _acquire(stage: str, body: bytes, tmp_path, tickers=("7203",), source_ids=None):
    return acquire_stage(
        stage,
        target_date=TARGET_DATE,
        trading_date=TARGET_DATE,
        research_cutoff=CUTOFF,
        tickers=list(tickers),
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=_transport(body),
        source_ids=source_ids,
    )


def test_ledger_turnover_is_reproducible_from_the_stored_bytes(tmp_path):
    result = _acquire("TURNOVER", pages.yahoo_quote_page(turnover_raw="1,234,567"), tmp_path)
    attempt = result.attempts[0]
    value = attempt["values"][0]

    stored = (tmp_path / attempt["source_page_path"]).read_bytes()
    reparsed = parse_source_page(
        stored,
        DEFINITIONS["YAHOO_JP_QUOTE"],
        ParseContext(
            source_id="YAHOO_JP_QUOTE",
            trading_date=TARGET_DATE,
            ticker="7203",
            content_type=attempt["content_type"],
        ),
    )
    assert reparsed.values[0].canonical_value_yen == value["canonical_value_yen"]
    assert value["canonical_value_yen"] == str(canonical_turnover_yen("1,234,567"))


def test_a_transcribed_turnover_does_not_survive_reparsing(tmp_path):
    """Simulate the original incident: an agent 'improves' the number."""
    result = _acquire("TURNOVER", pages.yahoo_quote_page(turnover_raw="1,234,567"), tmp_path)
    attempt = result.attempts[0]
    tampered_value = dict(attempt["values"][0], canonical_value_yen="9999999000")

    stored = (tmp_path / attempt["source_page_path"]).read_bytes()
    reparsed = parse_source_page(
        stored,
        DEFINITIONS["YAHOO_JP_QUOTE"],
        ParseContext(
            source_id="YAHOO_JP_QUOTE",
            trading_date=TARGET_DATE,
            ticker="7203",
            content_type=attempt["content_type"],
        ),
    )
    assert reparsed.values[0].canonical_value_yen != tampered_value["canonical_value_yen"]


def test_turnover_canonicalization_is_not_duplicated(tmp_path):
    """The parser must go through Ranking's helper, not its own arithmetic."""
    from src.source_parsers import numeric

    assert numeric.canonical_turnover_yen is canonical_turnover_yen
    assert numeric.canonical_turnover_from_raw("1,000", "THOUSAND_YEN") == canonical_turnover_yen(
        "1,000"
    )


def test_unknown_unit_is_parse_failed_not_a_guessed_conversion():
    from src.source_parsers.numeric import ParseFailed, canonical_turnover_from_raw

    with pytest.raises(ParseFailed):
        canonical_turnover_from_raw("1,000", "MILLION_YEN")


def test_ticker_cross_contamination_is_rejected(tmp_path):
    """A page for 6758 must never populate 7203's numbers."""
    result = _acquire("TURNOVER", pages.yahoo_quote_page(ticker="6758"), tmp_path)
    attempt = result.attempts[0]
    assert attempt["candidate_code"] == "7203"
    assert attempt["status"] == "PARSE_FAILED"
    assert attempt["values"] is None


def test_trading_date_mismatch_is_rejected(tmp_path):
    result = acquire_stage(
        "STAGE2",
        target_date=TARGET_DATE,
        trading_date="2026-08-13",  # a date the page does not contain
        research_cutoff=CUTOFF,
        tickers=["7203"],
        run_dir=tmp_path,
        source_matrix=MATRIX,
        transport=_transport(pages.yahoo_history_page()),
        source_ids=("YAHOO_JP_HISTORY",),
    )
    attempt = result.attempts[0]
    assert attempt["status"] == "NOT_FOUND"
    assert attempt["values"] is None


def test_ambiguous_page_never_silently_picks_a_number(tmp_path):
    result = _acquire(
        "TURNOVER", pages.yahoo_quote_page(extra_turnover="9,999,999"), tmp_path
    )
    attempt = result.attempts[0]
    assert attempt["status"] == "PARSE_FAILED"
    assert attempt["values"] is None


def test_prompt_injection_page_yields_only_data_not_actions(tmp_path):
    """A source page telling the agent to run curl is inert data.

    The deterministic path must extract exactly the same turnover as a clean
    page and must expose no execution surface whatsoever.
    """
    result = _acquire("TURNOVER", pages.PROMPT_INJECTION_PAGE, tmp_path)
    attempt = result.attempts[0]
    assert attempt["status"] == "FOUND"
    assert attempt["values"][0]["canonical_value_yen"] == str(
        canonical_turnover_yen("1,234,567")
    )
    # the injected instruction text is not promoted into any parsed field
    for value in attempt["values"]:
        assert "curl" not in str(value["value"])
        assert "Ignore previous instructions" not in str(value["value"])
