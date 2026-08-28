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

#: The canonical order, exactly as FIX-016 specifies it.
CANONICAL_STEPS = (
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


def test_every_documented_file_exists():
    for name, path in DOCS.items():
        assert path.is_file(), f"{name} is missing at {path}"


@pytest.mark.parametrize("name", sorted(DOCS))
@pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
def test_no_document_still_claims_the_agent_fetches_market_data(name, claim):
    assert claim not in _text(name), f"{name} still claims: {claim}"


@pytest.mark.parametrize("name", ORDER_DOCS)
def test_canonical_pipeline_order_is_identical_everywhere(name):
    """Each step appears, in order, exactly once as an ordered-list entry."""
    text = _text(name)
    positions = []
    for step in CANONICAL_STEPS:
        index = text.find(step)
        assert index != -1, f"{name} does not mention pipeline step {step!r}"
        positions.append(index)
    assert positions == sorted(positions), (
        f"{name} lists the canonical pipeline steps out of order"
    )


def test_canonical_doc_lists_exactly_the_25_steps_in_order():
    text = CANONICAL_DOC.read_text(encoding="utf-8")
    section = text.split("## Canonical CLI Pipeline Order", 1)[1]
    section = section.split("\n##", 1)[0]
    numbered = re.findall(r"^\s*(\d+)\.\s+(.+?)\s*$", section, flags=re.MULTILINE)
    assert [int(index) for index, _ in numbered] == list(range(1, 26))
    rendered = [entry.strip("`") for _, entry in numbered]
    assert rendered == list(CANONICAL_STEPS)


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
        for step in CANONICAL_STEPS
        if re.fullmatch(r"[a-z0-9-]+", step) and step != "market_data"
    }
    missing = sorted(step for step in documented if step not in choices)
    assert missing == [], f"documented but non-existent CLI command(s): {missing}"


# ------------------------------- FIX-R2-004: Claude Runtime Security Gate ---


def test_canonical_doc_places_the_runtime_security_gate_before_the_pipeline():
    text = _text("docs/canonical-pipeline.md")
    assert "Runtime Security Gate" in text
    assert "Business Canonical Pipeline" in text
    assert text.index("Runtime Security Gate  →  Business Canonical Pipeline") > 0


@pytest.mark.parametrize(
    "name", ["docs/canonical-pipeline.md", "docs/nightly-operation.md"]
)
def test_docs_name_the_os_managed_policy_as_the_production_boundary(name):
    text = _text(name)
    assert "/etc/claude-code/managed-settings.json" in text
    assert "Defense in Depth" in text or "Development defense only" in text


def test_nightly_operation_documents_the_dedicated_production_runtime():
    text = _text("docs/nightly-operation.md")
    for required in (
        "専用",
        "WSL2",
        "/etc/daytrade-production-runtime",
        "DAYTRADE_PRODUCTION_RUNTIME_V1",
    ):
        assert required in text


def test_nightly_operation_documents_human_only_provisioning():
    text = _text("docs/nightly-operation.md")
    for required in (
        "sudo apt-get install bubblewrap socat",
        "seccomp",
        "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED",
        "apparmor_restrict_unprivileged_userns",
        "2.1.219",
    ):
        assert required in text


def test_nightly_operation_documents_the_deployment_and_acceptance_steps():
    text = _text("docs/nightly-operation.md")
    for required in (
        "render-claude-production-policy",
        "deploy-claude-managed-policy",
        "claude doctor",
        "/status",
        "EXISTING_MANAGED_POLICY_PRESENT",
        "claude-production",
        "runtime_security.json",
    ):
        assert required in text


def test_nightly_operation_documents_the_human_seccomp_attestation_marker():
    text = _text("docs/nightly-operation.md")
    for required in (
        "/etc/daytrade-seccomp-verified",
        "DAYTRADE_SECCOMP_VERIFIED_V2",
        "/sandbox",
        "uid 0",
        "sandbox_seccomp",
    ):
        assert required in text
    assert "機械的に判定できない" not in text


def test_nightly_operation_documents_the_target_date_gate():
    text = _text("docs/nightly-operation.md")
    assert "CLAUDE_TARGET_DATE_INVALID" in text


def test_nightly_operation_defers_real_network_smoke_to_the_next_fix():
    text = _text("docs/nightly-operation.md")
    assert "FIX-R2-005" in text


def test_nightly_operation_forbids_recording_the_user_agent_value():
    text = _text("docs/nightly-operation.md")
    assert "http_user_agent_present" in text
    assert "credential" in text


