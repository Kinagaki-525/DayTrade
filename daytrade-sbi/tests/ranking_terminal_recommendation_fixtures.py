"""Shared test-only helper for building a genuine, fully hash-consistent
build-ranking-terminal-recommendation input set by driving the real
build-ranking CLI, mirroring tests/selection_calibration_fixtures.py's
approach for Calibration."""

from __future__ import annotations

import copy
import json

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, strategy_config_sha256
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

from tests.selection_calibration_fixtures import build_calibration_run_dir
from tests.test_ranking import PREVIOUS_TRADING_DAY, TARGET_DATE
from tests.test_cli import complete_pipeline_summary


def build_terminal_recommendation_run_dir(runs_dir, specs, *, data_unavailable: bool = False):
    """Build runs_dir/<TARGET_DATE>/ containing a genuine, hash-consistent
    ranking.json (via the real build-ranking CLI, same as Calibration) plus
    genuine candidate_pipeline.json / research_window.json siblings, ready
    to feed into build-ranking-terminal-recommendation."""
    run_dir = build_calibration_run_dir(runs_dir, specs, data_unavailable=data_unavailable)

    config = load_strategy_config(run_dir / "strategy_snapshot.yaml")
    config_sha = strategy_config_sha256(config)

    candidates = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))

    candidate_pipeline = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "generated_at": "2026-08-09T21:30:00+00:00",
        "strategy_version": config["strategy_version"],
        "config_sha256": config_sha,
        "summary": complete_pipeline_summary(),
        "candidates": [],
    }
    (run_dir / "candidate_pipeline.json").write_text(
        json.dumps(candidate_pipeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    research_window = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "previous_trading_day": PREVIOUS_TRADING_DAY,
        "research_cutoff": "2026-08-09T21:00:00+00:00",
        "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
        "research_window": {
            "run_type": "FIRST_RUN",
            "window_start": "2026-08-09T00:00:00+00:00",
            "window_end": "2026-08-09T21:00:00+00:00",
            "previous_research_cutoff": None,
            "previous_run_date": None,
            "bootstrap_lookback_days": 5,
        },
    }
    (run_dir / "research_window.json").write_text(
        json.dumps(research_window, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return run_dir
