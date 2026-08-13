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
