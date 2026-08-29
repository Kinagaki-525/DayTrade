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

## Claude Production Runtime Security（FIX-R2-004）

### Production Runtime要件

- 正式なProduction RuntimeはDayTrade Production**専用**のLinuxまたはWSL2。
  Native Windowsは禁止。
- `/etc/claude-code/managed-settings.json`はRepository単位ではなく、その
  **Linux/WSL distro上のClaude Code全体**へ適用されるGlobal Managed Policyです。
  したがって通常のDevelopment WSLへdeployしてはいけません。Development用Claudeと
  同じdistroへProduction Managed Policyを置く運用は禁止です。
- 専用Runtime marker `/etc/daytrade-production-runtime` の内容が
  `DAYTRADE_PRODUCTION_RUNTIME_V1` であることがPreflight条件です。このファイルは
  Human provisioningで作成します。

### Human-only prerequisites（Agentは絶対に実行しない）

Coding Agentはinstallを行いません。Preflightは存在確認だけを行い、不足していれば
`CLAUDE_SANDBOX_DEPENDENCY_MISSING`でfail closedします。

```bash
# Ubuntu/Debian (human, once per production runtime)
sudo apt-get install bubblewrap socat
```

- Claude Code >= 2.1.219（`sandbox.network.strictAllowlist`の要件）。
- Linux / WSL2 Productionでは Claude Sandbox seccomp filter が必須です。Preflightは
  seccompの有無を推測しません。次のHuman Runtime Acceptance v2（V2差分Probe方式）
  の結果だけを検証します。`/sandbox`のDependencies表示だけをAcceptanceの根拠には
  しません。

  以下のHuman Runtime Acceptance v2手順は、リポジトリの
  `<repository-root>/daytrade-sbi`をcurrent working directoryとする。
  Sandbox外Probeを実行した同じ`daytrade-sbi`ディレクトリからClaude Codeも起動する。

  1. HumanがClaude Sandbox**外**の通常のWSL shellで`daytrade-sbi`へ移動し、
     次を実行する。

     ```bash
     cd <repository-root>/daytrade-sbi
     scripts/claude-seccomp-probe --expect-unsandboxed
     ```

     結果が`DAYTRADE_SECCOMP_PROBE=UNIX_SOCKET_CREATE_ALLOWED`でexit 0である
     ことを確認する。

  2. 手順1と同じ`daytrade-sbi`ディレクトリをCWDとして、Claude Codeを
     Sandbox有効状態で起動する。

  3. `/sandbox`でHumanが現在のSandbox設定を確認する（参考情報であり、これ単独では
     Acceptanceの根拠にしない）。

  4. Claude Sandbox**内**で次を実行する。

     ```bash
     scripts/claude-seccomp-probe --expect-sandboxed
     ```

     結果が`DAYTRADE_SECCOMP_PROBE=UNIX_SOCKET_CREATE_BLOCKED_EPERM`でexit 0で
     あることを確認する。

  5. 手順1と手順4の両方が確認できた場合に限り、Humanがroot権限でattestation
     markerを作成する。

     ```bash
     sudo sh -c 'echo DAYTRADE_SECCOMP_VERIFIED_V2 > /etc/daytrade-seccomp-verified'
     sudo chown root:root /etc/daytrade-seccomp-verified
     sudo chmod 644 /etc/daytrade-seccomp-verified
     ```

     markerの内容は完全一致で`DAYTRADE_SECCOMP_VERIFIED_V2`とし、group・other
     非writableにする。手順1または手順4のいずれかが確認できない場合はmarkerを
     作成せず、ProductionはFail Closedのまま停止する。

  6. Preflightは`/etc/daytrade-seccomp-verified`が regular file / uid 0 /
     group・other非writable / 内容が`DAYTRADE_SECCOMP_VERIFIED_V2`であることを
     検証します。ひとつでも満たさなければ`CLAUDE_SANDBOX_SECCOMP_UNVERIFIED`で
     Hard Stopし、`claude`は起動せず`runtime_security.json`も書きません。旧
     `DAYTRADE_SECCOMP_VERIFIED_V1`のmarkerも同様にUnverified扱いです
     （V1からV2への自動migrationはありません）。

  このmarkerはHuman専用です。Probe自身・Coding Agent・Repository Scriptも
  作成しません。Native LinuxでもWSL2でも同一の要件です（判断分岐を増やさない
  ため）。
