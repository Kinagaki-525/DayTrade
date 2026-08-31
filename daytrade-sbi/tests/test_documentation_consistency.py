"""FIX-015 / FIX-016: the documentation must describe ONE pipeline.

Docs drift silently, and a stale sentence like "Codex does the web market
research" is not a cosmetic problem here: it tells the next operator to do
exactly the thing the Phase I rework removed. So the role model and the
canonical CLI order are asserted, not just written down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent

CANONICAL_DOC = PROJECT_ROOT / "docs/canonical-pipeline.md"

DOCS = {
    "AGENTS.md": PROJECT_ROOT / "AGENTS.md",
    "README.md": PROJECT_ROOT / "README.md",
    "TODO.md": PROJECT_ROOT / "TODO.md",
    "docs/architecture.md": PROJECT_ROOT / "docs/architecture.md",
    "docs/nightly-operation.md": PROJECT_ROOT / "docs/nightly-operation.md",
    "docs/canonical-pipeline.md": CANONICAL_DOC,
    "docs/source-acquisition.md": PROJECT_ROOT / "docs/source-acquisition.md",
    "prompts/nightly_research.md": PROJECT_ROOT / "prompts/nightly_research.md",
    "SKILL.md": REPO_ROOT / ".agents/skills/prepare-daytrade-plan/SKILL.md",
}

#: The **Business Canonical Dependency Order** (FIX-016): the business
#: dependencies -- which artifact needs which -- that every high-level document
#: repeats. This is the coarse layer of one SSOT, not a competing order: the
#: Detailed Nightly Execution Sequence below refines it.
BUSINESS_CANONICAL_DEPENDENCY_ORDER = (
    "snapshot-config",
    "validate-source-matrix",
    "resolve-research-window",
    "acquire-discovery",
    "init-candidate-research",
    "acquire-stage1-sources",
    "market_data Stage1 reflect",
    "apply-stage1",
    "TSE Listing Batch Gate",
    "plan-stage2-batches",
    "acquire-stage2-market-sources",
    "market_data Stage2 reflect",
    "acquire-actual-turnover",
    "market_data turnover reflect",
    "validate-market",
    "screen-market",
    "build-candidate-pipeline",
    "acquire-event-sources",
    "Event AI Classification (local only)",
    "merge-event-source-extraction",
    "init/complete event-research",
    "validate-event-research",
    "build-event-gate",
    "build-ranking",
    "Case A/B/C",
)

#: DTWO-2026-025. The **Detailed Nightly Execution Sequence**: the complete order
#: the canonical doc renders. It refines the Business Canonical Dependency Order
#: above with the validation and reporting stages a nightly actually runs -- it
#: adds steps, and never reorders a business dependency.
DETAILED_NIGHTLY_EXECUTION_SEQUENCE = (
    "snapshot-config",
    "validate-source-matrix",
    "resolve-research-window",
    "acquire-discovery",
    "init-candidate-research",
    "acquire-stage1-sources",
    "market_data Stage1 reflect",
    "apply-stage1",
    "TSE Listing Batch Gate",
    "plan-stage2-batches",
    "acquire-stage2-market-sources",
    "market_data Stage2 reflect",
    "acquire-actual-turnover",
    "market_data turnover reflect",
    "validate-market-research",
    "validate-market",
    "audit-official-ohlcv",
    "screen-market",
    "build-candidate-pipeline",
    "build-performance",
    "render-research",
    "acquire-event-sources",
    "Event AI Classification (local only)",
    "merge-event-source-extraction",
    "init/complete event-research",
    "validate-event-research",
    "build-event-gate",
    "build-ranking",
    "Case A/B/C（Selection / Recommendation）",
    "risk-check",
    "render-report",
    "render-daily-report",
    "validate-run-artifacts",
)

#: Documents that must spell out the whole canonical order.
ORDER_DOCS = (
    "README.md",
    "docs/architecture.md",
    "docs/nightly-operation.md",
    "docs/canonical-pipeline.md",
    "prompts/nightly_research.md",
    "SKILL.md",
)

#: Claims the Phase I rework made false. None may survive anywhere.
FORBIDDEN_CLAIMS = (
    "CodexがWeb市場調査",
    "CodexによるWeb市場調査",
    "Codex自身がWeb調査エージェント",
    "Web市場調査、出典保存",
    "VS Code上のCodex",
    "Codexが毎晩従う調査",
    "market_researcher",
    "Codexが保存した出典",
    "Web調査で得たSource Attempt",
    "sourced web research",
)


def _text(name: str) -> str:
    path = DOCS[name]
    assert path.is_file(), f"{name} is missing at {path}"
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body under ``heading``, up to the next heading of any level."""
    assert heading in text, f"missing section heading: {heading}"
    body = text[text.index(heading) + len(heading) :]
    following = re.search(r"^#{1,6} ", body, re.MULTILINE)
    return body[: following.start()] if following else body


def test_every_documented_file_exists():
    for name, path in DOCS.items():
        assert path.is_file(), f"{name} is missing at {path}"


@pytest.mark.parametrize("name", sorted(DOCS))
@pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
def test_no_document_still_claims_the_agent_fetches_market_data(name, claim):
    assert claim not in _text(name), f"{name} still claims: {claim}"


