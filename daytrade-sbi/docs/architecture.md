# v2 アーキテクチャ

## 責任分界

```text
Codex: Web調査・出典保存・候補比較・TRADE/NO_TRADE案
  ↓
Python: データ検証・固定条件スクリーニング・価格計算・Risk Engine
  ↓
人間: 出典と注文案の最終確認・SBI株アプリへの手入力・実績記録
```

Codexの評価は入力データに基づく候補比較であり、利益予測や発注権限ではありません。PythonはAIの代わりに銘柄を評価せず、設定された数値と構造だけを検証します。人間だけが実際の注文を決定・入力します。

## コンポーネント

| コンポーネント | 責任 |
| --- | --- |
| `config/strategy.yaml` | 固定リスク条件、検証中パラメータ、未決定スクリーニング値 |
| `prompts/nightly_research.md` | Codexが毎晩従う調査・保存・検証手順 |
| `src/market/` | 市場データと出典のモデル、完全性・整合性検証 |
| `src/contracts.py` | JSON Schemaの実行時検証と日次成果物間の紐付け検証 |
| `src/screening/` | 資金条件を適用。未決定条件は未評価として残し、承認済み実装なしの値は拒否 |
| `src/strategy/` | 前日高値ブレイクの価格計算 |
| `src/risk/` | AI案から独立した固定リスク検証。値は修正しない |
| `src/reports/` | PASS・REJECTED・NO_TRADEに応じた手動確認レポート |
| `src/recommendations.py` | 取引しなかった日を含む推奨履歴の記録 |
| `src/metrics/` | 実取引結果の集計 |
| `schemas/` | nightly実行で保存するJSONの構造契約 |

`sources.json`は出典台帳の正本です。`market_data.json`へ埋め込んだ数値出典や`recommendation.json`の参照URLが台帳に存在しない場合、後続処理へ進みません。

日次ディレクトリには`strategy_snapshot.yaml`を保存します。候補・推奨・Risk Engine結果へ同じ`strategy_version`と設定内容のSHA-256を引き継ぎ、別設定で作られた成果物の混在を拒否します。

## 状態

- `ELIGIBLE`: 市場データ検証と設定済み固定条件を通過。取引推奨ではない。
- `TRADE`: Codexが比較結果として作った1銘柄の注文案。Risk Engine通過前は採用不可。
- `NO_TRADE`: 適切な候補がない正常な結果。注文を作らない。
- `PASS`: 注文案が固定リスク条件を通過。
- `REJECTED`: 注文案またはデータが固定条件に違反。値を自動修正しない。
- `NOT_APPLICABLE`: `NO_TRADE`のため注文リスク評価対象がない。

## データ境界

`runs/`の市場データ・評価・注文案と、`trades/trades.csv`の実取引事実を混在させません。バックテストを追加する場合も別ディレクトリで管理します。

外部AI API、証券API、SBIログイン、ブラウザによる発注操作は実装対象外です。
