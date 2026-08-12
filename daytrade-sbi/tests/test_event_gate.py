from __future__ import annotations

import pytest

from src.config import load_strategy_config
from src.event_gate import build_event_gate, select_canonical_attempt
from tests.factories import (
    EVENT_GATE_INPUT_HASHES,
    make_event_evidence,
    make_event_gate_candidate,
    make_event_research,
    make_event_source_attempt,
    make_news_classification,
)


TARGET_DATE = "2026-08-10"
PREVIOUS_TRADING_DAY = "2026-08-07"
EVENT_GATE_AS_OF = "2026-08-09T21:00:00+09:00"


def _config():
    return load_strategy_config()


def _clean_earnings_attempts(ticker: str = "1234") -> list[dict]:
    return [
        make_event_source_attempt(
            attempt_id=f"jpx_earnings-{ticker}",
            source_id="JPX_EARNINGS_SCHEDULE",
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        ),
        make_event_source_attempt(
            attempt_id=f"ir-{ticker}",
            source_id="COMPANY_IR",
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        ),
    ]


def _clean_previous_day_attempts(ticker: str = "1234") -> list[dict]:
    return [
        make_event_source_attempt(
            attempt_id=f"tdnet-{ticker}",
            source_id="JPX_TDNET",
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        ),
        make_event_source_attempt(
            attempt_id=f"ir_disclosure-{ticker}",
            source_id="COMPANY_IR_DISCLOSURE",
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        ),
    ]


def _clean_news_attempts(ticker: str = "1234") -> list[dict]:
    return [
        make_event_source_attempt(
            attempt_id=f"yahoo_news-{ticker}",
            source_id="YAHOO_JP_NEWS",
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        ),
        make_event_source_attempt(
            attempt_id=f"kabutan_news-{ticker}",
            source_id="KABUTAN_NEWS",
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        ),
    ]


def _clean_attempts(ticker: str = "1234") -> list[dict]:
    return (
        _clean_earnings_attempts(ticker)
        # JPX_TDNET is reused by EG02 and EG03; keep a single attempt per source_id.
        + [
            make_event_source_attempt(
                attempt_id=f"ir_disclosure-{ticker}",
                source_id="COMPANY_IR_DISCLOSURE",
                ticker=ticker,
                target_date=TARGET_DATE,
                values=[],
            ),
            make_event_source_attempt(
                attempt_id=f"tdnet-{ticker}",
                source_id="JPX_TDNET",
                ticker=ticker,
                target_date=TARGET_DATE,
                values=[],
            ),
        ]
        + _clean_news_attempts(ticker)
    )


def _build(
    *,
    tickers: list[str],
    attempts: list[dict],
    news_classifications: dict[str, list[dict]] | None = None,
    event_gate_as_of: str | None = EVENT_GATE_AS_OF,
    candidate_pipeline_summary: dict | None = None,
):
    config = _config()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=event_gate_as_of,
        strategy_version=config["strategy_version"],
        tickers=tickers,
        candidates=[
            make_event_gate_candidate(
                ticker=ticker,
                news_classifications=(news_classifications or {}).get(ticker, []),
            )
            for ticker in tickers
        ],
    )
    candidate_pipeline = {
        "target_date": TARGET_DATE,
        "summary": candidate_pipeline_summary
        or {"pipeline_complete": True, "screening_complete": True},
    }
    candidates = {
        "target_date": TARGET_DATE,
        "candidates": [
            {"ticker": ticker, "status": "ELIGIBLE", "screening_status": "PASS"} for ticker in tickers
        ],
    }
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    return build_event_gate(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=EVENT_GATE_INPUT_HASHES,
    )


def test_all_rules_pass_yields_candidate_pass():
    payload = _build(tickers=["1234"], attempts=_clean_attempts())
    candidate = payload["candidates"][0]
    assert candidate["gate_status"] == "PASS"
    assert [rule["result"] for rule in candidate["rule_evaluations"]] == ["PASS"] * 4
    assert payload["event_gate_complete"] is True
    assert payload["ranking_ready"] is True
    assert payload["ranking_block_reasons"] == []


