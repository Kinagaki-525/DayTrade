# Nightly Research 2026-08-12

## Date Scope
- target_date: 2026-08-12
- previous_trading_day: 2026-08-10
- research_cutoff: 2026-08-10T20:00:00+09:00
- research_window: 2026-08-09T20:00:00+09:00 to 2026-08-10T20:00:00+09:00
- post_cutoff_information_status: OUT_OF_SCOPE

## Discovery
- VOLUME_RANKING: FOUND 50 rows, source=https://finance.yahoo.co.jp/stocks/ranking/volume?market=all, top=8918 (株)ランド 257,953,800
- PRICE_GAIN_RANKING: FOUND 50 rows, source=https://finance.yahoo.co.jp/stocks/ranking/up?market=all, top=4937 (株)Ｗａｑｏｏ +25.51
- Discovery union: 96 tickers

## Candidate Research Performed
- 9432 ＮＴＴ(株): Yahoo primary and Kabutan secondary matched on 2026-08-10 OHLCV.
- 9432 OHLCV: open 157.9 / high 159.9 / low 157.0 / close 159.9 / volume 227,940,200.
- 9432 tick_size: 0.1 based on JPX tick table and JPX listed-issues TOPIX Core30 category.
- 9432 order-plan preview: trigger 160.0, limit 160.1, shares 100, estimated capital 16,010 JPY, take-profit 168.1, stop-loss 155.1.

## Data Gaps
- Candidate Research is incomplete for the full Discovery union.
- TDnet/context checks are not complete for the full Discovery union.
- strategy.yaml still has unresolved screening keys set to null.

## Recommendation Basis
- 9432 may screen as ELIGIBLE in the narrow validated record set.
- The final recommendation remains DATA_UNAVAILABLE because candidate comparison across the Discovery union is incomplete.

## Audit Follow-up
- company_name and market are sourced from JPX_LISTED_COMPANY for trade-critical market_data fields.
- JPX_CALENDAR is recorded in sources.json for target/previous-trading-day evidence.
- JPX_TOPIX500 primary ticker-specific membership was not adopted; 9432 data_status is SINGLE_SOURCE_ONLY, so it must not be traded from this run.
