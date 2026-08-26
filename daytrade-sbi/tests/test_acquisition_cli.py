"""CLI-level contracts for the acquisition commands (transport fully mocked).

No test in this file touches the network: the curl subprocess is replaced by
:mod:`tests.fake_transport`, which serves synthetic pages and counts requests.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src import cli
from tests import fake_transport
from tests import source_page_fixtures as pages
from tests.factories import make_market_record, make_matching_source_attempt


# The nightly relation, never the same-day shortcut: acquisition runs on the
# evening of the previous trading day (which is what the page fixtures are
# dated, and what the OHLCV parsers match rows against) for the next session.
TRADING_DATE = pages.TRADING_DATE
PREVIOUS_DAY = TRADING_DATE
TARGET_DATE = "2026-08-13"
CUTOFF = f"{TRADING_DATE}T20:00:00+09:00"
TICKERS = ("7203", "6758")


def _acquisition_args(tmp_path):
    """CLI args **and** the canonical research_window.json they must match.

    The two are deliberately produced together: every acquisition command
    validates its --target-date / --trading-date / --research-cutoff against
    <run-dir>/research_window.json, so a test that supplies one without the
    other is not a runnable nightly context.
    """
    _research_window(tmp_path)
    return [
        "--target-date", TARGET_DATE,
        "--trading-date", TRADING_DATE,
        "--research-cutoff", CUTOFF,
        "--run-dir", str(tmp_path),
        "--sources", str(tmp_path / "sources.json"),
    ]


def _research_window(
    tmp_path,
    *,
    target_date=TARGET_DATE,
    previous_trading_day=PREVIOUS_DAY,
    research_cutoff=CUTOFF,
):
    """Write the run's canonical research_window.json and return its path."""
    window_start = (
        f"{date.fromisoformat(previous_trading_day) - timedelta(days=1)}"
        "T20:00:00+09:00"
    )
    path = tmp_path / "research_window.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_date": target_date,
                "previous_trading_day": previous_trading_day,
                "research_cutoff": research_cutoff,
                "research_window": {
                    "run_type": "FIRST_RUN",
                    "window_start": window_start,
                    "window_end": research_cutoff,
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
        cli.main([command, *_acquisition_args(tmp_path), "--ticker", "7203"])


def test_stage1_cli_derives_tickers_from_discovery(tmp_path, clean_curl):
    _market_research(tmp_path, TICKERS)
    output = tmp_path / "working" / "result.json"
    cli.main(["acquire-stage1-sources", *_acquisition_args(tmp_path), "--output", str(output)])

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
        cli.main(["acquire-stage1-sources", *_acquisition_args(tmp_path)])
    assert excinfo.value.code == "STAGE1_REQUIRES_MARKET_RESEARCH"
    assert clean_curl.call_count == 0


def test_stage2_cli_derives_tickers_from_stage1(tmp_path, clean_curl):
    _market_research(tmp_path, TICKERS, stage1_passed=("7203",))
    cli.main(["acquire-stage2-market-sources", *_acquisition_args(tmp_path)])

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

    FIX-R2-002: the exact same Logical Attempt (source_id, candidate_code,
    url, target_date, research_cutoff) is immutable once written -- a
    second run must reuse it byte-for-byte, not "upgrade" its original
    cache_status=MISS into a HIT. Re-running still costs zero additional
    transport calls; what changes is that the ledger keeps recording the
    truth of what actually happened (this attempt is the one that made the
    real GET), not a fiction about how the second run obtained it.
    """
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    args = ["acquire-actual-turnover", *_acquisition_args(tmp_path)]

    cli.main(args)
    assert clean_curl.call_count == 1

    cli.main(args)
    assert clean_curl.call_count == 1, "the second run must be served from cache"

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    attempts = ledger["source_attempts"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["cache_status"] == "MISS"
    assert attempt["network_request_performed"] is True
    assert attempt["request_id"]

    first_requested_at = attempt["requested_at"]
    first_retrieved_at = attempt["retrieved_at"]
    first_request_id = attempt["request_id"]
    first_sha = attempt["source_page_sha256"]

    cli.main(args)
    assert clean_curl.call_count == 1

    ledger_again = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    attempt_again = ledger_again["source_attempts"][0]
    assert attempt_again["requested_at"] == first_requested_at
    assert attempt_again["retrieved_at"] == first_retrieved_at
    assert attempt_again["request_id"] == first_request_id
    assert attempt_again["source_page_sha256"] == first_sha
    assert attempt_again["cache_status"] == "MISS"


def test_tampered_cached_page_hard_stops_instead_of_refetching(tmp_path, clean_curl):
    from src.source_fetch import SourceFetchError

    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    args = ["acquire-actual-turnover", *_acquisition_args(tmp_path)]
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
    output = tmp_path / "working" / "result.json"
    code = cli.main(
        ["acquire-actual-turnover", *_acquisition_args(tmp_path), "--output", str(output)]
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

    output = tmp_path / "working" / "result.json"
    code = cli.main(
        ["acquire-stage1-sources", *_acquisition_args(tmp_path), "--output", str(output)]
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
    output = tmp_path / "working" / "result.json"

    code = cli.main(
        [
            "acquire-discovery",
            *_acquisition_args(tmp_path),
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
        ["acquire-discovery", *_acquisition_args(tmp_path), "--research-window", str(window)]
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
        ["acquire-discovery", *_acquisition_args(tmp_path), "--research-window", str(window)]
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
    cli.main(["acquire-stage1-sources", *_acquisition_args(tmp_path)])

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
    cli.main(["acquire-stage2-market-sources", *_acquisition_args(tmp_path)])

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
    cli.main(["acquire-stage2-market-sources", *_acquisition_args(tmp_path)])

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["close"] is None
    assert record["data_status"] == "CONFLICT"


def test_turnover_updates_market_data_deterministically(tmp_path, clean_curl):
    _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    args = _acquisition_args(tmp_path)
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


# ------------------------------------------------------------ FIX-R2-001B ---
#
# previous_close / previous_high: generated deterministically from the same
# Yahoo/Kabutan history pages as the rest of OHLCV, selecting the row whose
# own parsed date exactly equals the JPX trading day immediately before
# ``--trading-date`` -- resolved by the shared src.trading_calendar resolver
# from a verified JPX_CALENDAR Source Attempt (Saturdays, Sundays, and
# confirmed JPX holidays excluded; never the nearest earlier row a page
# happens to publish, and never derived at all without calendar evidence).
# These tests deliberately use the real nightly relationship
# target_date > trading_date, not the same-day shortcut other stage tests use.

# CASE A: an ordinary weekday boundary with no holiday in the way.
CASE_A_TARGET_DATE = "2026-08-14"
CASE_A_TRADING_DATE = "2026-08-13"
CASE_A_EXPECTED_PREVIOUS = "2026-08-12"

# CASE B: a holiday boundary. 2026-08-11 is a confirmed JPX non-business day
# (via JPX_CALENDAR evidence), so the previous trading day before 2026-08-12
# is 2026-08-10, not 2026-08-11.
CASE_B_TARGET_DATE = "2026-08-13"
CASE_B_TRADING_DATE = "2026-08-12"
CASE_B_HOLIDAY = "2026-08-11"
CASE_B_EXPECTED_PREVIOUS = "2026-08-10"


def _run_args(tmp_path, *, target_date, trading_date, research_cutoff):
    _research_window(
        tmp_path,
        target_date=target_date,
        previous_trading_day=trading_date,
        research_cutoff=research_cutoff,
    )
    return [
        "--target-date", target_date,
        "--trading-date", trading_date,
        "--research-cutoff", research_cutoff,
        "--run-dir", str(tmp_path),
        "--sources", str(tmp_path / "sources.json"),
    ]


def _run_full_chain(tmp_path, args):
    cli.main(["acquire-stage1-sources", *args])
    cli.main(["acquire-stage2-market-sources", *args])
    cli.main(["acquire-actual-turnover", *args])


def _validate_market(tmp_path):
    output = tmp_path / "market_validation.json"
    cli.main(
        [
            "validate-market",
            "--market-data", str(tmp_path / "market_data.json"),
            "--sources", str(tmp_path / "sources.json"),
            "--output", str(output),
        ]
    )
    return json.loads(output.read_text(encoding="utf-8"))


def _yahoo_history_page(ticker: str, rows: tuple[tuple[str, str, str, str, str, str], ...]) -> bytes:
    """``rows`` are ``(YYYY-MM-DD, open, high, low, close, volume)``."""

    def _jp_date(iso: str) -> str:
        year, month, day = iso.split("-")
        return f"{int(year)}年{int(month)}月{int(day)}日"

    body = "".join(
        f"<tr><td>{_jp_date(row[0])}</td>"
        + "".join(f"<td>{cell}</td>" for cell in row[1:])
        + "</tr>"
        for row in rows
    )
    return f"""<html><head><meta charset="utf-8"></head><body>
<a href="https://finance.yahoo.co.jp/quote/{ticker}.T/history">history</a>
<table><tbody>{body}</tbody></table>
</body></html>""".encode("utf-8")


def _kabutan_history_page(ticker: str, rows: tuple[tuple[str, str, str, str, str, str], ...]) -> bytes:
    body = "".join(
        f"<tr><td>{row[0]}</td>" + "".join(f"<td>{cell}</td>" for cell in row[1:]) + "</tr>"
        for row in rows
    )
    return f"""<html><head><meta charset="utf-8"></head><body>
<span>code={ticker}</span>
<table><tbody>{body}</tbody></table>
</body></html>""".encode("utf-8")


def _install_history_routes(monkeypatch, routes, ticker, yahoo_rows, kabutan_rows):
    routes[f"https://finance.yahoo.co.jp/quote/{ticker}.T/history"] = _yahoo_history_page(
        ticker, yahoo_rows
    )
    routes[f"https://kabutan.jp/stock/kabuka?code={ticker}"] = _kabutan_history_page(
        ticker, kabutan_rows
    )
    fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )


def test_case_a_previous_fields_from_the_exact_calendar_resolved_date(
    tmp_path, monkeypatch
):
    """Ordinary weekday boundary: previous_close/high come from the row
    dated exactly CASE_A_EXPECTED_PREVIOUS, resolved from verified
    JPX_CALENDAR evidence -- and the full CLI chain reaches VALID."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,000", "1,111,100"),
    )
    _install_history_routes(monkeypatch, routes, ticker, rows, rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_A_TARGET_DATE,
        trading_date=CASE_A_TRADING_DATE,
        research_cutoff="2026-08-13T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    # CASE_A_EXPECTED_PREVIOUS (2026-08-12) row: close=1,000 / high=1,010.
    assert record["previous_close"] == "1000"
    assert record["previous_high"] == "1010"
    assert record["data_status"] == "VERIFIED"

    provenance = {item["field_name"]: item for item in record["field_provenance"]}
    assert provenance["previous_close"]["status"] == "VERIFIED"
    assert provenance["previous_close"]["verified_value"] == "1000"
    assert provenance["previous_close"]["primary_source_ref"]
    assert provenance["previous_close"]["secondary_source_ref"]
    assert provenance["previous_high"]["status"] == "VERIFIED"
    assert provenance["previous_high"]["verified_value"] == "1010"

    sources_ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    ledger_field_names = {source.get("field_name") for source in sources_ledger["sources"]}
    assert {"previous_close", "previous_high"} <= ledger_field_names
    # Source Ledger Integrity: every source traces to a real Attempt Value.
    attempt_value_refs = {
        value.get("source_ref")
        for attempt in sources_ledger["source_attempts"]
        for value in (attempt.get("values") or [])
    }
    for source in sources_ledger["sources"]:
        assert source["source_ref"] in attempt_value_refs

    validation = _validate_market(tmp_path)
    assert validation["results"][0]["valid_for_trade"] is True
    assert validation["results"][0]["errors"] == []


def test_case_b_holiday_boundary_skips_the_confirmed_non_business_day(
    tmp_path, monkeypatch
):
    """2026-08-11 is a confirmed JPX holiday. Even though a plausible-looking
    row for it exists on both history pages, previous_close/high must come
    from 2026-08-10, never from the holiday row."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    routes["https://www.jpx.co.jp/corporate/about-jpx/calendar/"] = pages.jpx_calendar_page(
        ((CASE_B_HOLIDAY, "山の日"),)
    )
    yahoo_rows = (
        (CASE_B_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        (CASE_B_HOLIDAY, "9,000", "9,999", "8,999", "9,500", "9,999,999"),
        (CASE_B_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,000", "1,111,100"),
    )
    kabutan_rows = yahoo_rows
    _install_history_routes(monkeypatch, routes, ticker, yahoo_rows, kabutan_rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_B_TARGET_DATE,
        trading_date=CASE_B_TRADING_DATE,
        research_cutoff="2026-08-12T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] == "1000"
    assert record["previous_high"] == "1010"
    assert record["data_status"] == "VERIFIED"

    validation = _validate_market(tmp_path)
    assert validation["results"][0]["valid_for_trade"] is True


def test_previous_date_resolution_fails_closed_when_the_year_is_not_covered(
    tmp_path, monkeypatch
):
    """Calendar evidence that only covers 2025 says nothing about whether any
    day in 2026 was a trading day -- CASE_A_TRADING_DATE (2026-08-13) must
    resolve to no previous date at all, not fall back to a bare weekday
    computation."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    routes["https://www.jpx.co.jp/corporate/about-jpx/calendar/"] = pages.jpx_calendar_page(
        (("2025-01-01", "元日"),)
    )
    rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,000", "1,111,100"),
    )
    _install_history_routes(monkeypatch, routes, ticker, rows, rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_A_TARGET_DATE,
        trading_date=CASE_A_TRADING_DATE,
        research_cutoff="2026-08-13T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["previous_high"] is None
    assert record["data_status"] == "DATA_UNAVAILABLE"


def test_tampered_calendar_attempt_values_fail_previous_fields_closed(
    tmp_path, monkeypatch
):
    """Raw calendar page SHA is untouched, but the recorded Attempt Values
    (non_business_days / calendar_covered_years) were rewritten after
    acquisition. Re-parsing the still-genuine stored page must catch the
    mismatch and fail previous fields closed."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,000", "1,111,100"),
    )
    _install_history_routes(monkeypatch, routes, ticker, rows, rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_A_TARGET_DATE,
        trading_date=CASE_A_TRADING_DATE,
        research_cutoff="2026-08-13T20:00:00+09:00",
    )
    cli.main(["acquire-stage1-sources", *args])

    ledger_path = tmp_path / "sources.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for attempt in ledger["source_attempts"]:
        if attempt["source_id"] == "JPX_CALENDAR":
            for value in attempt["values"]:
                if value["field_name"] == "calendar_covered_years":
                    value["value"] = ["2099"]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    cli.main(["acquire-stage2-market-sources", *args])
    cli.main(["acquire-actual-turnover", *args])

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["previous_high"] is None
    assert record["data_status"] == "DATA_UNAVAILABLE"


def test_expected_previous_row_missing_never_falls_back_to_an_older_row(
    tmp_path, monkeypatch
):
    """CASE A's expected previous date (2026-08-12) has no row on either
    page; an older row (2026-08-11) exists but must never be used."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    yahoo_rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        ("2026-08-11", "980", "1,010", "975", "1,000", "1,111,100"),
    )
    _install_history_routes(monkeypatch, routes, ticker, yahoo_rows, yahoo_rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_A_TARGET_DATE,
        trading_date=CASE_A_TRADING_DATE,
        research_cutoff="2026-08-13T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["previous_high"] is None
    assert record["data_status"] == "DATA_UNAVAILABLE"

    validation = _validate_market(tmp_path)
    result = validation["results"][0]
    assert result["valid_for_trade"] is False
    assert "Missing required market data: previous_close" in result["errors"]
    assert "Missing required market data: previous_high" in result["errors"]


def test_missing_jpx_calendar_evidence_fails_previous_fields_closed(
    tmp_path, monkeypatch
):
    """Stage2 run with no prior Stage1 JPX_CALENDAR attempt on the ledger:
    there is no verified calendar evidence, so previous_close/high must not
    be derived at all -- not from the nearest row, not from any guess."""
    ticker = "7203"
    fake_transport.install(monkeypatch, fake_transport.clean_fake((ticker,)))
    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date="2026-08-13",
        trading_date=pages.TRADING_DATE,
        research_cutoff="2026-08-12T20:00:00+09:00",
    )
    # Stage2 only: no acquire-stage1-sources call, so sources.json never
    # gets a JPX_CALENDAR attempt.
    cli.main(["acquire-stage2-market-sources", *args])
    cli.main(["acquire-actual-turnover", *args])

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["previous_high"] is None
    assert record["data_status"] == "DATA_UNAVAILABLE"


