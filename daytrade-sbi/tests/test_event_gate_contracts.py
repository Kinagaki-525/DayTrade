from __future__ import annotations

import pytest

from src.config import load_strategy_config, strategy_config_sha256
from src.contracts import (
    validate_event_gate_inputs,
    validate_event_gate_output_contract,
    validate_event_research_contract,
    validate_event_research_inputs,
)
from src.event_gate import build_event_gate
from tests.factories import (
    EVENT_GATE_INPUT_HASHES,
    make_event_gate_candidate,
    make_event_research,
    make_event_source_attempt,
)


TARGET_DATE = "2026-08-10"
PREVIOUS_TRADING_DAY = "2026-08-07"
EVENT_GATE_AS_OF = "2026-08-09T21:00:00+09:00"


def _config():
    return load_strategy_config()


def _clean_attempts(ticker: str = "1234") -> list[dict]:
    source_ids = [
        "JPX_EARNINGS_SCHEDULE",
        "COMPANY_IR",
        "JPX_TDNET",
        "COMPANY_IR_DISCLOSURE",
        "YAHOO_JP_NEWS",
        "KABUTAN_NEWS",
    ]
    return [
        make_event_source_attempt(
            attempt_id=f"{source_id.lower()}-{ticker}",
            source_id=source_id,
            ticker=ticker,
            target_date=TARGET_DATE,
            values=[],
        )
        for source_id in source_ids
    ]


def _candidate_pipeline(*, complete: bool = True, screening_complete: bool = True):
    config = _config()
    return {
        "target_date": TARGET_DATE,
        "strategy_version": config["strategy_version"],
        "summary": {"pipeline_complete": complete, "screening_complete": screening_complete},
    }


def _candidates(tickers: list[str]):
    config = _config()
    from src.config import strategy_config_sha256

    return {
        "target_date": TARGET_DATE,
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "candidates": [
            {"ticker": ticker, "status": "ELIGIBLE", "screening_status": "PASS"} for ticker in tickers
        ],
    }


def test_validate_event_research_inputs_requires_pipeline_complete():
    config = _config()
    candidate_pipeline = _candidate_pipeline(complete=False)
    candidates = _candidates(["1234"])
    with pytest.raises(ValueError, match="not complete"):
        validate_event_research_inputs(
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            config=config,
        )


def test_validate_event_research_inputs_requires_v4_config():
    config = dict(_config())
    config["config_schema_version"] = 3
    candidate_pipeline = _candidate_pipeline()
    candidates = _candidates(["1234"])
    with pytest.raises(ValueError, match="config_schema_version must be 4"):
        validate_event_research_inputs(
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            config=config,
        )


def test_validate_event_gate_inputs_requires_ticker_set_match():
    config = _config()
    candidates = _candidates(["1234"])
    candidate_pipeline = _candidate_pipeline()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["9999"],
    )
    source_payload = {"target_date": TARGET_DATE, "source_attempts": _clean_attempts("9999")}
    with pytest.raises(ValueError, match="event_gate_input_tickers"):
        validate_event_gate_inputs(
            event_research=event_research,
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            source_payload=source_payload,
            config=config,
            input_hashes=EVENT_GATE_INPUT_HASHES,
        )


def test_validate_event_gate_inputs_requires_event_gate_as_of():
    config = _config()
    candidates = _candidates(["1234"])
    candidate_pipeline = _candidate_pipeline()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=None,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["1234"],
    )
    source_payload = {"target_date": TARGET_DATE, "source_attempts": _clean_attempts()}
    with pytest.raises(ValueError, match="event_gate_as_of must be set"):
        validate_event_gate_inputs(
            event_research=event_research,
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            source_payload=source_payload,
            config=config,
            input_hashes=EVENT_GATE_INPUT_HASHES,
        )


def test_validate_event_gate_inputs_rejects_duplicate_attempt_ids():
    config = _config()
    candidates = _candidates(["1234"])
    candidate_pipeline = _candidate_pipeline()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["1234"],
    )
    attempts = _clean_attempts()
    duplicate = dict(attempts[0])
    attempts.append(duplicate)
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    with pytest.raises(ValueError, match="duplicate source_attempt"):
        validate_event_gate_inputs(
            event_research=event_research,
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            source_payload=source_payload,
            config=config,
            input_hashes=EVENT_GATE_INPUT_HASHES,
        )


def test_validate_event_gate_inputs_rejects_selected_attempt_id_mismatch():
    config = _config()
    candidates = _candidates(["1234"])
    candidate_pipeline = _candidate_pipeline()
    attempts = _clean_attempts()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["1234"],
        candidates=[
            make_event_gate_candidate(
                ticker="1234",
                selected_attempt_ids={"tdnet": "nonexistent-attempt"},
            )
        ],
    )
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    with pytest.raises(ValueError, match="event_research contract violation"):
        validate_event_gate_inputs(
            event_research=event_research,
            candidate_pipeline=candidate_pipeline,
            candidates=candidates,
            source_payload=source_payload,
            config=config,
            input_hashes=EVENT_GATE_INPUT_HASHES,
        )


def test_validate_event_gate_inputs_accepts_matching_canonical_selection():
    config = _config()
    candidates = _candidates(["1234"])
    candidate_pipeline = _candidate_pipeline()
    attempts = _clean_attempts()
    tdnet_attempt_id = next(a["attempt_id"] for a in attempts if a["source_id"] == "JPX_TDNET")
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["1234"],
        candidates=[
            make_event_gate_candidate(
                ticker="1234",
                selected_attempt_ids={"tdnet": tdnet_attempt_id},
            )
        ],
    )
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    validate_event_gate_inputs(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=EVENT_GATE_INPUT_HASHES,
    )


def test_validate_event_research_contract_rejects_duplicate_evidence_ids():
    config = _config()
    attempts = _clean_attempts()
    attempts[0]["values"] = [
        {
            "evidence_id": "dup-1",
            "event_type": "EARNINGS",
            "event_date": TARGET_DATE,
            "published_at": "2026-08-09T10:00:00+09:00",
            "title": "t",
            "ticker": "1234",
            "source_url": "https://issuer.example.test",
        }
    ]
    attempts[1]["values"] = [dict(attempts[0]["values"][0])]
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["1234"],
    )
    with pytest.raises(ValueError, match="duplicate evidence_id"):
        validate_event_research_contract(event_research=event_research, source_payload=source_payload)


def test_validate_event_gate_output_contract_passes_for_built_payload():
    config = _config()
    candidates = _candidates(["1234"])
    candidate_pipeline = _candidate_pipeline()
    attempts = _clean_attempts()
    event_research = make_event_research(
        target_date=TARGET_DATE,
        previous_trading_day=PREVIOUS_TRADING_DAY,
        event_gate_as_of=EVENT_GATE_AS_OF,
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        tickers=["1234"],
    )
    source_payload = {"target_date": TARGET_DATE, "source_attempts": attempts}
    payload = build_event_gate(
        event_research=event_research,
        candidate_pipeline=candidate_pipeline,
        candidates=candidates,
        source_payload=source_payload,
        config=config,
        input_hashes=EVENT_GATE_INPUT_HASHES,
    )
    validate_event_gate_output_contract(event_gate=payload, event_research=event_research)
