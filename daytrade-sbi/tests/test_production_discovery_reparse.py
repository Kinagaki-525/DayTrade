"""Contracts for the HUMAN-ONLY offline Discovery reparse recovery.

No test here touches the network. The run fixture is built by the *real*
acquisition CLI against :mod:`tests.fake_transport`, and the legacy (pre-fix)
parser is then simulated by rewriting only the parser-derived fields of the
two Discovery Attempts -- exactly the state the 2026-08-27 Production Nightly
left behind: good Raw Evidence, a stale Logical Parse Result.

The recovery itself is then run with the transport removed entirely
(``subprocess.run`` monkeypatched to explode), so "zero network requests" is
asserted structurally, not by counting.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from src import cli
from src import production_discovery_reparse as recovery
from src.claude_runtime_security import RUNTIME_SECURITY_CHECKS
from tests import fake_transport
from tests import source_page_fixtures as pages


TRADING_DATE = pages.TRADING_DATE
TARGET_DATE = "2026-08-13"
CUTOFF = f"{TRADING_DATE}T20:00:00+09:00"

HEAD_SHA = "bad369246083505295e7ac0eec552eb52064b903"

VOLUME_URL = "https://finance.yahoo.co.jp/stocks/ranking/volume?market=all"
GAIN_URL = "https://finance.yahoo.co.jp/stocks/ranking/up?market=all"

VOLUME_SOURCE_ID = "YAHOO_JP_VOLUME_RANKING"
GAIN_SOURCE_ID = "YAHOO_JP_GAIN_RANKING"


# --------------------------------------------------------------- fixtures ---


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_research_window(run_dir: Path) -> None:
    window_start = f"{date.fromisoformat(TRADING_DATE) - timedelta(days=1)}T20:00:00+09:00"
    (run_dir / "research_window.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_date": TARGET_DATE,
                "previous_trading_day": TRADING_DATE,
                "research_cutoff": CUTOFF,
                "research_window": {
                    "run_type": "FIRST_RUN",
                    "window_start": window_start,
                    "window_end": CUTOFF,
                    "previous_research_cutoff": None,
                    "previous_run_date": None,
                    "bootstrap_lookback_days": 1,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_runtime_security(run_dir: Path, *, git_head_sha: str = HEAD_SHA) -> None:
    (run_dir / "working").mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 1,
        "runtime": "claude-code",
        "runtime_profile": "production",
        "target_date": TARGET_DATE,
        "platform": "Linux-test",
        "claude_code_version": "2.1.219",
        "git_head_sha": git_head_sha,
        "production_python": "/opt/daytrade/bin/python3",
        "managed_settings_sha256": "0" * 64,
        "managed_runtime_guard_sha256": "1" * 64,
        "source_matrix_sha256": "2" * 64,
        "issuer_domain_registry_sha256": "3" * 64,
        "allowed_domains": ["finance.yahoo.co.jp"],
        "http_user_agent_present": True,
        "checks": {name: "PASS" for name in RUNTIME_SECURITY_CHECKS},
    }
    (run_dir / "working" / "runtime_security.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _acquire_discovery(monkeypatch, run_dir: Path, routes: dict[str, bytes]):
    """Build a real run directory through the real acquisition CLI."""
    fake = fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )
    cli.main(
        [
            "acquire-discovery",
            "--target-date", TARGET_DATE,
            "--trading-date", TRADING_DATE,
            "--research-cutoff", CUTOFF,
            "--run-dir", str(run_dir),
            "--sources", str(run_dir / "sources.json"),
            "--research-window", str(run_dir / "research_window.json"),
        ]
    )
    return fake


def _legacy_row(row: dict) -> dict:
    """One ranking row exactly as the pre-fix parser stored it.

    The defect: the company cell's *rank number* was kept as the company
    name.
    """
    legacy = dict(row)
    legacy["company_name"] = str(row["rank"])
    return legacy


def _downgrade_attempt(ledger: dict, source_id: str, keep: int) -> None:
    """Simulate the legacy parser's Logical Parse Result for one source."""
    for attempt in ledger["source_attempts"]:
        if attempt["source_id"] != source_id:
            continue
        for value in attempt["values"]:
            if value["field_name"] == "ranking_tickers":
                value["value"] = value["value"][:keep]
            elif value["field_name"] == "ranking_rows":
                value["value"] = [
                    _legacy_row(row) for row in value["value"][:keep]
                ]
            for entry in ledger["sources"]:
                if entry["source_ref"] == value["source_ref"]:
                    entry["value"] = value["value"]


