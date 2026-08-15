"""Shared Downstream Trust Layer: Ranking -> Selection -> Recommendation -> Risk.

Both ``risk-check`` (``src/cli.py``) and the Production Verifier
(``src/production_verify.py``) must answer the same question about a run:

    given only the raw bytes on disk, is ``recommendation.json`` (and, in
    Case C, ``selection.json``) actually *derivable* from the verified
    upstream artifacts by this repository's own official Builders?

Answering it by comparing artifacts against each other is not enough: a
forger who edits Selection, Recommendation and Risk *together* can make any
number of surface-level cross-checks agree. The only sound answer is to
re-run the real Builders and compare, which is what this module orchestrates
-- once, so the two call sites cannot drift apart.

This module is Read Only / Pure Orchestration:

* it never writes a file,
* it never touches the network,
* it never reimplements Selection / Recommendation / Risk business logic --
  every rule comes from ``src/contracts.py``, ``src/selection.py``,
  ``src/selection_recommendation.py``,
  ``src/ranking_terminal_recommendation.py`` and ``src/risk.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import load_strategy_config
from src.contracts import (
    load_json_document,
    validate_ranking_terminal_recommendation_output_contract,
    validate_ranking_terminal_recommendation_preconditions,
    validate_recommendation_candidate_link,
    validate_recommendation_pipeline_link,
    validate_recommendation_selection_link,
    validate_recommendation_sources,
    validate_selection_output_contract,
    validate_selection_preconditions,
    validate_selection_recommendation_output_contract,
    validate_selection_recommendation_preconditions,
)
from src.market import (
    MarketDataRecord,
    SourceLedgerValidationResult,
    load_market_data,
    validate_market_data,
    validate_source_ledger,
)
from src.research import (
    MarketDataResearchAlignmentResult,
    validate_market_records_against_research,
    validate_market_research,
)
from src.ranking_terminal_recommendation import determine_ranking_terminal_case
from src.ranking_trust import load_and_verify_ranking_trust_chain
from src.risk import OrderProposal, evaluate_order, not_applicable_result
from src.source_matrix import load_source_matrix

CASE_A = "CASE_A"
CASE_B = "CASE_B"
CASE_C_NO_TRADE = "CASE_C_NO_TRADE"
CASE_C_TRADE = "CASE_C_TRADE"

TERMINAL_CASES = frozenset({CASE_A, CASE_B})
SELECTION_CASES = frozenset({CASE_C_NO_TRADE, CASE_C_TRADE})

#: The exact key set of a Risk Result v2 ``input_hashes`` object.
RISK_INPUT_HASH_KEYS: tuple[str, ...] = (
    "recommendation_sha256",
    "candidates_sha256",
    "candidate_pipeline_sha256",
    "market_data_sha256",
    "sources_sha256",
    "source_matrix_sha256",
    "strategy_snapshot_sha256",
    "ranking_sha256",
    "event_gate_sha256",
    "research_window_sha256",
    "selection_sha256",
    "market_research_sha256",
)


class DownstreamTrustError(ValueError):
    """Raised for any Downstream Trust Chain violation (always a Hard Error)."""


def _hard_error(code: str, message: str) -> None:
    raise DownstreamTrustError(f"{code}: {message}")


def sha256_file_bytes(path: Path) -> str:
    """SHA256 of a file's *raw bytes* -- never of a re-serialized payload."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class VerifiedDownstreamBundle:
    """The outcome of a fully-verified Downstream Trust Chain.

    Every artifact here has been schema-validated *and* re-derived from its
    own verified inputs by this repository's official Builders.
    """

    case_id: str
    config: dict[str, Any]
    ranking: dict[str, Any]
    selection: dict[str, Any] | None
    recommendation: dict[str, Any]
    event_gate: dict[str, Any]
    candidates: dict[str, Any]
    candidate_pipeline: dict[str, Any]
    market_data: dict[str, Any]
    source_payload: dict[str, Any]
    research_window: dict[str, Any]
    source_matrix: dict[str, Any]
    actual_hashes: dict[str, str | None]
    paths: dict[str, Path]


# ---------------------------------------------------------------------------
# Shared Market/Research loading (used identically by CLI and Verifier)
# ---------------------------------------------------------------------------


def load_market_bundle(
    market_data_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
) -> tuple[
    str,
    list[MarketDataRecord],
    SourceLedgerValidationResult,
    dict[str, Any],
    dict[str, Any],
]:
    """Load + validate the Market Data / Sources / Source Matrix triple."""
    source_matrix = load_source_matrix(source_matrix_path)
    load_json_document(market_data_path, "market_data.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")
    target_date, records = load_market_data(market_data_path)
    assert target_date is not None
    ledger_result = validate_source_ledger(
        target_date,
        records,
        source_payload,
        source_matrix,
        source_base_dir=Path(sources_path).parent,
    )
    return target_date, records, ledger_result, source_payload, source_matrix


