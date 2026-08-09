# Nightly Research

Target date: 2026-08-10
Previous trading day: 2026-08-07
research_cutoff: 2026-08-07T20:00:00+09:00
research_executed_at: 2026-08-09T18:32:58+09:00

## Discovery Results

- YAHOO_JP_VOLUME_RANKING: FOUND. ALL_MARKETS, TOP50, update time 2026/08/07 18:40. Evidence: `source_pages/yahoo_volume.html`.
- YAHOO_JP_GAIN_RANKING: FOUND. ALL_MARKETS, TOP50, update time 2026/08/07 18:40. Evidence: `source_pages/yahoo_gain.html`.
- JPX_TDNET: PARSE_FAILED. The saved 2026-08-07 page 1 showed additional pages. Full research_window extraction was not completed, so the result is not a confirmed zero-disclosure result.
- Discovery union: 99 tickers from the two Yahoo routes only. TDnet candidates were not admitted because the TDnet route is incomplete.

## Source Audit

- JPX market holiday evidence lists 2026-08-11 as a market holiday and does not list 2026-08-10 or 2026-08-07. Combined with the JPX weekend/holiday closure rule, this run uses 2026-08-10 as target_date and 2026-08-07 as previous_trading_day.
- Discovery used only Source Matrix routes: VOLUME_RANKING, PRICE_GAIN_RANKING, and TIMELY_DISCLOSURE.
- Yahoo ranking rank and display values were saved only as Discovery reasons. They were not used as final ranking scores.

## Data Gaps And Conflicts

- TDnet full-page extraction for the research_window is incomplete.
- Candidate-level listed-company details, Yahoo/Kabutan OHLCV, JPX tick size, earnings schedule, candidate disclosures, and related news were not verified for the Discovery union.
- No missing or unverified values were filled into `market_data.json`.
- No source conflicts were resolved by inference.

## Partial Researcher Findings After Cutoff

- A read-only market researcher returned after the initial artifacts were generated. The additional findings are partial and do not change the `DATA_UNAVAILABLE` decision.
- Confirmed partial TDnet candidate matches included 2181, 4564, 4767, 6400, 6439, and 6993 inside the research_window.
- Candidate OHLCV was partially cross-checked for several low-price candidates, but listed-company details, share unit, security type, TOPIX500 membership, and candidate-level tick size remained incomplete.
- Two OHLCV volume conflicts were reported: 1360 and 1306 had a one-share Yahoo/Kabutan volume difference.
- 6439 was reported as single-source-only because the Yahoo `.T` template did not match the Nagoya listing while Kabutan had an OHLCV row.
- JPX_EARNINGS_SCHEDULE was reported as FOUND for 6740 and 8729 on 2026-08-10.
- Because the full Source Matrix candidate research was not completed and required trade-critical values remained missing, none of these partial findings were promoted into a tradeable `market_data.json` record.

## Discovery Information Not Used For Ranking

- Yahoo ranking rank, transaction price, price change, volume, and gain rate were used only as Discovery evidence.
- TDnet was not used for candidate discovery or final comparison because it is incomplete.

## Codex Comparison Assessment

The required TDnet Discovery and candidate-level trade-critical data are incomplete. The workflow did not reach an ELIGIBLE-candidate comparison. The recommendation decision is `DATA_UNAVAILABLE`.