def build_run(
    tmp_path: Path,
    monkeypatch,
    *,
    volume_rows: int = 50,
    gain_rows: int = 50,
    legacy_volume: int = 47,
    legacy_gain: int = 45,
    keep_market_research: bool = False,
    git_head_sha: str = HEAD_SHA,
) -> Path:
    """A stopped Production run: good Raw Evidence, stale Logical Parse Result."""
    run_dir = tmp_path / "runs" / TARGET_DATE
    run_dir.mkdir(parents=True)
    _write_research_window(run_dir)
    _acquire_discovery(
        monkeypatch,
        run_dir,
        {
            VOLUME_URL: pages.yahoo_ranking_page(pages.top50_tickers(1000)[:volume_rows]),
            GAIN_URL: pages.yahoo_ranking_page(pages.top50_tickers(2000)[:gain_rows]),
        },
    )
    _write_runtime_security(run_dir, git_head_sha=git_head_sha)

    if not keep_market_research:
        (run_dir / "market_research.json").unlink()

    sources_path = run_dir / "sources.json"
    ledger = json.loads(sources_path.read_text(encoding="utf-8"))
    _downgrade_attempt(ledger, VOLUME_SOURCE_ID, legacy_volume)
    _downgrade_attempt(ledger, GAIN_SOURCE_ID, legacy_gain)
    sources_path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return run_dir


def fake_git(head: str = HEAD_SHA, porcelain: str = ""):
    def run_command(argv, cwd):
        if argv == ["git", "rev-parse", "HEAD"]:
            return head + "\n"
        if argv == ["git", "status", "--porcelain"]:
            return porcelain
        raise AssertionError(f"unexpected command: {argv}")

    return run_command


@pytest.fixture()
def no_subprocess(monkeypatch):
    """Any subprocess at all during the recovery is a contract violation."""

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the recovery must never spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", explode)


def run_recovery(tmp_path: Path, **kwargs):
    kwargs.setdefault("run_command", fake_git())
    return recovery.reparse_production_discovery(
        TARGET_DATE, daytrade_root=tmp_path, **kwargs
    )


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _attempt(ledger: dict, source_id: str) -> dict:
    matches = [a for a in ledger["source_attempts"] if a["source_id"] == source_id]
    assert len(matches) == 1
    return matches[0]


def _rows(attempt: dict) -> list[dict]:
    for value in attempt["values"]:
        if value["field_name"] == "ranking_rows":
            return value["value"]
    raise AssertionError("attempt carries no ranking_rows")


def _tickers(attempt: dict) -> list[str]:
    for value in attempt["values"]:
        if value["field_name"] == "ranking_tickers":
            return value["value"]
    raise AssertionError("attempt carries no ranking_tickers")