def test_unparseable_jpx_calendar_fails_previous_fields_closed(tmp_path, monkeypatch):
    """JPX_CALENDAR is fetched but PARSE_FAILED (no recognizable date on the
    page): still no verified evidence, so previous fields stay unavailable."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    routes["https://www.jpx.co.jp/corporate/about-jpx/calendar/"] = (
        pages.jpx_calendar_page_unparseable()
    )
    fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )
    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date="2026-08-13",
        trading_date=pages.TRADING_DATE,
        research_cutoff="2026-08-12T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["previous_high"] is None
    assert record["data_status"] == "DATA_UNAVAILABLE"


def test_verified_previous_trading_date_is_none_when_coverage_does_not_form(
    tmp_path, monkeypatch
):
    """FIX-R2-001D test E: with Raw Evidence that carries a year heading but
    no valid holiday rows (so calendar_covered_years never forms),
    src.trading_calendar.verified_previous_trading_date must return None --
    called directly, not just observed indirectly through market_data."""
    from src.trading_calendar import verified_previous_trading_date

    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    routes["https://www.jpx.co.jp/corporate/about-jpx/calendar/"] = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><tbody></tbody></table>"
        "</body></html>"
    ).encode("utf-8")
    fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )
    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    target_date = "2026-08-13"
    trading_date = pages.TRADING_DATE
    research_cutoff = "2026-08-12T20:00:00+09:00"
    args = _run_args(
        tmp_path,
        target_date=target_date,
        trading_date=trading_date,
        research_cutoff=research_cutoff,
    )
    cli.main(["acquire-stage1-sources", *args])

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    result = verified_previous_trading_date(
        ledger,
        run_dir=tmp_path,
        trading_date=trading_date,
        target_date=target_date,
        research_cutoff=research_cutoff,
    )
    assert result is None


def test_incomplete_calendar_coverage_fails_previous_fields_closed_despite_a_convenient_history_row(
    tmp_path, monkeypatch
):
    """FIX-R2-001D test F: the calendar has a year heading but no valid
    holiday rows (coverage does not form), yet the history pages happen to
    carry a plausible-looking previous-day row. History must never be used
    to compensate for missing/incomplete calendar coverage."""
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    routes["https://www.jpx.co.jp/corporate/about-jpx/calendar/"] = (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h2>2026年</h2><table><tbody></tbody></table>"
        "</body></html>"
    ).encode("utf-8")
    fake_transport.install(
        monkeypatch, fake_transport.FakeCurl(fake_transport.ordered_routes(routes))
    )
    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date="2026-08-13",
        trading_date=pages.TRADING_DATE,
        research_cutoff="2026-08-12T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["previous_high"] is None
    assert record["data_status"] != "VERIFIED"


def test_previous_close_conflict_on_the_exact_expected_date_fails(tmp_path, monkeypatch):
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    yahoo_rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,000", "1,111,100"),
    )
    kabutan_rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        # Kabutan disagrees with Yahoo on the previous day's close.
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,999", "1,111,100"),
    )
    _install_history_routes(monkeypatch, routes, ticker, yahoo_rows, kabutan_rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_A_TARGET_DATE,
        trading_date=CASE_A_TRADING_DATE,
        research_cutoff="2026-08-13T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_close"] is None
    assert record["data_status"] == "CONFLICT"
    # previous_high (1,010 both sides) is unaffected by the previous_close conflict.
    assert record["previous_high"] == "1010"

    validation = _validate_market(tmp_path)
    assert validation["results"][0]["valid_for_trade"] is False


def test_previous_high_conflict_on_the_exact_expected_date_fails(tmp_path, monkeypatch):
    ticker = "7203"
    routes = fake_transport.clean_run_routes((ticker,))
    yahoo_rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,010", "975", "1,000", "1,111,100"),
    )
    kabutan_rows = (
        (CASE_A_TRADING_DATE, "1,000", "1,100", "990", "1,050", "2,345,600"),
        # Kabutan disagrees with Yahoo on the previous day's high.
        (CASE_A_EXPECTED_PREVIOUS, "980", "1,999", "975", "1,000", "1,111,100"),
    )
    _install_history_routes(monkeypatch, routes, ticker, yahoo_rows, kabutan_rows)

    _market_research(tmp_path, (ticker,), stage1_passed=(ticker,))
    args = _run_args(
        tmp_path,
        target_date=CASE_A_TARGET_DATE,
        trading_date=CASE_A_TRADING_DATE,
        research_cutoff="2026-08-13T20:00:00+09:00",
    )
    _run_full_chain(tmp_path, args)

    record = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )["records"][0]
    assert record["previous_high"] is None
    assert record["data_status"] == "CONFLICT"
    assert record["previous_close"] == "1000"

    validation = _validate_market(tmp_path)
    assert validation["results"][0]["valid_for_trade"] is False


def test_source_ledger_rejects_an_artificial_alias_not_backed_by_an_attempt_value():
    """FIX-R2-001B defect B: a source_ref grafted onto sources.json after
    parsing (no matching source_attempts[].values[] Attempt Value behind it)
    must fail Source Ledger validation, even if it is otherwise well-formed."""
    from src.market import validate_source_ledger

    record = make_market_record()
    ledger_sources = [source.as_dict() for source in record.sources]
    forged = dict(ledger_sources[0])
    forged["source_ref"] = f"{forged['source_ref']}->forged_alias"
    forged["field_name"] = "company_name"
    ledger_sources.append(forged)

    payload = {
        "schema_version": 3,
        "target_date": record.trading_date,
        "sources": ledger_sources,
        "source_attempts": [make_matching_source_attempt(record.sources)],
    }
    result = validate_source_ledger(record.trading_date, [record], payload)

    assert result.valid is False
    assert any(
        "does not trace to any source_attempts[].values[] Attempt Value" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    "field_name,new_value",
    [
        ("value", "999999"),
        ("field_name", "tampered_field"),
        ("source_id", "KABUTAN_HISTORY"),
        ("source_url", "https://finance.yahoo.co.jp/quote/9999.T/history"),
        ("retrieved_at", "2099-01-01T00:00:00+09:00"),
        ("trading_date", "2099-01-01"),
        ("ticker", "9999"),
    ],
)
def test_source_ledger_rejects_a_same_ref_tampered_field(field_name, new_value):
    """FIX-R2-001C section 6: a sources.json entry that keeps its original
    source_ref but has any single field (value/field_name/source_id/
    source_url/retrieved_at/trading_date/ticker) rewritten after acquisition
    must fail -- same-ref existence is not enough, full content must match
    the Attempt Value it claims to come from."""
    from src.market import validate_source_ledger

    record = make_market_record()
    ledger_sources = [source.as_dict() for source in record.sources]
    ledger_sources[0][field_name] = new_value

    payload = {
        "schema_version": 3,
        "target_date": record.trading_date,
        "sources": ledger_sources,
        "source_attempts": [make_matching_source_attempt(record.sources)],
    }
    result = validate_source_ledger(record.trading_date, [record], payload)

    assert result.valid is False
    assert any(
        "does not match the Source Record its Attempt Value implies" in error
        for error in result.errors
    )


def test_derived_tick_size_provenance_missing_a_real_source_ref_fails_validation():
    """tick_size is a Derived Value: field_provenance must reference a real
    JPX_TICK_SIZE PRIMARY source (never a fabricated tick_size-named Source
    Record). Removing that reference must fail validate-market."""
    from dataclasses import replace as dc_replace

    from src.market import validate_market_data

    record = make_market_record()
    tick_provenance = next(
        item for item in record.field_provenance if item["field_name"] == "tick_size"
    )
    stripped = dict(tick_provenance)
    stripped["primary_source_ref"] = None
    stripped["source_refs"] = [
        ref for ref in stripped["source_refs"] if ref != tick_provenance["primary_source_ref"]
    ]
    other_provenance = [
        item for item in record.field_provenance if item["field_name"] != "tick_size"
    ]
    record = dc_replace(record, field_provenance=tuple(other_provenance) + (stripped,))

    result = validate_market_data(record)

    assert result.valid_for_trade is False
    assert "Missing JPX primary source for tick_size" in result.errors


def test_global_source_is_canonical_across_two_candidates(tmp_path, clean_curl):
    """FIX-R2-001C section 9: JPX_TICK_SIZE (a Global Source) must not be
    cloned into a per-candidate Source Record. One network GET, one Attempt
    Value, one canonical Source Record shared by both candidates' provenance,
    and validate-market/validate_source_ledger must accept both at once."""
    tickers = ("7203", "6758")
    _market_research(tmp_path, tickers, stage1_passed=tickers)
    args = _run_args(
        tmp_path,
        target_date="2026-08-13",
        trading_date=pages.TRADING_DATE,
        research_cutoff="2026-08-12T20:00:00+09:00",
    )
    cli.main(["acquire-stage1-sources", *args])

    tick_size_calls_before = sum(
        1 for url in clean_curl.urls if "domestic/07.html" in url
    )
    cli.main(["acquire-stage2-market-sources", *args])
    tick_size_calls_after = sum(
        1 for url in clean_curl.urls if "domestic/07.html" in url
    )
    assert tick_size_calls_after - tick_size_calls_before == 1

    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    tick_size_attempts = [
        attempt
        for attempt in ledger["source_attempts"]
        if attempt["source_id"] == "JPX_TICK_SIZE"
    ]
    assert len(tick_size_attempts) == 1
    tick_size_ledger_sources = [
        source for source in ledger["sources"] if source["source_id"] == "JPX_TICK_SIZE"
    ]
    tick_size_refs = {source["source_ref"] for source in tick_size_ledger_sources}
    assert len(tick_size_refs) == 1, "JPX_TICK_SIZE must be one canonical Source Record"
    assert all(source["ticker"] is None for source in tick_size_ledger_sources)

    market_data = json.loads(
        (tmp_path / "market_data.json").read_text(encoding="utf-8")
    )
    records_by_ticker = {record["ticker"]: record for record in market_data["records"]}
    tick_size_ref = next(iter(tick_size_refs))
    for ticker in tickers:
        record = records_by_ticker[ticker]
        provenance = next(
            item for item in record["field_provenance"] if item["field_name"] == "tick_size"
        )
        assert provenance["primary_source_ref"] == tick_size_ref
        # No candidate-specific clone of the Global Source: exactly one
        # JPX_TICK_SIZE entry in this record's own sources, and it is the
        # canonical ref.
        record_tick_sources = [
            source for source in record["sources"] if source["source_id"] == "JPX_TICK_SIZE"
        ]
        assert {source["source_ref"] for source in record_tick_sources} == {tick_size_ref}

    ledger_result_errors = []
    from src.market import load_market_data, validate_source_ledger

    target_date, market_records = load_market_data(tmp_path / "market_data.json")
    ledger_result = validate_source_ledger(target_date, market_records, ledger)
    assert ledger_result.valid, ledger_result.errors

    output = tmp_path / "market_validation.json"
    cli.main(
        [
            "validate-market",
            "--market-data", str(tmp_path / "market_data.json"),
            "--sources", str(tmp_path / "sources.json"),
            "--output", str(output),
        ]
    )
    validation = json.loads(output.read_text(encoding="utf-8"))
    for result in validation["results"]:
        assert not any(
            "JPX_TICK_SIZE" in error or "Global Source" in error
            for error in result["errors"]
        ), (result["ticker"], result["errors"])


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


# ------------------------------------------------------------- FIX-PRD-001 ---
#
# <run-dir>/research_window.json is the run's only acquisition context:
# --target-date / --trading-date / --research-cutoff are compared against it
# as exact strings *before* any Physical Request is reserved.


def _context_args(tmp_path, **overrides):
    """Canonical nightly CLI args, with deliberate single-value overrides."""
    values = {
        "target_date": TARGET_DATE,
        "trading_date": TRADING_DATE,
        "research_cutoff": CUTOFF,
    }
    values.update(overrides)
    return [
        "--target-date", values["target_date"],
        "--trading-date", values["trading_date"],
        "--research-cutoff", values["research_cutoff"],
        "--run-dir", str(tmp_path),
        "--sources", str(tmp_path / "sources.json"),
    ]


def _assert_no_request_was_reserved(tmp_path, fake):
    """Nothing physical happened: no GET, no Request Record, no artifact.

    A Physical Request is reserved by writing network_requests/<id>.json
    *before* the transport runs, so an absent network_requests directory is
    the evidence the budget was never touched -- asserted alongside the
    transport call count, not instead of it.
    """
    assert fake.call_count == 0
    assert fake.urls == []
    assert not (tmp_path / "network_requests").exists()
    assert not (tmp_path / "source_pages").exists()
    assert not (tmp_path / "sources.json").exists()
    assert not (tmp_path / "market_research.json").exists()
    assert not (tmp_path / "market_data.json").exists()


def test_acquire_rejects_missing_canonical_research_window_before_network(
    tmp_path, monkeypatch
):
    """TEST-001: no canonical context, no acquisition."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))

    with pytest.raises(ValueError) as excinfo:
        cli.main(["acquire-stage1-sources", *_context_args(tmp_path)])

    assert "ACQUISITION_RESEARCH_WINDOW_MISSING" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