def test_earnings_on_target_date_rejects():
    attempts = _clean_attempts()
    attempts[0]["values"] = [
        make_event_evidence(evidence_id="ev-1", event_type="EARNINGS", event_date=TARGET_DATE)
    ]
    attempts[0]["result_count"] = 1
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(r for r in candidate["rule_evaluations"] if r["rule_id"] == "earnings_on_target_date")
    assert rule["result"] == "REJECT"
    assert rule["reason_codes"] == ["EARNINGS_ON_TARGET_DATE"]
    assert rule["evidence_ids"] == ["ev-1"]
    assert candidate["gate_status"] == "REJECT"


def test_earnings_on_previous_trading_day_rejects():
    attempts = _clean_attempts()
    tdnet_attempt = next(a for a in attempts if a["source_id"] == "JPX_TDNET")
    tdnet_attempt["values"] = [
        make_event_evidence(
            evidence_id="ev-2",
            event_type="EARNINGS",
            event_date=PREVIOUS_TRADING_DAY,
            published_at="2026-08-07T15:00:00+09:00",
        )
    ]
    tdnet_attempt["result_count"] = 1
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(
        r for r in candidate["rule_evaluations"] if r["rule_id"] == "earnings_on_previous_trading_day"
    )
    assert rule["result"] == "REJECT"
    assert rule["reason_codes"] == ["EARNINGS_ON_PREVIOUS_TRADING_DAY"]
    assert candidate["gate_status"] == "REJECT"


def test_tdnet_disclosure_in_window_rejects():
    attempts = _clean_attempts()
    tdnet_attempt = next(a for a in attempts if a["source_id"] == "JPX_TDNET")
    tdnet_attempt["values"] = [
        make_event_evidence(
            evidence_id="ev-3",
            event_type="OTHER_CORPORATE_EVENT",
            event_date=None,
            published_at="2026-08-08T10:00:00+09:00",
        )
    ]
    tdnet_attempt["result_count"] = 1
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(
        r for r in candidate["rule_evaluations"] if r["rule_id"] == "tdnet_disclosure_in_event_window"
    )
    assert rule["result"] == "REJECT"
    assert rule["reason_codes"] == ["TDNET_DISCLOSURE_FOUND"]
    assert rule["evidence_ids"] == ["ev-3"]
    assert candidate["gate_status"] == "REJECT"


def test_tdnet_disclosure_outside_window_passes():
    attempts = _clean_attempts()
    tdnet_attempt = next(a for a in attempts if a["source_id"] == "JPX_TDNET")
    tdnet_attempt["values"] = [
        make_event_evidence(
            evidence_id="ev-4",
            event_type="OTHER_CORPORATE_EVENT",
            event_date=None,
            published_at="2026-08-06T10:00:00+09:00",
        )
    ]
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(
        r for r in candidate["rule_evaluations"] if r["rule_id"] == "tdnet_disclosure_in_event_window"
    )
    assert rule["result"] == "PASS"


def test_dangerous_news_confirmed_rejects():
    attempts = _clean_attempts()
    tdnet_attempt = next(a for a in attempts if a["source_id"] == "JPX_TDNET")
    tdnet_attempt["values"] = [
        make_event_evidence(
            evidence_id="ev-5",
            event_type="LEGAL_REGULATORY",
            event_date=TARGET_DATE,
            published_at="2026-08-09T10:00:00+09:00",
        )
    ]
    news = {
        "1234": [
            make_news_classification(
                news_evidence_id="news-1",
                signal_type="LEGAL_REGULATORY",
                subject_status="CONFIRMED",
                confirmation_evidence_ids=["ev-5"],
                confirmation_source_attempt_ids=[tdnet_attempt["attempt_id"]],
            )
        ]
    }
    payload = _build(tickers=["1234"], attempts=attempts, news_classifications=news)
    candidate = payload["candidates"][0]
    assert candidate["gate_status"] == "REJECT"
    news_rule = next(
        r for r in candidate["rule_evaluations"]
        if r["rule_id"] == "dangerous_news_with_primary_confirmation"
    )
    assert news_rule["result"] == "REJECT"
    assert news_rule["reason_codes"] == ["DANGEROUS_NEWS_CONFIRMED"]
    assert news_rule["evidence_ids"] == ["ev-5"]


