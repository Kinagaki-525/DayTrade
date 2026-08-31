"""Research Window resolution, including the history scan.

The history scan is the part with teeth: it decides which past run a nightly
inherits its window start from. These tests drive it against real directories
on disk rather than monkeypatched helpers, because the questions that matter --
"is the newest directory always the answer?", "does a corrupt run fail closed?",
"is history left byte-identical?" -- are only meaningful against real files.
"""

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from src.research import merge_discovery_candidates
from src.research_window import resolve_research_window
from src.source_matrix import load_source_matrix
from tests.factories import make_candidate_research
from tests.test_market_research import complete_candidate_research, market_research_payload


# ------------------------------------------------------------- fixtures ---


def _at_cutoff(day: date) -> str:
    return f"{day.isoformat()}T20:00:00+09:00"


def market_research_for(run_date: str, *, overall_status: str = "COMPLETE") -> dict:
    """A schema-valid ``market_research.json`` payload dated ``run_date``."""
    target = date.fromisoformat(run_date)
    previous = target - timedelta(days=1)
    payload = complete_candidate_research(market_research_payload())
    payload["target_date"] = target.isoformat()
    payload["previous_trading_day"] = previous.isoformat()
    payload["research_cutoff"] = _at_cutoff(previous)
    payload["research_executed_at"] = f"{previous.isoformat()}T20:15:00+09:00"
    payload["research_window"] = {
        "run_type": "FIRST_RUN",
        "window_start": _at_cutoff(previous - timedelta(days=1)),
        "window_end": _at_cutoff(previous),
        "previous_research_cutoff": None,
        "previous_run_date": None,
        "bootstrap_lookback_days": 1,
    }
    payload["overall_status"] = overall_status
    return payload


def sources_for(run_date: str) -> dict:
    return {
        "schema_version": 3,
        "target_date": run_date,
        "sources": [],
        "source_attempts": [],
    }


#: A Source Ledger v3 attempt plus the Source Record its Attempt Value implies.
#: Built from the real Source Matrix definition so the canonical verifier has
#: something to verify -- an empty ledger proves nothing about integrity.
LEDGER_SOURCE_ID = "JPX_TRADING_UNIT"
LEDGER_URL = "https://www.jpx.co.jp/equities/trading/domestic/03.html"
LEDGER_PAGE = "source_pages/jpx_trading_unit.html"
LEDGER_PAGE_BYTES = b"<html>trading unit: 100</html>"
LEDGER_REF = "JPX_TRADING_UNIT:global:share_unit"


def ledger_sources_for(run_date: str) -> dict:
    """A ``sources.json`` with one FOUND attempt and its matching record."""
    previous = (date.fromisoformat(run_date) - timedelta(days=1)).isoformat()
    retrieved_at = f"{previous}T18:30:00+09:00"
    payload = sources_for(run_date)
    payload["source_attempts"] = [
        {
            "attempt_id": f"att_jpx_trading_unit_{run_date.replace('-', '')}_001",
            "source_id": LEDGER_SOURCE_ID,
            "source_role": "PRIMARY",
            "criticality": "TRADE_CRITICAL",
            "information_type": "TRADING_UNIT",
            "candidate_code": None,
            "target_date": run_date,
            "research_cutoff": _at_cutoff(date.fromisoformat(previous)),
            "requested_at": retrieved_at,
            "retrieved_at": retrieved_at,
            "url": LEDGER_URL,
            "source_page_path": LEDGER_PAGE,
            "source_page_sha256": hashlib.sha256(LEDGER_PAGE_BYTES).hexdigest(),
            "source_page_size_bytes": len(LEDGER_PAGE_BYTES),
            "cache_status": "MISS",
            "acquisition_method": "HTTP_GET",
            "http_status": 200,
            "content_type": "text/html",
            "network_request_performed": True,
            "request_id": "req-" + hashlib.sha256(run_date.encode()).hexdigest()[:32],
            "reused_from_attempt_id": None,
            "transport_exit_code": 0,
            "status": "FOUND",
            "result_count": 1,
            "values": [
                {
                    "source_ref": LEDGER_REF,
                    "ticker": None,
                    "field_name": "share_unit",
                    "trading_date": previous,
                    "value": 100,
                }
            ],
        }
    ]
    payload["sources"] = [
        {
            "source_ref": LEDGER_REF,
            "source_id": LEDGER_SOURCE_ID,
            "source_role": "PRIMARY",
            "information_type": "TRADING_UNIT",
            "source_status": "FOUND",
            "source_name": "JPX Domestic Stock Trading Unit Rule",
            "source_url": LEDGER_URL,
            "retrieved_at": retrieved_at,
            "trading_date": previous,
            "ticker": None,
            "field_name": "share_unit",
            "value": 100,
        }
    ]
    return payload