def test_acquire_rejects_invalid_canonical_research_window_before_network(
    tmp_path, monkeypatch
):
    """TEST-002: an unreadable context is a hard stop, not a fresh start."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    (tmp_path / "research_window.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        cli.main(["acquire-stage1-sources", *_context_args(tmp_path)])

    assert "ACQUISITION_RESEARCH_WINDOW_INVALID" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


def test_acquire_rejects_target_date_mismatch_before_network(tmp_path, monkeypatch):
    """TEST-003: a target_date the resolved window does not agree with."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    _research_window(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-stage1-sources",
                *_context_args(tmp_path, target_date="2026-08-14"),
            ]
        )

    assert "ACQUISITION_TARGET_DATE_MISMATCH" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


def test_acquire_rejects_trading_date_mismatch_before_network(tmp_path, monkeypatch):
    """TEST-004: the 2026-08-24 incident shape -- trading_date off by a day."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    _research_window(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-stage1-sources",
                *_context_args(tmp_path, trading_date="2026-08-11"),
            ]
        )

    assert "ACQUISITION_TRADING_DATE_MISMATCH" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


def test_acquire_rejects_research_cutoff_mismatch_before_network(
    tmp_path, monkeypatch
):
    """TEST-005: exact strings, not "the same instant".

    ``2026-08-12T11:00:00Z`` is the very same moment as the canonical
    ``2026-08-12T20:00:00+09:00`` and is still rejected: research_cutoff is
    part of the Physical Request identity, so two spellings of one instant
    would be two Request Budgets for a single run.
    """
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    _research_window(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-stage1-sources",
                *_context_args(tmp_path, research_cutoff="2026-08-12T11:00:00Z"),
            ]
        )

    assert "ACQUISITION_RESEARCH_CUTOFF_MISMATCH" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


def test_acquire_discovery_rejects_noncanonical_research_window_path(
    tmp_path, monkeypatch
):
    """TEST-006: path identity, not content equality."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    canonical = _research_window(tmp_path)
    copy = tmp_path / "copied_research_window.json"
    copy.write_bytes(canonical.read_bytes())
    assert copy.read_bytes() == canonical.read_bytes()

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-discovery",
                *_context_args(tmp_path),
                "--research-window", str(copy),
            ]
        )

    assert "ACQUISITION_RESEARCH_WINDOW_PATH_MISMATCH" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


