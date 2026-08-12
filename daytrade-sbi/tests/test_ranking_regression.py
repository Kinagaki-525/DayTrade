from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.config import load_strategy_config
from src.contracts import (
    load_json_document,
    validate_ranking_output_contract,
    validate_ranking_preconditions,
)
from src.ranking import build_ranking
from src.source_matrix import load_source_matrix


FIXTURE_DIR = Path("regression/2026-08-12-ranking-v1-complete/runs/2026-08-12")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ranking_v1_regression_fixture_hashes_are_real_not_placeholders():
    """The recorded input_hashes in event_gate.json and ranking.json must be
    the actual sha256 of the sibling artifact bytes, not 'aaa...'-style
    placeholders."""
    event_gate = json.loads((FIXTURE_DIR / "event_gate.json").read_text(encoding="utf-8"))
    ranking = json.loads((FIXTURE_DIR / "ranking.json").read_text(encoding="utf-8"))

    assert event_gate["input_hashes"]["candidates_sha256"] == _sha256(FIXTURE_DIR / "candidates.json")
    assert event_gate["input_hashes"]["sources_sha256"] == _sha256(FIXTURE_DIR / "sources.json")
    assert event_gate["input_hashes"]["strategy_snapshot_sha256"] == _sha256(
        FIXTURE_DIR / "strategy_snapshot.yaml"
    )

    assert ranking["input_hashes"]["event_gate_sha256"] == _sha256(FIXTURE_DIR / "event_gate.json")
    assert ranking["input_hashes"]["candidates_sha256"] == _sha256(FIXTURE_DIR / "candidates.json")
    assert ranking["input_hashes"]["market_data_sha256"] == _sha256(FIXTURE_DIR / "market_data.json")
    assert ranking["input_hashes"]["sources_sha256"] == _sha256(FIXTURE_DIR / "sources.json")
    assert ranking["input_hashes"]["strategy_snapshot_sha256"] == _sha256(
        FIXTURE_DIR / "strategy_snapshot.yaml"
    )


def test_ranking_v1_regression_fixture_build_ranking_reproduces_complete():
    """Loading the full Event Gate -> Ranking regression fixture chain and
    calling build_ranking() with the same inputs must reproduce the exact
    recorded ranking.json (COMPLETE, PASS candidate ranked)."""
    event_gate = load_json_document(FIXTURE_DIR / "event_gate.json", "event_gate.schema.json")
    candidates = load_json_document(FIXTURE_DIR / "candidates.json", "candidates.schema.json")
    market_data = load_json_document(FIXTURE_DIR / "market_data.json", "market_data.schema.json")
    source_payload = load_json_document(FIXTURE_DIR / "sources.json", "sources.schema.json")
    recorded_ranking = load_json_document(FIXTURE_DIR / "ranking.json", "ranking.schema.json")
    source_matrix = load_source_matrix()
    config = load_strategy_config(FIXTURE_DIR / "strategy_snapshot.yaml")

    input_hashes = dict(recorded_ranking["input_hashes"])

    validate_ranking_preconditions(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        source_matrix=source_matrix,
        config=config,
        input_hashes=input_hashes,
        source_base_dir=FIXTURE_DIR,
    )

    ranking = build_ranking(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
        input_hashes=input_hashes,
    )

    assert ranking["ranking_status"] == "COMPLETE"
    assert ranking["ranking_complete"] is True
    assert ranking["summary"]["top_ranked_ticker"] == "7203"
    assert ranking["candidates"][0]["final_rank"] == 1

    # Byte-level reproducibility: every field except generated_at (wall
    # clock at write time) must match the recorded artifact exactly.
    recomputed = dict(ranking)
    recorded = dict(recorded_ranking)
    recomputed.pop("generated_at", None)
    recorded.pop("generated_at", None)
    assert recomputed == recorded

    validate_ranking_output_contract(
        ranking=recorded_ranking,
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
    )
