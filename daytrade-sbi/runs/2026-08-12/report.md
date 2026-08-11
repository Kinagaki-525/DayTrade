# 日次デイトレ計画レポート

対象日: 2026-08-12
前営業日: 2026-08-10
情報カットオフ: 2026-08-10T20:00:00+09:00
判定: NO_TRADE
Risk Engine: NOT_APPLICABLE
調査パイプライン完了: はい

## 調査パイプライン概要
- Discovery候補: 96件
- Research完了: 96件
- Research未完了: 0件
- DATA_UNAVAILABLE: 0件
- ELIGIBLE: 35件
- REJECTED: 61件

## 判断理由
- candidate_pipelineは完了し、35件がELIGIBLEだが、TODO.md P0で最終的に1銘柄へ絞る選定・順位付けルールが未決定。
- 実取引前に毎晩機械的に1銘柄まで絞れる条件が必要なため、任意の銘柄選定は行わない。
- Discovery候補96件のうち61件はSource根拠付きStage 1 reject（資金超過59件、ETF対象外2件）。

## 欠落・矛盾
- 欠落・矛盾は記録されていない。

## 出典
- 出典取得試行: 77件
- 採用出典: 883件
- 主な出典不足: なし

参照URL:
- https://finance.yahoo.co.jp/stocks/ranking/volume?market=all
- https://finance.yahoo.co.jp/stocks/ranking/up?market=all
- https://www.jpx.co.jp/equities/trading/domestic/03.html
- https://www.jpx.co.jp/equities/trading/domestic/07.html
- https://www.jpx.co.jp/markets/indices/topix/

SBI確認事項:
- TRADEかつRisk Engine PASSの場合だけ、SBI証券の実画面で注文内容を人間が確認する。
- NO_TRADE、DATA_UNAVAILABLE、REJECTEDの場合は注文を作成しない。
