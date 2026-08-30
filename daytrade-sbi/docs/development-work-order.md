# Development Work Order

この文書は、DayTrade Repository における **Development Work Order** の唯一の正本
（SSOT）である。Normative ContractとCanonical Templateの両方をこの1ファイルに収録する。
Contractとtemplateを別ファイルへ分離しない。

Development Work Orderは単なるprompt templateではなく、**Development Process Contract**
である。ChatGPT / Humanが設計を確定してからClaude Codeへ実装を委譲するための正式な
handoff形式であり、要求・test・実装・PR・reviewのtraceabilityを担保する。

この文書はDevelopment専用である。Production Security Boundaryの**runtime上の正本**は
OS Managed PolicyとOS Managed Runtime Guardである。

Development Work Orderは、**通常は既存Production Security Boundaryを緩和してはならない**。
ただしHuman + ArchitectがProduction Security Boundary Changeを明示的に認可した正式
Work Orderでは、そのWork Orderが列挙する**repository-side source変更に限り**実装してよい。

このauthorizationは**installed Production stateへの変更権限を与えない**。Production
deploy / install / replacement / runtime operationは常にHuman-onlyである。

## Requirement Language

Normative requirementには次の語を使う。

| 語 | 意味 |
| --- | --- |
| **MUST** | 例外なく満たさなければならない。満たせない場合は実装せずSTOPする |
| **MUST NOT** | 例外なく行ってはならない |
| **SHOULD** | 正当な理由がない限り満たす。逸脱する場合はCompletion Evidenceへ理由を記す |
| **SHOULD NOT** | 正当な理由がない限り行わない。行う場合は同様に記す |
| **MAY** | 実装者の裁量で選んでよい |

Security Contract / Fail-Closed Contract / Scope / Non-Goals / Implementation Stop
ConditionsにはMUST / MUST NOTを優先して使う。

設計判断が必要な箇所で、次の曖昧表現を**単独で使ってはならない（MUST NOT）**。

```text
適切に / 必要に応じて / いい感じに / 既存実装に合わせて / 問題があれば修正 / 可能なら / など
```

これらを使う場合は、同じ箇所にClaudeが判断可能な具体的境界を併記する。

## Work Order Lifecycle

```text
ChatGPT Project Instructions
        │ pointer only
        ▼
GitHub main : daytrade-sbi/docs/development-work-order.md
        │  （Normative Contract + Canonical Template）
        ▼
ChatGPT      案件ごとの実Work Orderを生成
        ▼
Human        Work Order handoff
        ▼
Claude Development
        ├─ Implementation
        ├─ Tests
        ├─ Commit
        ├─ Safe Push
        └─ Draft PR
        ▼
Claude       Implementation Completion Report生成（Layer 1）
        ▼
Human        Report受領
        ▼
GitHub Actions   CI
        ▼
ChatGPT Review   Latest HEAD / Diff / CI / Report / Security / Fail-Closed を独立確認
        ▼
Review Evidence（Layer 2）
        ▼
APPROVE / REQUEST CHANGES
        ▼
Human Merge
```

実Work Orderはrepositoryへ保存しない。Work Order本文の保管場所はChatGPT / Humanの
handoffであり、repositoryが保持するのは**この形式仕様**である。Implementation
Completion ReportとReview Evidenceもrepository fileとして保存しない。

## Evidence Model

Evidenceは**provenanceの異なる2層**に分離する。両者を同じ概念として扱わない（MUST NOT）。
Claudeの自己申告と、ReviewerがGitHubから独立確認した事実は別物である。

| | Layer 1 | Layer 2 |
| --- | --- | --- |
| 名称 | **Implementation Completion Report** | **Review Evidence** |
| Producer | Claude Code | ChatGPT / Human |
| 性質 | 自己申告（Claudeが自ら実行・確認した結果） | 独立検証 |
| 情報源 | 実装したdiff / 実行したtest / local repository state | GitHub repository state / PR / latest HEAD SHA / diff / GitHub Actions / Layer 1 Report / Security・Fail-Closed評価 |
| 保存先 | Humanへのhandoff（repository fileにしない） | PRに紐づくdurable record（repository fileにしない） |

ChatGPTはLayer 1を**自己申告Evidenceとして扱い**、GitHub上で確認可能な情報を
独立に確認する（MUST）。Layer 1の記述をそのままLayer 2の検証結果として採用しない。