def write_source_page(run_dir: Path, content: bytes = LEDGER_PAGE_BYTES) -> Path:
    page = run_dir / LEDGER_PAGE
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(content)
    return page


def write_run(
    runs_dir: Path,
    run_date: str,
    *,
    research: dict | str | None = "default",
    sources: dict | str | None = "default",
    overall_status: str = "COMPLETE",
) -> Path:
    """Materialise one history run directory.

    ``None`` omits a file; a ``str`` writes it verbatim (for corrupt bytes);
    ``"default"`` writes the canonical payload.
    """
    run_dir = runs_dir / run_date
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, value, default in (
        ("market_research.json", research, lambda: market_research_for(run_date, overall_status=overall_status)),
        ("sources.json", sources, lambda: sources_for(run_date)),
    ):
        if value is None:
            continue
        if value == "default":
            text = json.dumps(default())
        elif isinstance(value, str):
            text = value
        else:
            text = json.dumps(value)
        (run_dir / name).write_text(text, encoding="utf-8")
    return run_dir


def resolve(runs_dir: Path, *, target_date: str, previous_trading_day: str) -> dict:
    return resolve_research_window(
        target_date=target_date,
        previous_trading_day=previous_trading_day,
        runs_dir=runs_dir,
        source_matrix=load_source_matrix(),
    ).as_dict()


def digest_tree(runs_dir: Path) -> dict[str, tuple[int, str]]:
    """Every file under ``runs_dir`` as ``path -> (size, sha256)``."""
    return {
        str(path.relative_to(runs_dir)): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(runs_dir.rglob("*"))
        if path.is_file()
    }


# ------------------------------------------------------------- baseline ---


def test_resolve_research_window_returns_first_run_without_history():
    result = resolve_research_window(
        target_date="2026-08-10",
        previous_trading_day="2026-08-07",
        runs_dir=Path("does-not-exist-for-first-run-test"),
        source_matrix=load_source_matrix(),
    ).as_dict()

    assert result["research_cutoff"] == "2026-08-07T20:00:00+09:00"
    assert result["post_cutoff_information_status"] == "OUT_OF_SCOPE"
    assert result["research_window"] == {
        "run_type": "FIRST_RUN",
        "window_start": "2026-08-06T20:00:00+09:00",
        "window_end": "2026-08-07T20:00:00+09:00",
        "previous_research_cutoff": None,
        "previous_run_date": None,
        "bootstrap_lookback_days": 1,
    }


def test_resolve_research_window_returns_normal_run_from_latest_history(tmp_path):
    write_run(tmp_path, "2026-08-10")

    result = resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")

    assert result["research_cutoff"] == "2026-08-10T20:00:00+09:00"
    assert result["post_cutoff_information_status"] == "NO_NON_BUSINESS_GAP"
    assert result["research_window"] == {
        "run_type": "NORMAL_RUN",
        "window_start": "2026-08-09T20:00:00+09:00",
        "window_end": "2026-08-10T20:00:00+09:00",
        "previous_research_cutoff": "2026-08-09T20:00:00+09:00",
        "previous_run_date": "2026-08-10",
        "bootstrap_lookback_days": None,
    }


def test_selected_history_supplies_the_exact_window_start_cutoff(tmp_path):
    """The window start is the selected run's own cutoff, not a recomputed date."""
    write_run(tmp_path, "2026-08-05")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    selected_cutoff = market_research_for("2026-08-05")["research_cutoff"]
    assert selected_cutoff == "2026-08-04T20:00:00+09:00"
    assert window["previous_run_date"] == "2026-08-05"
    assert window["previous_research_cutoff"] == selected_cutoff
    assert window["window_start"] == selected_cutoff


# ------------------------------------------------------- fallback (skip) ---


def test_history_scan_skips_latest_run_missing_market_research(tmp_path):
    """A run that stopped before writing its research is not history."""
    write_run(tmp_path, "2026-08-10", research=None, sources=None)
    write_run(tmp_path, "2026-08-07")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["run_type"] == "NORMAL_RUN"
    assert window["previous_run_date"] == "2026-08-07"
    assert window["window_start"] == "2026-08-06T20:00:00+09:00"


