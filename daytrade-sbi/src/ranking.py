from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.event_gate import select_canonical_attempt


RANKING_VERSION = "ranking-v1"
RANKING_METHOD = "ordinal_rank_sum"
RANKING_SCHEMA_VERSION = 1
TURNOVER_SOURCE_ID = "YAHOO_JP_QUOTE"

# Six business failure statuses. A single candidate in one of these states
# makes the entire ranking run DATA_UNAVAILABLE (fail-closed, no partial
# ranking). These are never converted to hard errors.
DATA_UNAVAILABLE_STATUS_TO_REASON_CODE = {
    "NOT_FOUND": "TURNOVER_SOURCE_NOT_FOUND",
    "NOT_YET_AVAILABLE": "TURNOVER_SOURCE_NOT_YET_AVAILABLE",
    "ACCESS_FAILED": "TURNOVER_SOURCE_ACCESS_FAILED",
    "PARSE_FAILED": "TURNOVER_SOURCE_PARSE_FAILED",
    "STALE": "TURNOVER_SOURCE_STALE",
    "CONFLICT": "TURNOVER_SOURCE_CONFLICT",
}

# Canonical/spec-defined ordering for the six known Ranking reason codes.
# Both the per-candidate `reason_codes` and the top-level aggregate
# `reason_codes` must be emitted (and validated) in this fixed order, never
# alphabetically and never in discovery order. Matches ranking.schema.json's
# enum ordering and DATA_UNAVAILABLE_STATUS_TO_REASON_CODE's declaration
# order above.
RANKING_DATA_REASON_ORDER: tuple[str, ...] = tuple(DATA_UNAVAILABLE_STATUS_TO_REASON_CODE.values())


def _order_reason_codes(codes: Any) -> list[str]:
    """Return codes deduplicated and sorted into RANKING_DATA_REASON_ORDER."""
    code_set = set(codes)
    return [code for code in RANKING_DATA_REASON_ORDER if code in code_set]

# Workflow-incomplete statuses are always hard errors, never DATA_UNAVAILABLE.
WORKFLOW_INCOMPLETE_STATUSES = frozenset(
    {"NOT_STARTED", "DEPENDENCY_NOT_READY", "EXECUTION_FAILED"}
)
# Statuses that are invalid/undefined for a required TRADE_CRITICAL source
# are always hard errors, never DATA_UNAVAILABLE.
INVALID_SOURCE_STATUSES = frozenset(
    {"SOURCE_POLICY_UNDEFINED", "NOT_REQUIRED", "SINGLE_SOURCE_ONLY"}
)

_RAW_VALUE_PATTERN = re.compile(r"^(?:\d+|\d{1,3}(?:,\d{3})+)$")


class RankingHardError(ValueError):
    """Hard error raised for integrity violations. Never converted to
    DATA_UNAVAILABLE and never used to write ranking.json."""


def _hard_error(code: str, message: str) -> RankingHardError:
    return RankingHardError(f"{code}: {message}")


