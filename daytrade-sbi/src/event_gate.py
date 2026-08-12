from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

EVENT_GATE_VERSION = "event-gate-v1"
EVENT_RULE_IDS = (
    "earnings_on_target_date",
    "earnings_on_previous_trading_day",
    "tdnet_disclosure_in_event_window",
    "dangerous_news_with_primary_confirmation",
)
GATE_TERMINAL_STATUSES = ("PASS", "REJECT", "DATA_UNAVAILABLE")

RANKING_BLOCK_REASON_ORDER = (
    "EVENT_GATE_INCOMPLETE",
    "DATA_UNAVAILABLE_PRESENT",
    "NO_EVENT_GATE_PASS_CANDIDATE",
)

REASON_CODES = frozenset(
    {
        "EVENT_RESEARCH_INVALID",
        "EVENT_RESEARCH_INPUT_MISMATCH",
        "SOURCE_REFERENCE_INVALID",
        "SOURCE_CONFLICT",
        "CANONICAL_ATTEMPT_MISMATCH",
        "EARNINGS_ON_TARGET_DATE",
        "EARNINGS_SCHEDULE_UNAVAILABLE",
        "EARNINGS_DATE_CONFLICT",
        "EARNINGS_ON_PREVIOUS_TRADING_DAY",
        "PREVIOUS_DAY_DISCLOSURE_UNAVAILABLE",
        "TDNET_DISCLOSURE_FOUND",
        "TDNET_SOURCE_UNAVAILABLE",
        "TDNET_COVERAGE_INCOMPLETE",
        "DANGEROUS_NEWS_CONFIRMED",
        "DANGEROUS_NEWS_UNCONFIRMED",
        "NEWS_CLASSIFICATION_UNKNOWN",
        "NEWS_SUBJECT_AMBIGUOUS",
        "NEWS_SOURCE_UNAVAILABLE",
    }
)


@dataclass(frozen=True)
class EventRuleEvaluation:
    rule_id: str
    result: str
    reason_codes: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_attempt_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "result": self.result,
            "reason_codes": list(self.reason_codes),
            "source_ids": list(self.source_ids),
            "source_attempt_ids": list(self.source_attempt_ids),
            "evidence_ids": list(self.evidence_ids),
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
    event_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    candidates: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    from src.config import strategy_config_sha256

    target_date = event_research.get("target_date")
    previous_trading_day = event_research.get("previous_trading_day")
    event_gate_as_of = event_research.get("event_gate_as_of")
    event_window_start = _event_window_start(previous_trading_day)
    event_gate_config = config.get("event_gate", {})
    attempts = source_payload.get("source_attempts", [])

    payload: dict[str, Any] = {
        "schema_version": 2,
        "event_gate_version": EVENT_GATE_VERSION,
        "target_date": target_date,
        "previous_trading_day": previous_trading_day,
        "event_window_start": event_window_start,
        "event_gate_as_of": event_gate_as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": config.get("strategy_version"),
        "config_sha256": strategy_config_sha256(config),
        "input_hashes": input_hashes,
        "upstream_candidate_tickers": _upstream_candidate_tickers(candidates),
        "event_gate_input_tickers": list(event_research.get("event_gate_input_tickers", [])),
        "event_gate_complete": False,
        "ranking_ready": False,
        "ranking_block_reasons": [],
        "summary": _empty_summary(),
        "candidates": [],
    }

    for event_research_candidate in sorted(
        event_research.get("candidates", []),
        key=lambda item: str(item.get("ticker", "")),
    ):
        ticker = str(event_research_candidate.get("ticker", "")).strip()
        rule_evaluations = [
            _evaluate_earnings_target_date(
                ticker=ticker,
                target_date=target_date,
                attempts=attempts,
                event_gate_as_of=event_gate_as_of,
                event_gate_config=event_gate_config,
            ),
            _evaluate_earnings_previous_day(
                ticker=ticker,
                target_date=target_date,
                previous_trading_day=previous_trading_day,
                attempts=attempts,
                event_gate_as_of=event_gate_as_of,
                event_gate_config=event_gate_config,
            ),
            _evaluate_tdnet(
                ticker=ticker,
                target_date=target_date,
                attempts=attempts,
                event_gate_as_of=event_gate_as_of,
                event_window_start=event_window_start,
                event_gate_config=event_gate_config,
            ),
            _evaluate_news(
                ticker=ticker,
                target_date=target_date,
                attempts=attempts,
                event_gate_as_of=event_gate_as_of,
                event_gate_config=event_gate_config,
                event_research_candidate=event_research_candidate,
            ),
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

    payload["summary"] = _build_summary(payload["candidates"])
    payload["event_gate_complete"] = _event_gate_complete(payload, source_payload)
    payload["ranking_block_reasons"] = _ranking_block_reasons(payload)
    payload["ranking_ready"] = (
        payload["event_gate_complete"] is True and not payload["ranking_block_reasons"]
    )
    return payload


def _event_window_start(previous_trading_day: str | None) -> str | None:
    if not previous_trading_day:
        return None
    return f"{previous_trading_day}T00:00:00+09:00"


def _upstream_candidate_tickers(candidates: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(candidate.get("ticker")).strip()
            for candidate in candidates.get("candidates", [])
            if str(candidate.get("ticker", "")).strip()
        }
    )


