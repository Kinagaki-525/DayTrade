# SBI証券 IFDOCO注文 入力整理メモ

このドキュメントは、SBI証券のIFDOCO注文へ入力する考え方と、`rules/strategy.yaml` の設定値との対応関係を整理するためのものです。

現時点ではSBI証券の画面仕様、項目名、入力順、制約を推測して補完しません。実際の画面で確認できた内容だけを追記してください。

## 対象外

- SBI証券へのログイン自動化
- SBI証券画面の操作自動化
- 証券口座への注文送信
- 呼値、株価、銘柄情報の外部API自動取得

## 現在確認している注文概念

IFDOCO注文は、買い注文が約定した後に、利確条件と損切り条件を組み合わせて決済するための条件注文として利用する想定です。

このプロジェクトでは、前日の夜に翌営業日の候補銘柄と注文条件を計算し、ユーザーがSBI証券の画面へ手動入力する前提です。

## strategy.yaml との対応

| `strategy.yaml` | 意味 | IFDOCO入力時の扱い |
| --- | --- | --- |
| `strategy_version` | 適用ルールの版 | 画面入力項目ではない。実取引記録へ同じ値を残す |
| `validation_status` | 戦略の検証状態 | 画面入力項目ではない。`unvalidated` は有効性未確認を示す |
| `capital` | 運用資金上限 | 100株購入時の概算代金がこの範囲内か確認する |
| `position_size` | 株数 | 原則100株として入力候補にする |
| `entry.strategy` | 検証するエントリー戦略 | 現在は前日高値ブレイク型 |
| `entry.trigger_ticks` | 前日高値から何ティック上で発動するか | `previous_high + tick_size * trigger_ticks` を発動価格候補にする |
| `entry.limit_offset_ticks` | 発動価格から何ティック上まで買いを許容するか | `entry_trigger + tick_size * limit_offset_ticks` を買い指値上限候補にする |
| `risk.max_loss_per_trade_yen` | 1回あたり最大損失目安 | 損切り条件がこの範囲を大きく超えないか確認する |
| `risk.max_trades_per_day` | 1日の最大取引回数 | 1日1取引を超えないようにする |
| `risk.max_positions` | 同時保有数 | 1銘柄のみ保有する |
| `risk.averaging_down` | ナンピン可否 | `false` のため追加買いしない |
| `risk.overnight_hold` | 持ち越し可否 | `false` のため当日中に決済する |
| `exit.take_profit_yen` | 1取引あたりの利確幅候補 | 100株なら1株あたり `take_profit_yen / 100` 円を買値に加える |
| `exit.stop_loss_yen` | 1取引あたりの損切り幅候補 | 100株なら1株あたり `stop_loss_yen / 100` 円を買値から引く |
| `exit.close_by_end_of_day` | 当日中決済 | 大引け前に未決済なら時間切れ決済候補として扱う |
| `validation.require_affordable_position` | 購入可能性チェック | 100株の概算購入額が資金内であることを必須条件にする |

## 手動入力前チェック

- 前日高値は推測せず、確認済みの値だけを使う
- 呼値は銘柄・価格帯に応じて確認済みの値だけを使う
- `entry_trigger`、`entry_limit`、`take_profit_price`、`stop_loss_price` を計算する
- 利確・損切りの換算価格が呼値に一致しない場合は、どちらも上方向の呼値へ丸めた候補値であることを確認する
- `entry_limit * position_size <= capital` であることを確認する
- その日の取引候補が1銘柄だけであることを確認する
- ナンピン注文や翌日持ち越し前提の注文を入れない
- 実際に発注した場合は、結果を `trades/trades.csv` に記録する
- `strategy_version` は注文条件を作成した時点の値を記録する

計算結果は手動入力の候補であり、注文の成立、有効性、利益を保証しません。画面へ入力する前に、SBI証券で実際に表示される項目、呼値、注文期限、概算代金をユーザーが確認します。

## 未確認事項

以下はSBI証券の画面で実際に確認してから追記します。

- IFDOCO注文画面の正式な項目名
- 逆指値、指値、成行などの画面上の選択肢
- 入力可能な価格単位とエラー表示
- 当日限り指定の具体的な入力方法
- 注文訂正・取消の操作手順
