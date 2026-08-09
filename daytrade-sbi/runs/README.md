# Nightly Run Records

このディレクトリには、Codexが実際に調査した対象日ごとの記録だけを保存します。例示目的の架空データは保存しません。

```text
runs/YYYY-MM-DD/
  strategy_snapshot.yaml
  sources.json
  market_data.json
  market_validation.json
  candidates.json
  research.md
  recommendation.json
  recommendation.md
  risk_result.json
```

ディレクトリ名は翌営業日です。各市場数値には出典URL、取得日時、データの取引日を残します。実取引結果はこのディレクトリではなく`trades/trades.csv`に記録します。

`strategy_snapshot.yaml`はその日の計算に使った設定の保存版です。`candidates.json`、`recommendation.json`、`risk_result.json`の`config_sha256`が一致しない成果物は組み合わせません。
