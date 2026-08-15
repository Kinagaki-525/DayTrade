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
        "DAYTRADE_SECCOMP_VERIFIED_V1",
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