def load_market_research_alignment(
    records: list[MarketDataRecord],
    market_research_path: Path | None,
    source_matrix: dict[str, Any],
    source_payload: dict[str, Any] | None = None,
) -> MarketDataResearchAlignmentResult | None:
    """Optional Market Research alignment -- semantics unchanged from the CLI."""
    if market_research_path is None:
        return None
    market_research = load_json_document(
        market_research_path,
        "market_research.schema.json",
    )
    research_result = validate_market_research(
        market_research,
        source_matrix,
        source_payload,
    )
    alignment_result = validate_market_records_against_research(records, market_research)
    return MarketDataResearchAlignmentResult(
        research_result.valid and alignment_result.valid,
        (*research_result.errors, *alignment_result.global_errors),
        alignment_result.errors_by_ticker,
    )


def alignment_error_messages(
    result: MarketDataResearchAlignmentResult,
) -> list[str]:
    messages = list(result.global_errors)
    for ticker, errors in sorted(result.errors_by_ticker.items()):
        messages.extend(f"{ticker}: {error}" for error in errors)
    return messages


# ---------------------------------------------------------------------------
# Case determination
# ---------------------------------------------------------------------------


def determine_downstream_case(
    ranking: dict[str, Any],
    config: dict[str, Any],
) -> str:
    """Return ``CASE_A``, ``CASE_B`` or ``"CASE_C"`` from verified Ranking+Config.

    Never derived from ``recommendation.decision`` or ``risk_result.status``:
    the case is a property of the *upstream* chain alone.
    """
    status = ranking.get("ranking_status")
    complete = ranking.get("ranking_complete")
    selection_config = config.get("selection")
    selection_enabled = (
        isinstance(selection_config, dict) and selection_config.get("enabled") is True
    )

    if status == "COMPLETE" and complete is True and selection_enabled:
        return "CASE_C"
    try:
        terminal_case = determine_ranking_terminal_case(ranking, config)
    except ValueError as exc:
        raise DownstreamTrustError(
            "DOWNSTREAM_TERMINAL_CASE_INVALID: cannot classify this run from its "
            f"verified ranking.json + config: {exc}"
        ) from exc
    return CASE_A if terminal_case == "A" else CASE_B


# ---------------------------------------------------------------------------
# The shared chain
# ---------------------------------------------------------------------------


