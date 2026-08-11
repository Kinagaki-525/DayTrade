# 2026-08-10 Nightly Research

## Discovery Results

- Target date: 2026-08-10; previous trading day: 2026-08-07; research_cutoff: 2026-08-07T20:00:00+09:00.
- JPX Calendar: https://www.jpx.co.jp/corporate/about-jpx/calendar/ was saved. 2026-08-10 and 2026-08-07 are weekdays outside the JPX 2026 holiday list; 2026-08-11 is listed as Mountain Day.
- YAHOO_JP_VOLUME_RANKING: TOP50 acquired from ALL_MARKETS. Page update time: 2026/08/07 18:40; result_count: 50.
- YAHOO_JP_GAIN_RANKING: TOP50 acquired from ALL_MARKETS. Page update time: 2026/08/07 18:40; result_count: 50.
- JPX_TDNET: 0 rows inside 2026-08-06 20:00-23:59; 1627 rows from 2026-08-07 00:00 through 20:00.
- Discovery Union: 972 tickers. Yahoo union: 99; TDnet unique tickers: 902.

## Source Audit

- Only Source Matrix-defined source IDs were used: JPX_CALENDAR, YAHOO_JP_VOLUME_RANKING, YAHOO_JP_GAIN_RANKING, JPX_TDNET.
- Yahoo ranking source URLs are the ranking pages, not individual quote pages.
- TDnet saved pages are under source_pages/tdnet_20260806*.html and source_pages/tdnet_20260807*.html.
- result_count=0 is used only for the parsed 2026-08-06 window segment after all nine pages were checked.

## Missing Data And Conflicts

- Full candidate-level listed-company, OHLCV, tick-size, earnings, disclosure, and news checks were not completed for the 972-ticker Discovery Union.
- market_data.records is empty, so no trade-critical numeric values are adopted.
- No order prices are calculated because verified previous_high and tick_size values are unavailable.

## Discovery Data Not Used For Ranking

- Yahoo ranks, displayed prices, changes, and volume are stored only as Discovery facts.
- TDnet disclosure counts and titles are stored only as Discovery facts.
- These values are not used as final comparison scores or order-price inputs.

## Codex Comparative Evaluation

- No ELIGIBLE candidate exists.
- Discovery completed, but trade-critical data for the full 972-ticker union is unavailable.
- Decision: DATA_UNAVAILABLE. No order values are created.