def _as_decimal(value: Any) -> Decimal | None:
    """Best-effort exact Decimal conversion, or ``None`` when the value is
    not a number at all.

    Several upstream schemas type a numeric field loosely (for example
    ``candidates.schema.json``'s ``featureValue.value`` also permits a JSON
    boolean), so ``Decimal(str(value))`` can raise ``decimal.InvalidOperation``
    -- an error outside the ``RANKING_*`` namespace -- on input that is
    perfectly schema-valid. Callers treat ``None`` as "does not equal the
    canonical value" and raise their own ``RANKING_*`` hard error, so every
    failure stays inside Ranking's error-code namespace.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def parse_raw_turnover_thousand_yen(raw_value: Any) -> Decimal:
    """Strictly parse a THOUSAND_YEN raw_value string: digits only, with
    valid 3-digit comma grouping. No naive comma stripping."""
    if not isinstance(raw_value, str) or not _RAW_VALUE_PATTERN.match(raw_value):
        raise _hard_error(
            "RANKING_TURNOVER_RAW_VALUE_MALFORMED",
            f"invalid turnover raw_value: {raw_value!r}",
        )
    digits = raw_value.replace(",", "")
    return Decimal(digits)


def canonical_turnover_yen(raw_value: Any) -> Decimal:
    """raw (THOUSAND_YEN) * 1000 = canonical (JPY), recomputed in Python."""
    return parse_raw_turnover_thousand_yen(raw_value) * Decimal(1000)


def pass_candidate_tickers(event_gate: dict[str, Any]) -> list[str]:
    """The Ranking universe: exactly the Event Gate ``PASS`` tickers.

    Tickers are validated, never normalized: a non-string or
    whitespace-padded ticker is a hard error rather than something silently
    ``str()``/``strip()``-coerced into the ranking universe, so a malformed
    event_gate.json can never be laundered into a plausible-looking ranking
    input. ``REJECT``/``DATA_UNAVAILABLE`` candidates are excluded.
    """
    pass_tickers: list[str] = []
    for candidate in event_gate.get("candidates", []):
        if not isinstance(candidate, dict):
            raise _hard_error(
                "RANKING_EVENT_GATE_CANDIDATES_INVALID",
                "every event_gate candidate must be an object",
            )
        if candidate.get("gate_status") != "PASS":
            continue
        ticker = candidate.get("ticker")
        if not isinstance(ticker, str) or not ticker or ticker != ticker.strip():
            raise _hard_error(
                "RANKING_TICKER_MALFORMED",
                f"event_gate PASS candidate ticker is not a canonical string: {ticker!r}",
            )
        pass_tickers.append(ticker)
    if len(pass_tickers) != len(set(pass_tickers)):
        raise _hard_error(
            "RANKING_DUPLICATE_CANDIDATE_TICKER",
            "duplicate PASS ticker in event_gate.candidates",
        )
    return pass_tickers


@dataclass(frozen=True)
class _CandidateInput:
    ticker: str
    input_status: str
    reason_codes: tuple[str, ...]
    turnover_attempt_id: str | None
    turnover_source_ref: str | None
    # Only the turnover feature can be missing (that is what makes a
    # candidate DATA_UNAVAILABLE). tick_size / entry_trigger are always
    # present: a null/non-positive tick_size and an unrecomputable
    # order_plan are both hard errors in _collect_ranking_inputs().
    turnover_value: Decimal | None
    tick_size: Decimal
    entry_trigger: Decimal
    tick_size_source_refs: tuple[str, ...]


def _collect_ranking_inputs(
    *,
    event_gate: dict[str, Any],
    candidates: dict[str, Any],
    market_data: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> list[_CandidateInput]:
    target_date = event_gate.get("target_date")
    previous_trading_day = event_gate.get("previous_trading_day")
    event_gate_as_of = event_gate.get("event_gate_as_of")
    attempts = source_payload.get("source_attempts", [])
    sources = source_payload.get("sources", [])

    # HIGH-1: date integrity. event_gate/candidates/market_data must all
    # agree on target_date -- a mismatch is always a hard error, never
    # folded into DATA_UNAVAILABLE.
    if not target_date:
        raise _hard_error(
            "RANKING_TARGET_DATE_MISSING",
            "event_gate.json target_date is required",
        )
    if not previous_trading_day:
        raise _hard_error(
            "RANKING_PREVIOUS_TRADING_DAY_MISSING",
            "event_gate.json previous_trading_day is required",
        )
    # event_gate_as_of is the load-bearing cutoff for Canonical Attempt
    # selection: select_canonical_attempt() returns None for a falsy
    # boundary, which would otherwise surface as a misleading
    # "no attempt found" per candidate (and as nothing at all when the PASS
    # set is empty). Reject it explicitly, like the other two date fields.
    if not event_gate_as_of:
        raise _hard_error(
            "RANKING_EVENT_GATE_AS_OF_MISSING",
            "event_gate.json event_gate_as_of is required",
        )
    # A *missing* target_date is a mismatch too: it must never be treated as
    # "nothing to compare" and waved through.
    if candidates.get("target_date") != target_date:
        raise _hard_error(
            "RANKING_TARGET_DATE_MISMATCH",
            "candidates.json target_date does not match event_gate.json target_date",
        )
    if market_data.get("target_date") != target_date:
        raise _hard_error(
            "RANKING_TARGET_DATE_MISMATCH",
            "market_data.json target_date does not match event_gate.json target_date",
        )

    candidates_by_ticker: dict[str, dict[str, Any]] = {}
    for candidate in candidates.get("candidates", []):
        if not isinstance(candidate, dict):
            raise _hard_error(
                "RANKING_CANDIDATES_INVALID",
                "every candidates.json candidate must be an object",
            )
        ticker = candidate.get("ticker")
        if not isinstance(ticker, str) or not ticker or ticker != ticker.strip():
            raise _hard_error(
                "RANKING_TICKER_MALFORMED",
                f"candidate ticker is not a canonical string: {ticker!r}",
            )
        if ticker in candidates_by_ticker:
            raise _hard_error(
                "RANKING_DUPLICATE_CANDIDATE_TICKER",
                f"duplicate candidate ticker: {ticker}",
            )
        candidates_by_ticker[ticker] = candidate

    market_by_ticker: dict[str, dict[str, Any]] = {}
    for record in market_data.get("records", []):
        if not isinstance(record, dict):
            raise _hard_error(
                "RANKING_MARKET_DATA_INVALID",
                "every market_data.json record must be an object",
            )
        ticker = record.get("ticker")
        if not isinstance(ticker, str) or not ticker or ticker != ticker.strip():
            raise _hard_error(
                "RANKING_TICKER_MALFORMED",
                f"market_data record ticker is not a canonical string: {ticker!r}",
            )
        if ticker in market_by_ticker:
            raise _hard_error(
                "RANKING_DUPLICATE_MARKET_RECORD",
                f"duplicate market_data record ticker: {ticker}",
            )
        market_by_ticker[ticker] = record

    pass_tickers = pass_candidate_tickers(event_gate)

    inputs: list[_CandidateInput] = []
    for ticker in sorted(pass_tickers):
        candidate = candidates_by_ticker.get(ticker)
        if candidate is None:
            raise _hard_error(
                "RANKING_CANDIDATE_LINK_MISSING",
                f"{ticker}: no matching candidates.json entry for event_gate PASS candidate",
            )
        if (
            candidate.get("status") != "ELIGIBLE"
            or candidate.get("screening_status") != "PASS"
            or candidate.get("order_plan") is None
        ):
            raise _hard_error(
                "RANKING_CANDIDATE_NOT_ELIGIBLE",
                f"{ticker}: candidate is not ELIGIBLE/PASS with a non-null order_plan",
            )

        market_record = market_by_ticker.get(ticker)
        if market_record is None:
            raise _hard_error(
                "RANKING_MARKET_DATA_LINK_MISSING",
                f"{ticker}: no matching market_data.json record",
            )
        if market_record.get("data_status") != "VERIFIED":
            raise _hard_error(
                "RANKING_MARKET_DATA_NOT_VERIFIED",
                f"{ticker}: market_data.json record is not VERIFIED",
            )
        if market_record.get("trading_date") != previous_trading_day:
            raise _hard_error(
                "RANKING_TRADING_DATE_MISMATCH",
                f"{ticker}: market_data.json trading_date does not match "
                "event_gate.previous_trading_day",
            )

        tick_size_raw = market_record.get("tick_size")
        if tick_size_raw is None:
            raise _hard_error(
                "RANKING_TICK_SIZE_INVALID",
                f"{ticker}: market_data.json tick_size is null",
            )
        tick_size = _as_decimal(tick_size_raw)
        if tick_size is None:
            raise _hard_error(
                "RANKING_TICK_SIZE_INVALID",
                f"{ticker}: market_data.json tick_size is not numeric: {tick_size_raw!r}",
            )
        if tick_size <= 0:
            raise _hard_error(
                "RANKING_TICK_SIZE_INVALID",
                f"{ticker}: market_data.json tick_size must be > 0",
            )

        tick_size_source_refs = _tick_size_source_refs(ticker, market_record)

        order_plan = _recompute_order_plan(
            ticker=ticker,
            candidate=candidate,
            market_record=market_record,
            config=config,
        )
        entry_trigger = order_plan.entry_trigger

        turnover_result = _resolve_turnover(
            ticker=ticker,
            candidate=candidate,
            market_record=market_record,
            attempts=attempts,
            sources=sources,
            target_date=target_date,
            previous_trading_day=previous_trading_day,
            event_gate_as_of=event_gate_as_of,
        )

        if turnover_result.input_status == "DATA_UNAVAILABLE":
            inputs.append(
                _CandidateInput(
                    ticker=ticker,
                    input_status="DATA_UNAVAILABLE",
                    reason_codes=turnover_result.reason_codes,
                    turnover_attempt_id=turnover_result.attempt_id,
                    turnover_source_ref=None,
                    turnover_value=None,
                    tick_size=tick_size,
                    entry_trigger=entry_trigger,
                    tick_size_source_refs=tick_size_source_refs,
                )
            )
        else:
            inputs.append(
                _CandidateInput(
                    ticker=ticker,
                    input_status="VALID",
                    reason_codes=(),
                    turnover_attempt_id=turnover_result.attempt_id,
                    turnover_source_ref=turnover_result.source_ref,
                    turnover_value=turnover_result.value,
                    tick_size=tick_size,
                    entry_trigger=entry_trigger,
                    tick_size_source_refs=tick_size_source_refs,
                )
            )

    return inputs


def _tick_size_source_refs(ticker: str, market_record: dict[str, Any]) -> tuple[str, ...]:
    """Extract and validate the tick_size field_provenance source_refs used
    as a Ranking feature. Reuses the market_data.json field_provenance
    contract established elsewhere in the codebase (see
    src/market.py::_validate_tick_size_provenance) rather than trusting the
    tick_size value in isolation."""
    provenances = [
        item
        for item in market_record.get("field_provenance") or []
        if isinstance(item, dict) and item.get("field_name") == "tick_size"
    ]
    if not provenances:
        raise _hard_error(
            "RANKING_TICK_SIZE_PROVENANCE_MISSING",
            f"{ticker}: market_data.json field_provenance for tick_size is missing",
        )
    if len(provenances) > 1:
        raise _hard_error(
            "RANKING_TICK_SIZE_PROVENANCE_INVALID",
            f"{ticker}: market_data.json has more than one field_provenance entry for tick_size",
        )
    provenance = provenances[0]
    if provenance.get("status") != "VERIFIED":
        raise _hard_error(
            "RANKING_TICK_SIZE_PROVENANCE_INVALID",
            f"{ticker}: market_data.json tick_size field_provenance status must be VERIFIED",
        )
    source_refs = provenance.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise _hard_error(
            "RANKING_TICK_SIZE_PROVENANCE_INVALID",
            f"{ticker}: market_data.json tick_size field_provenance source_refs is empty",
        )
    known_refs = {
        source.get("source_ref")
        for source in market_record.get("sources") or []
        if isinstance(source, dict)
    }
    missing = [ref for ref in source_refs if ref not in known_refs]
    if missing:
        raise _hard_error(
            "RANKING_TICK_SIZE_PROVENANCE_INVALID",
            f"{ticker}: market_data.json tick_size field_provenance source_refs "
            f"reference unknown sources: {missing}",
        )
    # Canonical ordering: sorted + deduplicated, never insertion order and
    # never allowing duplicates through into the artifact.
    return tuple(sorted({str(ref) for ref in source_refs}))


def _recompute_order_plan(
    *,
    ticker: str,
    candidate: dict[str, Any],
    market_record: dict[str, Any],
    config: dict[str, Any],
) -> Any:
    from src.strategy import build_order_plan

    previous_high = market_record.get("previous_high")
    tick_size = market_record.get("tick_size")
    try:
        recomputed = build_order_plan(previous_high, tick_size, config=config)
    except Exception as exc:  # noqa: BLE001
        raise _hard_error(
            "RANKING_ORDER_PLAN_MISMATCH",
            f"{ticker}: unable to recompute order_plan: {exc}",
        ) from exc

    stored = candidate.get("order_plan") or {}
    recomputed_dict = recomputed.as_dict()
    # Full-field recomputation: the compared field set is derived from the
    # recomputed OrderPlan itself, so a future OrderPlan field can never
    # silently escape verification. Extra stored fields are rejected too.
    extra = set(stored) - set(recomputed_dict)
    if extra:
        raise _hard_error(
            "RANKING_ORDER_PLAN_MISMATCH",
            f"{ticker}: order_plan contains unknown field(s): {', '.join(sorted(extra))}",
        )
    for field_name in sorted(recomputed_dict):
        stored_value = stored.get(field_name)
        recomputed_value = recomputed_dict.get(field_name)
        if isinstance(recomputed_value, Decimal):
            # Numeric fields are serialized as decimal strings: compare by
            # exact Decimal value, never by float or string identity.
            equal = _as_decimal(stored_value) == recomputed_value
        else:
            equal = stored_value == recomputed_value
        if not equal:
            raise _hard_error(
                "RANKING_ORDER_PLAN_MISMATCH",
                f"{ticker}: order_plan.{field_name} does not match recomputed value",
            )
    return recomputed


@dataclass(frozen=True)
class _TurnoverResult:
    input_status: str
    reason_codes: tuple[str, ...]
    source_id: str | None
    attempt_id: str | None
    source_ref: str | None
    value: Decimal | None


def _resolve_turnover(
    *,
    ticker: str,
    candidate: dict[str, Any],
    market_record: dict[str, Any],
    attempts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    target_date: str | None,
    previous_trading_day: str | None,
    event_gate_as_of: str | None,
) -> _TurnoverResult:
    attempt = select_canonical_attempt(
        attempts,
        ticker=ticker,
        source_id=TURNOVER_SOURCE_ID,
        target_date=str(target_date),
        event_gate_as_of=event_gate_as_of,
    )
    if attempt is None:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_MISSING",
            f"{ticker}: no canonical {TURNOVER_SOURCE_ID} source_attempt found",
        )

    attempt_id = attempt.get("attempt_id")
    status = attempt.get("status")

    # The Canonical Turnover Attempt's own identity fields must match the
    # TRADE_CRITICAL YAHOO_JP_QUOTE/TURNOVER contract for every status, not
    # only FOUND -- otherwise a mis-attributed attempt could silently drive
    # a DATA_UNAVAILABLE (or hard-error) outcome for the wrong reason.
    if attempt.get("source_id") != TURNOVER_SOURCE_ID:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt source_id must be {TURNOVER_SOURCE_ID}",
        )
    if attempt.get("source_role") != "PRIMARY":
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt source_role must be PRIMARY",
        )
    if attempt.get("criticality") != "TRADE_CRITICAL":
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt criticality must be TRADE_CRITICAL",
        )
    if attempt.get("information_type") != "TURNOVER":
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt information_type must be TURNOVER",
        )
    if attempt.get("candidate_code") != ticker:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt candidate_code does not match ticker",
        )
    if attempt.get("target_date") != target_date:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt target_date does not match target_date",
        )

    if status in WORKFLOW_INCOMPLETE_STATUSES:
        raise _hard_error(
            "RANKING_TURNOVER_SOURCE_WORKFLOW_INCOMPLETE",
            f"{ticker}: turnover source_attempt status {status} is workflow-incomplete",
        )
    if status in INVALID_SOURCE_STATUSES:
        raise _hard_error(
            "RANKING_TURNOVER_SOURCE_INVALID_STATUS",
            f"{ticker}: turnover source_attempt status {status} is invalid for a TRADE_CRITICAL source",
        )
    if status in DATA_UNAVAILABLE_STATUS_TO_REASON_CODE:
        # Artifact contradiction guard: a failure-status Canonical Attempt
        # must never coexist with a stale FOUND-shaped turnover value left
        # behind in the attempt itself, in market_data.json or in
        # candidates.json. That is a hard error, not a DATA_UNAVAILABLE
        # business outcome.
        if attempt.get("values"):
            raise _hard_error(
                "RANKING_TURNOVER_RESIDUAL_VALUE_CONTRADICTION",
                f"{ticker}: turnover source_attempt values must be empty when its "
                f"status is {status}",
            )
        if market_record.get("turnover") is not None:
            raise _hard_error(
                "RANKING_TURNOVER_RESIDUAL_VALUE_CONTRADICTION",
                f"{ticker}: market_data.json turnover must be null when the canonical "
                f"turnover attempt status is {status}",
            )
        candidate_turnover_feature = (candidate.get("features") or {}).get("turnover") or {}
        if candidate_turnover_feature.get("value") is not None:
            raise _hard_error(
                "RANKING_TURNOVER_RESIDUAL_VALUE_CONTRADICTION",
                f"{ticker}: candidates.json features.turnover.value must be null when the "
                f"canonical turnover attempt status is {status}",
            )
        return _TurnoverResult(
            input_status="DATA_UNAVAILABLE",
            reason_codes=(DATA_UNAVAILABLE_STATUS_TO_REASON_CODE[status],),
            source_id=TURNOVER_SOURCE_ID,
            attempt_id=str(attempt_id) if attempt_id else None,
            source_ref=None,
            value=None,
        )
    if status != "FOUND":
        raise _hard_error(
            "RANKING_TURNOVER_SOURCE_INVALID_STATUS",
            f"{ticker}: unrecognized turnover source_attempt status {status}",
        )

    if attempt.get("coverage_status") != "COMPLETE":
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: FOUND turnover source_attempt coverage_status must be COMPLETE",
        )
    if attempt.get("covered_dates") != [previous_trading_day]:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: FOUND turnover source_attempt covered_dates must be [previous_trading_day]",
        )

    values = attempt.get("values")
    if not isinstance(values, list) or len(values) != 1:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: FOUND turnover source_attempt must have exactly one value item",
        )
    item = values[0]
    if not isinstance(item, dict):
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt value item must be an object",
        )
    if item.get("field_name") != "turnover":
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt value field_name must be 'turnover'",
        )
    if item.get("trading_date") != previous_trading_day:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt trading_date does not match previous_trading_day",
        )
    if item.get("raw_unit") != "THOUSAND_YEN":
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt raw_unit must be THOUSAND_YEN",
        )
    if attempt.get("result_count") != 1:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt result_count must be 1",
        )

    canonical = canonical_turnover_yen(item.get("raw_value"))
    stored_canonical_raw = item.get("canonical_value_yen")
    if not isinstance(stored_canonical_raw, str) or not re.match(r"^\d+$", stored_canonical_raw):
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: canonical_value_yen must be a digits-only string",
        )
    if canonical != Decimal(stored_canonical_raw):
        raise _hard_error(
            "RANKING_TURNOVER_CANONICAL_MISMATCH",
            f"{ticker}: recomputed canonical turnover does not equal stored canonical_value_yen",
        )

    source_ref = item.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref:
        raise _hard_error(
            "RANKING_TURNOVER_ATTEMPT_CONTRACT_VIOLATION",
            f"{ticker}: turnover source_attempt value is missing source_ref",
        )

    _four_way_consistency_check(
        ticker=ticker,
        source_ref=source_ref,
        canonical=canonical,
        sources=sources,
        market_record=market_record,
        candidate=candidate,
        previous_trading_day=previous_trading_day,
    )

    return _TurnoverResult(
        input_status="VALID",
        reason_codes=(),
        source_id=TURNOVER_SOURCE_ID,
        attempt_id=str(attempt_id) if attempt_id else None,
        source_ref=source_ref,
        value=canonical,
    )


def _four_way_consistency_check(
    *,
    ticker: str,
    source_ref: str,
    canonical: Decimal,
    sources: list[dict[str, Any]],
    market_record: dict[str, Any],
    candidate: dict[str, Any],
    previous_trading_day: str | None,
) -> None:
    matches = [
        source
        for source in sources
        if isinstance(source, dict)
        and source.get("source_ref") == source_ref
        and source.get("source_id") == TURNOVER_SOURCE_ID
        and source.get("source_role") == "PRIMARY"
        and source.get("information_type") == "TURNOVER"
        and source.get("source_status") == "FOUND"
        and source.get("ticker") == ticker
        and source.get("trading_date") == previous_trading_day
        and source.get("field_name") == "turnover"
    ]
    if len(matches) != 1:
        raise _hard_error(
            "RANKING_TURNOVER_SOURCE_RECORD_MISMATCH",
            f"{ticker}: sources.json does not contain exactly one matching turnover record for {source_ref}",
        )
    source_decimal = _as_decimal(matches[0].get("value"))
    if source_decimal is None:
        raise _hard_error(
            "RANKING_TURNOVER_SOURCE_RECORD_MISMATCH",
            f"{ticker}: sources.json turnover value is not numeric",
        )
    if source_decimal != canonical:
        raise _hard_error(
            "RANKING_TURNOVER_SOURCE_RECORD_MISMATCH",
            f"{ticker}: sources.json turnover value does not equal canonical value",
        )

    market_turnover = market_record.get("turnover")
    if market_turnover is None:
        raise _hard_error(
            "RANKING_TURNOVER_MARKET_DATA_MISMATCH",
            f"{ticker}: market_data.json turnover is null",
        )
    if _as_decimal(market_turnover) != canonical:
        raise _hard_error(
            "RANKING_TURNOVER_MARKET_DATA_MISMATCH",
            f"{ticker}: market_data.json turnover does not equal canonical value",
        )

    features = candidate.get("features") or {}
    turnover_feature = features.get("turnover")
    if not isinstance(turnover_feature, dict):
        raise _hard_error(
            "RANKING_TURNOVER_CANDIDATE_MISMATCH",
            f"{ticker}: candidates.json features.turnover is missing",
        )
    feature_value = turnover_feature.get("value")
    if feature_value is None or _as_decimal(feature_value) != canonical:
        raise _hard_error(
            "RANKING_TURNOVER_CANDIDATE_MISMATCH",
            f"{ticker}: candidates.json features.turnover.value does not equal canonical value",
        )
    if turnover_feature.get("estimated") is not False:
        raise _hard_error(
            "RANKING_TURNOVER_CANDIDATE_MISMATCH",
            f"{ticker}: candidates.json features.turnover.estimated must be false",
        )
    if source_ref not in (turnover_feature.get("source_refs") or []):
        raise _hard_error(
            "RANKING_TURNOVER_CANDIDATE_MISMATCH",
            f"{ticker}: candidates.json features.turnover.source_refs does not contain the canonical source_ref",
        )


def _compare_relative_tick(left: _CandidateInput, right: _CandidateInput) -> int:
    """Cross-multiplication comparison of tick_size / entry_trigger. No
    division is used. Returns -1/0/1 like a normal comparator.

    ``a/b < c/d <=> a*d < c*b`` is only order-preserving for strictly
    positive denominators. That precondition holds by construction:
    ``entry_trigger`` always comes from ``build_order_plan()``, which
    rejects a non-positive ``previous_high`` (and any non-positive
    ``tick_size`` is rejected in ``_collect_ranking_inputs``), so every
    entry_trigger reaching this comparator is > 0.
    """
    left_value = left.tick_size * right.entry_trigger
    right_value = right.tick_size * left.entry_trigger
    if left_value < right_value:
        return -1
    if left_value > right_value:
        return 1
    return 0


def _competition_rank_turnover(inputs: list[_CandidateInput]) -> dict[str, int]:
    ordered = sorted(inputs, key=lambda item: (-item.turnover_value, item.ticker))
    ranks: dict[str, int] = {}
    previous_value: Decimal | None = None
    current_rank = 0
    for index, item in enumerate(ordered, start=1):
        if previous_value is None or item.turnover_value != previous_value:
            current_rank = index
            previous_value = item.turnover_value
        ranks[item.ticker] = current_rank
    return ranks


def _compare_relative_tick_then_ticker(left: _CandidateInput, right: _CandidateInput) -> int:
    """Total order used only to lay candidates out for competition ranking:
    relative tick asc, then ticker asc so the ordering is deterministic."""
    tick_cmp = _compare_relative_tick(left, right)
    if tick_cmp != 0:
        return tick_cmp
    return (left.ticker > right.ticker) - (left.ticker < right.ticker)


def _competition_rank_relative_tick(inputs: list[_CandidateInput]) -> dict[str, int]:
    ordered = sorted(inputs, key=functools.cmp_to_key(_compare_relative_tick_then_ticker))
    ranks: dict[str, int] = {}
    current_rank = 0
    previous_item: _CandidateInput | None = None
    for index, item in enumerate(ordered, start=1):
        if previous_item is None or _compare_relative_tick(item, previous_item) != 0:
            current_rank = index
        ranks[item.ticker] = current_rank
        previous_item = item
    return ranks


def _compare_final_candidate(
    left: dict[str, Any],
    right: dict[str, Any],
    inputs_by_ticker: dict[str, _CandidateInput],
) -> int:
    if left["rank_points"] != right["rank_points"]:
        return -1 if left["rank_points"] < right["rank_points"] else 1
    left_input = inputs_by_ticker[left["ticker"]]
    right_input = inputs_by_ticker[right["ticker"]]
    if left_input.turnover_value != right_input.turnover_value:
        return -1 if left_input.turnover_value > right_input.turnover_value else 1
    tick_cmp = _compare_relative_tick(left_input, right_input)
    if tick_cmp != 0:
        return tick_cmp
    if left["ticker"] != right["ticker"]:
        return -1 if left["ticker"] < right["ticker"] else 1
    return 0


def build_ranking(
    *,
    event_gate: dict[str, Any],
    candidates: dict[str, Any],
    market_data: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    """Pure function: build the Ranking v1 artifact. No file or network I/O.

    Does NOT convert rank 1 to a TRADE. Ranking COMPLETE never implies
    TRADE-eligibility.
    """
    from src.config import strategy_config_sha256

    if event_gate.get("event_gate_complete") is not True:
        raise _hard_error(
            "RANKING_EVENT_GATE_NOT_READY",
            "event_gate.event_gate_complete must be true",
        )
    if event_gate.get("ranking_ready") is not True:
        raise _hard_error(
            "RANKING_EVENT_GATE_NOT_READY",
            "event_gate.ranking_ready must be true",
        )
    if event_gate.get("ranking_block_reasons"):
        raise _hard_error(
            "RANKING_EVENT_GATE_NOT_READY",
            "event_gate.ranking_block_reasons must be empty",
        )

    target_date = event_gate.get("target_date")
    strategy_version = config.get("strategy_version")
    config_sha256 = strategy_config_sha256(config)

    inputs = _collect_ranking_inputs(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
    )
    # Event Gate's own rule is that zero PASS candidates blocks Ranking
    # (ranking_ready=false + NO_EVENT_GATE_PASS_CANDIDATE). An event_gate.json
    # that claims ranking_ready=true with an empty PASS set therefore
    # contradicts itself, and an artifact contradiction is always a hard
    # error -- never a DATA_UNAVAILABLE business outcome (which would
    # additionally emit an unexplained, empty reason_codes list).
    if not inputs:
        raise _hard_error(
            "RANKING_EVENT_GATE_NOT_READY",
            "event_gate.ranking_ready is true but there are no PASS candidates to rank",
        )

    payload_candidates: list[dict[str, Any]]
    ranking_status: str
    ranking_complete: bool
    ranked_count = 0

    data_unavailable = any(item.input_status == "DATA_UNAVAILABLE" for item in inputs)

    def _provenance(item: _CandidateInput) -> dict[str, Any]:
        return {
            "turnover_attempt_id": item.turnover_attempt_id,
            "turnover_source_ref": item.turnover_source_ref,
            "tick_size_source_refs": list(item.tick_size_source_refs),
        }

    if data_unavailable:
        ranking_status = "DATA_UNAVAILABLE"
        ranking_complete = False
        payload_candidates = []
        for item in inputs:
            payload_candidates.append(
                {
                    "ticker": item.ticker,
                    "input_status": item.input_status,
                    "reason_codes": _order_reason_codes(item.reason_codes),
                    "provenance": _provenance(item),
                    "feature_values": {
                        # Only turnover can be missing here; the remaining
                        # feature values are always known even for a
                        # DATA_UNAVAILABLE candidate.
                        "turnover_yen": (
                            str(item.turnover_value) if item.turnover_value is not None else None
                        ),
                        "tick_size_yen": str(item.tick_size),
                        "entry_trigger_yen": str(item.entry_trigger),
                        "relative_tick_size": {
                            "numerator_yen": str(item.tick_size),
                            "denominator_yen": str(item.entry_trigger),
                        },
                    },
                    "feature_ranks": None,
                    "rank_points": None,
                    "final_rank": None,
                }
            )
        # Deterministic order even in the fail-closed case.
        payload_candidates.sort(key=lambda item: item["ticker"])
    else:
        ranking_status = "COMPLETE"
        ranking_complete = True
        turnover_ranks = _competition_rank_turnover(inputs)
        relative_tick_ranks = _competition_rank_relative_tick(inputs)

        interim: list[dict[str, Any]] = []
        for item in inputs:
            turnover_rank = turnover_ranks[item.ticker]
            relative_tick_rank = relative_tick_ranks[item.ticker]
            rank_points = turnover_rank + relative_tick_rank
            interim.append(
                {
                    "ticker": item.ticker,
                    "input_status": "VALID",
                    "reason_codes": [],
                    "provenance": _provenance(item),
                    "feature_values": {
                        "turnover_yen": str(item.turnover_value),
                        "tick_size_yen": str(item.tick_size),
                        "entry_trigger_yen": str(item.entry_trigger),
                        "relative_tick_size": {
                            "numerator_yen": str(item.tick_size),
                            "denominator_yen": str(item.entry_trigger),
                        },
                    },
                    "feature_ranks": {
                        "turnover_rank": turnover_rank,
                        "relative_tick_size_rank": relative_tick_rank,
                    },
                    "rank_points": rank_points,
                    "final_rank": None,
                }
            )

        inputs_by_ticker = {item.ticker: item for item in inputs}
        interim.sort(
            key=functools.cmp_to_key(
                lambda a, b: _compare_final_candidate(a, b, inputs_by_ticker)
            )
        )
        for index, candidate in enumerate(interim, start=1):
            candidate["final_rank"] = index
        payload_candidates = interim
        ranked_count = len(payload_candidates)

    input_candidate_tickers = sorted(item.ticker for item in inputs)
    valid_input_count = sum(1 for item in inputs if item.input_status == "VALID")
    data_unavailable_count = sum(1 for item in inputs if item.input_status == "DATA_UNAVAILABLE")
    top_ranked_ticker = None
    if ranking_status == "COMPLETE":
        top_ranked_ticker = next(
            c["ticker"] for c in payload_candidates if c["final_rank"] == 1
        )
    aggregate_reason_codes = _order_reason_codes(
        code for item in inputs for code in item.reason_codes
    )

    return {
        "schema_version": RANKING_SCHEMA_VERSION,
        "ranking_version": RANKING_VERSION,
        "ranking_method": RANKING_METHOD,
        "target_date": target_date,
        "previous_trading_day": event_gate.get("previous_trading_day"),
        "event_gate_as_of": event_gate.get("event_gate_as_of"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": strategy_version,
        "config_sha256": config_sha256,
        "ranking_status": ranking_status,
        "ranking_complete": ranking_complete,
        "reason_codes": aggregate_reason_codes,
        "input_hashes": dict(input_hashes),
        "input_candidate_tickers": input_candidate_tickers,
        "summary": {
            "input_count": len(inputs),
            "valid_input_count": valid_input_count,
            "data_unavailable_count": data_unavailable_count,
            "ranked_count": ranked_count,
            "top_ranked_ticker": top_ranked_ticker,
        },
        "candidates": payload_candidates,
    }
