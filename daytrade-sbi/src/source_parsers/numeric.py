"""Deterministic numeric extraction helpers.

No AI component may produce any market numeric field. Every number that ever
reaches ``sources.json`` / ``market_data.json`` is produced here, from the raw
bytes of a stored source page, by pure Python.

Ambiguity is always fatal: when a page yields more than one distinct candidate
value for a field, the result is ``PARSE_FAILED``. We never "pick the first",
"pick the max", or "pick the last".
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from src.ranking import canonical_turnover_yen


class ParseFailed(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(f"PARSE_FAILED: {message}")
        self.code = "PARSE_FAILED"
        self.message = message


#: digits with strict 3-digit comma grouping (matches Ranking's raw_value rule)
GROUPED_INTEGER_PATTERN = re.compile(r"^\d{1,3}(,\d{3})*$|^\d+$")
GROUPED_DECIMAL_PATTERN = re.compile(r"^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$")

_TAG_PATTERN = re.compile(r"<[^>]*>")
_WHITESPACE_PATTERN = re.compile(r"[\s　]+")


def strip_tags(html: str) -> str:
    return _TAG_PATTERN.sub(" ", html)


def normalize_text(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def parse_grouped_integer(token: str) -> Decimal:
    """Strictly parse an integer token. No naive comma stripping."""
    if not isinstance(token, str) or not GROUPED_INTEGER_PATTERN.match(token.strip()):
        raise ParseFailed(f"not a well-formed integer token: {token!r}")
    try:
        return Decimal(token.strip().replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise ParseFailed(f"not a well-formed integer token: {token!r}") from exc


def parse_grouped_decimal(token: str) -> Decimal:
    if not isinstance(token, str) or not GROUPED_DECIMAL_PATTERN.match(token.strip()):
        raise ParseFailed(f"not a well-formed decimal token: {token!r}")
    try:
        return Decimal(token.strip().replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise ParseFailed(f"not a well-formed decimal token: {token!r}") from exc


def unambiguous(candidates: list[str], *, field_name: str) -> str:
    """Return the single candidate, or fail.

    Zero candidates -> PARSE_FAILED (missing).
    Two or more *distinct* candidates -> PARSE_FAILED (ambiguous). Repeated
    identical spellings of the same value are not ambiguity.
    """
    if not candidates:
        raise ParseFailed(f"{field_name}: no value found on the source page")
    distinct = sorted(set(candidates))
    if len(distinct) > 1:
        raise ParseFailed(
            f"{field_name}: ambiguous, {len(distinct)} distinct candidate values "
            f"({', '.join(repr(item) for item in distinct)})"
        )
    return distinct[0]


def canonical_turnover_from_raw(raw_value: str, raw_unit: str) -> Decimal:
    """Canonicalize a turnover raw value to JPY.

    The unit is fixed per parser; an unexpected unit is ``PARSE_FAILED``
    rather than a guessed conversion. The THOUSAND_YEN conversion itself is
    delegated to Ranking's existing shared helper so the two can never drift.
    """
    if raw_unit != "THOUSAND_YEN":
        raise ParseFailed(f"unsupported turnover raw_unit: {raw_unit!r}")
    try:
        return canonical_turnover_yen(raw_value)
    except Exception as exc:  # noqa: BLE001 - normalize into PARSE_FAILED
        raise ParseFailed(f"malformed turnover raw_value: {raw_value!r}") from exc
