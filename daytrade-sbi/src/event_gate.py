from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

EVENT_GATE_VERSION = "event-gate-v1"
EVENT_RULE_IDS = (
    "earnings_on_target_date",
    "earnings_on_previous_trading_day",
    "tdnet_any_disclosure",
    "dangerous_news_with_primary_confirmation",
)
GATE_TERMINAL_STATUSES = ("PASS", "REJECT", "DATA_UNAVAILABLE")


@dataclass(frozen=True)
class EventRuleEvaluation:
    rule_id: str
    result: str
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_attempt_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "result": self.result,
            "reason_codes": list(self.reason_codes),
            "evidence_ids": list(self.evidence_ids),
            "source_attempt_ids": list(self.source_attempt_ids),
        }


@dataclass(frozen=True)
class EventGateCandidateResult:
    ticker: str
    gate_status: str
    reason_codes: tuple[str, ...]
    rule_evaluations: tuple[EventRuleEvaluation, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "gate_status": self.gate_status,
            "reason_codes": list(self.reason_codes),
            "rule_evaluations": [rule.as_dict() for rule in self.rule_evaluations],
        }


def build_event_gate(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    candidates: dict[str, Any],
    source_payload: dict[str, Any],
    research_window: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "event_gate_version": EVENT_GATE_VERSION,
        "target_date": market_research.get("target_date"),
        "previous_trading_day": market_research.get("previous_trading_day"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": config.get("strategy_version"),
        "config_sha256": _config_sha256(config),
        "research_window_start": research_window.get("window_start"),
        "research_cutoff": market_research.get("research_cutoff"),
        "input_hashes": input_hashes,
        "input_candidate_tickers": _input_candidate_tickers(candidates),
        "event_gate_complete": False,
        "ranking_ready": False,
        "summary": {
            "input_count": 0,
            "pass_count": 0,
            "reject_count": 0,
            "data_unavailable_count": 0,
            "rule_counts": [
                {
                    "rule_id": rule_id,
                    "pass_count": 0,
                    "reject_count": 0,
                    "data_unavailable_count": 0,
                }
                for rule_id in EVENT_RULE_IDS
            ],
        },
        "candidates": [],
    }

    hard_pass_tickers = _hard_screening_pass_tickers(candidates)
    research_by_ticker = _research_by_ticker(market_research)
    attempts_by_id = _attempts_by_id(source_payload)
    evidence_by_id = _evidence_by_id(source_payload)
    target_date = market_research.get("target_date")
    previous_trading_day = market_research.get("previous_trading_day")
    research_cutoff = market_research.get("research_cutoff")

    for ticker in sorted(hard_pass_tickers):
        research = research_by_ticker.get(ticker)
        context = research.get("event_context") if isinstance(research, dict) else None
        if not _validate_event_context_data(research, ticker):
            rule_evaluations = [
                EventRuleEvaluation(
                    rule_id=rule_id,
                    result="DATA_UNAVAILABLE",
                    reason_codes=("EVENT_CONTEXT_UNAVAILABLE",),
                    evidence_ids=tuple(),
                    source_attempt_ids=tuple(),
                )
                for rule_id in EVENT_RULE_IDS
            ]
            gate_status = _candidate_gate_status(rule_evaluations)
            reason_codes = _candidate_reason_codes(rule_evaluations)
            payload["candidates"].append(
                EventGateCandidateResult(
                    ticker=ticker,
                    gate_status=gate_status,
                    reason_codes=reason_codes,
                    rule_evaluations=tuple(rule_evaluations),
                ).as_dict()
            )
            continue
        rule_evaluations: list[EventRuleEvaluation] = []
        rule_evaluations.append(
            _evaluate_earnings_target_date(
                ticker=ticker,
                research=research,
                target_date=target_date,
                previous_trading_day=previous_trading_day,
                attempts_by_id=attempts_by_id,
                evidence_by_id=evidence_by_id,
                context=context,
            )
        )
        rule_evaluations.append(
            _evaluate_earnings_previous_day(
                ticker=ticker,
                research=research,
                target_date=target_date,
                previous_trading_day=previous_trading_day,
                attempts_by_id=attempts_by_id,
                evidence_by_id=evidence_by_id,
                context=context,
            )
        )
        rule_evaluations.append(
            _evaluate_tdnet(
                ticker=ticker,
                research=research,
                research_cutoff=research_cutoff,
                attempts_by_id=attempts_by_id,
                evidence_by_id=evidence_by_id,
                context=context,
            )
        )
        rule_evaluations.append(
            _evaluate_news(
                ticker=ticker,
                research=research,
                research_cutoff=research_cutoff,
                attempts_by_id=attempts_by_id,
                evidence_by_id=evidence_by_id,
                context=context,
            )
        )
        gate_status = _candidate_gate_status(rule_evaluations)
        reason_codes = _candidate_reason_codes(rule_evaluations)
        payload["candidates"].append(
            EventGateCandidateResult(
                ticker=ticker,
                gate_status=gate_status,
                reason_codes=reason_codes,
                rule_evaluations=tuple(rule_evaluations),
            ).as_dict()
        )

    payload["summary"] = _build_summary(payload["candidates"], payload["summary"])
    payload["event_gate_complete"] = _event_gate_complete(payload)
    payload["ranking_ready"] = (
        payload["event_gate_complete"] is True
        and payload["summary"]["data_unavailable_count"] == 0
    )
    return payload


def _hard_screening_pass_tickers(candidates: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(candidate.get("ticker")).strip()
            for candidate in candidates.get("candidates", [])
            if candidate.get("status") == "ELIGIBLE"
            and str(candidate.get("screening_status") or "").strip() == "PASS"
        }
    )