def verify_downstream_recommendation_chain(
    *,
    recommendation_path: Path,
    candidates_path: Path,
    candidate_pipeline_path: Path,
    market_data_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
    config_path: Path,
    ranking_path: Path,
    event_gate_path: Path,
    research_window_path: Path,
    selection_path: Path | None = None,
) -> VerifiedDownstreamBundle:
    """Verify Ranking -> (Selection) -> Recommendation for one run.

    Raises ``DownstreamTrustError`` / ``ValueError`` (Hard Error) on any
    violation. On success the returned bundle is safe to feed into
    ``build_risk_result_v2``.
    """
    ranking_bundle = load_and_verify_ranking_trust_chain(
        ranking_path=ranking_path,
        event_gate_path=event_gate_path,
        candidates_path=candidates_path,
        market_data_path=market_data_path,
        sources_path=sources_path,
        source_matrix_path=source_matrix_path,
        config_path=config_path,
    )
    ranking = ranking_bundle.ranking
    config = ranking_bundle.config

    case = determine_downstream_case(ranking, config)

    recommendation = load_json_document(
        recommendation_path, "recommendation.schema.json"
    )
    candidate_pipeline = load_json_document(
        candidate_pipeline_path, "candidate_pipeline.schema.json"
    )
    research_window = load_json_document(
        research_window_path, "research_window.schema.json"
    )
    candidates = ranking_bundle.candidates
    market_data = ranking_bundle.market_data
    source_payload = ranking_bundle.source_payload

    validate_recommendation_candidate_link(recommendation, candidates)
    validate_recommendation_pipeline_link(recommendation, candidate_pipeline)
    validate_recommendation_sources(recommendation, source_payload)

    recommendation_schema_version = recommendation.get("schema_version")
    selection: dict[str, Any] | None = None
    selection_sha256: str | None = None

    if case == "CASE_C":
        if selection_path is None:
            _hard_error(
                "DOWNSTREAM_SELECTION_REQUIRED",
                "selection.enabled is true and ranking is COMPLETE, so selection.json "
                "is required for this run",
            )
        assert selection_path is not None
        if recommendation_schema_version != 2:
            _hard_error(
                "DOWNSTREAM_RECOMMENDATION_SCHEMA_VERSION_INVALID",
                "Case C requires recommendation.schema_version == 2; got "
                f"{recommendation_schema_version!r}",
            )

        # --- fixed Selection trust order (FIX-R2-003 section 12) -----------
        ranking_sha256 = sha256_file_bytes(ranking_path)
        strategy_snapshot_sha256 = sha256_file_bytes(config_path)
        selection = load_json_document(selection_path, "selection.schema.json")
        actual_selection_input_hashes = {
            "ranking_sha256": ranking_sha256,
            "strategy_snapshot_sha256": strategy_snapshot_sha256,
        }
        validate_selection_preconditions(
            ranking=ranking,
            config=config,
            input_hashes=actual_selection_input_hashes,
        )
        validate_selection_output_contract(
            selection=selection,
            ranking=ranking,
            config=config,
            input_hashes=actual_selection_input_hashes,
        )

        # Only a fully-verified selection.json earns a trusted hash.
        selection_sha256 = sha256_file_bytes(selection_path)

        validate_recommendation_selection_link(
            recommendation=recommendation,
            selection=selection,
            selection_sha256=selection_sha256,
        )
        validate_selection_recommendation_preconditions(
            selection=selection,
            ranking=ranking,
            candidates=candidates,
            candidate_pipeline=candidate_pipeline,
            market_data=market_data,
            research_window=research_window,
            source_payload=source_payload,
            config=config,
        )
        validate_selection_recommendation_output_contract(
            recommendation=recommendation,
            selection=selection,
            candidates=candidates,
            candidate_pipeline=candidate_pipeline,
            market_data=market_data,
            research_window=research_window,
            source_payload=source_payload,
            config=config,
            selection_sha256=selection_sha256,
        )

        selection_status = selection.get("selection_status")
        if selection_status == "SELECTED":
            case_id = CASE_C_TRADE
        elif selection_status == "NO_TRADE":
            case_id = CASE_C_NO_TRADE
        else:  # pragma: no cover -- the schema already constrains this
            _hard_error(
                "DOWNSTREAM_SELECTION_STATUS_INVALID",
                f"unexpected selection_status {selection_status!r}",
            )
            raise AssertionError("unreachable")  # pragma: no cover
    else:
        if selection_path is not None:
            _hard_error(
                "DOWNSTREAM_SELECTION_FORBIDDEN_FOR_TERMINAL_CASE",
                f"{case} is a Terminal (Ranking-driven) case and must not carry a "
                "selection.json",
            )
        if recommendation_schema_version != 1:
            _hard_error(
                "DOWNSTREAM_RECOMMENDATION_SCHEMA_VERSION_INVALID",
                f"{case} requires recommendation.schema_version == 1; got "
                f"{recommendation_schema_version!r}",
            )
        validate_ranking_terminal_recommendation_preconditions(
            ranking=ranking,
            event_gate=ranking_bundle.event_gate,
            candidates=candidates,
            candidate_pipeline=candidate_pipeline,
            market_data=market_data,
            research_window=research_window,
            source_payload=source_payload,
            config=config,
        )
        validate_ranking_terminal_recommendation_output_contract(
            recommendation=recommendation,
            ranking=ranking,
            candidate_pipeline=candidate_pipeline,
            research_window=research_window,
            config=config,
        )
        case_id = case

    actual_hashes: dict[str, str | None] = {
        "recommendation_sha256": sha256_file_bytes(recommendation_path),
        "candidates_sha256": sha256_file_bytes(candidates_path),
        "candidate_pipeline_sha256": sha256_file_bytes(candidate_pipeline_path),
        "market_data_sha256": sha256_file_bytes(market_data_path),
        "sources_sha256": sha256_file_bytes(sources_path),
        "source_matrix_sha256": sha256_file_bytes(source_matrix_path),
        "strategy_snapshot_sha256": sha256_file_bytes(config_path),
        "ranking_sha256": sha256_file_bytes(ranking_path),
        "event_gate_sha256": sha256_file_bytes(event_gate_path),
        "research_window_sha256": sha256_file_bytes(research_window_path),
        "selection_sha256": selection_sha256,
    }

    return VerifiedDownstreamBundle(
        case_id=case_id,
        config=config,
        ranking=ranking,
        selection=selection,
        recommendation=recommendation,
        event_gate=ranking_bundle.event_gate,
        candidates=candidates,
        candidate_pipeline=candidate_pipeline,
        market_data=market_data,
        source_payload=source_payload,
        research_window=research_window,
        source_matrix=ranking_bundle.source_matrix,
        actual_hashes=actual_hashes,
        paths={
            "recommendation": Path(recommendation_path),
            "candidates": Path(candidates_path),
            "candidate_pipeline": Path(candidate_pipeline_path),
            "market_data": Path(market_data_path),
            "sources": Path(sources_path),
            "source_matrix": Path(source_matrix_path),
            "config": Path(config_path),
            "ranking": Path(ranking_path),
            "event_gate": Path(event_gate_path),
            "research_window": Path(research_window_path),
            **({"selection": Path(selection_path)} if selection_path is not None else {}),
        },
    )


