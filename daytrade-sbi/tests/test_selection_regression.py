from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.config import load_strategy_config
from src.contracts import (
    load_json_document,
    validate_selection_output_contract,
    validate_selection_preconditions,
)
from src.selection import build_selection


FIXTURE_DIR = Path("regression/2026-08-12-selection-v1-selected/runs/2026-08-12")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_selection_v1_regression_fixture_hashes_are_real_not_placeholders():
    """The recorded input_hashes in ranking.json/selection.json must be the
    actual sha256 of the sibling artifact bytes, not 'aaa...'-style
    placeholders."""
    ranking = json.loads((FIXTURE_DIR / "ranking.json").read_text(encoding="utf-8"))
    selection = json.loads((FIXTURE_DIR / "selection.json").read_text(encoding="utf-8"))

    assert not (FIXTURE_DIR / "event_gate.json").exists()
    assert not (FIXTURE_DIR / "candidates.json").exists()

    assert ranking["input_hashes"]["strategy_snapshot_sha256"] == _sha256(
        FIXTURE_DIR / "strategy_snapshot.yaml"
    )
    assert selection["input_hashes"]["ranking_sha256"] == _sha256(FIXTURE_DIR / "ranking.json")
    assert selection["input_hashes"]["strategy_snapshot_sha256"] == _sha256(
        FIXTURE_DIR / "strategy_snapshot.yaml"
    )


def test_selection_v1_regression_fixture_build_selection_reproduces_selected():
    """Loading the Ranking -> Selection regression fixture and calling
    build_selection() with the same inputs must reproduce the exact recorded
    selection.json (SELECTED, rank1 ticker selected)."""
    ranking = load_json_document(FIXTURE_DIR / "ranking.json", "ranking.schema.json")
    recorded_selection = load_json_document(
        FIXTURE_DIR / "selection.json", "selection.schema.json"
    )
    config = load_strategy_config(FIXTURE_DIR / "strategy_snapshot.yaml")

    input_hashes = dict(recorded_selection["input_hashes"])

    validate_selection_preconditions(
        ranking=ranking,
        config=config,
        input_hashes=input_hashes,
    )

    rebuilt = build_selection(ranking=ranking, config=config, input_hashes=input_hashes)
    validate_selection_output_contract(
        selection=rebuilt,
        ranking=ranking,
        config=config,
        input_hashes=input_hashes,
    )

    left = {k: v for k, v in rebuilt.items() if k != "generated_at"}
    right = {k: v for k, v in recorded_selection.items() if k != "generated_at"}
    assert left == right
    assert recorded_selection["selection_status"] == "SELECTED"
    assert recorded_selection["selected_ticker"] == "7203"
    assert recorded_selection["reason_codes"] == ["SELECTION_ALL_RULES_PASSED"]
