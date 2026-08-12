# v2 アーキテクチャ

## 責任分界

```text
Codex: Web調査・出典保存（sources.json）・Event Research（企業固有EventのNews Classification）
  ↓
Python: データ検証・固定条件スクリーニング・Event Gate判定・価格計算・Risk Engine
  ↓
Ranking（ranking-v1）: Event Gate PASS候補だけを入力とする決定論的な順位付け（実際の売買代金desc・呼値/発動価格の相対比asc、単純Rank合計）
  ↓
Selection（selection-v1）: Ranking Rank 1だけを、設定済みの2つの固定閾値（最低売買代金・最大相対呼値）へ照らしてSELECTED/NO_TRADEへ振り分ける決定論的工程。Rank 2以下へのFallbackは行わない
  ↓
Selection Recommendation Builder: SelectionがSELECTEDならTRADE、NO_TRADEならNO_TRADEとして`recommendation.json`（Recommendation v2、`selection_sha256`を保持）を生成
  ↓
Risk Engine（`risk-check --selection`）: TRADE推奨の注文案を固定リスク条件で検証
  ↓
人間: 出典と注文案の最終確認・SBI株アプリへの手入力・実績記録
```

Codexの役割はWeb調査による出典保存とEvent ResearchでのNews Classificationなどの構造化に限られ、PASS/REJECT/DATA_UNAVAILABLEの判定や候補比較は行いません。PythonはHard Screening・Event Gate・Selectionで決定論的に判定し、AIの代わりに銘柄を評価しません。Ranking（ranking-v1）はEvent Gate `PASS`候補だけを入力として受け取り、実際の売買代金（`turnover`）の降順と、呼値/発動価格の相対比（`relative_tick_size`）の昇順という2つのFeatureだけをCompetition RankingしてRank合計で並べ替えます。AI・スコア・Weight・閾値は使いません。Rankingは`ranking.json`を生成するだけで、Rank 1をSELECTED/NO_TRADEへ変換するのはSelection（selection-v1、`src/selection.py`）の役割です。Ranking `COMPLETE`は単独ではTRADE可能を意味せず、Selectionが両ルール（最低売買代金・最大相対呼値）をPASSした場合だけSELECTEDになります。main agentが独自に比較して`TRADE`を作ることはなく、`build-selection`/`build-selection-recommendation`のCLIだけがSelectionの判定と`recommendation.json`の生成を行います。人間だけが実際の注文を決定・入力します。

## 責務境界