### Layer 1: Implementation Completion Report

Claudeが生成するEvidenceの正式名称は **Implementation Completion Report** とする。
Human-readable Markdownの完成形としてHumanへ返す（MUST）。

最低限次を含む（MUST）。

```text
Work Order ID
Work Order Version
Base SHA
Branch
Final HEAD SHA
PR
Changed Files
Implementation Summary
Acceptance Criteria Results
Tests actually run
Security Contract impact
Fail-Closed Contract impact
Deviations
Unresolved Items
```

Acceptance Criteria Resultの許可値:

```text
PASS
FAIL
BLOCKED
NOT VERIFIED
```

`NOT VERIFIED` は、**Claude自身が確認能力を持たない外部状態**に対して使う。
確認していないGitHub状態・CIを `PASS` や `success` と記載しては
**ならない（MUST NOT）**。GitHub Actions CIをClaude自身が確認できない場合は、

```text
GitHub CI: NOT VERIFIED BY CLAUDE
```

と記載する（MUST）。

**ClaudeがPR bodyへReportを反映できることは要求しない。** Reportの正式なhandoff先は
Humanである。PR bodyはCompletion情報の標準的な表示場所として維持するが、Claudeによる
PR body更新はMUSTではない。

### Layer 2: Review Evidence

ChatGPT / HumanがGitHubから独立確認して作るEvidence。最低限次を表現できることを
Contractとする（MUST）。

```text
Work Order ID
PR Number
Base Branch
Reviewed HEAD SHA
Changed Files
Acceptance Criteria Assessment
CI Assessment
Security Contract Assessment
Fail-Closed Assessment
Regression Assessment
Documentation Assessment
Known Deviations
Unresolved Items
Final Verdict
```

Final Verdictの許可値:

```text
APPROVE
REQUEST CHANGES
```

Review EvidenceのためにJSON / YAML schemaや新しいrepository artifactを作成しては
**ならない（MUST NOT）**。

### Durable Review Record

Human Merge前に、**reviewの対象となったHEADと最終Verdictを後から追跡できる形**で
GitHub上へ残す（MUST）。許可される保存先は次のいずれかである。

- PR body
- PR comment
- GitHub review record

このrecordの作成はChatGPT / Humanの責務であり、**Claude自身にこれらのGitHub metadata
writeを要求しては ならない（MUST NOT）**。Claudeへgh CLIやGitHub API write権限を
付与することでこれを満たそうとしない。

reviewed HEADがreview後に変わった場合、ChatGPTは新HEADを再reviewする（MUST）。

## Responsibility Contract

### ChatGPT / Architect / Reviewer

MUST:

- 現在のrepositoryを確認する
- 必要な外部一次情報を確認する
- 問題を定義する
- Architecture / Scope / Non-Goals / Data Contract / Security Contract /
  Fail-Closed Contractを決定する
- Test CasesとAcceptance Criteriaを定義する
- Work Orderを発行する
- PRをWork Orderに対してreviewする
- reviewにあたり、latest HEAD SHA / diff / CI結果 / repository stateを
  **GitHubから独立に確認する**
- Review Evidenceを作り、Durable Review RecordとしてGitHubへ残す

MUST NOT:

- Architecture決定をClaudeへ丸投げする
- Claudeの自己申告Reportを、独立確認なしに検証済みEvidenceとして扱う
- 自らmergeする

### Claude Code / Implementer

MUST:

- Work Orderとrepository policy（`CLAUDE.md` / `AGENTS.md` / 各docs）を読む
- Work OrderのFIXED decisionに従う
- Scope内で実装する
- testsを追加・実行する
- commit / Safe Push / Draft PRを行う
- **Implementation Completion Report（Layer 1）を完成形Markdownとして生成し、
  Humanへhandoffする**
- 自ら確認できない外部状態を `NOT VERIFIED` として返す

Claudeの実装完了責務は**Report生成とHumanへのhandoffまで**である。PR bodyへの反映や
GitHub Actions結果の最終確認はClaudeの完了条件に含まれない。

MUST NOT:

- FIXED Architectureを再設計する
- **Security Contractを緩和する** — ただし例外が1つだけある（下記）
- **Fail-Closed Contractを緩和する** — こちらは例外なし。generic authorizationでも
  Fail-Closed自体は緩和できない
