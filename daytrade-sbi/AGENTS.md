# AGENTS.md

このリポジトリは、日本株デイトレードについて、CodexがWeb市場調査・出典保存・イベント分類(決算・TDnet等)を行い、PythonがValidation・Hard Screening・Event Gate・Ranking・Selection・Recommendation Builder・Risk Engineを決定論的に実行し、人間が最終的な発注判断とSBI証券への手動入力を行うプロジェクトです。CodexやPythonのどちらも候補比較や最終選定の主体ではありません。候補の順位付けと選定は`src/ranking.py`・`src/selection.py`の決定論的ロジックだけが行い、AIが独自に比較・評価して`TRADE`を作ることはありません。

## 絶対禁止

- 実際の証券口座へ注文を送信するコードを追加しない
- SBI証券への自動ログインを実装しない
- SBI証券の画面操作やブラウザ発注を自動化しない
- OpenAI API、Anthropic API、Gemini API、その他LLM APIをPythonから呼び出さない
- LLM Provider抽象化やAPIキー管理を追加しない
- `config/strategy.yaml`の固定リスク条件を勝手に変更しない
- [TODO.md](TODO.md)の未決定値を推測して設定しない
- 未確認の株価、呼値、取引日、決算、開示、ニュース、約定、損益を生成しない
- Risk Engineの`REJECTED`を回避するためにAI案を都合よく変更しない
- 実取引結果とバックテスト結果を混同しない
- AI評価や売買戦略の有効性、将来の値上がりを断定しない
- `rules/versions/`に保存済みの版ファイルを変更・削除しない
- サブエージェントに日次成果物、設定、取引CSVを変更させない
- 人間の明示確認前に`trades/trades.csv`へ実績を追記しない
- SelectionはRank 1だけを見る。Rank 1がSelection RejectまたはRisk REJECTEDになった場合でも、Rank 2以下へフォールバックしない
- `config/strategy.yaml`の`selection.enabled`、`selection.rules.minimum_turnover_yen.threshold_yen`、`selection.rules.maximum_relative_tick_size.threshold_ratio`を推測で変更しない（未決定のまま`false`/`null`を維持する）
- Calibrationツールは`config/strategy.yaml`へ書き込まない。「最適な閾値」を推奨・自動適用しない。損益最適化を行わない
- Calibrationツールは`regression/`・`tests/`のフィクスチャを読み取らない。出力は`runs/`・`trades/`配下に書き込まない
- `recommendation.json`をagentが手で作成・編集しない。必ずPython CLI（Case A/Bは`build-ranking-terminal-recommendation`、Case Cは`build-selection`→`build-selection-recommendation`）が生成したものだけを使う
- Ranking `COMPLETE`かつ`selection.enabled=false`（Case B、較正待ち）またはRanking `DATA_UNAVAILABLE`（Case A）の場合も、日次結果を口頭で記録するだけでなく必ず`build-ranking-terminal-recommendation`を実行して`recommendation.json`を生成する
- Config v6でSelectionが有効なCase C（`build-selection`／`build-selection-recommendation`／`risk-check`）では、Selection/Recommendation自身のSHA256ハッシュチェーンが一致しているだけでは不十分。`build-selection`・`build-selection-recommendation`・`risk-check`はそれぞれ独立に、共有ヘルパー（`src/ranking_trust.py`の`load_and_verify_ranking_trust_chain`）を通じて`ranking.json`の上流Provenance（`event_gate.json`・`candidates.json`・`market_data.json`・`sources.json`・source matrix・`strategy_snapshot.yaml`）そのものを再検証してから初めてSelection/Recommendationの内容を信用する。したがって`--ranking`・`--selection`に加えて`--event-gate`・`--candidates`・`--market-data`・`--sources`・`--source-matrix`（`build-selection-recommendation`はさらに`--research-window`、`risk-check`のCase Cはさらに`--event-gate`・`--research-window`）が必須であり、agentが`ranking.json`を書き換えたうえで、その改ざん済み`ranking.json`から自己整合的な`selection.json`/`recommendation.json`を再生成しても、Case Cの有効な結果は得られない
- Config v6のCase A/B（`recommendation.schema_version=1`、`decision`が`NO_TRADE`または`DATA_UNAVAILABLE`）も`risk-check`が独立にRankingとその上流Artifact全体のTrust Chainを再検証する。`--ranking`・`--event-gate`・`--research-window`が必須で、`ranking.input_hashes`の完全一致・Ranking Contract関数の再実行・Terminal Recommendationの決定論的再計算まで行う。agentが`recommendation.json`を手で作成して`risk-check`へそのまま渡しても、Case A/Bであれ有効な結果は得られない
- Selection Calibrationが`ranking.json`をObservationとして採用する前に、その上流アーティファクト一式（`event_gate.json`・`candidates.json`・`market_data.json`・`sources.json`・source matrix・`strategy_snapshot.yaml`）を、Ranking CLI自身が使う実際のContract関数で完全に再検証する。コホート一致だけで内容未検証の`ranking.json`はObservationとして数えない

## Codexの役割