- `/etc/daytrade-production-runtime`も同様に regular file / uid 0 /
  group・other非writable / 内容一致 を要求します（content-only verificationでは
  ありません）。
- Ubuntu 24.04+では`sysctl kernel.apparmor_restrict_unprivileged_userns`を確認し、
  `1`の場合はClaude Code公式手順に従ってbwrap用AppArmor profileをHumanが設定します。
  Repository Scriptからは変更しません。

#### Production Python dependency bootstrap（Human専用）

Production RuntimeのPython依存関係は、固定のroot-owned virtualenvへinstallする。
**Coding Agentはこのbootstrapを実行しない。** Production runtimeごとに1回、Humanが行う。

| 項目 | 値 |
| --- | --- |
| Production venv | `/opt/daytrade-production-python` |
| Production canonical interpreter | `/opt/daytrade-production-python/bin/python3` |
| Dependency manifest | `daytrade-sbi/requirements-dev.txt` |

Ubuntu/DebianのsystemPythonはPEP 668の**externally-managed environment**であり、
そこへ直接installしない。distribution提供のPython libraryでrepository requirementsを
代替もしない（例: Ubuntu 26.04の`python3-jsonschema` 4.19.2は
`jsonschema>=4.23`を満たさない）。

1. base OS Pythonのversionがrepositoryのcanonical版（`daytrade-sbi/.python-version`）と
   一致することを確認する。一致しなければSTOPする。

   ```bash
   python3 --version
   ```

2. venv作成能力をHumanがaptで導入する。これはPython project dependenciesのinstallでは
   なく、venv creationのOS prerequisiteである。

   ```bash
   sudo apt-get install python3.14-venv
   ```

3. `/opt/daytrade-production-python`が既に存在する場合は**Fail-ClosedでSTOP**する。
   独自の削除・上書き・`--clear`による再作成を行わない。既存環境の扱いはHumanが
   別途判断する。

4. base Pythonからroot-owned venvを作る。**`--copies`が必須**である。

   ```bash
   sudo python3 -m venv --copies /opt/daytrade-production-python
   ```

   通常のsymlink-based venvを使ってはいけない。`canonical_production_python()`は
   `Path.resolve()`でsymlinkを解決するため、venvの`python3`がbase interpreterへの
   symlinkだと**canonical pathがvenvの外へ解決され**、Production Python identityと
   dependencyを持つvenv site-packages contextが分離し得る。`--copies`はvenv内Pythonを
   regular fileにするので、canonicalize後も同じpathのままになる。

5. venv内のpipからdependency manifestをinstallする。`requirements-dev.txt`は
   `-r requirements.txt`を含むため、2つを別々にinstallしない。

   ```bash
   sudo /opt/daytrade-production-python/bin/python3 -m pip install \
       --disable-pip-version-check -r <repository-root>/daytrade-sbi/requirements-dev.txt
   ```

6. root所有かつgroup/other非writableにする。Production ClaudeがDependency環境を
   書き換えられる状態にしない。

   ```bash
   sudo chown -R root:root /opt/daytrade-production-python
   sudo chmod -R go-w /opt/daytrade-production-python
   ```

7. Production Human shellでvenvのbinをPATH先頭へ置き、候補を1回だけ決める。

   ```bash
   export PATH="/opt/daytrade-production-python/bin:$PATH"
   PRODUCTION_PYTHON="$(command -v python3)"
   ```

   期待値は`/opt/daytrade-production-python/bin/python3`である。異なる場合はSTOPする。

