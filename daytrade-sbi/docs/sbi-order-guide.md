# SBI証券 IFDOCO注文 入力整理メモ

このドキュメントは、Risk Engineを通過した手動注文候補と、`config/strategy.yaml`の設定値との対応を整理するためのものです。

現時点ではSBI証券の画面仕様、正式項目名、入力順、制約を推測して補完しません。実際の画面で人間が確認できた内容だけを追記します。

## 対象外

- SBI証券へのログイン自動化
- SBI証券画面やブラウザの操作自動化
- 証券口座への注文送信
- Pythonからの市場データAPI・LLM API呼び出し

## 注文候補の作成

Codexは出典付き市場調査から1銘柄の`TRADE`案、`NO_TRADE`、または`DATA_UNAVAILABLE`を作成します。Python Risk Engineが`PASS`した場合だけ、`runs/YYYY-MM-DD/recommendation.md`を手入力候補として人間へ提示します。

`recommendation.md`は注文済みを意味しません。人間がSBI株アプリの実画面と照合し、採用するかを最終判断します。

## config/strategy.yamlとの対応

| 設定 | 意味 | 手動確認時の扱い |
| --- | --- | --- |
| `strategy_version` | 適用ルールの版 | SBI入力項目ではない。推奨・実取引記録へ残す |
| `validation_status` | 戦略の検証状態 | `unvalidated`は有効性未確認を示す |
| `account.account_type` | 口座・取引区分 | `cash`のため現物買候補のみ |
| `capital.total_yen` | 運用資金上限 | 必要資金が50,000円以内かRisk Engineが検証 |
| `capital.position_size` | 株数 | 100株でなければRisk Engineが拒否 |
| `previous_day_high_breakout.trigger_ticks` | 前日高値からの発動ティック数 | 発動価格をPythonが再計算 |
| `previous_day_high_breakout.entry_limit_offset_ticks` | 発動価格から買い上限までのティック数 | 買い上限をPythonが再計算 |
| `risk.max_loss_per_trade_yen` | 損切り発動基準の想定損失上限 | 想定損失が500円を超える案を拒否 |
| `risk.max_positions` | 同時保有上限 | 1ポジション以上保有中なら新規案を拒否 |
| `risk.max_trades_per_day` | 1日の取引上限 | 1取引済みなら新規案を拒否 |
| `risk.averaging_down` | ナンピン可否 | `false`のため追加買いしない |
| `risk.overnight_hold` | 持ち越し可否 | `false`のため当日中に決済する |
| `risk.short_selling` | 空売り可否 | `false`のため対象外 |
| `risk.margin_trading` | 信用取引可否 | `false`のため対象外 |
| `exit.take_profit_yen` | 1取引あたりの利確幅候補 | 100株で1株あたりの価格へ換算 |
| `screening.*` | 銘柄抽出条件 | `null`項目は未決定。入力値や判断基準へ変換しない |

## Risk Engine確認項目

- 市場データと数値ごとの出典が検証済み
- 前日高値と呼値が市場データに一致
- 発動価格、買い上限、利確、損切りが設定からの再計算値に一致
- 100株
- 買い上限 × 100株が50,000円以内
- 発動価格が買い上限以下
- 損切りが買い上限未満
- 利確が買い上限より大きい
- 想定損失が500円以内
- 各注文価格が呼値に整合
- 最大保有数と当日取引数を超えない

違反した注文案は値を修正せず`REJECTED`にします。

## 人間による手動入力前チェック

- 対象日、銘柄、株数、現物買であることを再確認する
- 前日高値と呼値を出典およびSBI画面で再確認する
- `recommendation.md`の発動価格、買い上限、利確、損切りを画面と照合する
- IFDOCO、逆指値、指値、成行候補、執行条件、注文期間の実際の画面仕様を確認する
- その日の注文・保有状況を確認する
- 注文する場合だけ人間が入力・送信する

逆指値発動後に成行を利用する場合、スリッページにより実際の損失が想定500円を超える可能性があります。

## 未確認事項

- IFDOCO注文画面の正式な項目名
- 逆指値、指値、成行など画面上の選択肢
- 入力可能な価格単位とエラー表示
- 当日限り指定と引け前決済の具体的な方法
- 注文訂正・取消の操作手順

確認できるまで推測で補完せず、[TODO.md](../TODO.md)で管理します。