- Codex自身がWeb調査エージェントとして市場データを調査する
- 一次情報または信頼性の高い市場データを優先する
- 検索結果の断片だけを価格決定の根拠にしない
- `config/source_matrix.yaml`に存在しない情報源を実行時に勝手に代替採用しない
- Discoveryは出来高ランキングと値上がり率ランキングに限定し、Discovery順位をそのままRankingへ使わない
- RankingはPython(`src/ranking.py`)だけが実行し、AIによる順位付けやRank変更を行わない
- `estimated_turnover`をRankingの入力に使わない。実際の売買代金だけを使う
- Ranking `COMPLETE`だけではTRADE可能を意味しない。Rank 1をSELECTED/NO_TRADEへ変換するのはSelection（`src/selection.py`、`build-selection`）だけであり、Codex/main agentが独自に比較して`TRADE`を作ることはない。`selection.enabled`がfalse、または閾値未較正の間は`build-selection`を実行しない
- 株価等の数値ごとに情報源、URL、取得日時、取引日、銘柄、値を保存する
- 更新日不明、古いデータ、必須値欠落、出典矛盾を推測で解決しない
- 確認できた事実とCodex評価を明確に分ける
- 固定条件通過銘柄を取得済み情報だけで比較する
- 1銘柄の`TRADE`案、`NO_TRADE`、または`DATA_UNAVAILABLE`を記録する
- 必要な市場データが揃わない場合は`DATA_UNAVAILABLE`として`NO_TRADE`と区別する
- 適切な候補がなければ`NO_TRADE`を正常結果として扱う
- Python Risk Engineを必ず実行する
- 注文案を、注文済み・約定見込み・利益見込みとは表現しない
- Skillはメインエージェントが実行し、`prepare`の読み取り調査・監査だけをサブエージェントへ委譲する。日次成果物の書き込みと`record`は委譲しない

## Pythonの役割

- 市場データと出典の形式・必須値・日付・数値整合性を検証する
- 設定済みの固定条件だけでスクリーニングする
- 前日高値ブレイクの価格を`Decimal`で計算する
- 資金、数量、価格関係、呼値、想定損失、保有数、取引回数を検証する
- AI案と設定から再計算した価格が一致するか検証する
- 違反値を修正せず`REJECTED`を返す
- SBI手入力候補レポートと実績集計を生成する
- 完全約定・当日決済済みの実績入力を日次成果物と照合し、重複なしで記録する
- 実現損益が未確認の場合は計算で補完せず、未計算として扱う
- Web接続やLLM判断をPythonへ組み込まない

## 人間の役割

- Codexが保存した出典と市場データを最終確認する
- SBI株アプリの実画面、注文種別、執行条件、注文期間を確認する
- 注文するかを最終判断する
- SBI証券へのログイン、注文入力、訂正、取消を行う
- 注文提出・発動・約定と実現損益を事実に基づいて記録する

## 正本

- v2設定: [config/strategy.yaml](config/strategy.yaml)
- Source Matrix: [config/source_matrix.yaml](config/source_matrix.yaml)
- 未決定事項: [TODO.md](TODO.md)
- nightly手順: [prompts/nightly_research.md](prompts/nightly_research.md)
- 翌営業日Skill: [prepare-daytrade-plan](../.agents/skills/prepare-daytrade-plan/SKILL.md)
- 実績記録Skill: [record-daytrade-result](../.agents/skills/record-daytrade-result/SKILL.md)
- Codexサブエージェント: `../.codex/agents/`
- JSON構造: `schemas/`
- 日次調査とAI評価: `runs/YYYY-MM-DD/`
- 推奨履歴: `trades/recommendations.csv`
- 実取引事実: `trades/trades.csv`
- 実取引CSV定義: `docs/trade-data-dictionary.md`
- 過去設定: `rules/versions/`

## 変更手順

- まず関連コード、設定、TODO、テストを確認する
- nightly成果物は`schemas/`で検証し、対象日・銘柄・戦略バージョン・設定SHA-256が一致しないファイルを組み合わせない
- `market_data.json`と`recommendation.json`が参照する出典は`sources.json`に存在することを確認する
- 日次計算には`runs/YYYY-MM-DD/strategy_snapshot.yaml`を使い、現在の設定で過去成果物を暗黙に再計算しない
- 売買ルール改善は提案に留め、ユーザー承認後だけ変更する
- 未決定のスクリーニング値は`null`のまま扱う
- ユーザー承認後に戦略ルールを変更する場合も、戦略バージョン規則が決まるまでは命名を独断で決めない
- 実取引行には、ユーザーが提示または確認した事実だけを記録する
- `execution_result.json`は実績入力の確認用記録とし、証券会社の証明や未確認事実として扱わない
- 未約定、一部約定、未決済、`NO_TRADE`、`DATA_UNAVAILABLE`、`REJECTED`を`trades/trades.csv`へ記録しない
- 推奨履歴の実行状態更新と一部約定の記録方法は、`TODO.md`で決まるまで自動化しない
- バックテストを追加する場合は`runs/`と`trades/`から分離する
- コード変更時は可能な限りテストを追加・更新する
- 完了前に`py -B -m pytest`を実行し、既存テストを壊したまま終了しない
