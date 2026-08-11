# 翌営業日 注文計画

対象日: 2026-08-12
戦略バージョン: v1
設定SHA-256: d3631bc50e9dc52d61fe562d32ec8574d2c07a1cbf1128cd063696e293e140df
情報カットオフ: 2026-08-10T20:00:00+09:00
cutoff後情報: 標準調査対象外。未調査の情報を確認済みとして扱わない。

判定
NO_TRADE

理由:
- candidate_pipelineは完了し、35件がELIGIBLEだが、TODO.md P0で最終的に1銘柄へ絞る選定・順位付けルールが未決定。
- 実取引前に毎晩機械的に1銘柄まで絞れる条件が必要なため、任意の銘柄選定は行わない。
- Discovery候補96件のうち61件はSource根拠付きStage 1 reject（資金超過59件、ETF対象外2件）。

候補パイプライン概要:
- Discovery候補: 96件
- Research完了: 96件
- Research未完了: 0件
- DATA_UNAVAILABLE: 0件
- ELIGIBLE: 35件
- REJECTED: 61件
- 調査パイプライン完了: はい
- Stage 2対象: 35件
- Stage 2未完了: 0件

主なSource状態:
- YAHOO_JP_VOLUME_RANKING: FOUND https://finance.yahoo.co.jp/stocks/ranking/volume?market=all
- YAHOO_JP_GAIN_RANKING: FOUND https://finance.yahoo.co.jp/stocks/ranking/up?market=all
- JPX_CALENDAR: FOUND https://www.jpx.co.jp/corporate/about-jpx/calendar/ (2026-08-11 is a JPX holiday; target is 2026-08-12.)
- JPX_TRADING_UNIT: FOUND https://www.jpx.co.jp/equities/trading/domestic/03.html
- JPX_TICK_SIZE: FOUND https://www.jpx.co.jp/equities/trading/domestic/07.html
- JPX_TOPIX500: FOUND https://www.jpx.co.jp/markets/indices/topix/ (JPX TOPIX page linked constituent CSV was saved and parsed.)

参照データ:
- https://finance.yahoo.co.jp/stocks/ranking/volume?market=all
- https://finance.yahoo.co.jp/stocks/ranking/up?market=all
- https://www.jpx.co.jp/equities/trading/domestic/03.html
- https://www.jpx.co.jp/equities/trading/domestic/07.html
- https://www.jpx.co.jp/markets/indices/topix/

注意事項:
- NO_TRADEは正常な結果であり、注文を作成しない。
