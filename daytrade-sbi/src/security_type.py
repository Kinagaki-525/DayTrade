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


__all__ = [
    "COMMON_STOCK_MARKET_SEGMENTS",
    "DOMESTIC_COMMON_STOCK",
    "FOREIGN_STOCK",
    "SUPPORTED_SECURITY_TYPES",
    "UNSUPPORTED_MARKET_SEGMENT_TYPES",
    "classify_security_type",
    "is_supported_security_type",
]
