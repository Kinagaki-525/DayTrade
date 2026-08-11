from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.stage1 import (
    source_attempt_ids_from_payload,
    source_backed_stage1_reject,
    source_refs_from_payload,
)


TIMING_KEYS = (
    "total",
    "calendar_check",
    "discovery",
    "candidate_stage1",
    "candidate_stage2",
    "source_audit",
    "market_validation",
    "screening",
    "ranking",
)


def build_performance_payload(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    timings_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_attempts = source_payload.get("source_attempts", [])
    sources = source_payload.get("sources", [])
    candidate_research = market_research.get("candidate_research", [])
    valid_source_refs = source_refs_from_payload(source_payload)
    valid_source_attempt_ids = source_attempt_ids_from_payload(source_payload)
    candidate_status_counts: dict[str, int] = {}
    for candidate in candidate_pipeline.get("candidates", []):
        status = candidate["pipeline_status"]
        candidate_status_counts[status] = candidate_status_counts.get(status, 0) + 1

    return {
        "schema_version": 1,
        "target_date": market_research["target_date"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "source_request_count": len(source_attempts),
            "adopted_source_count": len(sources),
            "duplicate_source_request_count": _duplicate_source_request_count(
                source_attempts
            ),
            "cache_hit_count": _cache_hit_count(source_attempts),
            "discovery_candidate_count": len(
                market_research.get("discovery_candidates", [])
            ),
            "stage1_candidate_count": _count_stage1_candidates(candidate_research),
            "stage1_rejected_count": sum(
                1
                for research in candidate_research
                if source_backed_stage1_reject(
                    research,
                    valid_source_refs=valid_source_refs,
                    valid_source_attempt_ids=valid_source_attempt_ids,
                )
            ),
            "stage2_candidate_count": _count_stage2_candidates(candidate_research),
            "context_research_candidate_count": sum(
                1
                for research in candidate_research
                if research.get("context_research_status")
                in {"IN_PROGRESS", "COMPLETE", "DATA_UNAVAILABLE"}
            ),
            "candidate_status_counts": candidate_status_counts,
        },
        "timings": _timings(timings_payload),
    }


def _duplicate_source_request_count(source_attempts: list[dict[str, Any]]) -> int:
    seen: dict[tuple[str, str, str], int] = {}
    for attempt in source_attempts:
        key = (
            str(attempt.get("url") or ""),
            str(attempt.get("target_date") or ""),
            str(attempt.get("research_cutoff") or ""),
        )
        seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def _cache_hit_count(source_attempts: list[dict[str, Any]]) -> int:
    return sum(1 for attempt in source_attempts if attempt.get("cache_status") == "HIT")


def _count_stage1_candidates(candidate_research: list[dict[str, Any]]) -> int:
    if not any("stage1_status" in research for research in candidate_research):
        return 0
    return sum(
        1
        for research in candidate_research
        if research.get("stage1_status") not in {None, "NOT_STARTED", "SKIPPED"}
    )


def _count_stage2_candidates(candidate_research: list[dict[str, Any]]) -> int:
    if not any("stage2_status" in research for research in candidate_research):
        return 0
    return sum(
        1
        for research in candidate_research
        if research.get("stage2_status")
        in {"IN_PROGRESS", "COMPLETE", "DATA_UNAVAILABLE"}
    )


def _timings(timings_payload: dict[str, Any] | None) -> dict[str, float | int | None]:
    source = timings_payload.get("timings", timings_payload) if timings_payload else {}
    timings: dict[str, float | int | None] = {}
    for key in TIMING_KEYS:
        value = source.get(key)
        timings[key] = value if isinstance(value, (int, float)) else None
    return timings
