"""Wiring between deterministic Source Acquisition and the run artifacts.

Phase I produced correct acquisition *units* that nothing consumed. This
module is the missing plumbing, and it is deliberately the only plumbing:

* :func:`resolve_stage_candidates` derives each stage's candidate set from the
  **artifacts already on disk**, never from a CLI argument. There is no way
  for an agent to hand a ticker to the network layer.
* :func:`build_market_research` turns Discovery attempts into
  ``market_research.json``.
* :func:`reflect_market_data` turns Stage1 / Stage2 / Turnover attempts into
  ``market_data.json``.

Every numeric value written here comes from a parsed Source Attempt whose raw
bytes are on disk with a verified SHA256. Nothing is transcribed by an AI, and
the agent never hand-edits either artifact.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.candidate_research import init_candidate_research_payload
from src.contracts import validate_json_document
from src.file_io import atomic_write_text
from src.research import merge_discovery_candidates, validate_market_research
from src.source_acquisition import AcquisitionError, AcquisitionResult
from src.tick_size import TickSizeError, select_tick_size


DISCOVERY_ROUTES: tuple[tuple[str, str], ...] = (
    ("VOLUME_RANKING", "YAHOO_JP_VOLUME_RANKING"),
    ("PRICE_GAIN_RANKING", "YAHOO_JP_GAIN_RANKING"),
)

#: Discovery must confirm a full TOP50 from each ranking route.
DISCOVERY_TOP_N = 50

OHLCV_FIELDS = ("open", "high", "low", "close", "volume")

#: OHLCV cross-check policy. Unchanged from the existing Source Ledger policy:
#: Yahoo is PRIMARY, Kabutan is SECONDARY, and a disagreement is a CONFLICT
#: rather than a silent preference for either side.
OHLCV_PRIMARY_SOURCE_ID = "YAHOO_JP_HISTORY"
OHLCV_SECONDARY_SOURCE_ID = "KABUTAN_HISTORY"


class StageWiringError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Candidate derivation (FIX-006): no stage ever takes a ticker from the CLI.
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_stage_candidates(stage: str, run_dir: Path) -> list[str]:
    """The candidate set a stage is authorized to acquire, read from disk.

    * DISCOVERY  -- none: Discovery *creates* the universe.
    * STAGE1     -- ``market_research.discovery_candidates``.
    * STAGE2     -- Stage 1 PASS candidates.
    * TURNOVER   -- the Stage 2 target set (= Stage 1 PASS).
    * EVENT      -- pipeline candidates that are ELIGIBLE / screening PASS.
    """
    run_dir = Path(run_dir)
    if stage == "DISCOVERY":
        return []

    if stage == "STAGE1":
        research = _load(run_dir / "market_research.json")
        if research is None:
            raise StageWiringError(
                "STAGE1_REQUIRES_MARKET_RESEARCH",
                "acquire-stage1-sources requires market_research.json from "
                "acquire-discovery; candidates are never supplied by an agent",
            )
        return _unique(
            str(candidate["ticker"]).strip()
            for candidate in research.get("discovery_candidates", [])
        )

    if stage in {"STAGE2", "TURNOVER"}:
        research = _load(run_dir / "market_research.json")
        if research is None:
            raise StageWiringError(
                "STAGE2_REQUIRES_MARKET_RESEARCH",
                f"{stage} requires market_research.json with Stage 1 results",
            )
        passed = [
            str(entry["ticker"]).strip()
            for entry in research.get("candidate_research", [])
            if entry.get("stage1_status") == "PASSED"
        ]
        if not passed:
            raise StageWiringError(
                "STAGE2_NO_STAGE1_PASS_CANDIDATES",
                f"{stage} has no Stage 1 PASS candidate to acquire for",
            )
        return _unique(passed)

    if stage == "EVENT":
        candidates = _load(run_dir / "candidates.json")
        if candidates is not None:
            eligible = [
                str(entry["ticker"]).strip()
                for entry in candidates.get("candidates", [])
                if entry.get("status") == "ELIGIBLE"
                and entry.get("screening_status") == "PASS"
            ]
            if eligible:
                return _unique(eligible)
        pipeline = _load(run_dir / "candidate_pipeline.json")
        if pipeline is None:
            raise StageWiringError(
                "EVENT_REQUIRES_CANDIDATE_PIPELINE",
                "acquire-event-sources requires candidates.json or "
                "candidate_pipeline.json; candidates are never supplied by an agent",
            )
        eligible = [
            str(entry["ticker"]).strip()
            for entry in pipeline.get("candidates", [])
            if entry.get("pipeline_status") == "ELIGIBLE"
        ]
        if not eligible:
            raise StageWiringError(
                "EVENT_NO_ELIGIBLE_CANDIDATES",
                "no ELIGIBLE / screening PASS candidate to acquire event sources for",
            )
        return _unique(eligible)

    raise StageWiringError("UNKNOWN_ACQUISITION_STAGE", f"unknown stage {stage!r}")


def _unique(values: Any) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


# ---------------------------------------------------------------------------
# Discovery -> market_research.json (FIX-007)
# ---------------------------------------------------------------------------


def _attempt_for(result: AcquisitionResult, source_id: str) -> dict[str, Any] | None:
    for attempt in result.attempts:
        if attempt.get("source_id") == source_id:
            return attempt
    return None


def _attempt_value(attempt: dict[str, Any] | None, field_name: str) -> Any:
    if not isinstance(attempt, dict):
        return None
    for value in attempt.get("values") or []:
        if isinstance(value, dict) and value.get("field_name") == field_name:
            return value.get("value")
    return None


def build_discovery_routes(result: AcquisitionResult) -> list[dict[str, Any]]:
    """Discovery attempts -> the two ``market_research.discovery`` routes."""
    routes: list[dict[str, Any]] = []
    for discovery_type, source_id in DISCOVERY_ROUTES:
        attempt = _attempt_for(result, source_id)
        if attempt is None:
            raise StageWiringError(
                "DISCOVERY_ATTEMPT_MISSING",
                f"discovery produced no attempt for {source_id}",
            )
        status = str(attempt.get("status"))
        source_url = str(attempt.get("url"))
        retrieved_at = str(attempt.get("retrieved_at") or attempt.get("requested_at"))
        rows = _attempt_value(attempt, "ranking_rows") or []
        if status != "FOUND":
            routes.append(
                {
                    "discovery_type": discovery_type,
                    "source_id": source_id,
                    "status": status,
                    "source_url": source_url,
                    "retrieved_at": retrieved_at,
                    "result_count": None,
                    "items": [],
                    "reason": "; ".join(attempt.get("notes") or []) or None,
                }
            )
            continue

        items = [
            {
                "ticker": str(row["ticker"]),
                "company_name": str(row["company_name"]),
                "market": row.get("market"),
                "rank": row.get("rank"),
                "source_url": source_url,
                "retrieved_at": retrieved_at,
            }
            for row in rows[:DISCOVERY_TOP_N]
        ]
        if len(items) != DISCOVERY_TOP_N:
            # A ranking route that did not deliver a full TOP50 is NOT a FOUND
            # route with fewer rows: Discovery either confirms the published
            # TOP50 or it is incomplete. Recording it as FOUND would silently
            # shrink the candidate universe.
            routes.append(
                {
                    "discovery_type": discovery_type,
                    "source_id": source_id,
                    "status": "PARSE_FAILED",
                    "source_url": source_url,
                    "retrieved_at": retrieved_at,
                    "result_count": None,
                    "items": [],
                    "reason": (
                        f"expected TOP{DISCOVERY_TOP_N}, page yielded {len(items)}"
                    ),
                }
            )
            continue
        routes.append(
            {
                "discovery_type": discovery_type,
                "source_id": source_id,
                "status": "FOUND",
                "source_url": source_url,
                "retrieved_at": retrieved_at,
                "result_count": len(items),
                "items": items,
            }
        )
    return routes


def confirm_discovery_top50(routes: list[dict[str, Any]]) -> list[str]:
    """Reason codes for any FOUND route that did not deliver a full TOP50."""
    reasons: list[str] = []
    for route in routes:
        if route["status"] != "FOUND":
            reasons.append(f"{route['discovery_type']}_UNAVAILABLE")
        elif route["result_count"] != DISCOVERY_TOP_N:
            reasons.append(f"{route['discovery_type']}_INCOMPLETE_TOP{DISCOVERY_TOP_N}")
    return reasons


def build_market_research(
    result: AcquisitionResult,
    *,
    research_window: dict[str, Any],
    source_matrix: dict[str, Any],
    research_executed_at: str,
) -> dict[str, Any]:
    """Build and validate ``market_research.json`` from Discovery attempts."""
    routes = build_discovery_routes(result)
    incomplete = confirm_discovery_top50(routes)
    discovery_candidates = merge_discovery_candidates(routes)

    payload = {
        "schema_version": 2,
        "target_date": research_window["target_date"],
        "previous_trading_day": research_window["previous_trading_day"],
        "research_cutoff": research_window["research_cutoff"],
        "research_executed_at": research_executed_at,
        "research_window": research_window["research_window"],
        "market_filter": source_matrix["default_discovery_market_filter"],
        "overall_status": (
            "DISCOVERY_INCOMPLETE" if incomplete else "PIPELINE_INCOMPLETE"
        ),
        "discovery": routes,
        "discovery_candidates": discovery_candidates,
        "candidate_research": [],
        "notes": incomplete,
    }

    # Reuse the existing candidate-research initializer rather than writing a
    # second, parallel implementation of the same skeleton. Running
    # ``init-candidate-research`` afterwards therefore stays valid and
    # idempotent.
    payload = init_candidate_research_payload(payload)

    validate_json_document(payload, "market_research.schema.json")
    outcome = validate_market_research(payload, source_matrix)
    if not outcome.valid:
        raise StageWiringError(
            "MARKET_RESEARCH_INVALID",
            "; ".join(outcome.errors),
        )
    return payload


def write_market_research(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        Path(path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


# ---------------------------------------------------------------------------
# Stage1 / Stage2 / Turnover -> market_data.json (FIX-008)
# ---------------------------------------------------------------------------

#: acquisition field_name -> market_data field name.
STAGE1_FIELD_MAP = {
    "listed_company_name": "company_name",
    "market_segment": "market",
    "trading_unit": "share_unit",
}


def _ledger_value_records(
    result: AcquisitionResult,
    ticker: str,
) -> list[dict[str, Any]]:
    """Every parsed value that applies to ``ticker``, in market_data source shape.

    Candidate-scoped attempts contribute their own values. Globally-fetched
    attempts (the JPX tick-size ladder, for instance) apply to every candidate,
    so their values are attributed to each candidate that consumes them --
    still pointing at the one shared attempt and its one stored raw page.
    """
    records: list[dict[str, Any]] = []
    by_ref = {str(value["source_ref"]): value for value in result.values}
    for attempt in result.attempts:
        candidate = attempt.get("candidate_code")
        if candidate is not None and str(candidate) != ticker:
            continue
        if attempt.get("status") != "FOUND":
            continue
        for value in attempt.get("values") or []:
            if not isinstance(value, dict) or "field_name" not in value:
                continue
            source_ref = str(value.get("source_ref") or "")
            existing = by_ref.get(source_ref)
            records.append(
                {
                    "source_ref": source_ref,
                    "source_id": str(attempt["source_id"]),
                    "source_role": (
                        existing["source_role"] if existing else attempt["source_role"]
                    ),
                    "information_type": attempt["information_type"],
                    "source_status": "FOUND",
                    "source_name": (
                        existing["source_name"]
                        if existing
                        else str(attempt["source_id"])
                    ),
                    "source_url": str(attempt["url"]),
                    "retrieved_at": str(attempt["retrieved_at"]),
                    "trading_date": str(value.get("trading_date")),
                    "ticker": ticker,
                    "field_name": str(value["field_name"]),
                    "value": value.get("value"),
                }
            )
    return records


def _by_source_and_field(
    values: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(value["source_id"]), str(value["field_name"])): value for value in values
    }


def _provenance(
    field_name: str,
    *,
    status: str,
    verified_value: Any,
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
    extra_refs: tuple[str, ...] = (),
    verified_at: str | None,
) -> dict[str, Any]:
    source_refs = [
        record["source_ref"] for record in (primary, secondary) if record is not None
    ]
    source_refs.extend(ref for ref in extra_refs if ref not in source_refs)
    return {
        "field_name": field_name,
        "status": status,
        "verified_value": verified_value,
        "primary_source_ref": primary["source_ref"] if primary else None,
        "secondary_source_ref": secondary["source_ref"] if secondary else None,
        "source_refs": source_refs,
        "verified_at": verified_at,
    }


def _empty_record(ticker: str, trading_date: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "company_name": None,
        "market": None,
        "share_unit": None,
        "data_status": "DATA_UNAVAILABLE",
        "data_status_reasons": [],
        "source_policy_status": "NOT_STARTED",
        "trading_date": trading_date,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": None,
        "previous_close": None,
        "previous_high": None,
        "tick_size": None,
        "turnover": None,
        "sources": [],
        "field_provenance": [],
    }


def _merge_sources(
    record: dict[str, Any],
    values: list[dict[str, Any]],
) -> None:
    existing = {str(item["source_ref"]): item for item in record.get("sources", [])}
    for value in values:
        existing[str(value["source_ref"])] = value
    record["sources"] = list(existing.values())


def _set_provenance(record: dict[str, Any], entry: dict[str, Any]) -> None:
    provenance = [
        item
        for item in record.get("field_provenance", [])
        if item.get("field_name") != entry["field_name"]
    ]
    provenance.append(entry)
    record["field_provenance"] = provenance


def _apply_stage1(record: dict[str, Any], indexed: dict[tuple[str, str], dict[str, Any]]) -> None:
    for source_field, market_field in STAGE1_FIELD_MAP.items():
        found = [
            value
            for (_, field_name), value in indexed.items()
            if field_name == source_field
        ]
        if not found:
            continue
        primary = found[0]
        record[market_field] = primary["value"]
        _set_provenance(
            record,
            _provenance(
                market_field,
                status="VERIFIED",
                verified_value=primary["value"],
                primary=primary,
                secondary=None,
                verified_at=primary.get("retrieved_at"),
            ),
        )


def _apply_stage2(record: dict[str, Any], indexed: dict[tuple[str, str], dict[str, Any]]) -> None:
    """OHLCV via the existing primary/secondary cross-check policy."""
    conflicts: list[str] = []
    for field_name in OHLCV_FIELDS:
        primary = indexed.get((OHLCV_PRIMARY_SOURCE_ID, field_name))
        secondary = indexed.get((OHLCV_SECONDARY_SOURCE_ID, field_name))
        if primary is None and secondary is None:
            continue
        if primary is not None and secondary is not None:
            if _same_number(primary["value"], secondary["value"]):
                status, value = "VERIFIED", primary["value"]
            else:
                status, value = "CONFLICT", None
                conflicts.append(field_name)
        else:
            status = "SINGLE_SOURCE_ONLY"
            value = (primary or secondary)["value"]
        record[field_name] = value
        _set_provenance(
            record,
            _provenance(
                field_name,
                status=status,
                verified_value=value,
                primary=primary,
                secondary=secondary,
                verified_at=(primary or secondary).get("retrieved_at"),
            ),
        )

    if conflicts:
        record["data_status"] = "CONFLICT"
        record["data_status_reasons"] = sorted(
            set(record.get("data_status_reasons", []))
            | {f"OHLCV_CONFLICT:{name}" for name in conflicts}
        )

    _apply_tick_size(record, indexed)


def _apply_tick_size(
    record: dict[str, Any],
    indexed: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Resolve tick_size from the parsed JPX band table + TOPIX500 membership.

    The provenance deliberately references JPX_TICK_SIZE, JPX_TOPIX500 and the
    price input source, which is exactly what
    ``market._validate_tick_size_provenance`` requires; nothing here relaxes
    that rule.
    """
    table_value = None
    for (source_id, field_name), value in indexed.items():
        if source_id == "JPX_TICK_SIZE" and field_name == "tick_size_table":
            table_value = value
    membership = indexed.get(("JPX_TOPIX500", "topix500_membership"))
    price = indexed.get((OHLCV_PRIMARY_SOURCE_ID, "close")) or indexed.get(
        (OHLCV_SECONDARY_SOURCE_ID, "close")
    )
    if table_value is None or membership is None or price is None:
        return
    try:
        tick = select_tick_size(list(table_value["value"]), price["value"])
    except TickSizeError as exc:
        record["tick_size"] = None
        _set_provenance(
            record,
            _provenance(
                "tick_size",
                status="DATA_UNAVAILABLE",
                verified_value=None,
                primary=table_value,
                secondary=membership,
                extra_refs=(str(price["source_ref"]),),
                verified_at=None,
            ),
        )
        record["data_status_reasons"] = sorted(
            set(record.get("data_status_reasons", [])) | {exc.code}
        )
        return

    record["tick_size"] = str(tick)
    _set_provenance(
        record,
        _provenance(
            "tick_size",
            status="VERIFIED",
            verified_value=str(tick),
            primary=table_value,
            secondary=membership,
            extra_refs=(str(price["source_ref"]),),
            verified_at=table_value.get("retrieved_at"),
        ),
    )