def _input_candidate_tickers(candidates: dict[str, Any]) -> list[str]:
    return sorted({str(candidate.get("ticker")).strip() for candidate in candidates.get("candidates", []) if str(candidate.get("ticker", "")).strip()})


def _research_by_ticker(market_research: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(research.get("ticker")).strip(): research
        for research in market_research.get("candidate_research", [])
        if isinstance(research, dict) and str(research.get("ticker", "")).strip()
    }


def _attempts_by_id(source_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(attempt.get("attempt_id", "")): attempt
        for attempt in source_payload.get("source_attempts", [])
        if isinstance(attempt, dict) and str(attempt.get("attempt_id", "")).strip()
    }


def _evidence_by_id(source_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for attempt in source_payload.get("source_attempts", []):
        if not isinstance(attempt, dict):
            continue
        values = attempt.get("values")
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            if isinstance(evidence_id, str):
                evidence[evidence_id] = item
    return evidence


def _config_sha256(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _select_canonical_attempt(
    attempts: list[dict[str, Any]],
    *,
    ticker: str,
    source_id: str,
    target_date: str,
) -> dict[str, Any] | None:
    matching = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict)
        and str(attempt.get("candidate_code") or "") == str(ticker)
        and str(attempt.get("source_id") or "") == str(source_id)
        and str(attempt.get("target_date") or "") == str(target_date)
    ]
    if not matching:
        return None
    found = [attempt for attempt in matching if str(attempt.get("status") or "") == "FOUND"]
    if found:
        matching = found
    matching.sort(
        key=lambda attempt: (
            _parse_dt(attempt.get("requested_at")),
            str(attempt.get("attempt_id") or ""),
        ),
        reverse=True,
    )
    return matching[0]


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _validate_event_context_data(research: dict[str, Any] | None, ticker: str) -> bool:
    if not isinstance(research, dict):
        return False
    context = research.get("event_context")
    if not isinstance(context, dict):
        return False
    selected = context.get("selected_attempt_ids")
    if not isinstance(selected, dict):
        return False
    if set(selected.keys()) != {
        "earnings_schedule",
        "tdnet",
        "yahoo_news",
        "kabutan_news",
        "issuer_disclosure",
    }:
        return False
    return isinstance(context.get("news_classifications"), list)


