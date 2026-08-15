"""Tests for the shared Downstream Trust Layer (src/downstream_trust.py).

Every fixture here is a genuine, hash-consistent chain produced by the real
CLIs -- nothing is hand-written to make a check pass, and no hash is ever
rewritten to force validity.
"""

from __future__ import annotations

import copy
import json

import pytest

from src import cli
from src.downstream_trust import (
    CASE_A,
    CASE_B,
    CASE_C_NO_TRADE,
    CASE_C_TRADE,
    RISK_INPUT_HASH_KEYS,
    build_risk_result_v2,
    sha256_file_bytes,
    verify_downstream_recommendation_chain,
)
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH
from tests.ranking_terminal_recommendation_fixtures import (
    build_terminal_recommendation_run_dir,
)
from tests.test_ranking import PREVIOUS_TRADING_DAY, TARGET_DATE
from tests.test_cli import complete_pipeline_summary
from tests.test_selection_cli import (
    _build_real_ranking_chain,
    _enabled_selection_config_path,
)

_TERMINAL_SPECS = [
    {"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}
]


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures: genuine chains
# ---------------------------------------------------------------------------


def _terminal_chain(tmp_path, *, data_unavailable: bool):
    """Case A (data_unavailable) or Case B: a genuine Ranking + Terminal
    Recommendation chain built by the real CLIs."""
    run_dir = build_terminal_recommendation_run_dir(
        tmp_path / "runs", _TERMINAL_SPECS, data_unavailable=data_unavailable
    )
    recommendation_path = run_dir / "recommendation.json"
    assert (
        cli.main(
            [
                "build-ranking-terminal-recommendation",
                "--ranking", str(run_dir / "ranking.json"),
                "--event-gate", str(run_dir / "event_gate.json"),
                "--candidates", str(run_dir / "candidates.json"),
                "--candidate-pipeline", str(run_dir / "candidate_pipeline.json"),
                "--market-data", str(run_dir / "market_data.json"),
                "--research-window", str(run_dir / "research_window.json"),
                "--sources", str(run_dir / "sources.json"),
                "--source-matrix", str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config", str(run_dir / "strategy_snapshot.yaml"),
                "--output", str(recommendation_path),
            ]
        )
        == 0
    )
    return {
        "ranking_path": run_dir / "ranking.json",
        "event_gate_path": run_dir / "event_gate.json",
        "candidates_path": run_dir / "candidates.json",
        "candidate_pipeline_path": run_dir / "candidate_pipeline.json",
        "market_data_path": run_dir / "market_data.json",
        "research_window_path": run_dir / "research_window.json",
        "sources_path": run_dir / "sources.json",
        "config_path": run_dir / "strategy_snapshot.yaml",
        "recommendation_path": recommendation_path,
        "source_matrix_path": DEFAULT_SOURCE_MATRIX_PATH,
    }


def _case_c_chain(tmp_path, *, minimum_turnover_yen: int = 1000, ticker: str = "AA01"):
    """A genuine Case C chain (Selection enabled) via the real CLIs.

    ``minimum_turnover_yen`` decides whether Rank 1 legitimately clears the
    Selection rules (SELECTED) or legitimately fails them (NO_TRADE). The
    rule itself is never weakened -- only the fixture's own threshold moves.
    """
    config_path, config = _enabled_selection_config_path(
        tmp_path, minimum_turnover_yen=minimum_turnover_yen
    )
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [
            {
                "ticker": ticker,
                "previous_high": "400",
                "tick_size": "1",
                "raw_value": "50,000",
            }
        ],
    )

    selection_path = tmp_path / "selection.json"
    assert (
        cli.main(
            [
                "build-selection",
                "--ranking", str(paths["ranking_path"]),
                "--event-gate", str(paths["event_gate_path"]),
                "--candidates", str(paths["candidates_path"]),
                "--market-data", str(paths["market_data_path"]),
                "--sources", str(paths["sources_path"]),
                "--source-matrix", str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config", str(config_path),
                "--output", str(selection_path),
            ]
        )
        == 0
    )

    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    from src.config import strategy_config_sha256

    _write_json(
        candidate_pipeline_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "generated_at": "2026-08-09T21:30:00+00:00",
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "summary": complete_pipeline_summary(),
            "candidates": [],
        },
    )
    research_window_path = tmp_path / "research_window.json"
    _write_json(
        research_window_path,
        {
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
        },
    )

    recommendation_path = tmp_path / "recommendation.json"
    assert (
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking", str(paths["ranking_path"]),
                "--selection", str(selection_path),
                "--event-gate", str(paths["event_gate_path"]),
                "--candidates", str(paths["candidates_path"]),
                "--candidate-pipeline", str(candidate_pipeline_path),
                "--market-data", str(paths["market_data_path"]),
                "--research-window", str(research_window_path),
                "--sources", str(paths["sources_path"]),
                "--source-matrix", str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config", str(config_path),
                "--output", str(recommendation_path),
            ]
        )
        == 0
    )

    return {
        "ranking_path": paths["ranking_path"],
        "event_gate_path": paths["event_gate_path"],
        "candidates_path": paths["candidates_path"],
        "candidate_pipeline_path": candidate_pipeline_path,
        "market_data_path": paths["market_data_path"],
        "research_window_path": research_window_path,
        "sources_path": paths["sources_path"],
        "config_path": config_path,
        "selection_path": selection_path,
        "recommendation_path": recommendation_path,
        "source_matrix_path": DEFAULT_SOURCE_MATRIX_PATH,
    }