def test_dangerous_news_confirmation_from_disallowed_source_is_unconfirmed():
    # confirmation_evidence_ids/confirmation_source_attempt_ids that do not resolve to an
    # attempt whose source_id is in event_gate.news.confirmation_source_ids (JPX_TDNET /
    # COMPANY_IR_DISCLOSURE) must NOT be treated as primary confirmation.
    attempts = _clean_attempts()
    yahoo_attempt = next(a for a in attempts if a["source_id"] == "YAHOO_JP_NEWS")
    yahoo_attempt["values"] = [
        make_event_evidence(
            evidence_id="ev-yahoo-1",
            event_type="LEGAL_REGULATORY",
            event_date=TARGET_DATE,
            published_at="2026-08-09T10:00:00+09:00",
        )
    ]
    news = {
        "1234": [
            make_news_classification(
                news_evidence_id="news-1",
                signal_type="LEGAL_REGULATORY",
                subject_status="CONFIRMED",
                confirmation_evidence_ids=["ev-yahoo-1"],
                confirmation_source_attempt_ids=[yahoo_attempt["attempt_id"]],
            )
        ]
    }
    payload = _build(tickers=["1234"], attempts=attempts, news_classifications=news)
    candidate = payload["candidates"][0]
    news_rule = next(
        r for r in candidate["rule_evaluations"]
        if r["rule_id"] == "dangerous_news_with_primary_confirmation"
    )
    assert news_rule["result"] == "DATA_UNAVAILABLE"
    assert news_rule["reason_codes"] == ["DANGEROUS_NEWS_UNCONFIRMED"]
    assert candidate["gate_status"] == "DATA_UNAVAILABLE"


def test_dangerous_news_unconfirmed_is_data_unavailable():
    news = {
        "1234": [
            make_news_classification(
                news_evidence_id="news-2",
                signal_type="LEGAL_REGULATORY",
                subject_status="CONFIRMED",
                confirmation_evidence_ids=[],
            )
        ]
    }
    payload = _build(tickers=["1234"], attempts=_clean_attempts(), news_classifications=news)
    candidate = payload["candidates"][0]
    news_rule = next(
        r for r in candidate["rule_evaluations"]
        if r["rule_id"] == "dangerous_news_with_primary_confirmation"
    )
    assert news_rule["result"] == "DATA_UNAVAILABLE"
    assert news_rule["reason_codes"] == ["DANGEROUS_NEWS_UNCONFIRMED"]
    assert candidate["gate_status"] == "DATA_UNAVAILABLE"


def test_news_classification_unknown_is_data_unavailable():
    news = {"1234": [make_news_classification(news_evidence_id="news-3", signal_type="UNKNOWN")]}
    payload = _build(tickers=["1234"], attempts=_clean_attempts(), news_classifications=news)
    news_rule = next(
        r for r in payload["candidates"][0]["rule_evaluations"]
        if r["rule_id"] == "dangerous_news_with_primary_confirmation"
    )
    assert news_rule["result"] == "DATA_UNAVAILABLE"
    assert news_rule["reason_codes"] == ["NEWS_CLASSIFICATION_UNKNOWN"]


def test_news_subject_ambiguous_is_data_unavailable():
    news = {
        "1234": [
            make_news_classification(
                news_evidence_id="news-4", signal_type="M_AND_A", subject_status="AMBIGUOUS"
            )
        ]
    }
    payload = _build(tickers=["1234"], attempts=_clean_attempts(), news_classifications=news)
    news_rule = next(
        r for r in payload["candidates"][0]["rule_evaluations"]
        if r["rule_id"] == "dangerous_news_with_primary_confirmation"
    )
    assert news_rule["result"] == "DATA_UNAVAILABLE"
    assert news_rule["reason_codes"] == ["NEWS_SUBJECT_AMBIGUOUS"]