- AI（Codex） = Research / Classification（Web調査で得たSource Attempt・Evidenceの`sources.json`への保存、Event ResearchのNews Classification）。PASS/REJECT/DATA_UNAVAILABLEを決定しない。
- Python = Validation / Screening / Event Gate / Selection（市場データ検証、Hard Screening、Event Gateの決定論的PASS/REJECT/DATA_UNAVAILABLE判定、SelectionのSELECTED/NO_TRADE判定）。
- Ranking（ranking-v1） = Event Gate `PASS`候補だけを入力とする決定論的な銘柄順位付け工程（`src/ranking.py`、`ranking.json`）。実際の売買代金desc・呼値/発動価格の相対比ascの2 Featureだけを単純Rank合計で並べる。AI・スコア・Weightは使わない。
- Selection（selection-v1） = Ranking Rank 1だけを対象に、`config/strategy.yaml`の`selection`ブロックにある2つの固定閾値（`minimum_turnover_yen`・`maximum_relative_tick_size`）へ照らして両方PASSならSELECTED、いずれかREJECTならNO_TRADEを`selection.json`へ保存する純粋関数（`src/selection.py`、`build-selection` CLI）。Rank 2以下へのFallbackは一切行わない（`fallback_policy: none`）。`selection.enabled`がfalseの間、または両閾値が`null`の間は`build-selection`自体がHard Errorで停止し、`selection.json`を生成しない。
- Selection Recommendation Builder = `selection.json`の判定結果を`recommendation.json`（Recommendation v2）へ機械的に変換する工程（`src/selection_recommendation.py`、`build-selection-recommendation` CLI）。SELECTEDならdecision=TRADE、NO_TRADEならdecision=NO_TRADEとし、`selection_sha256`で`selection.json`とのリンクを保持する。判定ロジックは持たず、Selectionの結果を転記するだけ。
- Selection Calibration = 過去の複数日の`ranking.json`（同一cohort = strategy_version + config_sha256 + source_matrix_sha256）を読み、Rank 1の売買代金・相対呼値の分布から、任意の閾値候補がどれだけの日でPASS/REJECTになるかを機械的に集計する工程（`src/selection_calibration.py`、`build-selection-calibration`・`evaluate-selection-thresholds` CLI）。`ranking.json`をRank 1特徴量の情報源として読むだけでなく、各`ranking.json`をObservationとして数える前に、その上流アーティファクト一式（`event_gate.json`・`candidates.json`・`market_data.json`・`sources.json`・source matrix・`strategy_snapshot.yaml`）とハッシュチェーンを、Ranking CLI自身が使う実際のContract関数（`validate_ranking_preconditions`/`validate_ranking_output_contract`）で完全に再検証してから初めてObservationとして採用する。整合しているように見えるだけの手作り・捏造`ranking.json`はHard Errorで拒否される。利益最適化・推奨閾値の提示・`config/strategy.yaml`への書き込みは一切行わない。あくまで人間が閾値を決めるための事実集計であり、AIによる推奨ではない。
- Rankingが使う実際の売買代金（`YAHOO_JP_QUOTE`）は、Stage 2 Candidate Researchの後・`screen-market`の前に調査して `sources.json` と `market_data.json.turnover` へ保存する。`candidates.json` の `features.turnover` は `screen-market` が `market_data.json` から生成するため、この順序でなければRankingが要求する4箇所整合が成立しない。

## AI実行構成

`$prepare-daytrade-plan`と`$record-daytrade-result`は、メインエージェントが実行するワークフローSkillです。Skill自体をサブエージェントとして実行しません。

`prepare`だけが、独立した読み取り作業を`market_researcher`と`source_auditor`へ委譲できます。サブエージェントはファイルを変更せず、調査・監査結果をメインへ返します。メインは結果を統合し、日次成果物を書き、Pythonを実行します。`record`は人間確認と逐次書き込みが中心のため、メインエージェントとPythonだけで実行します。

## コンポーネント

