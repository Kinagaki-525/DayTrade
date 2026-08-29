from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src.config import strategy_config_sha256
from src.stage1 import (
    source_attempt_ids_from_payload,
    source_ids_by_evidence_id_from_payload,
    source_refs_from_payload,
    stage1_contract_errors,
    stage1_reject_evidence_error,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
RUN_ARTIFACT_ALLOWLIST = {
    "strategy_snapshot.yaml",
    "event_gate.json",
    "event_research.json",
    "research_window.json",
    "market_research.json",
    "market_research_validation.json",
    "sources.json",
    "market_data.json",
    "market_validation.json",
    "candidates.json",
    "candidate_pipeline.json",
    "performance.json",
    "research.md",
    "recommendation.json",
    "recommendation.md",
    "risk_result.json",
    "report.md",
    "official_ohlcv_audit.json",
    "execution_result.json",
    "source_pages",
    "network_requests",
    "ranking.json",
    "selection.json",
}

#: ``runs/<date>/working/`` is the **Non-Business Sidecar**.
#:
#: Things legitimately land in a run directory without being Business
#: Artifacts: the Production Launcher's Runtime Security Attestation, the
#: Event AI Classification's local scratch output, and whatever further
#: runtime evidence a later contract adds. None is produced by a canonical
#: ``src.cli`` stage, none carries ``config_sha256`` / ``strategy_version``,
#: and none is part of any Trust Chain -- so none may be admitted to
#: :data:`RUN_ARTIFACT_ALLOWLIST`, where every entry is a Business Artifact
#: the Production Verifier hash-links.
#:
#: Treating the sidecar as an unexpected artifact was equally wrong: it made
#: every real Production Nightly Run ``INVALID_RUN`` for carrying the very
#: attestation that proves it ran under the Managed Policy.
#:
#: So ``working/`` is recognised as a directory and **skipped**, and the
#: Business Verifier does not look inside it at all. It deliberately does not
#: enumerate the sidecar's filenames: adding a new piece of runtime security
#: evidence must never, by itself, turn a good business run into
#: ``INVALID_RUN``. Validating that evidence is the Production Run Archive's
#: responsibility (``src/production_archive.py``), which classifies
#: ``runtime_security.json`` separately from the Business Artifact chain.
#:
#: Only the sidecar's *identity* is checked here, and strictly: a regular file
#: named ``working``, a symlink named ``working`` (never followed), and any
#: other directory name such as ``working2`` are all unexpected artifacts.
WORKING_SIDECAR_DIR = "working"


def load_json_document(path: str | Path, schema_name: str) -> dict[str, Any]:
    document_path = Path(path)
    with document_path.open("r", encoding="utf-8") as json_file:
        payload = json.load(
            json_file,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_non_standard_number,
        )
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {document_path}")
    validate_json_document(payload, schema_name)
    return payload


def validate_json_document(payload: dict[str, Any], schema_name: str) -> None:
    schema_path = SCHEMAS_DIR / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=_error_sort_key)
    if not errors:
        return

    details = []
    for error in errors:
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        details.append(f"{location}: {error.message}")
    raise ValueError(f"{schema_name} validation failed: {'; '.join(details)}")


def validate_event_research_inputs(
    *,
    candidate_pipeline: dict[str, Any],
    candidates: dict[str, Any],
    config: dict[str, Any],
) -> None:
    if config.get("config_schema_version") not in (4, 5, 6):
        raise ValueError("config_schema_version must be 4, 5, or 6 for event research/gate")
    if candidates.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("candidates strategy_version does not match --config")
    if candidates.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("candidates config_sha256 does not match --config")
    if candidate_pipeline.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("candidate_pipeline strategy_version does not match --config")
    _require_equal(candidate_pipeline, candidates, "target_date", "candidate_pipeline/candidates")
    if candidate_pipeline.get("summary", {}).get("pipeline_complete") is not True:
        raise ValueError("candidate_pipeline is not complete")
    if candidate_pipeline.get("summary", {}).get("screening_complete") is not True:
        raise ValueError("candidate_pipeline screening_complete is not true")


def validate_event_research_contract(
    *,
    event_research: dict[str, Any],
    source_payload: dict[str, Any],
) -> None:
    from src.event_research import event_research_contract_errors

    _check_sources_uniqueness(source_payload)
    errors = event_research_contract_errors(event_research, source_payload=source_payload)
    if errors:
        raise ValueError("event_research contract violation: " + "; ".join(errors))


def validate_event_gate_inputs(
    *,
    event_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    candidates: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, Any],
) -> None:
    if config.get("config_schema_version") not in (4, 5, 6):
        raise ValueError("config_schema_version must be 4, 5, or 6 for event research/gate")
    _require_equal(event_research, candidate_pipeline, "target_date", "event_research/candidate_pipeline")
    _require_equal(event_research, candidates, "target_date", "event_research/candidates")
    if candidates.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("candidates strategy_version does not match --config")
    if candidates.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("candidates config_sha256 does not match --config")
    if event_research.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("event_research strategy_version does not match --config")
    if event_research.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("event_research config_sha256 does not match --config")
    if candidate_pipeline.get("summary", {}).get("pipeline_complete") is not True:
        raise ValueError("candidate_pipeline is not complete")
    if candidate_pipeline.get("summary", {}).get("screening_complete") is not True:
        raise ValueError("candidate_pipeline screening_complete is not true")

    from src.event_research import hard_screening_pass_tickers

    pass_tickers = set(hard_screening_pass_tickers(candidates))
    event_gate_input_tickers = set(event_research.get("event_gate_input_tickers", []))
    if pass_tickers != event_gate_input_tickers:
        raise ValueError("event_gate_input_tickers does not match Hard Screening PASS candidates")

    candidate_tickers = [str(c.get("ticker")) for c in event_research.get("candidates", [])]
    if set(candidate_tickers) != event_gate_input_tickers:
        raise ValueError("event_research candidates do not match event_gate_input_tickers")
    if len(candidate_tickers) != len(set(candidate_tickers)):
        raise ValueError("event_research candidate ticker uniqueness failed")

    if not event_research.get("event_gate_as_of"):
        raise ValueError("event_research event_gate_as_of must be set before building event_gate")

    _check_sources_uniqueness(source_payload)

    from src.event_research import event_research_contract_errors

    errors = event_research_contract_errors(event_research, source_payload=source_payload)
    if errors:
        raise ValueError("event_research contract violation: " + "; ".join(errors))

    required_hashes = {
        "event_research_sha256",
        "candidate_pipeline_sha256",
        "candidates_sha256",
        "sources_sha256",
        "strategy_snapshot_sha256",
    }
    if set(input_hashes.keys()) != required_hashes:
        raise ValueError("input_hashes mismatch")