def _verify(chain, **overrides):
    kwargs = {
        "recommendation_path": chain["recommendation_path"],
        "candidates_path": chain["candidates_path"],
        "candidate_pipeline_path": chain["candidate_pipeline_path"],
        "market_data_path": chain["market_data_path"],
        "sources_path": chain["sources_path"],
        "source_matrix_path": chain["source_matrix_path"],
        "config_path": chain["config_path"],
        "ranking_path": chain["ranking_path"],
        "event_gate_path": chain["event_gate_path"],
        "research_window_path": chain["research_window_path"],
        "selection_path": chain.get("selection_path"),
    }
    kwargs.update(overrides)
    return verify_downstream_recommendation_chain(**kwargs)


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_case_a_genuine_chain_trust_passes(tmp_path):
    chain = _terminal_chain(tmp_path, data_unavailable=True)
    bundle = _verify(chain)
    assert bundle.case_id == CASE_A
    assert bundle.selection is None
    assert bundle.actual_hashes["selection_sha256"] is None
    assert bundle.recommendation["decision"] == "DATA_UNAVAILABLE"


def test_case_b_genuine_chain_trust_passes(tmp_path):
    chain = _terminal_chain(tmp_path, data_unavailable=False)
    bundle = _verify(chain)
    assert bundle.case_id == CASE_B
    assert bundle.selection is None
    assert bundle.actual_hashes["selection_sha256"] is None
    assert bundle.recommendation["decision"] == "NO_TRADE"


def test_case_c_no_trade_genuine_chain_trust_passes(tmp_path):
    chain = _case_c_chain(tmp_path, minimum_turnover_yen=10**15)
    assert _read_json(chain["selection_path"])["selection_status"] == "NO_TRADE"
    bundle = _verify(chain)
    assert bundle.case_id == CASE_C_NO_TRADE
    assert bundle.actual_hashes["selection_sha256"] == sha256_file_bytes(
        chain["selection_path"]
    )


def test_case_c_selected_genuine_chain_trust_passes(tmp_path):
    chain = _case_c_chain(tmp_path)
    assert _read_json(chain["selection_path"])["selection_status"] == "SELECTED"
    bundle = _verify(chain)
    assert bundle.case_id == CASE_C_TRADE
    assert bundle.recommendation["decision"] == "TRADE"


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_case_b_with_selection_artifact_is_hard_error(tmp_path):
    terminal = _terminal_chain(tmp_path / "terminal", data_unavailable=False)
    case_c_dir = tmp_path / "casec"
    case_c_dir.mkdir()
    case_c = _case_c_chain(case_c_dir)
    with pytest.raises(
        ValueError, match="DOWNSTREAM_SELECTION_FORBIDDEN_FOR_TERMINAL_CASE"
    ):
        _verify(terminal, selection_path=case_c["selection_path"])


