from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import strategy_config_sha256

EVENT_RESEARCH_VERSION = "event-research-v1"

SELECTED_ATTEMPT_KEYS = (
    "earnings_schedule_jpx",
    "earnings_schedule_issuer",
    "tdnet",
    "issuer_disclosure",
    "yahoo_news",
    "kabutan_news",
)

SELECTED_ATTEMPT_KEY_SOURCE_IDS = {
    "earnings_schedule_jpx": "JPX_EARNINGS_SCHEDULE",
    "earnings_schedule_issuer": "COMPANY_IR",
    "tdnet": "JPX_TDNET",
    "issuer_disclosure": "COMPANY_IR_DISCLOSURE",
    "yahoo_news": "YAHOO_JP_NEWS",
    "kabutan_news": "KABUTAN_NEWS",
}


def hard_screening_pass_tickers(candidates: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(candidate.get("ticker")).strip()
            for candidate in candidates.get("candidates", [])
            if candidate.get("status") == "ELIGIBLE"
            and str(candidate.get("screening_status") or "").strip() == "PASS"
        }
    )


def init_event_research_payload(
    *,
    candidate_pipeline: dict[str, Any],
    candidates: dict[str, Any],
    config: dict[str, Any],
    previous_trading_day: str,
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    summary = candidate_pipeline.get("summary", {})
    if summary.get("pipeline_complete") is not True:
        raise ValueError("candidate_pipeline is not complete")
    if summary.get("screening_complete") is not True:
        raise ValueError("candidate_pipeline screening_complete is not true")
    tickers = hard_screening_pass_tickers(candidates)
    return {
        "schema_version": 1,
        "event_research_version": EVENT_RESEARCH_VERSION,
        "target_date": candidate_pipeline.get("target_date"),
        "previous_trading_day": previous_trading_day,
        "event_research_started_at": datetime.now(timezone.utc).isoformat(),
        "event_gate_as_of": None,
        "strategy_version": config.get("strategy_version"),
        "config_sha256": strategy_config_sha256(config),
        "input_hashes": input_hashes,
        "event_gate_input_tickers": tickers,
        "candidates": [
            {
                "ticker": ticker,
                "selected_attempt_ids": {key: None for key in SELECTED_ATTEMPT_KEYS},
                "news_classifications": [],
            }
            for ticker in tickers
        ],
    }


def event_research_contract_errors(
    event_research: dict[str, Any],
    *,
    source_payload: dict[str, Any],
) -> list[str]:
    """Structural / referential checks that go beyond JSON Schema."""
    from src.event_gate import select_canonical_attempt

    errors: list[str] = []
    event_gate_as_of = event_research.get("event_gate_as_of")
    target_date = event_research.get("target_date")
    if not event_gate_as_of:
        errors.append("event_gate_as_of must be set before validation")
        return errors

    attempts = source_payload.get("source_attempts", [])
    attempt_ids = {
        str(attempt.get("attempt_id"))
        for attempt in attempts
        if isinstance(attempt, dict) and attempt.get("attempt_id")
    }
    input_tickers = set(event_research.get("event_gate_input_tickers", []))
    candidate_tickers = [str(c.get("ticker")) for c in event_research.get("candidates", [])]
    if set(candidate_tickers) != input_tickers:
        errors.append("event_research candidates do not match event_gate_input_tickers")
    if len(candidate_tickers) != len(set(candidate_tickers)):
        errors.append("event_research candidate tickers must be unique")

    for candidate in event_research.get("candidates", []):
        ticker = str(candidate.get("ticker", ""))
        selected = candidate.get("selected_attempt_ids", {})
        for key, attempt_id in selected.items():
            if attempt_id is None:
                continue
            if attempt_id not in attempt_ids:
                errors.append(f"{ticker}: selected_attempt_ids.{key} references unknown attempt_id {attempt_id}")
                continue
            source_id = SELECTED_ATTEMPT_KEY_SOURCE_IDS.get(key)
            canonical = select_canonical_attempt(
                attempts,
                ticker=ticker,
                source_id=source_id,
                target_date=target_date,
                event_gate_as_of=event_gate_as_of,
            )
            canonical_id = str(canonical.get("attempt_id")) if isinstance(canonical, dict) else None
            if canonical_id != attempt_id:
                errors.append(
                    f"{ticker}: selected_attempt_ids.{key} ({attempt_id}) does not match "
                    f"canonical attempt ({canonical_id})"
                )
        for classification in candidate.get("news_classifications", []):
            published_at = classification.get("published_at")
            if not isinstance(published_at, str):
                errors.append(f"{ticker}: news classification missing published_at")
                continue
            try:
                parsed = datetime.fromisoformat(published_at)
            except ValueError:
                errors.append(f"{ticker}: news classification published_at is not a valid datetime")
                continue
            if parsed.tzinfo is None:
                errors.append(f"{ticker}: news classification published_at must be timezone-aware")
    return errors