def test_context_mismatch_leaves_existing_artifacts_byte_identical(
    tmp_path, monkeypatch
):
    """TEST-007: a rejected context mutates nothing that already exists.

    Artifacts from earlier steps are present here, so the shared
    _assert_no_request_was_reserved() (which requires market_research.json to
    be absent) does not apply: the no-network and no-mutation conditions are
    spelled out directly instead.
    """
    fake = fake_transport.install(
        monkeypatch,
        fake_transport.clean_fake(("7203",)),
    )

    canonical = _research_window(tmp_path)

    research_path = _market_research(
        tmp_path,
        ("7203",),
        stage1_passed=("7203",),
    )

    before = {
        canonical: canonical.read_bytes(),
        research_path: research_path.read_bytes(),
    }

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-actual-turnover",
                *_context_args(
                    tmp_path,
                    trading_date="2026-08-11",
                ),
            ]
        )

    assert "ACQUISITION_TRADING_DATE_MISMATCH" in str(excinfo.value)

    assert fake.call_count == 0
    assert fake.urls == []

    assert not (tmp_path / "network_requests").exists()
    assert not (tmp_path / "source_pages").exists()
    assert not (tmp_path / "sources.json").exists()

    for path, content in before.items():
        assert path.read_bytes() == content, f"{path.name} was mutated"


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
def test_every_acquisition_command_validates_the_canonical_context_first(
    command, tmp_path, monkeypatch
):
    """The context check is the entry point of every acquire-* command.

    No stage artifact is created on purpose: the mismatch must be reported
    before resolve_stage_candidates would demand market_research.json, which
    is exactly the ordering FIX-PRD-001 requires.
    """
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))

    _research_window(tmp_path)

    argv = [
        command,
        *_context_args(
            tmp_path,
            target_date="2026-08-14",
        ),
    ]
    if command == "acquire-discovery":
        argv += [
            "--research-window",
            str(tmp_path / "research_window.json"),
        ]

    with pytest.raises(ValueError) as excinfo:
        cli.main(argv)

    assert "ACQUISITION_TARGET_DATE_MISMATCH" in str(excinfo.value)
    _assert_no_request_was_reserved(tmp_path, fake)


