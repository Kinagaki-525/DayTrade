# Development Workflow

この文書は、DayTrade Repository の **Development Claude Code** が Git 操作を行うときの
標準手順を定義する。

ここに書かれているのは **Layer C: Local Operational Governance** である。個人所有の
開発マシン上の作業手順であって、DayTrade の Business 結果を信用してよいかを決める
境界ではない。その境界は Layer A（Raw Evidence / SHA256 / Source Ledger / Trust Chain）
と Layer B（Screening から Risk Engine まで）にあり、そちらは fail-closed のままである。

## 標準フロー

Development Claude Code は **Git repository root** を current working directory として
起動する。repository root から raw `claude` を起動しても、`daytrade-sbi/scripts/claude-development`
を使ってもよい（後者は cwd を揃えるだけの convenience wrapper である）。

```text
Approved Development Work Order
  ↓
git switch main / git pull --ff-only origin main / git switch -c claude/<branch>
  ↓
実装 / 関連 test / Full pytest
  ↓
git add -- <explicit-path>
  ↓
git commit -m "<message>"
  ↓
git push -u origin claude/<branch>
  ↓
Draft Pull Request
  ↓
Implementation Completion Report（Claude → Human handoff）
  ↓
GitHub Actions `CI / pytest`
  ↓
ChatGPT / Human Review against Work Order（latest HEAD / diff / CI を独立確認）
  ↓
Human Merge
```

コマンドで書くと次のとおり。すべて **repository root から** 実行する。

```bash
git switch main
git pull --ff-only origin main
git switch -c claude/<new-branch>

# 実装 / 関連 test
cd daytrade-sbi && .venv/bin/python -B -m pytest

git add -- <explicit-path> [<explicit-path> ...]
git commit -m "<message>"
git push -u origin claude/<new-branch>
```

pytest は `daytrade-sbi/pytest.ini` を使うので `cd daytrade-sbi` してから実行する。

## 禁止する Git 操作

通常の Git 操作は許可されているが、次は許可されない。

```text
git push origin main       # main への直接 push
--force / --force-with-lease / -f / +refspec   # force push
remote branch の削除
tag の push
history rewrite を目的とする push
```

`main` への反映は常に **Human Merge** である。Claude も ChatGPT も merge しない。

`git add` は変更した file を明示して行う（`git add -- <path>`）。`git add .` や
`git add -A` は、意図しない file を巻き込んだまま commit する典型的な事故なので使わない。
これは強制ではなく **推奨** である。

## Development Launcher（optional）

```bash
daytrade-sbi/scripts/claude-development
```

引数は `--dry-run` だけを受け取る。やることは1つだけで、repository root を解決して
そこへ `chdir` してから `exec claude` する。

### なぜ repository root から起動するのか

`daytrade-sbi/` を current working directory として Claude を起動すると、Claude Sandbox の
write 許可はその subdirectory に限られ、repository root の `.git` は read-only になる。
この状態では `git add` が `.git/index.lock` を作成できず、commit を作れない。

repository root から起動すると、Sandbox を有効にしたまま `git add` と `git commit` の
両方が成功する。したがって解決策は「Sandbox を緩めること」ではなく「起動位置を正すこと」である。

### Launcher の契約

- repository root は launcher 自身の source 位置（`<root>/daytrade-sbi/src/claude_development_launcher.py`）
  から導出し、さらに `git rev-parse --show-toplevel` と一致することを確認する。
  一致しない場合だけ Fail-Closed で停止する（`CLAUDE_DEVELOPMENT_REPOSITORY_ROOT_UNRESOLVED`）
- branch / runtime profile / `/etc` の状態 / `GIT_*` 環境変数は**一切見ない**（DTWO-2026-026 で廃止）
- Sandbox を無効化しない。permission を緩めない。`.git` に `chmod` / `chown` しない
- file を1つも書き込まない

## Legacy compatibility utilities

```text
daytrade-sbi/scripts/claude-safe-sync-main
daytrade-sbi/scripts/claude-safe-start claude/<new-branch>
daytrade-sbi/scripts/claude-safe-push
```

この3つは **optional legacy compatibility utility** である。DTWO-2026-026 より前は
必須の Git workflow だったが、現在は標準 workflow でも必須 checkpoint でもない。

依然として動作し、それぞれ canonical origin・`claude/*` branch・clean tree・
同名 refspec だけという固い契約を持つので、使いたければ使ってよい。

- `claude-safe-sync-main`: `origin/main` を exact refspec で fetch し、local `main` が
  fast-forward 可能なときだけ更新する
- `claude-safe-start claude/<name>`: 同期後の `main` から新 branch を `--no-track` で作る
- `claude-safe-push`: 現在の `claude/*` branch を `HEAD:refs/heads/<同名>` へ push する。
  force / delete / tag / `main` push の形は構造的に作れない

## network_guard（Business Evidence Acquisition Bypass Guard）

`.claude/hooks/network_guard.py` は PreToolUse hook として Bash command 文字列を検査し、
**市場データ取得の迂回**だけを拒否する。

拒否対象:

```text
curl / wget / Invoke-WebRequest / Invoke-RestMethod
requests / httpx / urllib.request / socket への直接アクセス
python -c / py -c / node -e によるネットワークアクセス
nc / netcat / telnet / ssh / scp / ftp
gh
pip install / npm install
```

Git traffic は検査しない（DTWO-2026-026）。`git fetch` / `git pull` / `git push` /
`git switch` / `git checkout` はすべて通常どおり実行できる。

Source Acquisition CLI 内部で `src/source_fetch.py` が起動する curl subprocess は
**子プロセス**であり、PreToolUse hook の視界に入らないため影響を受けない。

この hook は Defense in Depth であって Business Security Boundary ではない。市場データの
URL 検証を実際に行うのは `src/network_policy.py` である。

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

CIは `daytrade-sbi/.python-version` に固定した Development runtime と同じ Python を使い、
`requirements-dev.txt` をinstallして Full pytestを実行する。

CI workflow の権限は `contents: read` のみとする。CIからcommit、push、PR変更、secret作成、
Production変更を行わない。

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

## Work Order

Development Work Order が発行されている作業では、実装を始める前に Work Order を読み、
その FIXED Design Decision に従う。形式仕様・Claude の裁量境界・STOP 契約・
Validation Gate Placement Contract の正本は
[development-work-order.md](development-work-order.md) である。

Evidence は provenance の異なる 2 層に分かれる。Claude が生成するのは
**Implementation Completion Report**（自己申告）までで、GitHub の latest HEAD / diff /
CI を独立確認して **Review Evidence** を作るのは ChatGPT / Human の責務である。Claude が
PR body を更新できないことや、GitHub Actions の結果を自分で確認できないことは
Capability boundary であり、それだけで実装を `IMPLEMENTATION_BLOCKED` にはしない。
確認できない外部状態は虚偽の `PASS` にせず `NOT VERIFIED` として handoff する。

## Humanだけが行う操作

次は Claude Code が自動実行しない。

- Pull Request の最終 merge
- Production への rollout と Production host 上の操作
- strategy threshold の最終承認
- SBI証券への売買注文

## Production非回帰

`claude-development` と legacy wrapper 群は Development 専用である。Production session
から GitHub 操作を行うことを正式 Nightly workflow へ追加しない。Production の起動と
運用は [nightly-operation.md](nightly-operation.md) の Human-only 手順に従う。