def _empty_summary() -> dict[str, Any]:
    return {
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
    }


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def _parse_dt_strict(value: Any, context: str) -> datetime:
    parsed = _parse_dt(value)
    if parsed is None:
        raise ValueError(f"{context}: invalid or missing datetime {value!r}")
    if parsed.tzinfo is None:
        raise ValueError(f"{context}: datetime {value!r} must be timezone-aware")
    return parsed


def select_canonical_attempt(
    attempts: list[dict[str, Any]],
    *,
    ticker: str,
    source_id: str,
    target_date: str,
    event_gate_as_of: str | None,
) -> dict[str, Any] | None:
    if not event_gate_as_of:
        return None
    as_of_dt = _parse_dt_strict(event_gate_as_of, "event_gate_as_of")
    matching = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        if str(attempt.get("candidate_code") or "") != str(ticker):
            continue
        if str(attempt.get("source_id") or "") != str(source_id):
            continue
        if str(attempt.get("target_date") or "") != str(target_date):
            continue
        requested_dt = _parse_dt_strict(
            attempt.get("requested_at"),
            f"source_attempt {attempt.get('attempt_id')} requested_at",
        )
        if requested_dt > as_of_dt:
            continue
        matching.append((requested_dt, attempt))
    if not matching:
        return None
    matching.sort(key=lambda pair: (pair[0], str(pair[1].get("attempt_id") or "")), reverse=True)
    return matching[0][1]