# Layer 1 of the order contract: the business dependencies, as every
# high-level document repeats them.
@pytest.mark.parametrize("name", ORDER_DOCS)
def test_business_canonical_dependency_order_is_identical_everywhere(name):
    """Each business dependency appears, in order, in every document."""
    text = _text(name)
    positions = []
    for step in BUSINESS_CANONICAL_DEPENDENCY_ORDER:
        index = text.find(step)
        assert index != -1, f"{name} does not mention business step {step!r}"
        positions.append(index)
    assert positions == sorted(positions), (
        f"{name} lists the Business Canonical Dependency Order out of order"
    )


def _canonical_order_entries() -> list[str]:
    """The numbered Detailed Nightly Execution Sequence, as rendered.

    Anchored on its own heading and bounded by the next heading, so neither
    prose nor line numbers are part of the contract -- only the ordered entries.
    """
    text = CANONICAL_DOC.read_text(encoding="utf-8")
    section = text.split("### Detailed Nightly Execution Sequence", 1)[1]
    section = re.split(r"\n#{2,4} ", section, maxsplit=1)[0]
    numbered = re.findall(r"^(\d+)\.\s+(.+?)\s*$", section, flags=re.MULTILINE)
    assert [int(index) for index, _ in numbered] == list(
        range(1, len(numbered) + 1)
    ), "the detailed sequence is not numbered contiguously from 1"
    return [entry.strip("`") for _, entry in numbered]


# Layer 2 of the order contract: the exact sequence the nightly executes.
def test_canonical_doc_renders_the_detailed_nightly_execution_sequence():
    assert _canonical_order_entries() == list(DETAILED_NIGHTLY_EXECUTION_SEQUENCE)


# The two layers have two different lengths, and the canonical document had
# called the 33-entry list by the 25-entry layer's name. Naming one layer with
# the other's count is how a reader concludes there is a single 33-step
# "business" order -- the exact confusion the two-layer contract exists to
# prevent -- so the counts are pinned to the names.
BUSINESS_DEPENDENCY_STEP_COUNT = 25
DETAILED_EXECUTION_STEP_COUNT = 33


def test_the_two_layers_have_their_contracted_lengths():
    assert len(BUSINESS_CANONICAL_DEPENDENCY_ORDER) == BUSINESS_DEPENDENCY_STEP_COUNT
    assert len(DETAILED_NIGHTLY_EXECUTION_SEQUENCE) == DETAILED_EXECUTION_STEP_COUNT
    assert len(_canonical_order_entries()) == DETAILED_EXECUTION_STEP_COUNT


@pytest.mark.parametrize(
    "phrase",
    [
        "Business Canonical Dependency Order（25 dependency step）",
        "Detailed Nightly Execution Sequence（33 execution step）",
    ],
)
def test_the_canonical_doc_names_each_layer_with_its_own_count(phrase):
    assert phrase in _text("docs/canonical-pipeline.md"), phrase


def test_no_document_calls_the_business_order_a_33_step_order():
    """The exact drift: '33 step' attached to the business layer's name."""
    misnomer = re.compile(r"Business Canonical[^\n]{0,40}33\s*step")
    for name in sorted(DOCS):
        found = misnomer.search(_text(name))
        assert found is None, (
            f"{name} calls the Business Canonical Dependency Order a 33-step "
            f"order: {found.group(0)!r}"
        )


def test_the_executor_rendering_note_distinguishes_the_two_layers():
    """Only the command rendering is executor-specific -- not the order."""
    section = _section(
        _text("docs/canonical-pipeline.md"), "### Executor-specific command rendering"
    )
    assert "Detailed Nightly Execution Sequence（上記33 step）" in section
    assert "Business Canonical Dependency Order（25 dependency step）の相対順序も同一" in section
    assert "commandのrendering" in section


# The two layers are one SSOT, so layer 2 must refine layer 1, never contradict
# it: adding validation and reporting stages may not reorder a business
# dependency that every other document repeats.
def _refines(entry: str, step: str) -> bool:
    """Does this detailed entry render ``step``?

    A prefix alone is not enough: ``validate-market`` is a prefix of
    ``validate-market-research``, which is a different stage. The entry must
    either be the step or extend it past a token boundary, the way
    ``Case A/B/C（Selection / Recommendation）`` extends ``Case A/B/C``.
    """
    if entry == step:
        return True
    if not entry.startswith(step):
        return False
    tail = entry[len(step)]
    return not (tail.isalnum() or tail in "-_/")


def test_the_detailed_sequence_preserves_the_business_dependency_order():
    order = list(DETAILED_NIGHTLY_EXECUTION_SEQUENCE)
    positions = []
    for step in BUSINESS_CANONICAL_DEPENDENCY_ORDER:
        matches = [
            index for index, entry in enumerate(order) if _refines(entry, step)
        ]
        assert len(matches) == 1, (
            f"the detailed sequence must refine {step!r} exactly once, "
            f"found {len(matches)}"
        )
        positions.append(matches[0])
    assert positions == sorted(positions), (
        "the detailed sequence reorders a business dependency"
    )
    assert len(order) >= len(BUSINESS_CANONICAL_DEPENDENCY_ORDER)