8. 候補を検証する。1つでも満たさなければSTOPする。

   ```bash
   test -f "$PRODUCTION_PYTHON" && test ! -L "$PRODUCTION_PYTHON"
   "$PRODUCTION_PYTHON" --version
   "$PRODUCTION_PYTHON" -m pip check
   ```

   - regular fileであり**symlinkでない**こと（`--copies`の確認）
   - versionが`.python-version`と一致すること
   - `pip check`が成功すること

9. repository自身のresolverが同じpathを返すことを確認する（`daytrade-sbi`をCWDとして
   実行する）。異なる場合はSTOPする。

   ```bash
   "$PRODUCTION_PYTHON" -c 'import sys; from src.claude_runtime_security import canonical_production_python; print(canonical_production_python(sys.executable))'
   ```

   出力が`/opt/daytrade-production-python/bin/python3`と完全一致することを確認する。

**禁止事項**（いずれもFail-Closedを回避する手段であり採用しない）:

- systemPythonへの通常の`pip install`、およびPEP 668のoverride
- `pip install`のuser installやrepository rootへの`pip --target`
- `PYTHONPATH`によるdependency injection
- `python`のalias追加、`python`→`python3`のmanual symlink作成、
  `python-is-python3`のinstall
- aptのPython libraryでrepository requirementsを代替すること

このvenvにpytestが常設されるが、それを理由に**Runtime Guardのallowed commandを
追加しない**。Production ClaudeのManaged Bash allowed invocationは引き続き
`<canonical production python> -B -m src.cli ...`だけである。

### Source Matrixのdomain変更時にHumanが行うこと

Production allowed domainsは`config/source_matrix.yaml`の`url_template` hostと
`config/issuer_domain_registry.yaml`から`derive_expected_domains()`が導出する。
したがって**Source MatrixのhostがPRで変わると、Expected Managed Policyも変わる**。

- 変更をmergeしたら、Humanが下記「Policy deployment」の手順で
  `scripts/render-claude-production-policy`と`scripts/deploy-claude-managed-policy`を
  **再実行する**。再deployしないとRuntime Guardが
  `CLAUDE_NETWORK_DOMAIN_SET_MISMATCH`で起動を拒否する（Fail-Closed）
- Development側の`.claude/settings.json`の`allowedDomains`もHumanが更新する。
  **Agentはこのファイルを変更しない。** 必要hostが欠けている場合の唯一の正しい挙動は
  `SECURITY_POLICY_CHANGE_REQUIRED`で停止し、Humanの変更を待つことである
- PR #15で`JPX_LISTED_COMPANY`が`www2.jpx.co.jp`（東証上場会社情報検索）へ移ったため、
  **この再deployが必要**である。追加は完全一致hostであり、wildcardではない

### Policy deployment（Human）

この手順のcommandはすべて**Production Linux/WSL2のHuman shell**で、`daytrade-sbi`を
current working directoryとして実行する。冒頭「開始方法」のPowerShell `py`は
Windows Python Launcher向けの別contextであり、ここでは使わない。

1. 専用Production WSL2を用意し、上記prerequisitesをHumanがinstallする。
   Python依存関係は「Production Python dependency bootstrap（Human専用）」を完了して
   いること。未完了ならここから先へ進まない。