def test_case_a_with_selection_artifact_is_hard_error(tmp_path):
    terminal = _terminal_chain(tmp_path / "terminal", data_unavailable=True)
    case_c_dir = tmp_path / "casec"
    case_c_dir.mkdir()
    case_c = _case_c_chain(case_c_dir)
    with pytest.raises(
        ValueError, match="DOWNSTREAM_SELECTION_FORBIDDEN_FOR_TERMINAL_CASE"
    ):
        _verify(terminal, selection_path=case_c["selection_path"])


def test_case_c_without_selection_is_hard_error(tmp_path):
    chain = _case_c_chain(tmp_path)
    with pytest.raises(ValueError, match="DOWNSTREAM_SELECTION_REQUIRED"):
        _verify(chain, selection_path=None)


def test_case_c_recommendation_schema_v1_is_hard_error(tmp_path):
    chain = _case_c_chain(tmp_path, minimum_turnover_yen=10**15)
    recommendation = _read_json(chain["recommendation_path"])
    recommendation["schema_version"] = 1
    _write_json(chain["recommendation_path"], recommendation)
    with pytest.raises(
        ValueError, match="DOWNSTREAM_RECOMMENDATION_SCHEMA_VERSION_INVALID"
    ):
        _verify(chain)


def test_case_b_recommendation_schema_v2_is_hard_error(tmp_path):
    chain = _terminal_chain(tmp_path, data_unavailable=False)
    recommendation = _read_json(chain["recommendation_path"])
    recommendation["schema_version"] = 2
    recommendation["selection_sha256"] = "0" * 64
    _write_json(chain["recommendation_path"], recommendation)
    with pytest.raises(
        ValueError, match="DOWNSTREAM_RECOMMENDATION_SCHEMA_VERSION_INVALID"
    ):
        _verify(chain)


def test_case_c_forged_selection_business_content_is_hard_error(tmp_path):
    """selection.json whose recorded input_hashes are all *correct* (the
    attacker recomputed them honestly) but whose business content claims a
    SELECTED outcome the real Selection rules never produced."""
    chain = _case_c_chain(tmp_path, minimum_turnover_yen=10**15)
    genuine = _read_json(chain["selection_path"])
    assert genuine["selection_status"] == "NO_TRADE"

    forged = copy.deepcopy(genuine)
    forged["selection_status"] = "SELECTED"
    forged["selected_ticker"] = forged["evaluated_ticker"]
    forged["reason_codes"] = ["SELECTION_ALL_RULES_PASSED"]
    for rule in forged["rule_evaluations"]:
        rule["result"] = "PASS"
        rule["reason_code"] = None
    _write_json(chain["selection_path"], forged)
    # The hash string link is intact: only the business content is forged.
    assert forged["input_hashes"]["ranking_sha256"] == sha256_file_bytes(
        chain["ranking_path"]
    )

    with pytest.raises(ValueError, match="SELECTION_OUTPUT_CONTRACT_MISMATCH"):
        _verify(chain)


def test_case_c_forged_recommendation_price_is_hard_error(tmp_path):
    chain = _case_c_chain(tmp_path)
    recommendation = _read_json(chain["recommendation_path"])
    recommendation["entry_limit"] = str(int(float(recommendation["entry_limit"])) + 5)
    _write_json(chain["recommendation_path"], recommendation)
    with pytest.raises(
        ValueError, match="SELECTION_RECOMMENDATION_OUTPUT_CONTRACT_MISMATCH"
    ):
        _verify(chain)


# ---------------------------------------------------------------------------
# build_risk_result_v2
# ---------------------------------------------------------------------------


