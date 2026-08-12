"""Shared test-only helper for building a genuine, fully hash-consistent
Selection Calibration run directory by driving the real build-ranking CLI
-- reused by both tests/test_selection_calibration.py and
tests/test_selection_calibration_cli.py so the two test modules exercise
byte-identical fixture construction."""

from __future__ import annotations

import copy
import hashlib

from src import cli
from src.config import DEFAULT_CONFIG_PATH
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

from tests.test_ranking import TARGET_DATE, _build_full_case, _write_ranking_inputs


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_calibration_run_dir(runs_dir, specs, *, data_unavailable: bool = False):
    """Build runs_dir/<TARGET_DATE>/ containing strategy_snapshot.yaml,
    event_gate.json, candidates.json, market_data.json, sources.json, and
    ranking.json, all genuinely hash-consistent (produced by the real
    build-ranking CLI), exactly as a production nightly run would."""
    event_gate, candidates, market_data, sources = _build_full_case(specs)

    if data_unavailable:
        sources["source_attempts"][0]["status"] = "ACCESS_FAILED"
        sources["source_attempts"][0]["values"] = None
        sources["source_attempts"][0]["result_count"] = None
        sources["source_attempts"][0]["coverage_status"] = None
        sources["source_attempts"][0]["covered_dates"] = None
        for record in market_data["records"]:
            record["turnover"] = None
        for candidate in candidates["candidates"]:
            candidate["features"]["turnover"]["value"] = None
            candidate["features"]["turnover"]["source_refs"] = []

    run_dir = runs_dir / TARGET_DATE
    run_dir.mkdir(parents=True)

    strategy_snapshot_path = run_dir / "strategy_snapshot.yaml"
    strategy_snapshot_path.write_bytes(DEFAULT_CONFIG_PATH.read_bytes())

    event_gate = copy.deepcopy(event_gate)
    event_gate["input_hashes"]["strategy_snapshot_sha256"] = _sha256_bytes(
        strategy_snapshot_path.read_bytes()
    )

    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        run_dir, event_gate, candidates, market_data, sources
    )
    ranking_path = run_dir / "ranking.json"

    result = cli.main(
        [
            "build-ranking",
            "--event-gate",
            str(event_gate_path),
            "--candidates",
            str(candidates_path),
            "--market-data",
            str(market_data_path),
            "--sources",
            str(sources_path),
            "--source-matrix",
            str(DEFAULT_SOURCE_MATRIX_PATH),
            "--config",
            str(strategy_snapshot_path),
            "--output",
            str(ranking_path),
        ]
    )
    assert result == 0
    return run_dir