def validate_event_gate_output_contract(
    *,
    event_gate: dict[str, Any],
    event_research: dict[str, Any],
) -> None:
    if event_gate.get("event_gate_as_of") != event_research.get("event_gate_as_of"):
        raise ValueError("event_gate event_gate_as_of does not match event_research")
    if set(event_gate.get("event_gate_input_tickers", [])) != set(
        event_research.get("event_gate_input_tickers", [])
    ):
        raise ValueError("event_gate event_gate_input_tickers does not match event_research")
    for candidate in event_gate.get("candidates", []):
        for rule in candidate.get("rule_evaluations", []):
            if rule.get("result") == "REJECT" and not rule.get("evidence_ids"):
                raise ValueError(
                    f"{candidate.get('ticker')}: REJECT rule {rule.get('rule_id')} requires evidence_ids"
                )
            if rule.get("result") == "DATA_UNAVAILABLE" and not rule.get("reason_codes"):
                raise ValueError(
                    f"{candidate.get('ticker')}: DATA_UNAVAILABLE rule {rule.get('rule_id')} requires reason_codes"
                )


def validate_ranking_preconditions(
    *,
    event_gate: dict[str, Any],
    candidates: dict[str, Any],
    market_data: dict[str, Any],
    source_payload: dict[str, Any],
    source_matrix: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, Any],
    source_base_dir: Path | None = None,
) -> None:
    if config.get("config_schema_version") not in (5, 6):
        raise ValueError("RANKING_CONFIG_SCHEMA_VERSION_INVALID: config_schema_version must be 5 or 6 for build-ranking")
    if "ranking" not in config:
        raise ValueError(
            "RANKING_CONFIG_BLOCK_MISSING: config is missing the ranking block required for build-ranking"
        )

    # CRITICAL: never trust event_gate.json's own top-level flags
    # (event_gate_complete / ranking_ready / ranking_block_reasons) at face
    # value. A schema-valid but internally-contradictory event_gate.json
    # must be rejected before any of those flags are relied upon below.
    _verify_event_gate_internal_consistency(event_gate, source_payload=source_payload)

    if event_gate.get("event_gate_complete") is not True:
        raise ValueError(
            "RANKING_EVENT_GATE_NOT_COMPLETE: event_gate.event_gate_complete must be true for build-ranking"
        )
    if event_gate.get("ranking_ready") is not True:
        raise ValueError("RANKING_EVENT_GATE_NOT_READY: event_gate.ranking_ready must be true for build-ranking")
    if event_gate.get("ranking_block_reasons"):
        raise ValueError(
            "RANKING_EVENT_GATE_BLOCKED: event_gate.ranking_block_reasons must be empty for build-ranking"
        )

    if event_gate.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("RANKING_STRATEGY_VERSION_MISMATCH: event_gate strategy_version does not match --config")
    if event_gate.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("RANKING_CONFIG_SHA256_MISMATCH: event_gate config_sha256 does not match --config")
    if candidates.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("RANKING_STRATEGY_VERSION_MISMATCH: candidates strategy_version does not match --config")
    if candidates.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("RANKING_CONFIG_SHA256_MISMATCH: candidates config_sha256 does not match --config")
    if not event_gate.get("target_date"):
        raise ValueError("RANKING_TARGET_DATE_MISSING: event_gate.target_date is required")
    if not event_gate.get("previous_trading_day"):
        raise ValueError("RANKING_PREVIOUS_TRADING_DAY_MISSING: event_gate.previous_trading_day is required")

    if candidates.get("target_date") != event_gate.get("target_date"):
        raise ValueError(
            "RANKING_TARGET_DATE_MISMATCH: candidates.json target_date does not match event_gate.json target_date"
        )
    if market_data.get("target_date") != event_gate.get("target_date"):
        raise ValueError(
            "RANKING_TARGET_DATE_MISMATCH: market_data.json target_date does not match event_gate.json target_date"
        )

    _check_sources_uniqueness(source_payload)
    _check_market_data_uniqueness(market_data)

    from src.ranking import pass_candidate_tickers
    from src.source_matrix import validate_source_matrix

    result = validate_source_matrix(source_matrix)
    if not result.valid:
        raise ValueError(
            "RANKING_SOURCE_MATRIX_INVALID: Source matrix validation failed: " + "; ".join(result.errors)
        )

    # CRITICAL-1: mandatory Source Ledger / Market Data provenance
    # validation. Parse market_data.json into the project's established
    # MarketDataRecord objects and run the same validate_source_ledger() /
    # validate_market_data() checks used elsewhere in the pipeline -- never
    # trust a bare data_status=="VERIFIED" string in isolation. Mirrors
    # _load_market_bundle()'s pattern in cli.py: pass source_base_dir so
    # referenced source page files are also verified to exist on disk.
    from src.market import MarketDataRecord, validate_market_data, validate_source_ledger

    records = [
        MarketDataRecord.from_dict(record) for record in market_data.get("records", [])
    ]
    ledger_result = validate_source_ledger(
        market_data.get("target_date"),
        records,
        source_payload,
        source_matrix=source_matrix,
        source_base_dir=source_base_dir,
    )
    if not ledger_result.valid:
        raise ValueError(
            "RANKING_SOURCE_LEDGER_INVALID: " + "; ".join(ledger_result.errors)
        )

    pass_tickers = set(pass_candidate_tickers(event_gate))
    for record in records:
        if record.ticker not in pass_tickers:
            continue
        validation_result = validate_market_data(record, source_matrix)
        if not validation_result.valid_for_trade:
            raise ValueError(
                f"RANKING_MARKET_DATA_INVALID: {record.ticker}: "
                + "; ".join(validation_result.errors)
            )

    required_hashes = {
        "event_gate_sha256",
        "candidates_sha256",
        "market_data_sha256",
        "sources_sha256",
        "source_matrix_sha256",
        "strategy_snapshot_sha256",
    }
    if set(input_hashes.keys()) != required_hashes:
        raise ValueError("RANKING_INPUT_HASHES_INVALID: input_hashes mismatch")

    # Hash-chain re-verification: candidates.json, sources.json, and the
    # strategy snapshot fed into ranking must be byte-identical to what
    # event_gate recorded, so a candidates/sources/config swap between the
    # Event Gate run and the ranking run is detected and rejected rather
    # than silently accepted.
    event_gate_hashes = event_gate.get("input_hashes") or {}
    for ranking_key, event_gate_key in (
        ("candidates_sha256", "candidates_sha256"),
        ("sources_sha256", "sources_sha256"),
        ("strategy_snapshot_sha256", "strategy_snapshot_sha256"),
    ):
        recorded = event_gate_hashes.get(event_gate_key)
        current = input_hashes.get(ranking_key)
        if not recorded or recorded != current:
            raise ValueError(
                "RANKING_HASH_CHAIN_MISMATCH: "
                f"event_gate.input_hashes.{event_gate_key} does not match the "
                f"{ranking_key} of the file supplied to build-ranking "
                "(candidates.json/sources.json/strategy snapshot must be "
                "identical to what Event Gate used)"
            )