- Scopeを独断で拡張する
- Production Human-only操作を実行する
- 確認していないGitHub状態・CI結果を `PASS` / `success` と報告する

Security relaxationの唯一の例外は、`Production Security Boundary Change
Authorization: HUMAN + ARCHITECT EXPLICIT` を持つWork Orderで、
`Authorized Repository-Side Files` と `Authorized Relaxations` の**両方へexactに
列挙されたintersection**に収まる変更である。次のいずれかに該当する場合、Security
relaxationを行っては**ならない（MUST NOT）**。

```text
Production Security Boundary Change Authorization = NONE
authorization metadata missing
authorization value invalid
Authorized Repository-Side Files に対象 file がない
Authorized Relaxations に対象 relaxation が exact に存在しない
Human + Architect authorization を確認できない
```

列挙されたintersectionを超えるrelaxationはMUST NOTであり、
[Protected Invariants](#protected-invariants)はこの例外の対象外である。

### Human

MUST:

- ChatGPTとClaude間のWork Order handoffを行う
- 必要なHuman decisionを与える
- Production environmentへの移行を判断する
- 最終Mergeを行う

GitHubへの最終MergeはHuman authorityである。ClaudeもChatGPTもmergeしない。

## Claude Discretion Model

Work Orderは指示ごとに裁量レベルを明示できる。

### FIXED

Architecture / behavior / contractとして確定済み。Claudeは再設計・変更しては
**ならない（MUST NOT）**。Architecture・Security Contract・Fail-Closed semantics・
Data Contractは原則FIXEDとする。

### BOUNDED

Work Orderが明示した制約内で実装詳細を選んで**よい（MAY）**。例: private helperの
名称、test helperの内部構成、既存styleに従う局所的refactor。

ただしobservable behavior / Data Contract / Security Contractを変更しては
**ならない（MUST NOT）**。

### DISCOVERY-ONLY

調査と報告のみ許可する。修正しては**ならない（MUST NOT）**。Scope外defectの扱いに
使う。発見内容はCompletion EvidenceのUnresolved Itemsへ記す。

## Fact / Decision Separation

Work Orderは次の3つを明確に分離する（MUST）。

| 区分 | 意味 | Claudeの扱い |
| --- | --- | --- |
| **Confirmed Facts** | ChatGPTがWork Order作成時に確認した事実 | 実装開始時にrepositoryと矛盾を発見したら、黙って読み替えず報告する |
| **Design Decisions** | ChatGPT / Humanが確定した実装設計 | FIXEDなら自己判断で変更しない |
| **Unverified Items** | 未確認事項 | 実装上の事実として扱わない |

Unverified Itemsが実装に必須の場合、Work Order発行前に解消するか、Implementation
Stop Conditionとして定義する（MUST）。

## Required Metadata

正式Work Orderは最低限次のmetadataを持つ（MUST）。

```text
Work Order ID
Work Order Version
Title
Environment
Repository
Base Branch
Verified Base SHA
Recommended Branch
Implementation Authority
Architecture / Requirements Authority
Merge Authority
```

### Production Security Boundary Change Authorization

Production Security Boundaryへ影響するWork Orderは、上記に加えて次のmetadataを
持たなければならない（MUST）。

```text
Production Security Boundary Change Authorization:
Authorized By:
Authorized Repository-Side Files:
Authorized Relaxations:
Preserved Security Controls:
Production Deployment Authority:
```

`Production Security Boundary Change Authorization`の許可値は次の2つだけである。

| 値 | 意味 |
| --- | --- |
| `NONE` | 通常のWork Order。既存Security Contractを緩和してはならない |
| `HUMAN + ARCHITECT EXPLICIT` | Human + Architectが明示認可したSecurity Boundary変更Work Order |

`HUMAN + ARCHITECT EXPLICIT`の場合、次をすべて満たすこと（MUST）。

- `Authorized By`にHuman authorityとArchitecture authorityの**両方**が確認できる値を書く
  （例: `Human + ChatGPT Architect`）。**Claude Code自身をここへ記載しては ならない（MUST NOT）**
- `Authorized Repository-Side Files`に、変更を許可するrepository内fileを**exactに列挙**する
- `Authorized Relaxations`に、認める緩和を**exactに列挙**する。曖昧な記述は無効
- `Preserved Security Controls`に、維持しなければならない契約を列挙する
- `Production Deployment Authority`は常に`Human only`である

Authorizationが無い、値が不正、または列挙が曖昧な場合、Security Boundaryへ影響する
変更を実装しては**ならない（MUST NOT）**。

### Protected Invariants

次は`Production Security Boundary Change Authorization: HUMAN + ARCHITECT EXPLICIT`
**だけでは緩和できない（MUST NOT）**。generic authorizationの対象外である。

```text
Raw Evidence integrity
SHA256 integrity
Physical Request Record
Exact Logical Attempt Immutability
Trust Chain
Canonical CLI Pipeline Order
Production Human-only operation boundary
Fail-Closed semantics
Safe Sync / Safe Start / Safe Push authority boundary
```

これらはpipelineが主張する事実の裏付けそのものであり、1つのWork Orderの都合で
交換できる性質のものではない。これら自体を変更する必要が生じた場合は、通常の
Security Boundary authorizationでは足りず、**Human + Architectがこの Governance
Contract 自体を変更対象として発行した別の正式Governance Work Order**を必要とする。

Happy Pathを通すためだけの理由でこれらを変更しては**ならない（MUST NOT）**。

### Authorization Non-Retroactivity

`Production Security Boundary Change Authorization`は、**そのauthorizationを含む
正式Work OrderがHumanからClaudeへhandoffされた後に開始されるimplementationにだけ**
適用する（MUST）。

次を後からauthorizationしては**ならない（MUST NOT）**。

```text
既に作成済みの commit
既に push 済みの branch
既に open 済みの PR
既に merge 済みの PR
既に Production へ反映済みの変更
```

Governance変更を、過去のSecurity Boundary変更に対する**retroactive approval**として
扱っては**ならない（MUST NOT）**。既に行われた変更の是非は、その変更自身のreviewで
判断する。authorizationを後付けして正当化しない。

### Work Order ID

Canonical format:

```text
DTWO-YYYY-NNN
```

`NNN`は3桁（例: `DTWO-2026-001`）。v1ではrepository側にID発番管理systemを実装しない。

### Environment

許可値は次だけとする（MUST）。

```text
DEVELOPMENT ONLY
```

Development Work Order内にProduction実行命令を含めては**ならない（MUST NOT）**。
Production操作が必要な場合はImplementation Stop Conditionとして扱う。

### Verified Base SHA

40文字のfull commit SHAを要求する（MUST）。省略SHAをcanonical formとして認めない。
Base SHAはChatGPTがWork Order作成時に確認したrepository stateを示す。

## Required Sections

Canonical Templateは次のsectionを、この順序を基本として含む（MUST）。

```text
 0. Work Order Metadata
 1. 目的
 2. 現状
 3. 確認済み事実
 4. 問題定義
 5. Scope
 6. Non-Goals
 7. Architecture
 8. 変更対象
 9. 変更禁止範囲
10. Data Contract
11. Security Contract
12. Fail-Closed Contract
13. Implementation Stop Conditions
14. Migration / Compatibility
15. Test Cases
16. Acceptance Criteria
17. 実装順序
18. Commit / Push / PR条件
19. Implementation Completion Report
20. 完了報告フォーマット
```

該当する変更が存在しないsectionも**削除してはならない（MUST NOT）**。その場合は
明示的に `変更なし` または `該当なし` と記す。空欄のまま発行しては**ならない（MUST NOT）**。

## Acceptance Criteria / Test Traceability

Acceptance CriteriaにはIDを付与する（MUST）。

```text
AC-01
AC-02
AC-03
```

Test CaseにもIDを付与し、対象Acceptance Criteriaを明示する（MUST）。

```text
TC-01
Covers: AC-01

TC-02
Covers: AC-01, AC-02
```

PR Completion EvidenceはAcceptance Criteria単位で結果を返す（MUST）。

## Security Contract

次はMUSTで維持する。

1. `Production Security Boundary Change Authorization: NONE`のWork Orderは、既存
   Security Contractを緩和しては**ならない**
2. `Production Security Boundary Change Authorization: HUMAN + ARCHITECT EXPLICIT`の
   場合**のみ**、`Authorized Repository-Side Files`および`Authorized Relaxations`へ
   **exactに列挙された変更だけ**を実装してよい
3. 明示認可されていないSecurity relaxationは禁止する
4. Work Orderとrepository Security Contractが衝突し、上記explicit authorizationに
   よって解決されていない場合は`IMPLEMENTATION_BLOCKED`とする
5. Sandbox / Runtime Guard / network / MCP / Trust Chain / Raw Evidence /
   Fail-Closed等は、`Preserved Security Controls`へ記載された契約を維持する
6. **installed Production Security Boundary**（実機の`/etc`）をDevelopment Claudeが
   変更しない
7. Production Human-only commandを実行しない
8. Productionへのdeployment / policy replacement / runtime operationはHuman-onlyとする
9. **Claude自身がauthorizationを作成・拡張・変更してはならない**
10. Happy Path成立だけを理由としてSecurityを緩和しては**ならない**
11. [Protected Invariants](#protected-invariants)はgeneric authorizationでは緩和
    できない。別の正式Governance Work Orderを要する
12. authorizationは[non-retroactive](#authorization-non-retroactivity)であり、
    既存のcommit / branch / PR / merge済み変更を後から正当化しない

`Authorization: NONE`のWork Orderが上位のrepository policyと衝突する場合、
**より厳しい既存policyを維持してSTOPする**（MUST）。

Sandbox / raw Git network operationのallowlist / Safe Sync / Safe Start / Safe Push
Contract / Canonical PipelineのSecurity・Trust Chainは、`Authorized Relaxations`へ
明示列挙されていない限り変更しない（MUST）。

次をClaudeへ付与しては**ならない（MUST NOT）**。Capability mismatchはEvidence handoffで
解決し、権限追加では解決しない。

```text
gh CLI permission
arbitrary GitHub API write
GitHub token
PR auto-edit privilege
PR comment auto-write privilege
auto merge privilege
```

## Fail-Closed Contract

Development Process Contract自体もFail-Closedとする。次の場合、Claudeは推測して
継続しては**ならない（MUST NOT）**。

- Work OrderのFIXED requirement同士が矛盾する
- Work Orderとrepository policyが矛盾する
- Security Contractを満たすとAcceptance Criteriaを満たせない
- Scope外変更なしでは実装不可能
- Production Human-only操作が必要
- 必須外部仕様が未確認
- Required file / API / symbolがWork OrderのConfirmed Factと実態で重大に異なる
- Work Orderが要求するBase前提が安全に成立しない
- Acceptance Criteriaの意味が一意に確定できない
- Final HEAD SHAを取得できずImplementation Completion Reportを成立させられない
- required testsを実行できない

Production Security Boundaryに関しては、次も必ず`IMPLEMENTATION_BLOCKED`とする。

- Production Security Boundaryへ影響するのに
  `Production Security Boundary Change Authorization`が`NONE`
- `Authorized Relaxations`が曖昧で、何を認めたのか一意に確定できない
- 変更しようとするsecurity fileが`Authorized Repository-Side Files`に存在しない
- Work Orderに記載された以上のSecurity relaxationが必要
- Production `/etc`の変更が必要
- Production Runtime operationが必要
- **Human + ArchitectがGovernance Contract自体を変更対象として明示認可した別の正式
  Governance Work Orderが存在しない状態で、Claude自身によるGovernance変更、または
  自己許可のためのGovernance変更が必要**
- Human + Architect authorizationを確認できない

最後の条件が禁じているのは**自己許可**であって、Governance変更そのものではない。
Human + Architectが[Protected Invariants](#protected-invariants)の記載どおりに
Governance Contract自体を変更対象として発行した正式Governance Work Orderがある場合は、
**そのWork Orderのexact scope内でGovernance変更を実装してよい**（この文書自体が
そうした変更で書かれている）。

一方で次は、authorizationの有無にかかわらず常にMUST NOTである。

```text
Claude 自身が authorization を生成する
Claude 自身が authorization を拡張する
Claude 自身が Governance を書き換えて自己許可する
Work Order なしで Protected Invariant を変更する
```

この場合、実装を拡張・再設計せずSTOPする。

### Capability Boundary（Fail-Closedではない）

次は**Capability boundary**であり、これ**だけ**を理由にFail-Closedで停止しては
**ならない（MUST NOT）**。

- Claude自身がPR bodyを編集できない
- Claude自身がGitHub Actions CIを確認できない
- Claude自身がGitHub review recordを書けない

これらはSecurityやFail-Closedを守るために必要な停止ではなく、Development Process
Contractと実際のCapabilityの不一致にすぎない。implementation / tests / commit /
Safe Push / Draft PR / Report生成が正常完了しているなら、作業は完了である。

確認不能な外部状態は、虚偽の `PASS` にせず `NOT VERIFIED` としてhandoffする（MUST）。
Capability mismatchの解決のためにSecurity boundaryを弱めては**ならない（MUST NOT）**。

## Implementation Stop Conditions

Claude側の作業停止状態は、software runtimeのFail-Closedとは別のContractとして扱う。

Canonical status:

```text
IMPLEMENTATION_BLOCKED
```

最低限次を報告する（MUST）。

```text
status: IMPLEMENTATION_BLOCKED
work_order_id:
reason:
affected_section:
confirmed_repository_state:
required_decision:
changes_made_before_stop:
tests_run_before_stop:
```

Claudeはblockerを発見した後、Work Orderを自己修正して作業を再開しては
**ならない（MUST NOT）**。Human / ChatGPTから改訂Work Orderまたは追加指示を受ける。

`IMPLEMENTATION_BLOCKED` の場合、commit / push / PR作成を無理に完了させない（MUST NOT）。

## Data Contract

Work Order自体のdata contractは次のとおり。

- Work Order = Human-readable Markdown（MUST）
- v1ではJSON / YAML schemaを導入しない
- machine-readable schema validationはv1のNon-Goal

Work Orderの導入・改訂は、既存Business Data Contract（Raw Evidence / SHA256 /
source artifacts / market data / ranking / selection / recommendation / risk /
config schema / source matrix schema / strategy schema）を変更しない（MUST NOT）。

## PR Body Contract

PR bodyはCompletion情報の**標準的な表示場所**として維持する。書式は
`.github/pull_request_template.md` に従う。

MUST:

- Work OrderがあるDevelopment PRではWork Order IDを記載する
- Acceptance CriteriaをID単位で報告する
- `PASS` を宣言する場合はtest / code / evidenceのいずれかを示す
- Security / Fail-Closedへの影響を明記する
- Final HEAD SHAはPR作成時点またはReport時点のHEADを記録する

MUST NOT:

- Work OrderからのDeviationが存在するのに `NONE` と偽る
- Claudeが自己判断でDeviationを正当化する
- PR templateへWork Order本文を貼り付ける

**PR bodyへ誰が書くかは能力に依存する。** ClaudeがPR bodyを更新できない環境では、
ClaudeはImplementation Completion ReportをHumanへhandoffし、Human / ChatGPTがPR body・
PR comment・GitHub review recordのいずれかへ反映する。ClaudeがPR bodyを更新できない
ことは、それだけでは実装の未完了を意味しない（Capability Boundaryを参照）。

## Migration / Compatibility

Work Order v1の導入はDevelopment Processの追加であり、既存runtime / pipeline
behaviorとの後方互換性を維持する（MUST）。

- 既存CLI invocationを変更しない
- existing test commandを変更しない
- Safe Git wrapper interfaceを変更しない
- Production commandを変更しない
- Business artifact schemaを変更しない
- Canonical CLI Pipeline Orderを変更しない

既存PRへ遡及的にWork Order準拠を要求しない。v1適用は、この仕様のmerge後に新規発行
されるDevelopment Work Orderからとする。

---

## Canonical Work Order Template

以下をコピーして実Work Orderを作成する。sectionを削除せず、該当しない場合は
`変更なし` / `該当なし` と明記する。

```markdown
# DayTrade Development Work Order

## 0. Work Order Metadata

- Work Order ID: DTWO-YYYY-NNN
- Work Order Version: <version>
- Title: <title>
- Environment: DEVELOPMENT ONLY
- Repository: Kinagaki-525/DayTrade
- Base Branch: main
- Verified Base SHA: <40-char full commit SHA>
- Recommended Branch: claude/<branch-name>
- Implementation Authority: Claude Code
- Architecture / Requirements Authority: ChatGPT / Human
- Merge Authority: Human only
- Production Security Boundary Change Authorization: NONE

<!--
Production Security Boundary へ影響する Work Order だけ、NONE を
HUMAN + ARCHITECT EXPLICIT へ変え、次の 5 行を追加する。曖昧な列挙は無効。

- Authorized By: Human + ChatGPT Architect
- Authorized Repository-Side Files: <exact file list>
- Authorized Relaxations: <exact relaxation list>
- Preserved Security Controls: <exact control list>
- Production Deployment Authority: Human only
-->


## 1. 目的

<この変更が何を達成するか>

## 2. 現状

<現在のrepositoryの振る舞い・構造>

## 3. 確認済み事実

Confirmed Facts（ChatGPTがWork Order作成時に確認した事実）:

- CF-01: <fact>
- CF-02: <fact>

Unverified Items（未確認事項。実装上の事実として扱わない）:

- <item> / 該当なし

## 4. 問題定義

<何が問題か。なぜ今の状態では不十分か>

## 5. Scope

- S-01: <変更内容>
- S-02: <変更内容>

## 6. Non-Goals

- NG-01: <実施しないこと>
- NG-02: <実施しないこと>

## 7. Architecture

Discretion: FIXED

<確定したArchitecture。Claudeは再設計しない>

## 8. 変更対象

Claudeが変更を許可されるfile:

- <path>
- <path>

## 9. 変更禁止範囲

次を変更してはならない（MUST NOT）:

- <path>
- <path>

## 10. Data Contract

Discretion: FIXED

<入出力・schema・artifactの契約。変更なしの場合はその旨を明記>

## 11. Security Contract

Discretion: FIXED

<維持すべきSecurity契約。緩和は禁止>

## 12. Fail-Closed Contract

Discretion: FIXED

<Fail-Closedで停止すべき条件>

## 13. Implementation Stop Conditions

次に該当する場合、実装を継続せず IMPLEMENTATION_BLOCKED として報告する:

- <condition>
- <condition>

## 14. Migration / Compatibility

<後方互換性。変更なしの場合はその旨を明記>

## 15. Test Cases

### TC-01

Covers: AC-01

<検証内容>

### TC-02

Covers: AC-01, AC-02

<検証内容>

## 16. Acceptance Criteria

- AC-01: <満たすべき条件>
- AC-02: <満たすべき条件>

## 17. 実装順序

1. <step>
2. <step>

## 18. Commit / Push / PR条件

Commit前にMUST:

- Scope外変更なし
- 関連tests / full pytest成功
- diff確認済み
- Acceptance Criteriaの自己確認済み

Staging / Push / PR:

- 明示file pathのみを `git add -- <explicit-path>` する
- `daytrade-sbi/scripts/claude-safe-push` だけでpushする
- Draft Pull Requestとして作成し、Humanが明示するまでmergeしない

## 19. Implementation Completion Report

Claudeは完成形MarkdownのImplementation Completion Report（Layer 1）をHumanへ返す。
AC-01以降の各項目を PASS / FAIL / BLOCKED / NOT VERIFIED で報告し、PASSには根拠を付ける。
Claude自身が確認できないGitHub状態・CIは NOT VERIFIED とし、PASS / success と書かない。
PR bodyへ反映できないことは、それだけでは未完了ではない。

## 20. 完了報告フォーマット

Work Order:
Status: COMPLETED / IMPLEMENTATION_BLOCKED
Base SHA:
Branch:
Final HEAD SHA:
PR:
Changed Files:
Implementation Summary:
Acceptance Criteria: AC-XX: PASS / FAIL / BLOCKED / NOT VERIFIED — evidence
Tests actually run:
- related tests:
- full pytest:
- git diff --check:
Security Contract: relaxation NONE / details
Fail-Closed Contract: relaxation NONE / details
Production Impact: NONE
Canonical Pipeline Impact: NONE
Work Order Deviations: NONE / details
Unresolved Items: NONE / details
GitHub CI: VERIFIED / NOT VERIFIED BY CLAUDE
Merge: NOT PERFORMED — Human Merge required
```

## 関連文書

- Development workflow: [development-workflow.md](development-workflow.md)
- Claude Code固有ルール: [../../CLAUDE.md](../../CLAUDE.md)
- Repository全体のAgent契約: [../AGENTS.md](../AGENTS.md)
- PR Completion Evidence template: [../../.github/pull_request_template.md](../../.github/pull_request_template.md)