# ------------------------------------------------------------- FIX-PRD-002 ---
#
# Discovery creates the candidate universe, so an incomplete Discovery is a
# terminal stop -- never an OPEN gate the orchestrator walks past into
# Stage1, and never a NO_TRADE.


def test_discovery_incomplete_closes_the_gate_after_writing_evidence(
    tmp_path, monkeypatch
):
    """TEST-008: CLOSED + exit 1, with the failure evidence kept on disk."""
    fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    window = _research_window(tmp_path)
    output = tmp_path / "working" / "result.json"

    code = cli.main(
        [
            "acquire-discovery",
            *_context_args(tmp_path),
            "--research-window", str(window),
            "--output", str(output),
        ]
    )

    market_research = json.loads(
        (tmp_path / "market_research.json").read_text(encoding="utf-8")
    )
    summary = json.loads(output.read_text(encoding="utf-8"))

    assert market_research["overall_status"] == "DISCOVERY_INCOMPLETE"
    assert code == 1
    assert summary["status"] == "CLOSED"
    # market_research's own canonical notes -- no new reason code invented.
    assert summary["reason_codes"] == market_research["notes"]
    assert market_research["notes"]

    # Evidence First: the run's record of what happened survives the stop.
    assert (tmp_path / "market_research.json").is_file()

    ledger = json.loads(
        (tmp_path / "sources.json").read_text(encoding="utf-8")
    )
    assert ledger["source_attempts"]

    network_request_files = list(
        (tmp_path / "network_requests").glob("*.json")
    )
    assert network_request_files


