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
6. `config/source_matrix.yaml`を検証
7. PythonでTDnet調査期間を確定し、`research_window.json`を保存
8. 固定Discovery経路だけで`market_research.json`を作成
9. Discovery CandidatesだけをSource Matrix順に調査し、`sources.json`と`market_data.json`を保存
10. Pythonで市場データと出典台帳を検証し、`candidates.json`を生成
11. Pythonで`candidate_pipeline.json`と`performance.json`を生成
12. 確認済み情報だけで候補を比較
13. `recommendation.json`へ`TRADE`、`NO_TRADE`、または`DATA_UNAVAILABLE`を保存
14. `TRADE`の場合だけ人間に保有数・当日取引数を確認し、Risk Engineを実行して`risk_result.json`を保存
15. `NO_TRADE`または`DATA_UNAVAILABLE`の場合は人間入力なしでRisk Engineの`NOT_APPLICABLE`を保存
16. `recommendation.md`を生成
17. `trades/recommendations.csv`へ推奨履歴を追加
18. 作成ファイル、判断理由、データ欠落、Risk Engine結果を報告

## 人間が行う処理

1. 出典、対象営業日、株価、呼値を再確認
2. `recommendation.md`とSBI株アプリの実画面を照合
3. 注文するか最終判断
4. 注文する場合のみSBI株アプリへ手入力
5. 完全約定・当日決済済みの場合は`$record-daytrade-result`を明示実行
6. Pythonが提示する実績プレビューを確認し、正しい場合だけCSV追記を承認

`recommendations.csv`の注文提出・発動・未約定状態を更新する確定手順と、一部約定の記録方法は未決定です。`TODO.md`で決まるまでSkillは自動更新しません。

## 中止条件

次の場合は、値を補完せず対象銘柄を除外するか`DATA_UNAVAILABLE`にします。ただし、TDnet調査期間を確定できない場合は日次成果物を作らず作業停止します。

- 対象営業日を確認できない
- 必須市場データが欠落している
- 数値の出典を追跡できない
- 出典間の数値矛盾を解消できない
- Discovery Critical Sourceが欠落している
- 最新の過去runが壊れており、TDnet調査期間を確定できない（作業停止）
- OHLCVが`CONFLICT`または`SINGLE_SOURCE_ONLY`
- 決算・重要開示の確認が必要だが確認できない
- SBI画面の注文仕様を人間が確認できない

Risk Engineが`REJECTED`の場合は、提案値を変更せず`REJECTED`として記録します。
`REJECTED`を回避するために提案値を都合よく変更して再実行しません。
