# Nightly Operation

## 通常Operator Flow（Production, 毎晩）

普通の夜はこの3段階だけである。

1. **必要ならHumanが通常のGit同期を行う** — `git switch main` /
   `git pull --ff-only origin main`
2. **User-Agentを設定してProduction Context Launcherを起動する**

   ```bash
   export DAYTRADE_HTTP_USER_AGENT='<human-managed value>'
   daytrade-sbi/scripts/claude-production --target-date YYYY-MM-DD
   ```

   `DAYTRADE_HTTP_USER_AGENT`はSource Acquisitionの実行前提である。値は環境の継承
   だけで届くので、**Launcherを起動するそのshellで設定する**。詳細は後述の
   [HTTP User-Agent](#http-user-agenthuman-shellで設定する)節を参照する
3. **`$prepare-daytrade-plan`** — Canonical CLI Pipeline Orderを実行する

このどこにもSecurity Gateは無い。DayTradeの結果を信用してよいかを決めるのは、
Pipelineの内側にあるRaw Evidence / SHA256 / Source Ledger / deterministic Parser /
Trust Chain / Risk Engineであり、localのClaude実行環境がどう構成されているかでは
ない。詳細は[Production Context Launcher](#production-context-launcher)を参照する。

## 開始方法

毎晩、Codex または Claude Code へ次のように依頼します。どちらでも同じリポジトリCLIパイプラインが動きます。

> `$prepare-daytrade-plan`

Skillは`prompts/nightly_research.md`を詳細手順として読みます。メインエージェントはCanonical CLI Pipeline Order（[canonical-pipeline.md](canonical-pipeline.md)）どおりにCLIを逐次実行します。市場データの取得は`acquire-*` CLIだけが行い、サブエージェントへ委譲できるのは読み取り専用の出典監査だけです。

実行前にPython依存関係が入っていること、テストが成功することを確認します。

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

## Production Context Launcher

`scripts/claude-production`は**Security Gateではない**。この checkout がNightlyを
実行してよい状態か（実在する`--target-date`、`main`、進行中のGit操作なし、tracked
変更なし、解決可能なHEAD）を確認し、run contextを環境変数へ置いて`daytrade-sbi`から
Claude Codeを`exec`する。それだけである。

```bash
daytrade-sbi/scripts/claude-production --target-date 2026-09-01
```

`--preflight-only`を付けるとClaudeを起動せずcheckだけを行う。これはHumanの
migration / troubleshooting用であり、Security Attestationではない。

### 起動を拒否する条件

| 条件 | error code |
| --- | --- |
| `--target-date`が実在するYYYY-MM-DDでない | `CLAUDE_TARGET_DATE_INVALID` |
| repository rootを解決できない / git と不一致 | `CLAUDE_PRODUCTION_REPOSITORY_UNRESOLVED` |
| current branchが`main`でない / detached HEAD | `CLAUDE_PRODUCTION_BRANCH_NOT_MAIN` |
| merge / rebase / cherry-pick / revert / bisect が進行中 | `CLAUDE_PRODUCTION_GIT_OPERATION_IN_PROGRESS` |
| tracked fileに未commitの変更がある | `CLAUDE_PRODUCTION_WORKING_TREE_DIRTY` |
| `git rev-parse HEAD`が40桁SHAを返さない | `CLAUDE_PRODUCTION_HEAD_UNRESOLVED` |

tracked cleanの判定は`git status --porcelain --untracked-files=no`相当である。
**untracked fileは起動拒否理由にしない。** checkoutに置かれた作業用fileは、これから
実行されるcommit済みcodeが何かについて何も語らないからである。

**Launcher failureはBusiness decisionではない。** `NO_TRADE`・`DATA_UNAVAILABLE`・
`REJECTED`のいずれへも変換しない。Business Stageが1つも走っていないのだから、
Business判断は存在しない。

### Launcherが確認しないこと

次はいずれも起動可否に影響しない（DTWO-2026-026で廃止）。

```text
/etc のProduction runtime marker / seccomp marker
/etc/claude-code のManaged Policy / Runtime Guard
Claude Codeのexact version
bwrap / socat / seccompの有無
MCP / Remote Control / /status の状態
local mainとfetchしたてのorigin/mainの一致
network到達性
runtime_security.json
HTTP User-Agentのpresence / value
```

これらは個人所有のlocal実行環境に対するOperational Securityであって、市場Evidenceの
正しさとは別軸である。そのため**Business Runの正当性条件から外した**。

### Launcherが設定する環境変数

```text
DAYTRADE_RUNTIME_PROFILE=production
DAYTRADE_PROJECT_ROOT=<repository root>
DAYTRADE_ROOT=<daytrade-sbi root>
DAYTRADE_RUN_DIR=<daytrade-sbi>/runs/<target-date>
DAYTRADE_TARGET_DATE=<target-date>
DAYTRADE_GIT_HEAD_SHA=<現在の40桁HEAD>
```

Launcherはrun directoryへfileを1つも書かない。`runtime_security.json`も
Security Attestationも生成しない。

### HTTP User-Agent（Human shellで設定する）

実HTTP Source Acquisitionには環境変数`DAYTRADE_HTTP_USER_AGENT`が必要である。
`src/source_fetch.py`の`user_agent()`はこの環境変数だけを読み、hardcoded defaultを
持たない。未設定・空文字・前後に空白を含む値はいずれも
`HTTP_USER_AGENT_NOT_CONFIGURED`となり、Source Acquisitionはfail-closedで停止する。
curl自身の身元で市場サイトへ黙ってGETしないための契約であり、この挙動は変更しない。

値が届く経路は**環境の継承だけ**である。

```text
Human shell（export DAYTRADE_HTTP_USER_AGENT）
  ↓ 環境をそのまま引き継ぐ
Production Context Launcher
  ↓
Claude / repository CLI
  ↓
src.source_fetch.user_agent()
  ↓
curl --user-agent
```

```bash
export DAYTRADE_HTTP_USER_AGENT='<human-managed value>'
daytrade-sbi/scripts/claude-production --target-date YYYY-MM-DD
```

- UA値は**Human-managed runtime input**である。repositoryへ保存しない。実際の
  User-Agent文字列をこのドキュメントを含むrepository内fileへ書かない
- artifact / log / evidenceへUA値を保存しない
- **Launcher自身はUAのpresenceもvalueも検査しない。** これはSecurity Gateではなく、
  Runtime Security Preflightでもなく、Canonical CLI Pipeline Orderの新stepでもない。
  設定漏れはLauncherではなく最初のSource Acquisitionでfail-closedとして現れる
- Humanが必要ならshell profile等、repository外の手段で恒久設定してよい。
  repositoryとして特定の恒久設定方式を強制しない
- Developmentでも、real `curl_transport`を使うSource Acquisitionを実行するなら
  同じrequirementが適用される。fake / mock transportを使うunit testはUAを読まないので、
  Human shellへUA設定を要求する運用契約にはしない

### Production Human-only boundary

次は常にHumanだけが行う。Production Claude sessionからは実行しない。

- Production hostへのdeploy / install / OS設定変更
- `trades/trades.csv`への実績記録、`record-execution`
- `activate-selection-config`（Threshold Pairの適用）
- `archive-production-run` / `verify-production-archive`
- `reparse-production-discovery`
- SBI証券へのlogin・発注・訂正・取消

Production Claudeが直接Write / Editしてよい唯一のArtifactは
`runs/<date>/working/event_source_extraction.json`である。
`sources.json`・`market_data.json`・`market_research.json`・`candidate_pipeline.json`・
`ranking.json`・`selection.json`・`recommendation.json`・`risk_result.json`・
業務レポートを手で編集してはならない。

### Production Python

Production RuntimeのPython依存関係は、固定のroot-owned virtualenvへHumanがinstallする。
**Coding Agentはこのbootstrapを実行しない。** Production runtimeごとに1回だけ行う。

| 項目 | 値 |
| --- | --- |
| Production venv | `/opt/daytrade-production-python` |
| Production canonical interpreter | `/opt/daytrade-production-python/bin/python3` |
| Dependency manifest | `daytrade-sbi/requirements-dev.txt` |

Ubuntu/DebianのsystemPythonはPEP 668の**externally-managed environment**であり、
そこへ直接installしない。distribution提供のPython libraryでrepository requirementsを
代替もしない（例: Ubuntu 26.04の`python3-jsonschema` 4.19.2は`jsonschema>=4.23`を
満たさない）。

Repositoryを対象HEADへcheckoutし、`daytrade-sbi`をcurrent working directoryとして
実行する。各stepは目視ではなく`test`によるshell assertionで判定し、失敗したらSTOPする。

```bash
BASE_PYTHON="$(command -v python3)"
test -n "$BASE_PYTHON"
EXPECTED_PYTHON_VERSION="$(cat .python-version)"
test "$(
    "$BASE_PYTHON" -c 'import platform; print(platform.python_version())'
)" = "$EXPECTED_PYTHON_VERSION"
sudo apt-get install python3.14-venv
test ! -e /opt/daytrade-production-python
sudo "$BASE_PYTHON" -m venv --copies /opt/daytrade-production-python
sudo /opt/daytrade-production-python/bin/python3 \
    -m pip install --disable-pip-version-check \
    -r "$PWD/requirements-dev.txt"
sudo chown -R root:root /opt/daytrade-production-python
sudo chmod -R go-w /opt/daytrade-production-python
```

- `/opt/daytrade-production-python`が既に存在する場合は**Fail-ClosedでSTOP**する。
  削除・上書き・`--clear`による再作成を行わない
- `--copies`を使う。symlink-based venvはinterpreter pathがvenvの外へ解決され、
  dependencyを持つsite-packages contextと分離し得る

Production Human shellでの確認:

```bash
export PATH="/opt/daytrade-production-python/bin:$PATH"
PRODUCTION_PYTHON="$(command -v python3)"
test "$PRODUCTION_PYTHON" = "/opt/daytrade-production-python/bin/python3"
test ! -L "$PRODUCTION_PYTHON"
test "$(
    "$PRODUCTION_PYTHON" -c 'import platform; print(platform.python_version())'
)" = "$EXPECTED_PYTHON_VERSION"
"$PRODUCTION_PYTHON" -m pip check
"$PRODUCTION_PYTHON" -B -m pytest
```

**禁止事項**（いずれもdependency契約を回避する手段である）:

- systemPythonへの通常の`pip install`、およびPEP 668のoverride
- `pip install`のuser installやrepository rootへの`pip --target`
- `PYTHONPATH`によるdependency injection
- `python`のalias追加、`python`→`python3`のmanual symlink、`python-is-python3`
- aptのPython libraryでrepository requirementsを代替すること

### Source Matrixのdomain変更時にHumanが行うこと

`config/source_matrix.yaml`の`url_template` hostが変わったら、`.claude/settings.json`の
`sandbox.network.allowedDomains`をHumanが更新する。**Agentはこのfileを変更しない。**
必要hostが欠けている場合の唯一の正しい挙動は`SECURITY_POLICY_CHANGE_REQUIRED`で
停止し、Humanの変更を待つことである。

Business側のhost検証は`src/network_policy.py`が行う。sandbox allowlistは
Defense in Depthであって、そこにhostがあることはBusiness許可を意味しない。

### Discovery停止後のParser Fix Recovery（Human専用）

Discovery Fail-Closedで停止したRunについて、次の4条件がすべて揃った場合だけ、
保存済みRaw EvidenceをHumanがofflineで再解析できる。

1. Parser defectが確認された
2. そのfixがmainへmerge済み
3. `runs/<target-date>/source_pages/`と`network_requests/`に該当Evidenceが残っている
4. Discoveryより後のStageを1つも実行していない

通常の`acquire-discovery`を再実行しても解決しないことに注意する。Exact Logical Attemptは
immutableで、同じAttemptはbyte-for-byteで再利用されるだけであり、**自動reparseは行われない**
（[source-acquisition.md](source-acquisition.md)）。Production Claudeはここで停止し、
Humanへ引き継ぐ。

```
1. Production Claude sessionを終了する
2. fixをmainへ同期する（Human）
3. Production Context Launcherが通る状態にする（`main` / tracked clean）
4. Humanが通常のshellから次を実行する
```

```bash
daytrade-sbi/scripts/reparse-production-discovery --target-date <YYYY-MM-DD>
```

```
5. result=REPARSED（再実行時はALREADY_REPARSED）を確認する
6. Production Claudeを再起動する
7. $prepare-daytrade-plan
8. acquire-discoveryは既存の補正済みAttemptを再利用し、Network GET 0件で
   market_research.jsonを再生成する
9. Canonical CLI Pipeline Orderを続行する
```

- **HUMAN-ONLY**。`--target-date`が唯一の入力で、`--force` / `--run-dir` / `--parser` /
  `--allow-network`は存在しない。canonical `src.cli` subcommandではないため
  Canonical CLI Pipeline Orderに載ることが構造的にできない。
  **Production Claudeは実行できないし、実行しようとしてもいけない**
- Networkへ出ない。GET 0件・retry 0件・新規Physical Request 0件
- `network_requests/*.json`と`source_pages/*`はread-only Evidenceで、実行前後の生byteが
  完全一致する。削除も再取得もしない
- `attempt_id` / `request_id`は変わらない。更新されるのは`sources.json`のParser由来fieldだけ
- **前提**: この recovery は`runs/<date>/working/runtime_security.json`の
  `git_head_sha`とlocal HEADの一致を要求し、保護対象treeがcleanでなければ停止する。
  修復のためのreset / restore / checkoutは行わない。DTWO-2026-026以降の新規Runは
  この attestation を生成しないため、**recoveryが使えるのは attestation を持つ
  既存Runだけ**である。新規Runに対する起動条件は未定であり、Fail-Closedのまま
  停止する（緩和はしていない）
- 下流Artifactが1件でもあれば`PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT`、
  現在のParserでもTOP50を確認できなければ`PRODUCTION_DISCOVERY_REPARSE_STILL_INCOMPLETE`で、
  どちらも`sources.json`を1 byteも変更しない
- 証跡は`runs/<date>/working/production_discovery_reparse/<git_head_sha>.json`（Non-Business
  Sidecar）。Business Artifactではなく、Business Verifierの検査対象にもならない
- 対象はDISCOVERYだけである。Stage1 / Stage2 / Turnover / Eventの汎用Replayは存在しない

### Run終了後: Production Run Archive（Human専用）

`runs/<target-date>/`はOperationalなdirectoryです。次回起動時のtracked clean判定を
通すためにgit ignoreされており、Pipelineが途中で止まれば半端な状態で残り、
整理すればその夜の唯一の記録が消えます。

そこでProduction Nightly Runが終了したら（`NO_TRADE` / `DATA_UNAVAILABLE` /
`REJECTED` / Discovery未完了でも同じです）、Production Claude sessionを終了したうえで、
Humanが通常のshellから証跡を封をします。

```bash
scripts/archive-production-run --target-date <YYYY-MM-DD>
scripts/verify-production-archive --target-date <YYYY-MM-DD>
```

- `--target-date`が唯一の入力です。`--force`はありません
- Operational Runはread-only sourceで、Archiveはそこへ書き込みません
- どちらもcanonical `src.cli` subcommandではないため、Canonical CLI Pipeline Orderに
  載ることが構造的にできません。**Production Claudeはこれらを実行しません**
- `archive_status: INCOMPLETE`はArchiveの失敗ではなく、その夜が途中で止まったことを
  意味します。途中で止まった夜の証跡も保存します
- Archiveは同一マシン上にあり、off-site backupの代わりにはなりません

`runs/<date>/working/`は**Non-Business Sidecar**です。Business Verifierは
`working/`を丸ごとskipし、内部のfile名を列挙しません。`working`という名の
regular file・symlink、`working2`のような別directoryは拒否します。Archiveは
`working/`配下もraw byteで保存しますが、その中身がArchiveの`archive_status`を
左右することはありません（Archive v2）。

契約の全文: [production-run-archive.md](production-run-archive.md)
