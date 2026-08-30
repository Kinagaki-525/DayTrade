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
        "2.1.224",
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


#: The Policy deployment section, from its heading to the next one. Scoped on
#: purpose: `py -m pytest` (Windows/PowerShell) and the CI `python` are
#: legitimate elsewhere in this document, and only the Production procedure is
#: bound by the Production Python identity contract.
POLICY_DEPLOYMENT_HEADING = "### Policy deployment（Human）"

#: How the Production Python candidate is discovered, and the shell variable
#: every later step must reuse so that pytest, render and deploy all name the
#: same interpreter.
PRODUCTION_PYTHON_DISCOVERY = 'PRODUCTION_PYTHON="$(command -v python3)"'
PRODUCTION_PYTHON_VAR = '"$PRODUCTION_PYTHON"'


def _section(text: str, heading: str) -> str:
    """The body under ``heading``, up to the next heading of any level."""
    assert heading in text, f"missing section heading: {heading}"
    body = text[text.index(heading) + len(heading) :]
    following = re.search(r"^#{1,6} ", body, re.MULTILINE)
    return body[: following.start()] if following else body


def test_policy_deployment_uses_one_production_python_identity():
    """The Production procedure must name ONE interpreter, discovered once.

    Production is a Linux/WSL contract that requires ``python3`` only; a bare
    ``python`` is not part of it, and documenting one would tell the operator
    to satisfy the prerequisite with an alias, a symlink or python-is-python3
    -- none of which the Security Contract knows about. pytest, render and
    deploy must therefore all reuse the same discovered candidate, so the
    interpreter that validated the tests is the interpreter the Managed Policy
    is rendered for.
    """
    section = _section(_text("docs/nightly-operation.md"), POLICY_DEPLOYMENT_HEADING)

    assert PRODUCTION_PYTHON_DISCOVERY in section, (
        "the Production Python candidate must be discovered via "
        f"{PRODUCTION_PYTHON_DISCOVERY}"
    )
    assert f"{PRODUCTION_PYTHON_VAR} -B -m pytest" in section, (
        "the prerequisite pytest must run under the discovered candidate"
    )
    for command in ("render-claude-production-policy", "deploy-claude-managed-policy"):
        invocation = re.search(
            rf"{re.escape(command)}.*?--production-python\s+(\S+)",
            section,
            re.DOTALL,
        )
        assert invocation is not None, f"{command} is not documented with a candidate"
        assert invocation.group(1) == PRODUCTION_PYTHON_VAR, (
            f"{command} must reuse {PRODUCTION_PYTHON_VAR}, "
            f"got {invocation.group(1)}"
        )
    # Discovered exactly once: a second `command -v python3` would be a second
    # discovery, which can resolve differently from the one pytest validated.
    assert section.count("$(command -v python3)") == 1


@pytest.mark.parametrize(
    "drift",
    ["python -B -m pytest", "python3 -B -m pytest", "py -B -m pytest"],
)
def test_policy_deployment_never_documents_a_bare_python_pytest(drift):
    """Regression guard for the exact drift this fix removes."""
    section = _section(_text("docs/nightly-operation.md"), POLICY_DEPLOYMENT_HEADING)
    assert drift not in section, (
        f"Policy deployment documents {drift!r}; Production has no such command "
        "and the prerequisite must use the discovered candidate"
    )


def test_policy_deployment_never_hardcodes_a_machine_specific_interpreter():
    """Canonicalization belongs to canonical_production_python(), not to a path
    typed into a document that is wrong on the next machine."""
    section = _section(_text("docs/nightly-operation.md"), POLICY_DEPLOYMENT_HEADING)
    hardcoded = re.search(r"/usr(?:/local)?/bin/python[0-9.]*", section)
    assert hardcoded is None, (
        f"Policy deployment hardcodes the interpreter path {hardcoded.group(0)!r}"
        if hardcoded
        else ""
    )


#: The Production Python dependency bootstrap, provisioned by a human once per
#: Production runtime. Scoped as its own section so the assertions below bind
#: the provisioning contract without touching the Windows or CI invocations
#: documented elsewhere.
PRODUCTION_BOOTSTRAP_HEADING = "#### Production Python dependency bootstrap（Human専用）"