def _apply_turnover(record: dict[str, Any], indexed: dict[tuple[str, str], dict[str, Any]]) -> None:
    """Turnover from the already-canonicalized YAHOO_JP_QUOTE value.

    The parser has already converted 売買代金 from its published thousand-yen
    unit into canonical yen; nothing here re-scales it, so the unit can only
    be wrong in one place, which is unit-tested.
    """
    turnover = indexed.get(("YAHOO_JP_QUOTE", "turnover"))
    if turnover is None:
        record["turnover"] = None
        _set_provenance(
            record,
            _provenance(
                "turnover",
                status="DATA_UNAVAILABLE",
                verified_value=None,
                primary=None,
                secondary=None,
                verified_at=None,
            ),
        )
        return
    record["turnover"] = turnover["value"]
    _set_provenance(
        record,
        _provenance(
            "turnover",
            status="VERIFIED",
            verified_value=turnover["value"],
            primary=turnover,
            secondary=None,
            verified_at=turnover.get("retrieved_at"),
        ),
    )


def _same_number(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:  # noqa: BLE001 - non-numeric values compare literally
        return left == right


def reflect_market_data(
    stage: str,
    result: AcquisitionResult,
    *,
    existing: dict[str, Any] | None,
    trading_date: str,
) -> dict[str, Any]:
    """Fold one stage's parsed values into ``market_data.json``.

    Deterministic and idempotent: re-running a stage over the same attempts
    produces the same document. The agent never edits this artifact.
    """
    if stage not in {"STAGE1", "STAGE2", "TURNOVER"}:
        raise StageWiringError(
            "STAGE_NOT_REFLECTED_INTO_MARKET_DATA",
            f"{stage} does not contribute to market_data.json",
        )

    payload = existing or {
        "schema_version": 1,
        "target_date": result.target_date,
        "records": [],
    }
    payload["target_date"] = result.target_date
    records = {str(record["ticker"]): record for record in payload.get("records", [])}

    tickers = _unique(
        str(attempt.get("candidate_code"))
        for attempt in result.attempts
        if attempt.get("candidate_code")
    )
    for ticker in tickers:
        record = records.get(ticker) or _empty_record(ticker, trading_date)
        values = _ledger_value_records(result, ticker)
        _merge_sources(record, values)
        indexed = _by_source_and_field(values)
        if stage == "STAGE1":
            _apply_stage1(record, indexed)
        elif stage == "STAGE2":
            _apply_stage2(record, indexed)
        else:
            _apply_turnover(record, indexed)
        record["source_policy_status"] = "FOUND"
        if record.get("data_status") != "CONFLICT":
            record["data_status"] = _record_data_status(record)
        records[ticker] = record

    payload["records"] = [records[ticker] for ticker in sorted(records)]
    validate_json_document(payload, "market_data.schema.json")
    return payload


def _record_data_status(record: dict[str, Any]) -> str:
    """VERIFIED only once every tradable field is present and verified."""
    required = ("open", "high", "low", "close", "volume", "tick_size")
    provenance = {
        item["field_name"]: item.get("status")
        for item in record.get("field_provenance", [])
    }
    if any(record.get(name) is None for name in required):
        return "DATA_UNAVAILABLE"
    if any(provenance.get(name) == "CONFLICT" for name in required):
        return "CONFLICT"
    if any(provenance.get(name) == "SINGLE_SOURCE_ONLY" for name in required):
        return "SINGLE_SOURCE_ONLY"
    if all(provenance.get(name) == "VERIFIED" for name in required):
        return "VERIFIED"
    return "DATA_UNAVAILABLE"


def write_market_data(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        Path(path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


__all__ = [
    "AcquisitionError",
    "DISCOVERY_TOP_N",
    "StageWiringError",
    "build_discovery_routes",
    "build_market_research",
    "confirm_discovery_top50",
    "reflect_market_data",
    "resolve_stage_candidates",
    "write_market_data",
    "write_market_research",
]