@pytest.mark.parametrize("overall_status", ["PIPELINE_INCOMPLETE", "DISCOVERY_INCOMPLETE"])
def test_history_scan_skips_valid_but_incomplete_latest_run(tmp_path, overall_status):
    """Valid research that never completed establishes no cutoff to inherit."""
    write_run(tmp_path, "2026-08-10", overall_status=overall_status)
    write_run(tmp_path, "2026-08-07")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-07"


def test_history_scan_skips_discovery_incomplete_latest_run(tmp_path):
    """Discovery that never returned is incomplete even if the file says COMPLETE."""
    incomplete = market_research_for("2026-08-10")
    route = incomplete["discovery"][0]
    route["status"] = "NOT_FOUND"
    route["result_count"] = None
    route["items"] = []
    incomplete["discovery_candidates"] = merge_discovery_candidates(
        incomplete["discovery"]
    )
    incomplete["candidate_research"] = [
        make_candidate_research(candidate["ticker"])
        for candidate in incomplete["discovery_candidates"]
    ]
    incomplete["overall_status"] = "DISCOVERY_INCOMPLETE"
    write_run(tmp_path, "2026-08-10", research=incomplete)
    write_run(tmp_path, "2026-08-07")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-07"


def test_history_scan_walks_back_past_several_ineligible_runs(tmp_path):
    write_run(tmp_path, "2026-08-10", research=None, sources=None)
    write_run(tmp_path, "2026-08-09", overall_status="PIPELINE_INCOMPLETE")
    write_run(tmp_path, "2026-08-08", research=None, sources=None)
    write_run(tmp_path, "2026-08-05")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-05"


def test_no_eligible_history_resolves_to_first_run(tmp_path):
    write_run(tmp_path, "2026-08-10", research=None, sources=None)
    write_run(tmp_path, "2026-08-09", overall_status="PIPELINE_INCOMPLETE")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["run_type"] == "FIRST_RUN"
    assert window["previous_run_date"] is None
    assert window["bootstrap_lookback_days"] == 1


# --------------------------------------------------------- fail-closed ---


def test_corrupt_latest_history_fails_closed(tmp_path):
    """Unreadable history is an error -- never a reason to reach further back."""
    write_run(tmp_path, "2026-08-10", research="{not json at all")
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_schema_invalid_latest_history_fails_closed(tmp_path):
    invalid = market_research_for("2026-08-10")
    del invalid["discovery"]
    write_run(tmp_path, "2026-08-10", research=invalid)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_target_date_mismatch_fails_closed(tmp_path):
    """A payload dated differently from its directory is not this run's evidence."""
    mismatched = market_research_for("2026-08-06")
    write_run(tmp_path, "2026-08-10", research=mismatched)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_without_source_ledger_fails_closed(tmp_path):
    """Research cannot be validated apart from the Source Ledger it cites."""
    write_run(tmp_path, "2026-08-10", sources=None)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_with_invalid_source_ledger_fails_closed(tmp_path):
    write_run(tmp_path, "2026-08-10", sources={"schema_version": 3})
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_source_ledger_target_date_mismatch_fails_closed(tmp_path):
    """The ledger must be this run's ledger, not one carried over."""
    stale = sources_for("2026-08-06")
    write_run(tmp_path, "2026-08-10", sources=stale)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_a_verifiable_source_ledger_is_accepted(tmp_path):
    """The integrity fixtures below only mean something if the clean one passes."""
    run_dir = write_run(tmp_path, "2026-08-10", sources=ledger_sources_for("2026-08-10"))
    write_source_page(run_dir)

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-10"


