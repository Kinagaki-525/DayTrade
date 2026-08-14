"""FIX-010: the Production Verifier checks the WHOLE artifact chain.

The previous version of this module accepted a "run" consisting of nothing but
``sources.json`` + ``recommendation.json``, and treated Case C NO_TRADE as a
failure. Both are wrong:

* a run is verified when the whole chain is present, schema-valid and
  cross-consistent -- two parseable files is not a run;
* NO_TRADE is a correct, normal termination. Demanding a TRADE would mean the
  verifier could only ever be satisfied by weakening Selection or Risk.

The positive fixtures come from :mod:`tests.production_run_fixtures`, which
regenerates the chain through the real CLIs rather than grafting artifacts
from different snapshots onto each other.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.production_verify import (
    INVALID_RUN,
    REQUIRED_ARTIFACTS,
    VERIFIED_CASE_A,
    VERIFIED_CASE_B,
    VERIFIED_CASE_C_NO_TRADE,
    VERIFIED_CASE_C_TRADE_RISK_PASS,
    VERIFIED_STATUSES,
    network_audit,
    verify_production_happy_path,
    verify_production_run,
)
from tests.production_run_fixtures import (
    HISTORICAL_SOURCE_MATRIX,
    build_case_b_run,
    build_case_c_run,
    sha256_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NO_TRADE_FIXTURE = (
    PROJECT_ROOT / "regression/2026-08-12-complete-no-trade/runs/2026-08-12"
)


def _verify(run_dir: Path):
    return verify_production_run(run_dir, source_matrix_path=HISTORICAL_SOURCE_MATRIX)


def _read(run_dir: Path, name: str) -> dict:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def _write(run_dir: Path, name: str, payload: dict) -> None:
    (run_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture()
def case_b(tmp_path):
    return build_case_b_run(tmp_path)


@pytest.fixture()
def case_c_no_trade(tmp_path):
    return build_case_c_run(tmp_path, selection_status="NO_TRADE")


@pytest.fixture()
def case_c_selected(tmp_path):
    return build_case_c_run(tmp_path, selection_status="SELECTED")


# ------------------------------------------------------------- positives ---


def test_case_b_is_valid(case_b):
    """Ranking COMPLETE + selection disabled -> NO_TRADE pending calibration."""
    report = _verify(case_b)
    assert report.status == VERIFIED_CASE_B, report.errors


def test_case_c_no_trade_is_valid_happy_path(case_c_no_trade):
    """Selection active, Rank 1 did not clear the thresholds.

    This is a NORMAL termination, not a failure: there is no Rank-2 fallback,
    so NO_TRADE is exactly what a sound run should produce here.
    """
    report = _verify(case_c_no_trade)
    assert report.status == VERIFIED_CASE_C_NO_TRADE, report.errors

    happy = verify_production_happy_path(
        case_c_no_trade, source_matrix_path=HISTORICAL_SOURCE_MATRIX
    )
    assert happy.status == VERIFIED_CASE_C_NO_TRADE, happy.errors
    assert happy.status in VERIFIED_STATUSES


def test_case_c_trade_is_valid(case_c_selected):
    report = _verify(case_c_selected)
    assert report.status == VERIFIED_CASE_C_TRADE_RISK_PASS, report.errors


def test_happy_path_does_not_require_a_forced_trade(case_b):
    """Case B is a legitimate terminal state for the happy-path verifier."""
    report = verify_production_happy_path(
        case_b, source_matrix_path=HISTORICAL_SOURCE_MATRIX
    )
    assert report.status == VERIFIED_CASE_B, report.errors


def test_verification_is_read_only(case_c_no_trade):
    before = {
        path.name: path.read_bytes()
        for path in case_c_no_trade.iterdir()
        if path.is_file()
    }
    _verify(case_c_no_trade)
    after = {
        path.name: path.read_bytes()
        for path in case_c_no_trade.iterdir()
        if path.is_file()
    }
    assert before == after, "verification must never write into the run"
    for payload in after.values():
        assert b"VERIFIED_CASE_" not in payload
        assert b"INVALID_RUN" not in payload


# ------------------------------------------------------------- negatives ---


def test_missing_run_dir_is_invalid(tmp_path):
    assert _verify(tmp_path / "nope").status == INVALID_RUN


def test_minimal_sources_plus_recommendation_is_invalid_run(tmp_path, case_b):
    """A "run" of two parseable files is not a run.

    This is the exact defect the previous verifier had: it reported VERIFIED
    for a directory with no ranking, no event gate and no risk result.
    """
    minimal = tmp_path / "minimal"
    minimal.mkdir()
    shutil.copy(case_b / "sources.json", minimal / "sources.json")
    shutil.copy(case_b / "recommendation.json", minimal / "recommendation.json")

    report = _verify(minimal)
    assert report.status == INVALID_RUN
    assert any("required_artifacts_present" in error for error in report.errors)


@pytest.mark.parametrize("artifact", REQUIRED_ARTIFACTS)
def test_every_required_artifact_is_required(artifact, case_c_no_trade):
    (case_c_no_trade / artifact).unlink()
    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any(artifact in error for error in report.errors)


def test_missing_ranking_is_invalid(case_c_no_trade):
    (case_c_no_trade / "ranking.json").unlink()
    assert _verify(case_c_no_trade).status == INVALID_RUN


def test_missing_risk_result_is_invalid(case_c_no_trade):
    (case_c_no_trade / "risk_result.json").unlink()
    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any("risk_result.json" in error for error in report.errors)


def test_missing_selection_in_case_c_is_invalid(case_c_no_trade):
    (case_c_no_trade / "selection.json").unlink()
    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any("case_c_selection" in error for error in report.errors)


def test_forged_ranking_is_invalid(case_c_no_trade):
    """A hand-edited ranking breaks its own input-hash chain."""
    ranking = _read(case_c_no_trade, "ranking.json")
    ranking["summary"]["rank1_ticker"] = "9999"
    _write(case_c_no_trade, "ranking.json", ranking)

    assert _verify(case_c_no_trade).status == INVALID_RUN


def test_ranking_upstream_tamper_is_invalid(case_c_no_trade):
    """Editing an upstream artifact after ranking ran breaks the hash chain."""
    candidates = _read(case_c_no_trade, "candidates.json")
    candidates["candidates"][0]["reasons"] = ["tampered after ranking"]
    _write(case_c_no_trade, "candidates.json", candidates)

    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    # Detected by whichever hash-chained check sees it first -- the point is
    # that a post-hoc upstream edit cannot survive verification.
    assert report.errors


def test_forged_selection_is_invalid(case_c_no_trade):
    """Flipping selection to SELECTED without re-running it is detectable: the
    recorded ranking hash link no longer matches, and a SELECTED selection
    demands a TRADE recommendation that does not exist."""
    selection = _read(case_c_no_trade, "selection.json")
    selection["selection_status"] = "SELECTED"
    selection["selected_ticker"] = selection.get("evaluated_ticker")
    _write(case_c_no_trade, "selection.json", selection)

    assert _verify(case_c_no_trade).status == INVALID_RUN


def test_forged_selection_ranking_link_is_invalid(case_c_no_trade):
    selection = _read(case_c_no_trade, "selection.json")
    selection["input_hashes"]["ranking_sha256"] = "0" * 64
    _write(case_c_no_trade, "selection.json", selection)

    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any("selection_ranking_link" in error for error in report.errors)


def test_forged_recommendation_is_invalid(case_c_no_trade):
    """A TRADE recommendation with a NO_TRADE selection is a contradiction."""
    recommendation = _read(case_c_no_trade, "recommendation.json")
    recommendation["decision"] = "TRADE"
    _write(case_c_no_trade, "recommendation.json", recommendation)

    assert _verify(case_c_no_trade).status == INVALID_RUN


def test_forged_risk_link_is_invalid(case_c_selected):
    """risk_result must describe the same decision/ticker as the
    recommendation it is supposed to have evaluated."""
    risk = _read(case_c_selected, "risk_result.json")
    risk["ticker"] = "9999"
    _write(case_c_selected, "risk_result.json", risk)

    report = _verify(case_c_selected)
    assert report.status == INVALID_RUN
    assert any("recommendation_risk_link" in error for error in report.errors)


def test_target_date_mismatch_is_invalid(case_c_no_trade):
    recommendation = _read(case_c_no_trade, "recommendation.json")
    recommendation["target_date"] = "2026-08-13"
    _write(case_c_no_trade, "recommendation.json", recommendation)

    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any("cross_artifact_consistency" in error for error in report.errors)


def test_config_sha_mismatch_is_invalid(case_c_no_trade):
    risk = _read(case_c_no_trade, "risk_result.json")
    risk["config_sha256"] = "a" * 64
    _write(case_c_no_trade, "risk_result.json", risk)

    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any("cross_artifact_consistency" in error for error in report.errors)


def test_raw_source_page_tamper_is_invalid(tmp_path, monkeypatch):
    """One flipped byte in a stored raw page invalidates the run.

    The run is produced by the real acquisition CLI against a fake transport,
    so the stored page and its recorded SHA256 are genuine.
    """
    from src import cli
    from src.production_verify import VerificationReport, verify_raw_evidence
    from tests import fake_transport

    fake_transport.install(monkeypatch, fake_transport.clean_fake(("7203",)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "market_research.json").write_text(
        json.dumps(
            {
                "discovery_candidates": [{"ticker": "7203"}],
                "candidate_research": [{"ticker": "7203", "stage1_status": "PASSED"}],
            }
        ),
        encoding="utf-8",
    )
    cli.main(
        [
            "acquire-actual-turnover",
            "--target-date", "2026-08-12",
            "--trading-date", "2026-08-12",
            "--research-cutoff", "2026-08-11T20:00:00+09:00",
            "--run-dir", str(run_dir),
            "--sources", str(run_dir / "sources.json"),
        ]
    )
    sources = _read(run_dir, "sources.json")
    attempt = sources["source_attempts"][0]
    assert attempt["source_page_sha256"]

    clean = VerificationReport(status="", run_dir=str(run_dir))
    verify_raw_evidence(run_dir, sources, clean)
    assert clean.errors == []

    stored = run_dir / attempt["source_page_path"]
    stored.write_bytes(stored.read_bytes() + b"<!-- tampered -->")

    tampered = VerificationReport(status="", run_dir=str(run_dir))
    verify_raw_evidence(run_dir, sources, tampered)
    assert any("SOURCE_PAGE_HASH_MISMATCH" in error for error in tampered.errors)


def test_schema_violation_makes_the_run_invalid(case_c_no_trade):
    sources = _read(case_c_no_trade, "sources.json")
    sources["schema_version"] = 99
    _write(case_c_no_trade, "sources.json", sources)
    assert _verify(case_c_no_trade).status == INVALID_RUN


def test_unexpected_run_artifact_is_invalid(case_c_no_trade):
    (case_c_no_trade / "notes_from_the_agent.json").write_text("{}", encoding="utf-8")
    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert any("run_artifact_allowlist" in error for error in report.errors)


def test_case_a_requires_a_data_unavailable_recommendation(case_b):
    """A DATA_UNAVAILABLE ranking may not terminate in anything else."""
    ranking = _read(case_b, "ranking.json")
    ranking["ranking_status"] = "DATA_UNAVAILABLE"
    _write(case_b, "ranking.json", ranking)

    assert _verify(case_b).status == INVALID_RUN


def test_case_b_requires_the_pending_calibration_reason(case_b):
    recommendation = _read(case_b, "recommendation.json")
    recommendation["selection_reasons"] = ["because I said so"]
    _write(case_b, "recommendation.json", recommendation)

    report = _verify(case_b)
    assert report.status == INVALID_RUN
    assert any("case_b_reason" in error for error in report.errors)


def test_case_b_with_a_selection_artifact_is_invalid(tmp_path):
    case_b = build_case_b_run(tmp_path / "b")
    case_c = build_case_c_run(tmp_path / "c", selection_status="NO_TRADE")
    shutil.copy(case_c / "selection.json", case_b / "selection.json")
    assert _verify(case_b).status == INVALID_RUN


# --------------------------------------------------------- network audit ---

TARGET_DATE = "2026-08-13"
RESEARCH_CUTOFF = "2026-08-12T20:00:00+09:00"


def _complete_request(tmp_path, *, url, origin_attempt_id, source_status="FOUND"):
    from src.request_budget import complete_request, reserve_request

    outcome = reserve_request(
        tmp_path,
        url=url,
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="JPX_TDNET",
        origin_candidate_code=None,
        origin_attempt_id=origin_attempt_id,
    )
    record = complete_request(
        tmp_path,
        outcome.record["request_id"],
        source_status=source_status,
        http_status=200,
        content_type="text/html",
        transport_exit_code=0,
        source_page_path=None,
        source_page_sha256=None,
        source_page_size_bytes=None,
    )
    return record


def test_network_audit_counts_requests_not_attempts(tmp_path):
    """FIX-R2-002: N candidate attempts off one shared page is ONE request,
    counted from network_requests/*.json, not from cache_status."""
    tdnet_record = _complete_request(
        tmp_path,
        url="https://www.release.tdnet.info/inbs/I_main_00.html",
        origin_attempt_id="att-1",
    )
    yahoo_record = _complete_request(
        tmp_path, url="https://finance.yahoo.co.jp/quote/7203.T", origin_attempt_id="att-3"
    )
    payload = {
        "source_attempts": [
            {
                "attempt_id": "att-1",
                "url": tdnet_record["url"],
                "status": "FOUND",
                "cache_status": "MISS",
                "request_id": tdnet_record["request_id"],
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            },
            {
                "attempt_id": "att-2",
                "url": tdnet_record["url"],
                "status": "FOUND",
                "cache_status": "HIT",
                "request_id": tdnet_record["request_id"],
                "network_request_performed": False,
                "reused_from_attempt_id": "att-1",
            },
            {
                "attempt_id": "att-3",
                "url": yahoo_record["url"],
                "status": "FOUND",
                "cache_status": "MISS",
                "request_id": yahoo_record["request_id"],
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            },
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert audit["attempt_count"] == 3
    assert audit["request_count"] == 2
    assert audit["cache_hit_count"] == 1
    assert audit["hosts"] == {
        "www.release.tdnet.info": 1,
        "finance.yahoo.co.jp": 1,
    }
    assert audit["request_budget_respected"] is True
    assert audit["network_audit_complete"] is True


def test_network_audit_flags_a_duplicate_attempt_id(tmp_path):
    record = _complete_request(
        tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1"
    )
    payload = {
        "source_attempts": [
            {
                "attempt_id": "att-1",
                "url": record["url"],
                "status": "FOUND",
                "cache_status": "MISS",
                "request_id": record["request_id"],
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            },
            {
                "attempt_id": "att-1",
                "url": record["url"],
                "status": "FOUND",
                "cache_status": "MISS",
                "request_id": record["request_id"],
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            },
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert audit["duplicate_attempt_ids"] == ["att-1"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_second_attempt_claiming_the_same_get(tmp_path):
    """Two different attempt_ids both claim to be the real GET
    (network_request_performed=True) for the same Physical Request -- only
    the request's own recorded origin_attempt_id may claim that."""
    record = _complete_request(
        tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1"
    )
    payload = {
        "source_attempts": [
            {
                "attempt_id": "att-1",
                "url": record["url"],
                "status": "FOUND",
                "cache_status": "MISS",
                "request_id": record["request_id"],
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            },
            {
                "attempt_id": "att-2",
                "url": record["url"],
                "status": "FOUND",
                "cache_status": "MISS",
                "request_id": record["request_id"],
                "network_request_performed": True,
                "reused_from_attempt_id": None,
            },
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert audit["invalid_reuse_links"] == ["att-2"]
    assert audit["request_budget_respected"] is False


def _base_attempt(record, attempt_id, **overrides):
    attempt = {
        "attempt_id": attempt_id,
        "url": record["url"],
        "status": "FOUND",
        "cache_status": "MISS",
        "request_id": record["request_id"],
        "network_request_performed": True,
        "reused_from_attempt_id": None,
    }
    attempt.update(overrides)
    return attempt


def test_network_audit_flags_a_miss_without_a_request_record(tmp_path):
    payload = {
        "source_attempts": [
            _base_attempt(
                {"url": "https://www.jpx.co.jp/a", "request_id": "req-" + "0" * 32},
                "att-1",
            )
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert audit["orphan_attempt_request_ids"] == ["att-1"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_hit_without_a_request_record(tmp_path):
    payload = {
        "source_attempts": [
            _base_attempt(
                {"url": "https://www.jpx.co.jp/a", "request_id": "req-" + "0" * 32},
                "att-2",
                cache_status="HIT",
                network_request_performed=False,
                reused_from_attempt_id="att-1",
            )
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert audit["orphan_attempt_request_ids"] == ["att-2"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_hit_without_reused_from_attempt_id(tmp_path):
    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    payload = {
        "source_attempts": [
            _base_attempt(record, "att-1"),
            _base_attempt(
                record,
                "att-2",
                cache_status="HIT",
                network_request_performed=False,
                reused_from_attempt_id=None,
            ),
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert audit["invalid_reuse_links"] == ["att-2"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_hit_referencing_a_hit(tmp_path):
    """HIT -> HIT is forbidden: reused_from_attempt_id must chain to the
    genuine MISS origin, never to another reuse."""
    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    payload = {
        "source_attempts": [
            _base_attempt(record, "att-1"),
            _base_attempt(
                record,
                "att-2",
                cache_status="HIT",
                network_request_performed=False,
                reused_from_attempt_id="att-1",
            ),
            _base_attempt(
                record,
                "att-3",
                cache_status="HIT",
                network_request_performed=False,
                reused_from_attempt_id="att-2",
            ),
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert "att-3" in audit["invalid_reuse_links"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_hit_referencing_another_request_id(tmp_path):
    record_a = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    record_b = _complete_request(tmp_path, url="https://www.jpx.co.jp/b", origin_attempt_id="att-2")
    payload = {
        "source_attempts": [
            _base_attempt(record_a, "att-1"),
            _base_attempt(record_b, "att-2"),
            # att-3 claims request_id from record_b but reuses att-1 (whose
            # actual request_id is record_a's) -- a forged cross-request link.
            _base_attempt(
                record_b,
                "att-3",
                cache_status="HIT",
                network_request_performed=False,
                reused_from_attempt_id="att-1",
            ),
        ]
    }
    audit = network_audit(tmp_path, payload)
    assert "att-3" in audit["invalid_reuse_links"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_an_orphan_request_record(tmp_path):
    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-missing")
    payload = {"source_attempts": []}
    audit = network_audit(tmp_path, payload)
    assert audit["orphan_request_records"] == [record["request_id"]]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_reserved_request(tmp_path):
    from src.request_budget import reserve_request

    outcome = reserve_request(
        tmp_path,
        url="https://www.jpx.co.jp/a",
        target_date=TARGET_DATE,
        research_cutoff=RESEARCH_CUTOFF,
        origin_source_id="JPX_TDNET",
        origin_candidate_code=None,
        origin_attempt_id="att-1",
    )
    payload = {"source_attempts": []}
    audit = network_audit(tmp_path, payload)
    assert audit["reserved_request_count"] == 1
    assert audit["network_audit_complete"] is False
    assert outcome.record["state"] == "RESERVED"


def test_network_audit_flags_a_duplicate_physical_request(tmp_path):
    """Two request record files claiming the same (url, target_date,
    research_cutoff) physical identity under different request_ids."""
    from src.request_budget import network_request_path
    import json as _json

    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    duplicate = dict(record)
    duplicate["request_id"] = "req-" + "1" * 32
    duplicate["origin_attempt_id"] = "att-2"
    network_request_path(tmp_path, duplicate["request_id"]).write_text(
        _json.dumps(duplicate), encoding="utf-8"
    )

    audit = network_audit(tmp_path, {"source_attempts": []})
    assert audit["duplicate_physical_keys"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_request_id_tamper(tmp_path):
    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    from src.request_budget import network_request_path
    import json as _json

    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["request_id"] = "req-" + "9" * 32
    path.unlink()
    path.write_text(_json.dumps(tampered), encoding="utf-8")

    audit = network_audit(tmp_path, {"source_attempts": []})
    assert tampered["request_id"] in audit["invalid_request_ids"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_request_url_tamper(tmp_path):
    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    from src.request_budget import network_request_path
    import json as _json

    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["url"] = "https://www.jpx.co.jp/tampered"
    path.write_text(_json.dumps(tampered), encoding="utf-8")

    audit = network_audit(tmp_path, {"source_attempts": []})
    assert record["request_id"] in audit["invalid_request_ids"]
    assert audit["request_budget_respected"] is False


def test_network_audit_flags_a_request_cutoff_tamper(tmp_path):
    record = _complete_request(tmp_path, url="https://www.jpx.co.jp/a", origin_attempt_id="att-1")
    from src.request_budget import network_request_path
    import json as _json

    path = network_request_path(tmp_path, record["request_id"])
    tampered = dict(record)
    tampered["research_cutoff"] = "2099-01-01T00:00:00+09:00"
    path.write_text(_json.dumps(tampered), encoding="utf-8")

    audit = network_audit(tmp_path, {"source_attempts": []})
    assert record["request_id"] in audit["invalid_request_ids"]
    assert audit["request_budget_respected"] is False


def test_real_run_network_audit_matches_the_ledger(case_c_no_trade):
    report = _verify(case_c_no_trade)
    audit = report.network_audit
    sources = _read(case_c_no_trade, "sources.json")
    assert audit["attempt_count"] == len(sources["source_attempts"])
    assert audit["request_count"] <= audit["attempt_count"]


def test_a_reserved_request_invalidates_an_otherwise_complete_run(case_c_no_trade):
    """FIX-R2-002: a RESERVED (never COMPLETED) Physical Request Record --
    e.g. left behind by a crashed acquisition process -- must fail the whole
    verification, not just be a quiet audit-dict flag nobody looks at."""
    from src.request_budget import reserve_request

    reserve_request(
        case_c_no_trade,
        url="https://www.jpx.co.jp/never-completed",
        target_date="2026-08-12",
        research_cutoff="2026-08-11T20:00:00+09:00",
        origin_source_id="JPX_TDNET",
        origin_candidate_code=None,
        origin_attempt_id="att-crashed",
    )
    report = _verify(case_c_no_trade)
    assert report.status == INVALID_RUN
    assert report.network_audit["reserved_request_count"] == 1
    assert report.network_audit["network_audit_complete"] is False


# ---------------------------------------------------------------- reuse ---


def test_verification_reuses_the_production_validators():
    """Guard against a second, drifting copy of the business rules."""
    import inspect

    from src import production_verify

    source = inspect.getsource(production_verify)
    for reused in (
        "validate_source_ledger",
        "validate_market_data",
        "validate_event_gate_integrity",
        "load_and_verify_ranking_trust_chain",
        "validate_recommendation_risk_link",
        "validate_run_artifact_allowlist",
    ):
        assert reused in source, reused
    # no reimplementation of ranking / selection / risk logic here
    assert "def build_ranking" not in source
    assert "def build_selection" not in source
    assert "def evaluate_order" not in source


def test_the_five_diagnostic_statuses_are_distinct():
    assert VERIFIED_CASE_A in VERIFIED_STATUSES
    assert len(VERIFIED_STATUSES) == 5
    assert INVALID_RUN not in VERIFIED_STATUSES
    assert sha256_file(HISTORICAL_SOURCE_MATRIX)