#: The fixed Production virtualenv, and the interpreter inside it. Fixed on
#: purpose: the Production Python identity has to be the same path on every
#: Production runtime, and a per-machine location would make the Managed
#: Policy's canonical identity machine-specific.
PRODUCTION_VENV_ROOT = "/opt/daytrade-production-python"
PRODUCTION_VENV_PYTHON = "/opt/daytrade-production-python/bin/python3"

#: PATH materialization: the Production session must find the venv interpreter
#: first, so a single `command -v python3` discovers it rather than the system
#: Python that carries none of the dependencies.
PRODUCTION_PATH_EXPORT = 'export PATH="/opt/daytrade-production-python/bin:$PATH"'

#: Ways of installing dependencies that Production must never be told to use.
#: The first two override PEP 668 on an externally-managed system Python; the
#: rest put dependencies somewhere the Production interpreter's identity does
#: not account for.
FORBIDDEN_BOOTSTRAP_INSTALLS = (
    "--break-system-packages",
    "pip install --user",
    "-m pip install --user",
    "--target",
    "PYTHONPATH=",
)

#: Workarounds for a missing bare `python`. Production requires python3 only.
FORBIDDEN_PYTHON_WORKAROUNDS = (
    "python-is-python3",
    "ln -s",
    "alias python",
)


def _canonical_python_major_minor() -> str:
    version = (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    major, minor, *_ = version.split(".")
    return f"{major}.{minor}"


def _commands(section: str) -> str:
    """Every shell command in a section, with line continuations joined.

    Deliberately excludes prose: a document that *forbids* a construct has to
    be able to name it, so the prohibitions below are asserted against what the
    operator would actually run, not against every mention of the string.
    """
    blocks = re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", section, re.DOTALL)
    return "\n".join(blocks).replace("\\\n", " ")


def test_production_bootstrap_section_exists():
    text = _text("docs/nightly-operation.md")
    assert PRODUCTION_BOOTSTRAP_HEADING in text, (
        "Production has no documented way to provision its Python dependencies"
    )


def test_production_bootstrap_uses_a_fixed_copy_based_root_owned_venv():
    """The venv must be created with --copies, not with symlinks.

    canonical_production_python() resolves symlinks. A symlink-based venv's
    python3 therefore canonicalizes to the *base* interpreter, outside the
    venv -- so the Managed Policy identity and the interpreter that actually
    carries the dependencies would come apart. --copies makes the venv
    interpreter a regular file that survives canonicalization as itself.
    """
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert PRODUCTION_VENV_ROOT in section
    assert PRODUCTION_VENV_PYTHON in section
    venv_command = re.search(r"[^\n]*-m venv[^\n]*", _commands(section))
    assert venv_command is not None, "the venv creation command is not documented"
    assert "--copies" in venv_command.group(0), (
        "the Production venv must be created with --copies: "
        f"{venv_command.group(0)!r}"
    )


def test_production_bootstrap_documents_the_matching_venv_os_package():
    """The apt prerequisite must track the repository's canonical version."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    expected = f"python{_canonical_python_major_minor()}-venv"
    assert expected in section, (
        f"the documented venv OS package does not match .python-version "
        f"(expected {expected})"
    )


def test_production_bootstrap_installs_only_the_dev_requirements_manifest():
    """requirements-dev.txt already includes -r requirements.txt.

    Installing both would pin the same manifest twice and invite them to drift
    apart in the operator's hands.
    """
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert "requirements-dev.txt" in section
    installs = re.findall(r"-m pip install[^\n]*", _commands(section))
    assert installs, "no dependency install command is documented"
    for install in installs:
        assert "requirements-dev.txt" in install, (
            f"a non-canonical manifest is installed: {install!r}"
        )


def test_production_bootstrap_installs_through_the_venv_interpreter():
    """Never through the system Python: that is the PEP 668 violation."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    for line in _commands(section).splitlines():
        if "-m pip install" not in line:
            continue
        assert PRODUCTION_VENV_PYTHON in line, (
            f"dependencies are installed outside the Production venv: {line.strip()!r}"
        )


@pytest.mark.parametrize("forbidden", FORBIDDEN_BOOTSTRAP_INSTALLS)
def test_production_bootstrap_never_overrides_the_managed_system_python(forbidden):
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert forbidden not in _commands(section), (
        f"Production bootstrap runs {forbidden!r}, which installs outside "
        "the Production venv identity"
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN_PYTHON_WORKAROUNDS)
def test_production_bootstrap_never_requires_a_bare_python_workaround(forbidden):
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert forbidden not in _commands(section), (
        f"Production bootstrap runs {forbidden!r}; Production needs python3 only"
    )


def test_production_bootstrap_is_fail_closed_on_an_existing_venv():
    """An existing venv is stopped on, never silently reused or wiped."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert "--clear" not in _commands(section), (
        "a --clear would destroy an existing environment"
    )
    assert "STOP" in section


def test_production_bootstrap_verifies_the_production_python_identity():
    """The candidate must be checked against the repository's own resolver."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert "canonical_production_python" in section
    assert "pip check" in section
    assert ".python-version" in section


def test_policy_deployment_materializes_the_production_python_path_first():
    """PATH first, then one discovery: otherwise `command -v python3` finds the
    system Python, which carries none of the Production dependencies."""
    section = _section(_text("docs/nightly-operation.md"), POLICY_DEPLOYMENT_HEADING)
    assert PRODUCTION_PATH_EXPORT in section
    assert section.index(PRODUCTION_PATH_EXPORT) < section.index(
        PRODUCTION_PYTHON_DISCOVERY
    ), "PATH must be materialized before the Production Python is discovered"
    assert PRODUCTION_VENV_PYTHON in section, (
        "the expected candidate value is not documented"
    )


@pytest.mark.parametrize(
    "script,prefix",
    [
        ("render-claude-production-policy", '"$PRODUCTION_PYTHON" scripts/'),
        ("deploy-claude-managed-policy", 'sudo "$PRODUCTION_PYTHON" scripts/'),
    ],
)
def test_policy_scripts_run_under_the_production_python(script, prefix):
    """The scripts are launched by the Production interpreter itself, not by
    their shebang: under sudo's secure_path a shebang lookup can select a
    different python3 than the one whose dependencies were validated."""
    section = _section(_text("docs/nightly-operation.md"), POLICY_DEPLOYMENT_HEADING)
    assert f"{prefix}{script}" in section, (
        f"{script} must be launched as {prefix}{script}"
    )


#: Every Production start goes through this section, so it -- not only the
#: one-off Policy deployment -- has to materialize the Production Python.
PRODUCTION_ENTRY_HEADING = "### Production Entry Contract"

#: The exact-path assertion an operator runs, rather than eyeballing a printed
#: path against a documented one.
PRODUCTION_PYTHON_FIXED_PATH_TEST = (
    'test "$PRODUCTION_PYTHON" = "/opt/daytrade-production-python/bin/python3"'
)


def test_production_entry_materializes_the_production_python_every_time():
    """A fresh shell must not rediscover the system Python.

    claude-production resolves its candidate with which("python3") when
    DAYTRADE_PRODUCTION_PYTHON is unset, so a session started without the venv
    on PATH would run under /usr/bin/python3 -- an identity the Managed Policy
    was never rendered for. Putting the venv first on PATH is what makes the
    launcher's own lookup resolve to the same interpreter.
    """
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_ENTRY_HEADING)
    commands = _commands(section)
    assert PRODUCTION_PATH_EXPORT in commands
    assert PRODUCTION_PYTHON_DISCOVERY in commands
    assert PRODUCTION_PYTHON_FIXED_PATH_TEST in commands
    assert 'test ! -L "$PRODUCTION_PYTHON"' in commands


