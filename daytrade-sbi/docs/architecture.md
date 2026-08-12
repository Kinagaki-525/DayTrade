# v2 アーキテクチャ

## 責任分界

```text
Codex: Web調査・出典保存（sources.json）・Event Research（企業固有EventのNews Classification）
  ↓
Python: データ検証・固定条件スクリーニング・Event Gate判定・価格計算・Risk Engine
  ↓
Ranking（ranking-v1）: Event Gate PASS候補だけを入力とする決定論的な順位付け（実際の売買代金desc・呼値/発動価格の相対比asc、単純Rank合計）
  ↓
Selection / Absolute Quality Gate（未実装）: Ranking結果からRank 1をTRADEへ変換する工程
  ↓
人間: 出典と注文案の最終確認・SBI株アプリへの手入力・実績記録
```

Codexの役割はWeb調査による出典保存とEvent ResearchでのNews Classificationなどの構造化に限られ、PASS/REJECT/DATA_UNAVAILABLEの判定や候補比較は行いません。PythonはHard ScreeningとEvent Gateで決定論的に判定し、AIの代わりに銘柄を評価しません。Ranking（ranking-v1）はEvent Gate `PASS`候補だけを入力として受け取り、実際の売買代金（`turnover`）の降順と、呼値/発動価格の相対比（`relative_tick_size`）の昇順という2つのFeatureだけをCompetition RankingしてRank合計で並べ替えます。AI・スコア・Weight・閾値は使いません。Rankingは`ranking.json`を生成するだけで、Rank 1をTRADEへ変換するSelection / Absolute Quality Gateは今回実装しません。Ranking `COMPLETE`はTRADE可能を意味しません。main agentが独自に比較して`TRADE`を作ることはなく、Selectionが実装されるまで`NO_TRADE`または`DATA_UNAVAILABLE`を保存します。人間だけが実際の注文を決定・入力します。

## 責務境界

- AI（Codex） = Research / Classification（Web調査で得たSource Attempt・Evidenceの`sources.json`への保存、Event ResearchのNews Classification）。PASS/REJECT/DATA_UNAVAILABLEを決定しない。
- Python = Validation / Screening / Event Gate（市場データ検証、Hard Screening、Event Gateの決定論的PASS/REJECT/DATA_UNAVAILABLE判定）。
- Ranking（ranking-v1） = Event Gate `PASS`候補だけを入力とする決定論的な銘柄順位付け工程（`src/ranking.py`、`ranking.json`）。実際の売買代金desc・呼値/発動価格の相対比ascの2 Featureだけを単純Rank合計で並べる。AI・スコア・Weightは使わない。Rank 1をTRADEへ変換するSelection / Absolute Quality Gateは未実装であり、別途要件定義する。実装されるまでTRADE推奨は作成しない。

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
| `src/ranking.py` | Ranking v1: Event Gate `PASS`候補だけを対象に、実際の売買代金と呼値/発動価格の相対比の2 FeatureをCompetition Rankingし、単純Rank合計で`ranking.json`を生成する純粋関数。Selection/`TRADE`変換は行わない |
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
- `PASS`: Hard Screeningでは有効なTRADE_CRITICAL ruleを通過した状態。Event Gateでは対象日の決算・前営業日決算・TDnet開示・一次確認済み危険ニュースのいずれにも該当しないと判定された状態。Risk Engineでは注文案が固定リスク条件を通過した状態。Ranking `COMPLETE`後も、最終選定（Selection / Absolute Quality Gate）が実装・完了するまで取引推奨ではない。
- `REJECT`: Hard Screeningの有効ruleにより候補から除外、またはEvent Gateで決算・TDnet開示・一次確認済み危険ニュースが確認され候補から除外された状態。Rankingへ渡さない。
- `TRADE`: 最終選定（Selection / Absolute Quality Gate）の仕様確定後に生成可能となる、Ranking結果から選ばれる1銘柄の注文案。誰がどのように生成するかは今回確定しない。Selection未実装中は生成しない。Ranking `COMPLETE`やRank 1の存在だけでは生成しない。Risk Engine通過前は採用不可。
- `NO_TRADE`: Event Gateが正常完了し、`PASS`候補・`DATA_UNAVAILABLE`候補がともに0件の場合の結果。Rankingが`COMPLETE`しRank 1が存在する場合も、Selection未実装（Selection未実装である旨を既存フィールドへ記録）として`NO_TRADE`を保存する。注文を作らない。
- `DATA_UNAVAILABLE`: 必要な市場データまたはSource Policyが揃わず、取引判断まで到達していない状態。Event Gateに`DATA_UNAVAILABLE`の候補が1件でも存在する場合を含む。Rankingへ渡さない。
- `REJECTED`: 注文案またはデータが固定条件に違反。値を自動修正しない。
- `NOT_APPLICABLE`: `NO_TRADE`または`DATA_UNAVAILABLE`のため注文リスク評価対象がない。

## データ境界

`runs/`の市場データ・評価・注文案と、`trades/trades.csv`の実取引正本を混在させません。`execution_result.json`は人間が確認した実績をCSVへ反映する前に検証するための日次入力記録であり、証券会社から自動取得した証明ではありません。バックテストを追加する場合も別ディレクトリで管理します。

外部AI API、証券API、SBIログイン、ブラウザによる発注操作は実装対象外です。