@pytest.mark.parametrize(
    "name", ["AGENTS.md", "README.md", "docs/architecture.md", "docs/canonical-pipeline.md"]
)
def test_the_three_way_role_split_is_stated(name):
    """Agent = orchestration + local classification, Python = everything
    deterministic, Human = approvals and the final order decision."""
    text = _text(name)
    assert "Orchestration" in text
    assert "Python" in text
    for human_duty in ("Issuer Domain", "Threshold Pair"):
        assert human_duty in text, f"{name} does not state the human duty {human_duty}"


def test_codex_is_still_referenced_as_a_supported_agent():
    """Codex references are not deleted -- Codex and Claude Code simply use the
    same repository CLI pipeline."""
    for name in ("AGENTS.md", "README.md", "docs/canonical-pipeline.md"):
        text = _text(name)
        assert "Codex" in text, f"{name} dropped its Codex reference"
    assert (REPO_ROOT / ".codex" / "config.toml").is_file()


def test_every_acquisition_cli_is_documented_with_its_candidate_source():
    """The docs must say where each stage's candidate set comes from, because
    'the agent supplies it' is precisely what is no longer true."""
    text = _text("docs/canonical-pipeline.md")
    for command in (
        "acquire-discovery",
        "acquire-stage1-sources",
        "acquire-stage2-market-sources",
        "acquire-actual-turnover",
        "acquire-event-sources",
    ):
        assert command in text
    assert "--ticker" in text, "the docs must state that --ticker does not exist"


def test_documented_commands_all_exist_in_the_cli():
    from src import cli

    parser = cli.build_parser()
    choices = set(parser._subparsers._group_actions[0].choices)  # noqa: SLF001
    documented = {
        step
        for step in BUSINESS_CANONICAL_DEPENDENCY_ORDER
        if re.fullmatch(r"[a-z0-9-]+", step) and step != "market_data"
    }
    missing = sorted(step for step in documented if step not in choices)
    assert missing == [], f"documented but non-existent CLI command(s): {missing}"


# ------------------------------- DTWO-2026-026: the guard that remains ---


def test_the_project_network_guard_is_not_deleted():
    assert (REPO_ROOT / ".claude" / "hooks" / "network_guard.py").is_file()


# --------------------------------------------- FIX-PRD-002 / FIX-PRD-004 ---
#
# Three contracts only. These pin *meaning* the runtime enforces, not
# wording: each one, if silently dropped from the docs, would let an
# operator reach a conclusion the code refuses to support.


#: How each document states "Discovery incomplete is not a NO_TRADE". The
#: wording differs (AGENTS.md and the nightly prompt are Japanese, the Skill
#: is English), so each document is pinned by its own phrase rather than one
#: shared string -- and by the denial itself, not merely by both terms
#: appearing somewhere in the file.
DISCOVERY_NOT_NO_TRADE = {
    "AGENTS.md": "は`NO_TRADE`ではない",
    "prompts/nightly_research.md": "は`NO_TRADE`ではない",
    "SKILL.md": "not** `NO_TRADE`",
}


def test_discovery_incomplete_is_documented_as_not_a_no_trade():
    """An unbuildable candidate universe is a stop, not a daily decision."""
    for name, denial_phrase in DISCOVERY_NOT_NO_TRADE.items():
        text = _text(name)
        assert "DISCOVERY_INCOMPLETE" in text, name
        assert denial_phrase in text, name


def test_docs_forbid_hand_writing_the_terminal_artifacts():
    """Recommendation / Risk artifacts come from the Builders, never an agent.

    The mere presence of the three words would pass trivially, so the whole
    prohibition sentence is pinned: an agent may not hand-write
    ``recommendation.json`` / ``risk_result.json`` to fill in a daily result
    after Discovery stopped the pipeline.
    """
    text = _text("AGENTS.md")
    assert (
        "agentが`recommendation.json`・`risk_result.json`を手書きして"
        "日次結果を補完してはいけない"
    ) in text, "AGENTS.md no longer forbids hand-writing the terminal artifacts"
    assert (
        "Discovery未完了でPipelineが停止した場合、後続のRecommendation / "
        "Risk Builderを実行して日次結果を新規生成・補完しない"
    ) in text, "AGENTS.md no longer ties the prohibition to Discovery Fail-Closed"


def test_network_audit_ssot_is_the_physical_request_record():
    """FIX-PRD-004: network_requests/, not source_attempts[], is the SSOT.

    Pinned by the sentence that states it, not by the two nouns appearing
    somewhere in the file -- and the superseded ``attempt_id``-as-budget-key
    claim is pinned as absent, in the doc and in the implementation alike.
    """
    text = _text("docs/source-acquisition.md")
    assert (
        "Network Auditの正本は`runs/<target_date>/network_requests/*.json`の"
        "Physical Request Recordであり、\n`sources.json.source_attempts`ではない"
    ) in text, "docs/source-acquisition.md no longer names the Network Audit SSOT"
    assert (
        "`len(source_attempts)`はPhysical Request数ではない" in text
    ), "docs/source-acquisition.md no longer separates Logical from Physical"
    # The superseded claim -- attempt_id as the Request Budget key -- must
    # not come back.
    assert "`attempt_id`がそのまま予算キー" not in text

    # Same superseded claim, in the implementation's own docstrings.
    source = (PROJECT_ROOT / "src" / "source_acquisition.py").read_text(
        encoding="utf-8"
    )
    assert "doubles as the Request Budget key" not in source, (
        "src/source_acquisition.py revived attempt_id as the Request Budget key"
    )
    assert "Deterministic **Logical Attempt** identity." in source
    assert "This is *not* the Physical Request Budget key." in source
    assert (
        "``runs/<target_date>/network_requests/<request_id>.json``" in source
    ), "src/source_acquisition.py no longer names the Request Record as the SSOT"