def _check_market_data_uniqueness(market_data: dict[str, Any]) -> None:
    tickers: set[str] = set()
    for record in market_data.get("records", []):
        ticker = record.get("ticker")
        if isinstance(ticker, str):
            if ticker in tickers:
                raise ValueError(f"RANKING_DUPLICATE_MARKET_DATA_TICKER: duplicate market_data record ticker: {ticker}")
            tickers.add(ticker)


def _verify_event_gate_internal_consistency(
    event_gate: dict[str, Any],
    *,
    source_payload: dict[str, Any],
) -> None:
    """Independent re-verification of event_gate.json's internal
    consistency, run on the Ranking side before any of event_gate.json's
    own top-level flags (event_gate_complete / ranking_ready /
    ranking_block_reasons / summary counts) are trusted.

    The recomputation itself lives in event_gate.py as the public, pure
    validator ``validate_event_gate_integrity()`` -- next to the logic it
    mirrors -- so Ranking never reaches into event_gate.py's private
    helpers and never reimplements the rule-aggregation / summary /
    block-reason derivations. This only maps its findings onto Ranking's
    RANKING_* error-code namespace.
    """
    from src.event_gate import validate_event_gate_integrity

    errors = validate_event_gate_integrity(event_gate, source_payload)
    if errors:
        raise ValueError(
            "; ".join(f"RANKING_{error}" for error in errors)
        )


