"""Shared Ranking Trust Chain verification helper.

Several tools consume ``ranking.json`` as an upstream artifact (Case A/B's
``build-ranking-terminal-recommendation``, Case C's ``build-selection``, and
the risk-check terminal path). Each of them must re-verify -- not merely
schema-validate -- that the ``ranking.json`` they were handed was genuinely
produced from its own claimed upstream artifacts (``event_gate.json``,
``candidates.json``, ``market_data.json``, ``sources.json``,
``source_matrix.yaml``, ``strategy_snapshot.yaml``) before trusting anything
inside it. This module extracts that verification sequence into one place so
it is implemented once instead of copy-pasted per call site.

This module performs filesystem I/O (reading, hashing, schema-validating
loads) and is therefore explicitly an orchestration / trust-boundary helper,
not a pure business-logic function. Pure functions such as
``build_selection()`` and ``build_ranking_terminal_recommendation()`` must
stay free of any I/O of their own; callers use this helper to obtain
verified inputs and then pass plain dicts into those pure functions.

Fixed processing order (mirrors ``_build_ranking_terminal_recommendation`` in
``src/cli.py`` exactly):

  1. raw-byte SHA256 of every claimed Ranking input (never re-serialized)
  2. schema-validating load of every JSON artifact
  3. load + validate source_matrix.yaml
  4. load the strategy config snapshot
  5. assert ranking.input_hashes == the actual hashes computed in step 1
     (full dict equality, not a subset check)
  6. validate_ranking_preconditions (the SAME function build-ranking itself
     calls, with source_base_dir set so Source Ledger file existence is
     re-checked)
  7. validate_ranking_output_contract (catches a schema-valid but
     hand-edited ranking.json)

Any failure anywhere in this sequence is a Hard Error -- raised as
``ValueError`` -- and callers must not proceed to their own business logic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import load_strategy_config
from src.contracts import (
    load_json_document,
    validate_ranking_output_contract,
    validate_ranking_preconditions,
)
from src.source_matrix import load_source_matrix, validate_source_matrix

RANKING_INPUT_HASHES_MISMATCH_CODE = "RANKING_INPUT_HASHES_MISMATCH"


@dataclass(frozen=True)
class VerifiedRankingBundle:
    """The outcome of a fully-verified Ranking Trust Chain.

    Every field here has already passed schema validation and, for
    ``ranking``, full cross-artifact provenance re-verification via
    ``validate_ranking_preconditions``/``validate_ranking_output_contract``.
    """

    ranking: dict[str, Any]
    event_gate: dict[str, Any]
    candidates: dict[str, Any]
    market_data: dict[str, Any]
    source_payload: dict[str, Any]
    source_matrix: dict[str, Any]
    config: dict[str, Any]
    actual_ranking_input_hashes: dict[str, str]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_and_verify_ranking_trust_chain(
    *,
    ranking_path: Path,
    event_gate_path: Path,
    candidates_path: Path,
    market_data_path: Path,
    sources_path: Path,
    source_matrix_path: Path,
    config_path: Path,
) -> VerifiedRankingBundle:
    """Load, hash, and fully re-verify a claimed Ranking Trust Chain.

    Raises ``ValueError`` (Hard Error) if ``ranking.json``'s recorded
    ``input_hashes`` do not exactly match the real SHA256 of the supplied
    upstream files, or if either of the existing Ranking Contract functions
    rejects the artifacts. Never reimplements Ranking business logic
    (turnover ranking, relative-tick ranking, aggregation, tie-break) --
    it only orchestrates loading, hashing, and delegating to the existing
    contract functions.
    """

    actual_ranking_input_hashes = {
        "event_gate_sha256": _sha256_bytes(event_gate_path.read_bytes()),
        "candidates_sha256": _sha256_bytes(candidates_path.read_bytes()),
        "market_data_sha256": _sha256_bytes(market_data_path.read_bytes()),
        "sources_sha256": _sha256_bytes(sources_path.read_bytes()),
        "source_matrix_sha256": _sha256_bytes(source_matrix_path.read_bytes()),
        "strategy_snapshot_sha256": _sha256_bytes(config_path.read_bytes()),
    }

    ranking = load_json_document(ranking_path, "ranking.schema.json")
    event_gate = load_json_document(event_gate_path, "event_gate.schema.json")
    candidates = load_json_document(candidates_path, "candidates.schema.json")
    market_data = load_json_document(market_data_path, "market_data.schema.json")
    source_payload = load_json_document(sources_path, "sources.schema.json")

    source_matrix = load_source_matrix(source_matrix_path)
    validate_source_matrix(source_matrix)

    config = load_strategy_config(config_path)

    if ranking.get("input_hashes") != actual_ranking_input_hashes:
        mismatched_keys = sorted(
            key
            for key in set(actual_ranking_input_hashes) | set(ranking.get("input_hashes") or {})
            if (ranking.get("input_hashes") or {}).get(key) != actual_ranking_input_hashes.get(key)
        )
        raise ValueError(
            f"{RANKING_INPUT_HASHES_MISMATCH_CODE}: ranking.input_hashes does not match the "
            "actual SHA256 of the --event-gate/--candidates/--market-data/--sources/"
            "--source-matrix/--config files supplied here; mismatched keys: "
            + ", ".join(mismatched_keys)
        )

    validate_ranking_preconditions(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        source_matrix=source_matrix,
        config=config,
        input_hashes=actual_ranking_input_hashes,
        source_base_dir=sources_path.parent,
    )
    validate_ranking_output_contract(
        ranking=ranking,
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
    )

    return VerifiedRankingBundle(
        ranking=ranking,
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        source_matrix=source_matrix,
        config=config,
        actual_ranking_input_hashes=actual_ranking_input_hashes,
    )