# --------------------------------------------------------------- FIX-PR13 ---
#
# The Production Path Materialization Contract tells the agent to use the
# CLI's own --output instead of a shell redirect. For acquire-* that general
# rule is what produced the 2026-08-27 incident (the result summary
# overwrote market_research.json), so every document carrying the rule must
# also carry the exception.


ACQUIRE_OUTPUT_CONTRACT_DOCS = (
    "docs/source-acquisition.md",
    "prompts/nightly_research.md",
    # The Skill carries the Production Runtime Profile's own "use the CLI's
    # own --output" rule, so it has to carry the acquire-* exception too.
    "SKILL.md",
)


@pytest.mark.parametrize("name", ACQUIRE_OUTPUT_CONTRACT_DOCS)
def test_docs_state_the_acquire_output_contract(name):
    text = _text(name)
    assert "ACQUISITION_OUTPUT_PATH_INVALID" in text, name
    assert "working/" in text, name


# --------------------------------------------------------------- PR #14 ---
#
# The offline Discovery reparse recovery only stays safe if every
# agent-facing document says the same four things: normal acquisition never
# re-parses stored evidence, the recovery that does is HUMAN-ONLY, it spends
# no network request and performs no retry, and an agent must never run it.
# A document that drops one of those is how "just re-run acquire-discovery"
# or "delete the evidence and retry" gets attempted in production.

REPARSE_RECOVERY_DOCS = (
    "docs/nightly-operation.md",
    "docs/source-acquisition.md",
    "prompts/nightly_research.md",
    "SKILL.md",
)


@pytest.mark.parametrize("name", REPARSE_RECOVERY_DOCS)
def test_docs_name_the_human_only_discovery_reparse_recovery(name):
    text = _text(name)
    assert "reparse-production-discovery" in text, name
    assert "HUMAN-ONLY" in text, name


#: How each document denies that a normal acquisition re-parses stored
#: evidence. The wording differs by language, so each is pinned by its own
#: phrase rather than by one shared string.
REPARSE_IS_NOT_AUTOMATIC = {
    "docs/nightly-operation.md": "自動reparseは行われない",
    "docs/source-acquisition.md": "自動reparse",
    "prompts/nightly_research.md": "自動reparseは行わない",
    "SKILL.md": "never re-parses",
}


@pytest.mark.parametrize("name", REPARSE_RECOVERY_DOCS)
def test_docs_deny_that_normal_acquisition_reparses_stored_evidence(name):
    text = _text(name)
    assert REPARSE_IS_NOT_AUTOMATIC[name] in text, name
    # The Exact Logical Attempt contract is what makes that true.
    assert "Exact Logical Attempt" in text, name


#: How each document states the zero-network / zero-retry property.
REPARSE_NO_NETWORK = {
    "docs/nightly-operation.md": "GET 0件・retry 0件",
    "docs/source-acquisition.md": "GET 0件、retry 0件",
    "prompts/nightly_research.md": "GET 0件・retry 0件",
    "SKILL.md": "performs zero network requests",
}


@pytest.mark.parametrize("name", REPARSE_RECOVERY_DOCS)
def test_docs_state_the_recovery_spends_no_network_request(name):
    assert REPARSE_NO_NETWORK[name] in _text(name), name


#: How each agent-facing document forbids the agent from running it. The
#: operations doc is written for the human operator, so it carries the
#: Production Claude prohibition rather than an agent-facing one.
REPARSE_AGENT_PROHIBITION = {
    "docs/nightly-operation.md": "Production Claudeは実行できない",
    "docs/source-acquisition.md": "Production Claudeもagentも実行しない",
    "prompts/nightly_research.md": (
        "Production Claude自身がこのscriptを実行してはいけない"
    ),
    "SKILL.md": "The agent must never run it",
}


@pytest.mark.parametrize("name", REPARSE_RECOVERY_DOCS)
def test_docs_forbid_an_agent_from_running_the_recovery(name):
    assert REPARSE_AGENT_PROHIBITION[name] in _text(name), name


@pytest.mark.parametrize("name", ("prompts/nightly_research.md", "SKILL.md"))
def test_agent_docs_forbid_deleting_the_evidence_to_retry(name):
    """"Delete the request records and fetch again" is the one recovery that
    would destroy the Request Budget's truth, so it is denied explicitly."""
    text = _text(name)
    assert "network_requests/" in text, name
    assert "source_pages/" in text, name
    for denial in ("削除しない", "Never delete"):
        if denial in text:
            break
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError(f"{name} does not forbid deleting the evidence")


def test_the_recovery_is_documented_as_discovery_only():
    """No generic stage replay: the contract covers DISCOVERY and nothing
    else, so the docs must not imply Stage1/Stage2/Turnover/Event replay."""
    for name in ("docs/nightly-operation.md", "docs/source-acquisition.md"):
        text = _text(name)
        assert "Stage1 / Stage2 / Turnover / Event" in text, name
        assert "汎用Replay" in text, name