def validate_ranking_output_contract(
    *,
    ranking: dict[str, Any],
    event_gate: dict[str, Any],
    candidates: dict[str, Any],
    market_data: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> None:
    from src.ranking import build_ranking, pass_candidate_tickers

    if ranking.get("target_date") != event_gate.get("target_date"):
        raise ValueError("RANKING_TARGET_DATE_MISMATCH: ranking target_date does not match event_gate")
    if ranking.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("RANKING_STRATEGY_VERSION_MISMATCH: ranking strategy_version does not match --config")
    if ranking.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("RANKING_CONFIG_SHA256_MISMATCH: ranking config_sha256 does not match --config")
    if ranking.get("ranking_status") not in ("COMPLETE", "DATA_UNAVAILABLE"):
        raise ValueError("RANKING_STATUS_INVALID: ranking_status must be COMPLETE or DATA_UNAVAILABLE")

    pass_tickers = set(pass_candidate_tickers(event_gate))
    ranking_ticker_list = [c.get("ticker") for c in ranking.get("candidates", [])]
    ranking_tickers = set(ranking_ticker_list)
    if len(ranking_ticker_list) != len(ranking_tickers):
        raise ValueError(
            "RANKING_DUPLICATE_CANDIDATE_TICKER: ranking.candidates contains a duplicate ticker"
        )
    if pass_tickers != ranking_tickers:
        raise ValueError(
            "RANKING_CANDIDATE_SET_MISMATCH: ranking.candidates does not match event_gate PASS candidates"
        )

    summary = ranking.get("summary") or {}
    if ranking.get("ranking_status") == "COMPLETE":
        if ranking.get("ranking_complete") is not True:
            raise ValueError(
                "RANKING_COMPLETE_FLAG_MISMATCH: ranking_complete must be true when ranking_status is COMPLETE"
            )
        for candidate in ranking.get("candidates", []):
            if candidate.get("final_rank") is None:
                raise ValueError(
                    "RANKING_FINAL_RANK_MISSING: COMPLETE ranking requires a final_rank for every candidate"
                )
    else:
        if ranking.get("ranking_complete") is not False:
            raise ValueError(
                "RANKING_COMPLETE_FLAG_MISMATCH: ranking_complete must be false when ranking_status is "
                "DATA_UNAVAILABLE"
            )
        for candidate in ranking.get("candidates", []):
            if candidate.get("final_rank") is not None or candidate.get("rank_points") is not None:
                raise ValueError(
                    "RANKING_DATA_UNAVAILABLE_FIELDS_NOT_NULL: DATA_UNAVAILABLE ranking must null out "
                    "final_rank/rank_points for every candidate"
                )
        if summary.get("ranked_count") != 0 or summary.get("top_ranked_ticker") is not None:
            raise ValueError(
                "RANKING_DATA_UNAVAILABLE_SUMMARY_INVALID: DATA_UNAVAILABLE ranking requires "
                "summary.ranked_count==0 and summary.top_ranked_ticker==null"
            )

    if set(ranking.get("input_candidate_tickers", [])) != pass_tickers:
        raise ValueError(
            "RANKING_INPUT_CANDIDATE_TICKERS_MISMATCH: ranking.input_candidate_tickers does not match "
            "event_gate PASS candidates"
        )

    # Semantic field recomputation: previous_trading_day and
    # event_gate_as_of are carried through verbatim from event_gate.json,
    # so recompute-and-compare against event_gate rather than merely
    # schema-shape-checking their presence. generated_at is intentionally
    # exempt (wall-clock at write time).
    if ranking.get("previous_trading_day") != event_gate.get("previous_trading_day"):
        raise ValueError(
            "RANKING_PREVIOUS_TRADING_DAY_MISMATCH: ranking.previous_trading_day does not match "
            "event_gate.previous_trading_day"
        )
    if ranking.get("event_gate_as_of") != event_gate.get("event_gate_as_of"):
        raise ValueError(
            "RANKING_EVENT_GATE_AS_OF_MISMATCH: ranking.event_gate_as_of does not match "
            "event_gate.event_gate_as_of"
        )

    # Recomputation-based verification: the output must exactly match a
    # fresh call to build_ranking with the same inputs (no mutation, no
    # non-determinism). This also re-verifies summary fields,
    # top_ranked_ticker, and the full ordered reason_codes set by
    # recomputation rather than schema-shape/presence alone.
    recomputed = build_ranking(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
        input_hashes=ranking.get("input_hashes", {}),
    )
    if recomputed.get("candidates") != ranking.get("candidates"):
        raise ValueError(
            "RANKING_CANDIDATES_RECOMPUTE_MISMATCH: ranking.candidates does not match recomputed ranking result"
        )
    if recomputed.get("ranking_status") != ranking.get("ranking_status"):
        raise ValueError(
            "RANKING_STATUS_RECOMPUTE_MISMATCH: ranking_status does not match recomputed ranking result"
        )
    if recomputed.get("summary") != summary:
        raise ValueError(
            "RANKING_SUMMARY_RECOMPUTE_MISMATCH: ranking.summary does not match recomputed ranking result"
        )
    if recomputed.get("input_candidate_tickers") != ranking.get("input_candidate_tickers"):
        raise ValueError(
            "RANKING_INPUT_CANDIDATE_TICKERS_RECOMPUTE_MISMATCH: ranking.input_candidate_tickers does not "
            "match recomputed ranking result"
        )
    if recomputed.get("reason_codes") != ranking.get("reason_codes"):
        raise ValueError(
            "RANKING_REASON_CODES_RECOMPUTE_MISMATCH: ranking.reason_codes does not match recomputed "
            "ranking result (including canonical order)"
        )
    if recomputed.get("previous_trading_day") != ranking.get("previous_trading_day"):
        raise ValueError(
            "RANKING_PREVIOUS_TRADING_DAY_RECOMPUTE_MISMATCH: ranking.previous_trading_day does not match "
            "recomputed ranking result"
        )
    if recomputed.get("event_gate_as_of") != ranking.get("event_gate_as_of"):
        raise ValueError(
            "RANKING_EVENT_GATE_AS_OF_RECOMPUTE_MISMATCH: ranking.event_gate_as_of does not match "
            "recomputed ranking result"
        )


def _check_sources_uniqueness(source_payload: dict[str, Any]) -> None:
    attempt_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for attempt in source_payload.get("source_attempts", []):
        if not isinstance(attempt, dict):
            continue
        attempt_id = attempt.get("attempt_id")
        if isinstance(attempt_id, str):
            if attempt_id in attempt_ids:
                raise ValueError(f"duplicate source_attempt attempt_id: {attempt_id}")
            attempt_ids.add(attempt_id)
        values = attempt.get("values")
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            if isinstance(evidence_id, str):
                if evidence_id in evidence_ids:
                    raise ValueError(f"duplicate evidence_id: {evidence_id}")
                evidence_ids.add(evidence_id)


def validate_recommendation_candidate_link(
    recommendation: dict[str, Any],
    candidates: dict[str, Any],
) -> None:
    _require_equal(recommendation, candidates, "target_date", "recommendation/candidates")
    _require_equal(
        recommendation,
        candidates,
        "strategy_version",
        "recommendation/candidates",
    )
    _require_equal(
        recommendation,
        candidates,
        "config_sha256",
        "recommendation/candidates",
    )
    if recommendation["decision"] != "TRADE":
        return

    ticker = recommendation["ticker"]
    matches = [
        candidate
        for candidate in candidates["candidates"]
        if candidate["ticker"] == ticker and candidate["status"] == "ELIGIBLE"
    ]
    if len(matches) != 1:
        raise ValueError(
            "TRADE recommendation must reference exactly one ELIGIBLE candidate"
        )


def validate_recommendation_sources(
    recommendation: dict[str, Any],
    source_payload: dict[str, Any],
) -> None:
    _require_equal(
        recommendation,
        source_payload,
        "target_date",
        "recommendation/sources",
    )
    ledger_urls = {source["source_url"] for source in source_payload["sources"]}
    missing_urls = [
        url for url in recommendation["source_urls"] if url not in ledger_urls
    ]
    if missing_urls:
        raise ValueError(
            "recommendation source_urls are missing from sources.json: "
            + ", ".join(missing_urls)
        )
    attempt_urls = {
        attempt["url"]
        for attempt in source_payload.get("source_attempts", [])
        if attempt.get("url")
    }
    known_urls = ledger_urls | attempt_urls
    missing_status_urls = [
        status["url"]
        for status in recommendation.get("source_statuses", [])
        if status.get("url") not in known_urls
    ]
    if missing_status_urls:
        raise ValueError(
            "recommendation source_statuses are missing from sources.json/source_attempts: "
            + ", ".join(missing_status_urls)
        )


def validate_recommendation_pipeline_link(
    recommendation: dict[str, Any],
    candidate_pipeline: dict[str, Any],
) -> None:
    _require_equal(
        recommendation,
        candidate_pipeline,
        "target_date",
        "recommendation/candidate_pipeline",
    )
    _require_equal(
        recommendation,
        candidate_pipeline,
        "strategy_version",
        "recommendation/candidate_pipeline",
    )
    _require_equal(
        recommendation,
        candidate_pipeline,
        "config_sha256",
        "recommendation/candidate_pipeline",
    )
    summary = candidate_pipeline.get("summary", {})
    if summary.get("pipeline_complete") is not True:
        raise ValueError("candidate_pipeline is not complete")
    recommendation_summary = recommendation.get("pipeline_summary")
    if not isinstance(recommendation_summary, dict):
        raise ValueError("recommendation pipeline_summary is required")
    if recommendation_summary != summary:
        for field_name in sorted(set(recommendation_summary) | set(summary)):
            if recommendation_summary.get(field_name) != summary.get(field_name):
                raise ValueError(
                    "recommendation/candidate_pipeline pipeline_summary "
                    f"{field_name} does not match"
                )
        raise ValueError(
            "recommendation/candidate_pipeline pipeline_summary does not match"
        )
    # Note: candidate_pipeline.json is upstream of Ranking and its own
    # schema always carries summary.ranking_complete == false; Ranking
    # completion is tracked separately in ranking.json (and now
    # selection.json), never here. A TRADE recommendation therefore does
    # NOT require candidate_pipeline.summary.ranking_complete to be true.


def validate_research_report_inputs(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    performance: dict[str, Any],
) -> None:
    validate_performance_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=source_payload,
    )
    _require_equal(
        market_research,
        performance,
        "target_date",
        "market_research/performance",
    )


