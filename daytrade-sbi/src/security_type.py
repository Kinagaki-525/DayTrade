"""Deterministic security-type classification for Discovery candidates.

Classification is **composed from several sources**, never read off one page:
the JPX stock-search result publishes the market segment, and the JPX foreign
stocks listed-issues page decides whether a Prime/Standard/Growth code is a
foreign stock or a domestic common stock. No parser decides this on its own.

The function is fail-closed in both directions:

* a market segment this module does not explicitly recognize yields ``None``
  -- it is never assumed to be a domestic common stock;
* a Prime/Standard/Growth code yields ``None`` whenever the foreign
  listed-issues evidence is unavailable (missing, or PARSE_FAILED), because
  "absent from a list we could not read" is not evidence of anything.

``None`` means *unclassified*, which Stage 1 treats as "cannot pass", never as
a default classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


DOMESTIC_COMMON_STOCK = "DOMESTIC_COMMON_STOCK"
FOREIGN_STOCK = "FOREIGN_STOCK"

#: The market segments in which a listed issue is either a domestic common
#: stock or a foreign stock. Which of the two it is cannot be decided from the
#: segment alone -- JPX lists foreign stocks in these same segments -- so the
#: foreign listed-issues evidence is what separates them.
COMMON_STOCK_MARKET_SEGMENTS: frozenset[str] = frozenset(
    {"プライム", "スタンダード", "グロース"}
)

#: Market segments that identify a product which is not a common stock at all,
#: mapped to the security type they denote. Only segments that are
#: Human-verified or fixture-backed appear here; anything else is
#: unclassified, not guessed into this table.
UNSUPPORTED_MARKET_SEGMENT_TYPES: dict[str, str] = {
    "ETF": "ETF",
    "ETN": "ETN",
    "REIT": "REIT",
    "インフラファンド": "INFRASTRUCTURE_FUND",
}

#: The only security type the current strategy trades. Everything else is a
#: Stage 1 SECURITY_TYPE_UNSUPPORTED reject -- an evidence-backed rejection,
#: not a silent exclusion from the candidate universe.
SUPPORTED_SECURITY_TYPES: frozenset[str] = frozenset({DOMESTIC_COMMON_STOCK})


def classify_security_type(
    *,
    market_segment: str | None,
    candidate_code: str,
    foreign_issue_codes: Iterable[str] | None,
) -> str | None:
    """The candidate's security type, or ``None`` when it cannot be decided.

    ``foreign_issue_codes`` is ``None`` when the JPX foreign listed-issues
    evidence is unavailable for this run; it is a (possibly empty) collection
    only when that page parsed successfully.
    """
    segment = (market_segment or "").strip()
    if not segment:
        return None

    unsupported = UNSUPPORTED_MARKET_SEGMENT_TYPES.get(segment)
    if unsupported is not None:
        return unsupported

    if segment not in COMMON_STOCK_MARKET_SEGMENTS:
        return None

    if foreign_issue_codes is None:
        return None
    if candidate_code in set(foreign_issue_codes):
        return FOREIGN_STOCK
    return DOMESTIC_COMMON_STOCK


def is_supported_security_type(security_type: str | None) -> bool:
    return security_type in SUPPORTED_SECURITY_TYPES


LISTED_COMPANY_SOURCE_ID = "JPX_LISTED_COMPANY"
FOREIGN_STOCK_LIST_SOURCE_ID = "JPX_FOREIGN_STOCK_LIST"
MARKET_SEGMENT_FIELD = "market_segment"
FOREIGN_TRADING_UNITS_FIELD = "foreign_stock_trading_units"


@dataclass(frozen=True)
class SecurityTypeEvidence:
    """A security type recomputed from Source Evidence, with its own refs.

    ``security_type`` is ``None`` whenever the evidence cannot decide it --
    missing, ambiguous or unrecognized. ``source_refs`` holds exactly the
    Source Records the classification actually consulted, so a Stage 1 check
    built from it cannot claim evidence it did not use, nor drop evidence it
    did.
    """

    security_type: str | None
    source_refs: tuple[str, ...]


def _sole_ledger_value(
    source_values,
    *,
    source_id: str,
    field_name: str,
    ticker: str | None,
):
    """The one Canonical Source Record for this (source_id, field, ticker).

    Returns ``None`` for both "absent" and "more than one": two Canonical
    Source Records covering the same field for the same candidate is
    ambiguity, and ambiguity is never resolved by picking one of them -- not
    even when their values happen to agree.
    """
    matching = [
        value
        for value in source_values or ()
        if isinstance(value, dict)
        and str(value.get("source_id") or "") == source_id
        and str(value.get("field_name") or "") == field_name
        and str(value.get("source_ref") or "")
        and (
            value.get("ticker") is None
            if ticker is None
            else str(value.get("ticker") or "") == ticker
        )
    ]
    if len(matching) != 1:
        return None
    return matching[0]


def resolve_security_type_evidence(
    source_values,
    *,
    candidate_code: str,
) -> SecurityTypeEvidence:
    """Recompute a candidate's security type from the Canonical Source Ledger.

    ``source_values`` is ``sources.json``'s ``sources[]`` -- the canonical
    evidence, not the copy of it that ``market_data.json`` carries. Stage 1
    must never trust a ``market_data.security_type`` string on its own, and
    re-deriving it from ``market_data``'s own embedded records would be just
    as circular: a tampered market_data would simply agree with itself. This
    runs the *same* :func:`classify_security_type` over the Source Ledger, so
    the caller can require the two to agree before deciding anything.

    The market segment is read from the candidate-scoped
    ``JPX_LISTED_COMPANY`` record, and the foreign listed-issues map from the
    Global ``JPX_FOREIGN_STOCK_LIST`` record (``ticker=null``) -- the latter
    only when the segment actually needs it.
    """
    listing = _sole_ledger_value(
        source_values,
        source_id=LISTED_COMPANY_SOURCE_ID,
        field_name=MARKET_SEGMENT_FIELD,
        ticker=candidate_code,
    )
    if listing is None:
        return SecurityTypeEvidence(None, ())

    segment = str(listing.get("value") or "").strip()
    listing_ref = str(listing["source_ref"])

    # An explicit non-common-stock segment is decided by the listing evidence
    # alone: the foreign list is irrelevant to it, and requiring it here would
    # break an ETF rejection whenever the foreign page happened to fail.
    if segment in UNSUPPORTED_MARKET_SEGMENT_TYPES:
        return SecurityTypeEvidence(
            UNSUPPORTED_MARKET_SEGMENT_TYPES[segment], (listing_ref,)
        )

    if segment not in COMMON_STOCK_MARKET_SEGMENTS:
        return SecurityTypeEvidence(None, ())

    foreign = _sole_ledger_value(
        source_values,
        source_id=FOREIGN_STOCK_LIST_SOURCE_ID,
        field_name=FOREIGN_TRADING_UNITS_FIELD,
        ticker=None,
    )
    if foreign is None or not isinstance(foreign.get("value"), dict):
        return SecurityTypeEvidence(None, ())

    security_type = classify_security_type(
        market_segment=segment,
        candidate_code=candidate_code,
        foreign_issue_codes=list(foreign["value"]),
    )
    if security_type is None:
        return SecurityTypeEvidence(None, ())
    # Both refs are mandatory here: separating a foreign stock from a domestic
    # common stock consumed the listing segment AND the complete foreign list.
    return SecurityTypeEvidence(
        security_type, (listing_ref, str(foreign["source_ref"]))
    )


__all__ = [
    "COMMON_STOCK_MARKET_SEGMENTS",
    "DOMESTIC_COMMON_STOCK",
    "FOREIGN_STOCK",
    "SUPPORTED_SECURITY_TYPES",
    "UNSUPPORTED_MARKET_SEGMENT_TYPES",
    "SecurityTypeEvidence",
    "classify_security_type",
    "is_supported_security_type",
    "resolve_security_type_evidence",
]