def test_history_source_record_tampered_after_acquisition_fails_closed(tmp_path):
    """Source Record / Attempt Value integrity: a same-ref record that was
    edited after acquisition is rejected exactly like a fabricated one."""
    tampered = ledger_sources_for("2026-08-10")
    tampered["sources"][0]["value"] = 1000
    run_dir = write_run(tmp_path, "2026-08-10", sources=tampered)
    write_source_page(run_dir)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="tampered after acquisition"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_source_record_without_a_backing_attempt_fails_closed(tmp_path):
    unbacked = ledger_sources_for("2026-08-10")
    unbacked["source_attempts"] = []
    run_dir = write_run(tmp_path, "2026-08-10", sources=unbacked)
    write_source_page(run_dir)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="does not trace to any"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_source_page_hash_mismatch_fails_closed(tmp_path):
    """Raw Evidence integrity: the stored page must still be the parsed bytes."""
    run_dir = write_run(tmp_path, "2026-08-10", sources=ledger_sources_for("2026-08-10"))
    write_source_page(run_dir, b"<html>trading unit: 1000</html>")
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="SOURCE_PAGE_HASH_MISMATCH"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_history_missing_source_page_fails_closed(tmp_path):
    """The ledger names a raw page; a ledger whose evidence is gone is invalid."""
    write_run(tmp_path, "2026-08-10", sources=ledger_sources_for("2026-08-10"))
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="source_page_path does not exist"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_a_broken_ledger_never_falls_back_to_an_older_run(tmp_path):
    """The whole point of Fail-Closed: corrupting a run must not let an older
    one silently become the answer."""
    tampered = ledger_sources_for("2026-08-10")
    tampered["sources"][0]["value"] = 1000
    run_dir = write_run(tmp_path, "2026-08-10", sources=tampered)
    write_source_page(run_dir)
    older = write_run(tmp_path, "2026-08-07", sources=ledger_sources_for("2026-08-07"))
    write_source_page(older)

    with pytest.raises(ValueError, match="2026-08-10 Source Ledger failed"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


def test_an_ineligible_run_with_a_valid_ledger_falls_back(tmp_path):
    """Ineligible is not invalid: a verified but incomplete run is stepped over."""
    incomplete = write_run(
        tmp_path,
        "2026-08-10",
        sources=ledger_sources_for("2026-08-10"),
        overall_status="PIPELINE_INCOMPLETE",
    )
    write_source_page(incomplete)
    older = write_run(tmp_path, "2026-08-07", sources=ledger_sources_for("2026-08-07"))
    write_source_page(older)

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-07"


def test_history_failing_market_research_validation_fails_closed(tmp_path):
    """A stage1 REJECT with no source-backed check is a tampering signal."""
    unbacked = market_research_for("2026-08-10")
    unbacked["candidate_research"][0].update(
        {
            "universe_status": "PASSED",
            "stage1_status": "REJECTED",
            "stage2_status": "SKIPPED",
            "context_research_status": "SKIPPED",
            "reason_codes": ["SHARE_UNIT_NOT_100"],
            "missing_requirements": [],
            "stage1_checks": [
                {
                    "check_id": "share_unit",
                    "status": "REJECTED",
                    "reason_code": "SHARE_UNIT_NOT_100",
                    "source_refs": [],
                    "source_attempt_ids": [],
                }
            ],
        }
    )
    write_run(tmp_path, "2026-08-10", research=unbacked)
    write_run(tmp_path, "2026-08-07")

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")


# ------------------------------------------------------------ filtering ---


def test_history_scan_ignores_non_date_children(tmp_path):
    (tmp_path / "README.md").write_text("not a run\n", encoding="utf-8")
    (tmp_path / "working").mkdir()
    (tmp_path / "2026-08-99").mkdir()
    write_run(tmp_path, "2026-08-07")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-07"


def test_history_scan_ignores_the_target_date_and_future_runs(tmp_path):
    """The run being resolved is not its own history, and neither is a later one."""
    write_run(tmp_path, "2026-08-11", research="{corrupt on purpose")
    write_run(tmp_path, "2026-08-20", research="{corrupt on purpose")
    write_run(tmp_path, "2026-08-07")

    window = resolve(
        tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10"
    )["research_window"]

    assert window["previous_run_date"] == "2026-08-07"


# --------------------------------------------------------- immutability ---


def test_history_scan_never_modifies_history(tmp_path):
    """Every historical byte must survive a scan that walks past several runs."""
    write_run(tmp_path, "2026-08-10", research=None, sources=None)
    write_run(tmp_path, "2026-08-09", overall_status="PIPELINE_INCOMPLETE")
    write_run(tmp_path, "2026-08-05")
    before = digest_tree(tmp_path)
    assert before, "the immutability check needs history to guard"

    resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")

    assert digest_tree(tmp_path) == before


def test_history_scan_never_modifies_history_when_failing_closed(tmp_path):
    write_run(tmp_path, "2026-08-10", research="{not json at all")
    write_run(tmp_path, "2026-08-07")
    before = digest_tree(tmp_path)

    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        resolve(tmp_path, target_date="2026-08-11", previous_trading_day="2026-08-10")

    assert digest_tree(tmp_path) == before


# --------------------------------------------------------------- inputs ---


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