2. RepositoryをReviewed HEADへcheckoutする。
3. **Production Python candidateを1回だけ決める。** 以降のpytest / render / deployは
   すべてこの同じ値を使う。PATHを先にmaterializeしてから`command -v python3`を
   1回だけ実行する（同一手順中に再discoveryしない）。

   ```bash
   export PATH="/opt/daytrade-production-python/bin:$PATH"
   PRODUCTION_PYTHON="$(command -v python3)"
   ```

   期待値は`/opt/daytrade-production-python/bin/python3`である。PATHを先に
   materializeしないと、dependencyを持たないsystem Pythonが選ばれる。

   `python3`はProduction Python candidateの**discovery launcher**にすぎない。
   Managed Policy / Runtime Security上のcanonical identityは、この候補を
   `canonical_production_python()`がresolveした**absolute executable path**である。
   `--production-python`にsymlink（例: `python3 -> python3.11`）を渡しても構わない。
   `canonical_production_python()`が内部で必ずresolved absolute pathへ正規化するため、
   Managed Bash allow rule / Hook command / `runtime_security.json` /
   `DAYTRADE_PRODUCTION_PYTHON` / Runtime Guardの比較はすべて同一identityになる。
   Humanが事前にsymlinkを手動解決したり、独自にcanonicalizeする必要はない。

   **Productionはbare `python` commandの存在を要求しない。** `python`のalias追加、
   `python -> python3` symlink作成、`python-is-python3`のinstallをprerequisiteに
   しない。`command -v python3`が失敗して`PRODUCTION_PYTHON`が空になる場合は
   Fail-ClosedとしてPolicy deploymentを開始しない（aliasやsymlinkで回避しない）。

4. 依存関係とtestをその候補で確認する。versionはrepositoryのcanonical版
   （`daytrade-sbi/.python-version`）と一致していなければならない。

   ```bash
   "$PRODUCTION_PYTHON" --version
   "$PRODUCTION_PYTHON" -B -m pytest
   ```

   version不一致、または0 failed以外ならSTOPする。独自にPythonをinstall・切替しない。

5. Policyをrenderする（`/etc`には何も書きません）。scriptのshebang解決に任せず、
   **script自身も同じProduction Pythonで起動する**。

   ```bash
   "$PRODUCTION_PYTHON" scripts/render-claude-production-policy --production-python "$PRODUCTION_PYTHON" > /tmp/managed-settings.json
   ```

6. rendered policyをHumanがreviewする。
7. root権限でdeployする。ここも同様にscript自身をProduction Pythonで起動する。
   `sudo`の`secure_path`はPATHを差し替えるため、shebang任せだと別のPythonが
   選ばれ得る。

   ```bash
   sudo "$PRODUCTION_PYTHON" scripts/deploy-claude-managed-policy --production-python "$PRODUCTION_PYTHON"
   ```

   既に`/etc/claude-code/managed-settings.json`が存在する場合は
   `EXISTING_MANAGED_POLICY_PRESENT`で停止します。自動merge・自動backup・overwriteは
   行わず、`--force`も存在しません。既存の組織Policyを壊さないためです。
   `managed-settings.d`へのdrop-in配置も行いません。
8. `claude doctor`を実行し、Managed Settingsにinvalid entryがないことを確認する。
9. `scripts/claude-production --target-date <YYYY-MM-DD> --preflight-only`で
   Runtime Security Preflightを通す。
10. Claude Codeの`/status`でSetting sourcesを確認し、file-based
    *Enterprise managed settings*が実際に読み込まれていることを確認する。別のmanaged
    sourceが優先されている場合はPASS扱いにせず、そのPolicyをreviewするまで停止する。
11. Offline Runtime Smoke（`validate-source-matrix`等、networkを使わないCLI）を実行する。
    後述のProduction Path Materialization Contractに従い、path argumentは具体的な
    absolute pathへmaterializeし、1 Bash callにcanonical CLI commandを1個だけ入れる。

    ```text
    <production python> -B -m src.cli validate-source-matrix --source-matrix <DAYTRADE_ROOT>/config/source_matrix.yaml
    ```

    期待結果は`{"valid": true, "errors": []}`とexit code 0。終了コードを見るために
    `; echo "EXIT_CODE=$?"`を付け足さない（`;`はGuardが拒否する）。
    `--source-matrix config/source_matrix.yaml`のような相対パスは
    `CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN`で拒否される。
12. 実Sourceへの Network Smoke（JPX / Yahoo / Kabutan / TDnet）はFIX-R2-005で初めて行う。

### Runtime Security Preflight

`scripts/claude-production`は次の順序でfail closedに検査し、すべてPASSしたときだけ
`runs/<target-date>/working/runtime_security.json`を書いて`claude`を`exec`します。