@pytest.mark.parametrize("flags", ["--preflight-only", "--target-date"])
def test_production_entry_launches_the_launcher_under_the_production_python(flags):
    """Both the preflight and the real Nightly start run the launcher with the
    Production interpreter itself, not through its shebang."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_ENTRY_HEADING)
    commands = _commands(section)
    launches = [
        block
        for block in re.findall(
            r'"\$PRODUCTION_PYTHON" scripts/claude-production[^\n]*(?:\\\n[^\n]*)*',
            commands,
        )
        if flags in block
    ]
    assert launches, (
        f"the Production entry command carrying {flags} is not launched as "
        '"$PRODUCTION_PYTHON" scripts/claude-production'
    )


def test_production_entry_never_documents_a_bare_launcher_command():
    """A bare `scripts/claude-production ...` would rely on the shebang."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_ENTRY_HEADING)
    for line in _commands(section).splitlines():
        stripped = line.strip()
        if not stripped.startswith("scripts/claude-production"):
            continue
        raise AssertionError(
            f"the Production entry documents a bare launcher command: {stripped!r}"
        )


def test_production_entry_never_hardcodes_the_system_interpreter():
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_ENTRY_HEADING)
    hardcoded = re.search(r"/usr(?:/local)?/bin/python[0-9.]*", _commands(section))
    assert hardcoded is None, (
        "the Production entry hardcodes the system interpreter: "
        f"{hardcoded.group(0) if hardcoded else ''!r}"
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN_PYTHON_WORKAROUNDS)
def test_production_entry_never_requires_a_bare_python_workaround(forbidden):
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_ENTRY_HEADING)
    assert forbidden not in _commands(section)


