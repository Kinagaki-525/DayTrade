# Nightly Research Prompt

翌営業日の日本株デイトレ候補を調査し、構造化artifact、候補パイプライン、Risk Engine結果、Markdownレポートを作成する。

## 基本ルール

1. 最初に `AGENTS.md`、`config/strategy.yaml`、`TODO.md` を読む。
2. PythonからWeb取得、LLM API、外部市場データAPIを呼ばない。Web調査はCodexまたは読み取り専用subagentが行い、Pythonは検証、計算、レンダリングを担当する。
3. `config/source_matrix.yaml` に定義された Source ID、role、criticality、information_type だけを使う。実行時に代替サイトを採用しない。
4. Discovery経路、売買ルール、資金100,000円、100株固定、Weekend Cutoff、`previous_day_high_breakout` は変更しない。
5. Discovery順位や表示値は候補発見理由として保存するだけで、最終Rankingの評価値として使わない。
6. 未取得値、日付不明、更新時刻不明、根拠不足は推測で埋めない。`NOT_STARTED`、`DEPENDENCY_NOT_READY`、`EXECUTION_FAILED`、`DATA_UNAVAILABLE`、`CONFLICT`、`SINGLE_SOURCE_ONLY` などで明示する。
7. `DATA_UNAVAILABLE` は、必要Source checkを試行済みで外部要因が記録されている場合だけ使う。未実施や未mergeは `PIPELINE_INCOMPLETE` として扱う。
8. `screening` の `enabled: false` や `threshold: null` をCodex判断で補完しない。
9. SBI証券へのログイン、画面操作、注文、送信は行わない。
10. dependentなCLIは必ず逐次実行する。並列化してよいのは、互いに独立したWeb調査の読み取りだけ。

## 保存先

対象日のディレクトリへ次を保存する。

```text
runs/YYYY-MM-DD/
  strategy_snapshot.yaml
  research_window.json
  market_research.json
  market_research_validation.json
  sources.json
  market_data.json
  market_validation.json
  candidates.json
  candidate_pipeline.json
  event_research.json
  event_gate.json
  performance.json
  research.md
  recommendation.json
  recommendation.md
  risk_result.json
  report.md
  official_ohlcv_audit.json
  source_pages/
```

正常終了時は `tmp_pydeps`、一時parser、作業用HTMLなどが `runs/YYYY-MM-DD/` 直下に残っていないことを検証する。

## 初期化

対象日と前営業日を権威ある情報で確認し、`YYYY-MM-DD` を実日付に置き換えて実行する。

```powershell
py -B -m src.cli snapshot-config --output runs/YYYY-MM-DD/strategy_snapshot.yaml
py -B -m src.cli validate-source-matrix --source-matrix config/source_matrix.yaml
py -B -m src.cli resolve-research-window --target-date YYYY-MM-DD --previous-trading-day YYYY-MM-DD --runs-dir runs --source-matrix config/source_matrix.yaml --output runs/YYYY-MM-DD/research_window.json
```

以降の `screen-market`、`risk-check` では `--config runs/YYYY-MM-DD/strategy_snapshot.yaml` を使う。市場データ検証系では `--source-matrix config/source_matrix.yaml` と `--market-research runs/YYYY-MM-DD/market_research.json` を指定する。

## Discovery

- Discoveryは `VOLUME_RANKING` と `PRICE_GAIN_RANKING` のYahoo!ファイナンス2ランキングに限定する。
- 各ランキングはTOP50を保存する。2ランキングを証券コード単位でUnion/Dedupし、Discovery Candidateは最大100銘柄とする。
- TDnet単独候補、ニュース、検索、SNS、アクセスランキング、AI選定テーマからDiscovery Candidateを追加しない。
- `discovery_candidates` はDiscovery unionと完全一致させる。
- Discovery unionを保存したら、次を実行して全Discovery Candidate分の `candidate_research[]` を初期化する。

```powershell
py -B -m src.cli init-candidate-research --market-research runs/YYYY-MM-DD/market_research.json --output runs/YYYY-MM-DD/market_research.json
```

## Candidate Research

Discovery Candidate全件を起点にする。候補を途中で消さない。