def validate_daily_report_inputs(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    performance: dict[str, Any],
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> None:
    validate_research_report_inputs(
        market_research=market_research,
        candidate_pipeline=candidate_pipeline,
        source_payload=source_payload,
        performance=performance,
    )
    validate_recommendation_pipeline_link(recommendation, candidate_pipeline)
    validate_recommendation_risk_link(recommendation, risk_result)


def validate_candidate_pipeline_inputs(
    *,
    market_research: dict[str, Any],
    market_target_date: str,
    candidates: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> None:
    expected_target_date = market_research.get("target_date")
    if market_target_date != expected_target_date:
        raise ValueError("market_data target_date does not match market_research")
    _require_equal(
        market_research,
        candidates,
        "target_date",
        "market_research/candidates",
    )
    _require_equal(
        market_research,
        source_payload,
        "target_date",
        "market_research/sources",
    )
    if candidates.get("strategy_version") != config.get("strategy_version"):
        raise ValueError("candidates strategy_version does not match --config")
    if candidates.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError("candidates config_sha256 does not match --config")
    valid_source_refs = source_refs_from_payload(source_payload)
    valid_source_attempt_ids = source_attempt_ids_from_payload(source_payload)
    source_ids_by_evidence_id = source_ids_by_evidence_id_from_payload(source_payload)
    for research in market_research.get("candidate_research", []):
        contract_errors = stage1_contract_errors(research)
        if contract_errors:
            raise ValueError(contract_errors[0])
        evidence_error = stage1_reject_evidence_error(
            research,
            valid_source_refs=valid_source_refs,
            valid_source_attempt_ids=valid_source_attempt_ids,
            source_ids_by_evidence_id=source_ids_by_evidence_id,
            source_values=source_payload.get("sources", []),
        )
        if evidence_error is not None:
            raise ValueError(evidence_error)


def validate_performance_inputs(
    *,
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
) -> None:
    _require_equal(
        market_research,
        candidate_pipeline,
        "target_date",
        "market_research/candidate_pipeline",
    )
    _require_equal(
        market_research,
        source_payload,
        "target_date",
        "market_research/sources",
    )


def validate_run_artifact_allowlist(run_dir: str | Path) -> tuple[str, ...]:
    path = Path(run_dir)
    unexpected: list[str] = []
    for item in path.iterdir():
        if item.name in RUN_ARTIFACT_ALLOWLIST:
            continue
        if (
            item.name == WORKING_SIDECAR_DIR
            and item.is_dir()
            and not item.is_symlink()
        ):
            # The Non-Business Sidecar: a real directory, never a symlink.
            # Skipped whole -- its contents are not Business Artifacts and are
            # deliberately not enumerated here. See WORKING_SIDECAR_DIR.
            continue
        unexpected.append(item.name)
    return tuple(sorted(unexpected))


def validate_recommendation_risk_link(
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> None:
    for field_name in (
        "target_date",
        "decision",
        "ticker",
        "strategy_version",
        "config_sha256",
    ):
        _require_equal(
            recommendation,
            risk_result,
            field_name,
            "recommendation/risk_result",
        )


def validate_selection_preconditions(
    *,
    ranking: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, Any],
) -> None:
    """Preconditions checked before build_selection is invoked by the CLI.

    Fixed order (each check raises a SELECTION_*-namespaced hard error, never
    a bare KeyError/TypeError/InvalidOperation/ZeroDivisionError):
      1. config_schema_version == 6
      2. selection block exists in config
      3. selection.enabled is True
      4. input_hashes key set is exactly {ranking_sha256, strategy_snapshot_sha256}
      5. ranking.strategy_version == config.strategy_version
      6. ranking.config_sha256 == strategy_config_sha256(config)
      7. ranking.input_hashes.strategy_snapshot_sha256 == input_hashes.strategy_snapshot_sha256
      8. ranking.ranking_status == "COMPLETE"
      9. ranking.ranking_complete is True
     10. validate_rank1_integrity(ranking) passes
     11. selection thresholds in config already passed config-level
         validation -- relied upon via load_strategy_config's
         _validate_selection_config path; not re-derived here.
    """
    from src.selection import validate_rank1_integrity

    # 1. config_schema_version == 6.
    if config.get("config_schema_version") != 6:
        raise ValueError(
            "SELECTION_CONFIG_SCHEMA_VERSION_INVALID: config_schema_version must be 6 for build-selection"
        )

    # 2. selection block exists in config.
    selection_config = config.get("selection")
    if not isinstance(selection_config, dict):
        raise ValueError(
            "SELECTION_CONFIG_BLOCK_MISSING: config is missing the selection block required for build-selection"
        )

    # 3. selection.enabled is True.
    if selection_config.get("enabled") is not True:
        raise ValueError(
            "SELECTION_CONFIG_DISABLED: selection.enabled must be true to build a selection"
        )

    # 4. input_hashes key set is exactly {ranking_sha256, strategy_snapshot_sha256}.
    if set(input_hashes) != {"ranking_sha256", "strategy_snapshot_sha256"}:
        raise ValueError(
            "SELECTION_INPUT_HASHES_INVALID: input_hashes must contain exactly "
            "{ranking_sha256, strategy_snapshot_sha256}"
        )

    # 5. ranking.strategy_version == config.strategy_version.
    if ranking.get("strategy_version") != config.get("strategy_version"):
        raise ValueError(
            "SELECTION_STRATEGY_VERSION_MISMATCH: ranking.strategy_version does not match --config"
        )

    # 6. ranking.config_sha256 == strategy_config_sha256(config).
    if ranking.get("config_sha256") != strategy_config_sha256(config):
        raise ValueError(
            "SELECTION_CONFIG_SHA256_MISMATCH: ranking.config_sha256 does not match --config"
        )

    # 7. ranking.input_hashes.strategy_snapshot_sha256 must match the
    #    strategy_snapshot_sha256 supplied to build-selection.
    ranking_strategy_snapshot_sha256 = (ranking.get("input_hashes") or {}).get(
        "strategy_snapshot_sha256"
    )
    if ranking_strategy_snapshot_sha256 != input_hashes.get("strategy_snapshot_sha256"):
        raise ValueError(
            "SELECTION_HASH_CHAIN_MISMATCH: input_hashes.strategy_snapshot_sha256 does not match "
            "ranking.input_hashes.strategy_snapshot_sha256"
        )

    # 8. ranking.ranking_status == "COMPLETE".
    if ranking.get("ranking_status") != "COMPLETE":
        raise ValueError(
            "SELECTION_RANKING_STATUS_INVALID: ranking.ranking_status must be COMPLETE for build-selection"
        )

    # 9. ranking.ranking_complete is True.
    if ranking.get("ranking_complete") is not True:
        raise ValueError(
            "SELECTION_RANKING_NOT_COMPLETE: ranking.ranking_complete must be true for build-selection"
        )

    # 10. validate_rank1_integrity(ranking) passes (reused from selection.py,
    #     never duplicated here).
    validate_rank1_integrity(ranking)

    # 11. selection thresholds in config are relied upon to have already
    #     passed config-level validation via load_strategy_config's
    #     _validate_selection_config path (invoked when the config was
    #     loaded); this function trusts that path rather than re-deriving
    #     threshold-validity checks from scratch.


def validate_selection_output_contract(
    *,
    selection: dict[str, Any],
    ranking: dict[str, Any],
    config: dict[str, Any],
    input_hashes: dict[str, Any],
) -> None:
    """Recompute selection from the same inputs and compare, excluding generated_at."""
    from src.selection import build_selection

    recomputed = build_selection(ranking=ranking, config=config, input_hashes=input_hashes)
    left = {k: v for k, v in selection.items() if k != "generated_at"}
    right = {k: v for k, v in recomputed.items() if k != "generated_at"}
    if left != right:
        raise ValueError(
            "SELECTION_OUTPUT_CONTRACT_MISMATCH: selection.json does not match the "
            "recomputed selection payload for the same inputs"
        )


def validate_selection_recommendation_preconditions(
    *,
    selection: dict[str, Any],
    ranking: dict[str, Any],
    candidates: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    market_data: dict[str, Any],
    research_window: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Cross-artifact consistency check across ALL of
    build-selection-recommendation's inputs, run after the P0-2
    selection/ranking hash-chain and recompute checks
    (validate_selection_preconditions / validate_selection_output_contract)
    and before build_selection_recommendation() is invoked.

    Every artifact this CLI reads must agree on target_date; strategy
    identity (strategy_version/config_sha256) must be consistent across the
    artifacts that carry it; the candidate pipeline must actually be
    complete; and previous_trading_day must match between selection.json
    and research_window.json. Any mismatch is a Hard Error.
    """
    target_dates = {
        "selection": selection.get("target_date"),
        "ranking": ranking.get("target_date"),
        "candidates": candidates.get("target_date"),
        "candidate_pipeline": candidate_pipeline.get("target_date"),
        "market_data": market_data.get("target_date"),
        "research_window": research_window.get("target_date"),
        "sources": source_payload.get("target_date"),
    }
    if len(set(target_dates.values())) != 1:
        raise ValueError(
            "SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH: target_date does not match "
            "across all inputs: " + ", ".join(f"{k}={v!r}" for k, v in sorted(target_dates.items()))
        )

    strategy_versions = {
        "selection": selection.get("strategy_version"),
        "candidates": candidates.get("strategy_version"),
        "candidate_pipeline": candidate_pipeline.get("strategy_version"),
        "config": config.get("strategy_version"),
    }
    if len(set(strategy_versions.values())) != 1:
        raise ValueError(
            "SELECTION_RECOMMENDATION_STRATEGY_VERSION_MISMATCH: strategy_version does not "
            "match across selection/candidates/candidate_pipeline/config: "
            + ", ".join(f"{k}={v!r}" for k, v in sorted(strategy_versions.items()))
        )

    expected_config_sha256 = strategy_config_sha256(config)
    config_sha256s = {
        "selection": selection.get("config_sha256"),
        "candidates": candidates.get("config_sha256"),
        "candidate_pipeline": candidate_pipeline.get("config_sha256"),
        "config": expected_config_sha256,
    }
    if len(set(config_sha256s.values())) != 1:
        raise ValueError(
            "SELECTION_RECOMMENDATION_CONFIG_SHA256_MISMATCH: config_sha256 does not match "
            "across selection/candidates/candidate_pipeline/config: "
            + ", ".join(f"{k}={v!r}" for k, v in sorted(config_sha256s.items()))
        )

    summary = candidate_pipeline.get("summary") or {}
    if summary.get("pipeline_complete") is not True:
        raise ValueError(
            "SELECTION_RECOMMENDATION_PIPELINE_NOT_COMPLETE: "
            "candidate_pipeline.summary.pipeline_complete must be true"
        )
    if summary.get("screening_complete") is not True:
        raise ValueError(
            "SELECTION_RECOMMENDATION_SCREENING_NOT_COMPLETE: "
            "candidate_pipeline.summary.screening_complete must be true"
        )

    if research_window.get("previous_trading_day") != selection.get("previous_trading_day"):
        raise ValueError(
            "SELECTION_RECOMMENDATION_PREVIOUS_TRADING_DAY_MISMATCH: "
            "research_window.previous_trading_day does not match selection.previous_trading_day"
        )


def validate_ranking_terminal_recommendation_preconditions(
    *,
    ranking: dict[str, Any],
    event_gate: dict[str, Any],
    candidates: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    market_data: dict[str, Any],
    research_window: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Cross-artifact consistency check for build-ranking-terminal-recommendation
    (Case A / Case B), run after the full Ranking Contract re-verification
    (validate_ranking_preconditions / validate_ranking_output_contract) and
    before build_ranking_terminal_recommendation() is invoked.

    Mirrors validate_selection_recommendation_preconditions's style for the
    Case C path so both trust chains carry the same rigor.
    """
    target_dates = {
        "ranking": ranking.get("target_date"),
        "event_gate": event_gate.get("target_date"),
        "candidates": candidates.get("target_date"),
        "candidate_pipeline": candidate_pipeline.get("target_date"),
        "market_data": market_data.get("target_date"),
        "research_window": research_window.get("target_date"),
        "sources": source_payload.get("target_date"),
    }
    if len(set(target_dates.values())) != 1:
        raise ValueError(
            "RANKING_TERMINAL_TARGET_DATE_MISMATCH: target_date does not match "
            "across all inputs: " + ", ".join(f"{k}={v!r}" for k, v in sorted(target_dates.items()))
        )

    previous_trading_days = {
        "ranking": ranking.get("previous_trading_day"),
        "event_gate": event_gate.get("previous_trading_day"),
        "research_window": research_window.get("previous_trading_day"),
    }
    if len(set(previous_trading_days.values())) != 1:
        raise ValueError(
            "RANKING_TERMINAL_PREVIOUS_TRADING_DAY_MISMATCH: previous_trading_day does not "
            "match across ranking/event_gate/research_window: "
            + ", ".join(f"{k}={v!r}" for k, v in sorted(previous_trading_days.items()))
        )

    strategy_versions = {
        "ranking": ranking.get("strategy_version"),
        "candidates": candidates.get("strategy_version"),
        "candidate_pipeline": candidate_pipeline.get("strategy_version"),
        "config": config.get("strategy_version"),
        "event_gate": event_gate.get("strategy_version"),
    }
    if len(set(strategy_versions.values())) != 1:
        raise ValueError(
            "RANKING_TERMINAL_STRATEGY_VERSION_MISMATCH: strategy_version does not "
            "match across ranking/candidates/candidate_pipeline/config/event_gate: "
            + ", ".join(f"{k}={v!r}" for k, v in sorted(strategy_versions.items()))
        )

    expected_config_sha256 = strategy_config_sha256(config)
    config_sha256s = {
        "ranking": ranking.get("config_sha256"),
        "candidates": candidates.get("config_sha256"),
        "candidate_pipeline": candidate_pipeline.get("config_sha256"),
        "event_gate": event_gate.get("config_sha256"),
        "config": expected_config_sha256,
    }
    if len(set(config_sha256s.values())) != 1:
        raise ValueError(
            "RANKING_TERMINAL_CONFIG_SHA256_MISMATCH: config_sha256 does not match "
            "across ranking/candidates/candidate_pipeline/event_gate/config: "
            + ", ".join(f"{k}={v!r}" for k, v in sorted(config_sha256s.items()))
        )

    summary = candidate_pipeline.get("summary") or {}
    if summary.get("pipeline_complete") is not True:
        raise ValueError(
            "RANKING_TERMINAL_PIPELINE_NOT_COMPLETE: "
            "candidate_pipeline.summary.pipeline_complete must be true"
        )
    if summary.get("screening_complete") is not True:
        raise ValueError(
            "RANKING_TERMINAL_SCREENING_NOT_COMPLETE: "
            "candidate_pipeline.summary.screening_complete must be true"
        )


def validate_ranking_terminal_recommendation_output_contract(
    *,
    recommendation: dict[str, Any],
    ranking: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    research_window: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Recompute the Terminal Recommendation (Case A/B) from the same
    already-trust-chain-verified inputs and compare, full dict equality.

    Mirrors validate_selection_output_contract's recompute-and-compare
    pattern. build_ranking_terminal_recommendation() never emits a
    generated_at-style non-deterministic field, so unlike
    validate_selection_output_contract this is a full, unqualified dict
    comparison. Used by both build-ranking-terminal-recommendation callers
    and risk-check's terminal_driven_v6 trust-chain re-verification so the
    comparison logic is never duplicated.
    """
    from src.ranking_terminal_recommendation import build_ranking_terminal_recommendation

    recomputed = build_ranking_terminal_recommendation(
        ranking=ranking,
        candidate_pipeline=candidate_pipeline,
        research_window=research_window,
        config=config,
    )
    if recommendation != recomputed:
        raise ValueError(
            "RISK_TERMINAL_RECOMMENDATION_CONTRACT_MISMATCH: recommendation.json does not "
            "match the recomputed Terminal Recommendation payload for the same "
            "genuinely re-verified Ranking + upstream artifact inputs"
        )


def validate_selection_recommendation_output_contract(
    *,
    recommendation: dict[str, Any],
    selection: dict[str, Any],
    candidates: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    market_data: dict[str, Any],
    research_window: dict[str, Any],
    source_payload: dict[str, Any],
    config: dict[str, Any],
    selection_sha256: str,
) -> None:
    """Recompute the Selection Recommendation (Case C) from the same
    already-trust-chain-verified inputs and compare, full dict equality.

    Mirrors validate_ranking_terminal_recommendation_output_contract's
    recompute-and-compare pattern for the Case C path.
    ``build_selection_recommendation()`` never emits a generated_at-style
    non-deterministic field, so this is a full, unqualified dict comparison.
    ``selection_sha256`` must be the ACTUAL raw SHA256 of selection.json as
    computed by the caller -- never trusted from any embedded field.
    """
    from src.selection_recommendation import build_selection_recommendation

    recomputed = build_selection_recommendation(
        selection=selection,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        research_window=research_window,
        source_payload=source_payload,
        config=config,
        selection_sha256=selection_sha256,
    )
    if recommendation != recomputed:
        raise ValueError(
            "SELECTION_RECOMMENDATION_OUTPUT_CONTRACT_MISMATCH: recommendation.json does "
            "not match the recomputed Selection Recommendation payload for the same "
            "genuinely re-verified Selection + upstream artifact inputs"
        )


def validate_recommendation_selection_link(
    *,
    recommendation: dict[str, Any],
    selection: dict[str, Any],
    selection_sha256: str,
) -> None:
    """Cross-check a Recommendation v2 payload against its selection.json."""
    if recommendation.get("schema_version") != 2:
        raise ValueError(
            "RECOMMENDATION_SELECTION_LINK: recommendation.schema_version must be 2"
        )
    for field_name in ("target_date", "strategy_version", "config_sha256"):
        _require_equal(
            recommendation,
            selection,
            field_name,
            "recommendation/selection",
        )
    if recommendation.get("selection_sha256") != selection_sha256:
        raise ValueError(
            "RECOMMENDATION_SELECTION_LINK: recommendation.selection_sha256 does not "
            "match the actual SHA256 of selection.json"
        )
    status = selection.get("selection_status")
    if status == "SELECTED":
        if recommendation.get("decision") != "TRADE":
            raise ValueError(
                "RECOMMENDATION_SELECTION_LINK: SELECTED selection requires decision=TRADE"
            )
        if recommendation.get("ticker") != selection.get("selected_ticker"):
            raise ValueError(
                "RECOMMENDATION_SELECTION_LINK: recommendation.ticker does not match "
                "selection.selected_ticker"
            )
    elif status == "NO_TRADE":
        if recommendation.get("decision") != "NO_TRADE":
            raise ValueError(
                "RECOMMENDATION_SELECTION_LINK: NO_TRADE selection requires decision=NO_TRADE"
            )
        if recommendation.get("ticker") is not None:
            raise ValueError(
                "RECOMMENDATION_SELECTION_LINK: NO_TRADE selection requires ticker=null"
            )
    else:
        raise ValueError(
            "RECOMMENDATION_SELECTION_LINK: selection.selection_status must be "
            "SELECTED or NO_TRADE"
        )
    if recommendation.get("selection_reasons") != selection.get("reason_codes"):
        raise ValueError(
            "RECOMMENDATION_SELECTION_LINK: recommendation.selection_reasons does not "
            "match selection.reason_codes"
        )


def _require_equal(
    left: dict[str, Any],
    right: dict[str, Any],
    field_name: str,
    label: str,
) -> None:
    if left.get(field_name) != right.get(field_name):
        raise ValueError(f"{label} {field_name} does not match")


def _error_sort_key(error: Any) -> tuple[str, str]:
    return (".".join(str(item) for item in error.absolute_path), error.message)


def _reject_non_standard_number(value: str) -> None:
    raise ValueError(f"Non-standard JSON number is not allowed: {value}")
