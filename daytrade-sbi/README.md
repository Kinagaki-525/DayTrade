# daytrade-sbi v2

SBI証券、資金100,000円、国内株現物を前提に、Codexによる市場調査・候補比較と、Pythonによる価格計算・固定リスク検証を組み合わせて、翌営業日の手動注文案を管理するプロジェクトです。

このプロジェクトは利益を保証するものではありません。AIの評価と現在の売買ルールが利益を生むことは確認されていません。実取引データと取引しなかった日の判断を蓄積し、後から客観的に検証することが目的です。

OpenAI APIなどの外部LLM APIは使用しません。SBI証券へのログイン、画面操作、注文送信も自動化しません。

## 責任分界

| 担当 | 責任 |
| --- | --- |
| Codex | Web市場調査、出典保存、候補比較、`TRADE`、`NO_TRADE`、または`DATA_UNAVAILABLE`案の作成 |
| Python | 市場データ検証、固定条件スクリーニング、価格計算、Risk Engine、集計 |
| 人間 | 出典と注文案の最終確認、SBI株アプリへの手入力、実取引結果の記録 |

Codexが作成するものは注文候補です。注文判断・注文操作・訂正・取消は人間だけが行います。

## Codex Skillとサブエージェント

日次運用はリポジトリ直下の2つのCodex Skillから明示的に開始します。市場調査Sourceは[config/source_matrix.yaml](config/source_matrix.yaml)で固定し、実行ごとに任意のサイトへ代替しません。

- `$prepare-daytrade-plan`: 翌営業日の調査、候補比較、Risk Engine、手入力候補までをメインエージェントが統括
- `$record-daytrade-result`: 人間が確認した完全決済済み取引を検証し、確認後に記録・集計

`prepare`では、Web調査用の`market_researcher`と出典監査用の`source_auditor`だけを読み取り専用サブエージェントとして使用できます。日次成果物の書き込みと`TRADE`、`NO_TRADE`、または`DATA_UNAVAILABLE`の判断はメインエージェントが行います。`record`はサブエージェントを使いません。

## 固定条件

- 証券会社: SBI証券
- 対象: 日本株
- 口座・取引: 現物買のみ
- 運用資金: 100,000円
- 売買単位: 100株
- 同時保有: 最大1銘柄
- 1日最大1取引
- ナンピン禁止
- 翌日持ち越し禁止
- 空売り禁止
- 信用取引禁止
- 損切り発動基準の想定損失上限: 500円
- 条件に適合しなければ`NO_TRADE`、必要データが揃わなければ`DATA_UNAVAILABLE`

逆指値発動後の成行等では、スリッページにより実際の損失が500円を超える可能性があります。500円は実損失の保証ではありません。

## 検証中の戦略

許可する戦略は`previous_day_high_breakout`だけです。

1. 前日高値 + 1ティックをエントリー発動価格候補にする
2. 発動価格 + 1ティックを買い指値上限候補にする
3. 買い上限 × 100株が100,000円以内か確認する
4. 利確幅800円、損切り発動基準500円から出口価格を計算する
5. Risk Engineを通過した案だけを人間の確認対象にする
6. 人間が採用する場合も当日中に決済する

`+1ティック`、利確800円、損切り発動基準500円の有効性は未確認です。現在の初期検証用パラメータであり、固定の正解として扱いません。

利確・損切りの換算価格が呼値に一致しない場合は上方向の呼値へ丸めます。利確額を下回らず、想定損失を上限以内に収めるための計算方法であり、戦略の有効性を示すものではありません。

## 未決定項目

[TODO.md](TODO.md)で管理します。`screening`の流動性、価格、スプレッド、ギャップ、決算・開示除外などはrule objectとして管理し、未採用ruleは`enabled: false`かつ`threshold: null`です。CodexやPythonが値を推測して補完してはいけません。

## 処理フロー