def test_missing_earnings_attempt_is_data_unavailable():
    attempts = [
        attempt for attempt in _clean_attempts() if attempt["source_id"] != "COMPANY_IR"
    ]
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(r for r in candidate["rule_evaluations"] if r["rule_id"] == "earnings_on_target_date")
    assert rule["result"] == "DATA_UNAVAILABLE"
    assert rule["reason_codes"] == ["EARNINGS_SCHEDULE_UNAVAILABLE"]
    assert candidate["gate_status"] == "DATA_UNAVAILABLE"


def test_canonical_attempt_ignores_found_preference_and_uses_latest():
    attempts = _clean_attempts()
    attempts.append(
        make_event_source_attempt(
            attempt_id="jpx_earnings-1234-later-failed",
            source_id="JPX_EARNINGS_SCHEDULE",
            ticker="1234",
            target_date=TARGET_DATE,
            requested_at="2026-08-09T21:00:00+09:00",
            status="ACCESS_FAILED",
            coverage_status=None,
            values=[],
        )
    )
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(r for r in candidate["rule_evaluations"] if r["rule_id"] == "earnings_on_target_date")
    assert rule["result"] == "DATA_UNAVAILABLE"
    assert rule["reason_codes"] == ["EARNINGS_SCHEDULE_UNAVAILABLE"]
    assert "jpx_earnings-1234-later-failed" in rule["source_attempt_ids"]


def test_attempt_after_event_gate_as_of_is_excluded():
    attempts = _clean_attempts()
    attempts[0]["requested_at"] = "2026-08-09T23:00:00+09:00"  # after EVENT_GATE_AS_OF
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    rule = next(r for r in candidate["rule_evaluations"] if r["rule_id"] == "earnings_on_target_date")
    assert rule["result"] == "DATA_UNAVAILABLE"


def test_zero_candidates_yields_no_trade_summary():
    payload = _build(tickers=[], attempts=[])
    assert payload["summary"]["input_count"] == 0
    assert payload["event_gate_complete"] is True
    assert payload["ranking_ready"] is False
    assert payload["ranking_block_reasons"] == ["NO_EVENT_GATE_PASS_CANDIDATE"]


def test_reject_dominates_data_unavailable_in_aggregation():
    attempts = _clean_attempts()
    attempts[0]["values"] = [
        make_event_evidence(evidence_id="ev-6", event_type="EARNINGS", event_date=TARGET_DATE)
    ]
    ir_attempt = next(a for a in attempts if a["source_id"] == "YAHOO_JP_NEWS")
    ir_attempt["status"] = "ACCESS_FAILED"
    ir_attempt["coverage_status"] = None
    payload = _build(tickers=["1234"], attempts=attempts)
    candidate = payload["candidates"][0]
    assert candidate["gate_status"] == "REJECT"


def test_select_canonical_attempt_breaks_ties_by_attempt_id_desc():
    attempts = [
        make_event_source_attempt(
            attempt_id="aaa",
            source_id="JPX_TDNET",
            ticker="1234",
            target_date=TARGET_DATE,
            requested_at="2026-08-09T20:00:00+09:00",
        ),
        make_event_source_attempt(
            attempt_id="zzz",
            source_id="JPX_TDNET",
            ticker="1234",
            target_date=TARGET_DATE,
            requested_at="2026-08-09T20:00:00+09:00",
        ),
    ]
    canonical = select_canonical_attempt(
        attempts,
        ticker="1234",
        source_id="JPX_TDNET",
        target_date=TARGET_DATE,
        event_gate_as_of=EVENT_GATE_AS_OF,
    )
    assert canonical["attempt_id"] == "zzz"


def test_select_canonical_attempt_raises_on_naive_requested_at():
    attempts = [
        make_event_source_attempt(
            attempt_id="naive",
            source_id="JPX_TDNET",
            ticker="1234",
            target_date=TARGET_DATE,
            requested_at="2026-08-09T20:00:00",
        )
    ]
    with pytest.raises(ValueError):
        select_canonical_attempt(
            attempts,
            ticker="1234",
            source_id="JPX_TDNET",
            target_date=TARGET_DATE,
            event_gate_as_of=EVENT_GATE_AS_OF,
        )