検査順序は platform → `production_marker` → `sandbox_seccomp` → `git_clean` →
`claude_version` → `sandbox_dependencies` → managed policy … です。`--target-date`は
最初にYYYY-MM-DD（実在日付）としてvalidateされ、run directoryが
`runs/<target-date>`直下であることを確認します。違反は`CLAUDE_TARGET_DATE_INVALID`で、
このときfilesystemには何も書きません。Security Boundaryのpath
（`/etc/claude-code/managed-settings.json`・`/etc/claude-code/daytrade-runtime-guard.py`・
`/etc/daytrade-production-runtime`・`/etc/daytrade-seccomp-verified`）はCLIから
差し替えできません。`--target-date`と`--preflight-only`だけがHuman入力です。

`production_marker` / `sandbox_seccomp` / `git_clean` / `claude_version` / `sandbox_dependencies` /
`managed_settings` / `managed_settings_permissions` / `sandbox_required` /
`sandbox_escape_disabled` / `strict_network_allowlist` / `managed_domain_lock` /
`managed_hook_lock` / `managed_permission_lock` / `mcp_lockdown` / `domain_sync` /
`runtime_guard` / `runtime_guard_sha` / `http_user_agent`

`runtime_security.json`には`DAYTRADE_HTTP_USER_AGENT`の値、環境変数一式、token、
API key、cookie、credentialを書きません。User-Agentは`http_user_agent_present: true`
だけを記録します。実値は`runtime_security.json`にも、logにも、`report.md`にも、
`recommendation.md`にも書きません。

### Production Entry Contract

Production Nightly Runは、`scripts/claude-production`のRuntime Security Preflightを
PASSしたProduction Claudeからのみ開始します。単に`claude`を起動しただけのsessionは、
同じマシン上であってもProduction Runtimeとして扱いません。Preflightを通っていない
sessionからSource Acquisition CLIを実行しないでください。

`DAYTRADE_HTTP_USER_AGENT`は必須です。未設定または空白のみの場合、Preflightは
`CLAUDE_HTTP_USER_AGENT_MISSING`でfail closedになり、`src/source_fetch.py`側も
`HTTP_USER_AGENT_NOT_CONFIGURED`で停止します。コード側のdefault User-Agentも
fallbackも存在せず、追加してはいけません。診断のためにHumanが一時的に使った値を
Repositoryのcanonical defaultへ昇格させることもしません。

### Production Path Materialization Contract

このリポジトリのドキュメント（[canonical-pipeline.md](canonical-pipeline.md)・
[prompts/nightly_research.md](../prompts/nightly_research.md)・
`.agents/skills/prepare-daytrade-plan/SKILL.md`・本ドキュメント）に現れる
`config/source_matrix.yaml`や`runs/YYYY-MM-DD/ranking.json`のような相対パスは、
すべての実行環境（Codex / Development Claude Code / Production Claude Code）で共通の
**論理パス表記**です。Production Claude Codeでは、これはBash Toolへそのまま渡せる
command文字列**ではありません**。

Production Runtime Guardはshell実行**前**のcommand文字列だけを検査するため、
path argumentが具体的なabsolute pathでなければ
`CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN`でfail closedします
（例: `--source-matrix must be an absolute path in production`）。
これはGuardの欠陥ではなくGuardの契約そのものであり、
Guard側でrelative pathやshell expansionを許可する変更は行いません。

したがってProduction Claude Codeは、Bash Toolへ渡す直前に、論理パスを次の2つの
runtime contextから具体的なabsolute pathへmaterializeします。DayTrade Rootを
特定のOS絶対パスとしてドキュメントやSkillへhardcodeしません。

- **DayTrade Root**: Production Launcher（`scripts/claude-production`）は
  `exec claude`の直前に`os.chdir(<daytrade root>)`するため、Production Claude Code
  sessionのcurrent working directoryがDayTrade Rootです。sessionが提示している
  working directoryのabsolute pathをそのまま使います。
