from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from src.source_matrix import (
    GLOBAL_SOURCE_IDS,
    SOURCE_ROLES,
    SOURCE_STATUSES,
    load_source_matrix,
    source_by_id,
    source_definition_errors,
)
from src.strategy import exact_int, is_tick_aligned, to_decimal


@dataclass(frozen=True)
class SourceRecord:
    source_ref: str | None
    source_id: str | None
    source_role: str | None
    information_type: str | None
    source_status: str | None
    source_name: str | None
    source_url: str | None
    retrieved_at: str | None
    trading_date: str | None
    ticker: str | None
    field_name: str | None
    value: Any

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRecord:
        return cls(
            source_ref=_optional_text(data.get("source_ref")),
            source_id=_optional_text(data.get("source_id")),
            source_role=_optional_text(data.get("source_role")),
            information_type=_optional_text(data.get("information_type")),
            source_status=_optional_text(data.get("source_status")),
            source_name=_optional_text(data.get("source_name")),
            source_url=_optional_text(data.get("source_url")),
            retrieved_at=_optional_text(data.get("retrieved_at")),
            trading_date=_optional_text(data.get("trading_date")),
            ticker=_optional_text(data.get("ticker")),
            field_name=_optional_text(data.get("field_name")),
            value=data.get("value"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketDataRecord:
    ticker: str | None
    company_name: str | None
    market: str | None
    share_unit: int | None
    security_type: str | None
    source_policy_status: str | None
    data_status: str | None
    data_status_reasons: tuple[str, ...]
    trading_date: str | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None
    previous_close: Decimal | None
    previous_high: Decimal | None
    tick_size: Decimal | None
    turnover: Decimal | None
    spread: Decimal | None
    earnings_scheduled: bool | None
    special_disclosures: bool | None
    sources: tuple[SourceRecord, ...]
    field_provenance: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketDataRecord:
        raw_sources = data.get("sources")
        if raw_sources is None:
            sources: tuple[SourceRecord, ...] = ()
        elif not isinstance(raw_sources, list) or any(
            not isinstance(item, dict) for item in raw_sources
        ):
            raise ValueError("market data sources must be an array of objects")
        else:
            sources = tuple(SourceRecord.from_dict(item) for item in raw_sources)
        raw_field_provenance = data.get("field_provenance", [])
        if not isinstance(raw_field_provenance, list) or any(
            not isinstance(item, dict) for item in raw_field_provenance
        ):
            raise ValueError("market data field_provenance must be an array of objects")
        return cls(
            ticker=_optional_text(data.get("ticker")),
            company_name=_optional_text(data.get("company_name")),
            market=_optional_text(data.get("market")),
            share_unit=_optional_int(data.get("share_unit"), "share_unit"),
            security_type=_optional_text(data.get("security_type")),
            source_policy_status=_optional_text(data.get("source_policy_status")),
            data_status=_optional_text(data.get("data_status")),
            data_status_reasons=tuple(
                str(reason) for reason in data.get("data_status_reasons", [])
            ),
            trading_date=_optional_text(data.get("trading_date")),
            open=_optional_decimal(data.get("open")),
            high=_optional_decimal(data.get("high")),
            low=_optional_decimal(data.get("low")),
            close=_optional_decimal(data.get("close")),
            volume=_optional_int(data.get("volume"), "volume"),
            previous_close=_optional_decimal(data.get("previous_close")),
            previous_high=_optional_decimal(data.get("previous_high")),
            tick_size=_optional_decimal(data.get("tick_size")),
            turnover=_optional_decimal(data.get("turnover")),
            spread=_optional_decimal(data.get("spread")),
            earnings_scheduled=_optional_bool(data.get("earnings_scheduled")),
            special_disclosures=_optional_bool(data.get("special_disclosures")),
            sources=sources,
            field_provenance=tuple(dict(item) for item in raw_field_provenance),
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        return _json_ready(result)


def load_market_data(path: str | Path) -> tuple[str | None, list[MarketDataRecord]]:
    with Path(path).open("r", encoding="utf-8") as json_file:
        payload = json.load(json_file, parse_float=Decimal, parse_int=Decimal)
    if not isinstance(payload, dict):
        raise ValueError("market_data.json must contain an object")
    if payload.get("schema_version") != 1:
        raise ValueError("market_data.json schema_version must be 1")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("market_data.json records must be an array")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Every market_data.json record must be an object")
    target_date = _optional_text(payload.get("target_date"))
    if target_date is None:
        raise ValueError("market_data.json target_date is required")
    try:
        parsed_target_date = _parse_iso_date(target_date)
    except ValueError as exc:
        raise ValueError("market_data.json target_date must use YYYY-MM-DD") from exc
    parsed_records = [MarketDataRecord.from_dict(record) for record in records]
    for record in parsed_records:
        if record.trading_date is None:
            continue
        try:
            record_date = _parse_iso_date(record.trading_date)
        except ValueError:
            continue
        if record_date >= parsed_target_date:
            raise ValueError("market data trading_date must be before target_date")
    return target_date, parsed_records


def _parse_iso_date(value: str) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None or str(value).strip() == "" else to_decimal(value)


def _optional_int(value: Any, name: str) -> int | None:
    return None if value is None or str(value).strip() == "" else exact_int(value, name)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Expected boolean or null, got {value!r}")
    return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


REQUIRED_TRADE_FIELDS = (
    "ticker",
    "company_name",
    "market",
    "share_unit",
    "data_status",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "previous_close",
    "previous_high",
    "tick_size",
)
#: DIRECT SOURCE FIELDS: a literal, same-named Source Record is mandatory.
#: No field_provenance chain may substitute for it -- see
#: ``_has_verified_provenance``'s removal and FIX-R2-001C section 4.
DIRECT_NUMERIC_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "previous_close",
    "previous_high",
)

#: MAPPED SOURCE FIELDS: no literal same-named Source Record exists (the
#: page's own parsed field_name differs), but exactly one explicit source
#: contract -- not a generic provenance bypass -- authorizes the mapping.
#: ``comparison`` decides how the referenced source's value must match the
#: market_data field's value.
MappedFieldContract = tuple[str, str, str, str]  # (source_id, field_name, source_role, comparison)
MAPPED_FIELD_CONTRACTS: dict[str, MappedFieldContract] = {
    "company_name": ("JPX_LISTED_COMPANY", "listed_company_name", "PRIMARY", "text"),
    "market": ("JPX_LISTED_COMPANY", "market_segment", "PRIMARY", "text"),
    "share_unit": ("JPX_TRADING_UNIT", "trading_unit", "PRIMARY", "numeric"),
}

OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
#: previous_close / previous_high follow the identical Yahoo-PRIMARY /
#: Kabutan-SECONDARY dual-source policy as OHLCV_FIELDS -- see
#: _validate_ohlcv_source_policy.
PREVIOUS_DAY_FIELDS = ("previous_close", "previous_high")
#: Source Ledger Integrity (every sources.json source traces to a real
#: source_attempts[].values[] Attempt Value) applies from this ledger
#: schema_version onward; see validate_source_ledger.
SOURCES_SCHEMA_VERSION_FOR_INTEGRITY_CHECK = 3

DATA_AVAILABLE_STATUS = "VERIFIED"
DATA_BLOCKING_STATUSES = {
    "DATA_UNAVAILABLE",
    "CONFLICT",
    "SINGLE_SOURCE_ONLY",
    "SOURCE_POLICY_UNDEFINED",
}
PRICE_INPUT_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "previous_close",
    "previous_high",
)


@dataclass(frozen=True)
class MarketValidationResult:
    valid_for_trade: bool
    data_status: str | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "valid_for_trade": self.valid_for_trade,
            "data_status": self.data_status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def validate_market_data(
    record: MarketDataRecord,
    source_matrix: dict[str, Any] | None = None,
) -> MarketValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    effective_source_matrix = (
        source_matrix if source_matrix is not None else load_source_matrix()
    )

    for field_name in REQUIRED_TRADE_FIELDS:
        if getattr(record, field_name) is None:
            errors.append(f"Missing required market data: {field_name}")

    if record.data_status in DATA_BLOCKING_STATUSES:
        errors.append(f"Market data status is not verified: {record.data_status}")
    elif record.data_status is not None and record.data_status != DATA_AVAILABLE_STATUS:
        errors.append(f"Unknown market data status: {record.data_status}")
    if record.source_policy_status == "SOURCE_POLICY_UNDEFINED":
        errors.append("Source policy is undefined for this candidate")
    elif (
        record.source_policy_status is not None
        and record.source_policy_status not in SOURCE_STATUSES
    ):
        errors.append(f"Unknown source policy status: {record.source_policy_status}")

    if record.trading_date is not None and not _is_iso_date(record.trading_date):
        errors.append("trading_date must use YYYY-MM-DD")
    if record.share_unit is not None and record.share_unit != 100:
        errors.append("share_unit must be 100 for the current strategy")

    price_fields = (
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "previous_high",
        "tick_size",
    )
    for field_name in price_fields:
        value = getattr(record, field_name)
        if value is not None and value <= 0:
            errors.append(f"{field_name} must be greater than 0")
    if record.volume is not None and record.volume < 0:
        errors.append("volume must be greater than or equal to 0")

    ohlc = (record.open, record.high, record.low, record.close)
    if all(value is not None for value in ohlc):
        assert record.open is not None
        assert record.high is not None
        assert record.low is not None
        assert record.close is not None
        if record.low > record.high:
            errors.append("low must not exceed high")
        if not record.low <= record.open <= record.high:
            errors.append("open must be between low and high")
        if not record.low <= record.close <= record.high:
            errors.append("close must be between low and high")

    if record.tick_size is not None and record.tick_size > 0:
        for field_name in price_fields[:-1]:
            value = getattr(record, field_name)
            if value is not None and not is_tick_aligned(value, record.tick_size):
                errors.append(f"{field_name} is not aligned to tick_size")

    if not record.sources:
        errors.append("At least one traceable source is required")
    for index, source in enumerate(record.sources):
        errors.extend(_validate_source(source, record, index, effective_source_matrix))
    errors.extend(_validate_field_provenance(record))

    for field_name in DIRECT_NUMERIC_FIELDS:
        field_sources = [
            source for source in record.sources if source.field_name == field_name
        ]
        if not field_sources:
            errors.append(f"Missing source for market data field: {field_name}")
            continue
        record_value = getattr(record, field_name)
        source_values = [_as_decimal(source.value) for source in field_sources]
        valid_values = [value for value in source_values if value is not None]
        if record_value is not None and record_value not in valid_values:
            errors.append(f"No source value matches market data field: {field_name}")
        if len(set(valid_values)) > 1:
            errors.append(f"Conflicting source values for market data field: {field_name}")

    for field_name in MAPPED_FIELD_CONTRACTS:
        errors.extend(_validate_mapped_field(record, field_name))

    errors.extend(_validate_ohlcv_source_policy(record))

    if record.turnover is None:
        warnings.append("turnover is not available")
    if record.spread is None:
        warnings.append("spread is not available")
    if record.earnings_scheduled is None:
        warnings.append("earnings schedule is not confirmed")
    if record.special_disclosures is None:
        warnings.append("special disclosures are not confirmed")

    return MarketValidationResult(
        not errors,
        record.data_status,
        tuple(errors),
        tuple(warnings),
    )


def _validate_source(
    source: SourceRecord,
    record: MarketDataRecord,
    index: int,
    source_matrix: dict[str, Any],
) -> list[str]:
    prefix = f"sources[{index}]"
    errors: list[str] = []
    #: A Global Source's Attempt Value legitimately carries ticker=None (see
    #: source_matrix.GLOBAL_SOURCE_IDS): it is one canonical Source Record
    #: shared by every candidate that consumes it, not a candidate-scoped
    #: value that happens to be missing its ticker.
    is_global_source = source.source_id in GLOBAL_SOURCE_IDS
    for field_name in (
        "source_ref",
        "source_id",
        "source_role",
        "information_type",
        "source_status",
        "source_name",
        "source_url",
        "retrieved_at",
        "trading_date",
        "field_name",
    ) + (() if is_global_source else ("ticker",)):
        if getattr(source, field_name) is None:
            errors.append(f"{prefix}.{field_name} is required")
    if source.value is None:
        errors.append(f"{prefix}.value is required")
    errors.extend(
        source_definition_errors(
            source_id=source.source_id,
            source_role=source.source_role,
            information_type=source.information_type,
            source_matrix=source_matrix,
            prefix=prefix,
        )
    )
    if source.source_role is not None and source.source_role not in SOURCE_ROLES:
        errors.append(f"{prefix}.source_role is not a known role")
    if source.source_status is not None and source.source_status != "FOUND":
        errors.append(f"{prefix}.source_status must be FOUND for saved values")
    if source.source_url is not None:
        parsed = urlparse(source.source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"{prefix}.source_url must be an HTTP(S) URL")
    if source.retrieved_at is not None and not _is_timezone_aware_datetime(
        source.retrieved_at
    ):
        errors.append(f"{prefix}.retrieved_at must be an ISO 8601 datetime with timezone")
    if source.trading_date is not None and not _is_iso_date(source.trading_date):
        errors.append(f"{prefix}.trading_date must use YYYY-MM-DD")
    if not (is_global_source and source.ticker is None):
        # Global Sources are permitted (not required) to carry ticker=None
        # -- see source_matrix.GLOBAL_SOURCE_IDS -- but pre-existing frozen
        # regression evidence built before that normalization still
        # attributes them to a specific candidate, which remains valid as
        # long as it agrees with the record it is attached to.
        if record.ticker is not None and source.ticker != record.ticker:
            errors.append(f"{prefix}.ticker does not match the market record")
    if record.trading_date is not None and source.trading_date != record.trading_date:
        errors.append(f"{prefix}.trading_date does not match the market record")
    return errors


def _tick_size_has_jpx_primary_source(record: MarketDataRecord) -> bool:
    """Whether tick_size's field_provenance traces to a real JPX_TICK_SIZE PRIMARY source.

    tick_size is a Derived Value (JPX_TICK_SIZE band table + JPX_TOPIX500
    membership + a price input, resolved deterministically), so there is no
    literal source record whose own field_name is "tick_size" -- that would
    require fabricating a Source Record that never came out of a parser.
    Traceability instead runs through field_provenance[tick_size].source_refs,
    which ``_validate_tick_size_provenance`` already requires to reference a
    genuine JPX_TICK_SIZE PRIMARY source record (under that source's own
    field_name, e.g. "tick_size_table").
    """
    source_by_ref = {
        source.source_ref: source
        for source in record.sources
        if source.source_ref is not None
    }
    for provenance in record.field_provenance:
        if _optional_text(provenance.get("field_name")) != "tick_size":
            continue
        source_refs = provenance.get("source_refs", [])
        if not isinstance(source_refs, list):
            continue
        for source_ref in source_refs:
            source = source_by_ref.get(str(source_ref))
            if (
                source is not None
                and source.source_id == "JPX_TICK_SIZE"
                and source.source_role == "PRIMARY"
                and source.source_status == "FOUND"
            ):
                return True
    return False


def _validate_ohlcv_source_policy(record: MarketDataRecord) -> list[str]:
    errors: list[str] = []
    for field_name in OHLCV_FIELDS + PREVIOUS_DAY_FIELDS:
        field_sources = [
            source
            for source in record.sources
            if source.field_name == field_name and source.source_status == "FOUND"
        ]
        has_primary = any(
            source.source_id == "YAHOO_JP_HISTORY"
            and source.source_role == "PRIMARY"
            for source in field_sources
        )
        has_secondary = any(
            source.source_id == "KABUTAN_HISTORY"
            and source.source_role == "SECONDARY"
            for source in field_sources
        )
        if has_primary and not has_secondary:
            errors.append(f"SINGLE_SOURCE_ONLY for OHLCV field: {field_name}")
        elif not has_primary:
            errors.append(f"Missing primary OHLCV source for field: {field_name}")
        elif not has_secondary:
            errors.append(f"Missing secondary OHLCV source for field: {field_name}")

    if not _tick_size_has_jpx_primary_source(record):
        errors.append("Missing JPX primary source for tick_size")
    return errors


def _validate_mapped_field(record: MarketDataRecord, field_name: str) -> list[str]:
    """MAPPED SOURCE FIELD: no literal same-named Source Record is expected
    (the source page's own parsed field_name differs), but exactly one
    explicit contract in ``MAPPED_FIELD_CONTRACTS`` -- never a generic
    "any VERIFIED provenance will do" bypass -- authorizes the substitution.

    Requires: a VERIFIED field_provenance entry for ``field_name``; a
    non-empty ``source_refs``; at least one of those refs resolving to a
    real, FOUND record source whose source_id/field_name/role exactly match
    the contract (a reference to some other, unrelated source is rejected,
    not merely ignored); and that matching source's value equal to the
    market_data field's value under the contract's comparison rule.
    """
    contract_source_id, contract_field_name, contract_role, comparison = (
        MAPPED_FIELD_CONTRACTS[field_name]
    )
    record_value = getattr(record, field_name)

    # A literal same-named Source Record (frozen pre-mapping regression
    # fixtures predate the STAGE1_FIELD_MAP rename and were built this way)
    # is strictly stronger evidence than the mapped-contract chain below, so
    # it is still accepted directly -- this is a specific, single-field
    # equivalence, never the removed generic "any VERIFIED provenance"
    # bypass, and current production data never produces one.
    literal_sources = [
        source for source in record.sources if source.field_name == field_name
    ]
    if literal_sources:
        errors: list[str] = []
        source_values = [
            source.value for source in literal_sources if source.value is not None
        ]
        if record_value is not None:
            if comparison == "text":
                if not any(str(value) == str(record_value) for value in source_values):
                    errors.append(
                        f"No source value matches market data field: {field_name}"
                    )
            else:
                record_decimal = _as_decimal(record_value)
                if not any(
                    _as_decimal(value) == record_decimal for value in source_values
                ):
                    errors.append(
                        f"No source value matches market data field: {field_name}"
                    )
        return errors

    provenance = next(
        (
            item
            for item in record.field_provenance
            if _optional_text(item.get("field_name")) == field_name
        ),
        None,
    )
    if provenance is None or provenance.get("status") != "VERIFIED":
        return [f"Missing source for market data field: {field_name}"]

    source_refs = provenance.get("source_refs", [])
    if not isinstance(source_refs, list) or not source_refs:
        return [f"field_provenance[{field_name}].source_refs is required"]

    source_by_ref = {
        source.source_ref: source
        for source in record.sources
        if source.source_ref is not None
    }
    referenced = [source_by_ref.get(str(ref)) for ref in source_refs]
    matching = [
        source
        for source in referenced
        if source is not None
        and source.source_id == contract_source_id
        and source.field_name == contract_field_name
        and source.source_role == contract_role
        and source.source_status == "FOUND"
    ]
    if not matching:
        return [
            f"field_provenance[{field_name}] does not reference a "
            f"{contract_source_id}.{contract_field_name} ({contract_role}) source"
        ]

    errors: list[str] = []
    if record_value is not None:
        if comparison == "text":
            if not any(str(source.value) == str(record_value) for source in matching):
                errors.append(f"No source value matches market data field: {field_name}")
        else:
            record_decimal = _as_decimal(record_value)
            if not any(
                _as_decimal(source.value) == record_decimal for source in matching
            ):
                errors.append(f"No source value matches market data field: {field_name}")
    return errors


def _validate_field_provenance(record: MarketDataRecord) -> list[str]:
    errors: list[str] = []
    source_by_ref = {
        source.source_ref: source
        for source in record.sources
        if source.source_ref is not None
    }
    for index, provenance in enumerate(record.field_provenance):
        prefix = f"field_provenance[{index}]"
        field_name = _optional_text(provenance.get("field_name"))
        status = _optional_text(provenance.get("status"))
        verified_value = provenance.get("verified_value")
        source_refs = provenance.get("source_refs", [])
        if field_name is None:
            errors.append(f"{prefix}.field_name is required")
            continue
        if not isinstance(source_refs, list):
            errors.append(f"{prefix}.source_refs must be an array")
            source_refs = []
        missing_refs = [
            str(source_ref)
            for source_ref in source_refs
            if str(source_ref) not in source_by_ref
        ]
        if missing_refs:
            errors.append(
                f"{prefix}.source_refs are missing from market data sources: "
                + ", ".join(missing_refs)
            )
        if status == "VERIFIED":
            if verified_value is None:
                errors.append(f"{prefix}.verified_value is required when VERIFIED")
            record_value = getattr(record, field_name, None)
            if record_value is not None and _as_decimal(verified_value) is not None:
                if _as_decimal(verified_value) != record_value:
                    errors.append(
                        f"{prefix}.verified_value does not match market data field"
                    )
            if field_name in OHLCV_FIELDS + PREVIOUS_DAY_FIELDS:
                primary_ref = _optional_text(provenance.get("primary_source_ref"))
                secondary_ref = _optional_text(provenance.get("secondary_source_ref"))
                if primary_ref is None or secondary_ref is None:
                    errors.append(
                        f"{prefix}.OHLCV VERIFIED requires primary and secondary source refs"
                    )
                else:
                    primary = source_by_ref.get(primary_ref)
                    secondary = source_by_ref.get(secondary_ref)
                    if primary is None or secondary is None:
                        errors.append(
                            f"{prefix}.OHLCV source refs must exist in market data sources"
                        )
                    elif _source_values_conflict(primary.value, secondary.value):
                        errors.append(
                            f"{prefix}.OHLCV primary and secondary source values conflict"
                        )
        elif status == "CONFLICT" and verified_value is not None:
            errors.append(f"{prefix}.verified_value must be null when CONFLICT")
    errors.extend(_validate_tick_size_provenance(record, source_by_ref))
    return errors


def _validate_tick_size_provenance(
    record: MarketDataRecord,
    source_by_ref: dict[str, SourceRecord],
) -> list[str]:
    if record.tick_size is None:
        return []
    tick_provenances = [
        provenance
        for provenance in record.field_provenance
        if _optional_text(provenance.get("field_name")) == "tick_size"
    ]
    if not tick_provenances:
        return ["field_provenance for tick_size is required"]

    provenance = tick_provenances[0]
    prefix = "field_provenance[tick_size]"
    source_refs = provenance.get("source_refs", [])
    if not isinstance(source_refs, list):
        return [f"{prefix}.source_refs must be an array"]
    sources = [
        source
        for source_ref in source_refs
        if (source := source_by_ref.get(str(source_ref))) is not None
    ]
    errors: list[str] = []
    if provenance.get("status") != "VERIFIED":
        errors.append(f"{prefix}.status must be VERIFIED for a tradable tick_size")
    if not any(
        source.source_id == "JPX_TICK_SIZE" and source.source_role == "PRIMARY"
        for source in sources
    ):
        errors.append(f"{prefix} must reference JPX_TICK_SIZE primary source")
    if not any(
        source.source_id == "JPX_TOPIX500" and source.source_role == "PRIMARY"
        for source in sources
    ):
        errors.append(f"{prefix} must reference JPX_TOPIX500 primary membership source")
    if not any(
        source.field_name in PRICE_INPUT_FIELDS
        and source.source_id in {"YAHOO_JP_HISTORY", "KABUTAN_HISTORY", "JPX_DAILY_REPORT"}
        for source in sources
    ):
        errors.append(f"{prefix} must reference a price input source")
    return errors


def _source_values_conflict(left: Any, right: Any) -> bool:
    left_decimal = _as_decimal(left)
    right_decimal = _as_decimal(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal != right_decimal
    return left != right


def _is_iso_date(value: str) -> bool:
    try:
        _parse_iso_date(value)
    except ValueError:
        return False
    return True


def _is_timezone_aware_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _as_decimal(value: object) -> Decimal | None:
    try:
        return to_decimal(value)  # type: ignore[arg-type]
    except (ValueError, InvalidOperation):
        return None


@dataclass(frozen=True)
class SourceLedgerValidationResult:
    valid: bool
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"valid": self.valid, "errors": list(self.errors)}


def validate_source_ledger(
    market_target_date: str,
    records: Iterable[MarketDataRecord],
    source_payload: dict[str, Any],
    source_matrix: dict[str, Any] | None = None,
    source_base_dir: Path | None = None,
) -> SourceLedgerValidationResult:
    errors: list[str] = []
    effective_source_matrix = (
        source_matrix if source_matrix is not None else load_source_matrix()
    )
    if source_payload["target_date"] != market_target_date:
        errors.append("sources.json target_date does not match market_data.json")

    ledger_sources = source_payload["sources"]
    # Source Ledger Integrity applies from schema_version 3 (Source Ledger v3,
    # where every Attempt Value keeps the source_ref its Source Record later
    # reuses) onward. Pre-v3 fixtures -- frozen historical regression runs
    # predating that convention -- are grandfathered rather than rewritten;
    # they are exercised for their own (unchanged) invariants elsewhere.
    check_attempt_integrity = int(source_payload.get("schema_version") or 0) >= SOURCES_SCHEMA_VERSION_FOR_INTEGRITY_CHECK
    expected_by_ref = (
        _expected_source_records(source_payload.get("source_attempts", []), effective_source_matrix)
        if check_attempt_integrity
        else None
    )
    for index, source in enumerate(ledger_sources):
        errors.extend(
            source_definition_errors(
                source_id=_optional_text(source.get("source_id")),
                source_role=_optional_text(source.get("source_role")),
                information_type=_optional_text(source.get("information_type")),
                source_matrix=effective_source_matrix,
                prefix=f"sources[{index}]",
            )
        )
        errors.extend(
            _source_url_template_errors(
                _optional_text(source.get("source_id")),
                _optional_text(source.get("source_url")),
                effective_source_matrix,
                f"sources[{index}]",
            )
        )
        # Source Ledger Integrity (FIX-R2-001C section 6): every
        # sources.json source must not merely share a source_ref with some
        # Attempt Value -- it must be canonically IDENTICAL to the Source
        # Record that Attempt Value deterministically implies (value,
        # field_name, source_id, source_url, retrieved_at, trading_date,
        # ticker, source_name all included). A same-ref entry that was
        # tampered with after acquisition -- not just a fabricated,
        # unbacked ref -- is rejected the same way a missing one is.
        if check_attempt_integrity:
            source_ref = _optional_text(source.get("source_ref"))
            expected = expected_by_ref.get(source_ref)
            if expected is None:
                errors.append(
                    f"sources[{index}].source_ref does not trace to any "
                    "source_attempts[].values[] Attempt Value"
                )
            elif _canonical_source(source) != _canonical_source(expected):
                errors.append(
                    f"sources[{index}] does not match the Source Record its "
                    "Attempt Value implies (tampered after acquisition)"
                )

    canonical_ledger = [_canonical_source(source) for source in ledger_sources]
    duplicate_count = len(canonical_ledger) - len(set(canonical_ledger))
    if duplicate_count:
        errors.append(f"sources.json contains {duplicate_count} duplicate source record(s)")

    ledger_set = set(canonical_ledger)
    for record in records:
        for index, source in enumerate(record.sources):
            if _canonical_source(source.as_dict()) not in ledger_set:
                errors.append(
                    "market_data.json source is missing from sources.json: "
                    f"ticker={record.ticker}, index={index}"
                )
    for index, attempt in enumerate(source_payload.get("source_attempts", [])):
        if not _optional_text(attempt.get("attempt_id")):
            errors.append(f"source_attempts[{index}].attempt_id is required")
        if attempt.get("target_date") != market_target_date:
            errors.append(f"source_attempts[{index}].target_date does not match market_data.json")
        status = attempt.get("status")
        if status not in SOURCE_STATUSES:
            errors.append(f"source_attempts[{index}].status is not known")
        result_count = attempt.get("result_count")
        if status == "FOUND" and result_count is None:
            errors.append(f"source_attempts[{index}].result_count is required when FOUND")
        if status != "FOUND" and result_count is not None:
            errors.append(
                f"source_attempts[{index}].result_count must be null unless status is FOUND"
            )
        role = attempt.get("source_role")
        if role not in SOURCE_ROLES:
            errors.append(f"source_attempts[{index}].source_role is not known")
        errors.extend(
            source_definition_errors(
                source_id=_optional_text(attempt.get("source_id")),
                source_role=_optional_text(role),
                information_type=_optional_text(attempt.get("information_type")),
                criticality=_optional_text(attempt.get("criticality")),
                source_matrix=effective_source_matrix,
                prefix=f"source_attempts[{index}]",
            )
        )
        errors.extend(
            _source_url_template_errors(
                _optional_text(attempt.get("source_id")),
                _optional_text(attempt.get("url")),
                effective_source_matrix,
                f"source_attempts[{index}]",
            )
        )
        errors.extend(
            _source_page_path_errors(
                attempt.get("source_page_path"),
                source_base_dir,
                f"source_attempts[{index}]",
            )
        )
        errors.extend(
            _source_page_hash_errors(
                attempt,
                source_base_dir,
                f"source_attempts[{index}]",
            )
        )
    return SourceLedgerValidationResult(not errors, tuple(errors))


def _source_url_template_errors(
    source_id: str | None,
    source_url: str | None,
    source_matrix: dict[str, Any],
    prefix: str,
) -> list[str]:
    if source_id is None or source_url is None:
        return []
    definition = source_by_id(source_matrix).get(source_id)
    if definition is None:
        return []
    url_template = _optional_text(definition.get("url_template"))
    if url_template is None:
        return []
    if _url_matches_template(source_url, url_template):
        return []
    return [f"{prefix}.url does not match source_matrix.yaml url_template for {source_id}"]


def _url_matches_template(source_url: str, url_template: str) -> bool:
    if "{" not in url_template:
        return (
            source_url == url_template
            or source_url.startswith(url_template.rstrip("/") + "/")
            or source_url.startswith(url_template + "?")
            or source_url.startswith(url_template + "#")
        )
    pattern = re.escape(url_template)
    pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/?#]+", pattern)
    if url_template.endswith("/"):
        pattern += r".*"
    else:
        pattern += r"([?#].*)?"
    return re.fullmatch(pattern, source_url) is not None


def _source_page_path_errors(
    source_page_path: Any,
    source_base_dir: Path | None,
    prefix: str,
) -> list[str]:
    if source_page_path is None or source_base_dir is None:
        return []
    relative_path = Path(str(source_page_path))
    base = source_base_dir.resolve()
    target = (base / relative_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return [f"{prefix}.source_page_path must stay under sources.json directory"]
    if not target.is_file():
        return [f"{prefix}.source_page_path does not exist: {source_page_path}"]
    return []


def _source_page_hash_errors(
    attempt: dict[str, Any],
    source_base_dir: Path | None,
    prefix: str,
) -> list[str]:
    """Re-verify stored raw evidence against its recorded SHA256.

    This is the raw-evidence gate for Source Ledger v3: the bytes a
    deterministic parser read must still be, byte for byte, the bytes the
    transport wrote. A mismatch is a hard error -- never a silent re-fetch,
    never a silent repair.
    """
    expected = attempt.get("source_page_sha256")
    source_page_path = attempt.get("source_page_path")
    if expected is None or source_page_path is None or source_base_dir is None:
        return []
    target = (source_base_dir.resolve() / Path(str(source_page_path))).resolve()
    if not target.is_file():
        return []  # already reported by _source_page_path_errors
    data = target.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    errors: list[str] = []
    if actual != expected:
        errors.append(
            f"SOURCE_PAGE_HASH_MISMATCH: {prefix}.source_page_path {source_page_path} "
            f"expected {expected}, found {actual}"
        )
    recorded_size = attempt.get("source_page_size_bytes")
    if recorded_size is not None and recorded_size != len(data):
        errors.append(
            f"SOURCE_PAGE_SIZE_MISMATCH: {prefix}.source_page_size_bytes "
            f"{recorded_size} != {len(data)}"
        )
    return errors


def audit_official_ohlcv(
    records: Iterable[MarketDataRecord],
    source_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    ledger_sources = source_payload["sources"]
    results: list[dict[str, Any]] = []
    for record in records:
        differences: list[dict[str, Any]] = []
        audit_sources = [
            source
            for source in ledger_sources
            if source.get("ticker") == record.ticker
            and source.get("source_id") == "JPX_DAILY_REPORT"
            and source.get("source_role") == "AUDIT"
        ]
        if not audit_sources:
            results.append(
                {
                    "ticker": record.ticker,
                    "audit_status": "NOT_YET_AVAILABLE",
                    "differences": [],
                }
            )
            continue
        for field_name in OHLCV_FIELDS:
            official_sources = [
                source for source in audit_sources if source.get("field_name") == field_name
            ]
            if not official_sources:
                differences.append(
                    {
                        "field_name": field_name,
                        "saved_value": getattr(record, field_name),
                        "official_value": None,
                    }
                )
                continue
            official_value = _as_decimal(official_sources[0].get("value"))
            saved_value = getattr(record, field_name)
            if official_value != saved_value:
                differences.append(
                    {
                        "field_name": field_name,
                        "saved_value": saved_value,
                        "official_value": official_sources[0].get("value"),
                    }
                )
        results.append(
            {
                "ticker": record.ticker,
                "audit_status": "CONFLICT" if differences else "FOUND",
                "differences": differences,
            }
        )
    return results


def _expected_source_records(
    source_attempts: list[dict[str, Any]],
    source_matrix: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Reconstruct the Source Record every FOUND Attempt Value implies.

    A Source Ledger entry is trustworthy only if it is exactly what its own
    Attempt already parsed and stored -- never a value re-typed, re-dated,
    re-attributed, or otherwise edited after the fact. ``source_name`` comes
    from the Source Matrix (never the attempt), matching how the real
    acquisition pipeline builds it.
    """
    definitions = source_by_id(source_matrix)
    expected: dict[str, dict[str, Any]] = {}
    for attempt in source_attempts:
        if not isinstance(attempt, dict) or attempt.get("status") != "FOUND":
            continue
        source_id = _optional_text(attempt.get("source_id"))
        definition = definitions.get(source_id, {}) if source_id else {}
        source_name = definition.get("source_name") or source_id
        for value in attempt.get("values") or []:
            if not isinstance(value, dict):
                continue
            source_ref = _optional_text(value.get("source_ref"))
            if source_ref is None:
                continue
            expected[source_ref] = {
                "source_ref": source_ref,
                "source_id": source_id,
                "source_role": _optional_text(attempt.get("source_role")),
                "information_type": _optional_text(attempt.get("information_type")),
                "source_status": "FOUND",
                "source_name": source_name,
                "source_url": _optional_text(attempt.get("url")),
                "retrieved_at": _optional_text(attempt.get("retrieved_at")),
                "trading_date": _optional_text(value.get("trading_date")),
                "ticker": _optional_text(value.get("ticker")),
                "field_name": _optional_text(value.get("field_name")),
                "value": value.get("value"),
            }
    return expected


def _canonical_source(source: dict[str, Any]) -> str:
    normalized = dict(source)
    normalized["value"] = _canonical_value(normalized.get("value"))
    return json.dumps(
        _json_ready(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if not number.is_finite():
        return value
    return {"numeric": format(number.normalize(), "f")}
