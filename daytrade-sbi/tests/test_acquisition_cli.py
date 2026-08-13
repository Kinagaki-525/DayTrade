"""CLI-level contracts for the acquisition commands (transport fully mocked).

No test in this file touches the network: the curl subprocess is replaced by
:mod:`tests.fake_transport`, which serves synthetic pages and counts requests.
"""

from __future__ import annotations

import json

import pytest

from src import cli
from tests import fake_transport
from tests import source_page_fixtures as pages


TARGET_DATE = pages.TRADING_DATE
PREVIOUS_DAY = "2026-08-11"
CUTOFF = "2026-08-11T20:00:00+09:00"
TICKERS = ("7203", "6758")


def _base_args(tmp_path):
    return [
        "--target-date", TARGET_DATE,
        "--trading-date", TARGET_DATE,
        "--research-cutoff", CUTOFF,
        "--run-dir", str(tmp_path),
        "--sources", str(tmp_path / "sources.json"),
    ]


def _research_window(tmp_path):
    path = tmp_path / "research_window.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_date": TARGET_DATE,
                "previous_trading_day": PREVIOUS_DAY,
                "research_cutoff": CUTOFF,
                "research_window": {
                    "run_type": "FIRST_RUN",
                    "window_start": "2026-08-10T20:00:00+09:00",
                    "window_end": CUTOFF,
                    "previous_research_cutoff": None,
                    "previous_run_date": None,
                    "bootstrap_lookback_days": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _market_research(tmp_path, tickers=TICKERS, stage1_passed=()):
    """A minimal market_research.json standing in for earlier pipeline steps."""
    path = tmp_path / "market_research.json"
    path.write_text(
        json.dumps(
            {
                "discovery_candidates": [{"ticker": ticker} for ticker in tickers],
                "candidate_research": [
                    {"ticker": ticker, "stage1_status": "PASSED"}
                    for ticker in stage1_passed
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def clean_curl(monkeypatch):
    return fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))


# ---------------------------------------------------------------- FIX-006 ---


@pytest.mark.parametrize(
    "command",
    [
        "acquire-discovery",
        "acquire-stage1-sources",
        "acquire-stage2-market-sources",
        "acquire-actual-turnover",
        "acquire-event-sources",
    ],
)
def test_no_acquisition_cli_accepts_an_injected_ticker(command, tmp_path):
    """There must be no way for an agent to hand a ticker to the network layer.

    Which tickers get network access is derived from artifacts on disk, so a
    ``--ticker`` option (or any alias of it) must not exist at all.
    """
    parser = cli.build_parser()
    subparser = parser._subparsers._group_actions[0].choices[command]  # noqa: SLF001
    option_strings = {
        option for action in subparser._actions for option in action.option_strings
    }
    for forbidden in ("--ticker", "--tickers", "--candidate", "--candidates", "--code"):
        assert forbidden not in option_strings, f"{command} exposes {forbidden}"

    with pytest.raises(SystemExit):
        cli.main([command, *_base_args(tmp_path), "--ticker", "7203"])


def test_stage1_cli_derives_tickers_from_discovery(tmp_path, clean_curl):
    _market_research(tmp_path, TICKERS)
    output = tmp_path / "result.json"
    cli.main(["acquire-stage1-sources", *_base_args(tmp_path), "--output", str(output)])

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    acquired = {
        attempt["candidate_code"]
        for attempt in ledger["source_attempts"]
        if attempt["candidate_code"]
    }
    assert acquired == set(TICKERS)
    assert json.loads(output.read_text(encoding="utf-8"))["candidate_count"] == 2


def test_stage1_without_discovery_refuses_to_fetch(tmp_path, clean_curl):
    from src.stage_wiring import StageWiringError

    with pytest.raises(StageWiringError) as excinfo:
        cli.main(["acquire-stage1-sources", *_base_args(tmp_path)])
    assert excinfo.value.code == "STAGE1_REQUIRES_MARKET_RESEARCH"
    assert clean_curl.call_count == 0


def test_stage2_cli_derives_tickers_from_stage1(tmp_path, clean_curl):
    _market_research(tmp_path, TICKERS, stage1_passed=("7203",))
    cli.main(["acquire-stage2-market-sources", *_base_args(tmp_path)])

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    acquired = {
        attempt["candidate_code"]
        for attempt in ledger["source_attempts"]
        if attempt["candidate_code"]
    }
    # Only the Stage 1 PASS candidate is fetched; 6758 never reaches the network.
    assert acquired == {"7203"}
    assert not any("6758" in url for url in clean_curl.urls)


# ---------------------------------------------------------------- FIX-005 ---


def test_same_acquisition_cli_twice_performs_one_get(tmp_path, clean_curl):
    """The Request Budget is about *network requests*, not ledger rows.

    Asserting on the merged ledger would hide a duplicate GET, because the
    second attempt overwrites the first under the same attempt_id. So this
    counts the transport calls directly.
    """
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    args = ["acquire-actual-turnover", *_base_args(tmp_path)]

    cli.main(args)
    assert clean_curl.call_count == 1

    cli.main(args)
    assert clean_curl.call_count == 1, "the second run must be served from cache"

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    attempts = ledger["source_attempts"]
    assert len(attempts) == 1
    assert attempts[0]["cache_status"] == "HIT"


def test_tampered_cached_page_hard_stops_instead_of_refetching(tmp_path, clean_curl):
    from src.source_fetch import SourceFetchError

    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    args = ["acquire-actual-turnover", *_base_args(tmp_path)]
    cli.main(args)

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    stored = tmp_path / ledger["source_attempts"][0]["source_page_path"]
    stored.write_bytes(stored.read_bytes() + b"<!-- tampered -->")

    with pytest.raises(SourceFetchError) as excinfo:
        cli.main(args)
    assert excinfo.value.code == "SOURCE_PAGE_HASH_MISMATCH"
    # Crucially: it did NOT silently re-fetch to "self-heal".
    assert clean_curl.call_count == 1


def test_acquire_actual_turnover_writes_a_v3_ledger(tmp_path, clean_curl):
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    output = tmp_path / "result.json"
    code = cli.main(
        ["acquire-actual-turnover", *_base_args(tmp_path), "--output", str(output)]
    )
    assert code == 0

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    assert ledger["schema_version"] == 3
    attempt = ledger["source_attempts"][0]
    assert attempt["source_id"] == "YAHOO_JP_QUOTE"
    assert attempt["status"] == "FOUND"
    assert (tmp_path / attempt["source_page_path"]).is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["stage"] == "TURNOVER"
    assert clean_curl.call_count == 1


def test_acquire_stage1_reports_a_closed_listing_gate(tmp_path, monkeypatch):
    """A listing page that does not carry the candidate closes the batch gate."""
    routes = fake_transport.clean_run_routes(TICKERS)
    routes["https://www.jpx.co.jp/listing/co-search/"] = pages.jpx_listed_company_page(
        "9999"
    )
    fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )
    _market_research(tmp_path, TICKERS)

    output = tmp_path / "result.json"
    code = cli.main(
        ["acquire-stage1-sources", *_base_args(tmp_path), "--output", str(output)]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "CLOSED"
    assert payload["reason_codes"] == ["TSE_LISTING_BATCH_GATE_FAILED"]
    assert code == 1


# ---------------------------------------------------------------- FIX-007 ---


def test_discovery_cli_builds_market_research(tmp_path, monkeypatch):
    """acquire-discovery is atomic: fetch both rankings, save raw evidence,
    parse, confirm TOP50, and emit a schema-valid market_research.json."""
    top50 = pages.top50_tickers()
    fake = fake_transport.install(
        monkeypatch, fake_transport.clean_fake(top50, ranking_tickers=top50)
    )
    window = _research_window(tmp_path)
    output = tmp_path / "result.json"

    code = cli.main(
        [
            "acquire-discovery",
            *_base_args(tmp_path),
            "--research-window", str(window),
            "--output", str(output),
        ]
    )
    assert code == 0
    assert fake.call_count == 2  # exactly the two ranking routes

    research = json.loads(
        (tmp_path / "market_research.json").read_text(encoding="utf-8")
    )
    assert [route["discovery_type"] for route in research["discovery"]] == [
        "VOLUME_RANKING",
        "PRICE_GAIN_RANKING",
    ]
    assert all(route["result_count"] == 50 for route in research["discovery"])
    assert len(research["discovery_candidates"]) == 50
    assert research["discovery_candidates"][0]["company_name"]
    assert sorted(research["discovery_candidates"][0]["discovered_by"]) == [
        "PRICE_GAIN_RANKING",
        "VOLUME_RANKING",
    ]

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    for attempt in ledger["source_attempts"]:
        assert (tmp_path / attempt["source_page_path"]).is_file()


def test_discovery_feeds_init_candidate_research_through_the_real_cli(
    tmp_path, monkeypatch
):
    """The Discovery -> candidate research chain runs through the real CLIs,
    not hand-built fixtures."""
    top50 = pages.top50_tickers()
    fake_transport.install(
        monkeypatch, fake_transport.clean_fake(top50, ranking_tickers=top50)
    )
    window = _research_window(tmp_path)
    cli.main(
        ["acquire-discovery", *_base_args(tmp_path), "--research-window", str(window)]
    )

    initialized = tmp_path / "market_research_initialized.json"
    code = cli.main(
        [
            "init-candidate-research",
            "--market-research", str(tmp_path / "market_research.json"),
            "--output", str(initialized),
        ]
    )
    assert code == 0
    payload = json.loads(initialized.read_text(encoding="utf-8"))
    assert len(payload["candidate_research"]) == 50


def test_discovery_reports_incomplete_when_a_ranking_is_short(tmp_path, monkeypatch):
    fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    window = _research_window(tmp_path)
    cli.main(
        ["acquire-discovery", *_base_args(tmp_path), "--research-window", str(window)]
    )
    research = json.loads(
        (tmp_path / "market_research.json").read_text(encoding="utf-8")
    )
    assert research["overall_status"] == "DISCOVERY_INCOMPLETE"
    assert "VOLUME_RANKING_UNAVAILABLE" in research["notes"]
    # A short ranking is never recorded as a FOUND route with fewer rows: that
    # would silently shrink the candidate universe.
    assert all(route["status"] != "FOUND" for route in research["discovery"])
    assert research["discovery_candidates"] == []


# ---------------------------------------------------------------- FIX-008 ---


def test_stage1_reflects_into_market_data(tmp_path, clean_curl):
    _market_research(tmp_path, ("7203",))
    cli.main(["acquire-stage1-sources", *_base_args(tmp_path)])

    market_data = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )
    record = market_data["records"][0]
    assert record["ticker"] == "7203"
    assert record["company_name"] == "Example 7203 Corporation"
    assert record["market"] == "プライム"
    assert record["share_unit"] == "100"
    assert {item["field_name"] for item in record["field_provenance"]} >= {
        "company_name",
        "market",
        "share_unit",
    }


def test_stage2_cross_checks_ohlcv_into_market_data(tmp_path, clean_curl):
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    cli.main(["acquire-stage2-market-sources", *_base_args(tmp_path)])

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["close"] == "1050"
    assert record["volume"] == "2345600"
    provenance = {item["field_name"]: item for item in record["field_provenance"]}
    # Yahoo PRIMARY / Kabutan SECONDARY agreed, so the field is VERIFIED with
    # both refs recorded -- the existing cross-check policy, unchanged.
    assert provenance["close"]["status"] == "VERIFIED"
    assert provenance["close"]["primary_source_ref"]
    assert provenance["close"]["secondary_source_ref"]
    # tick_size resolved from the JPX band table (close 1050 -> the <=3000 band).
    assert record["tick_size"] == "1"
    assert provenance["tick_size"]["status"] == "VERIFIED"


def test_stage2_conflicting_ohlcv_is_a_conflict_not_a_preference(
    tmp_path, monkeypatch
):
    routes = fake_transport.clean_run_routes(("7203",))
    routes["https://kabutan.jp/stock/kabuka?code=7203"] = (
        b'<html><head><meta charset="utf-8"></head><body><span>code=7203</span>'
        b"<table><tbody><tr><td>2026-08-12</td><td>1,000</td><td>1,100</td>"
        b"<td>990</td><td>9,999</td><td>2,345,600</td></tr></tbody></table>"
        b"</body></html>"
    )
    fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    cli.main(["acquire-stage2-market-sources", *_base_args(tmp_path)])

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["close"] is None
    assert record["data_status"] == "CONFLICT"


def test_turnover_updates_market_data_deterministically(tmp_path, clean_curl):
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    args = _base_args(tmp_path)
    cli.main(["acquire-stage1-sources", *args])
    cli.main(["acquire-stage2-market-sources", *args])
    cli.main(["acquire-actual-turnover", *args])

    first = (tmp_path / "market_data.json").read_text(encoding="utf-8")
    record = json.loads(first)["records"][0]
    # 1,234,567 thousand yen -> canonical yen. The parser canonicalizes once;
    # nothing downstream re-scales it.
    assert record["turnover"] == "1234567000"
    assert record["company_name"] == "Example 7203 Corporation"
    assert record["close"] == "1050"

    # Deterministic: re-running the stage reproduces the same document byte
    # for byte, and issues no additional GET.
    calls = clean_curl.call_count
    cli.main(["acquire-actual-turnover", *args])
    assert (tmp_path / "market_data.json").read_text(encoding="utf-8") == first
    assert clean_curl.call_count == calls


def test_agent_cannot_hand_edit_market_data_through_the_cli():
    """There is no CLI surface that writes a caller-supplied market_data value."""
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices  # noqa: SLF001
    for command in (
        "acquire-stage1-sources",
        "acquire-stage2-market-sources",
        "acquire-actual-turnover",
    ):
        options = {
            option
            for action in choices[command]._actions
            for option in action.option_strings
        }
        for forbidden in ("--close", "--open", "--turnover", "--tick-size", "--volume"):
            assert forbidden not in options


# ------------------------------------------------------------------ misc ---


def test_verify_production_run_cli_reports_invalid(tmp_path):
    output = tmp_path / "verify.json"
    code = cli.main(
        ["verify-production-run", "--run-dir", str(tmp_path), "--output", str(output)]
    )
    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "INVALID_RUN"


def test_activate_selection_config_cli_requires_a_pair_id(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(
            ["activate-selection-config", "--calibration", str(tmp_path / "c.json")]
        )