# ---------------------------------------------------------------------------
# Risk Result v2
# ---------------------------------------------------------------------------


def _require_non_negative_int(value: Any, name: str) -> int:
    if type(value) is not int:  # bool is deliberately rejected
        _hard_error(
            "RISK_EVALUATION_CONTEXT_INVALID",
            f"{name} must be an integer for a Case C TRADE run; got {value!r}",
        )
    if value < 0:
        _hard_error(
            "RISK_EVALUATION_CONTEXT_INVALID",
            f"{name} must be >= 0; got {value!r}",
        )
    return int(value)


def build_risk_result_v2(
    *,
    bundle: VerifiedDownstreamBundle,
    current_positions: int | None,
    trades_today: int | None,
    market_research_path: Path | None = None,
) -> dict[str, Any]:
    """Build a Risk Result v2 payload from an already-verified bundle.

    Pure: it reads the artifacts named by the bundle and returns a dict. It
    never writes anything, and it never reimplements Risk business logic --
    ``evaluate_order`` / ``not_applicable_result`` decide the outcome.
    """
    recommendation = bundle.recommendation
    paths = bundle.paths

    input_hashes: dict[str, str | None] = dict(bundle.actual_hashes)
    input_hashes["market_research_sha256"] = (
        sha256_file_bytes(market_research_path) if market_research_path is not None else None
    )
    if set(input_hashes) != set(RISK_INPUT_HASH_KEYS):  # pragma: no cover - guard
        raise DownstreamTrustError(
            "RISK_INPUT_HASHES_KEYSET_INVALID: unexpected Risk input hash key set"
        )

    if bundle.case_id == CASE_C_TRADE:
        positions = _require_non_negative_int(current_positions, "current_positions")
        trades = _require_non_negative_int(trades_today, "trades_today")
        evaluation_context: dict[str, Any] = {
            "current_positions": positions,
            "trades_today": trades,
        }

        (
            market_target_date,
            records,
            ledger_result,
            source_payload,
            source_matrix,
        ) = load_market_bundle(
            paths["market_data"], paths["sources"], paths["source_matrix"]
        )
        alignment_result = load_market_research_alignment(
            records,
            market_research_path,
            source_matrix,
            source_payload,
        )
        ticker = str(recommendation.get("ticker", "")).strip()
        matches = [record for record in records if record.ticker == ticker]
        if len(matches) != 1:
            _hard_error(
                "RISK_MARKET_DATA_RECORD_AMBIGUOUS",
                f"expected exactly 1 market_data record for {ticker!r}; got {len(matches)}",
            )
        market_record = matches[0]
        alignment_valid = True
        if alignment_result is not None:
            alignment_valid = (
                not alignment_result.global_errors
                and not alignment_result.errors_by_ticker.get(ticker)
            )
        market_valid = (
            ledger_result.valid
            and alignment_valid
            and validate_market_data(market_record, source_matrix).valid_for_trade
        )
        proposal = OrderProposal.from_dict(recommendation)
        result = evaluate_order(
            proposal,
            bundle.config,
            market_data_valid=market_valid,
            market_target_date=market_target_date,
            market_previous_high=market_record.previous_high,
            market_tick_size=market_record.tick_size,
            current_positions=positions,
            trades_today=trades,
        )
    else:
        evaluation_context = {"current_positions": None, "trades_today": None}
        result = not_applicable_result(None)

    return {
        "schema_version": 2,
        "target_date": recommendation.get("target_date"),
        "decision": recommendation.get("decision"),
        "strategy_version": recommendation["strategy_version"],
        "config_sha256": recommendation["config_sha256"],
        **result.as_dict(),
        "evaluation_context": evaluation_context,
        "input_hashes": input_hashes,
    }


__all__ = [
    "CASE_A",
    "CASE_B",
    "CASE_C_NO_TRADE",
    "CASE_C_TRADE",
    "RISK_INPUT_HASH_KEYS",
    "DownstreamTrustError",
    "VerifiedDownstreamBundle",
    "alignment_error_messages",
    "build_risk_result_v2",
    "determine_downstream_case",
    "load_market_bundle",
    "load_market_research_alignment",
    "sha256_file_bytes",
    "verify_downstream_recommendation_chain",
]
