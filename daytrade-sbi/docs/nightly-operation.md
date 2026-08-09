# Nightly Operation

## 開始方法

毎晩、VS Code上のCodexへ次のように依頼します。

> `$prepare-daytrade-plan`

Skillは`prompts/nightly_research.md`を詳細手順として読みます。メインエージェントが全体を統括し、市場調査と出典監査だけを読み取り専用サブエージェントへ委譲します。

実行前にPython依存関係を導入し、テストが成功することを確認します。

```powershell
py -m pip install -r requirements-dev.txt
py -B -m pytest
```

## Codexが行う処理

1. `AGENTS.md`を確認
2. `config/strategy.yaml`を確認
3. `TODO.md`を確認
4. 翌営業日と前営業日を確認
5. `config/strategy.yaml`を対象日ディレクトリへスナップショット保存
6. 読み取り専用サブエージェントを必要に応じて使い、Webで市場データ・決算予定・適時開示・必要なニュースを調査
7. `sources.json`と`market_data.json`を保存
8. Pythonで市場データと出典台帳を検証し、`candidates.json`を生成
9. 確認済み情報だけで候補を比較
10. `recommendation.json`へ`TRADE`または`NO_TRADE`を保存
11. 人間に保有数・当日取引数を確認し、Risk Engineを実行して`risk_result.json`を保存
12. `recommendation.md`を生成
13. `trades/recommendations.csv`へ推奨履歴を追加
14. 作成ファイル、判断理由、データ欠落、Risk Engine結果を報告

## 人間が行う処理

1. 出典、対象営業日、株価、呼値を再確認
2. `recommendation.md`とSBI株アプリの実画面を照合
3. 注文するか最終判断
4. 注文する場合のみSBI株アプリへ手入力
5. 完全約定・当日決済済みの場合は`$record-daytrade-result`を明示実行
6. Pythonが提示する実績プレビューを確認し、正しい場合だけCSV追記を承認

`recommendations.csv`の注文提出・発動・未約定状態を更新する確定手順と、一部約定の記録方法は未決定です。`TODO.md`で決まるまでSkillは自動更新しません。

## 中止条件

次の場合は、値を補完せず対象銘柄を除外するか`NO_TRADE`にします。

- 対象営業日を確認できない
- 必須市場データが欠落している
- 数値の出典を追跡できない
- 出典間の数値矛盾を解消できない
- 決算・重要開示の確認が必要だが確認できない
- Risk Engineが`REJECTED`
- SBI画面の注文仕様を人間が確認できない

`REJECTED`を回避するために提案値を都合よく変更して再実行しません。