def test_discovery_summary_candidate_count_is_the_discovery_union(
    tmp_path, monkeypatch
):
    """TEST-009: Discovery reports what it produced, not what it was given.

    ``resolve_stage_candidates("DISCOVERY", ...)`` is empty by contract --
    Discovery *creates* the universe -- so counting the stage's input tickers
    reported 0 candidates for a fully successful Discovery.
    """
    from src.stage_wiring import resolve_stage_candidates

    top50 = pages.top50_tickers()
    fake_transport.install(
        monkeypatch, fake_transport.clean_fake(top50, ranking_tickers=top50)
    )
    window = _research_window(tmp_path)
    output = tmp_path / "working" / "result.json"

    code = cli.main(
        [
            "acquire-discovery",
            *_context_args(tmp_path),
            "--research-window", str(window),
            "--output", str(output),
        ]
    )

    market_research = json.loads(
        (tmp_path / "market_research.json").read_text(encoding="utf-8")
    )
    summary = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert resolve_stage_candidates("DISCOVERY", tmp_path) == []
    assert summary["candidate_count"] == len(
        market_research["discovery_candidates"]
    )
    assert summary["candidate_count"] > 0


def test_successful_discovery_keeps_the_gate_open(tmp_path, monkeypatch):
    """TEST-010: fail-closed must not close the happy path."""
    top50 = pages.top50_tickers()
    fake_transport.install(
        monkeypatch, fake_transport.clean_fake(top50, ranking_tickers=top50)
    )
    window = _research_window(tmp_path)
    output = tmp_path / "working" / "result.json"

    code = cli.main(
        [
            "acquire-discovery",
            *_context_args(tmp_path),
            "--research-window", str(window),
            "--output", str(output),
        ]
    )

    market_research = json.loads(
        (tmp_path / "market_research.json").read_text(encoding="utf-8")
    )
    summary = json.loads(output.read_text(encoding="utf-8"))

    assert market_research["overall_status"] != "DISCOVERY_INCOMPLETE"
    assert code == 0
    assert summary["status"] == "OPEN"
    assert summary["reason_codes"] == []


