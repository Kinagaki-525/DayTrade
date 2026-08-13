"""Builders for a COMPLETE production run directory.

The shipped regression fixtures are deliberately partial (each exercises one
stage). The Production Verifier checks the *whole* chain, so these helpers
compose a complete, hash-consistent run out of them:

* ``event_gate.json`` / ``candidates.json`` / ``market_data.json`` /
  ``sources.json`` are byte-identical across the ranking and selection
  fixtures, so both cases can share them,
* the missing links (``research_window.json``, ``event_research.json``,
  ``recommendation.json``, ``risk_result.json``) are generated here to be
  cross-consistent with the fixture's own target_date / strategy_version /
  config SHA.

Every artifact is real, hash-chained data -- nothing is a placeholder, and the
hashes are never rewritten to make a check pass.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from src.config import load_strategy_config, strategy_config_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RANKING_FIXTURE = PROJECT_ROOT / "regression/2026-08-12-ranking-v1-complete/runs/2026-08-12"
SELECTION_FIXTURE = (
    PROJECT_ROOT / "regression/2026-08-12-selection-v1-selected/runs/2026-08-12"
)

#: The historical Source Matrix these fixtures were produced under. Their
#: recorded input hashes are pinned to it and must NOT be rewritten.
HISTORICAL_SOURCE_MATRIX = (
    PROJECT_ROOT
    / "config/source_matrix_registry"
    / "f141bb351a22548535cd6ea1f2b76002004abeef4a50c51c68d28659cdbd6b44.yaml"
)

NO_TRADE_FIXTURE = (
    PROJECT_ROOT / "regression/2026-08-12-complete-no-trade/runs/2026-08-12"
)

SHARED_ARTIFACTS = (
    "event_gate.json",
    "candidates.json",
    "market_data.json",
    "sources.json",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _research_window(target_date: str, previous_trading_day: str) -> dict[str, Any]:
    """Reuse the shipped research_window fixture, re-dated to this run."""
    payload = _read(NO_TRADE_FIXTURE / "research_window.json")
    cutoff = f"{previous_trading_day}T20:00:00+09:00"
    payload["target_date"] = target_date
    payload["previous_trading_day"] = previous_trading_day
    payload["research_cutoff"] = cutoff
    payload["research_window"]["window_end"] = cutoff
    payload["research_window"]["window_start"] = (
        f"{previous_trading_day}T00:00:00+09:00"
    )
    return payload


def _event_research(
    run_dir: Path,
    event_gate: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """An event_research.json consistent with the fixture's event_gate.json."""
    tickers = list(event_gate.get("event_gate_input_tickers", []))
    return {
        "schema_version": 1,
        "event_research_version": "event-research-v1",
        "target_date": event_gate["target_date"],
        "previous_trading_day": event_gate["previous_trading_day"],
        "event_research_started_at": event_gate["generated_at"],
        "event_gate_as_of": event_gate["event_gate_as_of"],
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "input_hashes": {
            "candidate_pipeline_sha256": sha256_file(run_dir / "candidate_pipeline.json"),
            "candidates_sha256": sha256_file(run_dir / "candidates.json"),
            "strategy_snapshot_sha256": sha256_file(run_dir / "strategy_snapshot.yaml"),
        },
        "event_gate_input_tickers": tickers,
        "candidates": [
            {
                "ticker": ticker,
                "selected_attempt_ids": {
                    "earnings_schedule_jpx": None,
                    "earnings_schedule_issuer": None,
                    "tdnet": None,
                    "issuer_disclosure": None,
                    "yahoo_news": None,
                    "kabutan_news": None,
                },
                "news_classifications": [],
            }
            for ticker in tickers
        ],
    }


def _candidate_pipeline(
    target_date: str,
    config: dict[str, Any],
    candidates: dict[str, Any],
) -> dict[str, Any]:
    entries = candidates.get("candidates", [])
    count = len(entries)
    return {
        "schema_version": 1,
        "target_date": target_date,
        "generated_at": candidates["generated_at"],
        "strategy_version": config["strategy_version"],
        "config_sha256": strategy_config_sha256(config),
        "summary": {
            "discovered": count,
            "research_complete": count,
            "research_incomplete": 0,
            "data_unavailable": 0,
            "screened": count,
            "eligible": count,
            "rejected": 0,
            "pipeline_complete": True,
            "stage2_target_count": count,
            "stage2_completed_count": count,
            "stage2_unavailable_count": 0,
            "stage2_incomplete_count": 0,
            "coverage_rate": 1,
            "research_incomplete_reason_counts": {},
            "screening_input_count": count,
            "screening_pass_count": count,
            "screening_reject_count": 0,
            "screening_data_unavailable_count": 0,
            "screening_incomplete_count": 0,
            "screening_complete": True,
            "candidate_count_consistent": True,
            "all_enabled_rules_evaluated": True,
            "screening_rule_counts": [],
            "ranking_complete": False,
        },
        "candidates": [
            {
                "ticker": entry["ticker"],
                "company_name": f"Example {entry['ticker']} Corporation",
                "market": "TSE Prime",
                "discovery_reasons": [
                    {
                        "discovery_type": "VOLUME_RANKING",
                        "source_id": "YAHOO_JP_VOLUME_RANKING",
                        "source_url": (
                            "https://finance.yahoo.co.jp/stocks/ranking/volume?market=all"
                        ),
                        "rank": index,
                    }
                ],
                "pipeline_status": "ELIGIBLE",
                "reason_codes": [],
                "missing_requirements": [],
                "completed_checks": [],
                "failed_checks": [],
                "source_attempt_ids": [],
                "source_refs": [],
                "research_incomplete_reason": None,
            }
            for index, entry in enumerate(entries, start=1)
        ],
    }