標準 `source_checks[].check_id` は次に固定する。

```text
listed_company
trading_unit
primary_ohlcv
secondary_ohlcv
tick_size
topix500_membership
earnings_schedule
tdnet
news_context
```

- 不要なcheckは `NOT_REQUIRED` として明示する。
- 未実施は `NOT_STARTED`、上流依存待ちは `DEPENDENCY_NOT_READY`、実行失敗は `EXECUTION_FAILED` とし、外部出典不足とは分ける。
- Stage 1通過候補は必ず `RESEARCH_COMPLETE`、`DATA_UNAVAILABLE`、`ELIGIBLE`、`REJECTED` の終端状態に到達させる。
- Stage 1 rejectは `stage1_checks[]` に `check_id`、`status`、`reason_code`、`source_refs`、`source_attempt_ids` を保存する。Source根拠なしのrejectは使わない。
- Stage 1の取得済み事実を保存したら、次を実行して100株条件と100,000円資金条件をSource根拠付きで分類する。

```powershell
py -B -m src.cli apply-stage1 --market-research runs/YYYY-MM-DD/market_research.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/market_research.json
py -B -m src.cli plan-stage2-batches --market-research runs/YYYY-MM-DD/market_research.json --output runs/YYYY-MM-DD/market_research.json
```

- `plan-stage2-batches` が作成した `subagent_batches[]` の候補を勝手に減らさない。必要な場合だけ `--batch-size` を明示して調整する。
- 売買単位は `JPX_TRADING_UNIT` を使う。`JPX_LISTED_COMPANY` を売買単位Sourceとして流用しない。
- TOPIX500 membershipは既存の `JPX_TOPIX500` を使う。
- Tick sizeは `JPX_TICK_SIZE`、価格入力Source、`JPX_TOPIX500` membership Sourceを `field_provenance.source_refs` で追跡する。audit-only根拠だけなら `SINGLE_SOURCE_ONLY` のまま取引不可とする。
- TDnet 0件は、検索や取得が成功して `result_count=0` の場合だけ `FOUND` として記録する。失敗時は `result_count=null`。

Subagentを使った場合は `market_research.subagent_batches[]` に `REQUESTED -> RETURNED -> VALIDATED -> MERGED` を記録する。`RETURNED` または `VALIDATED` なのに `merged_candidate_codes` に入っていない候補は `SUBAGENT_RESULT_NOT_MERGED` として pipeline incomplete にする。

## 検証とパイプライン

Web調査で `market_research.json`、`sources.json`、`market_data.json` を保存したら、必ず次を逐次実行する。

```powershell
py -B -m src.cli validate-market-research --market-research runs/YYYY-MM-DD/market_research.json --research-window runs/YYYY-MM-DD/research_window.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --output runs/YYYY-MM-DD/market_research_validation.json
py -B -m src.cli validate-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --output runs/YYYY-MM-DD/market_validation.json
py -B -m src.cli audit-official-ohlcv --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --output runs/YYYY-MM-DD/official_ohlcv_audit.json
py -B -m src.cli screen-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/candidates.json
py -B -m src.cli build-candidate-pipeline --market-research runs/YYYY-MM-DD/market_research.json --market-data runs/YYYY-MM-DD/market_data.json --candidates runs/YYYY-MM-DD/candidates.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/candidate_pipeline.json
py -B -m src.cli build-performance --market-research runs/YYYY-MM-DD/market_research.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --sources runs/YYYY-MM-DD/sources.json --output runs/YYYY-MM-DD/performance.json
py -B -m src.cli render-research --market-research runs/YYYY-MM-DD/market_research.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --sources runs/YYYY-MM-DD/sources.json --performance runs/YYYY-MM-DD/performance.json --output runs/YYYY-MM-DD/research.md
```

`candidate_pipeline.summary.pipeline_complete=false`、`research_incomplete > 0`、または `screening_complete=false` の場合、recommendationをRisk Engineへ進めない。未実施、欠落、未merge、Source記録不足、Screening未完了を修正してから再実行する。

## Event Research と Event Gate

Hard Screening PASS銘柄（`candidates.json` で `status=ELIGIBLE` かつ `screening_status=PASS`）についてだけ、企業固有イベント（決算、TDnet開示、危険ニュース）を確認する。`pipeline_complete` と `screening_complete` が両方 `true` の場合だけ開始する。

