# Nightly Run Records

このディレクトリには、Codexが実際に調査した対象日ごとの記録だけを保存します。例示目的の架空データは保存しません。

```text
runs/YYYY-MM-DD/
  strategy_snapshot.yaml
  research_window.json
  market_research.json
  market_research_validation.json
  sources.json
  market_data.json
  market_validation.json
  candidates.json
  candidate_pipeline.json
  performance.json
  research.md
  recommendation.json
  recommendation.md
  risk_result.json
  report.md
  official_ohlcv_audit.json
  execution_result.json  # 完全決済済み取引がある場合だけ
```

ディレクトリ名は翌営業日です。各市場数値には出典URL、取得日時、データの取引日を残します。`execution_result.json`は人間が確認した実績をCSV追記前に検証する入力記録で、実取引ログの正本は`trades/trades.csv`です。

`strategy_snapshot.yaml`はその日の計算に使った設定の保存版です。`candidates.json`、`recommendation.json`、`risk_result.json`の`config_sha256`が一致しない成果物は組み合わせません。

`research_window.json`はPythonが確定したTDnet調査対象期間です。初回実行は`FIRST_RUN`として設定済みの24時間初回補完期間を使い、成功後の次回以降は直近の有効な`market_research.json`の`research_cutoff`から通常の調査期間へ移行します。
