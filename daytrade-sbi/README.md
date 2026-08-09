# daytrade-sbi v2

SBI証券、資金50,000円、国内株現物を前提に、Codexによる市場調査・候補比較と、Pythonによる価格計算・固定リスク検証を組み合わせて、翌営業日の手動注文案を管理するプロジェクトです。

このプロジェクトは利益を保証するものではありません。AIの評価と現在の売買ルールが利益を生むことは確認されていません。実取引データと取引しなかった日の判断を蓄積し、後から客観的に検証することが目的です。

OpenAI APIなどの外部LLM APIは使用しません。SBI証券へのログイン、画面操作、注文送信も自動化しません。

## 責任分界

| 担当 | 責任 |
| --- | --- |
| Codex | Web市場調査、出典保存、候補比較、`TRADE`または`NO_TRADE`案の作成 |
| Python | 市場データ検証、固定条件スクリーニング、価格計算、Risk Engine、集計 |
| 人間 | 出典と注文案の最終確認、SBI株アプリへの手入力、実取引結果の記録 |

Codexが作成するものは注文候補です。注文判断・注文操作・訂正・取消は人間だけが行います。

## 固定条件

- 証券会社: SBI証券
- 対象: 日本株
- 口座・取引: 現物買のみ
- 運用資金: 50,000円
- 売買単位: 100株
- 同時保有: 最大1銘柄
- 1日最大1取引
- ナンピン禁止
- 翌日持ち越し禁止
- 空売り禁止
- 信用取引禁止
- 損切り発動基準の想定損失上限: 500円
- 条件に適合しなければ`NO_TRADE`

逆指値発動後の成行等では、スリッページにより実際の損失が500円を超える可能性があります。500円は実損失の保証ではありません。

## 検証中の戦略

許可する戦略は`previous_day_high_breakout`だけです。

1. 前日高値 + 1ティックをエントリー発動価格候補にする
2. 発動価格 + 1ティックを買い指値上限候補にする
3. 買い上限 × 100株が50,000円以内か確認する
4. 利確幅800円、損切り発動基準500円から出口価格を計算する
5. Risk Engineを通過した案だけを人間の確認対象にする
6. 人間が採用する場合も当日中に決済する

`+1ティック`、利確800円、損切り発動基準500円の有効性は未確認です。現在の初期検証用パラメータであり、固定の正解として扱いません。

利確・損切りの換算価格が呼値に一致しない場合は上方向の呼値へ丸めます。利確額を下回らず、想定損失を上限以内に収めるための計算方法であり、戦略の有効性を示すものではありません。

## 未決定項目

[TODO.md](TODO.md)で管理します。`screening`の流動性、価格、スプレッド、ギャップ、決算・開示除外などの閾値はすべて`null`です。CodexやPythonが値を推測して補完してはいけません。

## 処理フロー

```text
CodexがWeb調査
  ↓ 出典付きでruns/YYYY-MM-DDへ保存
実行時のstrategy.yamlをスナップショット保存
  ↓
Pythonが市場データを検証
  ↓
Pythonが固定条件でスクリーニング
  ↓
CodexがELIGIBLE銘柄を比較
  ↓ TRADE 1銘柄 または NO_TRADE
Python Risk Engineが注文案を再計算・検証
  ↓ PASS または REJECTED
MarkdownのSBI手入力候補を生成
  ↓
人間が最終確認し、採用する場合だけ手入力
  ↓
人間が実績を記録し、Pythonが集計
```

Risk EngineはAI案を修正しません。違反があれば`REJECTED`にします。`NO_TRADE`と`REJECTED`を都合よく`TRADE`へ変更しません。

## ディレクトリ構成

```text
daytrade-sbi/
  README.md
  AGENTS.md
  TODO.md
  .gitattributes
  config/
    strategy.yaml
  prompts/
    nightly_research.md
  schemas/
    market_data.schema.json
    market_validation.schema.json
    sources.schema.json
    candidates.schema.json
    recommendation.schema.json
    risk_result.schema.json
  runs/
    README.md
    YYYY-MM-DD/
  trades/
    trades.csv
    recommendations.csv
  src/
    config.py
    contracts.py
    file_io.py
    cli.py
    recommendations.py
    market/
    screening/
    strategy/
    risk/
    reports/
    metrics/
  tests/
  docs/
    architecture.md
    nightly-operation.md
    sbi-order-guide.md
    trade-data-dictionary.md
  rules/versions/
    v1.yaml
```

`rules/versions/v1.yaml`はv1設定の不変履歴です。v2の設定正本は[config/strategy.yaml](config/strategy.yaml)です。`strategy_version: v1`は売買パラメータを引き継いでいることを示し、v2はプロジェクト構成の世代を示します。戦略バージョンの今後の命名・更新基準は未決定です。

## セットアップ

Python 3.10以上を使用します。

```powershell
py -m pip install -r requirements-dev.txt
py -B -m pytest
```

実行時依存関係だけを導入する場合:

```powershell
py -m pip install -r requirements.txt
```

## 毎晩の使い方

VS Code上のCodexへ次のように依頼します。

> `prompts/nightly_research.md`に従って翌営業日の調査を実行してください。

詳細は[nightly-operation.md](docs/nightly-operation.md)を参照してください。CodexはWeb調査で確認した事実と評価を分け、すべての重要数値にURL・取得日時・取引日を保存します。

Pythonツールは外部Web接続なしで実行できます。

```powershell
py -B -m src.cli snapshot-config --output runs/YYYY-MM-DD/strategy_snapshot.yaml
py -B -m src.cli validate-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --output runs/YYYY-MM-DD/market_validation.json
py -B -m src.cli screen-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/candidates.json
py -B -m src.cli risk-check --recommendation runs/YYYY-MM-DD/recommendation.json --candidates runs/YYYY-MM-DD/candidates.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/risk_result.json --current-positions <確認済み保有数> --trades-today <確認済み当日取引数>
py -B -m src.cli render-report --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --output runs/YYYY-MM-DD/recommendation.md
```

保有数と当日取引数は人間が確認した値を明示し、未確認時に0と仮定しません。
`candidates.json`、`recommendation.json`、`risk_result.json`には戦略バージョンと設定内容のSHA-256を保存し、異なる実行日のファイルや設定を混在させた場合は処理を停止します。

## 記録と集計

- `runs/YYYY-MM-DD/`: Web調査、出典、候補、Codex評価、Risk Engine結果
- `trades/recommendations.csv`: `NO_TRADE`や未約定を含む推奨履歴
- `trades/trades.csv`: 実際に約定した取引だけ
- `rules/versions/`: 過去の戦略設定

存在しない株価、注文、約定、損益を推測して埋めないでください。実取引とバックテストも同じファイルへ保存しません。

```python
from src.metrics import calculate_metrics_from_csv

metrics = calculate_metrics_from_csv("trades/trades.csv")
print(metrics)
```

`win_rate`は損益を計算できる取引に占める利益が正の取引の割合です。損益0円は勝ちにも負けにも数えませんが分母には含めます。損益が未入力または非数値の行は推測せず、`uncalculable_trades`に数えます。
