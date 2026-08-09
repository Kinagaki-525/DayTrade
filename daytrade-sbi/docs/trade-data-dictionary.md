# 取引・推奨データ定義

市場調査、推奨、実取引を別のデータとして管理します。確認できない値は推測せず空欄または`null`にします。

## 保存先

| データ | 保存先 | 内容 |
| --- | --- | --- |
| 市場調査 | `runs/YYYY-MM-DD/` | 出典、確認データ、Codex評価、注文案、Risk Engine結果 |
| 推奨履歴 | `trades/recommendations.csv` | `TRADE`、`NO_TRADE`、`REJECTED`とその後の提出・発動・約定状況 |
| 実取引 | `trades/trades.csv` | 実際に約定した取引だけ |
| 実績入力 | `runs/YYYY-MM-DD/execution_result.json` | 人間が確認した完全決済済み取引をCSV追記前に検証する入力 |

バックテスト結果は上記へ保存しません。

## 共通ルール

- CSVは1行を1件として扱う
- 日付は`YYYY-MM-DD`、時刻は日本時間の`HH:MM`または`HH:MM:SS`を使う
- 金額と価格は通貨記号や桁区切りを付けず、10進数で記録する
- `ticker`は数値へ変換せず文字列として扱う
- `strategy_version`は注文条件作成時の`config/strategy.yaml`の値を記録する
- 未確認値を他の価格から逆算して補完しない

## trades.csv

`trades/trades.csv`には実際に約定した取引だけを記録します。注文候補、未約定、NO_TRADE、仮想取引は記録しません。

| カラム | 内容 |
| --- | --- |
| `trade_date` | 実取引日 |
| `ticker` | 銘柄コード |
| `company_name` | 取引時点で確認した会社名 |
| `strategy_type` | 適用した戦略。現在は`previous_day_high_breakout`のみ |
| `previous_close` | 注文条件作成時に確認した前営業日終値 |
| `previous_high` | 注文条件作成時に確認した前営業日高値 |
| `tick_size` | 注文条件の計算に使用した呼値 |
| `entry_trigger` | 設定した買い発動価格 |
| `entry_limit` | 設定した買い指値上限 |
| `planned_take_profit` | 設定した利確価格 |
| `planned_stop_loss` | 設定した損切り発動価格 |
| `actual_entry` | 実際の買い約定価格 |
| `actual_exit` | 実際の売り約定価格 |
| `shares` | 実際の約定株数 |
| `profit_loss_yen` | 確認できた実現損益。未確認の場合は空欄 |
| `exit_reason` | `take_profit`、`stop_loss`、`end_of_day`、`other`のいずれか |
| `entry_time` | 実際の買い約定時刻 |
| `exit_time` | 実際の売り約定時刻 |
| `strategy_version` | 注文条件作成時の戦略版 |
| `notes` | 例外事項や`other`の具体的理由など |

`src.metrics`は`profit_loss_yen`を再計算せず、記録値を集計します。手数料・税金を含むかどうかなど採用基準を変更する場合は、既存データとの比較可能性を確認し、変更日と内容を文書化します。

## execution_result.json

`execution_result.json`には、実際の買い・売りがともに約定し、当日中に決済済みであることを人間が確認した場合だけ入力します。計画値と会社名は`recommendation.json`、前日終値は`market_data.json`からPythonが取得するため、実績入力へ重複記載しません。

`profit_loss_yen`は人間が確認できた値だけを入力し、不明なら`null`とします。Pythonは約定価格から推測しません。`exit_reason`が`other`の場合は`notes`を必須とします。一部約定、未決済、未約定は記録方法が未決定のため、このSchemaではCSVへ記録できません。

```powershell
py -B -m src.cli validate-execution --execution runs/YYYY-MM-DD/execution_result.json --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --market-data runs/YYYY-MM-DD/market_data.json
py -B -m src.cli record-execution --execution runs/YYYY-MM-DD/execution_result.json --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --market-data runs/YYYY-MM-DD/market_data.json
py -B -m src.cli calculate-metrics
```

`record-execution`は同じ`trade_date`の完全一致行を再追記しません。同日の内容が既存行と異なる場合は、1日最大1取引の固定条件に基づき、既存行を自動修正せず競合として停止します。

## recommendations.csv

推奨時点で確認できない実行結果は空欄にします。`false`を推測入力しません。

| カラム | 内容 |
| --- | --- |
| `target_date` | 推奨対象の翌営業日 |
| `strategy_version` | 推奨生成時に使用した戦略バージョン |
| `config_sha256` | 推奨生成時に使用した設定内容のSHA-256 |
| `ticker` | `TRADE`候補の銘柄コード。`NO_TRADE`では空欄可 |
| `decision` | `TRADE`または`NO_TRADE`。Risk Engine拒否は`risk_result`へ記録 |
| `strategy_type` | 使用した戦略。`NO_TRADE`では空欄可 |
| `entry_trigger` | 提案した買い発動価格 |
| `entry_limit` | 提案した買い指値上限 |
| `take_profit` | 提案した利確価格 |
| `stop_loss` | 提案した損切り発動価格 |
| `risk_result` | `PASS`、`REJECTED`、`NOT_APPLICABLE` |
| `order_submitted` | 人間が注文提出を確認後、`true`または`false`を記録 |
| `entry_triggered` | 発動の事実を確認後に記録 |
| `entry_filled` | 約定の事実を確認後に記録 |
| `notes` | 見送り理由、拒否理由、未約定理由など |

推奨履歴の実行状態をいつ・どの手順で更新するかは、[TODO.md](../TODO.md)の未決定事項です。