# --------------------------- exact, runnable bootstrap commands (PR #17) ---


def test_production_bootstrap_commands_carry_no_placeholders():
    """An operator must be able to paste the commands and run them."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    commands = _commands(section)
    placeholder = re.search(r"<[a-z][a-z0-9 _-]*>", commands)
    assert placeholder is None, (
        f"the bootstrap commands carry the placeholder {placeholder.group(0)!r}"
        if placeholder
        else ""
    )
    assert '"$PWD/requirements-dev.txt"' in commands, (
        "the dependency manifest must be resolved from the documented CWD"
    )


def test_production_bootstrap_uses_one_base_python_for_check_and_venv():
    """The interpreter whose version was checked is the one that builds the
    venv -- a second bare `python3` could resolve elsewhere."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    commands = _commands(section)
    assert 'BASE_PYTHON="$(command -v python3)"' in commands
    assert 'sudo "$BASE_PYTHON" -m venv' in commands
    assert not re.search(r"sudo python3(?:\.[0-9.]+)? -m venv", commands), (
        "the venv is created by a rediscovered python3 rather than $BASE_PYTHON"
    )


def test_production_bootstrap_compares_versions_by_shell_test():
    """Version agreement is asserted by the shell, not by the operator's eye."""
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    commands = _commands(section)
    assert 'EXPECTED_PYTHON_VERSION="$(cat .python-version)"' in commands
    compared = {
        variable
        for body in re.findall(
            r'test "\$\((.*?)\)" = "\$EXPECTED_PYTHON_VERSION"', commands, re.DOTALL
        )
        for variable in ("$BASE_PYTHON", "$PRODUCTION_PYTHON")
        if variable in body
    }
    assert compared == {"$BASE_PYTHON", "$PRODUCTION_PYTHON"}, (
        "both the base interpreter and the Production venv interpreter must be "
        f"compared against .python-version; compared: {sorted(compared)}"
    )


def test_production_bootstrap_asserts_the_canonical_identity_by_shell_test():
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    commands = _commands(section)
    assert PRODUCTION_PYTHON_FIXED_PATH_TEST in commands
    assert 'CANONICAL_PRODUCTION_PYTHON="$(' in commands
    assert (
        'test "$CANONICAL_PRODUCTION_PYTHON" = "$PRODUCTION_PYTHON"' in commands
    ), "the canonical resolver result must be compared, not printed for review"


def test_production_bootstrap_runs_pytest_under_the_production_python():
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert '"$PRODUCTION_PYTHON" -B -m pytest' in _commands(section)


def test_production_bootstrap_states_its_checkout_and_cwd_prerequisite():
    section = _section(_text("docs/nightly-operation.md"), PRODUCTION_BOOTSTRAP_HEADING)
    assert "Reviewed HEAD" in section
    assert "daytrade-sbi" in section


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

# ------------------------- DTWO-2026-021: policy lifecycle and prerequisites ---

POLICY_REPLACEMENT_HEADING = "### Policy replacement（Human専用）"


def test_policy_replacement_is_documented_as_a_separate_human_only_operation():
    """Installing and replacing are different operations with different risks."""
    section = _section(_text("docs/nightly-operation.md"), POLICY_REPLACEMENT_HEADING)
    assert "replace-claude-managed-policy" in section
    assert "EXISTING_MANAGED_POLICY_PRESENT" in section
    assert "--expected-installed-sha256" in section
    assert "--expected-rendered-sha256" in section
    assert "--check" in section
    # The installer's refusal must not be described as something to work around.
    assert "--force" not in _commands(section)


def test_policy_replacement_documents_the_fail_closed_conditions():
    section = _section(_text("docs/nightly-operation.md"), POLICY_REPLACEMENT_HEADING)
    for required in (
        "compare-and-swap",
        "os.replace",
        "Runtime Guard",
        "Human inspection required",
    ):
        assert required in section, required
    assert "手で編集しない" in section


