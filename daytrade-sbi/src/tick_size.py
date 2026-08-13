"""Shared tick-size band resolution.

The JPX tick-size table is a published price-band ladder: the tick that
applies to a price is the tick of the lowest band whose upper bound is >= that
price. TOPIX500 membership selects which ladder applies, because JPX publishes
a finer ladder for TOPIX500 constituents.

This lives in its own module rather than inside Market Data or Ranking so
there is exactly one implementation: Market Data construction and any future
consumer resolve the tick from the *same* parsed band table, never from a
second hand-written ladder.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


class TickSizeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


_NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _decimal(token: Any) -> Decimal | None:
    if token is None:
        return None
    text = str(token).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def band_upper_bound(price_band: Any) -> Decimal | None:
    """The inclusive upper bound of a published price band.

    ``"3000以下"`` -> 3000. A band with no parseable number (the open-ended
    top band) returns ``None`` and is treated as unbounded.
    """
    match = _NUMBER_PATTERN.search(str(price_band or ""))
    if match is None:
        return None
    return _decimal(match.group(0))


def select_tick_size(bands: list[dict[str, Any]], price: Any) -> Decimal:
    """The tick size that applies to ``price`` under this band table.

    Bands are ordered by their upper bound; the first band that still covers
    the price wins, and an unbounded band covers everything above the last
    bounded one. Anything ambiguous is an error rather than a guess.
    """
    price_decimal = _decimal(price)
    if price_decimal is None:
        raise TickSizeError("TICK_SIZE_PRICE_INVALID", f"price is not numeric: {price!r}")
    if not bands:
        raise TickSizeError("TICK_SIZE_TABLE_EMPTY", "tick size band table is empty")

    bounded: list[tuple[Decimal, Decimal]] = []
    unbounded: list[Decimal] = []
    for band in bands:
        tick = _decimal(band.get("tick_size"))
        if tick is None or tick <= 0:
            raise TickSizeError(
                "TICK_SIZE_BAND_INVALID",
                f"band has no positive tick_size: {band!r}",
            )
        upper = band_upper_bound(band.get("price_band"))
        if upper is None:
            unbounded.append(tick)
        else:
            bounded.append((upper, tick))

    if len(unbounded) > 1:
        raise TickSizeError(
            "TICK_SIZE_TABLE_AMBIGUOUS",
            "tick size band table has more than one unbounded band",
        )

    for upper, tick in sorted(bounded, key=lambda pair: pair[0]):
        if price_decimal <= upper:
            return tick

    if unbounded:
        return unbounded[0]
    raise TickSizeError(
        "TICK_SIZE_BAND_NOT_FOUND",
        f"no tick size band covers price {price_decimal}",
    )


__all__ = ["TickSizeError", "band_upper_bound", "select_tick_size"]
