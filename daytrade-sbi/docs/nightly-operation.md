# Nightly Operation

## 開始方法

毎晩、Codex または Claude Code へ次のように依頼します。どちらでも同じリポジトリCLIパイプラインが動きます。

> `$prepare-daytrade-plan`

Skillは`prompts/nightly_research.md`を詳細手順として読みます。メインエージェントはCanonical CLI Pipeline Order（[canonical-pipeline.md](canonical-pipeline.md)）どおりにCLIを逐次実行します。市場データの取得は`acquire-*` CLIだけが行い、サブエージェントへ委譲できるのは読み取り専用の出典監査だけです。

実行前にPython依存関係を導入し、テストが成功することを確認します。

```powershell
py -m pip install -r requirements-dev.txt
py -B -m pytest
```

## Canonical CLI Pipeline Order

夜間実行はこの順序で逐次実行します（正本: [docs/canonical-pipeline.md](canonical-pipeline.md)）。

1. `snapshot-config`
2. `validate-source-matrix`
3. `resolve-research-window`
4. `acquire-discovery`
5. `init-candidate-research`
6. `acquire-stage1-sources`
7. market_data Stage1 reflect
8. `apply-stage1`
9. TSE Listing Batch Gate
10. `plan-stage2-batches`
11. `acquire-stage2-market-sources`
12. market_data Stage2 reflect
13. `acquire-actual-turnover`
14. market_data turnover reflect
15. `validate-market`
16. `screen-market`
17. `build-candidate-pipeline`
18. `acquire-event-sources`
19. Event AI Classification (local only)
20. `merge-event-source-extraction`
21. `init/complete event-research`
22. `validate-event-research`
23. `build-event-gate`
24. `build-ranking`
25. Case A/B/C

## Agentが行う処理（Canonical CLI Pipeline Orderの実行）