def _load_sources(run_dir: Path) -> dict:
    return json.loads((run_dir / "sources.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------- T01 ---


def test_recovery_reparses_the_stored_evidence_with_the_current_parser(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)

    before = _load_sources(run_dir)
    assert len(_tickers(_attempt(before, VOLUME_SOURCE_ID))) == 47
    assert len(_tickers(_attempt(before, GAIN_SOURCE_ID))) == 45

    result = run_recovery(tmp_path)

    assert result["result"] == recovery.RESULT_REPARSED
    assert result["network_request_count"] == 0
    assert result["source_count"] == 2
    assert result["stage"] == "DISCOVERY"

    after = _load_sources(run_dir)
    for source_id in (VOLUME_SOURCE_ID, GAIN_SOURCE_ID):
        attempt = _attempt(after, source_id)
        assert attempt["status"] == "FOUND"
        assert len(_tickers(attempt)) == 50
        rows = _rows(attempt)
        assert len(rows) == 50
        assert all(row["company_name"] != str(row["rank"]) for row in rows)
        assert all(row["company_name"].startswith("Example") for row in rows)


def test_recovery_updates_the_source_ledger_values_in_place(
    tmp_path, monkeypatch, no_subprocess
):
    """The ``<attempt_id>#<field_name>`` scheme is reused, not replaced."""
    run_dir = build_run(tmp_path, monkeypatch)
    before = _load_sources(run_dir)
    before_refs = [value["source_ref"] for value in before["sources"]]

    run_recovery(tmp_path)

    after = _load_sources(run_dir)
    assert [value["source_ref"] for value in after["sources"]] == before_refs
    ledger_values = {value["source_ref"]: value for value in after["sources"]}
    for source_id in (VOLUME_SOURCE_ID, GAIN_SOURCE_ID):
        attempt = _attempt(after, source_id)
        ref = f"{attempt['attempt_id']}#ranking_tickers"
        assert len(ledger_values[ref]["value"]) == 50
        assert ledger_values[ref]["source_id"] == source_id


# ------------------------------------------------------------- T02 / T03 ---


def test_physical_request_records_are_byte_identical(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = _snapshot(run_dir / "network_requests")
    assert before

    run_recovery(tmp_path)

    assert _snapshot(run_dir / "network_requests") == before


def test_raw_source_pages_are_byte_identical(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = _snapshot(run_dir / "source_pages")
    assert before

    run_recovery(tmp_path)

    after = _snapshot(run_dir / "source_pages")
    assert after == before
    assert {name: _sha256(data) for name, data in after.items()} == {
        name: _sha256(data) for name, data in before.items()
    }


def test_market_research_is_never_written_by_the_recovery(
    tmp_path, monkeypatch, no_subprocess
):
    """A schema-invalid remnant is evidence: hashed, reported, never repaired."""
    run_dir = build_run(tmp_path, monkeypatch, keep_market_research=True)
    remnant = json.dumps({"status": "CLOSED", "stage": "DISCOVERY"}) + "\n"
    (run_dir / "market_research.json").write_text(remnant, encoding="utf-8")

    result = run_recovery(tmp_path)

    assert result["result"] == recovery.RESULT_REPARSED
    assert (run_dir / "market_research.json").read_text(encoding="utf-8") == remnant
    audit = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))
    assert audit["market_research_before_sha256"] == _sha256(remnant.encode("utf-8"))


# ------------------------------------------------------------------- T04 ---


@pytest.mark.parametrize(
    "forbidden",
    ["reserve_request", "complete_request", "curl_transport", "_fetch_source"],
)
def test_the_recovery_module_cannot_reach_the_network_layer(forbidden):
    assert not hasattr(recovery, forbidden), (
        f"src/production_discovery_reparse.py imported {forbidden}"
    )
    source = (
        Path(recovery.__file__).with_name("production_discovery_reparse.py")
    ).read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.lstrip().startswith(("#", "*")):
            continue
        assert f"{forbidden}(" not in line, line


def test_the_recovery_never_spawns_a_subprocess_of_its_own(
    tmp_path, monkeypatch, no_subprocess
):
    """With ``subprocess.run`` removed, the recovery still completes: the only
    subprocess it would ever use is the injectable local read-only git."""
    build_run(tmp_path, monkeypatch)
    assert run_recovery(tmp_path)["result"] == recovery.RESULT_REPARSED


# ------------------------------------------------------------------- T05 ---


def test_normal_acquire_discovery_after_recovery_spends_zero_network_requests(
    tmp_path, monkeypatch
):
    """The most important integration contract of this recovery.

    After the human-only recovery, the canonical pipeline resumes: the same
    ``acquire-discovery`` reuses the corrected Logical Attempts, performs no
    GET at all, and regenerates ``market_research.json`` with a full TOP50 on
    both routes.
    """
    run_dir = build_run(tmp_path, monkeypatch)
    run_recovery(tmp_path)
    requests_before = _snapshot(run_dir / "network_requests")

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("acquire-discovery must not re-fetch after a recovery")

    monkeypatch.setattr(subprocess, "run", explode)

    exit_code = cli.main(
        [
            "acquire-discovery",
            "--target-date", TARGET_DATE,
            "--trading-date", TRADING_DATE,
            "--research-cutoff", CUTOFF,
            "--run-dir", str(run_dir),
            "--sources", str(run_dir / "sources.json"),
            "--research-window", str(run_dir / "research_window.json"),
            "--output", str(run_dir / "working" / "discovery.json"),
        ]
    )
    assert exit_code == 0

    summary = json.loads(
        (run_dir / "working" / "discovery.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "OPEN"

    # The Network Audit SSOT is the Physical Request Record set, not the
    # summary's Logical cache_status tally (an immutable reused MISS attempt
    # keeps reporting itself as the MISS that really made the GET). No record
    # was added, removed or rewritten, and the transport was never reachable.
    assert _snapshot(run_dir / "network_requests") == requests_before

    research = json.loads(
        (run_dir / "market_research.json").read_text(encoding="utf-8")
    )
    assert research["overall_status"] == "PIPELINE_INCOMPLETE"
    assert [route["result_count"] for route in research["discovery"]] == [50, 50]
    assert research["discovery_candidates"]
    for route in research["discovery"]:
        assert route["status"] == "FOUND"
        for item in route["items"]:
            assert item["company_name"] != str(item["rank"])
            assert item["company_name"] != item["ticker"]


# ------------------------------------------------------------- T06 / T07 ---


IDENTITY_FIELDS = recovery.IMMUTABLE_ATTEMPT_FIELDS


def test_recovery_immutable_fields_cover_the_acquisition_contract():
    """Every field normal ``merge_ledger`` treats as immutable is preserved
    here too -- except ``status``, which is the parser-derived field a Parser
    fix legitimately corrects."""
    from src.source_acquisition import _IMMUTABLE_ATTEMPT_FIELDS

    assert set(_IMMUTABLE_ATTEMPT_FIELDS) - {"status"} == set(IDENTITY_FIELDS)


@pytest.mark.parametrize("field_name", IDENTITY_FIELDS)
def test_identity_and_physical_fields_are_unchanged(
    field_name, tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = _load_sources(run_dir)

    run_recovery(tmp_path)

    after = _load_sources(run_dir)
    for source_id in (VOLUME_SOURCE_ID, GAIN_SOURCE_ID):
        assert _attempt(after, source_id).get(field_name) == _attempt(
            before, source_id
        ).get(field_name)


def test_the_normal_merge_ledger_immutability_contract_is_not_relaxed():
    """Recovery bypasses ``merge_ledger``; it does not weaken it."""
    from src.source_acquisition import AcquisitionError, merge_ledger

    existing = {
        "schema_version": 3,
        "target_date": TARGET_DATE,
        "sources": [],
        "source_attempts": [{"attempt_id": "att-1", "status": "PARSE_FAILED"}],
    }
    addition = {
        "schema_version": 3,
        "target_date": TARGET_DATE,
        "sources": [],
        "source_attempts": [{"attempt_id": "att-1", "status": "FOUND"}],
    }
    with pytest.raises(AcquisitionError) as excinfo:
        merge_ledger(existing, addition)
    assert excinfo.value.code == "SOURCE_ATTEMPT_IMMUTABILITY_VIOLATION"


# ------------------------------------------------------------------- T08 ---


def test_a_page_that_still_cannot_yield_top50_writes_nothing(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch, volume_rows=30, legacy_volume=30)
    before = (run_dir / "sources.json").read_bytes()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_STILL_INCOMPLETE"
    assert (run_dir / "sources.json").read_bytes() == before
    assert not (run_dir / "working" / recovery.AUDIT_DIRNAME).exists()


# ------------------------------------------------------- T09 / T10 ... T13 ---


def _page_path(run_dir: Path, source_id: str) -> Path:
    attempt = _attempt(_load_sources(run_dir), source_id)
    return run_dir / attempt["source_page_path"]


def test_a_tampered_raw_page_is_a_hard_error(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    page = _page_path(run_dir, VOLUME_SOURCE_ID)
    page.write_bytes(page.read_bytes() + b" ")

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_SOURCE_PAGE_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_missing_request_record_is_a_hard_error(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    attempt = _attempt(_load_sources(run_dir), VOLUME_SOURCE_ID)
    (run_dir / "network_requests" / f"{attempt['request_id']}.json").unlink()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def _mutate_request_record(run_dir: Path, source_id: str, **changes) -> None:
    attempt = _attempt(_load_sources(run_dir), source_id)
    path = run_dir / "network_requests" / f"{attempt['request_id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(changes)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def test_a_reserved_request_is_never_retried(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    _mutate_request_record(
        run_dir,
        VOLUME_SOURCE_ID,
        state="RESERVED",
        completed_at=None,
        source_status=None,
        http_status=None,
        content_type=None,
        transport_exit_code=None,
        source_page_path=None,
        source_page_sha256=None,
        source_page_size_bytes=None,
    )

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_REQUEST_NOT_COMPLETED"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_request_that_did_not_find_the_page_is_never_upgraded(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    _mutate_request_record(run_dir, VOLUME_SOURCE_ID, source_status="ACCESS_FAILED")

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_REQUEST_NOT_FOUND"
    assert (run_dir / "sources.json").read_bytes() == before


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("source_page_sha256", "f" * 64),
        ("source_page_path", "source_pages/somewhere-else.raw"),
        ("source_page_size_bytes", 12),
        ("http_status", 418),
        ("content_type", "application/json"),
        ("transport_exit_code", 7),
    ],
)
def test_attempt_and_request_record_must_agree_on_the_evidence(
    field_name, value, tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    _mutate_request_record(run_dir, VOLUME_SOURCE_ID, **{field_name: value})

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_MISMATCH"
    assert (run_dir / "sources.json").read_bytes() == before


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("url", "https://finance.yahoo.co.jp/stocks/ranking/volume?market=other"),
        ("target_date", "2026-08-14"),
        ("research_cutoff", f"{TRADING_DATE}T21:00:00+09:00"),
    ],
)
def test_a_request_record_that_describes_another_tuple_is_rejected(
    field_name, value, tmp_path, monkeypatch, no_subprocess
):
    """``load_request_record`` recomputes the id from the tuple, so a changed
    tuple is caught as a Request Record integrity violation."""
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    _mutate_request_record(run_dir, VOLUME_SOURCE_ID, **{field_name: value})

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def test_an_attempt_without_a_request_id_never_reaches_the_parser(
    tmp_path, monkeypatch, no_subprocess
):
    """The Source Ledger schema already forbids a MISS attempt with no
    request_id, so the recovery stops on the ledger itself."""
    run_dir = build_run(tmp_path, monkeypatch)
    ledger = _load_sources(run_dir)
    _attempt(ledger, VOLUME_SOURCE_ID)["request_id"] = None
    before = _rewrite_sources(run_dir, ledger)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def test_an_attempt_carrying_no_request_id_has_no_evidence_to_reparse(tmp_path):
    """The guard itself, exercised directly: no request_id means no Physical
    Request evidence, so there is nothing a Parser fix could re-evaluate."""
    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        recovery._verified_request_record(
            tmp_path, {"source_id": VOLUME_SOURCE_ID, "request_id": None}
        )
    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_REQUEST_RECORD_INVALID"


# ------------------------------------------------------- T14 / T15 / T16 ---


def _rewrite_sources(run_dir: Path, ledger: dict) -> bytes:
    (run_dir / "sources.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return (run_dir / "sources.json").read_bytes()


def test_a_missing_discovery_attempt_is_a_hard_error(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    ledger = _load_sources(run_dir)
    ledger["source_attempts"] = [
        attempt
        for attempt in ledger["source_attempts"]
        if attempt["source_id"] != GAIN_SOURCE_ID
    ]
    before = _rewrite_sources(run_dir, ledger)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_ATTEMPT_SET_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_duplicate_discovery_attempt_is_a_hard_error(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    ledger = _load_sources(run_dir)
    duplicate = copy.deepcopy(_attempt(ledger, VOLUME_SOURCE_ID))
    duplicate["attempt_id"] = duplicate["attempt_id"] + "x"
    ledger["source_attempts"].append(duplicate)
    before = _rewrite_sources(run_dir, ledger)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_ATTEMPT_SET_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_candidate_scoped_discovery_attempt_is_a_hard_error(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    ledger = _load_sources(run_dir)
    _attempt(ledger, VOLUME_SOURCE_ID)["candidate_code"] = "7203"
    before = _rewrite_sources(run_dir, ledger)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_ATTEMPT_SET_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


# ------------------------------------------------------------------- T17 ---


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("target_date", "2026-08-14"),
        ("research_cutoff", f"{TRADING_DATE}T21:00:00+09:00"),
    ],
)
def test_an_attempt_outside_the_canonical_research_window_is_rejected(
    field_name, value, tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    ledger = _load_sources(run_dir)
    _attempt(ledger, VOLUME_SOURCE_ID)[field_name] = value
    before = _rewrite_sources(run_dir, ledger)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_ledger_for_another_target_date_is_rejected(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    ledger = _load_sources(run_dir)
    ledger["target_date"] = "2026-08-14"
    before = _rewrite_sources(run_dir, ledger)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_SOURCES_INVALID"
    assert (run_dir / "sources.json").read_bytes() == before


# ------------------------------------------------------------------- T18 ---


@pytest.mark.parametrize(
    "artifact",
    [
        "market_data.json",
        "market_research_validation.json",
        "market_validation.json",
        "candidates.json",
        "candidate_pipeline.json",
        "performance.json",
        "research.md",
        "event_research.json",
        "event_gate.json",
        "ranking.json",
        "selection.json",
        "recommendation.json",
        "recommendation.md",
        "risk_result.json",
        "report.md",
        "official_ohlcv_audit.json",
        "execution_result.json",
    ],
)
def test_any_downstream_artifact_blocks_the_recovery(
    artifact, tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    (run_dir / artifact).write_text("{}", encoding="utf-8")

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT"
    )
    assert (run_dir / "sources.json").read_bytes() == before
    assert (run_dir / artifact).is_file(), "the recovery must never delete anything"


def test_a_run_whose_discovery_already_passed_is_refused(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch, keep_market_research=True)
    before = (run_dir / "sources.json").read_bytes()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_DISCOVERY_ALREADY_COMPLETE"
    )
    assert (run_dir / "sources.json").read_bytes() == before


# ------------------------------------------------- runtime security gates ---


def test_a_missing_runtime_security_attestation_stops_the_recovery(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    (run_dir / "working" / "runtime_security.json").unlink()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_RUNTIME_SECURITY_INVALID"
    )
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_different_local_head_stops_the_recovery(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path, run_command=fake_git(head="0" * 40))

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_GIT_HEAD_MISMATCH"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_dirty_protected_tree_stops_the_recovery(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(
            tmp_path,
            run_command=fake_git(porcelain=" M daytrade-sbi/src/source_parsers/yahoo_jp.py\n"),
        )

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_SOURCE_TREE_DIRTY"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_missing_run_directory_is_a_hard_error(tmp_path, no_subprocess):
    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)
    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_RUN_MISSING"


@pytest.mark.parametrize("bad", ["2026-8-13", "../../etc", "2026-02-30", ""])
def test_only_a_real_iso_target_date_is_accepted(bad, tmp_path, no_subprocess):
    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        recovery.reparse_production_discovery(
            bad, daytrade_root=tmp_path, run_command=fake_git()
        )
    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_TARGET_DATE_INVALID"


# ------------------------------------------------------- T19 / T20 / T21 ---


def test_the_audit_sidecar_is_schema_valid_and_records_the_before_after_state(
    tmp_path, monkeypatch, no_subprocess
):
    from src.contracts import validate_json_document

    run_dir = build_run(tmp_path, monkeypatch)
    before_sha = _sha256((run_dir / "sources.json").read_bytes())

    result = run_recovery(tmp_path)

    audit_path = Path(result["audit_path"])
    assert audit_path == run_dir / "working" / recovery.AUDIT_DIRNAME / f"{HEAD_SHA}.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    validate_json_document(audit, recovery.AUDIT_SCHEMA_NAME)

    assert audit["stage"] == "DISCOVERY"
    assert audit["result"] == recovery.RESULT_REPARSED
    assert audit["git_head_sha"] == HEAD_SHA
    assert audit["network_request_count"] == 0
    assert audit["sources_before_sha256"] == before_sha
    assert audit["sources_after_sha256"] == _sha256(
        (run_dir / "sources.json").read_bytes()
    )
    assert audit["market_research_before_sha256"] is None
    assert audit["research_cutoff"] == CUTOFF
    assert audit["previous_trading_day"] == TRADING_DATE

    by_source = {entry["source_id"]: entry for entry in audit["attempts"]}
    assert set(by_source) == {VOLUME_SOURCE_ID, GAIN_SOURCE_ID}
    assert by_source[VOLUME_SOURCE_ID]["before_ranking_ticker_count"] == 47
    assert by_source[VOLUME_SOURCE_ID]["after_ranking_ticker_count"] == 50
    assert by_source[GAIN_SOURCE_ID]["before_ranking_row_count"] == 45
    assert by_source[GAIN_SOURCE_ID]["after_ranking_row_count"] == 50
    for entry in audit["attempts"]:
        assert entry["parser_id"] == "yahoo_jp.ranking"
        assert entry["after_status"] == "FOUND"
        assert entry["request_id"].startswith("req-")


def test_running_the_recovery_twice_is_idempotent(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    first = run_recovery(tmp_path)
    assert first["result"] == recovery.RESULT_REPARSED

    after_first = (run_dir / "sources.json").read_bytes()
    audit_bytes = Path(first["audit_path"]).read_bytes()

    second = run_recovery(tmp_path)

    assert second["result"] == recovery.RESULT_ALREADY_REPARSED
    assert (run_dir / "sources.json").read_bytes() == after_first
    assert Path(first["audit_path"]).read_bytes() == audit_bytes


def test_a_conflicting_audit_for_the_same_head_is_never_overwritten(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    audit_path = recovery.audit_path_for(run_dir, HEAD_SHA)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    foreign = json.dumps({"schema_version": 1, "result": "REPARSED"}) + "\n"
    audit_path.write_text(foreign, encoding="utf-8")
    before = (run_dir / "sources.json").read_bytes()

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_AUDIT_CONFLICT"
    assert audit_path.read_text(encoding="utf-8") == foreign
    assert (run_dir / "sources.json").read_bytes() == before


# ------------------------------- commit transaction: ledger + audit ---
#
# A failed recovery must leave the run byte-identical to how it was found.
# The ledger write and the audit finalisation are therefore one transaction:
# if the audit cannot be written, the ledger is rolled back to its
# pre-recovery bytes rather than left silently corrected with no evidence.


def test_a_failed_audit_write_rolls_the_ledger_back(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    real_write = recovery.atomic_write_text

    def failing_write(path, content):
        if recovery.AUDIT_DIRNAME in Path(path).parts:
            raise OSError("no space left on device")
        return real_write(path, content)

    monkeypatch.setattr(recovery, "atomic_write_text", failing_write)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_AUDIT_WRITE_FAILED"
    assert (run_dir / "sources.json").read_bytes() == before
    assert not list((run_dir / "working" / recovery.AUDIT_DIRNAME).glob("*.json"))


def test_a_failed_ledger_readback_rolls_the_ledger_back(
    tmp_path, monkeypatch, no_subprocess
):
    """Any post-commit failure, not only the audit write, restores the bytes."""
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    real_write = recovery.atomic_write_text

    def corrupting_write(path, content):
        if Path(path).name == "sources.json" and content != before.decode("utf-8"):
            return real_write(path, content.replace('"schema_version": 3', '"schema_version": 1'))
        return real_write(path, content)

    monkeypatch.setattr(recovery, "atomic_write_text", corrupting_write)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_WRITE_FAILED"
    assert (run_dir / "sources.json").read_bytes() == before


def test_an_unusable_audit_destination_is_refused_before_the_ledger_is_touched(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    # A regular file where the audit directory must be: unusable, and known
    # to be unusable before anything is committed.
    (run_dir / "working" / recovery.AUDIT_DIRNAME).write_text("", encoding="utf-8")

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID"
    )
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_failed_rollback_is_reported_as_such(tmp_path, monkeypatch, no_subprocess):
    """If the restoration itself cannot be completed, the operator is told the
    run needs inspection instead of being handed an ordinary failure."""
    run_dir = build_run(tmp_path, monkeypatch)
    real_write = recovery.atomic_write_text
    calls = {"count": 0}

    def failing_write(path, content):
        if recovery.AUDIT_DIRNAME in Path(path).parts:
            raise OSError("no space left on device")
        if Path(path).name == "sources.json":
            calls["count"] += 1
            if calls["count"] > 1:  # the rollback write
                raise OSError("read-only file system")
        return real_write(path, content)

    monkeypatch.setattr(recovery, "atomic_write_text", failing_write)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert excinfo.value.code == "PRODUCTION_DISCOVERY_REPARSE_ROLLBACK_FAILED"
    assert "AUDIT_WRITE_FAILED" in excinfo.value.message


# ------------------------------------------- audit path symlink escape ---


def test_a_symlinked_audit_directory_is_never_followed(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_dir / "working" / recovery.AUDIT_DIRNAME).symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID"
    )
    assert list(outside.iterdir()) == [], "the recovery wrote outside the run"
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_symlinked_audit_file_is_never_followed(tmp_path, monkeypatch, no_subprocess):
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    audit_dir = run_dir / "working" / recovery.AUDIT_DIRNAME
    audit_dir.mkdir(parents=True)
    (audit_dir / f"{HEAD_SHA}.json").symlink_to(outside / "escaped.json")

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_AUDIT_DESTINATION_INVALID"
    )
    assert list(outside.iterdir()) == []
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_symlinked_working_sidecar_is_refused_by_the_run_scan(
    tmp_path, monkeypatch, no_subprocess
):
    """``working`` itself is a top-level run entry, so the run directory scan
    refuses the symlink before the audit destination is ever considered."""
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    outside = tmp_path / "outside"
    (outside / "working").mkdir(parents=True)
    (outside / "working" / "runtime_security.json").write_bytes(
        (run_dir / "working" / "runtime_security.json").read_bytes()
    )
    import shutil

    shutil.rmtree(run_dir / "working")
    (run_dir / "working").symlink_to(outside / "working", target_is_directory=True)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT"
    )
    assert not (outside / "working" / recovery.AUDIT_DIRNAME).exists()
    assert (run_dir / "sources.json").read_bytes() == before


# --------------------------------------- stale parser-derived leftovers ---


def _make_legacy_parse_failed(run_dir: Path) -> bytes:
    """The pre-fix state where the old parser gave up entirely on one route."""
    ledger = _load_sources(run_dir)
    attempt = _attempt(ledger, VOLUME_SOURCE_ID)
    stale_refs = {
        value["source_ref"] for value in attempt["values"] if "source_ref" in value
    }
    attempt["status"] = "PARSE_FAILED"
    attempt["values"] = None
    attempt["result_count"] = None
    attempt["notes"] = ["RANKING_ROWS_AMBIGUOUS", "LEGACY_PARSER_GAVE_UP"]
    attempt["coverage_status"] = "PARTIAL"
    attempt["coverage_start"] = f"{TRADING_DATE}T00:00:00+09:00"
    attempt["coverage_end"] = f"{TRADING_DATE}T20:00:00+09:00"
    attempt["covered_dates"] = [TRADING_DATE]
    ledger["sources"] = [
        value for value in ledger["sources"] if value["source_ref"] not in stale_refs
    ]
    return _rewrite_sources(run_dir, ledger)


def test_recovery_clears_stale_parser_derived_fields(
    tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    _make_legacy_parse_failed(run_dir)

    result = run_recovery(tmp_path)

    assert result["result"] == recovery.RESULT_REPARSED
    attempt = _attempt(_load_sources(run_dir), VOLUME_SOURCE_ID)
    assert attempt["status"] == "FOUND"
    assert attempt["notes"] == []
    assert "LEGACY_PARSER_GAVE_UP" not in json.dumps(attempt)
    for field_name in recovery.STALE_PARSER_DERIVED_FIELDS:
        assert field_name not in attempt, field_name
    assert len(_tickers(attempt)) == 50
    assert len(_rows(attempt)) == 50


@pytest.mark.parametrize("field_name", IDENTITY_FIELDS)
def test_clearing_stale_fields_never_touches_identity(
    field_name, tmp_path, monkeypatch, no_subprocess
):
    run_dir = build_run(tmp_path, monkeypatch)
    _make_legacy_parse_failed(run_dir)
    before = _attempt(_load_sources(run_dir), VOLUME_SOURCE_ID)

    run_recovery(tmp_path)

    after = _attempt(_load_sources(run_dir), VOLUME_SOURCE_ID)
    assert after.get(field_name) == before.get(field_name)


def test_a_legacy_parse_failed_attempt_reappears_in_market_research(
    tmp_path, monkeypatch
):
    """End to end: a route the old parser failed on becomes a real TOP50."""
    run_dir = build_run(tmp_path, monkeypatch)
    _make_legacy_parse_failed(run_dir)
    run_recovery(tmp_path)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("acquire-discovery must not re-fetch after a recovery")

    monkeypatch.setattr(subprocess, "run", explode)
    cli.main(
        [
            "acquire-discovery",
            "--target-date", TARGET_DATE,
            "--trading-date", TRADING_DATE,
            "--research-cutoff", CUTOFF,
            "--run-dir", str(run_dir),
            "--sources", str(run_dir / "sources.json"),
            "--research-window", str(run_dir / "research_window.json"),
        ]
    )
    research = json.loads((run_dir / "market_research.json").read_text(encoding="utf-8"))
    assert research["overall_status"] == "PIPELINE_INCOMPLETE"
    assert [route["result_count"] for route in research["discovery"]] == [50, 50]


# ------------------------- Physical Request Record immutability in-flight ---


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("origin_attempt_id", "att-tampered"),
        ("origin_source_id", "SOMETHING_ELSE"),
        ("origin_candidate_code", "7203"),
    ],
)
def test_a_request_record_changed_mid_recovery_is_caught(
    field_name, value, tmp_path, monkeypatch, no_subprocess
):
    """Fields the cross-check never reads are covered too: the whole Request
    Record file is compared as raw bytes, before and after the reparse."""
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    real_confirm = recovery.confirm_discovery_top50

    def mutate_then_confirm(routes):
        _mutate_request_record(run_dir, VOLUME_SOURCE_ID, **{field_name: value})
        return real_confirm(routes)

    monkeypatch.setattr(recovery, "confirm_discovery_top50", mutate_then_confirm)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_CHANGED_DURING_RECOVERY"
    )
    assert (run_dir / "sources.json").read_bytes() == before


def test_a_reformatted_request_record_is_still_a_change(
    tmp_path, monkeypatch, no_subprocess
):
    """Byte comparison, not semantic comparison: a rewritten Request Record is
    a rewritten Request Record."""
    run_dir = build_run(tmp_path, monkeypatch)
    before = (run_dir / "sources.json").read_bytes()
    real_confirm = recovery.confirm_discovery_top50

    def reformat_then_confirm(routes):
        attempt = _attempt(_load_sources(run_dir), VOLUME_SOURCE_ID)
        path = run_dir / "network_requests" / f"{attempt['request_id']}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(record, indent=4) + "\n", encoding="utf-8")
        return real_confirm(routes)

    monkeypatch.setattr(recovery, "confirm_discovery_top50", reformat_then_confirm)

    with pytest.raises(recovery.ProductionDiscoveryReparseError) as excinfo:
        run_recovery(tmp_path)

    assert (
        excinfo.value.code
        == "PRODUCTION_DISCOVERY_REPARSE_EVIDENCE_CHANGED_DURING_RECOVERY"
    )
    assert (run_dir / "sources.json").read_bytes() == before


# -------------------------------------------------------- HUMAN-ONLY CLI ---


def test_the_human_script_exposes_only_target_date():
    script = (
        Path(recovery.__file__).resolve().parents[1]
        / "scripts"
        / "reparse-production-discovery"
    )
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "HUMAN-ONLY" in text
    assert "NO NETWORK" in text
    assert "A coding agent must never run this" in text
    for forbidden in (
        "--force",
        "--run-dir",
        "--sources",
        "--source-page",
        "--request-id",
        "--url",
        "--ticker",
        "--parser",
        "--source-matrix",
        "--archive-root",
        "--allow-network",
    ):
        assert f'"{forbidden}"' not in text


def test_the_recovery_is_not_a_src_cli_subcommand():
    parser = cli.build_parser()
    choices = set(parser._subparsers._group_actions[0].choices)  # noqa: SLF001
    assert "reparse-production-discovery" not in choices
    assert not any("reparse" in choice for choice in choices)


def test_reparse_main_reports_json_on_success(tmp_path, monkeypatch, capsys):
    build_run(tmp_path, monkeypatch)
    capsys.readouterr()  # discard the fixture acquisition's own CLI summary

    exit_code = recovery.reparse_main(
        ["--target-date", TARGET_DATE],
        daytrade_root=tmp_path,
        run_command=fake_git(),
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == recovery.RESULT_REPARSED
    assert payload["network_request_count"] == 0
    assert payload["stage"] == "DISCOVERY"


def test_reparse_main_reports_the_error_code_on_failure(tmp_path, capsys):
    exit_code = recovery.reparse_main(
        ["--target-date", TARGET_DATE],
        daytrade_root=tmp_path,
        run_command=fake_git(),
    )

    assert exit_code == 2
    assert "PRODUCTION_DISCOVERY_REPARSE_RUN_MISSING" in capsys.readouterr().err


def test_every_raised_error_code_is_declared():
    assert len(set(recovery.ERROR_CODES)) == len(recovery.ERROR_CODES)
    for code in recovery.ERROR_CODES:
        assert code.startswith("PRODUCTION_DISCOVERY_REPARSE_")


# ------------------------------------------------------------- T24 guard ---


def test_the_production_runtime_guard_does_not_approve_the_recovery():
    guard = (
        Path(recovery.__file__).resolve().parents[1]
        / "ops"
        / "claude"
        / "daytrade_runtime_guard.py"
    ).read_text(encoding="utf-8")
    assert "reparse-production-discovery" not in guard
    assert "production_discovery_reparse" not in guard


def test_the_recovery_audit_is_not_a_business_artifact():
    from src.contracts import RUN_ARTIFACT_ALLOWLIST, WORKING_SIDECAR_DIR

    assert recovery.AUDIT_DIRNAME not in RUN_ARTIFACT_ALLOWLIST
    assert WORKING_SIDECAR_DIR == recovery.WORKING_DIRNAME
