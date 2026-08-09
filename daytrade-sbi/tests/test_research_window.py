from copy import deepcopy
from pathlib import Path

import pytest

import src.research_window as research_window
from src.research_window import resolve_research_window
from src.source_matrix import load_source_matrix
from tests.test_market_research import complete_candidate_research, market_research_payload


def test_resolve_research_window_returns_first_run_without_history():
    result = resolve_research_window(
        target_date="2026-08-10",
        previous_trading_day="2026-08-07",
        runs_dir=Path("does-not-exist-for-first-run-test"),
        source_matrix=load_source_matrix(),
    ).as_dict()

    assert result["research_cutoff"] == "2026-08-07T20:00:00+09:00"
    assert result["research_window"] == {
        "run_type": "FIRST_RUN",
        "window_start": "2026-08-06T20:00:00+09:00",
        "window_end": "2026-08-07T20:00:00+09:00",
        "previous_research_cutoff": None,
        "previous_run_date": None,
        "bootstrap_lookback_days": 1,
    }


def test_resolve_research_window_returns_normal_run_from_latest_history(monkeypatch):
    monkeypatch.setattr(
        research_window,
        "_latest_previous_run",
        lambda runs_dir, target_date: Path("2026-08-10"),
    )
    monkeypatch.setattr(
        research_window,
        "_load_valid_previous_market_research",
        lambda run_dir, source_matrix: complete_candidate_research(market_research_payload()),
    )

    result = resolve_research_window(
        target_date="2026-08-11",
        previous_trading_day="2026-08-10",
        runs_dir=Path("unused"),
        source_matrix=load_source_matrix(),
    ).as_dict()

    assert result["research_cutoff"] == "2026-08-10T20:00:00+09:00"
    assert result["research_window"] == {
        "run_type": "NORMAL_RUN",
        "window_start": "2026-08-07T20:00:00+09:00",
        "window_end": "2026-08-10T20:00:00+09:00",
        "previous_research_cutoff": "2026-08-07T20:00:00+09:00",
        "previous_run_date": "2026-08-10",
        "bootstrap_lookback_days": None,
    }


def test_resolve_research_window_rejects_latest_history_missing_research(monkeypatch):
    monkeypatch.setattr(
        research_window,
        "_latest_previous_run",
        lambda runs_dir, target_date: Path("2026-08-10"),
    )

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve_research_window(
            target_date="2026-08-11",
            previous_trading_day="2026-08-10",
            runs_dir=Path("unused"),
            source_matrix=load_source_matrix(),
        )


def test_resolve_research_window_rejects_unconfirmed_tdnet_history(monkeypatch):
    payload = complete_candidate_research(market_research_payload())
    payload["discovery"][2]["status"] = "ACCESS_FAILED"
    payload["discovery"][2]["reason"] = "network error"
    payload["overall_status"] = "DISCOVERY_INCOMPLETE"

    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(
        research_window,
        "load_json_document",
        lambda path, schema: payload,
    )

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        research_window._load_valid_previous_market_research(
            Path("2026-08-10"),
            load_source_matrix(),
        )


def test_resolve_research_window_rejects_invalid_bootstrap_setting():
    source_matrix = deepcopy(load_source_matrix())
    source_matrix["tdnet_bootstrap_lookback_days"] = 0

    with pytest.raises(ValueError, match="tdnet_bootstrap_lookback_days"):
        resolve_research_window(
            target_date="2026-08-10",
            previous_trading_day="2026-08-07",
            runs_dir=Path("does-not-exist-for-invalid-setting-test"),
            source_matrix=source_matrix,
        )


def test_resolve_research_window_rejects_invalid_date_order():
    with pytest.raises(ValueError, match="previous_trading_day must be before target_date"):
        resolve_research_window(
            target_date="2026-08-10",
            previous_trading_day="2026-08-10",
            runs_dir=Path("unused"),
            source_matrix=load_source_matrix(),
        )