def test_claude_md_states_the_production_runtime_security_rules():
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for required in (
        "/etc/claude-code/managed-settings.json",
        "event_source_extraction.json",
        "deploy-claude-managed-policy",
        "sudo",
    ):
        assert required in text


def test_every_new_runtime_security_file_exists():
    expected = [
        PROJECT_ROOT / "src" / "claude_runtime_security.py",
        PROJECT_ROOT / "schemas" / "runtime_security.schema.json",
        PROJECT_ROOT / "ops" / "claude" / "managed-settings.template.json",
        PROJECT_ROOT / "ops" / "claude" / "daytrade_runtime_guard.py",
        PROJECT_ROOT / "scripts" / "render-claude-production-policy",
        PROJECT_ROOT / "scripts" / "deploy-claude-managed-policy",
        PROJECT_ROOT / "scripts" / "claude-production",
        PROJECT_ROOT / "tests" / "test_claude_runtime_security.py",
        PROJECT_ROOT / "tests" / "test_claude_runtime_guard.py",
    ]
    missing = [str(path) for path in expected if not path.is_file()]
    assert missing == []


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


# ------------------------------- FIX-PRD-005: production path contract ---
#
# The Runtime Acceptance failure was a documentation failure: the operating
# procedure showed logical relative paths (`config/source_matrix.yaml`) and
# said nothing about the guard's absolute-path contract, so a production
# operator pasted them into a Bash call and was correctly denied. The guard
# is right; the procedure was incomplete. These tests pin the *procedure*,
# and pin that the guard's denials were not softened to compensate.

#: The two contract names every agent-facing production document must carry,
#: so that the Skill, the prompt and the operations doc point at one rule.
PRODUCTION_CONTRACT_DOCS = (
    "docs/nightly-operation.md",
    "docs/canonical-pipeline.md",
    "prompts/nightly_research.md",
    "SKILL.md",
)


@pytest.mark.parametrize("name", PRODUCTION_CONTRACT_DOCS)
def test_production_docs_name_both_command_materialisation_contracts(name):
    text = _text(name)
    assert "Production Path Materialization Contract" in text, name
    assert "1 Bash call = 1 canonical CLI command" in text, name


#: The documents a production agent actually renders commands from. The
#: canonical-pipeline doc names the contract but stays the pipeline-order
#: SSOT, so it is not asked to repeat the syntax detail.
COMMAND_RENDERING_DOCS = (
    "docs/nightly-operation.md",
    "prompts/nightly_research.md",
    "SKILL.md",
)


@pytest.mark.parametrize("name", COMMAND_RENDERING_DOCS)
def test_production_docs_forbid_every_unexpanded_path_form(name):
    """A production command string is inspected before any shell expands it,
    so each of these forms has to be called out as unusable, not just
    'use absolute paths'."""
    text = _text(name)
    for form in ("$DAYTRADE_ROOT", "${DAYTRADE_ROOT}", "$DAYTRADE_RUN_DIR", "$(pwd)"):
        assert form in text, f"{name} does not mention the {form} form"


def test_nightly_operation_states_the_materialisation_sources():
    """Where the absolute paths come from -- the launcher's cwd and the run
    directory -- rather than a hardcoded machine path."""
    text = _text("docs/nightly-operation.md")
    assert "CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in text
    assert "CLAUDE_PRODUCTION_BASH_DENIED" in text
    assert "current working directory" in text
    assert "runtime_security.json" in text
    assert '; echo "EXIT_CODE=$?"' in text


def test_no_document_hardcodes_the_operators_daytrade_root():
    """The materialised path is derived at runtime; a machine-specific root
    baked into a document is exactly what makes the contract wrong on the
    next machine."""
    hardcoded = "/home/daytrade/DayTrade"
    for name in DOCS:
        assert hardcoded not in _text(name), f"{name} hardcodes {hardcoded}"


def test_skill_defers_to_the_operations_doc_for_the_production_contract():
    text = _text("SKILL.md")
    assert "scripts/claude-production" in text
    assert "daytrade-sbi/docs/nightly-operation.md" in text
    assert "CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in text


def test_claude_md_states_the_absolute_path_and_single_command_contract():
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Production Path Materialization Contract" in text
    assert "1 Bash call = 1 canonical CLI command" in text
    assert "CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN" in text