def _attempt_events(attempt: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(attempt, dict):
        return []
    values = attempt.get("values")
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _source_clean(attempt: dict[str, Any] | None, matches: list[dict[str, Any]]) -> bool:
    if not isinstance(attempt, dict):
        return False
    if attempt.get("status") != "FOUND":
        return False
    if matches:
        return False
    return attempt.get("coverage_status") == "COMPLETE"


def _evaluate_earnings_rule(
    *,
    rule_id: str,
    ticker: str,
    attempt_target_date: str,
    match_date: str,
    source_ids: tuple[str, ...],
    attempts: list[dict[str, Any]],
    event_gate_as_of: str | None,
    match_reason: str,
    unavailable_reason: str,
    conflict_reason: str,
) -> EventRuleEvaluation:
    canonical_by_source: dict[str, dict[str, Any] | None] = {}
    for source_id in source_ids:
        canonical_by_source[source_id] = select_canonical_attempt(
            attempts,
            ticker=ticker,
            source_id=source_id,
            target_date=attempt_target_date,
            event_gate_as_of=event_gate_as_of,
        )

    source_attempt_ids = tuple(
        sorted(
            {
                str(attempt.get("attempt_id"))
                for attempt in canonical_by_source.values()
                if isinstance(attempt, dict) and attempt.get("attempt_id")
            }
        )
    )

    matches_by_source: dict[str, list[dict[str, Any]]] = {}
    clean_sources: set[str] = set()
    earnings_dates_by_source: dict[str, set[str]] = {}
    for source_id, attempt in canonical_by_source.items():
        events = _attempt_events(attempt)
        matches = [
            item
            for item in events
            if item.get("event_type") == "EARNINGS" and item.get("event_date") == match_date
        ]
        matches_by_source[source_id] = matches
        earnings_dates_by_source[source_id] = {
            str(item.get("event_date"))
            for item in events
            if item.get("event_type") == "EARNINGS" and item.get("event_date")
        }
        if _source_clean(attempt, matches):
            clean_sources.add(source_id)

    all_matches = [match for matches in matches_by_source.values() for match in matches]
    if all_matches:
        evidence_ids = tuple(
            sorted({str(item.get("evidence_id")) for item in all_matches if item.get("evidence_id")})
        )
        return EventRuleEvaluation(
            rule_id=rule_id,
            result="REJECT",
            reason_codes=(match_reason,),
            source_ids=source_ids,
            source_attempt_ids=source_attempt_ids,
            evidence_ids=evidence_ids,
        )

    if len(clean_sources) == len(source_ids):
        return EventRuleEvaluation(
            rule_id=rule_id,
            result="PASS",
            reason_codes=(),
            source_ids=source_ids,
            source_attempt_ids=source_attempt_ids,
            evidence_ids=(),
        )

    non_empty_date_sets = [dates for dates in earnings_dates_by_source.values() if dates]
    if len(non_empty_date_sets) >= 2:
        combined = set()
        conflict = False
        for dates in non_empty_date_sets:
            if combined and combined != dates:
                conflict = True
            combined |= dates
        if conflict:
            return EventRuleEvaluation(
                rule_id=rule_id,
                result="DATA_UNAVAILABLE",
                reason_codes=(conflict_reason,),
                source_ids=source_ids,
                source_attempt_ids=source_attempt_ids,
                evidence_ids=(),
            )

    return EventRuleEvaluation(
        rule_id=rule_id,
        result="DATA_UNAVAILABLE",
        reason_codes=(unavailable_reason,),
        source_ids=source_ids,
        source_attempt_ids=source_attempt_ids,
        evidence_ids=(),
    )


def _evaluate_earnings_target_date(
    *,
    ticker: str,
    target_date: str,
    attempts: list[dict[str, Any]],
    event_gate_as_of: str | None,
    event_gate_config: dict[str, Any],
) -> EventRuleEvaluation:
    earnings_config = event_gate_config.get("earnings", {})
    source_ids = tuple(earnings_config.get("target_date_source_ids", ()))
    return _evaluate_earnings_rule(
        rule_id="earnings_on_target_date",
        ticker=ticker,
        attempt_target_date=target_date,
        match_date=target_date,
        source_ids=source_ids,
        attempts=attempts,
        event_gate_as_of=event_gate_as_of,
        match_reason="EARNINGS_ON_TARGET_DATE",
        unavailable_reason="EARNINGS_SCHEDULE_UNAVAILABLE",
        conflict_reason="EARNINGS_DATE_CONFLICT",
    )


def _evaluate_earnings_previous_day(
    *,
    ticker: str,
    target_date: str,
    previous_trading_day: str,
    attempts: list[dict[str, Any]],
    event_gate_as_of: str | None,
    event_gate_config: dict[str, Any],
) -> EventRuleEvaluation:
    earnings_config = event_gate_config.get("earnings", {})
    source_ids = tuple(earnings_config.get("previous_day_source_ids", ()))
    return _evaluate_earnings_rule(
        rule_id="earnings_on_previous_trading_day",
        ticker=ticker,
        attempt_target_date=target_date,
        match_date=previous_trading_day,
        source_ids=source_ids,
        attempts=attempts,
        event_gate_as_of=event_gate_as_of,
        match_reason="EARNINGS_ON_PREVIOUS_TRADING_DAY",
        unavailable_reason="PREVIOUS_DAY_DISCLOSURE_UNAVAILABLE",
        conflict_reason="PREVIOUS_DAY_DISCLOSURE_UNAVAILABLE",
    )


def _evaluate_tdnet(
    *,
    ticker: str,
    target_date: str,
    attempts: list[dict[str, Any]],
    event_gate_as_of: str | None,
    event_window_start: str | None,
    event_gate_config: dict[str, Any],
) -> EventRuleEvaluation:
    tdnet_config = event_gate_config.get("tdnet", {})
    source_id = str(tdnet_config.get("source_id") or "JPX_TDNET")
    canonical = select_canonical_attempt(
        attempts,
        ticker=ticker,
        source_id=source_id,
        target_date=target_date,
        event_gate_as_of=event_gate_as_of,
    )
    source_attempt_ids = (
        (str(canonical.get("attempt_id")),) if isinstance(canonical, dict) and canonical.get("attempt_id") else ()
    )
    if canonical is None or canonical.get("status") != "FOUND":
        return EventRuleEvaluation(
            rule_id="tdnet_disclosure_in_event_window",
            result="DATA_UNAVAILABLE",
            reason_codes=("TDNET_SOURCE_UNAVAILABLE",),
            source_ids=(source_id,),
            source_attempt_ids=source_attempt_ids,
            evidence_ids=(),
        )

    window_start_dt = _parse_dt_strict(event_window_start, "event_window_start") if event_window_start else None
    as_of_dt = _parse_dt_strict(event_gate_as_of, "event_gate_as_of") if event_gate_as_of else None

    in_window: list[dict[str, Any]] = []
    for item in _attempt_events(canonical):
        published_at = item.get("published_at")
        if published_at is None:
            continue
        published_dt = _parse_dt_strict(
            published_at,
            f"evidence {item.get('evidence_id')} published_at",
        )
        if window_start_dt is not None and published_dt < window_start_dt:
            continue
        if as_of_dt is not None and published_dt > as_of_dt:
            continue
        in_window.append(item)

    if in_window:
        evidence_ids = tuple(
            sorted({str(item.get("evidence_id")) for item in in_window if item.get("evidence_id")})
        )
        return EventRuleEvaluation(
            rule_id="tdnet_disclosure_in_event_window",
            result="REJECT",
            reason_codes=("TDNET_DISCLOSURE_FOUND",),
            source_ids=(source_id,),
            source_attempt_ids=source_attempt_ids,
            evidence_ids=evidence_ids,
        )

    if canonical.get("coverage_status") != "COMPLETE":
        return EventRuleEvaluation(
            rule_id="tdnet_disclosure_in_event_window",
            result="DATA_UNAVAILABLE",
            reason_codes=("TDNET_COVERAGE_INCOMPLETE",),
            source_ids=(source_id,),
            source_attempt_ids=source_attempt_ids,
            evidence_ids=(),
        )

    return EventRuleEvaluation(
        rule_id="tdnet_disclosure_in_event_window",
        result="PASS",
        reason_codes=(),
        source_ids=(source_id,),
        source_attempt_ids=source_attempt_ids,
        evidence_ids=(),
    )


def _evaluate_news(
    *,
    ticker: str,
    target_date: str,
    attempts: list[dict[str, Any]],
    event_gate_as_of: str | None,
    event_gate_config: dict[str, Any],
    event_research_candidate: dict[str, Any],
) -> EventRuleEvaluation:
    news_config = event_gate_config.get("news", {})
    source_ids = tuple(news_config.get("required_source_ids", ()))

    canonical_ids: list[str] = []
    source_unavailable = False
    for source_id in source_ids:
        canonical = select_canonical_attempt(
            attempts,
            ticker=ticker,
            source_id=source_id,
            target_date=target_date,
            event_gate_as_of=event_gate_as_of,
        )
        if isinstance(canonical, dict) and canonical.get("attempt_id"):
            canonical_ids.append(str(canonical.get("attempt_id")))
        if (
            not isinstance(canonical, dict)
            or canonical.get("status") != "FOUND"
            or canonical.get("coverage_status") != "COMPLETE"
        ):
            source_unavailable = True

    classifications = event_research_candidate.get("news_classifications", [])
    if not isinstance(classifications, list):
        classifications = []

    source_attempt_ids = tuple(sorted(set(canonical_ids)))

    if any(item.get("signal_type") == "UNKNOWN" for item in classifications):
        evidence_ids = tuple(
            sorted(
                {
                    str(item.get("news_evidence_id"))
                    for item in classifications
                    if item.get("signal_type") == "UNKNOWN" and item.get("news_evidence_id")
                }
            )
        )
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_CLASSIFICATION_UNKNOWN",),
            source_ids=source_ids,
            source_attempt_ids=source_attempt_ids,
            evidence_ids=evidence_ids,
        )

    if any(item.get("subject_status") == "AMBIGUOUS" for item in classifications):
        evidence_ids = tuple(
            sorted(
                {
                    str(item.get("news_evidence_id"))
                    for item in classifications
                    if item.get("subject_status") == "AMBIGUOUS" and item.get("news_evidence_id")
                }
            )
        )
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_SUBJECT_AMBIGUOUS",),
            source_ids=source_ids,
            source_attempt_ids=source_attempt_ids,
            evidence_ids=evidence_ids,
        )

    confirmation_source_ids = frozenset(news_config.get("confirmation_source_ids", ()))
    attempts_by_id = {
        str(attempt.get("attempt_id")): attempt
        for attempt in attempts
        if isinstance(attempt, dict) and attempt.get("attempt_id")
    }

    blocking = [
        item
        for item in classifications
        if item.get("signal_type") not in {"NON_EVENT", "UNKNOWN"} and item.get("subject_status") == "CONFIRMED"
    ]
    if blocking:
        confirmed_evidence: set[str] = set()
        confirmed_attempt_ids: set[str] = set()
        for item in blocking:
            item_evidence_ids = set(item.get("confirmation_evidence_ids") or [])
            for attempt_id in item.get("confirmation_source_attempt_ids") or []:
                attempt = attempts_by_id.get(str(attempt_id))
                if not isinstance(attempt, dict):
                    continue
                if str(attempt.get("source_id") or "") not in confirmation_source_ids:
                    continue
                attempt_evidence_ids = {
                    str(evt.get("evidence_id")) for evt in _attempt_events(attempt) if evt.get("evidence_id")
                }
                matched = item_evidence_ids & attempt_evidence_ids
                if matched:
                    confirmed_evidence |= matched
                    confirmed_attempt_ids.add(str(attempt_id))
        if confirmed_evidence:
            return EventRuleEvaluation(
                rule_id="dangerous_news_with_primary_confirmation",
                result="REJECT",
                reason_codes=("DANGEROUS_NEWS_CONFIRMED",),
                source_ids=source_ids,
                source_attempt_ids=tuple(sorted(set(source_attempt_ids) | confirmed_attempt_ids)),
                evidence_ids=tuple(sorted(confirmed_evidence)),
            )
        evidence_ids = tuple(
            sorted({str(item.get("news_evidence_id")) for item in blocking if item.get("news_evidence_id")})
        )
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("DANGEROUS_NEWS_UNCONFIRMED",),
            source_ids=source_ids,
            source_attempt_ids=source_attempt_ids,
            evidence_ids=evidence_ids,
        )

    if source_unavailable:
        return EventRuleEvaluation(
            rule_id="dangerous_news_with_primary_confirmation",
            result="DATA_UNAVAILABLE",
            reason_codes=("NEWS_SOURCE_UNAVAILABLE",),
            source_ids=source_ids,
            source_attempt_ids=source_attempt_ids,
            evidence_ids=(),
        )

    return EventRuleEvaluation(
        rule_id="dangerous_news_with_primary_confirmation",
        result="PASS",
        reason_codes=(),
        source_ids=source_ids,
        source_attempt_ids=source_attempt_ids,
        evidence_ids=(),
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


def _build_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_summary()
    summary["input_count"] = len(candidates)
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


def _event_gate_complete(payload: dict[str, Any], source_payload: dict[str, Any]) -> bool:
    candidates = payload.get("candidates", [])
    input_tickers = set(payload.get("event_gate_input_tickers", []))
    candidate_tickers = [candidate.get("ticker") for candidate in candidates]
    if set(candidate_tickers) != input_tickers:
        return False
    if len(candidate_tickers) != len(set(candidate_tickers)):
        return False
    if len(candidates) != payload.get("summary", {}).get("input_count", 0):
        return False

    attempt_ids = {
        str(attempt.get("attempt_id"))
        for attempt in source_payload.get("source_attempts", [])
        if isinstance(attempt, dict) and attempt.get("attempt_id")
    }
    evidence_ids = _all_evidence_ids(source_payload)

    for candidate in candidates:
        rules = candidate.get("rule_evaluations", [])
        if len(rules) != len(EVENT_RULE_IDS):
            return False
        if tuple(rule.get("rule_id") for rule in rules) != EVENT_RULE_IDS:
            return False
        for rule in rules:
            if rule.get("result") not in GATE_TERMINAL_STATUSES:
                return False
            if any(code not in REASON_CODES for code in rule.get("reason_codes", [])):
                return False
            if rule.get("result") == "REJECT" and not rule.get("evidence_ids"):
                return False
            if rule.get("result") == "DATA_UNAVAILABLE" and not rule.get("reason_codes"):
                return False
            for attempt_id in rule.get("source_attempt_ids", []):
                if attempt_id not in attempt_ids:
                    return False
            for evidence_id in rule.get("evidence_ids", []):
                if evidence_id not in evidence_ids:
                    return False
    return True


def _all_evidence_ids(source_payload: dict[str, Any]) -> set[str]:
    evidence_ids: set[str] = set()
    for attempt in source_payload.get("source_attempts", []):
        if not isinstance(attempt, dict):
            continue
        values = attempt.get("values")
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("evidence_id"):
                evidence_ids.add(str(item.get("evidence_id")))
    return evidence_ids


def _ranking_block_reasons(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if payload.get("event_gate_complete") is not True:
        reasons.append("EVENT_GATE_INCOMPLETE")
    summary = payload.get("summary", {})
    if summary.get("data_unavailable_count", 0) > 0:
        reasons.append("DATA_UNAVAILABLE_PRESENT")
    if summary.get("pass_count", 0) == 0:
        reasons.append("NO_EVENT_GATE_PASS_CANDIDATE")
    return [reason for reason in RANKING_BLOCK_REASON_ORDER if reason in reasons]


def validate_event_gate_integrity(
    payload: dict[str, Any],
    source_payload: dict[str, Any],
) -> list[str]:
    """Pure, public re-verification of an already-written event_gate.json's
    internal consistency.

    This is a *read-only validator*: it recomputes the derivations that
    ``build_event_gate()`` performs (per-candidate ``gate_status`` from
    ``rule_evaluations``, ``summary`` counts, ``event_gate_complete``,
    ``ranking_block_reasons`` and ``ranking_ready``) from the recorded
    candidate/rule data and reports every field that does not match what the
    artifact claims. It never changes what Event Gate itself computes or
    writes.

    It lives here, next to the logic it mirrors, so downstream consumers
    (Ranking) can independently re-verify Event Gate's own flags before
    trusting them without reaching into this module's private helpers.

    Returns a list of ``"CODE: message"`` strings; an empty list means the
    artifact is internally consistent. Callers map ``CODE`` onto their own
    error-code namespace (Ranking prefixes each with ``RANKING_``).
    """
    errors: list[str] = []

    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return ["EVENT_GATE_CANDIDATES_INVALID: event_gate.candidates must be a list"]

    all_tickers: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return ["EVENT_GATE_CANDIDATES_INVALID: every event_gate candidate must be an object"]
        ticker = candidate.get("ticker")
        if not isinstance(ticker, str):
            errors.append(
                "EVENT_GATE_TICKER_MALFORMED: event_gate candidate ticker is not a string"
            )
            continue
        if not ticker or ticker != ticker.strip():
            errors.append(
                "EVENT_GATE_TICKER_MALFORMED: event_gate candidate ticker is empty or has "
                f"leading/trailing whitespace: {ticker!r}"
            )
            continue
        all_tickers.append(ticker)
    if errors:
        return errors

    # Uniqueness across ALL statuses combined (PASS/REJECT/DATA_UNAVAILABLE),
    # not just within PASS.
    if len(all_tickers) != len(set(all_tickers)):
        errors.append(
            "EVENT_GATE_DUPLICATE_TICKER: duplicate candidate ticker across event_gate.candidates "
            "(all statuses combined)"
        )

    # event_gate_input_tickers must match the candidate ticker set exactly.
    if set(all_tickers) != set(payload.get("event_gate_input_tickers", [])):
        errors.append(
            "EVENT_GATE_INPUT_TICKER_MISMATCH: event_gate.event_gate_input_tickers does not match "
            "the set of tickers across event_gate.candidates"
        )

    # Recompute each candidate's gate_status from its rule_evaluations using
    # the same aggregation build_event_gate() applies.
    for candidate in candidates:
        rule_evaluations = candidate.get("rule_evaluations", [])
        if not isinstance(rule_evaluations, list) or not all(
            isinstance(rule, dict) for rule in rule_evaluations
        ):
            errors.append(
                f"EVENT_GATE_RULE_EVALUATIONS_INVALID: {candidate.get('ticker')}: "
                "rule_evaluations must be a list of objects"
            )
            continue
        recomputed_status = _candidate_gate_status(
            [_RuleResultView(rule.get("result")) for rule in rule_evaluations]
        )
        if recomputed_status != candidate.get("gate_status"):
            errors.append(
                f"EVENT_GATE_STATUS_MISMATCH: {candidate.get('ticker')}: recorded gate_status="
                f"{candidate.get('gate_status')!r} does not match recomputed gate_status "
                f"{recomputed_status!r} from rule_evaluations"
            )
    if errors:
        return errors

    recomputed_summary = _build_summary(candidates)
    if recomputed_summary != payload.get("summary"):
        errors.append(
            "EVENT_GATE_SUMMARY_MISMATCH: event_gate.summary does not match recomputed "
            "candidate status counts"
        )

    recomputed_complete = _event_gate_complete(payload, source_payload)
    if recomputed_complete != payload.get("event_gate_complete"):
        errors.append(
            "EVENT_GATE_COMPLETE_MISMATCH: event_gate.event_gate_complete does not match "
            "recomputed value"
        )

    recomputed_block_reasons = _ranking_block_reasons(
        {"event_gate_complete": recomputed_complete, "summary": recomputed_summary}
    )
    if recomputed_block_reasons != payload.get("ranking_block_reasons"):
        errors.append(
            "EVENT_GATE_BLOCK_REASONS_MISMATCH: event_gate.ranking_block_reasons does not match "
            "recomputed value"
        )
    recomputed_ready = recomputed_complete is True and not recomputed_block_reasons
    if recomputed_ready != payload.get("ranking_ready"):
        errors.append(
            "EVENT_GATE_READY_MISMATCH: event_gate.ranking_ready does not match recomputed value"
        )
    return errors


class _RuleResultView:
    """Minimal attribute-carrying adapter so ``_candidate_gate_status()``
    (which expects :class:`EventRuleEvaluation` instances) can be reused
    against the plain ``rule_evaluations`` dicts loaded back from
    event_gate.json, without reimplementing its aggregation logic."""

    __slots__ = ("result",)

    def __init__(self, result: Any) -> None:
        self.result = result