def test_apparmor_is_documented_separately_for_native_linux_and_wsl():
    """TC-12: WSL has no such sysctl; that is not a licence to weaken anything."""
    text = _text("docs/nightly-operation.md")
    assert "kernel.apparmor_restrict_unprivileged_userns" in text
    assert "NOT APPLICABLE" in text
    assert "WSL2" in text
    # The absent sysctl must never be created, nor kernel security disabled.
    assert "sysctl`を新規作成しない" in text
    assert "kernel security設定を無効化しない" in text
    # Seccomp V2 stays mandatory on WSL.
    assert "DAYTRADE_SECCOMP_VERIFIED_V2" in text


def test_no_document_instructs_changing_the_apparmor_sysctl():
    for name in DOCS:
        text = _text(name)
        for forbidden in (
            "sysctl -w kernel.apparmor",
            "sysctl --write kernel.apparmor",
        ):
            assert forbidden not in text, f"{name} instructs changing the sysctl"


def test_http_user_agent_presence_checkpoint_precedes_preflight():
    """TC-13: presence only -- never a default, never the value itself."""
    section = _section(
        _text("docs/nightly-operation.md"), "### Production Entry Contract"
    )
    assert 'test -n "$DAYTRADE_HTTP_USER_AGENT"' in section
    assert "CLAUDE_HTTP_USER_AGENT_MISSING" in section
    assert "default" in section
    assert "値そのものをこのdocumentやrepository artifactへ書かない" in section


# --------------------------- DTWO-2026-830: Production Remote Control ---

REMOTE_CONTROL_HEADING = "### Production Remote Control（Human専用）"


def _section_with_subsections(text: str, heading: str) -> str:
    """A section including its own subsections.

    _section stops at the next heading of any level, which would truncate a
    section that organises itself with #### subheadings. This stops only at the
    next heading of the same or higher level.
    """
    assert heading in text, f"missing section heading: {heading}"
    level = len(heading) - len(heading.lstrip("#"))
    body = text[text.index(heading) + len(heading) :]
    following = re.search(rf"^#{{1,{level}}} ", body, re.MULTILINE)
    return body[: following.start()] if following else body


def test_remote_control_section_states_the_managed_policy_contract():
    """The four keys the policy pins, named where an operator will read them."""
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    for token in (
        "/remote-control",
        "disableRemoteControl",
        "remoteControlAtStartup",
        "crossSessionInbound",
        "SendMessage",
        "ListAgents",
        "2.1.224",
    ):
        assert token in section, token


def test_remote_control_activation_follows_preflight_and_status():
    """Activation is a human step in an already-verified session."""
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    for step in (
        "scripts/claude-production",
        "Runtime Security Preflight",
        "/status",
        "Enterprise managed settings",
    ):
        assert step in section, step
    assert section.index("Runtime Security Preflight") < section.index(
        "/remote-control"
    )


def test_remote_control_prohibits_the_launcher_and_server_start_modes():
    """The transport must never be started for the human."""
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    for prohibited in (
        "claude remote-control",
        "claude --remote-control",
        "remoteControlAtStartup=true",
    ):
        assert prohibited in section, prohibited
    assert "Launcherから自動でRemote Controlを開始しない" in section


def test_remote_control_does_not_widen_the_business_network_allowlist():
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    assert "Anthropic hostを追加しない" in section
    for control in (
        "strictAllowlist",
        "allowManagedDomainsOnly",
        "allowLocalBinding",
        "allowAllUnixSockets",
        "allowedDomains",
    ):
        assert control in section, control


def test_remote_control_forbids_attachments_and_stored_secrets():
    """AC-13/AC-14."""
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    assert "attachment" in section
    assert "Production v1" in section
    for secret in ("session URL", "QR", "token", "credential"):
        assert secret in section, secret
    assert "runtime_security.json" in section
    assert "Raw Evidence" in section


def test_remote_control_failure_is_not_a_business_result():
    """AC-15: a transport that did not connect decides nothing about a trade."""
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    for verdict in ("NO_TRADE", "DATA_UNAVAILABLE", "TRADE", "REJECTED"):
        assert verdict in section, verdict
    assert "Business Pipelineの結果では" in section


def test_remote_control_incident_path_uses_the_reviewed_replacement_lifecycle():
    section = _section_with_subsections(
        _text("docs/nightly-operation.md"), REMOTE_CONTROL_HEADING
    )
    assert "reviewed replacement" in section
    assert "Production Policyを直接編集しない" in section
