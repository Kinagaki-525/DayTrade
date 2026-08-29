# Development Workflow

この文書は、DayTrade Repository の **Development Claude Code** が Git 操作を行うときの標準手順を定義する。

この仕組みは Development workflow control / Defense in Depth であり、Production Security Boundary ではない。Production の正本は OS Managed Policy と OS Managed Runtime Guard であり、この文書や Repository 内 script で緩和してはならない。

## 標準フロー

Development Claude Code は **Git repository root** を current working directory として起動する。起動は `daytrade-sbi/scripts/claude-development` だけを使う。

```text
Approved Development Work Order
  ↓
Safe Sync Main / Safe Start
  ↓
daytrade-sbi/scripts/claude-development
  ↓
Claude は repository root から起動する
  ↓
実装 / 関連 test / Full pytest
  ↓
git add -- <explicit-path>
  ↓
git commit -m "<message>"
  ↓
daytrade-sbi/scripts/claude-safe-push
  ↓
Draft Pull Request + Completion Evidence
  ↓
GitHub Actions `CI / pytest`
  ↓
ChatGPT / Human Review against Work Order
  ↓
Human Merge
```

0. Approved Development Work Order（ChatGPT / Human が発行）
1. `daytrade-sbi/scripts/claude-safe-sync-main`
2. `daytrade-sbi/scripts/claude-safe-start claude/<new-branch>`
3. `daytrade-sbi/scripts/claude-development`（Claude session の起動）
4. 実装
5. 関連 test
6. Full pytest
7. 変更 file を明示して `git add -- <explicit-path> [<explicit-path> ...]`
8. `git commit -m "<message>"`
9. `daytrade-sbi/scripts/claude-safe-push`
10. Draft Pull Request（body は `.github/pull_request_template.md` の Completion Evidence）
11. GitHub Actions `CI / pytest`
12. ChatGPT / Human Review（Work Order の Acceptance Criteria に対して）
13. Human Merge

Development Work Order が発行されている作業では、実装を始める前に Work Order を読み、
その FIXED Design Decision に従う。Work Order の形式仕様と Claude の裁量境界・STOP 契約の
正本は [development-work-order.md](development-work-order.md) である。Work Order は
`CLAUDE.md` / `AGENTS.md` / 既存 Security Contract を緩和できない。矛盾を見つけた場合は
推測して実装を続けず、`IMPLEMENTATION_BLOCKED` として停止する。

Safe Sync / Safe Start / Safe Push wrapper は cwd に依存せず repository を Git 自身から解決するため、repository root からも `daytrade-sbi/` からも同じ契約で動作する。pytest は `daytrade-sbi/pytest.ini` を使うので `cd daytrade-sbi && .venv/bin/python -B -m pytest` として実行する。

raw `git fetch` / `git pull` / `git push` / `git switch` / `git checkout` / `git ls-remote` を Claude Code から直接実行してはいけない。