- **Run Directory / Target Date**: Run Directoryは`<DayTrade Root>/runs/<target-date>`
  です。target dateはHumanが`scripts/claude-production --target-date`で指定した日付で、
  Preflightが書いた`runs/<target-date>/working/runtime_security.json`の`target_date`
  （および`production_python`）で確認できます。この確認はReadで行い、Bashで`cat`しません。

materializeした結果、Bash Toolへ渡すcommandには次のいずれも残してはいけません。

| 残してはいけない表記 | 例 |
| --- | --- |
| repository相対パス | `config/source_matrix.yaml` / `runs/YYYY-MM-DD/ranking.json` |
| `./` `../` `~/` | `./runs/...` / `../daytrade-sbi/config/...` / `~/DayTrade/...` |
| 環境変数展開 | `$DAYTRADE_ROOT/...` / `${DAYTRADE_ROOT}/...` / `$DAYTRADE_RUN_DIR/...` / `${DAYTRADE_RUN_DIR}/...` |
| command substitution | `$(pwd)/...` / `` `pwd`/... `` |

環境変数もcommand substitutionもshell実行時にしか展開されないため、Guardから見れば
前者は「絶対パスでない値」、後者は「shell metacharacter」であり、いずれも拒否されます。

正しいmaterialization（`<DAYTRADE_ROOT>`と`<TARGET_DATE>`は、実際のabsolute path・
実日付へ置換済みの具体的な文字列であること）:

```text
<production python> -B -m src.cli validate-source-matrix --source-matrix <DAYTRADE_ROOT>/config/source_matrix.yaml
<production python> -B -m src.cli build-ranking --candidates <DAYTRADE_ROOT>/runs/<TARGET_DATE>/candidates.json --config <DAYTRADE_ROOT>/runs/<TARGET_DATE>/strategy_snapshot.yaml --event-gate <DAYTRADE_ROOT>/runs/<TARGET_DATE>/event_gate.json --market-data <DAYTRADE_ROOT>/runs/<TARGET_DATE>/market_data.json --output <DAYTRADE_ROOT>/runs/<TARGET_DATE>/ranking.json --source-matrix <DAYTRADE_ROOT>/config/source_matrix.yaml --sources <DAYTRADE_ROOT>/runs/<TARGET_DATE>/sources.json
```

`<production python>`も同様に、`runtime_security.json`の`production_python`と完全一致する
canonical absolute pathです（`python3`やsymlink aliasは拒否されます）。

### Production 1-call-1-command Contract

Productionでは **1 Bash call = 1 canonical CLI command** です。Bash Tool 1回につき
canonical `src.cli` commandをちょうど1個だけ実行します。

- 終了コード確認のために`; echo "EXIT_CODE=$?"`を付けない。`;`は
  `CLAUDE_PRODUCTION_BASH_DENIED`（`shell metacharacter ';' is not permitted in
  production`）になります。Bash Tool自身がcommandのnon-zero exitを返すため、
  終了コード確認用の追加shell commandは不要です。
- `&&` / `||` / `|` / `&` / redirect（`>` `>>` `<`）/ command substitution（`$(...)`
  / `` `...` ``）/ process substitution / 改行 / `cd`はすべて拒否されます。
- 出力ファイルが必要な場合はredirectではなくCLI自身の`--output`を使います。
- **`acquire-*`はこの例外です。** `acquire-*`のBusiness Artifactは
  CLI自身がcanonical pathへ生成し、`--output`はCLI result summaryの出力先でしかありません。
  標準Nightlyでは`acquire-*`に`--output`を付けず、result summaryをBash Toolのstdoutで
  確認します。summaryをfileへ残す必要がある場合だけ
  `runs/<target-date>/working/<result-name>.json`を指定できます。
  run directory直下のBusiness Artifact（`market_research.json` / `market_data.json` /
  `sources.json` / `research_window.json` / `strategy_snapshot.yaml`）、`--sources`自身、
  run directory外を指定すると、ネットワークGETを1回も消費する前に
  `ACQUISITION_OUTPUT_PATH_INVALID`で停止します。