def test_risk_result_v2_trade_context_is_persisted(tmp_path):
    chain = _case_c_chain(tmp_path)
    bundle = _verify(chain)
    payload = build_risk_result_v2(
        bundle=bundle, current_positions=0, trades_today=0
    )
    assert payload["schema_version"] == 2
    assert payload["evaluation_context"] == {"current_positions": 0, "trades_today": 0}
    assert tuple(sorted(payload["input_hashes"])) == tuple(sorted(RISK_INPUT_HASH_KEYS))
    assert payload["input_hashes"]["selection_sha256"] == sha256_file_bytes(
        chain["selection_path"]
    )
    assert payload["input_hashes"]["market_research_sha256"] is None
    assert payload["status"] in ("PASS", "REJECTED")


def test_risk_result_v2_non_trade_context_is_normalized_to_null(tmp_path):
    chain = _case_c_chain(tmp_path, minimum_turnover_yen=10**15)
    bundle = _verify(chain)
    # Even though a caller passed 0/0, a non-TRADE artifact records null/null.
    payload = build_risk_result_v2(
        bundle=bundle, current_positions=0, trades_today=0
    )
    assert payload["evaluation_context"] == {
        "current_positions": None,
        "trades_today": None,
    }
    assert payload["status"] == "NOT_APPLICABLE"
    assert payload["input_hashes"]["selection_sha256"] == sha256_file_bytes(
        chain["selection_path"]
    )


def test_risk_result_v2_terminal_case_selection_hash_is_null(tmp_path):
    chain = _terminal_chain(tmp_path, data_unavailable=False)
    bundle = _verify(chain)
    payload = build_risk_result_v2(
        bundle=bundle, current_positions=None, trades_today=None
    )
    assert payload["input_hashes"]["selection_sha256"] is None
    assert payload["evaluation_context"] == {
        "current_positions": None,
        "trades_today": None,
    }


def _market_research_stub(tmp_path, ticker="AA01"):
    """A minimal, schema-valid-shaped market_research.json stand-in.

    It is never read in a non-TRADE case -- which is exactly the point: its
    hash must not be recorded even though it exists and was supplied.
    """
    path = tmp_path / "market_research.json"
    _write_json(path, {"discovery_candidates": [{"ticker": ticker}]})
    return path


@pytest.mark.parametrize("data_unavailable", [True, False])
def test_risk_result_v2_terminal_case_market_research_hash_is_null(
    tmp_path, data_unavailable
):
    """FIX-R2-003A section 5: Case A / Case B never consume market_research."""
    chain = _terminal_chain(tmp_path, data_unavailable=data_unavailable)
    bundle = _verify(chain)
    payload = build_risk_result_v2(
        bundle=bundle,
        current_positions=None,
        trades_today=None,
        market_research_path=_market_research_stub(tmp_path),
    )
    assert payload["input_hashes"]["market_research_sha256"] is None


def test_risk_result_v2_case_c_no_trade_market_research_hash_is_null(tmp_path):
    chain = _case_c_chain(tmp_path, minimum_turnover_yen=10**15)
    bundle = _verify(chain)
    assert bundle.case_id == CASE_C_NO_TRADE
    payload = build_risk_result_v2(
        bundle=bundle,
        current_positions=0,
        trades_today=0,
        market_research_path=_market_research_stub(tmp_path),
    )
    assert payload["input_hashes"]["market_research_sha256"] is None
    assert payload["evaluation_context"] == {
        "current_positions": None,
        "trades_today": None,
    }


@pytest.mark.parametrize("bad", [None, True, -1, "0", 1.0])
def test_risk_result_v2_trade_requires_non_negative_int_context(tmp_path, bad):
    chain = _case_c_chain(tmp_path)
    bundle = _verify(chain)
    with pytest.raises(ValueError, match="RISK_EVALUATION_CONTEXT_INVALID"):
        build_risk_result_v2(bundle=bundle, current_positions=bad, trades_today=0)
    with pytest.raises(ValueError, match="RISK_EVALUATION_CONTEXT_INVALID"):
        build_risk_result_v2(bundle=bundle, current_positions=0, trades_today=bad)
