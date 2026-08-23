# Development Workflow

この文書は、DayTrade Repository の **Development Claude Code** が Git 操作を行うときの標準手順を定義する。

この仕組みは Development workflow control / Defense in Depth であり、Production Security Boundary ではない。Production の正本は OS Managed Policy と OS Managed Runtime Guard であり、この文書や Repository 内 script で緩和してはならない。

## 標準フロー

新しい作業を始めるときは `daytrade-sbi/` を current working directory とし、次の順序を使う。

1. `scripts/claude-safe-sync-main`
2. `scripts/claude-safe-start claude/<new-branch>`
3. 実装
4. 関連 test
5. Full pytest
6. 変更 file を明示して `git add`
7. `git commit`
8. `scripts/claude-safe-push`
9. Draft Pull Request
10. GitHub Actions `CI / pytest`
11. ChatGPT / Human Review
12. Human Merge

raw `git fetch` / `git pull` / `git push` / `git switch` / `git checkout` / `git ls-remote` を Claude Code から直接実行してはいけない。

## Safe Sync Main

```bash
scripts/claude-safe-sync-main
```

引数は受け取らない。

この command は canonical `origin` の `main` だけを exact refspec で fetch し、local `main` が `origin/main` と同一、または fast-forward 可能な場合だけ成功する。

許可する network fetch は次の意味だけである。

```text
origin refs/heads/main -> refs/remotes/origin/main
```

次の場合は Fail-Closed で停止し、自動修復しない。

- canonical origin を確認できない
- Working Tree が dirty
- merge / rebase / cherry-pick / revert / bisect 中
- detached HEAD
- current branch が `main` / `claude/*` 以外
- local `main` が存在しない
- local `main` が `origin/main` より ahead
- local `main` と `origin/main` が diverged
- fetch / authentication に失敗

`reset` / `rebase` / `pull` / force 操作で修復しない。

## Safe Start

```bash
scripts/claude-safe-start claude/<new-branch>
```

引数は新規 `claude/*` branch 名1件だけ。

Safe Start は Safe Sync Main と同じ契約で `main` を同期した後、その **最新 local main** から新規 branch を作る。既存 local branch または既存 remote branch と同名の場合は拒否する。

新branchは upstream を自動設定しない。

次を行わない。

- 既存branchの上書き
- `switch -C`
- `checkout -B`
- branch削除
- reset
- rebase
- pull
- merge
- push

### Squash Mergeとの関係

この Repository では Squash Merge により、merge 済み feature branch の旧 HEAD が新しい `main` の ancestor にならない場合がある。

したがって Safe Start は、現在いる `claude/*` branch の HEAD が `main` の ancestor かどうかを開始条件にしない。

Safe Start が保証するのは次だけである。

- 現在の作業が clean / commit 済み
- 旧branch refを変更・削除しない
- canonical `main` を安全に同期する
- 新branchを同期済み `main` から作成する

旧branchのPRが本当にmerge済みかどうかは Human / Review process の責務である。

## Safe Push

```bash
scripts/claude-safe-push
```

既存契約を維持する。

- `claude/*` の現在branchだけ
- canonical originだけ
- clean treeだけ
- force / delete / tag / main pushなし
- callerはremote / refspecを指定できない

## GitHub Actions CI

Workflow:

```text
CI
```

Required check候補となるjob名:

```text
pytest
```

Trigger:

- `pull_request` targeting `main`
- `push` to `main`

CIは `daytrade-sbi/.python-version` に固定した Development runtime と同じ Python を使い、`requirements-dev.txt` をinstallして Full pytestを実行する。

CI workflow の権限は `contents: read` のみとする。CIからcommit、push、PR変更、secret作成、Production変更を行わない。

## Python Runtime Contract

2026-08-23 に Development WSL の次の command で確認した runtime を固定する。

```bash
.venv/bin/python --version
```

確認結果:

```text
Python 3.14.4
```

したがって `daytrade-sbi/.python-version` は `3.14.4` とする。

## Humanだけが行う操作

次は Claude Code が自動実行しない。

- Pull Request の最終 merge
- Production Managed Policy の deployment
- strategy threshold の最終承認
- SBI証券への売買注文

## Production非回帰

Safe Sync / Safe Start / Safe Push は Development 専用である。

Production ではこれらの wrapper を許可しない。Production allowedDomains に `github.com` を追加しない。Production Managed Policy / Runtime Guard をこの開発フローのために変更しない。