def _validate_news_classifications(research: dict[str, Any]) -> bool:
    if not isinstance(research, dict):
        return False
    context = research.get("event_context")
    if not isinstance(context, dict):
        return False
    classifications = context.get("news_classifications")
    if not isinstance(classifications, list):
        return False
    return True


def _validate_confirmation_evidence(research: dict[str, Any]) -> bool:
    return _validate_news_classifications(research)


def _evaluate_earnings_target_date(
    *,
    ticker: str,
    research: dict[str, Any] | None,
    target_date: str,
    previous_trading_day: str,
    attempts_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    context: dict[str, Any] | None,
) -> EventRuleEvaluation:
    evidence_ids: list[str] = []
    source_attempt_ids: list[str] = []
    if research is None:
        return EventRuleEvaluation(
            rule_id="earnings_on_target_date",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(evidence_ids),
            source_attempt_ids=tuple(source_attempt_ids),
        )

    attempt = _select_canonical_attempt(
        [attempts_by_id.get(attempt_id) for attempt_id in research.get("source_attempt_ids", []) if attempt_id in attempts_by_id],
        ticker=ticker,
        source_id="JPX_EARNINGS_SCHEDULE",
        target_date=target_date,
    )
    if attempt is None:
        return EventRuleEvaluation(
            rule_id="earnings_on_target_date",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(evidence_ids),
            source_attempt_ids=tuple(source_attempt_ids),
        )
    if attempt.get("status") != "FOUND":
        return EventRuleEvaluation(
            rule_id="earnings_on_target_date",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    values = attempt.get("values")
    if not isinstance(values, list):
        return EventRuleEvaluation(
            rule_id="earnings_on_target_date",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    for item in values:
        if not isinstance(item, dict):
            continue
        if str(item.get("scheduled_date") or "") == str(target_date):
            evidence_ids.append(str(item.get("evidence_id") or ""))
    if evidence_ids:
        return EventRuleEvaluation(
            rule_id="earnings_on_target_date",
            result="REJECT",
            reason_codes=("EARNINGS_ON_TARGET_DATE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    if len(values) == 0:
        return EventRuleEvaluation(
            rule_id="earnings_on_target_date",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    return EventRuleEvaluation(
        rule_id="earnings_on_target_date",
        result="PASS",
        reason_codes=(),
        evidence_ids=tuple(sorted(evidence_ids)),
        source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
    )


def _evaluate_earnings_previous_day(
    *,
    ticker: str,
    research: dict[str, Any] | None,
    target_date: str,
    previous_trading_day: str,
    attempts_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    context: dict[str, Any] | None,
) -> EventRuleEvaluation:
    evidence_ids: list[str] = []
    if research is None:
        return EventRuleEvaluation(
            rule_id="earnings_on_previous_trading_day",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(evidence_ids),
            source_attempt_ids=tuple(),
        )

    attempt = _select_canonical_attempt(
        [attempts_by_id.get(attempt_id) for attempt_id in research.get("source_attempt_ids", []) if attempt_id in attempts_by_id],
        ticker=ticker,
        source_id="JPX_EARNINGS_SCHEDULE",
        target_date=target_date,
    )
    if attempt is None:
        return EventRuleEvaluation(
            rule_id="earnings_on_previous_trading_day",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(evidence_ids),
            source_attempt_ids=tuple(),
        )
    if attempt.get("status") != "FOUND":
        return EventRuleEvaluation(
            rule_id="earnings_on_previous_trading_day",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    values = attempt.get("values")
    if not isinstance(values, list):
        return EventRuleEvaluation(
            rule_id="earnings_on_previous_trading_day",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    for item in values:
        if not isinstance(item, dict):
            continue
        if str(item.get("scheduled_date") or "") == str(previous_trading_day):
            evidence_ids.append(str(item.get("evidence_id") or ""))
    if evidence_ids:
        return EventRuleEvaluation(
            rule_id="earnings_on_previous_trading_day",
            result="REJECT",
            reason_codes=("EARNINGS_ON_PREVIOUS_TRADING_DAY",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    if len(values) == 0:
        return EventRuleEvaluation(
            rule_id="earnings_on_previous_trading_day",
            result="DATA_UNAVAILABLE",
            reason_codes=("EARNINGS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    return EventRuleEvaluation(
        rule_id="earnings_on_previous_trading_day",
        result="PASS",
        reason_codes=(),
        evidence_ids=tuple(sorted(evidence_ids)),
        source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
    )


def _evaluate_tdnet(
    *,
    ticker: str,
    research: dict[str, Any] | None,
    research_cutoff: str | None,
    attempts_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    context: dict[str, Any] | None,
) -> EventRuleEvaluation:
    if research is None:
        return EventRuleEvaluation(
            rule_id="tdnet_any_disclosure",
            result="DATA_UNAVAILABLE",
            reason_codes=("TDNET_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(),
            source_attempt_ids=tuple(),
        )
    attempt = _select_canonical_attempt(
        [attempts_by_id.get(attempt_id) for attempt_id in research.get("source_attempt_ids", []) if attempt_id in attempts_by_id],
        ticker=ticker,
        source_id="JPX_TDNET",
        target_date=research.get("target_date") or "",
    )
    if attempt is None:
        return EventRuleEvaluation(
            rule_id="tdnet_any_disclosure",
            result="DATA_UNAVAILABLE",
            reason_codes=("TDNET_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(),
            source_attempt_ids=tuple(),
        )
    if attempt.get("status") != "FOUND":
        return EventRuleEvaluation(
            rule_id="tdnet_any_disclosure",
            result="DATA_UNAVAILABLE",
            reason_codes=("TDNET_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    evidence_ids: list[str] = []
    for item in attempt.get("values", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("ticker") or "") != str(ticker):
            continue
        published_at = str(item.get("published_at") or "")
        if published_at and research_cutoff and published_at <= research_cutoff:
            evidence_ids.append(str(item.get("evidence_id") or ""))
    if evidence_ids:
        return EventRuleEvaluation(
            rule_id="tdnet_any_disclosure",
            result="REJECT",
            reason_codes=("TDNET_DISCLOSURE_FOUND",),
            evidence_ids=tuple(sorted(evidence_ids)),
            source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
        )
    return EventRuleEvaluation(
        rule_id="tdnet_any_disclosure",
        result="PASS",
        reason_codes=(),
        evidence_ids=tuple(sorted(evidence_ids)),
        source_attempt_ids=(str(attempt.get("attempt_id") or ""),),
    )


def _evaluate_news(
    *,
    ticker: str,
    research: dict[str, Any] | None,
    research_cutoff: str | None,
    attempts_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    context: dict[str, Any] | None,
) -> EventRuleEvaluation:
    evidence_ids: list[str] = []
    if not isinstance(context, dict):
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(),
            source_attempt_ids=tuple(),
        )
    classifications = context.get("news_classifications")
    if not isinstance(classifications, list):
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_SOURCE_UNAVAILABLE",),
            evidence_ids=tuple(),
            source_attempt_ids=tuple(),
        )
    blocking = [item for item in classifications if str(item.get("signal_type") or "") not in {"NON_EVENT", "UNKNOWN"}]
    if blocking:
        if any(
            str(item.get("subject_status") or "") == "CONFIRMED"
            and isinstance(item.get("confirmation_evidence_ids"), list)
            and item.get("confirmation_evidence_ids")
            for item in blocking
        ):
            return EventRuleEvaluation(
                rule_id="dangerous_news_with_primary_confirmation",
                result="REJECT",
                reason_codes=("DANGEROUS_NEWS_CONFIRMED",),
                evidence_ids=tuple(sorted({str(item.get("evidence_id") or "") for item in blocking if isinstance(item, dict)})),
                source_attempt_ids=tuple(),
            )
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("DANGEROUS_NEWS_UNCONFIRMED",),
            evidence_ids=tuple(sorted({str(item.get("evidence_id") or "") for item in blocking if isinstance(item, dict)})),
            source_attempt_ids=tuple(),
        )
    if any(str(item.get("signal_type") or "") == "UNKNOWN" for item in classifications):
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_CLASSIFICATION_UNKNOWN",),
            evidence_ids=tuple(sorted({str(item.get("evidence_id") or "") for item in classifications if isinstance(item, dict)})),
            source_attempt_ids=tuple(),
        )
    if any(str(item.get("subject_status") or "") == "AMBIGUOUS" for item in classifications):
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_SUBJECT_AMBIGUOUS",),
            evidence_ids=tuple(sorted({str(item.get("evidence_id") or "") for item in classifications if isinstance(item, dict)})),
            source_attempt_ids=tuple(),
        )
    return EventRuleEvaluation(
        rule_id="dangerous_news_with_primary_confirmation",
        result="PASS",
        reason_codes=(),
        evidence_ids=tuple(sorted({str(item.get("evidence_id") or "") for item in classifications if isinstance(item, dict)})),
        source_attempt_ids=tuple(),
    )


def _candidate_gate_status(rule_evaluations: list[EventRuleEvaluation]) -> str:
    if any(rule.result == "REJECT" for rule in rule_evaluations):
        return "REJECT"
    if any(rule.result == "DATA_UNAVAILABLE" for rule in rule_evaluations):
        return "DATA_UNAVAILABLE"
    return "PASS"


def _candidate_reason_codes(rule_evaluations: list[EventRuleEvaluation]) -> tuple[str, ...]:
    reason_codes: list[str] = []
    for rule in rule_evaluations:
        for reason_code in rule.reason_codes:
            if reason_code not in reason_codes:
                reason_codes.append(reason_code)
    return tuple(reason_codes)


def _build_summary(candidates: list[dict[str, Any]], previous_summary: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "input_count": len(candidates),
        "pass_count": 0,
        "reject_count": 0,
        "data_unavailable_count": 0,
        "rule_counts": [
            {
                "rule_id": rule_id,
                "pass_count": 0,
                "reject_count": 0,
                "data_unavailable_count": 0,
            }
            for rule_id in EVENT_RULE_IDS
        ],
    }
    for candidate in candidates:
        if candidate["gate_status"] == "PASS":
            summary["pass_count"] += 1
        elif candidate["gate_status"] == "REJECT":
            summary["reject_count"] += 1
        else:
            summary["data_unavailable_count"] += 1
        for rule in candidate.get("rule_evaluations", []):
            for entry in summary["rule_counts"]:
                if entry["rule_id"] == rule["rule_id"]:
                    if rule["result"] == "PASS":
                        entry["pass_count"] += 1
                    elif rule["result"] == "REJECT":
                        entry["reject_count"] += 1
                    else:
                        entry["data_unavailable_count"] += 1
                    break
    return summary


def _event_gate_complete(payload: dict[str, Any]) -> bool:
    candidates = payload.get("candidates", [])
    if len(candidates) != payload.get("summary", {}).get("input_count", 0):
        return False
    tickers = [candidate.get("ticker") for candidate in candidates]
    if len(tickers) != len(set(tickers)):
        return False
    for candidate in candidates:
        if len(candidate.get("rule_evaluations", [])) != len(EVENT_RULE_IDS):
            return False
        seen = set()
        for rule in candidate.get("rule_evaluations", []):
            rule_id = rule.get("rule_id")
            if rule_id in seen or rule_id not in EVENT_RULE_IDS:
                return False
            seen.add(rule_id)
            if rule.get("result") not in GATE_TERMINAL_STATUSES:
                return False
    return True