| コンポーネント | 責任 |
| --- | --- |
| `config/strategy.yaml` | 固定リスク条件、検証中パラメータ、未決定スクリーニング値 |
| `config/source_matrix.yaml` | 市場調査で使うSource ID、Role、Criticality、URLテンプレート |
| `prompts/nightly_research.md` | Codexが毎晩従う調査・保存・検証手順 |
| `src/source_matrix.py` | Source Matrixの構造検証と標準Source ID管理 |
| `src/research_window.py` | TDnet調査期間を、初回補完期間または前回cutoffから決定 |
| `src/research.py` | Discovery成果物の検証とDiscovery候補Union |
| `src/market.py` | 市場データ、出典取得試行、OHLCV二重確認、後日監査 |
| `src/candidate_pipeline.py` | Discovery後の候補を下流で消さず、候補単位のパイプライン状態と集計を生成 |
| `src/performance.py` | Sourceリクエスト数、ステージ件数、任意の工程別時間を性能評価用に集計 |
| `src/contracts.py` | JSON Schemaの実行時検証と日次成果物間の紐付け検証 |
| `src/screening.py` | Candidate Research済みの検証済み値だけでHard Screeningを行い、Rule評価、Source Provenance、分析Featureを保存 |
| `src/event_research.py` | Event Research: Hard Screening `PASS`候補についてEvent Research用Artifactを管理する。Web調査で得たSource Attempt・Evidence自体は`sources.json`を正本とし、`event_research.json`にはそれらへの参照（`selected_attempt_ids`）、`news_classifications`、`event_gate_as_of`、`event_gate_input_tickers`などEvent Gate Input Candidate情報を保存する。PASS/REJECT/DATA_UNAVAILABLEは判定しない |
| `src/event_gate.py` | Event Gate: 保存済みArtifactから決算日・前営業日決算・TDnet開示・一次確認済み危険ニュースを決定論的に評価し、`event_gate.json`へPASS/REJECT/DATA_UNAVAILABLEを保存 |
| `src/ranking.py` | Ranking v1: Event Gate `PASS`候補だけを対象に、実際の売買代金と呼値/発動価格の相対比の2 FeatureをCompetition Rankingし、単純Rank合計で`ranking.json`を生成する純粋関数。SELECTED/NO_TRADE変換は行わない |
| `src/selection.py` | Selection v1: `ranking.json`のRank 1だけを対象に、`selection.rules`の2つの固定閾値（`minimum_turnover_yen`・`maximum_relative_tick_size`）で無短絡評価し、両方PASSならSELECTED、いずれかREJECTならNO_TRADEを`selection.json`へ保存する純粋関数。Decimal/整数の厳密比較のみを用い`float()`は使わない。Rank 2以下へのFallbackはない |
| `src/selection_recommendation.py` | `selection.json`の判定を`recommendation.json`（Recommendation v2）へ機械的に転記する純粋関数。SELECTED→TRADE、NO_TRADE→NO_TRADEの1対1変換のみで、独自の判定ロジックは持たない |
| `src/selection_calibration.py` | Selection Calibration v1: 同一cohort（strategy_version + config_sha256）の複数日`ranking.json`からRank 1の売買代金・相対呼値の実測値分布を集計し、任意の閾値候補に対する厳密なPASS/REJECT件数・比率を報告する純粋関数。利益最適化や推奨閾値の提示は一切行わない |
| `src/strategy.py` | 前日高値ブレイクの価格計算 |
| `src/risk.py` | AI案から独立した固定リスク検証。値は修正しない |
| `src/reports.py` | PASS・REJECTED・NO_TRADE・DATA_UNAVAILABLEに応じた手動確認レポート |
| `src/recommendations.py` | 取引しなかった日を含む推奨履歴の記録 |
| `src/metrics.py` | 実取引結果の集計 |
| `src/execution.py` | 完全決済済み実績と日次成果物の照合、CSV行生成、重複防止付き追記 |
| `schemas/` | nightly実行で保存するJSONの構造契約 |

`research_window.json`はPythonが確定したTDnet調査対象期間です。TDnetはDiscoveryではなく、Full Candidate Research内のCandidate Contextとして候補単位で確認します。対象日と前営業日の間に週末等の空白がある場合は`post_cutoff_information_status=OUT_OF_SCOPE`を出し、cutoff後情報を未確認0件として扱わないことを示します。

`market_research.json`はDiscoveryとCandidate Researchの経緯を保存する正本で、同じ`research_window`を含めます。Discovery CandidateはYahoo!ファイナンス出来高TOP50と値上がり率TOP50のUnion/Dedupだけで作り、TDnet単独銘柄は追加しません。Universe判定結果は`candidate_research[].universe_status`に保存します。Stage 1除外は、`sources.json`または`source_attempts`に存在する参照で裏付けられた許可済み`stage1_checks`がある場合だけ有効です。資金条件のStage 1除外は固定金額ではなく、設定済みの`capital.total_yen`と`capital.position_size`に対する`capital_limit`として扱います。

`candidate_pipeline.json`はDiscovery Unionを起点に全候補の状態を残す成果物で、`market_data.json`や`candidates.json`が空でも候補を消しません。`summary`にはHard Screening件数、Rule別件数、`screening_complete`、`ranking_complete=false`も保存します。

`sources.json`は出典台帳の正本で、成功した値の`sources`と、取得不能・未掲載・古い情報を含む`source_attempts`を分けて保存します。保存HTMLはEvidenceとして`source_attempts[].source_page_path`から追跡します。`market_data.json`へ埋め込んだ数値出典や`recommendation.json`の参照URLが台帳に存在しない場合、後続処理へ進みません。Event Researchで得た決算予定・TDnet開示・ニュースのWeb調査結果も含め、`sources.json`がSource Attempt・EvidenceのSingle Source of Truthです。