```powershell
py -B -m src.cli init-event-research --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --candidates runs/YYYY-MM-DD/candidates.json --previous-trading-day YYYY-MM-DD --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/event_research.json
```

`init-event-research` はTicker skeletonだけを作る。Web調査（JPX決算予定、TDnet、Issuer開示、Yahoo!ニュース、Kabutanニュース）はCodexが行い、`sources.json` へSource Attemptを追加し、`event_research.json` の `selected_attempt_ids`・`news_classifications`・`event_gate_as_of` を埋める。日付推測、材料強度判断、PASS/REJECT判定はEvent Researchでは行わない。

```powershell
py -B -m src.cli validate-event-research --event-research runs/YYYY-MM-DD/event_research.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --candidates runs/YYYY-MM-DD/candidates.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml
py -B -m src.cli build-event-gate --event-research runs/YYYY-MM-DD/event_research.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --candidates runs/YYYY-MM-DD/candidates.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/event_gate.json
```

`event_gate.json` の `ranking_ready=false`、または `event_gate_complete=false` の場合、Rankingへ進めない（Ranking自体は未実装のため、`recommendation.json` は常に `NO_TRADE` か `DATA_UNAVAILABLE` とする）。

## Codex比較とRecommendation

- Hard Screeningでは `candidates.json` に `screening_status`、Rule評価、Source Provenance、分析Featureを保存する。
- `candidate_pipeline.summary.ranking_complete=false` の間は、`screening_pass_count` が1件でも複数でも `TRADE` を作成しない。
- `REJECTED` を候補へ戻さない。
- Ranking未実装中は、候補が残っている場合も `NO_TRADE` とし、理由にRanking未実装を記録する。必要データが外部要因で揃わない場合だけ `DATA_UNAVAILABLE`。
- `recommendation.json` には `research_cutoff`、`post_cutoff_information_status`、`pipeline_summary`、必要に応じて `source_statuses` を保存する。
- `pipeline_summary` は `candidate_pipeline.summary` から転記し、推測で変更しない。

## Risk Engineとレポート

`TRADE` の場合だけ、Risk Engine前に現在の保有数と当日取引数を人間へ確認する。確認できない場合は停止する。

```powershell
py -B -m src.cli risk-check --recommendation runs/YYYY-MM-DD/recommendation.json --candidates runs/YYYY-MM-DD/candidates.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/risk_result.json --current-positions <確認済み保有数> --trades-today <確認済み当日取引数>
```

`NO_TRADE` または `DATA_UNAVAILABLE` の場合は、保有数と当日取引数を聞かずに `NOT_APPLICABLE` を生成する。

```powershell
py -B -m src.cli risk-check --recommendation runs/YYYY-MM-DD/recommendation.json --candidates runs/YYYY-MM-DD/candidates.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/risk_result.json
```

Risk Engine後、Markdownは構造化JSONから再生成する。途中追記、`may screen`、`order-plan preview` などの途中判断を残さない。

```powershell
py -B -m src.cli render-report --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --output runs/YYYY-MM-DD/recommendation.md
py -B -m src.cli render-daily-report --market-research runs/YYYY-MM-DD/market_research.json --candidate-pipeline runs/YYYY-MM-DD/candidate_pipeline.json --sources runs/YYYY-MM-DD/sources.json --performance runs/YYYY-MM-DD/performance.json --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --output runs/YYYY-MM-DD/report.md
py -B -m src.cli validate-run-artifacts --run-dir runs/YYYY-MM-DD
py -B -m src.cli record-recommendation --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json
```

## 最終報告

次を簡潔に報告する。

- 対象日
- `TRADE`、`NO_TRADE`、`DATA_UNAVAILABLE`、またはRisk Engine `REJECTED`
- 選定または見送り理由
- `candidate_pipeline.summary.pipeline_complete` と `research_incomplete`
- Risk Engine結果
- 欠落・矛盾したデータ
- 作成ファイル
- SBI実画面で人間が確認すべき事項

発注済み、約定見込み、利益見込みとは表現しない。
