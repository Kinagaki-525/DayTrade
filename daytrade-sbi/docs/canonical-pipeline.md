# Canonical Pipeline Order

このリポジトリの夜間実行は、**この順序**でCLIを逐次実行します。すべてのドキュメント
（`AGENTS.md` / `README.md` / `docs/architecture.md` / `docs/nightly-operation.md` /
`prompts/nightly_research.md` / `.agents/skills/prepare-daytrade-plan/SKILL.md`）は、
この1つの順序だけを正本として参照します。順序を変える場合はここを変更し、
`tests/test_documentation_consistency.py` を通してから他のドキュメントを更新します。

## 役割分担（Agent / Python / Human）

| 主体 | 責務 |
| --- | --- |
| Agent（Codex / Claude Code） | Orchestration（CLIを正しい順序で実行し、結果を報告する）と、**ローカル保存済み生ページ**に対する非構造イベント分類（Event AI Classification）だけ |
| Python | 決定論的なSource取得（curl GET）・Parse・Validation・Screening・Event Gate・Ranking・Selection・Recommendation・Risk Engine |
| Human | Issuer Domainの承認、Threshold Pairの承認、最終的な発注判断 |

CodexとClaude Codeは**同じリポジトリCLIパイプライン**を使います。どちらも
Web調査で市場データを取得しません。数値はすべてPythonのcurl GET→生バイト保存
（SHA256付き）→決定論的Parserを通ります。Agentが`market_data.json`や
`recommendation.json`を手で書くことはありません。

## Canonical CLI Pipeline Order

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

手順7・12・14の "market_data reflect" は、対応する`acquire-*`コマンドが
`--run-dir`の`market_data.json`へ自動的に反映します。Agentが別途書き込む工程では
ありません。

## 候補集合の由来

`acquire-*`コマンドに`--ticker`はありません。どの銘柄がネットワークアクセスを
受けるかは、ディスク上の成果物から決定論的に導出されます。

| コマンド | 候補集合の由来 |
| --- | --- |
| `acquire-discovery` | なし（Discoveryが候補universeを作る側） |
| `acquire-stage1-sources` | `market_research.json` の `discovery_candidates` |
| `acquire-stage2-market-sources` | Stage 1 `PASS` 候補 |
| `acquire-actual-turnover` | Stage 2 対象集合（= Stage 1 `PASS`） |
| `acquire-event-sources` | `candidates.json` / `candidate_pipeline.json` の `status=ELIGIBLE` かつ `screening_status=PASS` |

### Stage 1: 上場確認とStrategy eligibilityの分離

`acquire-stage1-sources`は`JPX_CALENDAR` / `JPX_LISTED_COMPANY` /
`JPX_FOREIGN_STOCK_LIST` / `JPX_TRADING_UNIT`を取得します。

- TSE Listing Batch Gateは従来どおりall-or-nothing。全candidateの
  `JPX_LISTED_COMPANY`が`FOUND`でなければ`TSE_LISTING_BATCH_GATE_FAILED`
- ETF等も東証上場が確認できればListingは`FOUND`。サポート外商品を
  Listing `NOT_FOUND`にはしない
- `apply-stage1`のeligibilityは`security_type` → `share_unit` → `capital_limit`の順。
  `DOMESTIC_COMMON_STOCK`以外は`SECURITY_TYPE_UNSUPPORTED`でREJECT、
  非100単位は既存の`SHARE_UNIT_NOT_100`でREJECT
- `security_type`が決定できない候補はPASSさせず、Stage 1未完了のまま停止する

詳細: [source-acquisition.md](source-acquisition.md)

## Claude Code Executor: Runtime Security Gate（FIX-R2-004）

Business Canonical Pipelineそのものは、どのAgentで実行しても同一であり、Claude依存では
ありません。Claude Codeを**Production executor**として使う場合にだけ、パイプラインの前に
Runtime Security Gateが挟まります。

```
Runtime Security Gate  →  Business Canonical Pipeline
```

- Runtime Security Gate = OS Managed Policy（`/etc/claude-code/managed-settings.json`）と
  OS Managed Runtime Guard（`/etc/claude-code/daytrade-runtime-guard.py`）、および
  `scripts/claude-production` Preflight。
- Production Security Boundaryの**正本はOS Managed Policy**であり、プロジェクトの
  `.claude/settings.json`ではありません。`.claude/settings.json`と
  `.claude/hooks/network_guard.py`はDevelopment用のDefense in Depthとして残しますが、
  `allowManagedHooksOnly: true`のProductionでは実行されません。
- Managed Policyのsandbox allowlistは`config/source_matrix.yaml`と
  `config/issuer_domain_registry.yaml`から決定論的に導出されます
  （`src/claude_runtime_security.derive_expected_domains`）。Hostを手で書き足す運用は
  ありません。
- 手順の詳細は[docs/nightly-operation.md](nightly-operation.md)を参照してください。

### Executor-specific command rendering

Business Canonical Pipeline Order（上記25 step）は、どのexecutorでも同一です。変わるのは
**commandのrendering**だけです。

- このドキュメント・`prompts/nightly_research.md`・`.agents/skills/prepare-daytrade-plan/SKILL.md`の
  `config/source_matrix.yaml`・`runs/YYYY-MM-DD/...`は、executor非依存の**論理パス表記**です。
- Claude Production executorは、同じCanonical Pipelineを、Runtime Guardの契約に従って
  **具体的なabsolute pathへmaterializeしてから**実行します。さらにBash Tool 1回につき
  canonical CLI commandを1個だけ実行します（**1 Bash call = 1 canonical CLI command**）。
  正本は[docs/nightly-operation.md](nightly-operation.md)の
  **Production Path Materialization Contract**と**Production 1-call-1-command Contract**。
- Canonical Pipeline Orderそのものと、Production command renderingを混同しないでください。
  path materializationはstage順序・引数の意味・業務ロジックを一切変えません。
