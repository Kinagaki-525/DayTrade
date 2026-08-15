"""FIX-009: the Event chain, end to end, through the real CLIs.

This is the definition of "Event implementation done":

    fake raw pages
      -> acquire-event-sources
      -> (local, read-only) AI classification, stubbed here
      -> merge-event-source-extraction
      -> init-event-research / complete-event-research
      -> validate-event-research
      -> build-event-gate

and for a clean synthetic universe the gate must reach
``event_gate_complete == true``, ``gate_status == "PASS"`` and
``ranking_ready == true``.

Nothing here touches the network: the curl subprocess is replaced by
:mod:`tests.fake_transport`, which also counts the GETs actually issued.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from src import cli
from src.config import (
    DEFAULT_CONFIG_PATH,
    load_strategy_config,
    strategy_config_sha256,
)
from tests import fake_transport
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
PREVIOUS_DAY = "2026-08-11"
CUTOFF = "2026-08-11T20:00:00+09:00"
#: The gate only sees attempts requested at or before this instant. The
#: acquisition attempts are stamped with the real wall clock, so the cutoff is
#: taken just after them rather than from the synthetic fixture calendar.
EVENT_GATE_AS_OF = (
    datetime.now(timezone.utc) + timedelta(hours=1)
).isoformat(timespec="seconds")
TICKERS = ("7203", "6758")

CONFIG = load_strategy_config(DEFAULT_CONFIG_PATH)
CONFIG_SHA256 = strategy_config_sha256(CONFIG)
EVENT_GATE_CONFIG = CONFIG["event_gate"]

#: Every source id the shipped Event Gate configuration actually reads.
REQUIRED_EVENT_SOURCE_IDS = (
    set(EVENT_GATE_CONFIG["earnings"]["target_date_source_ids"])
    | set(EVENT_GATE_CONFIG["earnings"]["previous_day_source_ids"])
    | {EVENT_GATE_CONFIG["tdnet"]["source_id"]}
    | set(EVENT_GATE_CONFIG["news"]["required_source_ids"])
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


ISSUER_HOST = "ir.example-issuer.co.jp"


def _issuer_registry(tmp_path, tickers=TICKERS):
    """A human-approved issuer registry, so COMPANY_IR* can be acquired."""
    path = tmp_path / "issuer_domain_registry.yaml"
    entries = "\n".join(
        f"  - ticker: '{ticker}'\n"
        f"    issuer_name: Example {ticker} Corporation\n"
        f"    approved_hosts:\n"
        f"      - {ISSUER_HOST}\n"
        f"    approved_by: human-reviewer\n"
        f"    approved_at: '2026-08-01'"
        for ticker in tickers
    )
    path.write_text(
        "registry_schema_version: 1\n"
        "approval_policy:\n"
        "  human_approved_only: true\n"
        "  auto_discovery_allowed: false\n"
        f"issuers:\n{entries}\n",
        encoding="utf-8",
    )
    return path


def _feature(value, estimated=False):
    return {
        "value": value,
        "source_refs": ["src-feature"],
        "source_urls": ["https://finance.yahoo.co.jp/quote/7203.T/history"],
        "estimated": estimated,
    }


def _candidates(tmp_path, tickers=TICKERS):
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "generated_at": "2026-08-11T21:00:00+00:00",
        "strategy_version": CONFIG["strategy_version"],
        "config_sha256": CONFIG_SHA256,
        "candidates": [
            {
                "ticker": ticker,
                "status": "ELIGIBLE",
                "screening_status": "PASS",
                "reasons": [],
                "unresolved_screening": [],
                "passed_rules": [],
                "failed_rules": [],
                "unavailable_rules": [],
                "reason_codes": [],
                "source_refs": [],
                "rule_evaluations": [],
                "features": {
                    "previous_day_volume": _feature(3000000),
                    "required_capital_yen": _feature("90200"),
                    "daily_range_yen": _feature("30"),
                    "daily_range_pct": _feature("3.37"),
                    "previous_day_change_pct": _feature("1.71"),
                    "turnover": _feature("2655000000"),
                    "estimated_turnover": _feature("2670000000", estimated=True),
                },
                "order_plan": {
                    "strategy_version": CONFIG["strategy_version"],
                    "validation_status": "unvalidated",
                    "entry_trigger": "901",
                    "entry_limit": "902",
                    "affordable": True,
                    "estimated_purchase_amount": "90200",
                    "expected_loss_yen": "500",
                    "shares": 100,
                    "take_profit_price": "910",
                    "stop_loss_price": "897",
                },
            }
            for ticker in tickers
        ],
    }
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _candidate_pipeline(tmp_path, tickers=TICKERS):
    count = len(tickers)
    payload = {
        "schema_version": 1,
        "target_date": TARGET_DATE,
        "generated_at": "2026-08-11T21:00:00+00:00",
        "strategy_version": CONFIG["strategy_version"],
        "config_sha256": CONFIG_SHA256,
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
                "ticker": ticker,
                "company_name": f"Example {ticker} Corporation",
                "market": "プライム",
                "discovery_reasons": [
                    {
                        "discovery_type": "VOLUME_RANKING",
                        "source_id": "YAHOO_JP_VOLUME_RANKING",
                        "source_url": (
                            "https://finance.yahoo.co.jp/stocks/ranking/volume"
                            "?market=all"
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
            for index, ticker in enumerate(tickers, start=1)
        ],
    }
    path = tmp_path / "candidate_pipeline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _acquire_event_sources(tmp_path, monkeypatch, routes=None, tickers=TICKERS):
    """Run acquire-event-sources against a fake transport. Returns the fake."""
    monkeypatch.setenv(
        "DAYTRADE_ISSUER_DOMAIN_REGISTRY", str(_issuer_registry(tmp_path, tickers))
    )
    fake = fake_transport.install(
        monkeypatch,
        fake_transport.FakeCurl(
            fake_transport.ordered_routes(
                routes
                if routes is not None
                else fake_transport.clean_run_routes(tickers)
            )
        ),
    )
    _candidates(tmp_path, tickers)
    _candidate_pipeline(tmp_path, tickers)
    cli.main(
        [
            "acquire-event-sources",
            "--target-date", TARGET_DATE,
            "--trading-date", TARGET_DATE,
            "--research-cutoff", CUTOFF,
            "--run-dir", str(tmp_path),
            "--sources", str(tmp_path / "sources.json"),
        ]
    )
    return fake


def _stub_classification(tmp_path, ledger):
    """Stand in for the local, read-only Event AI Classification step.

    It reads only attempts that already exist in the ledger, and for this
    clean scenario finds nothing: no events, no news classifications.
    """
    from src.source_matrix import AI_CLASSIFICATION_SOURCE_IDS

    extractions = [
        {
            "source_attempt_id": attempt["attempt_id"],
            "source_id": attempt["source_id"],
            "ticker": attempt["candidate_code"],
            "trading_date": attempt["target_date"],
            "source_page_sha256": attempt["source_page_sha256"],
            "coverage_status": "COMPLETE",
            "events": [],
            "news_classifications": [],
        }
        for attempt in ledger["source_attempts"]
        if attempt["source_id"] in AI_CLASSIFICATION_SOURCE_IDS
        and attempt["status"] == "FOUND"
    ]
    path = tmp_path / "event_source_extraction.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_date": TARGET_DATE,
                "generated_at": "2026-08-11T21:30:00Z",
                "classifier": "stub-event-classifier",
                "extractions": extractions,
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_event_chain(tmp_path):
    """merge -> init -> complete -> validate -> build-event-gate."""
    sources = tmp_path / "sources.json"
    ledger = json.loads(sources.read_text(encoding="utf-8"))
    extraction = _stub_classification(tmp_path, ledger)

    cli.main(
        [
            "merge-event-source-extraction",
            "--extraction", str(extraction),
            "--sources", str(sources),
            "--run-dir", str(tmp_path),
        ]
    )

    event_research = tmp_path / "event_research.json"
    cli.main(
        [
            "init-event-research",
            "--candidate-pipeline", str(tmp_path / "candidate_pipeline.json"),
            "--candidates", str(tmp_path / "candidates.json"),
            "--previous-trading-day", PREVIOUS_DAY,
            "--output", str(event_research),
        ]
    )
    cli.main(
        [
            "complete-event-research",
            "--event-research", str(event_research),
            "--sources", str(sources),
            "--event-gate-as-of", EVENT_GATE_AS_OF,
            "--extraction", str(extraction),
            "--output", str(event_research),
        ]
    )
    validation = tmp_path / "event_research_validation.json"
    validate_code = cli.main(
        [
            "validate-event-research",
            "--event-research", str(event_research),
            "--candidate-pipeline", str(tmp_path / "candidate_pipeline.json"),
            "--candidates", str(tmp_path / "candidates.json"),
            "--sources", str(sources),
            "--output", str(validation),
        ]
    )

    event_gate = tmp_path / "event_gate.json"
    cli.main(
        [
            "build-event-gate",
            "--event-research", str(event_research),
            "--candidate-pipeline", str(tmp_path / "candidate_pipeline.json"),
            "--candidates", str(tmp_path / "candidates.json"),
            "--sources", str(sources),
            "--output", str(event_gate),
        ]
    )
    return validate_code, json.loads(event_gate.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------


def test_all_six_event_sources_are_acquired(tmp_path, monkeypatch):
    _acquire_event_sources(tmp_path, monkeypatch)
    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    acquired = {attempt["source_id"] for attempt in ledger["source_attempts"]}
    assert acquired == {
        "JPX_TDNET",
        "JPX_EARNINGS_SCHEDULE",
        "COMPANY_IR",
        "COMPANY_IR_DISCLOSURE",
        "YAHOO_JP_NEWS",
        "KABUTAN_NEWS",
    }
    # Everything the shipped Event Gate config reads is actually fetched.
    assert REQUIRED_EVENT_SOURCE_IDS <= acquired


def test_company_ir_is_acquired_when_required(tmp_path, monkeypatch):
    """COMPANY_IR is in event_gate.earnings.target_date_source_ids. Dropping it
    would make the earnings rule permanently DATA_UNAVAILABLE, so it must be
    acquired, candidate-scoped, for every candidate."""
    assert "COMPANY_IR" in EVENT_GATE_CONFIG["earnings"]["target_date_source_ids"]

    _acquire_event_sources(tmp_path, monkeypatch)
    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    company_ir = [
        attempt
        for attempt in ledger["source_attempts"]
        if attempt["source_id"] == "COMPANY_IR"
    ]
    assert {attempt["candidate_code"] for attempt in company_ir} == set(TICKERS)
    assert all(attempt["status"] == "FOUND" for attempt in company_ir)


def test_global_jpx_page_is_reused_but_candidate_attempts_exist(tmp_path, monkeypatch):
    """One shared JPX_TDNET page: network requests == 1, candidate attempts == N.

    The distinction matters -- counting attempts would wrongly suggest N GETs,
    and counting GETs alone would wrongly suggest the Event Gate has evidence
    for only one candidate.
    """
    fake = _acquire_event_sources(tmp_path, monkeypatch)
    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))

    tdnet_url = "https://www.release.tdnet.info/inbs/I_main_00.html"
    assert fake.urls.count(tdnet_url) == 1, "the shared page must be fetched once"

    tdnet_attempts = [
        attempt
        for attempt in ledger["source_attempts"]
        if attempt["source_id"] == "JPX_TDNET"
    ]
    assert len(tdnet_attempts) == len(TICKERS)
    assert {attempt["candidate_code"] for attempt in tdnet_attempts} == set(TICKERS)
    # All N candidate attempts reference the very same stored raw page.
    assert len({attempt["source_page_sha256"] for attempt in tdnet_attempts}) == 1
    assert len({attempt["source_page_path"] for attempt in tdnet_attempts}) == 1
    assert sorted(attempt["cache_status"] for attempt in tdnet_attempts) == [
        "HIT",
        "MISS",
    ]


def test_event_source_chain_reaches_event_gate_pass(tmp_path, monkeypatch):
    """The mandatory end-to-end assertion for a clean synthetic universe."""
    _acquire_event_sources(tmp_path, monkeypatch)
    validate_code, event_gate = _run_event_chain(tmp_path)

    assert validate_code == 0
    assert event_gate["event_gate_complete"] is True
    assert event_gate["ranking_block_reasons"] == []
    assert event_gate["ranking_ready"] is True
    assert {candidate["ticker"] for candidate in event_gate["candidates"]} == set(
        TICKERS
    )
    for candidate in event_gate["candidates"]:
        assert candidate["gate_status"] == "PASS", candidate["reason_codes"]
    assert event_gate["summary"]["pass_count"] == len(TICKERS)


def test_scheduled_earnings_on_the_target_date_rejects(tmp_path, monkeypatch):
    """The gate is genuinely wired to the evidence: a real earnings row on the
    target date must REJECT, not pass."""
    routes = fake_transport.clean_run_routes(TICKERS)
    routes[
        "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/"
    ] = pages.jpx_earnings_schedule_page(
        (("7203", TARGET_DATE, "Q2 results"),)
    )
    _acquire_event_sources(tmp_path, monkeypatch, routes=routes)
    _, event_gate = _run_event_chain(tmp_path)

    by_ticker = {c["ticker"]: c for c in event_gate["candidates"]}
    assert by_ticker["7203"]["gate_status"] == "REJECT"
    assert "EARNINGS_ON_TARGET_DATE" in by_ticker["7203"]["reason_codes"]
    # The other candidate is unaffected: a REJECT is per-candidate evidence, so
    # Ranking still proceeds on the remaining PASS candidate.
    assert by_ticker["6758"]["gate_status"] == "PASS"
    assert event_gate["summary"]["reject_count"] == 1
    assert event_gate["ranking_ready"] is True


def test_tdnet_disclosure_in_the_event_window_rejects(tmp_path, monkeypatch):
    routes = fake_transport.clean_run_routes(TICKERS)
    routes["https://www.release.tdnet.info/inbs/I_main_00.html"] = pages.jpx_tdnet_page(
        (("2026-08-11T18:00:00+09:00", "Downward revision of full-year guidance"),)
    )
    _acquire_event_sources(tmp_path, monkeypatch, routes=routes)
    _, event_gate = _run_event_chain(tmp_path)

    for candidate in event_gate["candidates"]:
        assert candidate["gate_status"] == "REJECT"
        assert "TDNET_DISCLOSURE_FOUND" in candidate["reason_codes"]
    assert event_gate["ranking_ready"] is False


def test_unknown_news_classification_blocks_ranking(tmp_path, monkeypatch):
    """A staged UNKNOWN news signal is DATA_UNAVAILABLE, never an implicit pass."""
    _acquire_event_sources(tmp_path, monkeypatch)
    sources = tmp_path / "sources.json"
    ledger = json.loads(sources.read_text(encoding="utf-8"))
    extraction_path = _stub_classification(tmp_path, ledger)
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))

    for item in extraction["extractions"]:
        if item["source_id"] == "YAHOO_JP_NEWS" and item["ticker"] == "7203":
            item["events"] = [
                {
                    "evidence_id": "ev-unknown-news",
                    "event_type": "NEWS",
                    "event_date": TARGET_DATE,
                    "published_at": "2026-08-11T12:00:00+09:00",
                }
            ]
            item["news_classifications"] = [
                {
                    "news_evidence_id": "ev-unknown-news",
                    "published_at": "2026-08-11T12:00:00+09:00",
                    "signal_type": "UNKNOWN",
                    "subject_status": "CONFIRMED",
                    "confirmation_evidence_ids": [],
                    "confirmation_source_attempt_ids": [],
                }
            ]
    extraction_path.write_text(json.dumps(extraction), encoding="utf-8")

    cli.main(
        [
            "merge-event-source-extraction",
            "--extraction", str(extraction_path),
            "--sources", str(sources),
            "--run-dir", str(tmp_path),
        ]
    )
    event_research = tmp_path / "event_research.json"
    cli.main(
        [
            "init-event-research",
            "--candidate-pipeline", str(tmp_path / "candidate_pipeline.json"),
            "--candidates", str(tmp_path / "candidates.json"),
            "--previous-trading-day", PREVIOUS_DAY,
            "--output", str(event_research),
        ]
    )
    cli.main(
        [
            "complete-event-research",
            "--event-research", str(event_research),
            "--sources", str(sources),
            "--event-gate-as-of", EVENT_GATE_AS_OF,
            "--extraction", str(extraction_path),
            "--output", str(event_research),
        ]
    )
    event_gate_path = tmp_path / "event_gate.json"
    cli.main(
        [
            "build-event-gate",
            "--event-research", str(event_research),
            "--candidate-pipeline", str(tmp_path / "candidate_pipeline.json"),
            "--candidates", str(tmp_path / "candidates.json"),
            "--sources", str(sources),
            "--output", str(event_gate_path),
        ]
    )
    event_gate = json.loads(event_gate_path.read_text(encoding="utf-8"))
    by_ticker = {c["ticker"]: c for c in event_gate["candidates"]}
    assert by_ticker["7203"]["gate_status"] == "DATA_UNAVAILABLE"
    assert "NEWS_CLASSIFICATION_UNKNOWN" in by_ticker["7203"]["reason_codes"]
    assert event_gate["ranking_ready"] is False


def test_event_extraction_values_are_what_the_gate_selects(tmp_path, monkeypatch):
    """The merged Event Objects must be reachable through the gate's own
    canonical-attempt selection, not merely present somewhere in the ledger."""
    from src.event_gate import _attempt_events, select_canonical_attempt

    _acquire_event_sources(tmp_path, monkeypatch)
    sources = tmp_path / "sources.json"
    ledger = json.loads(sources.read_text(encoding="utf-8"))
    extraction = _stub_classification(tmp_path, ledger)
    cli.main(
        [
            "merge-event-source-extraction",
            "--extraction", str(extraction),
            "--sources", str(sources),
            "--run-dir", str(tmp_path),
        ]
    )
    merged = json.loads(sources.read_text(encoding="utf-8"))

    canonical = select_canonical_attempt(
        merged["source_attempts"],
        ticker="7203",
        source_id="YAHOO_JP_NEWS",
        target_date=TARGET_DATE,
        event_gate_as_of=EVENT_GATE_AS_OF,
    )
    assert canonical is not None
    assert canonical["coverage_status"] == "COMPLETE"
    assert _attempt_events(canonical) is not None


@pytest.mark.parametrize("source_id", sorted(REQUIRED_EVENT_SOURCE_IDS))
def test_every_configured_event_source_yields_a_candidate_scoped_attempt(
    source_id, tmp_path, monkeypatch
):
    _acquire_event_sources(tmp_path, monkeypatch)
    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    scoped = {
        attempt["candidate_code"]
        for attempt in ledger["source_attempts"]
        if attempt["source_id"] == source_id
    }
    assert scoped == set(TICKERS), source_id