def test_the_runtime_guard_still_denies_relative_paths_and_metacharacters():
    """The documented fix must not have been implemented by relaxing the
    guard. Pinned against the guard source itself, not its docs."""
    guard = (PROJECT_ROOT / "ops" / "claude" / "daytrade_runtime_guard.py").read_text(
        encoding="utf-8"
    )
    assert "must be an absolute path in production" in guard
    assert "if not candidate.is_absolute():" in guard
    for meta in (";", "&&", "||", "|", "$(", "`", ">", "<"):
        assert f'"{meta}"' in guard, f"the guard no longer lists {meta!r} as a metacharacter"


# --------------------------------------------------------------------------
# PR #9 review: a documented production example that the guard would deny is
# worse than no example -- the operator pastes it, gets CLAUDE_PRODUCTION_*,
# and cannot tell whether the doc or the guard is wrong. So the flag set in
# the build-ranking example is pinned against the guard's own SUBCOMMAND_FLAGS
# rather than against a second hand-maintained list.


def _guard_module():
    """Import the runtime guard as a module so its contract tables are the
    single source of truth for these documentation assertions."""
    import importlib.util

    guard_path = PROJECT_ROOT / "ops" / "claude" / "daytrade_runtime_guard.py"
    spec = importlib.util.spec_from_file_location(
        "daytrade_runtime_guard_docs_ssot", guard_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_build_ranking_examples(name):
    """Every documented production build-ranking command line, i.e. the ones
    rendered as '<production python> -B -m src.cli build-ranking ...'."""
    return [
        line.strip()
        for line in _text(name).splitlines()
        if line.strip().startswith("<production python>")
        and " -m src.cli build-ranking " in line
    ]


def test_nightly_operation_shows_a_production_build_ranking_example():
    assert _production_build_ranking_examples("docs/nightly-operation.md")


def test_the_production_build_ranking_example_matches_the_guard_contract():
    """--ranking is an input of build-selection, not of build-ranking; the
    guard denies it with CLAUDE_PRODUCTION_NOT_CANONICAL. The example must be
    a complete, pasteable command, so no ellipsis either."""
    allowed = _guard_module().SUBCOMMAND_FLAGS["build-ranking"]
    examples = _production_build_ranking_examples("docs/nightly-operation.md")
    for example in examples:
        assert "--ranking " not in example, example
        assert "--output " in example, example
        assert "..." not in example, example
        flags = {token for token in example.split() if token.startswith("--")}
        assert flags == set(allowed), example


def test_the_production_build_ranking_example_is_one_materialised_command():
    """Materialising the documented placeholders must yield exactly what the
    guard accepts: one line, absolute paths only, no shell expansion and no
    metacharacter left to expand."""
    for example in _production_build_ranking_examples("docs/nightly-operation.md"):
        materialised = (
            example.replace("<production python>", "/opt/daytrade/bin/python3")
            .replace("<DAYTRADE_ROOT>", "/srv/daytrade-sbi")
            .replace("<TARGET_DATE>", "2026-08-14")
        )
        assert "<" not in materialised and ">" not in materialised, materialised
        for form in ("$DAYTRADE_ROOT", "${DAYTRADE_ROOT}", "$DAYTRADE_RUN_DIR", "$(", "`"):
            assert form not in materialised, f"{form} survives in {materialised}"
        for meta in (";", "&&", "||", "|"):
            assert meta not in materialised, f"{meta} survives in {materialised}"
        tokens = materialised.split()
        assert tokens[:4] == [
            "/opt/daytrade/bin/python3",
            "-B",
            "-m",
            "src.cli",
        ], materialised
        values = [token for token in tokens[5:] if not token.startswith("--")]
        assert values, materialised
        for value in values:
            assert value.startswith("/srv/daytrade-sbi/"), value
            for relative in ("./", "../", "~/"):
                assert relative not in value, value


@pytest.mark.parametrize("name", PRODUCTION_CONTRACT_DOCS)
def test_production_docs_never_equate_a_bash_call_with_a_pipeline_step(name):
    """'1 Bash call = 1 stage' is wrong: one canonical-pipeline number can
    hold several canonical CLI commands (e.g. init/complete event-research),
    so the contract is per command, not per stage."""
    text = _text(name)
    for wrong in ("1 Bash call = 1 stage", "1 Bash call = 1 Stage"):
        assert wrong not in text, name


# --------------------------------------------------------------- FIX-PR13 ---
#
# The Production Path Materialization Contract tells the agent to use the
# CLI's own --output instead of a shell redirect. For acquire-* that general
# rule is what produced the 2026-08-27 incident (the result summary
# overwrote market_research.json), so every document carrying the rule must
# also carry the exception.


ACQUIRE_OUTPUT_CONTRACT_DOCS = (
    "docs/nightly-operation.md",
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
