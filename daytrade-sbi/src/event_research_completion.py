"""Complete an initialized ``event_research.json`` deterministically.

``init-event-research`` produces the candidate skeleton with a null
``event_gate_as_of`` and empty ``selected_attempt_ids``. Filling those in is
pure bookkeeping over the Source Ledger, so it is done by code, not by an
agent: the canonical attempt for each (ticker, source_id) is chosen by the
**existing** ``event_gate.select_canonical_attempt``, which is the same
selection the gate itself will apply. Anything else would let the recorded
provenance disagree with the evidence the gate actually evaluates.

News classifications are copied from the staged Event AI Classification
working file into the existing Event Research contract shape by
:func:`src.event_source_extraction.staged_news_classifications`; nothing is
re-worded or re-keyed here.
"""

from __future__ import annotations

from typing import Any

from src.event_gate import select_canonical_attempt
from src.event_research import SELECTED_ATTEMPT_KEY_SOURCE_IDS


class EventResearchCompletionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def complete_event_research(
    *,
    event_research: dict[str, Any],
    source_payload: dict[str, Any],
    event_gate_as_of: str,
    news_classifications: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return a completed copy of ``event_research``.

    ``event_gate_as_of`` is the run's research cutoff: every attempt requested
    after it is invisible to the gate, which is what makes the gate decision
    reproducible after the fact.
    """
    if not event_gate_as_of:
        raise EventResearchCompletionError(
            "EVENT_GATE_AS_OF_REQUIRED",
            "completing event research requires an event_gate_as_of timestamp",
        )

    attempts = source_payload.get("source_attempts", [])
    target_date = event_research.get("target_date")
    classifications = news_classifications or {}

    completed = dict(event_research)
    completed["event_gate_as_of"] = event_gate_as_of
    candidates: list[dict[str, Any]] = []
    for candidate in event_research.get("candidates", []):
        ticker = str(candidate.get("ticker"))
        selected: dict[str, Any] = {}
        for key, source_id in SELECTED_ATTEMPT_KEY_SOURCE_IDS.items():
            canonical = select_canonical_attempt(
                attempts,
                ticker=ticker,
                source_id=source_id,
                target_date=target_date,
                event_gate_as_of=event_gate_as_of,
            )
            selected[key] = (
                str(canonical["attempt_id"]) if isinstance(canonical, dict) else None
            )
        candidates.append(
            {
                **candidate,
                "selected_attempt_ids": selected,
                "news_classifications": list(classifications.get(ticker, [])),
            }
        )
    completed["candidates"] = candidates
    return completed


__all__ = ["EventResearchCompletionError", "complete_event_research"]