def test_the_archive_doc_classifies_the_recovery_evidence_as_a_sidecar():
    text = (PROJECT_ROOT / "docs" / "production-run-archive.md").read_text(
        encoding="utf-8"
    )
    assert "production_discovery_reparse/" in text
    assert "RUN_ARTIFACT_ALLOWLIST" in text
    assert "固定契約ではない" in text


def test_the_recovery_files_exist_and_are_not_cli_subcommands():
    from src import cli

    for path in (
        PROJECT_ROOT / "src" / "production_discovery_reparse.py",
        PROJECT_ROOT / "scripts" / "reparse-production-discovery",
        PROJECT_ROOT / "schemas" / "production_discovery_reparse.schema.json",
        PROJECT_ROOT / "tests" / "test_production_discovery_reparse.py",
    ):
        assert path.is_file(), path
    choices = set(cli.build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001
    assert "reparse-production-discovery" not in choices


@pytest.mark.parametrize("name", ACQUIRE_OUTPUT_CONTRACT_DOCS)
def test_no_document_shows_an_acquire_output_into_a_business_artifact(name):
    """The very command shape that broke production is never illustrated."""
    for line in _text(name).splitlines():
        # Command lines only: prose about --output is the contract itself.
        if "src.cli acquire-" not in line or "--output" not in line:
            continue
        assert "/working/" in line, f"{name}: unsafe acquire --output example: {line}"

# ---------------------------------------- the normal operator flow (§21) ---

#: The three stages a normal production night has, in order.
OPERATOR_FLOW = (
    "通常のGit同期",
    "scripts/claude-production --target-date",
    "$prepare-daytrade-plan",
)


def test_the_normal_operator_flow_is_documented_in_order():
    """An operator should not have to read a security architecture to find out
    what to do on an ordinary evening -- and after DTWO-2026-026 there is no
    security architecture in front of the pipeline to read."""
    text = _text("docs/nightly-operation.md")
    section = text.split("## 開始方法", 1)[0]
    assert "通常Operator Flow" in section
    positions = []
    for stage in OPERATOR_FLOW:
        index = section.find(stage)
        assert index != -1, f"the operator flow does not mention {stage!r}"
        positions.append(index)
    assert positions == sorted(positions), "the operator flow stages are out of order"


def test_the_operator_flow_has_no_security_gate_in_front_of_the_pipeline():
    """AC-23: the retired nightly checkpoints are gone from the flow."""
    text = _text("docs/nightly-operation.md")
    section = text.split("## 開始方法", 1)[0]
    for retired in (
        "Runtime Security Preflight",
        "/status",
        "Managed Policy",
        "Remote Control",
        "smoke",
    ):
        assert retired not in section, f"{retired} survived in the operator flow"


def test_a_launcher_failure_is_never_a_business_decision():
    section = _section(_text("docs/nightly-operation.md"), "### 起動を拒否する条件")
    assert "Business decision" in section
    assert "NO_TRADE" in section
    assert "DATA_UNAVAILABLE" in section


def test_the_launcher_is_documented_as_not_a_security_gate():
    section = _section(
        _text("docs/nightly-operation.md"), "## Production Context Launcher"
    )
    assert "Security Gateではない" in section


def test_the_retired_runtime_security_checks_are_documented_as_retired():
    """AC-03: what the launcher no longer looks at, said out loud."""
    text = _text("docs/nightly-operation.md")
    section = _section(text, "### Launcherが確認しないこと")
    for retired in (
        "/etc",
        "Managed Policy",
        "Runtime Guard",
        "exact version",
        "seccomp",
        "Remote Control",
        "runtime_security.json",
    ):
        assert retired in section, retired


# ------------------- Production Security Boundary Change Authorization ---
#
# Governance bootstrap. Changing the policy template in this repository and
# deploying to /etc are different acts with different authorities; the contract
# now says which is which, and says that only a human-and-architect authorised
# Work Order may do the first.

WORK_ORDER_DOC = PROJECT_ROOT / "docs/development-work-order.md"


def _work_order_doc() -> str:
    return WORK_ORDER_DOC.read_text(encoding="utf-8")


def test_tc_gov_01_a_work_order_without_authorization_may_not_relax_security():
    """AC-GOV-02: default deny. Authorization: NONE relaxes nothing."""
    text = _work_order_doc()
    assert "Production Security Boundary Change Authorization" in text
    assert "NONE" in text
    assert "HUMAN + ARCHITECT EXPLICIT" in text
    assert "通常は既存Business Security Contractを緩和してはならない" in text
    assert "repository-side source変更に限り" in text


def test_tc_gov_02_authorization_is_an_exact_file_and_relaxation_intersection():
    """AC-GOV-03: per-file AND per-relaxation, never blanket."""
    text = _work_order_doc()
    for field in (
        "Authorized By",
        "Authorized Repository-Side Files",
        "Authorized Relaxations",
        "Preserved Security Controls",
        "Production Deployment Authority",
    ):
        assert field in text, field
    assert "exactに列挙" in text
    assert "曖昧な列挙は無効" in text or "曖昧な記述は無効" in text


def test_tc_gov_07_the_production_host_is_never_reachable():
    """AC-GOV-01/AC-GOV-05: repository source is not the Production host."""
    text = _work_order_doc()
    assert "Production hostのinstalled stateへの変更権限を与えない" in text
    assert "Human-onlyである" in text
    assert "Production hostのinstalled state" in text


def test_ac_gov_04_claude_cannot_authorize_itself():
    """AC-GOV-04: the one loophole that would make all of this decorative."""
    text = _work_order_doc()
    assert "Claude Code自身をここへ記載しては" in text
    assert "Claude自身がauthorizationを作成・拡張・変更してはならない" in text
    for prohibited in (
        "Claude 自身が authorization を生成する",
        "Claude 自身が authorization を拡張する",
        "Claude 自身が Governance を書き換えて自己許可する",
        "Work Order なしで Protected Invariant を変更する",
    ):
        assert prohibited in text, prohibited


def test_ac_gov_04_a_formal_governance_work_order_is_still_executable():
    """The prohibition is on self-authorisation, not on governance change.

    Protected Invariants say a formal governance work order is the way to
    change them. A fail-closed rule that blocked every governance change
    would make that route unreachable, leaving the contract unable to amend
    itself by the one path it documents.
    """
    text = _work_order_doc()
    blocked = _section(text, "## Fail-Closed Contract")
    # The block is conditional on the authorising work order being absent.
    assert "Governance Work Orderが存在しない状態で" in blocked
    assert "自己許可のためのGovernance変更が必要" in blocked
    # And the permitted route is stated, not merely implied.
    assert "禁じているのは**自己許可**であって、Governance変更そのものではない" in blocked
    assert "そのWork Orderのexact scope内でGovernance変更を実装してよい" in blocked


def test_ac_gov_04_an_unauthorized_governance_change_is_still_blocked():
    """Both halves must hold at once: the route exists, and self-permission
    still fails closed."""
    text = _work_order_doc()
    blocked = _section(text, "## Fail-Closed Contract")
    assert "Claude自身によるGovernance変更" in blocked
    assert "authorizationの有無にかかわらず常にMUST NOT" in blocked


def test_an_unauthorized_boundary_change_is_fail_closed():
    """AC-GOV-02: the fail-closed list names each way authorisation can be absent."""
    text = _work_order_doc()
    for condition in (
        "`NONE`",
        "`Authorized Relaxations`が曖昧",
        "`Authorized Repository-Side Files`に存在しない",
        "Production `/etc`の変更が必要",
        "Human + Architect authorizationを確認できない",
    ):
        assert condition in text, condition


def test_the_preserved_controls_are_still_required():
    """AC-GOV-03: authorisation does not suspend the rest of the contract."""
    text = _work_order_doc()
    assert "Preserved Security Controls`へ記載された契約を維持する" in text
    assert "明示認可されていないSecurity relaxationは禁止" in text


def test_tc_gov_03_the_responsibility_contract_admits_the_authorized_case():
    """AC-GOV-08: the contract must not forbid what it elsewhere permits.

    An unconditional "never relax the Security Contract" in the implementer's
    duties would make the authorized case impossible, leaving Claude with two
    rules that cannot both be followed.
    """
    text = _work_order_doc()
    duties = text[text.index("### Claude Code / Implementer") :]
    duties = duties[: duties.index("### Human")]
    assert "例外が1つだけある" in duties
    assert "HUMAN + ARCHITECT EXPLICIT" in duties
    assert "intersection" in duties
    for absent in (
        "authorization metadata missing",
        "authorization value invalid",
        "Human + Architect authorization を確認できない",
    ):
        assert absent in duties, absent


def test_tc_gov_04_fail_closed_is_not_relaxable_by_authorization():
    """AC-GOV-07: the escape hatch must not reach the fail-closed semantics."""
    text = _work_order_doc()
    duties = text[text.index("### Claude Code / Implementer") :]
    duties = duties[: duties.index("### Human")]
    assert "こちらは例外なし" in duties
    assert "Fail-Closed semantics" in text
    assert "generic authorizationでも" in duties


@pytest.mark.parametrize(
    "invariant",
    [
        "Raw Evidence integrity",
        "SHA256 integrity",
        "Physical Request Record",
        "Exact Logical Attempt Immutability",
        "Trust Chain",
        "Canonical CLI Pipeline Order",
        "Production Human-only operation boundary",
        "Fail-Closed semantics",
        "Safe Sync / Safe Start / Safe Push authority boundary",
    ],
)
def test_tc_gov_05_protected_invariants_are_outside_generic_authorization(invariant):
    """AC-GOV-07: these need their own governance work order, not a boundary one."""
    text = _work_order_doc()
    protected = _section(text, "### Protected Invariants")
    assert invariant in protected, invariant
    assert "だけでは緩和できない" in protected
    assert "別の正式Governance Work Order" in protected


def test_tc_gov_06_authorization_is_not_retroactive():
    """AC-GOV-06: a governance change does not bless what already happened."""
    text = _work_order_doc()
    section = _section(text, "### Authorization Non-Retroactivity")
    assert "handoffされた後に開始されるimplementationにだけ" in section
    for past in (
        "既に作成済みの commit",
        "既に push 済みの branch",
        "既に open 済みの PR",
        "既に merge 済みの PR",
        "既に Production へ反映済みの変更",
    ):
        assert past in section, past
    assert "retroactive approval" in section


def test_claude_md_defers_to_the_work_order_ssot_without_duplicating_it():
    """AC-GOV-08: one governance SSOT, pointed at rather than copied.

    Two copies of a contract are two contracts, and the second one is always
    the stale one. CLAUDE.md keeps the rules Claude must not need to look up --
    no self-authorisation, no false PASS -- and sends the rest to the SSOT.
    """
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "daytrade-sbi/docs/development-work-order.md" in text
    assert "このファイルへ複製しない" in text
    assert "Claude自身がWork Orderへauthorizationを追加" in text
    assert "NOT VERIFIED BY CLAUDE" in text


# ------------------------------------------- static pipeline audit (§22) ---
#
# The Nightly Prompt and the canonical document are two descriptions of the
# same pipeline, and they had drifted. This audit compares the command tokens
# themselves -- not paragraph wording, not line numbers -- so either side
# moving alone fails.

#: Subcommands a normal nightly must never contain, with the reason.
HUMAN_ONLY_SUBCOMMANDS = {
    "record-recommendation": "appends to a global CSV outside the run directory",
    "record-execution": "records a human-confirmed fill",
    "activate-selection-config": "applies a human-chosen threshold pair",
    "build-selection-calibration": "calibration, not a nightly stage",
    "evaluate-selection-thresholds": "calibration, not a nightly stage",
}

_CLI_INVOCATION = re.compile(r"-m\s+src\.cli\s+([a-z0-9][a-z0-9-]*)")


def _prompt_subcommands() -> set[str]:
    return set(_CLI_INVOCATION.findall(_text("prompts/nightly_research.md")))


#: Canonical-order entries that name more than one subcommand in prose.
CANONICAL_ENTRY_ALIASES = {
    "init/complete event-research": {
        "init-event-research",
        "complete-event-research",
    },
}


def _cli_subcommands() -> set[str]:
    from src import cli

    return set(cli.build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001


def _canonical_doc_subcommands() -> set[str]:
    known = _cli_subcommands()
    found: set[str] = set()
    for entry in _canonical_order_entries():
        if entry in CANONICAL_ENTRY_ALIASES:
            found |= CANONICAL_ENTRY_ALIASES[entry]
        elif entry in known:
            found.add(entry)
    assert found <= known, f"an alias names an unknown subcommand: {found - known}"
    return found


def test_every_nightly_prompt_command_is_a_real_cli_subcommand():
    unknown = _prompt_subcommands() - _cli_subcommands()
    assert not unknown, (
        "prompts/nightly_research.md invokes src.cli subcommands that do not "
        f"exist: {sorted(unknown)}"
    )


def test_the_nightly_prompt_contains_no_human_only_command():
    present = _prompt_subcommands() & set(HUMAN_ONLY_SUBCOMMANDS)
    assert not present, (
        "prompts/nightly_research.md invokes a human-only command in the normal "
        f"flow: {sorted(present)}"
    )


def test_the_canonical_pipeline_contains_no_human_only_command():
    present = _canonical_doc_subcommands() & set(HUMAN_ONLY_SUBCOMMANDS)
    assert not present, (
        f"docs/canonical-pipeline.md lists a human-only command: {sorted(present)}"
    )


def test_record_recommendation_stays_out_of_the_nightly():
    """The specific regression this audit exists to catch."""
    assert "record-recommendation" in HUMAN_ONLY_SUBCOMMANDS
    assert "record-recommendation" not in _prompt_subcommands()
    assert "record-recommendation" not in _canonical_doc_subcommands()


def test_every_canonical_pipeline_command_exists_in_the_cli():
    unknown = _canonical_doc_subcommands() - _cli_subcommands()
    assert not unknown, (
        f"docs/canonical-pipeline.md lists unknown subcommands: {sorted(unknown)}"
    )


# ------------------------------ Validation Gate Placement (VG-01..VG-09) ---
#
# DTWO-2026-025 Rev.2. A gate that cannot run before a commit exists -- a real
# provider on a real host, a CI run, a human acceptance pass -- was being
# written down as a pre-commit requirement, which makes the contract
# unsatisfiable and tempts an implementer to claim a PASS nobody observed. The
# amendment moves those gates to where their evidence can name an immutable
# SHA, and fixes that moving them does not weaken them.

VALIDATION_GATE_HEADING = "## Validation Gate Placement Contract"
PRE_COMMIT_HEADING = "### Pre-Commit Gate"
PRE_MERGE_HEADING = "### Pre-Merge Gate"
PRE_PRODUCTION_HEADING = "### Pre-Production Gate"

#: The standard development lifecycle, in the order the SSOT draws it.
STANDARD_LIFECYCLE = ("Tests", "Commit", "Push", "Draft PR", "CI", "Review")


def test_vg_01_the_standard_lifecycle_still_tests_before_it_commits():
    """The amendment moves external gates; it does not move the local ones."""
    lifecycle = _section(_work_order_doc(), "## Work Order Lifecycle")
    positions = []
    for stage in STANDARD_LIFECYCLE:
        index = lifecycle.find(stage)
        assert index != -1, f"the lifecycle no longer names {stage!r}"
        positions.append(index)
    assert positions == sorted(positions), "the standard lifecycle is out of order"


def test_vg_02_the_three_gate_classes_are_defined_in_order():
    text = _work_order_doc()
    positions = []
    for heading in (
        VALIDATION_GATE_HEADING,
        PRE_COMMIT_HEADING,
        PRE_MERGE_HEADING,
        PRE_PRODUCTION_HEADING,
    ):
        assert heading in text, f"the work order SSOT is missing {heading}"
        positions.append(text.index(heading))
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    "heading, required",
    [
        (
            PRE_COMMIT_HEADING,
            ("repository-local deterministic tests", "commitしては"),
        ),
        (
            PRE_MERGE_HEADING,
            (
                "GitHub Actions CI",
                "real-provider compatibility",
                "Human Merge: BLOCKED",
            ),
        ),
        (
            PRE_PRODUCTION_HEADING,
            (
                "Production historical compatibility audit",
                "Development Claudeが実行しては",
            ),
        ),
    ],
)
def test_vg_02_each_gate_class_states_what_it_holds(heading, required):
    body = _section(_work_order_doc(), heading)
    for token in required:
        assert token in body, f"{heading} does not state {token!r}"


def test_vg_03_an_external_gate_is_not_a_pre_commit_requirement_by_default():
    """The correction itself: commit first, then run what needs a fixed SHA."""
    body = _section(_work_order_doc(), PRE_MERGE_HEADING)
    assert "immutable SHAへ固定した後" in body
    for permitted in ("git add", "commit", "push", "Draft PR"):
        assert permitted in body, permitted
    assert "禁止しては" in body
    # A work order may still pin a gate to pre-commit explicitly.
    assert "明示的にPRE-COMMITと指定した" in body


def test_vg_04_external_gate_evidence_names_an_exact_commit():
    body = _section(_work_order_doc(), "### Exact HEAD Evidence")
    assert "exact commit SHA" in body
    assert "Provider Compatibility Tested HEAD" in body
    assert "<40-char SHA>" in body


def test_vg_05_evidence_does_not_survive_a_head_change():
    body = _section(_work_order_doc(), "### Exact HEAD Evidence")
    assert "old PC Evidence = INVALID FOR NEW HEAD" in body
    assert "流用しては" in body
    assert "再実施する" in body
    # A PR body edit moves no bytes, so it invalidates nothing.
    assert "commit SHAが変わらない場合" in body


def test_vg_05_a_failed_gate_blocks_instead_of_rewriting_history():
    body = _section(_work_order_doc(), "### Gate Failure Semantics")
    assert "Merge: BLOCKED" in body
    assert "Production: BLOCKED" in body
    assert "修正commitを追加する" in body
    for forbidden in (
        "history rewrite",
        "force push",
        "validator relaxation",
        "Evidence rewrite",
    ):
        assert forbidden in body, forbidden


def test_vg_06_an_unrun_external_gate_is_never_reported_as_a_pass():
    body = _section(_work_order_doc(), "### No False PASS")
    for claim in ("PASS", "SUCCESS", "VERIFIED"):
        assert claim in body, claim
    assert "Provider Compatibility: NOT VERIFIED BY CLAUDE" in body
    assert "GitHub CI: NOT VERIFIED BY CLAUDE" in body


#: The pre-merge order every external gate follows, start to finish.
EXTERNAL_GATE_ORDER = (
    "GitHub Actions CI",
    "exact PR HEAD freeze",
    "external / manual gate on the frozen HEAD",
    "Architect Final Review",
    "Human Merge",
    "Production rollout",
)


def test_vg_07_an_external_gate_runs_after_ci_on_a_frozen_head():
    section = _section(_work_order_doc(), PRE_MERGE_HEADING)
    positions = []
    for stage in EXTERNAL_GATE_ORDER:
        index = section.find(stage)
        assert index != -1, f"the pre-merge gate order omits {stage!r}"
        positions.append(index)
    assert positions == sorted(positions), "the pre-merge gate order is wrong"
    assert "CIがsuccessになる前にHuman側のexternal gateを開始しない" in section


def test_vg_08_one_failed_or_missing_case_blocks_merge_and_production():
    section = _section(_work_order_doc(), PRE_MERGE_HEADING)
    assert "全件実施" in section
    assert "1件のFAILも、1件の未実施も、同じくBLOCKED" in section
    assert "Human Merge: BLOCKED" in section
    assert "Production rollout: BLOCKED" in section


def test_vg_08_no_local_substitute_is_accepted_for_an_external_gate():
    section = _section(_work_order_doc(), PRE_MERGE_HEADING)
    assert "mock test" in section
    assert "代替にならない" in section


@pytest.mark.parametrize(
    "prohibited",
    [
        "raw git push",
        "gh CLI",
        "force push",
        "Production operation",
        "Security relaxation",
        "Fail-Closed relaxation",
    ],
)
def test_vg_09_the_gate_amendment_grants_no_new_authority(prohibited):
    """Reclassifying where a gate runs must not become a way to remove one."""
    body = _section(
        _work_order_doc(), "### Gate Placement Is Not an Authority Change"
    )
    assert prohibited in body, prohibited
    assert "消えるのではなく後段のgateへ移る" in body
    assert "Protected Invariants" in body
