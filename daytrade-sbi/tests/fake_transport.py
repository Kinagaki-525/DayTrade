"""A URL-routing fake ``curl`` for the end-to-end acquisition tests.

Automated tests never touch the network. This stands in for the real curl
subprocess at exactly the boundary the production code uses -- body to the
``--output`` file, metadata to stdout -- so the tests exercise the real
transport contract while every byte comes from a synthetic fixture.

It also **counts calls**, which is how the Request Budget and the
"one shared page, N candidate attempts" rule are asserted: not by inspecting
the final ledger (which hides duplicates), but by counting the GETs that were
actually issued.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from src import source_fetch
from tests import source_page_fixtures as pages


TRADING_DATE = pages.TRADING_DATE


class FakeCurl:
    """Routes a URL to a synthetic page and records every request."""

    def __init__(self, routes: dict[str, bytes], default: bytes | None = None) -> None:
        self.routes = routes
        self.default = default
        self.urls: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.urls)

    def body_for(self, url: str) -> bytes | None:
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                return body
        return self.default

    def __call__(self, argv, **kwargs):
        assert kwargs.get("shell") is False, "the transport must never use a shell"
        url = argv[argv.index("--url") + 1]
        self.urls.append(url)
        body = self.body_for(url)
        if body is None:
            Path(argv[argv.index("--output") + 1]).write_bytes(b"")
            return subprocess.CompletedProcess(argv, 0, b"404 text/html", b"")
        Path(argv[argv.index("--output") + 1]).write_bytes(body)
        return subprocess.CompletedProcess(
            argv, 0, b"200 text/html; charset=utf-8", b""
        )


def install(monkeypatch, fake: FakeCurl) -> FakeCurl:
    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "daytrade-test/1.0")
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def clean_run_routes(
    tickers: tuple[str, ...],
    *,
    ranking_tickers: tuple[str, ...] | None = None,
) -> dict[str, bytes]:
    """A synthetic universe in which nothing is wrong.

    Every source resolves, OHLCV agrees between Yahoo and Kabutan, no earnings
    are scheduled, no disclosure is published and no news exists -- i.e. the
    scenario in which the Event Gate should legitimately PASS.
    """
    ranking = pages.yahoo_ranking_page(ranking_tickers or tickers)
    routes: dict[str, bytes] = {
        "https://finance.yahoo.co.jp/stocks/ranking/volume": ranking,
        "https://finance.yahoo.co.jp/stocks/ranking/up": ranking,
        "https://www.jpx.co.jp/corporate/about-jpx/calendar/": pages.jpx_calendar_page(),
        "https://www.jpx.co.jp/equities/trading/domestic/03.html": (
            pages.jpx_trading_unit_page()
        ),
        "https://www.jpx.co.jp/equities/trading/domestic/07.html": (
            pages.jpx_tick_size_page()
        ),
        "https://www.jpx.co.jp/markets/indices/topix/": pages.jpx_topix500_page(),
        # One globally-shared TDnet index page, relevant to every candidate.
        "https://www.release.tdnet.info/inbs/I_main_00.html": pages.jpx_tdnet_page(),
        "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/": (
            pages.jpx_earnings_schedule_page()
        ),
    }
    listing_rows = "".join(
        f"<tr><td>{code}</td><td>Example {code} Corporation</td><td>プライム</td></tr>"
        for code in tickers
    )
    routes["https://www.jpx.co.jp/listing/co-search/"] = (
        "<html><head><meta charset=\"utf-8\"></head><body><table><tbody>"
        f"{listing_rows}</tbody></table></body></html>"
    ).encode("utf-8")

    for code in tickers:
        routes[f"https://finance.yahoo.co.jp/quote/{code}.T/history"] = (
            pages.yahoo_history_page(code)
        )
        routes[f"https://kabutan.jp/stock/kabuka?code={code}"] = (
            pages.kabutan_history_page(code)
        )
        routes[f"https://finance.yahoo.co.jp/quote/{code}.T/news"] = (
            pages.news_page(code)
        )
        routes[f"https://kabutan.jp/stock/news?code={code}"] = pages.news_page(code)
        # The bare quote URL must be matched last: it is a prefix of the
        # history and news URLs above.
        routes[f"https://finance.yahoo.co.jp/quote/{code}.T"] = pages.yahoo_quote_page(
            code
        )
    return routes


def ordered_routes(routes: dict[str, bytes]) -> dict[str, bytes]:
    """Longest prefix first, so ``/quote/7203.T`` cannot shadow
    ``/quote/7203.T/history``."""
    return {key: routes[key] for key in sorted(routes, key=len, reverse=True)}


def clean_fake(tickers: tuple[str, ...], **kwargs) -> FakeCurl:
    return FakeCurl(ordered_routes(clean_run_routes(tickers, **kwargs)))


__all__: list[str] = [
    "FakeCurl",
    "TRADING_DATE",
    "clean_fake",
    "clean_run_routes",
    "install",
    "ordered_routes",
]

_ = Callable  # re-exported typing convenience for test helpers