1. `AGENTS.md`を確認
2. `config/strategy.yaml`を確認
3. `TODO.md`を確認
4. 翌営業日と前営業日を確認
5. `config/strategy.yaml`を対象日ディレクトリへスナップショット保存
6. `config/source_matrix.yaml`を検証
7. PythonでTDnet調査期間を確定し、`research_window.json`を保存
8. `acquire-discovery`を実行する。Yahoo!ファイナンス2ランキングをcurl GETで取得し、生ページをSHA256付きで保存し、決定論的にParseしてTOP50を確認し、`market_research.json`のDiscovery Candidateを作成する。Agentが候補を手で書かない
9. `init-candidate-research`で全Discovery Candidateの`candidate_research[]`を初期化
10. `acquire-stage1-sources`を実行する。候補集合は`market_research.json`の`discovery_candidates`から導出され、Agentが銘柄を渡すことはない。Stage 1の値は`sources.json`と`market_data.json`へ自動反映される。続けて`apply-stage1`で売買単位・資金条件を分類
11. Stage 2着手前のTSE上場確認（Fail Closed、全件一括ゲート）: `apply-stage1`の直後、`plan-stage2-batches`を実行する前に、Stage 1 `PASS`候補**全件**について、検証済みSource Evidenceから東京証券取引所上場であると確認できるかを一括で判定する。`YAHOO_JP_HISTORY` / `YAHOO_JP_NEWS` / `YAHOO_JP_QUOTE`のURL templateはいずれも`.T`（東証）Suffix固定でDiscoveryは`ALL_MARKETS`のため、確認できない候補が混ざり得る。**1件でも確認できない候補があれば`plan-stage2-batches`を実行せず、Stage 2 Candidate Research・Turnover Research・Event Research・Rankingを含む以降の全ステージを開始せず、夜間実行全体をこの時点で停止する。** 個別候補をStage 1 `PASS`集合から除外・スキップして残りだけ進めることはしない
12. Stage 1 `PASS`候補全件のTSE上場確認が成功した場合のみ`plan-stage2-batches`でStage 2調査対象をbatch化し、全候補についてCandidate Researchを実施・merge
13. `acquire-actual-turnover`: Stage 2完了後、**`screen-market`を実行する前に**、（TSE上場確認は手順11で全件一括ゲート済みの）Stage 1 `PASS`候補について前営業日の実際の売買代金を`YAHOO_JP_QUOTE`で調査する。`FOUND`の場合は`sources.json`のSource Attempt・Source Recordと`market_data.json`の`turnover`へ同じCanonical値を保存する。失敗した場合は失敗Source Attemptをそのまま保存し、`market_data.json`の`turnover`を`null`にして古い`FOUND`値を残さない
14. Pythonで市場データと出典台帳を検証し、`official_ohlcv_audit.json`と、Hard Screening結果・Rule評価・分析Featureを含む`candidates.json`を生成
15. Pythonで`candidate_pipeline.json`、`performance.json`、`research.md`を生成
16. `candidate_pipeline.summary.pipeline_complete=true`、`screening_complete=true`を確認する
17. Event: `acquire-event-sources`を実行する。候補集合は`candidates.json` / `candidate_pipeline.json`の`status=ELIGIBLE`かつ`screening_status=PASS`から導出される。6つのEvent Source（`JPX_TDNET` / `JPX_EARNINGS_SCHEDULE` / `COMPANY_IR` / `COMPANY_IR_DISCLOSURE` / `YAHOO_JP_NEWS` / `KABUTAN_NEWS`）を取得し、共有ページは1回のGETで候補ごとのSource Attemptを作る。次にEvent AI Classificationが**ローカル保存済み生ページだけ**を読んで一時作業ファイルを書き、`merge-event-source-extraction`が全項目を再検証して`sources.json`へ反映する。最後に`init-event-research`→`complete-event-research`で`selected_attempt_ids`、`news_classifications`、`event_gate_as_of`を確定する。PASS・REJECT・DATA_UNAVAILABLEの判定はEvent Researchでは行わない
18. Event Research Validation: `validate-event-research`を実行し、`event_research.json`の整合性を検証する
19. Event Gate: `build-event-gate`を実行し、決定論的Pythonロジックで`event_gate.json`を生成する
20. Event Gate Validation: `event_gate.json`の`event_gate_complete=true`を確認する。`false`の場合は以降へ進まず、Rankingを開始しない
21. Event Gateの結果に応じて次のとおりFail Closedで分岐する。新しいReason CodeやSchemaは追加しない
    - `ranking_ready=false`の場合、Rankingを開始しない
    - Event Gate候補に`gate_status=DATA_UNAVAILABLE`が1件でも存在する場合、Rankingを開始せず日次結果を`DATA_UNAVAILABLE`とする
    - Event Gateが正常完了し`PASS`候補・`DATA_UNAVAILABLE`候補がともに0件の場合、Rankingを開始せず`NO_TRADE`とする
    - Event Gateが正常完了し`PASS`候補が1件以上かつ`DATA_UNAVAILABLE`候補が0件の場合、`ranking_ready=true`となり`build-ranking`を実行して`ranking.json`を生成する。`ranking.json`の`ranking_status`は`COMPLETE`または`DATA_UNAVAILABLE`のいずれかであり、以降はSelectionの3ケース（Case A/B/C、下記）へ分岐する。main agentがRank 1を独自比較して`TRADE`を作ることは一切なく、Selection関連のCLI実行と結果報告に徹する
    - `REJECT`・`DATA_UNAVAILABLE`のEvent Gate候補はいずれのケースでもRankingへ渡さない

### Selectionの3ケース（Ranking完了後の分岐）

Ranking完了後、main agentはCLI（Case A/Bは`build-ranking-terminal-recommendation`、Case Cは`build-selection`・`build-selection-recommendation`）を実行し、その結果を報告するだけです。Rank 1の`feature_values`（売買代金・相対呼値）を自分で読んでPASS/REJECTを判断することはなく、`recommendation.json`を手で作成することもありません。