raw Git は **repository root から 1 Bash call で直接実行**する。Git global option（`-C` / `--git-dir` / `--work-tree` など）、`cd` による cwd 変更、`GIT_DIR` / `GIT_WORK_TREE` などの Git routing 環境変数、`bash -c` 経由の実行は使わない。詳細は「[Canonical Raw Git Contract（FIX-DEV-GIT-011）](#canonical-raw-git-contractfix-dev-git-011)」を参照する。

## Development Launcher

```bash
daytrade-sbi/scripts/claude-development
```

引数は `--dry-run` だけを受け取る。remote / branch / Claude の起動 option を caller が指定することはない。

### なぜ repository root から起動するのか

`daytrade-sbi/` を current working directory として Claude を起動すると、Claude Sandbox の write 許可はその subdirectory に限られ、repository root の `.git` は read-only になる。この状態では `git add` が `.git/index.lock` を作成できず、Development Claude は commit を作れない。

repository root から起動すると、Sandbox を有効にしたまま `git add` と `git commit` の両方が成功する。したがって解決策は「Sandbox を緩めること」ではなく「起動位置を正すこと」である。

### Launcher の契約

- repository root は launcher 自身の source 位置（`<root>/daytrade-sbi/src/claude_development_launcher.py`）から導出し、さらに `git rev-parse --show-toplevel` と一致することを確認する。一致しない場合は Fail-Closed で停止する
- `exec claude` の直前に repository root へ `chdir` する。Human が `daytrade-sbi/` などの subdirectory から呼んでも、Claude の cwd は repository root になる
- 次のいずれかを検出した場合は起動を拒否する（`CLAUDE_DEVELOPMENT_PRODUCTION_RUNTIME`）
  - 環境変数 `DAYTRADE_RUNTIME_PROFILE` が `production`
  - Production runtime marker `/etc/daytrade-production-runtime` が存在する
  - OS Managed Policy `/etc/claude-code/managed-settings.json` が存在する
- Git repository semantics を変更する環境変数が1つでも設定済みなら起動を拒否する
  （`CLAUDE_DEVELOPMENT_GIT_ENVIRONMENT_OVERRIDE`）。対象は `GIT_DIR` / `GIT_WORK_TREE` /
  `GIT_INDEX_FILE` / `GIT_COMMON_DIR` / `GIT_NAMESPACE` / `GIT_OBJECT_DIRECTORY` /
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` / `GIT_CEILING_DIRECTORIES`。値ではなく**設定の有無**で
  判定し、空文字でも拒否する。値を黙って削除して続行しない（Fail-Closed）。
  network_guard は Bash command 文字列しか見ないため、session 起動前から export された
  これらの変数を検出できない。`GIT_WORK_TREE=/etc` が有効な session では、guard が
  repository-relative と読んだ `git add -- passwd` が別の場所へ書き込む
- current branch が `claude/*` でない場合は起動を拒否する（`CLAUDE_DEVELOPMENT_BRANCH_NOT_ALLOWED`）
- Sandbox を無効化しない。`allowUnsandboxedCommands` を変更しない。permission を緩めない
- `.git` に対する `chmod` / `chown` を行わない
- file を1つも書き込まない。`/etc` を変更しない
- raw `git push` を許可しない。push 経路は `daytrade-sbi/scripts/claude-safe-push` のままである

### Branch 契約（`claude/*` 限定）

Development session は commit を作る。したがって launcher は、起動時点で
current branch が `claude/*` であることを要求する。次はすべて Fail-Closed で起動を拒否する。

- `main`
- `claude/` 以外の任意 branch（例: `feature/example`）
- detached HEAD
- branch 名を解決できない場合（`git symbolic-ref --quiet HEAD` が非0）

これにより、Development Claude が local `main` の上で `git add` / `git commit` を行う経路が閉じる。
`claude/*` branch は `daytrade-sbi/scripts/claude-safe-start` で作成する。

この `claude/` prefix は `daytrade-sbi/scripts/claude_safe_git.py` の `BRANCH_PREFIX` と同一であり、
両者が一致することを test で固定する。ただし launcher の判定は Safe Git helper より**厳しい**。
Safe Sync Main は `main` 上でも実行する必要があるため helper は `main` を許容するが、
launcher は `main` を許容しない。

raw `git push` 拒否、Safe Push の既存契約、Sandbox 設定はこの branch 契約によって一切変化しない。

### Production Launcher との関係

`daytrade-sbi/scripts/claude-development` と `daytrade-sbi/scripts/claude-production` は**完全に別物**である。

| | `claude-development` | `claude-production` |
| --- | --- | --- |
| 用途 | Development session | Production nightly |
| 実行者 | Development Human | Human only |
| Preflight | Development runtime 確認のみ | FIX-R2-004 Runtime Security Gate 全体 |
| Production runtime | 起動を拒否する | 必須 |
| 位置付け | Development workflow control | Production 運用 entry point |

`claude-development` の Production 資産に対する契約は次のとおりである。

- Production runtime marker（`/etc/daytrade-production-runtime`）と OS Managed Policy
  （`/etc/claude-code/managed-settings.json`）の**存在有無だけを read-only で参照する**。
  これは Development launcher 自身の起動拒否判定のためであり、それ以外の目的で参照しない
- これらの内容を書き換えない
- Production Managed Policy を deploy・変更しない
- Production Runtime Guard（`ops/claude/daytrade_runtime_guard.py`）を変更しない
- Production Security Boundary を緩和しない
- Production launcher（`daytrade-sbi/scripts/claude-production`）を変更しない

Production では `claude-development` を使わない。

### Human が raw `claude` を起動しない

通常の Development 作業で `daytrade-sbi/` から raw `claude` を起動する手順は正式運用としない。起動位置を誤ると上記の `.git` read-only 問題が再発するため、Development session は必ず `claude-development` から開始する。

## Safe Sync Main

```bash
daytrade-sbi/scripts/claude-safe-sync-main
```

引数は受け取らない。

この command は canonical `origin` の `main` だけを exact refspec で fetch し、local `main` が `origin/main` と同一、または fast-forward 可能な場合だけ成功する。

許可する network fetch は次の意味だけである。

```text
origin refs/heads/main -> refs/remotes/origin/main
```

fetch は `--no-tags --no-recurse-submodules` を固定し、tag取得とsubmodule再帰fetchを行わない。

次の場合は Fail-Closed で停止し、自動修復しない。

- canonical origin を確認できない
- fetch URL / push URL がcanonical URL 1件だけではない
- `remote.origin.mirror` が有効
- `remote.origin.vcs` が設定され、custom remote helper経路になり得る
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
daytrade-sbi/scripts/claude-safe-start claude/<new-branch>
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
daytrade-sbi/scripts/claude-safe-push
```

既存の単一ref push契約を維持する。

- `claude/*` の現在branchだけ
- canonical originだけ
- fetch URL / push URLはcanonical URL 1件だけ
- `remote.origin.mirror` を拒否
- `remote.origin.vcs` を拒否し、custom remote helper経路を許可しない
- noncanonical URLを診断に出す場合もuserinfo credentialは除去する
- Git stderrのauthentication/helper詳細をwrapper errorへ転記しない
- clean treeだけ
- force / delete / tag / main pushなし
- callerはremote / refspecを指定できない

## Local Git 操作と network_guard

`.claude/hooks/network_guard.py` は raw Git を allowlist 方式で判定する。local 専用 subcommand だけを許可し、未知の subcommand・alias・network subcommand はすべて Fail-Closed で拒否する。

### Canonical Raw Git Contract（FIX-DEV-GIT-011）

Development Claude が実行する raw Git は、**repository root からの直接実行**だけである。

```text
1 Bash call = 1 direct git command
```

許可する形式は `git <subcommand> ...` だけであり、git executable の次の token は
subcommand そのものでなければならない。executable の綴りは `git` でも `/usr/bin/git` でもよい。

```bash
git status
git diff
git branch --show-current
git add -- CLAUDE.md
git add -- daytrade-sbi/src/cli.py
git restore --staged -- CLAUDE.md
git commit -m "message"
```

network_guard の path validator は repository root（`CLAUDE_PROJECT_DIR`）を基準に operand を
判定する。一方 Git 側は global option・cwd・環境変数によって、自分が使う git-dir / work-tree /
cwd を移動できる。移動されると「guard が検査した path」と「Git が実際に書き込む path」が
別物になり、明示 file 契約が成立しない。したがって execution context は検査するのではなく
**固定する**。

**Git global option はすべて Fail-Closed で拒否する。** 正式 Development workflow に
global option は不要であり、将来の Git が追加する option を無害と仮定しないためである。

```bash
git -C <dir> ...
git --git-dir <dir> ... / git --git-dir=<dir> ...
git --work-tree <dir> ... / git --work-tree=<dir> ...
git --namespace ...
git --exec-path ...
git --super-prefix ...
git --attr-source ...
git -c <key>=<value> ... / git --config-env=...
git --no-pager ...        # 無害な option も個別に許可しない
```

shell 側の execution context も同じ理由で固定する。次はすべて拒否する。

```bash
GIT_WORK_TREE=/etc git add -- passwd
GIT_DIR=/tmp/x git status
env GIT_WORK_TREE=/etc git add -- passwd
cd daytrade-sbi && git add -- src/cli.py
bash -c "git add -- CLAUDE.md"
sh -c "git commit -m message"
git-add -- CLAUDE.md          # 分割 executable も canonical form ではない
```

`add` / `restore` / `commit` は repository root から 1 Bash call で直接実行する。
`cd daytrade-sbi && .venv/bin/python -B -m pytest` のように git を含まない command は
この契約の対象外であり、従来どおり実行できる。

network subcommand（`push` / `fetch` / `pull` / `clone` / `ls-remote` / `send-pack`）の拒否、
alias bypass の拒否、未知 subcommand の Fail-Closed は一切緩めない。

### 許可する unstage

```bash
git restore --staged -- <path>
```

index だけを HEAD の状態へ戻す形式のみを許可する。次はすべて拒否する。

- `git restore <path>`（working tree を上書きする）
- `git restore --worktree ...` / `git restore -W ...`
- `git restore --staged <path>`（`--` path separator が無い曖昧な形式）
- `git restore --staged --source=<commit> -- <path>`（HEAD 以外から復元する）
- `git restore --staged --patch -- <path>`
- `--staged` 以外の option を伴う任意の形式
- `--` の後の path が下記 path validator を満たさない形式
  （`git restore --staged -- :/` / `:` / `src/..` / `daytrade-sbi` など）

`git restore` 全体を allowlist しない。上記の1形式だけである。

### 許可する stage（`git add`）

```bash
git add -- <explicit-file> [<explicit-file> ...]
```

`--` は必須であり、`--` の後に1つ以上の **明示 file path** が必要である。`--` の前に option を置けない。
stage 対象を command に書かれた file 以上へ広げる形式はすべて Fail-Closed で拒否する。

- `git add <path>`（`--` が無い曖昧な形式）
- `git add .` / `git add -- .`
- `git add -A` / `git add --all`
- `git add -u` / `git add --update`
- `git add -p` / `git add --patch`
- `git add -i` / `git add --interactive`
- `git add -N ...` / `git add --intent-to-add ...`
- `git add *` / `git add -- <glob>`（`*` `?` `[` `]` を含む path）

### path validator（`git add` / `git restore --staged` 共通）

Git pathspec は file 名ではなく言語である。`--` の後には magic（`:/` = repository 全体、
`:` = 現在の prefix、`:(top)` / `:(exclude)...` / `:(glob)...`）が書け、path 正規化
（`src/..` / `src/.` / 末尾 `/`）や directory 名だけでも stage 範囲は同じだけ広がる。
今回の正式契約は「明示 file 単位の stage」であり、両 command の operand に同一の
validator を適用する。次はすべて Fail-Closed で拒否する。

- `:` で始まる path（pathspec magic）: `:/` / `:` / `:(top)` / `:(exclude)foo` / `:!foo`
- `-` で始まる path（option に読める）
- absolute path: `/etc/passwd` / `C:\...`
- path component としての `.` / `..`: `src/..` / `src/../` / `src/.` / `../outside.txt`
- 先頭 `/`・重複 `//`・末尾 `/`（directory 指定）
- glob（`*` `?` `[` `]`）と backslash 区切り
- 実在する directory: `git add -- daytrade-sbi` / `git add -- daytrade-sbi/src`
- realpath が repository 外へ出る path（symlink 経由を含む）

削除済み tracked file を stage する契約は維持する。「disk 上に存在しない」ことは、それ自体
だけでは拒否理由にしない。disk 上に無い operand は read-only の `git ls-files` で index と
照合し、**入力 path と完全一致する tracked entry がちょうど1件**の場合だけ許可する
（FIX-DEV-GIT-012）。

| disk | `git ls-files` | 判定 |
| --- | --- | --- |
| 通常 file として存在 | — | ALLOW |
| 存在しない | 入力 path と一致する 1件（削除済み tracked file） | ALLOW |
| 存在しない | 0件（untracked かつ未作成） | DENY |
| 存在しない | 2件以上（wholesale 削除された directory） | DENY |
| 存在しない | 1件だが入力 path と不一致 | DENY |
| 存在しない | 照合不能（git が答えられない） | DENY |

`git add -- definitely-not-existent-development-file` は「明示 file ちょうど1件」ではないので
DENY である。

### shell expansion / command substitution

network_guard が見るのは command **文字列**であり、実際に git が受け取る argv は
その後の shell 展開で決まる。したがって guard が検査した path / message と git が受け取る
argv が異なる形式は許可しない。raw Git invocation に次が含まれる場合は Fail-Closed で拒否する。

- command substitution: `$(...)` / `` `...` `` / `<(...)`（git に限らず command 全体で拒否）
- parameter expansion: `$VAR` / `${VAR}`
- glob / brace: `*` `?` `[` `]` `{` `}`
- 先頭の `~`（tilde expansion）

拒否例:

```bash
git add -- "$(printf ':/' )"
git add -- "$PATH"
git commit -m "$(cat /etc/passwd)"
git commit -m "$HOME"
git commit -m *
git commit -m `cat file`
```

command substitution の中身を解析して「中身が無害なら許可」とする方式は廃止した。
`printf` も `cat` も禁止 pattern を含まないまま pathspec magic や任意 file の内容を
生成できるため、中身の検査では守れない。形式そのものを拒否する。

なお shlex は判定時点で quote を除去済みであり、`"*"` と `*` を区別できない。
両方とも Fail-Closed で拒否する。この結果 commit message は上記の文字を含められない
canonical form に狭まるが、message は言い換えが可能な唯一の operand である。

### 許可する commit

Development の契約は「明示 path で `git add --` → 既に index にある内容だけを、
message 付きで commit する」である。したがって `git commit` は、commit 自身が追加の file を
stage / 選択する形式、既存 commit を書き換える形式、および session が応答できない
入力待ち（editor / stdin）に入る形式を許可しない。option は allowlist 方式で判定し、
未知の option は Fail-Closed で拒否する。

許可する形式:

```bash
git commit -m "normal message"
git commit --message="message"
git commit --message "message"
```

拒否する形式:

- `git commit`（message 未指定。editor 経路になる）
- `git commit -F <file>` / `git commit --file=<file>`（任意の readable file を
  commit message へ取り込める。commit message 誤検知は修正済みで、Development 正式
  workflow に message file は不要）
- `git commit -F -` / `git commit --file=-`（stdin 待ちになる）
- `git commit -a ...` / `git commit --all ...`（index 外の変更を巻き込む）
- `git commit --amend ...`（既存 commit の書き換え）
- `git commit --fixup=<commit>` / `git commit --squash=<commit>`
- `git commit -o ...` / `git commit --only ...`
- `git commit -i ...` / `git commit --include ...`
- `git commit -- <path>` / `git commit <path>`（pathspec commit）
- `--allow-empty` / `--no-verify` を含む、上記 allowlist 外の option すべて

### 許可する branch 操作

```bash
git branch --show-current
git branch --list
```

`git branch` は inspection だけを許可する。branch の作成・削除・rename・copy・force 移動・
upstream 設定は Development Claude が行わない。branch 作成は
`daytrade-sbi/scripts/claude-safe-start` だけが担当する。

拒否する形式:

- `git branch <name>`（作成）
- `git branch -d ...` / `git branch -D ...`
- `git branch -f ...` / `git branch --force ...`
- `git branch -m ...` / `git branch -M ...` / `git branch --move ...`
- `git branch -c ...` / `git branch -C ...` / `git branch --copy ...`
- `git branch --set-upstream-to=...`
- 引数なしの `git branch`、および `--show-current` / `--list` 以外の任意の形式（曖昧な形式は Fail-Closed）

commit message は引き続き **data** として扱う。message 中に `git push` / `git fetch` /
`git metadata` が含まれても command として解析しない。ただし `bash -c` / `sh -c` は
message 中にあっても command として解析し、拒否する。command substitution と
shell expansion は解析せずに形式そのものを拒否する（上記「shell expansion /
command substitution」）。

### Commit message は command として解析しない

network_guard は「shell が実際に command として評価する位置」だけを再帰解析する。

- shell の `-c` operand（`bash -c "..."` / `sh -c "..."`）は command として解析する
- command substitution（`$(...)` / backtick / `<(...)`）は、commit message の中にあっても command として解析する
- それ以外の argument は data として扱う

したがって次は許可される。

```bash
git commit -m "chore: development git metadata acceptance"
git commit -m "fix git push regression"
```

一方、次は引き続き拒否される。

```bash
bash -c "git push origin claude/example"
sh -c "git fetch origin"
git commit -m "$(git push origin claude/example)"
```

`git push` / `git fetch` / `git pull` / `git clone` / `git ls-remote` / `git send-pack` の拒否、`git -c alias.*` などの alias bypass 拒否、未知 subcommand の Fail-Closed は一切緩めない。

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

`claude-development` / Safe Sync / Safe Start / Safe Push は Development 専用である。

Production ではこれらの wrapper を許可しない。`claude-development` は Production runtime を検出した時点で起動を拒否する。Production allowedDomains に `github.com` を追加しない。Production Managed Policy / Runtime Guard をこの開発フローのために変更しない。