`event_research.json`はHard Screening `PASS`候補（`status=ELIGIBLE`かつ`screening_status=PASS`）だけを対象に、`sources.json`のSource Attemptへの参照（`selected_attempt_ids`）、`news_classifications`、`event_gate_as_of`、`event_gate_input_tickers`などEvent Research構造を保存する成果物です。Source Attempt・Evidence自体を`event_research.json`へ複製しません。PASS/REJECT/DATA_UNAVAILABLEの判定は含みません。`event_gate.json`は`event_research.json`と`sources.json`のSource Attemptを入力に、`earnings_on_target_date`・`earnings_on_previous_trading_day`・`tdnet_disclosure_in_event_window`・`dangerous_news_with_primary_confirmation`をPythonが決定論的に評価し、候補ごとの`gate_status`（PASS/REJECT/DATA_UNAVAILABLE）と`event_gate_complete`・`ranking_ready`を保存します。詳細なCLI手順は`prompts/nightly_research.md`を正本とします。

日次ディレクトリには`strategy_snapshot.yaml`を保存します。候補・推奨・Risk Engine結果へ同じ`strategy_version`と設定内容のSHA-256を引き継ぎ、別設定で作られた成果物の混在を拒否します。

## 状態

- `ELIGIBLE`: 市場データ検証と設定済み固定条件を通過。取引推奨ではない。
- `PASS`: Hard Screeningでは有効なTRADE_CRITICAL ruleを通過した状態。Event Gateでは対象日の決算・前営業日決算・TDnet開示・一次確認済み危険ニュースのいずれにも該当しないと判定された状態。Risk Engineでは注文案が固定リスク条件を通過した状態。Ranking `COMPLETE`後も、Selection（selection-v1）が両ルールをPASSと判定する（`selection_status: SELECTED`）まで取引推奨ではない。
- `REJECT`: Hard Screeningの有効ruleにより候補から除外、またはEvent Gateで決算・TDnet開示・一次確認済み危険ニュースが確認され候補から除外された状態。Rankingへ渡さない。
- `SELECTED` / `NO_TRADE`（Selection）: `build-selection`が`ranking.json`のRank 1へ`selection.rules`の2閾値（最低売買代金・最大相対呼値）を適用した結果。両方PASSなら`SELECTED`（`selected_ticker`にRank 1銘柄）、いずれかREJECTなら`NO_TRADE`（`selected_ticker: null`）。`selection.enabled`がfalse、または閾値が未設定（`null`）の間は`build-selection`自体を実行しない（実行するとHard Errorで停止する）。
- `TRADE`: Selectionが`SELECTED`と判定したRank 1銘柄について、`build-selection-recommendation`が`recommendation.json`（Recommendation v2）へ`decision: TRADE`として生成する注文案。Selectionを経由しない`TRADE`は作らない。Risk Engine通過前は採用不可。
- `NO_TRADE`: (1) Event Gateが正常完了し、`PASS`候補・`DATA_UNAVAILABLE`候補がともに0件の場合、または (2) Ranking `COMPLETE`だが`selection.enabled`がfalse（閾値未設定でSelectionを実行できない）場合、または (3) Selectionが`NO_TRADE`と判定した場合、のいずれか。注文を作らない。
- `DATA_UNAVAILABLE`: 必要な市場データまたはSource Policyが揃わず、取引判断まで到達していない状態。Event Gateに`DATA_UNAVAILABLE`の候補が1件でも存在する場合を含む。Rankingへ渡さない。
- `REJECTED`: 注文案またはデータが固定条件に違反。値を自動修正しない。
- `NOT_APPLICABLE`: `NO_TRADE`または`DATA_UNAVAILABLE`のため注文リスク評価対象がない。

## データ境界

`runs/`の市場データ・評価・注文案と、`trades/trades.csv`の実取引正本を混在させません。`execution_result.json`は人間が確認した実績をCSVへ反映する前に検証するための日次入力記録であり、証券会社から自動取得した証明ではありません。バックテストを追加する場合も別ディレクトリで管理します。

外部AI API、証券API、SBIログイン、ブラウザによる発注操作は実装対象外です。
