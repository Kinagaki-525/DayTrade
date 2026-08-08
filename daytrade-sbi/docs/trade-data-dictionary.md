# 実取引データ定義

`trades/trades.csv` は実際に行った取引だけを記録するファイルです。注文候補、仮想取引、バックテスト結果は記録しません。確認できない値は推測せず空欄にします。

## 共通ルール

- 1行を1回の実取引として扱う
- 日付は `YYYY-MM-DD`、時刻は日本時間の `HH:MM` または `HH:MM:SS` を使う
- 金額と価格は通貨記号や桁区切りを付けず、10進数で記録する
- `ticker` は数値へ変換せず文字列として扱う
- `strategy_version` は取引条件の作成に使った `rules/strategy.yaml` の値を記録し、対応する `rules/versions/<strategy_version>.yaml` が存在することを確認する
- 未確認値を、エントリー価格や出口価格から逆算して補完しない

## カラム

| カラム | 内容 |
| --- | --- |
| `trade_date` | 取引日 |
| `ticker` | 銘柄コード |
| `company_name` | 取引時点で確認した会社名 |
| `previous_close` | 前営業日の終値 |
| `previous_high` | 前営業日の高値 |
| `tick_size` | 注文条件の計算に使用した呼値 |
| `entry_trigger` | 逆指値の発動価格候補として計算・入力した値 |
| `entry_limit` | 買い指値上限として計算・入力した値 |
| `entry_price` | 実際の買い約定価格 |
| `shares` | 実際の約定株数 |
| `take_profit_price` | 注文時に設定した利確価格 |
| `stop_loss_price` | 注文時に設定した損切り価格 |
| `exit_price` | 実際の売り約定価格 |
| `exit_reason` | `take_profit`、`stop_loss`、`end_of_day`、`other` のいずれか |
| `profit_loss_yen` | 確認できた実現損益。未確認の場合は空欄 |
| `entry_time` | 実際の買い約定時刻 |
| `exit_time` | 実際の売り約定時刻 |
| `strategy_version` | 適用した戦略版 |
| `notes` | 例外事項や、`other` の具体的理由など |

## 損益の扱い

`metrics.py` は `profit_loss_yen` を再計算せず、その記録値を集計します。手数料・税金を含むかどうかなど損益の採用基準を変更する場合は、既存データとの比較可能性を確認し、変更日と内容を文書化してください。