- **Case A（Ranking `DATA_UNAVAILABLE`）**: `ranking.json`の`ranking_status`が`DATA_UNAVAILABLE`の場合、Selectionを実行しない。`build-ranking-terminal-recommendation`を実行し、`decision=DATA_UNAVAILABLE`の`recommendation.json`（schema_version 1、`selection_reasons`は`ranking.reason_codes`の転記）を生成して日次結果を確定する。
- **Case B（Ranking `COMPLETE` かつ Selection未設定）**: `ranking_status`は`COMPLETE`だが、`config/strategy.yaml`の`selection.enabled`がfalse（または閾値が`null`のまま較正待ち）の場合、`build-selection`を実行しない（実行してもSelection側がHard Errorで停止し`selection.json`を生成しない）。代わりに`build-ranking-terminal-recommendation`を実行し、`decision=NO_TRADE`・理由`SELECTION_NOT_ACTIVE_PENDING_CALIBRATION`の`recommendation.json`を生成する。

  ```bash
  py -m src.cli build-ranking-terminal-recommendation \
    --ranking runs/<target_date>/ranking.json \
    --event-gate runs/<target_date>/event_gate.json \
    --candidates runs/<target_date>/candidates.json \
    --candidate-pipeline runs/<target_date>/candidate_pipeline.json \
    --market-data runs/<target_date>/market_data.json \
    --research-window runs/<target_date>/research_window.json \
    --sources runs/<target_date>/sources.json \
    --source-matrix config/source_matrix.yaml \
    --config runs/<target_date>/strategy_snapshot.yaml \
    --output runs/<target_date>/recommendation.json
  ```

  `build-ranking-terminal-recommendation`はCase Cの`build-selection-recommendation`と同じ厳密さで、`ranking.json`が主張する全上流アーティファクト（`event_gate.json`・`candidates.json`・`market_data.json`・`sources.json`・source matrix・`strategy_snapshot.yaml`）の生バイトSHA256を`ranking.input_hashes`と完全一致で再検証し、Ranking CLI自身が使う`validate_ranking_preconditions`/`validate_ranking_output_contract`を再実行してからCase A/Bを判定する。

- **Case C（Ranking `COMPLETE` かつ Selection有効）**: `ranking_status`が`COMPLETE`で`selection.enabled`がtrue（閾値も設定済み）の場合、次の順にCLIを実行する。

  ```bash
  py -m src.cli build-selection \
    --ranking runs/<target_date>/ranking.json \
    --event-gate runs/<target_date>/event_gate.json \
    --candidates runs/<target_date>/candidates.json \
    --market-data runs/<target_date>/market_data.json \
    --sources runs/<target_date>/sources.json \
    --source-matrix config/source_matrix.yaml \
    --config runs/<target_date>/strategy_snapshot.yaml \
    --output runs/<target_date>/selection.json

  py -m src.cli build-selection-recommendation \
    --ranking runs/<target_date>/ranking.json \
    --selection runs/<target_date>/selection.json \
    --event-gate runs/<target_date>/event_gate.json \
    --candidates runs/<target_date>/candidates.json \
    --candidate-pipeline runs/<target_date>/candidate_pipeline.json \
    --market-data runs/<target_date>/market_data.json \
    --research-window runs/<target_date>/research_window.json \
    --sources runs/<target_date>/sources.json \
    --source-matrix config/source_matrix.yaml \
    --config runs/<target_date>/strategy_snapshot.yaml \
    --output runs/<target_date>/recommendation.json
  ```

  `build-selection`・`build-selection-recommendation`はいずれも、`selection.json`/`recommendation.json`自身のSHA256ハッシュチェーンだけでなく、共有ヘルパー（`load_and_verify_ranking_trust_chain`）経由で`ranking.json`をその上流アーティファクト一式に対して独立に再検証してから初めて結果を確定する。`selection.json`の`selection_status`が`SELECTED`なら`recommendation.json`の`decision`は`TRADE`、`NO_TRADE`なら`decision`は`NO_TRADE`になる（`build-selection-recommendation`はSelectionの判定結果を機械的に転記するだけで、独自の判定は行わない）。`TRADE`の場合だけ、続けて`risk-check`へ`--selection runs/<target_date>/selection.json`を渡してRisk Engineを実行する。