# --------------------------------------------------------------- FIX-PR13 ---
#
# On the 2026-08-27 production nightly, acquire-discovery was invoked with
# --output <run-dir>/market_research.json. The command writes its Business
# Artifact first and *then* emits the CLI result summary to --output, so the
# summary overwrote the market_research.json the very same command had just
# produced. The dangerous destination is now unrepresentable: --output may
# only name a file under <run-dir>/working/, checked before any Physical
# Request is reserved.


BUSINESS_ARTIFACTS = (
    "market_research.json",
    "market_data.json",
    "sources.json",
    "research_window.json",
    "strategy_snapshot.yaml",
)


@pytest.mark.parametrize("artifact", BUSINESS_ARTIFACTS)
def test_acquire_output_cannot_target_a_business_artifact(
    artifact, tmp_path, monkeypatch
):
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    _research_window(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-stage1-sources",
                *_context_args(tmp_path),
                "--output", str(tmp_path / artifact),
            ]
        )

    assert "ACQUISITION_OUTPUT_PATH_INVALID" in str(excinfo.value)
    assert fake.call_count == 0
    assert not (tmp_path / "network_requests").exists()
    assert not (tmp_path / "source_pages").exists()


def test_acquire_output_cannot_escape_the_run_directory(tmp_path, monkeypatch):
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _research_window(run_dir)

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-stage1-sources",
                "--target-date", TARGET_DATE,
                "--trading-date", TRADING_DATE,
                "--research-cutoff", CUTOFF,
                "--run-dir", str(run_dir),
                "--sources", str(run_dir / "sources.json"),
                "--output", str(tmp_path / "elsewhere.json"),
            ]
        )

    assert "ACQUISITION_OUTPUT_PATH_INVALID" in str(excinfo.value)
    assert fake.call_count == 0
    assert not (run_dir / "network_requests").exists()


