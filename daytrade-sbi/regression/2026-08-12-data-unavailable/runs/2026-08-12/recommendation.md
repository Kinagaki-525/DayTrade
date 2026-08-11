# 翌営業日注文案

対象日: 2026-08-12
戦略バージョン: v1
設定SHA-256: 5c7f803bf4da07155165b3b1f9075e577746280aa886f2b3d1348643c0b38d82
情報カットオフ: 2026-08-10T20:00:00+09:00
cutoff後情報: 標準調査対象外（未調査の情報を「0件確認済み」とは扱わない）

判定:
DATA_UNAVAILABLE

理由:
- Discovery found 96 unique tickers, but Candidate Research is missing for 95 tickers in the saved artifacts.
- The only screened ticker, 9432, is DATA_UNAVAILABLE because JPX_TOPIX500 primary ticker-specific membership was not adopted; tick-size classification remains SINGLE_SOURCE_ONLY.
- Context checks such as TDnet are not complete for the full Discovery union, and strategy.yaml still has unresolved screening keys set to null.

候補パイプライン概要:
- Discovery候補: 96件
- Research完了: 0件
- Research未完了: 95件
- DATA_UNAVAILABLE: 1件
- ELIGIBLE: 0件
- REJECTED: 0件

主なSource状態:
- YAHOO_JP_VOLUME_RANKING: FOUND https://finance.yahoo.co.jp/stocks/ranking/volume?market=all (Yahoo volume ranking rows 1-50, page update time 2026/08/10 18:40.)
- YAHOO_JP_GAIN_RANKING: FOUND https://finance.yahoo.co.jp/stocks/ranking/up?market=all (Yahoo gain ranking rows 1-50, page update time 2026/08/10 18:40.)
- YAHOO_JP_HISTORY: FOUND https://finance.yahoo.co.jp/quote/9432.T/history (Used 2026/8/10 history row.)
- KABUTAN_HISTORY: FOUND https://kabutan.jp/stock/kabuka?code=9432 (Used 2026-08-10 daily row from Source Matrix Kabutan URL. Values match Yahoo.)
- JPX_LISTED_COMPANY: FOUND https://www2.jpx.co.jp/tseHpFront/StockSearch.do?method=&topSearchStr=9432 (JPX listed company search evidence page saved.)
- JPX_LISTED_COMPANY: FOUND https://www.jpx.co.jp/equities/trading/domestic/03.html (JPX trading unit page states domestic stocks trade in units of 100 shares.)
- JPX_LISTED_COMPANY_AUDIT: FOUND https://www.jpx.co.jp/markets/statistics-equities/misc/01.html (JPX listed issues July 2026 Excel checked for 9432.)
- JPX_TICK_SIZE: FOUND https://www.jpx.co.jp/equities/trading/domestic/07.html (JPX tick size table plus JPX listed issues TOPIX Core30 category imply 0.1 yen tick for 9432 at this price.)
- JPX_CALENDAR: FOUND https://www.jpx.co.jp/corporate/about-jpx/calendar/ (JPX calendar confirms 2026-08-11 is a holiday; run uses 2026-08-12 target and 2026-08-10 previous trading day.)
- JPX_TOPIX500: STALE https://www.jpx.co.jp/markets/indices/topix/ (Current ticker-specific JPX_TOPIX500 primary evidence was not adopted. Tick-size classification for 9432 relies only on JPX listed-issues audit field TOPIX Core30, so trade-critical tick_size remains SINGLE_SOURCE_ONLY.)

参照データ:
- https://finance.yahoo.co.jp/quote/9432.T/history
- https://kabutan.jp/stock/kabuka?code=9432
- https://www.jpx.co.jp/equities/trading/domestic/03.html
- https://www.jpx.co.jp/markets/statistics-equities/misc/01.html
- https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls
- https://www.jpx.co.jp/equities/trading/domestic/07.html
- https://www2.jpx.co.jp/tseHpFront/StockSearch.do?method=&topSearchStr=9432
- https://www.jpx.co.jp/corporate/about-jpx/calendar/

注意事項:
- 必要な市場データが揃わず、取引判断まで到達していない。