22. Case Cで`decision=TRADE`の場合だけ人間に保有数・当日取引数を確認し、`risk-check --selection ... --ranking ... --event-gate ... --research-window ...`（すべて必須。Selection駆動の`recommendation.json`はRisk Engine自身が`ranking.json`の上流Trust Chain全体とSelection/Recommendationの再計算まで独立に検証するため、いずれか一つでも欠けるとHard Errorで停止する）でRisk Engineを実行して`risk_result.json`を保存
23. Case A・Case B、またはCase Cで`decision`が`TRADE`以外の場合は人間入力なしでRisk Engineの`NOT_APPLICABLE`を保存する。Case A/B（`recommendation.schema_version=1`）の場合、`risk-check`自身がRankingとその上流Artifact全体のtrust chainを独立に再検証するため、`--ranking`・`--event-gate`・`--research-window`の3つが必須になる（欠けるとHard Errorで停止し、`risk_result.json`は生成されない）。Case C（`recommendation.schema_version=2`）の場合も、`TRADE`/`NO_TRADE`のいずれであれ`risk-check`が`--selection`・`--ranking`・`--event-gate`・`--research-window`のすべてからRankingのTrust Chain全体とSelection/Recommendationの再計算を独立に検証してから`NOT_APPLICABLE`を確定する
24. `recommendation.md`と`report.md`を生成
25. run artifact allowlist（`event_research.json`、`event_gate.json`、`ranking.json`を含む）を検証し、`trades/recommendations.csv`へ推奨履歴を追加
26. 作成ファイル、判断理由、データ欠落、Risk Engine結果を報告

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

## Risk Result v2 と Production Verifier（FIX-R2-003）

`config_schema_version: 6`の設定で`risk-check`を実行すると、`risk_result.json`は
`schema_version: 2`で出力される（Case A/B/Cすべて）。v2では次の2フィールドが必須になる。

- `evaluation_context`: Risk評価に実際に使った`current_positions` / `trades_today`。
  Case C `TRADE`の場合だけ0以上の整数を記録し、Case A・Case B・Case C `NO_TRADE`では
  Risk評価入力を使用しないため必ず`null` / `null`へ正規化される。
- `input_hashes`: 上流Artifactの**生バイト**SHA256をちょうど12キー記録する。
  `selection_sha256`はCase A/Bでは`null`、Case Cでは実際の`selection.json`のハッシュ。
  `market_research_sha256`はCase C `TRADE`で`--market-research`を渡した場合だけ非`null`になる。
  Case A・Case B・Case C `NO_TRADE`ではRiskが`market_research.json`を読まないため、
  `--market-research`を渡しても`evaluation_context`と同じく`null`へ正規化される
  （FIX-R2-003A）。

`config_schema_version`が6未満の履歴Runは従来どおり`risk_result.schema_version: 1`のままで、
引き続きスキーマ的に有効である。

Production Verifierは`risk_result.json`に書かれた値を正本として信用しない。Selection /
Recommendation / Riskをリポジトリ自身の公式Builder（`src/downstream_trust.py`経由で
`risk-check`と共有）で再計算し、完全一致しなければ`INVALID_RUN`になる。

- `verify-production-run`: Case A / Case B / Case Cのいずれも、Trust Chainが全て成功した
  場合にのみ`VERIFIED_*`となる。
- `verify-production-happy-path`: **Case Cのみ**を許可する。Case A・Case Bは正当なRunでは
  あるが、Selectionが有効化された本番Happy Pathではないため`INVALID_RUN`になる。
  Case C `NO_TRADE`も、Risk `REJECTED`も、正当なHappy Pathである（TRADEを強制しない）。