def test_acquire_output_cannot_target_the_source_ledger_inside_working(
    tmp_path, monkeypatch
):
    """--sources is off limits wherever it lives, working/ included."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    _research_window(tmp_path)
    ledger = tmp_path / "working" / "sources.json"

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-stage1-sources",
                "--target-date", TARGET_DATE,
                "--trading-date", TRADING_DATE,
                "--research-cutoff", CUTOFF,
                "--run-dir", str(tmp_path),
                "--sources", str(ledger),
                "--output", str(ledger),
            ]
        )

    assert "ACQUISITION_OUTPUT_PATH_INVALID" in str(excinfo.value)
    assert fake.call_count == 0


def test_invalid_output_leaves_existing_business_artifacts_byte_identical(
    tmp_path, monkeypatch
):
    """Not one byte of prior evidence changes, and not one GET is spent."""
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(("7203",)))
    window = _research_window(tmp_path)
    research = _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    before = {path: path.read_bytes() for path in (window, research)}

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-actual-turnover",
                *_context_args(tmp_path),
                "--output", str(tmp_path / "market_research.json"),
            ]
        )

    assert "ACQUISITION_OUTPUT_PATH_INVALID" in str(excinfo.value)
    assert fake.call_count == 0
    assert fake.urls == []
    assert not (tmp_path / "network_requests").exists()
    assert not (tmp_path / "source_pages").exists()
    assert not (tmp_path / "sources.json").exists()
    for path, content in before.items():
        assert path.read_bytes() == content, f"{path.name} was mutated"


def test_acquire_output_under_working_is_allowed(tmp_path, clean_curl):
    _market_research(tmp_path, TICKERS)
    output = tmp_path / "working" / "result.json"

    code = cli.main(
        [
            "acquire-stage1-sources",
            *_acquisition_args(tmp_path),
            "--output", str(output),
        ]
    )

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["stage"] == "STAGE1"
    assert (tmp_path / "market_data.json").is_file()


def test_acquire_without_output_prints_the_summary(tmp_path, clean_curl, capsys):
    _market_research(tmp_path, TICKERS)
    code = cli.main(["acquire-stage1-sources", *_acquisition_args(tmp_path)])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "STAGE1"


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
def test_every_acquisition_command_rejects_an_unsafe_output(
    command, tmp_path, monkeypatch
):
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(TICKERS))
    window = _research_window(tmp_path)

    argv = [
        command,
        *_context_args(tmp_path),
        "--output", str(tmp_path / "market_research.json"),
    ]
    if command == "acquire-discovery":
        argv += ["--research-window", str(window)]

    with pytest.raises(ValueError) as excinfo:
        cli.main(argv)

    assert "ACQUISITION_OUTPUT_PATH_INVALID" in str(excinfo.value)
    assert fake.call_count == 0
    assert not (tmp_path / "network_requests").exists()


def test_acquire_output_cannot_be_an_existing_directory_under_working(
    tmp_path, monkeypatch
):
    """Under working/ is necessary, not sufficient: the summary is a file.

    A directory destination could only fail at write time -- after the stage
    had already spent its Physical Requests and written its Business
    Artifact -- so it is refused pre-acquisition like any other bad --output.
    """
    fake = fake_transport.install(monkeypatch, fake_transport.clean_fake(("7203",)))
    window = _research_window(tmp_path)
    research = _market_research(tmp_path, ("7203",), stage1_passed=("7203",))
    before = {path: path.read_bytes() for path in (window, research)}

    destination = tmp_path / "working" / "result.json"
    destination.mkdir(parents=True)

    with pytest.raises(ValueError) as excinfo:
        cli.main(
            [
                "acquire-actual-turnover",
                *_context_args(tmp_path),
                "--output", str(destination),
            ]
        )

    assert "ACQUISITION_OUTPUT_PATH_INVALID" in str(excinfo.value)
    assert fake.call_count == 0
    assert fake.urls == []
    assert not (tmp_path / "network_requests").exists()
    assert not (tmp_path / "source_pages").exists()
    assert not (tmp_path / "sources.json").exists()
    assert destination.is_dir()
    for path, content in before.items():
        assert path.read_bytes() == content, f"{path.name} was mutated"


def test_acquire_output_may_overwrite_an_existing_working_file(tmp_path, clean_curl):
    """An existing regular file under working/ stays a valid destination."""
    _market_research(tmp_path, TICKERS)
    output = tmp_path / "working" / "result.json"
    output.parent.mkdir(parents=True)
    output.write_text("stale\n", encoding="utf-8")

    code = cli.main(
        [
            "acquire-stage1-sources",
            *_acquisition_args(tmp_path),
            "--output", str(output),
        ]
    )

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["stage"] == "STAGE1"
