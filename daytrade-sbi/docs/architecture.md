# v2 アーキテクチャ

## 責任分界

```text
Codex: Web調査・出典保存・候補比較・TRADE/NO_TRADE/DATA_UNAVAILABLE案
  ↓
Python: データ検証・固定条件スクリーニング・価格計算・Risk Engine
  ↓
人間: 出典と注文案の最終確認・SBI株アプリへの手入力・実績記録
```

Codexの評価は入力データに基づく候補比較であり、利益予測や発注権限ではありません。PythonはAIの代わりに銘柄を評価せず、設定された数値と構造だけを検証します。人間だけが実際の注文を決定・入力します。

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
| `src/screening.py` | 資金条件を適用。未決定条件は未評価として残し、承認済み実装なしの値は拒否 |
| `src/strategy.py` | 前日高値ブレイクの価格計算 |
| `src/risk.py` | AI案から独立した固定リスク検証。値は修正しない |
| `src/reports.py` | PASS・REJECTED・NO_TRADE・DATA_UNAVAILABLEに応じた手動確認レポート |
| `src/recommendations.py` | 取引しなかった日を含む推奨履歴の記録 |
| `src/metrics.py` | 実取引結果の集計 |
| `src/execution.py` | 完全決済済み実績と日次成果物の照合、CSV行生成、重複防止付き追記 |
| `schemas/` | nightly実行で保存するJSONの構造契約 |

`research_window.json`はPythonが確定したTDnet調査対象期間です。TDnetはDiscoveryではなく、Full Candidate Research内のCandidate Contextとして候補単位で確認します。対象日と前営業日の間に週末等の空白がある場合は`post_cutoff_information_status=OUT_OF_SCOPE`を出し、cutoff後情報を未確認0件として扱わないことを示します。

`market_research.json`はDiscoveryとCandidate Researchの経緯を保存する正本で、同じ`research_window`を含めます。Discovery CandidateはYahoo!ファイナンス出来高TOP50と値上がり率TOP50のUnion/Dedupだけで作り、TDnet単独銘柄は追加しません。Universe判定結果は`candidate_research[].universe_status`に保存します。Stage 1除外は、`sources.json`または`source_attempts`に存在する参照で裏付けられた許可済み`stage1_checks`がある場合だけ有効です。資金条件のStage 1除外は固定金額ではなく、設定済みの`capital.total_yen`と`capital.position_size`に対する`capital_limit`として扱います。

`candidate_pipeline.json`はDiscovery Unionを起点に全候補の状態を残す成果物で、`market_data.json`や`candidates.json`が空でも候補を消しません。

`sources.json`は出典台帳の正本で、成功した値の`sources`と、取得不能・未掲載・古い情報を含む`source_attempts`を分けて保存します。保存HTMLはEvidenceとして`source_attempts[].source_page_path`から追跡します。`market_data.json`へ埋め込んだ数値出典や`recommendation.json`の参照URLが台帳に存在しない場合、後続処理へ進みません。

日次ディレクトリには`strategy_snapshot.yaml`を保存します。候補・推奨・Risk Engine結果へ同じ`strategy_version`と設定内容のSHA-256を引き継ぎ、別設定で作られた成果物の混在を拒否します。

## 状態

- `ELIGIBLE`: 市場データ検証と設定済み固定条件を通過。取引推奨ではない。
- `TRADE`: Codexが比較結果として作った1銘柄の注文案。Risk Engine通過前は採用不可。
- `NO_TRADE`: 適切な候補がない正常な結果。注文を作らない。
- `DATA_UNAVAILABLE`: 必要な市場データまたはSource Policyが揃わず、取引判断まで到達していない状態。
- `PASS`: 注文案が固定リスク条件を通過。
- `REJECTED`: 注文案またはデータが固定条件に違反。値を自動修正しない。
- `NOT_APPLICABLE`: `NO_TRADE`または`DATA_UNAVAILABLE`のため注文リスク評価対象がない。

## データ境界

`runs/`の市場データ・評価・注文案と、`trades/trades.csv`の実取引正本を混在させません。`execution_result.json`は人間が確認した実績をCSVへ反映する前に検証するための日次入力記録であり、証券会社から自動取得した証明ではありません。バックテストを追加する場合も別ディレクトリで管理します。

外部AI API、証券API、SBIログイン、ブラウザによる発注操作は実装対象外です。
