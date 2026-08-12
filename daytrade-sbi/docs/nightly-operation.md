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
8. Yahoo!ファイナンス2ランキングだけで`market_research.json`のDiscovery Candidateを作成
9. `init-candidate-research`で全Discovery Candidateの`candidate_research[]`を初期化
10. Stage 1の確認済み事実を`sources.json`と`market_data.json`へ保存し、`apply-stage1`で売買単位・資金条件を分類
11. `plan-stage2-batches`でStage 2調査対象をbatch化し、必要なCandidate Researchを実施・merge
12. Ranking用Actual Turnover Research: Stage 2 Candidate Researchのmerge完了後、**`screen-market`を実行する前に**、Stage 1 `PASS`候補全件について前営業日の実際の売買代金を`YAHOO_JP_QUOTE`で調査する。`YAHOO_JP_QUOTE`のURL templateは`.T`（東証）固定でDiscoveryは`ALL_MARKETS`のため、検証済みSource Evidenceから東証上場と確認できる候補だけを対象とする。確認できない場合はSuffixを推測せず、`FOUND`として記録せず、その候補をRankingへ進めない。`FOUND`の場合は`sources.json`のSource Attempt・Source Recordと`market_data.json`の`turnover`へ同じCanonical値を保存する。失敗した場合は失敗Source Attemptをそのまま保存し、`market_data.json`の`turnover`を`null`にして古い`FOUND`値を残さない
13. Pythonで市場データと出典台帳を検証し、`official_ohlcv_audit.json`と、Hard Screening結果・Rule評価・分析Featureを含む`candidates.json`を生成
14. Pythonで`candidate_pipeline.json`、`performance.json`、`research.md`を生成
15. `candidate_pipeline.summary.pipeline_complete=true`、`screening_complete=true`を確認する
16. Event Research: `status=ELIGIBLE`かつ`screening_status=PASS`の候補だけを対象に`init-event-research`を実行し、Web調査で得たSource Attempt・Evidenceを`sources.json`（Source Attempt・Evidenceの正本）へ保存する。それらへの参照である`selected_attempt_ids`、`news_classifications`、`event_gate_as_of`を`event_research.json`へ保存して完成させる。PASS・REJECT・DATA_UNAVAILABLEの判定はEvent Researchでは行わない
17. Event Research Validation: `validate-event-research`を実行し、`event_research.json`の整合性を検証する
18. Event Gate: `build-event-gate`を実行し、決定論的Pythonロジックで`event_gate.json`を生成する
19. Event Gate Validation: `event_gate.json`の`event_gate_complete=true`を確認する。`false`の場合は以降へ進まず、Rankingを開始しない
20. Event Gateの結果に応じて次のとおりFail Closedで分岐する。新しいReason CodeやSchemaは追加しない
    - `ranking_ready=false`の場合、Rankingを開始しない
    - Event Gate候補に`gate_status=DATA_UNAVAILABLE`が1件でも存在する場合、Rankingを開始せず日次結果を`DATA_UNAVAILABLE`とする
    - Event Gateが正常完了し`PASS`候補・`DATA_UNAVAILABLE`候補がともに0件の場合、Rankingを開始せず`NO_TRADE`とする
    - Event Gateが正常完了し`PASS`候補が1件以上かつ`DATA_UNAVAILABLE`候補が0件の場合、`ranking_ready=true`となり`build-ranking`を実行して`ranking.json`を生成する。`ranking.json`の`ranking_status`は`COMPLETE`または`DATA_UNAVAILABLE`のいずれかであり、いずれの場合もRank 1をTRADEへ変換するSelection / Absolute Quality Gateは未実装のため、main agentが独自比較して`TRADE`を作ることはない。`ranking_status=DATA_UNAVAILABLE`の場合は日次結果を`DATA_UNAVAILABLE`とし、`ranking_status=COMPLETE`の場合は既存フィールドでSelection未実装である旨を記録した`NO_TRADE`を`recommendation.json`へ保存する
    - `REJECT`・`DATA_UNAVAILABLE`のEvent Gate候補はいずれのケースでもRankingへ渡さない
21. `TRADE`の場合だけ人間に保有数・当日取引数を確認し、Risk Engineを実行して`risk_result.json`を保存
22. `NO_TRADE`または`DATA_UNAVAILABLE`の場合は人間入力なしでRisk Engineの`NOT_APPLICABLE`を保存
23. `recommendation.md`と`report.md`を生成
24. run artifact allowlist（`event_research.json`、`event_gate.json`、`ranking.json`を含む）を検証し、`trades/recommendations.csv`へ推奨履歴を追加
25. 作成ファイル、判断理由、データ欠落、Risk Engine結果を報告

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
- Discovery Critical Source（Yahoo!ファイナンス2ランキング）が欠落している
- 最新の過去runが壊れており、TDnet調査期間を確定できない（作業停止）
- OHLCVが`CONFLICT`または`SINGLE_SOURCE_ONLY`
- 決算・重要開示の確認が必要だが確認できない
- SBI画面の注文仕様を人間が確認できない

Risk Engineが`REJECTED`の場合は、提案値を変更せず`REJECTED`として記録します。
`REJECTED`を回避するために提案値を都合よく変更して再実行しません。
