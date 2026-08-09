# Nightly Research Prompt

翌営業日の日本株デイトレ候補を調査し、v2の実行記録を作成してください。

## 必須ルール

1. 最初に `AGENTS.md`、`config/strategy.yaml`、`TODO.md` を全文読む。
2. PythonからLLM APIや市場データAPIを呼び出さない。市場調査はCodex自身がWebで行う。
3. 翌営業日を公式な取引日情報で確認し、`runs/YYYY-MM-DD/` を対象日として使用する。
4. 株価、出来高、呼値、決算予定、適時開示などの事実は、一次情報または信頼性の高い市場データで確認する。
5. 検索結果の断片だけを価格決定の根拠にしない。
6. 各数値について `source_name`、`source_url`、`retrieved_at`、`trading_date`、`ticker`、`field_name`、`value` を保存する。
7. 日付不明、更新時刻不明、古いデータ、出典間の矛盾、必須値欠落は推測で解決しない。対象から除外するか `NO_TRADE` とする。
8. `screening`の`null`値をCodexの判断で補完しない。
9. 必ず銘柄を選ぶ必要はない。`NO_TRADE`は正常な結果として扱う。
10. `previous_day_high_breakout`以外の戦略を追加しない。
11. Risk Engineを必ず実行し、`REJECTED`になった値を修正して無理に`PASS`へ変えない。
12. SBI証券へのログイン、画面操作、注文送信は行わない。
13. AI評価や戦略の有効性、翌日の上昇を断定しない。

## 保存先

対象日のディレクトリへ次を保存する。

```text
runs/YYYY-MM-DD/
  strategy_snapshot.yaml
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
```

以降の`screen-market`と`risk-check`では、このスナップショットを`--config`へ指定する。

## 実行手順

### 1. 調査

- 資金50,000円・100株の固定条件から、買い上限がおおむね500円以下となり得る日本株を調査対象にする。
- 前営業日の始値、高値、安値、終値、出来高、前日終値、前日高値、適用する呼値を確認する。
- 決算予定、適時開示、重要ニュース、流動性に関する確認結果を保存する。
- `sources.json`を出典台帳、`market_data.json`を構造化された確認データとして作成する。

### 2. 事実と評価の分離

`research.md`には最低限、次の見出しを設ける。

```markdown
## 確認できた事実

## データ上の不確実性・欠落

## Codexによる比較評価
```

事実には出典を付け、評価には断定を避けた比較理由を書く。取得していない情報を評価理由に使わない。

### 3. Pythonによる市場データ検証と固定条件スクリーニング

対象日を`YYYY-MM-DD`へ置き換えて実行する。

```powershell
py -B -m src.cli validate-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --output runs/YYYY-MM-DD/market_validation.json
py -B -m src.cli screen-market --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/candidates.json
```

`REJECTED`の銘柄を候補に戻さない。`ELIGIBLE`は固定条件を通過した意味だけであり、利益見込みを意味しない。

### 4. Codexによる候補比較

- `ELIGIBLE`銘柄だけを比較する。
- 取得済みデータ、決算・開示確認、流動性、ギャップなどを根拠として順位を付ける。
- 1銘柄を`TRADE`候補にするか、適切な候補がなければ`NO_TRADE`とする。
- 未決定の固定閾値を新設しない。
- `recommendation.json`へ判断、理由、参照URLを保存する。`TRADE`の参照URLは1件以上とし、すべて`sources.json`にも記録する。
- `strategy_version`と`config_sha256`は`candidates.json`からそのまま転記し、生成・推測しない。

### 5. Risk Engineとレポート

Risk Engine実行前に、対象日開始時点の保有数と対象日の取引済み回数を人間へ確認する。確認できない場合は0と推測せず、`NO_TRADE`または作業停止として記録する。以下の`<確認済み保有数>`と`<確認済み当日取引数>`を確認値へ置き換える。

```powershell
py -B -m src.cli risk-check --recommendation runs/YYYY-MM-DD/recommendation.json --candidates runs/YYYY-MM-DD/candidates.json --market-data runs/YYYY-MM-DD/market_data.json --sources runs/YYYY-MM-DD/sources.json --config runs/YYYY-MM-DD/strategy_snapshot.yaml --output runs/YYYY-MM-DD/risk_result.json --current-positions <確認済み保有数> --trades-today <確認済み当日取引数>
py -B -m src.cli render-report --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json --output runs/YYYY-MM-DD/recommendation.md
py -B -m src.cli record-recommendation --recommendation runs/YYYY-MM-DD/recommendation.json --risk-result runs/YYYY-MM-DD/risk_result.json
```

- `TRADE`かつRisk Engineが`PASS`の場合だけ、`recommendation.md`をSBI手入力候補として提示する。
- `REJECTED`の場合は拒否理由をそのまま報告する。
- `NO_TRADE`の場合は注文値を作らず、理由を記録する。
- `order_submitted`、`entry_triggered`、`entry_filled`は人間が確認するまで空欄にする。

## 最終報告

- 対象日
- `TRADE`、`NO_TRADE`、`REJECTED`のいずれか
- 選定または見送り理由
- Risk Engine結果
- 欠落・矛盾したデータ
- 作成ファイル
- 人間がSBI画面で確認すべき事項

を簡潔に報告する。発注済み、約定見込み、利益見込みとは表現しない。