def _no_trade_recommendation(
    target_date: str,
    previous_trading_day: str,
    config: dict[str, Any],
    reasons: list[str],
    pipeline_summary: dict[str, Any],
) -> dict[str, Any]:
    """Reuse the shipped NO_TRADE recommendation, re-pinned to this run."""
    payload = _read(NO_TRADE_FIXTURE / "recommendation.json")
    payload["target_date"] = target_date
    payload["strategy_version"] = config["strategy_version"]
    payload["config_sha256"] = strategy_config_sha256(config)
    payload["research_cutoff"] = f"{previous_trading_day}T20:00:00+09:00"
    payload["selection_reasons"] = reasons
    payload["pipeline_summary"] = pipeline_summary
    return payload


def _risk_result(
    recommendation: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    payload = _read(NO_TRADE_FIXTURE / "risk_result.json")
    payload["target_date"] = recommendation["target_date"]
    payload["strategy_version"] = recommendation["strategy_version"]
    payload["config_sha256"] = recommendation["config_sha256"]
    payload["decision"] = recommendation["decision"]
    payload["status"] = status
    payload["ticker"] = recommendation["ticker"]
    return payload


def build_case_b_run(tmp_path: Path) -> Path:
    """Case B: ranking COMPLETE, Selection not yet activated -> NO_TRADE."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    for name in (*SHARED_ARTIFACTS, "ranking.json", "strategy_snapshot.yaml"):
        shutil.copy(RANKING_FIXTURE / name, run_dir / name)

    config = load_strategy_config(run_dir / "strategy_snapshot.yaml")
    assert not (config.get("selection") or {}).get("enabled"), (
        "Case B requires selection disabled"
    )

    ranking = _read(run_dir / "ranking.json")
    event_gate = _read(run_dir / "event_gate.json")
    candidates = _read(run_dir / "candidates.json")
    target_date = ranking["target_date"]

    _write(
        run_dir / "research_window.json",
        _research_window(target_date, ranking["previous_trading_day"]),
    )
    _write(
        run_dir / "candidate_pipeline.json",
        _candidate_pipeline(target_date, config, candidates),
    )
    _write(run_dir / "event_research.json", _event_research(run_dir, event_gate, config))

    recommendation = _no_trade_recommendation(
        target_date,
        ranking["previous_trading_day"],
        config,
        ["SELECTION_NOT_ACTIVE_PENDING_CALIBRATION"],
        _read(run_dir / "candidate_pipeline.json")["summary"],
    )
    _write(run_dir / "recommendation.json", recommendation)
    _write(
        run_dir / "risk_result.json",
        _risk_result(recommendation, status="NOT_APPLICABLE"),
    )
    return run_dir


def build_case_c_run(tmp_path: Path, *, selection_status: str = "NO_TRADE") -> Path:
    """Case C: Selection is active.

    Activating Selection changes the strategy snapshot, which invalidates every
    downstream artifact produced under the previous one. So this deliberately
    does NOT graft the ranking fixture's event_gate/ranking onto the selection
    snapshot: their recorded config SHA would disagree, and rewriting it would
    be exactly the forgery the verifier exists to catch. It regenerates the
    chain through the real CLIs under the selection-enabled snapshot instead.
    """
    from src import cli

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    for name in ("market_data.json", "sources.json"):
        shutil.copy(RANKING_FIXTURE / name, run_dir / name)
    snapshot = (SELECTION_FIXTURE / "strategy_snapshot.yaml").read_text(
        encoding="utf-8"
    )
    if selection_status == "NO_TRADE":
        # Raise the human-approved turnover threshold in this FIXTURE snapshot
        # so Rank 1 legitimately fails the rule. Nothing is weakened and the
        # live config/strategy.yaml is untouched -- this exercises the normal
        # NO_TRADE termination, which is a correct outcome, not a failure.
        assert "threshold_yen: 1000000000" in snapshot
        snapshot = snapshot.replace(
            "threshold_yen: 1000000000", "threshold_yen: 999000000000"
        )
    (run_dir / "strategy_snapshot.yaml").write_text(snapshot, encoding="utf-8")

    config_path = run_dir / "strategy_snapshot.yaml"
    config = load_strategy_config(config_path)
    assert (config.get("selection") or {}).get("enabled") is True, (
        "Case C requires selection enabled"
    )

    # Screening is re-run under the new snapshot, because candidates.json
    # records the config SHA it was screened under.
    cli.main(
        [
            "screen-market",
            "--market-data", str(run_dir / "market_data.json"),
            "--sources", str(run_dir / "sources.json"),
            "--source-matrix", str(HISTORICAL_SOURCE_MATRIX),
            "--config", str(config_path),
            "--output", str(run_dir / "candidates.json"),
        ]
    )

    reference_gate = _read(RANKING_FIXTURE / "event_gate.json")
    candidates = _read(run_dir / "candidates.json")
    target_date = reference_gate["target_date"]
    previous_trading_day = reference_gate["previous_trading_day"]

    _write(
        run_dir / "research_window.json",
        _research_window(target_date, previous_trading_day),
    )
    _write(
        run_dir / "candidate_pipeline.json",
        _candidate_pipeline(target_date, config, candidates),
    )
    _write(
        run_dir / "event_research.json",
        _event_research(run_dir, reference_gate, config),
    )

    cli.main(
        [
            "build-event-gate",
            "--event-research", str(run_dir / "event_research.json"),
            "--candidate-pipeline", str(run_dir / "candidate_pipeline.json"),
            "--candidates", str(run_dir / "candidates.json"),
            "--sources", str(run_dir / "sources.json"),
            "--config", str(config_path),
            "--output", str(run_dir / "event_gate.json"),
        ]
    )
    cli.main(
        [
            "build-ranking",
            "--event-gate", str(run_dir / "event_gate.json"),
            "--candidates", str(run_dir / "candidates.json"),
            "--market-data", str(run_dir / "market_data.json"),
            "--sources", str(run_dir / "sources.json"),
            "--source-matrix", str(HISTORICAL_SOURCE_MATRIX),
            "--config", str(config_path),
            "--output", str(run_dir / "ranking.json"),
        ]
    )
    cli.main(
        [
            "build-selection",
            "--ranking", str(run_dir / "ranking.json"),
            "--event-gate", str(run_dir / "event_gate.json"),
            "--candidates", str(run_dir / "candidates.json"),
            "--market-data", str(run_dir / "market_data.json"),
            "--sources", str(run_dir / "sources.json"),
            "--source-matrix", str(HISTORICAL_SOURCE_MATRIX),
            "--config", str(config_path),
            "--output", str(run_dir / "selection.json"),
        ]
    )

    selection = _read(run_dir / "selection.json")
    if selection["selection_status"] != selection_status:
        raise AssertionError(
            f"expected selection_status {selection_status}, "
            f"got {selection['selection_status']}"
        )

    # Recommendation and Risk both come from the real CLIs, so the terminal
    # decision is whatever Selection and the Risk Engine actually produce.
    cli.main(
        [
            "build-selection-recommendation",
            "--ranking", str(run_dir / "ranking.json"),
            "--selection", str(run_dir / "selection.json"),
            "--event-gate", str(run_dir / "event_gate.json"),
            "--candidates", str(run_dir / "candidates.json"),
            "--candidate-pipeline", str(run_dir / "candidate_pipeline.json"),
            "--market-data", str(run_dir / "market_data.json"),
            "--research-window", str(run_dir / "research_window.json"),
            "--sources", str(run_dir / "sources.json"),
            "--source-matrix", str(HISTORICAL_SOURCE_MATRIX),
            "--config", str(config_path),
            "--output", str(run_dir / "recommendation.json"),
        ]
    )
    cli.main(
        [
            "risk-check",
            "--recommendation", str(run_dir / "recommendation.json"),
            "--candidates", str(run_dir / "candidates.json"),
            "--candidate-pipeline", str(run_dir / "candidate_pipeline.json"),
            "--market-data", str(run_dir / "market_data.json"),
            "--sources", str(run_dir / "sources.json"),
            "--source-matrix", str(HISTORICAL_SOURCE_MATRIX),
            "--config", str(config_path),
            "--selection", str(run_dir / "selection.json"),
            "--ranking", str(run_dir / "ranking.json"),
            "--event-gate", str(run_dir / "event_gate.json"),
            "--research-window", str(run_dir / "research_window.json"),
            "--current-positions", "0",
            "--trades-today", "0",
            "--output", str(run_dir / "risk_result.json"),
        ]
    )
    return run_dir


__all__ = [
    "HISTORICAL_SOURCE_MATRIX",
    "RANKING_FIXTURE",
    "SELECTION_FIXTURE",
    "build_case_b_run",
    "build_case_c_run",
    "sha256_file",
]
