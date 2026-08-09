# Nightly Research Prompt

翌営業日の日本株デイトレ候補を調査し、v2の実行記録を作成してください。

## 必須ルール

1. 最初に `AGENTS.md`、`config/strategy.yaml`、`TODO.md` を全文読む。
2. PythonからLLM APIや市場データAPIを呼び出さない。市場調査はCodex自身が固定Source Matrixに沿ってWebで行う。
3. 翌営業日を公式な取引日情報で確認し、`runs/YYYY-MM-DD/` を対象日として使用する。
4. `config/source_matrix.yaml`に定義されたSource ID、Role、Criticalityを正とし、実行時にSource Matrix外のサイトを代替採用しない。
5. 検索結果の断片だけを価格決定の根拠にしない。
6. 各数値について `source_ref`、`source_id`、`source_role`、`information_type`、`source_status`、`source_name`、`source_url`、`retrieved_at`、`trading_date`、`ticker`、`field_name`、`value` を保存する。
7. 日付不明、更新時刻不明、古いデータ、出典間の矛盾、必須値欠落は推測で解決しない。`DATA_UNAVAILABLE`、`CONFLICT`、`SINGLE_SOURCE_ONLY`、`STALE`などとして記録する。
8. `screening`の`null`値をCodexの判断で補完しない。
9. 必ず銘柄を選ぶ必要はない。`NO_TRADE`は正常な結果、`DATA_UNAVAILABLE`は取引判断未到達として扱う。
10. `previous_day_high_breakout`以外の戦略を追加しない。
11. Risk Engineを必ず実行し、`REJECTED`になった値を修正して無理に`PASS`へ変えない。
12. SBI証券へのログイン、画面操作、注文送信は行わない。
13. AI評価や戦略の有効性、翌日の上昇を断定しない。
14. Morning Researchは標準フローに含めない。

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
  research.md
  recommendation.json
  recommendation.md
  risk_result.json
```

JSONの構造は `schemas/` を参照する。確認不能な値は`null`にし、架空の値や例示値を実行記録へ書かない。

対象日を確定した直後に設定を保存する。

```powershell
py -B -m src.cli snapshot-config --output runs/YYYY-MM-DD/strategy_snapshot.yaml
py -B -m src.cli validate-source-matrix --source-matrix config/source_matrix.yaml
py -B -m src.cli resolve-research-window --target-date <対象日YYYY-MM-DD> --previous-trading-day <前営業日YYYY-MM-DD> --runs-dir runs --source-matrix config/source_matrix.yaml --output runs/<対象日YYYY-MM-DD>/research_window.json
```

以降の`screen-market`と`risk-check`では、このスナップショットを`--config`へ指定し、市場データ検証系コマンドでは`--source-matrix config/source_matrix.yaml`と`--market-research runs/YYYY-MM-DD/market_research.json`を指定する。

## 実行手順

### 1. Market Discovery

- Discovery経路は `VOLUME_RANKING`、`PRICE_GAIN_RANKING`、`TIMELY_DISCLOSURE` の3つだけに限定する。
- Yahoo!ファイナンスの出来高ランキングTOP50と値上がり率ランキングTOP50は、Universe未確定のため市場フィルタ`ALL_MARKETS`を使用し、実際に使用したフィルタを保存する。
- TDnetは `resolve-research-window` が出力した `research_window.window_start` から `research_window.window_end` までを確認する。`FIRST_RUN` は設定済みの24時間初回補完期間であり、それだけを理由に `DATA_UNAVAILABLE` にしない。`HISTORY_INVALID` の場合は初回補完せず停止する。
- Discovery候補を銘柄コード単位でUnionし、`research_window.json`の `research_cutoff` と `research_window`、`discovered_by`、発見理由をすべて`market_research.json`へ保存する。
- Discovery順位や表示値は、最終Rankingの評価値として使わない。

```powershell
py -B -m src.cli validate-market-research --market-research runs/YYYY-MM-DD/market_research.json --research-window runs/YYYY-MM-DD/research_window.json --source-matrix config/source_matrix.yaml --output runs/YYYY-MM-DD/market_research_validation.json
```

### 2. Candidate Research

- Discovery Candidatesについてのみ、銘柄基本情報、前営業日OHLCV、呼値、決算予定、適時開示、関連ニュースをSource Matrix順に確認する。
- OHLCVはYahoo!ファイナンスをPrimary、株探をSecondaryとし、同一対象日のOpen/High/Low/Close/Volumeが一致した場合だけ`VERIFIED`とする。
- Secondary取得不能は`SINGLE_SOURCE_ONLY`、値不一致は`CONFLICT`、対象日違いは`STALE`として保存し、未確認値を補完しない。
- Source試行は成功・失敗とも`sources.json`の`source_attempts`へ保存し、成功値は`sources`へ保存する。
- `sources.json`を出典台帳、`market_data.json`を構造化された確認データとして作成する。

### 3. 事実と評価の分離

`research.md`には最低限、次の見出しを設ける。

```markdown
## Discovery結果