- 複数のcanonical CLI commandを1つのBash callへまとめない。Canonical CLI Pipeline Orderの
  各canonical `src.cli` commandを、それぞれ独立したBash callとして逐次実行します。
  Pipelineの番号とBash callは1:1ではありません（`init/complete event-research`のように
  1つの番号表現に複数のcanonical CLI commandが含まれる箇所があります）。
  Canonical CLI Pipeline Order自体は変わりません。

### Production Boundaryの正本

Production Security Boundaryの正本は**OS Managed Policy**です。プロジェクトの
`.claude/settings.json`と`.claude/hooks/network_guard.py`はDevelopment defense onlyとして
維持しますが、`allowManagedHooksOnly: true`のProductionでは実行されません。
`strictAllowlist`はProject Scopeではproduction gateにならないため、Managed Policyだけに
設定します。

Production中のClaudeは既定でBash denyです。許可されるのは
`<production python> -B -m src.cli <approved subcommand> ...`のexec formだけで、
`;` `&&` `||` `|` `&` redirection command substitution process substitution `cd`は
すべて拒否されます。Claudeが直接Write/Editできる唯一のArtifactは
`runs/<date>/working/event_source_extraction.json`です。

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
3. Production preflightを通す
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
  `APPROVED_SUBCOMMANDS`に載ることが構造的にできず、**Production Claudeは実行できないし、
  実行しようとしてもいけない**
- Networkへ出ない。GET 0件・retry 0件・新規Physical Request 0件
- `network_requests/*.json`と`source_pages/*`はread-only Evidenceで、実行前後の生byteが
  完全一致する。削除も再取得もしない
- `attempt_id` / `request_id`は変わらない。更新されるのは`sources.json`のParser由来fieldだけ
- Runtime Security Attestation（`runs/<date>/working/runtime_security.json`）の
  `git_head_sha`とlocal HEADが一致し、保護対象treeがcleanでなければ停止する。
  修復のためのreset / restore / checkoutは行わない
- 下流Artifactが1件でもあれば`PRODUCTION_DISCOVERY_REPARSE_DOWNSTREAM_ARTIFACT_PRESENT`、
  現在のParserでもTOP50を確認できなければ`PRODUCTION_DISCOVERY_REPARSE_STILL_INCOMPLETE`で、
  どちらも`sources.json`を1 byteも変更しない
- 証跡は`runs/<date>/working/production_discovery_reparse/<git_head_sha>.json`（Non-Business
  Sidecar）。Business Artifactではなく、Business Verifierの検査対象にもならない
- 対象はDISCOVERYだけである。Stage1 / Stage2 / Turnover / Eventの汎用Replayは存在しない

### Run終了後: Production Run Archive（Human専用）

`runs/<target-date>/`はOperationalなdirectoryです。次回Preflightの`git_clean`を
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
- どちらもcanonical `src.cli` subcommandではないため、Production Managed Policyの
  `APPROVED_SUBCOMMANDS`に載ることが構造的にできません。**Production Claudeは
  これらを実行できませんし、実行しようとしてはいけません**
- `archive_status: INCOMPLETE`はArchiveの失敗ではなく、その夜が途中で止まったことを
  意味します。途中で止まった夜の証跡も保存します
- Archiveは同一マシン上にあり、off-site backupの代わりにはなりません

`runs/<date>/working/`は**Non-Business Sidecar**です。Business Verifierは
`working/`を丸ごとskipし、内部のfile名を列挙しません（将来Runtime Security Evidenceが
増えても、それだけでBusiness Runを`INVALID_RUN`にしないためです）。
`working`という名のregular file・symlink、`working2`のような別directoryは拒否します。
Archiveは`working/`配下もraw byteで保存したうえで、`runtime_security.json`を
Runtime Security証跡として、Business Artifact chainとは独立に
`VALID` / `MISSING` / `INVALID`で分類します。

契約の全文: [production-run-archive.md](production-run-archive.md)
