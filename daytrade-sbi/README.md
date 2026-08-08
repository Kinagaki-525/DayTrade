# daytrade-sbi

SBI証券、資金50,000円、国内株現物を前提に、デイトレードの売買ルールと実取引結果を記録・検証するためのシンプルなPythonプロジェクトです。

このプロジェクトは利益を保証するものではありません。現在の売買ルールの有効性は未確認であり、実取引データを蓄積して後から客観的に検証するために管理します。

## 前提

- 証券会社: SBI証券
- 対象: 日本株
- 取引: 現物のみ
- 運用資金: 50,000円
- 売買単位: 原則100株
- 同時保有: 1銘柄
- 1日最大1取引
- ナンピン禁止
- 翌日への持ち越し禁止
- 1回の最大損失目安: 500円
- 条件を満たさない日は取引しない
- 前日の夜に翌日の注文条件を決定する
- 日中はSBI証券のIFDOCO注文による条件注文を利用する想定

証券会社への注文送信、SBI証券への自動ログイン、画面操作の自動化は実装しません。

## 現在の検証戦略

最初の検証対象は「前日高値ブレイク型」です。

基本方針:

1. 前日の高値を取得する
2. 前日高値 + 1ティックをエントリー発動価格候補とする
3. 発動価格 + 1ティックを買い指値上限候補とする
4. 100株購入した場合でも総購入額が50,000円以内であることを確認する
5. 買い約定後に利確・損切り条件を設定する
6. 当日中に決済する

`trigger_ticks`、`limit_offset_ticks`、`take_profit_yen`、`stop_loss_yen` は検証対象です。実績なしに有効な戦略だとは扱いません。

利確額・損切り額を1株あたりの価格へ換算した結果が呼値に一致しない場合、価格は上方向の呼値へ丸めます。利確では設定額を下回らず、損切りでは設定した最大損失を超えないための計算上の扱いです。この丸め方自体も、戦略の有効性を示すものではありません。

`strategy_version` は、実取引に適用したルールを後から特定するための識別子です。現行設定と同じ内容を `rules/versions/<strategy_version>.yaml` に保存します。承認済みの売買ルールを変更するときは新しい版を作成し、既存の版ファイルと過去の取引行に記録済みの版は書き換えません。`validation_status: unvalidated` は、現時点で有効性が確認されていないことを明示します。

## ディレクトリ構成

```text
daytrade-sbi/
  README.md
  AGENTS.md
  .gitignore
  pytest.ini
  requirements.txt
  requirements-dev.txt
  rules/
    strategy.yaml
    versions/
      v1.yaml
  trades/
    trades.csv
  src/
    __init__.py
    strategy.py
    metrics.py
  tests/
    test_strategy.py
    test_metrics.py
    fixtures/
  docs/
    trade-data-dictionary.md
    sbi-order-guide.md
```

## 情報の正本

- 売買ルールの正本: `rules/strategy.yaml`
- 現行・過去ルールの不変スナップショット: `rules/versions/<strategy_version>.yaml`
- 実取引事実の正本: `trades/trades.csv`
- CSV列の定義: `docs/trade-data-dictionary.md`
- SBI証券への手動入力の整理: `docs/sbi-order-guide.md`

計算された注文候補は実取引結果ではありません。AIやプログラムが算出した値を、約定値や損益として `trades.csv` に転記しないでください。

## 基本的な使い方

売買ルールは [rules/strategy.yaml](rules/strategy.yaml) で管理します。実取引の結果は [trades/trades.csv](trades/trades.csv) に追記します。存在しない株価や取引結果を推測して埋めないでください。

Python 3.10以上を使用します。計算機能だけを利用する場合の依存関係を準備します。

```powershell
py -m pip install -r requirements.txt
```

開発・テスト用依存関係も含めて準備する場合:

```powershell
py -m pip install -r requirements-dev.txt
```

注文条件の計算例:

```python
from src.strategy import build_order_plan

plan = build_order_plan(previous_high="400", tick_size="1")
print(plan)
```

実取引結果の集計例:

```python
from src.metrics import calculate_metrics_from_csv

metrics = calculate_metrics_from_csv("trades/trades.csv")
print(metrics)
```

`win_rate` は、損益を計算できる取引に占める利益が正の取引の割合です。損益0円は勝ちにも負けにも数えませんが、分母には含めます。損益が未入力または非数値の行は推測せず、`uncalculable_trades` に数えます。

テスト実行:

```powershell
py -m pytest
```

## 注意

- バックテスト結果と実取引結果は明確に区別してください。
- `trades.csv` は実際に行った取引だけを記録してください。
- 取引データを追記するときは、使用した `strategy_version` を必ず記録してください。
- リスク管理ルールを緩和する変更は、事前に理由と影響を確認してから行ってください。