## Source Audit

## データ欠落・矛盾

## Rankingに使わなかったDiscovery情報

## Codexによる比較評価
```

事実には出典を付け、評価には断定を避けた比較理由を書く。取得していない情報を評価理由に使わない。

### 4. Pythonによる市場データ検証と固定条件スクリーニング

対象日を`YYYY-MM-DD`へ置き換えて実行する。

```powershell
py -B -m src.cli validate-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --output runs/YYYY-MM-DD/market_validation.json
py -B -m src.cli screen-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/candidates.json
```

`REJECTED`の銘柄を候補に戻さない。`ELIGIBLE`は固定条件を通過した意味だけであり、利益見込みを意味しない。
`DATA_UNAVAILABLE`は市場データ不足により取引判断へ到達していない状態であり、通常の`NO_TRADE`とは区別する。

### 5. Codexによる候補比較

- `ELIGIBLE`銘柄だけを比較する。
- 取得済みデータ、既存ルールで利用可能な情報だけを根拠として順位を付ける。
- 1銘柄を`TRADE`候補にするか、適切な候補がなければ`NO_TRADE`、必要データが揃わなければ`DATA_UNAVAILABLE`とする。
- 未決定の固定閾値を新設しない。
- `recommendation.json`へ判断、理由、参照URLを保存する。`TRADE`の参照URLは1件以上とし、すべて`sources.json`にも記録する。
- `strategy_version`と`config_sha256`は`candidates.json`からそのまま転記し、生成・推測しない。

### 6. Risk Engineとレポート

`TRADE`の場合だけ、Risk Engine実行前に、対象日開始時点の保有数と対象日の取引済み回数を人間へ確認する。確認できない場合は0と推測せず作業停止する。以下の`<確認済み保有数>`と`<確認済み当日取引数>`を確認値へ置き換える。

```powershell
py -B -m src.cli risk-check --recommendation runs/YYYY-MM-DD/recommendation.json --candidates runs/YYYY-MM-DD/candidates.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/risk_result.json --current-positions <確認済み保有数> --trades-today <確認済み当日取引数>
py -B -m src.cli render-report --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --output runs/YYYY-MM-DD/recommendation.md
py -B -m src.cli record-recommendation --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json
```

`NO_TRADE`または`DATA_UNAVAILABLE`の場合は、人間へ保有数・当日取引数を確認しない。入力値なしで次を実行し、`NOT_APPLICABLE`を生成する。

```powershell
py -B -m src.cli risk-check --recommendation runs/YYYY-MM-DD/recommendation.json --candidates runs/YYYY-MM-DD/candidates.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --source-matrix config/source_matrix.yaml --market-research runs/YYYY-MM-DD/market_research.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/risk_result.json
py -B -m src.cli render-report --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --output runs/YYYY-MM-DD/recommendation.md
py -B -m src.cli record-recommendation --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json
```

- `TRADE`かつRisk Engineが`PASS`の場合だけ、`recommendation.md`をSBI手入力候補として提示する。
- `REJECTED`の場合は拒否理由をそのまま報告する。
- `NO_TRADE`または`DATA_UNAVAILABLE`の場合、Risk Engine結果は`NOT_APPLICABLE`として扱う。
- `NO_TRADE`の場合は注文値を作らず、理由を記録する。
- `DATA_UNAVAILABLE`の場合は注文値を作らず、調査不能理由を記録する。
- `order_submitted`、`entry_triggered`、`entry_filled`は人間が確認するまで空欄にする。

## 最終報告

- 対象日
- `TRADE`、`NO_TRADE`、`DATA_UNAVAILABLE`、またはRisk Engine `REJECTED`
- 選定または見送り理由
- Risk Engine結果
- 欠落・矛盾したデータ
- 作成ファイル
- 人間がSBI画面で確認すべき事項

を簡潔に報告する。発注済み、約定見込み、利益見込みとは表現しない。