```text
$prepare-daytrade-planを明示実行
  ↓
実行時のstrategy.yamlをスナップショット保存
  ↓
Source Matrixを検証
  ↓
PythonがTDnet調査期間を確定
  ↓
メインCodexが読み取り専用サブエージェントへWeb調査を委譲
  ↓ 固定Discovery経路をmarket_research.jsonへ保存
Source Matrix順にCandidate Research
  ↓ 出典付きでruns/YYYY-MM-DDへ保存
Ranking用の実際の売買代金をStage 1 PASS候補へ調査
  ↓ screen-marketより前にsources.jsonとmarket_data.jsonへ保存
Pythonが市場データを検証
  ↓
Pythonが固定条件でスクリーニング
  ↓
Pythonが候補パイプラインと性能計測を保存
  ↓
Hard Screening PASS候補と分析Featureを保存
  ↓ Selection未実装中はRanking COMPLETEでもTRADEへ進めずNO_TRADEまたはDATA_UNAVAILABLE
Python Risk Engineが注文案を再計算・検証
  ↓ PASS、REJECTED、または NOT_APPLICABLE
MarkdownのSBI手入力候補を生成
  ↓
人間が最終確認し、採用する場合だけ手入力
  ↓
$record-daytrade-resultを明示実行
  ↓ 人間が実績を確認
Pythonが検証・記録・集計
```

Risk EngineはAI案を修正しません。違反があれば`REJECTED`にします。`NO_TRADE`、`DATA_UNAVAILABLE`、`REJECTED`を都合よく`TRADE`へ変更しません。

## ディレクトリ構成

```text
DayTrade/
  .agents/skills/
    prepare-daytrade-plan/
    record-daytrade-result/
  .codex/
    config.toml
    agents/
      market-researcher.toml
      source-auditor.toml
  daytrade-sbi/
    README.md
    AGENTS.md
    TODO.md
    config/strategy.yaml
    prompts/nightly_research.md
    schemas/
    runs/YYYY-MM-DD/
    trades/
    src/
    tests/
    docs/
    rules/versions/
```

`rules/versions/v1.yaml`は旧50,000円設定を含むv1履歴です。現在の設定正本は[config/strategy.yaml](config/strategy.yaml)です。今回の資金上限100,000円への変更後も`strategy_version: v1`を維持し、設定差分は`config_sha256`で識別します。戦略バージョンの今後の命名・更新基準は未決定です。

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

> `$prepare-daytrade-plan`

詳細は[nightly-operation.md](docs/nightly-operation.md)を参照してください。Skillは[prompts/nightly_research.md](prompts/nightly_research.md)を既存の詳細手順として読みます。CodexはWeb調査で確認した事実と評価を分け、すべての重要数値にURL・取得日時・取引日を保存します。TDnetの調査対象期間はPythonの`resolve-research-window`で確定し、初回だけ設定済みの24時間初回補完期間を使います。

Pythonツールは外部Web接続なしで実行できます。直接実行する場合のコマンドは[prompts/nightly_research.md](prompts/nightly_research.md)を正本とします。

`TRADE`の場合、保有数と当日取引数は人間が確認した値を明示し、未確認時に0と仮定しません。`NO_TRADE`または`DATA_UNAVAILABLE`の場合は、これらを確認せずRisk Engine結果を`NOT_APPLICABLE`として保存します。
`candidates.json`、`recommendation.json`、`risk_result.json`には戦略バージョンと設定内容のSHA-256を保存し、異なる実行日のファイルや設定を混在させた場合は処理を停止します。

## 記録と集計

- `runs/YYYY-MM-DD/`: Web調査、出典、候補、Codex評価、Risk Engine結果
- `runs/YYYY-MM-DD/candidate_pipeline.json`: Discovery後の候補を消さずに候補単位の処理状態を保存
- `runs/YYYY-MM-DD/performance.json`: Sourceリクエスト数、ステージ件数、任意の工程別時間を性能評価用に保存
- `trades/recommendations.csv`: `NO_TRADE`、`DATA_UNAVAILABLE`、未約定を含む推奨履歴
- `trades/trades.csv`: 実際に約定した取引だけ
- `rules/versions/`: 過去の戦略設定

存在しない株価、注文、約定、損益を推測して埋めないでください。実取引とバックテストも同じファイルへ保存しません。

完全に約定し当日中に決済した取引を記録する場合は、Codexへ次のSkillを明示します。

> `$record-daytrade-result`

Skillは人間が確認した事実を`runs/YYYY-MM-DD/execution_result.json`へ保存し、CSV行のプレビュー後に明示確認を求めます。同じ日の異なる内容は自動追記せず競合として停止します。未約定、一部約定、未決済の記録方法は未決定のため、自動処理しません。直接実行するCLIとCSV仕様は[trade-data-dictionary.md](docs/trade-data-dictionary.md)を参照してください。

`win_rate`は損益を計算できる取引に占める利益が正の取引の割合です。損益0円は勝ちにも負けにも数えませんが分母には含めます。損益が未入力または非数値の行は推測せず、`uncalculable_trades`に数えます。
