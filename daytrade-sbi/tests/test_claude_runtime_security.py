"""FIX-R2-004: the Claude Code production Managed Policy and its preflight.

Every check here is fixture-based. The real ``/etc`` is never read or written,
no package is installed, Claude Code is never started, and no socket is opened:
a tmp directory stands in for "the OS".
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from src import claude_runtime_security as crs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent

#: The Source Matrix template hosts of the CURRENT repository state. This is a
#: test expectation only -- production code derives it, never hardcodes it.
CURRENT_EXPECTED_DOMAINS = (
    "finance.yahoo.co.jp",
    "kabutan.jp",
    "www.jpx.co.jp",
    "www.release.tdnet.info",
)

PRODUCTION_PYTHON = str(Path(sys.executable).resolve())


# ------------------------------------------------------------- fixtures ---


def _matrix(entries) -> str:
    return json.dumps({"sources": entries})


def _registry(issuers=(), *, human_only=True, auto=False) -> str:
    return json.dumps(
        {
            "registry_schema_version": 1,
            "approval_policy": {
                "human_approved_only": human_only,
                "auto_discovery_allowed": auto,
            },
            "issuers": list(issuers),
        }
    )


@pytest.fixture()
def yaml_pair(tmp_path):
    def build(matrix_entries, issuers=(), **registry_kwargs):
        matrix = tmp_path / "source_matrix.yaml"
        registry = tmp_path / "issuer_domain_registry.yaml"
        matrix.write_text(_matrix(matrix_entries), encoding="utf-8")
        registry.write_text(_registry(issuers, **registry_kwargs), encoding="utf-8")
        return matrix, registry

    return build


def _source(source_id, url, scope="MATRIX_TEMPLATE_HOST"):
    return {
        "source_id": source_id,
        "url_template": url,
        "acquisition": {"network_scope": scope},
    }


@pytest.fixture()
def rendered():
    return crs.render_managed_settings(
        project_root=REPOSITORY_ROOT,
        daytrade_root=PROJECT_ROOT,
        production_python=PRODUCTION_PYTHON,
    )


@pytest.fixture()
def managed_root(tmp_path, rendered):
    """A fixture stand-in for /etc/claude-code."""
    root = tmp_path / "etc" / "claude-code"
    root.mkdir(parents=True)
    settings = root / crs.MANAGED_SETTINGS_FILENAME
    settings.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    guard = root / crs.MANAGED_GUARD_FILENAME
    guard.write_bytes(crs.RUNTIME_GUARD_SOURCE_PATH.read_bytes())
    for path in (settings, guard):
        os.chmod(path, 0o644)
    return root


def _install(managed_root, payload):
    path = managed_root / crs.MANAGED_SETTINGS_FILENAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


UID = os.getuid()


# ---------------------------------------------------- domain derivation ---


def test_current_repository_derives_exactly_the_four_template_hosts():
    assert crs.derive_expected_domains() == CURRENT_EXPECTED_DOMAINS


def test_derivation_is_a_pure_function_of_its_two_input_files(yaml_pair):
    matrix, registry = yaml_pair([_source("A", "https://example.co.jp/x")])
    assert crs.derive_expected_domains(matrix, registry) == ("example.co.jp",)


def test_issuer_placeholder_hosts_are_never_derived_from_the_matrix(yaml_pair):
    matrix, registry = yaml_pair(
        [
            _source("A", "https://example.co.jp/x"),
            _source("IR", "https://{issuer_domain}/ir/", "VERIFIED_ISSUER_DOMAIN"),
        ]
    )
    assert crs.derive_expected_domains(matrix, registry) == ("example.co.jp",)


def test_an_empty_issuer_registry_contributes_no_domain(yaml_pair):
    matrix, registry = yaml_pair([_source("A", "https://example.co.jp/x")], [])
    assert crs.derive_expected_domains(matrix, registry) == ("example.co.jp",)


def test_human_approved_issuer_hosts_are_added(yaml_pair):
    matrix, registry = yaml_pair(
        [_source("A", "https://example.co.jp/x")],
        [{"ticker": "7203", "approved_hosts": ["ir.issuer.co.jp"]}],
    )
    assert crs.derive_expected_domains(matrix, registry) == (
        "example.co.jp",
        "ir.issuer.co.jp",
    )


def test_duplicate_hosts_are_deduplicated_and_sorted(yaml_pair):
    matrix, registry = yaml_pair(
        [
            _source("B", "https://zzz.example.co.jp/2"),
            _source("A", "https://aaa.example.co.jp/1"),
            _source("C", "https://aaa.example.co.jp/3"),
        ]
    )
    assert crs.derive_expected_domains(matrix, registry) == (
        "aaa.example.co.jp",
        "zzz.example.co.jp",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.co.jp/x",
        "https://user:pw@example.co.jp/x",
        "https://example.co.jp:8443/x",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "https://EXAMPLE.co.jp/x",
    ],
)
def test_unsafe_matrix_hosts_are_rejected(yaml_pair, url):
    matrix, registry = yaml_pair([_source("A", url)])
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.derive_expected_domains(matrix, registry)
    assert excinfo.value.code in (
        "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
        "CLAUDE_MANAGED_POLICY_INVALID",
    )


@pytest.mark.parametrize(
    "host", ["*.jpx.co.jp", "", "localhost", "203.0.113.9", "WWW.JPX.CO.JP", "a.b:443"]
)
def test_validate_allowed_host_rejects_unsafe_entries(host):
    with pytest.raises(crs.RuntimeSecurityError):
        crs.validate_allowed_host(host)


def test_a_registry_that_permits_auto_discovery_is_rejected(yaml_pair):
    matrix, registry = yaml_pair([_source("A", "https://example.co.jp/x")], auto=True)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.derive_expected_domains(matrix, registry)
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"


def test_a_registry_without_human_approval_is_rejected(yaml_pair):
    matrix, registry = yaml_pair(
        [_source("A", "https://example.co.jp/x")], human_only=False
    )
    with pytest.raises(crs.RuntimeSecurityError):
        crs.derive_expected_domains(matrix, registry)


def test_the_live_issuer_registry_is_human_approved_only():
    payload = crs._load_yaml(crs.DEFAULT_ISSUER_REGISTRY_PATH)
    policy = payload["approval_policy"]
    assert policy["human_approved_only"] is True
    assert policy["auto_discovery_allowed"] is False


# ------------------------------------------------------------- template ---


def test_the_template_carries_every_required_placeholder():
    text = crs.MANAGED_SETTINGS_TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder in (
        "__PRODUCTION_PYTHON__",
        "__PROJECT_ROOT__",
        "__DAYTRADE_ROOT__",
        "__EVENT_EXTRACTION_PATH_PATTERN__",
        "__ALLOWED_DOMAINS_JSON__",
    ):
        assert placeholder in text


def test_the_template_is_not_directly_installable_json():
    """It is a template, not a finished /etc artifact."""
    with pytest.raises(ValueError):
        json.loads(crs.MANAGED_SETTINGS_TEMPLATE_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------- renderer ---


def test_rendered_policy_meets_the_managed_contract(rendered):
    assert rendered["requiredMinimumVersion"] == "2.1.219"
    permissions = rendered["permissions"]
    assert permissions["defaultMode"] == "dontAsk"
    assert permissions["disableBypassPermissionsMode"] == "disable"
    assert permissions["disableAutoMode"] == "disable"
    for rule in ("WebSearch", "WebFetch", "Agent", "mcp__*"):
        assert rule in permissions["deny"]
    assert rendered["allowManagedPermissionRulesOnly"] is True
    assert rendered["allowManagedHooksOnly"] is True
    assert rendered["allowManagedMcpServersOnly"] is True
    assert rendered["allowedMcpServers"] == []
    assert rendered["disableClaudeAiConnectors"] is True
    assert rendered["disableSkillShellExecution"] is True

    sandbox = rendered["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["autoAllowBashIfSandboxed"] is False
    assert sandbox["excludedCommands"] == []
    assert sandbox["filesystem"]["disabled"] is False

    network = sandbox["network"]
    assert network["strictAllowlist"] is True
    assert network["allowManagedDomainsOnly"] is True
    assert network["allowLocalBinding"] is False
    assert network["allowAllUnixSockets"] is False
    assert tuple(network["allowedDomains"]) == CURRENT_EXPECTED_DOMAINS


def test_no_sandbox_weakening_flag_is_present(rendered):
    sandbox = rendered["sandbox"]
    for flag in (
        "enableWeakerNestedSandbox",
        "enableWeakerNetworkIsolation",
        "allowAppleEvents",
    ):
        assert sandbox.get(flag) in (None, False)


def test_rendered_hooks_use_the_os_managed_guard_in_exec_form(rendered):
    hooks = rendered["hooks"]
    pre = hooks["PreToolUse"][0]
    assert pre["matcher"] == "Bash|Edit|Write|NotebookEdit"
    entry = pre["hooks"][0]
    assert entry["command"] == PRODUCTION_PYTHON
    assert entry["args"] == ["-I", "-B", str(crs.MANAGED_GUARD_PATH)]

    config = hooks["ConfigChange"][0]
    for source in (
        "user_settings",
        "project_settings",
        "local_settings",
        "skills",
        "policy_settings",
    ):
        assert source in config["matcher"]


def test_the_managed_hook_never_points_at_a_repository_path(rendered):
    blob = json.dumps(rendered)
    assert str(crs.RUNTIME_GUARD_SOURCE_PATH) not in blob
    assert "/etc/claude-code/daytrade-runtime-guard.py" in blob


def test_renderer_writes_nothing_to_disk(tmp_path, rendered):
    assert list(tmp_path.iterdir()) == []


def test_production_python_must_be_an_absolute_existing_file():
    with pytest.raises(crs.RuntimeSecurityError):
        crs.render_managed_settings(
            project_root=REPOSITORY_ROOT,
            daytrade_root=PROJECT_ROOT,
            production_python="python3",
        )
    with pytest.raises(crs.RuntimeSecurityError):
        crs.render_managed_settings(
            project_root=REPOSITORY_ROOT,
            daytrade_root=PROJECT_ROOT,
            production_python="/nonexistent/python3",
        )


def test_no_production_python_path_is_hardcoded_in_the_module():
    text = (PROJECT_ROOT / "src" / "claude_runtime_security.py").read_text("utf-8")
    assert "/usr/bin/python3" not in text


def test_the_expected_domain_set_is_not_hardcoded_in_production_code():
    for path in (
        PROJECT_ROOT / "src" / "claude_runtime_security.py",
        crs.MANAGED_SETTINGS_TEMPLATE_PATH,
    ):
        text = path.read_text("utf-8")
        for host in CURRENT_EXPECTED_DOMAINS:
            assert host not in text, f"{path.name} hardcodes {host}"


# ------------------------------------------- absolute permission paths ---


def test_absolute_edit_permission_pattern_uses_a_double_slash():
    assert crs.absolute_edit_permission_pattern(Path("/home/a/x")) == "//home/a/x"
    assert crs.absolute_edit_permission_pattern("/tmp/y.json") == "//tmp/y.json"


@pytest.mark.parametrize("value", ["runs/x.json", "", "./x", "../x"])
def test_a_relative_permission_pattern_is_a_hard_error(value):
    with pytest.raises(crs.RuntimeSecurityError):
        crs.absolute_edit_permission_pattern(value)


def test_the_rendered_edit_rule_uses_the_absolute_permission_form(rendered):
    edit_rules = [
        rule for rule in rendered["permissions"]["allow"] if rule.startswith("Edit(")
    ]
    assert edit_rules, "the event extraction Edit rule disappeared"
    expected = "Edit(" + crs.absolute_edit_permission_pattern(
        PROJECT_ROOT / "runs" / "*" / "working" / "event_source_extraction.json"
    ) + ")"
    assert edit_rules == [expected]
    for rule in edit_rules:
        assert rule.startswith("Edit(//")
        assert not rule.startswith("Edit(///")


def test_a_single_slash_edit_rule_is_rejected(rendered):
    single = "Edit(" + str(
        PROJECT_ROOT / "runs" / "*" / "working" / "event_source_extraction.json"
    ) + ")"
    assert single not in rendered["permissions"]["allow"]
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_edit_permission_rules(["Bash(x *)", single])
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"


def test_a_policy_whose_edit_rule_was_downgraded_is_rejected(rendered):
    payload = json.loads(json.dumps(rendered))
    payload["permissions"]["allow"] = [
        rule.replace("Edit(//", "Edit(/") for rule in payload["permissions"]["allow"]
    ]
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_managed_settings_contract(
            payload, expected_domains=CURRENT_EXPECTED_DOMAINS
        )
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"


def test_a_policy_without_any_edit_rule_is_rejected(rendered):
    payload = json.loads(json.dumps(rendered))
    payload["permissions"]["allow"] = ["Bash(x *)"]
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_managed_settings_contract(
            payload, expected_domains=CURRENT_EXPECTED_DOMAINS
        )
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"


# ------------------------------------------------- managed hardening ---


def test_the_rendered_policy_disables_sideload_flags_and_remote_control(rendered):
    assert rendered["disableSideloadFlags"] is True
    assert rendered["disableRemoteControl"] is True


@pytest.mark.parametrize(
    "key", ["disableSideloadFlags", "disableRemoteControl"]
)
@pytest.mark.parametrize("mutate", ["false", "missing"])
def test_a_policy_without_the_new_hardening_flags_is_rejected(rendered, key, mutate):
    payload = json.loads(json.dumps(rendered))
    if mutate == "false":
        payload[key] = False
    else:
        payload.pop(key)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_managed_settings_contract(
            payload, expected_domains=CURRENT_EXPECTED_DOMAINS
        )
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"


# ---------------------------------------------------------- target date ---


def test_a_canonical_target_date_is_accepted():
    assert crs.validate_target_date("2026-08-15") == "2026-08-15"


@pytest.mark.parametrize(
    "value",
    [
        "../config",
        "../../src",
        "/tmp",
        "2026-8-15",
        "2026-02-30",
        "2026-08-15/../../config",
        " 2026-08-15",
        "2026-08-15 ",
        "",
        "2026-13-01",
        "20260815",
        "2026-08-15/foo",
        "2026-08-15\n",
    ],
)
def test_every_non_canonical_target_date_is_a_hard_error(value):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.validate_target_date(value)
    assert excinfo.value.code == "CLAUDE_TARGET_DATE_INVALID"


def test_run_dir_stays_directly_under_runs(tmp_path):
    run_dir = crs.resolve_run_dir(tmp_path, "2026-08-15")
    assert run_dir == (tmp_path / "runs" / "2026-08-15").resolve()
    assert run_dir.parent == (tmp_path / "runs").resolve()
    assert run_dir.name == "2026-08-15"


@pytest.mark.parametrize("value", ["../config", "2026-08-15/../../config", "/tmp"])
def test_run_dir_refuses_to_escape_the_runs_root(tmp_path, value):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.resolve_run_dir(tmp_path, value)
    assert excinfo.value.code == "CLAUDE_TARGET_DATE_INVALID"


# ------------------------------------------------------------- versions ---


def test_claude_version_gate_accepts_the_minimum_and_above():
    assert crs.verify_claude_version("2.1.219 (Claude Code)") == (2, 1, 219)
    assert crs.verify_claude_version("2.2.0") == (2, 2, 0)


@pytest.mark.parametrize("text", ["2.1.218", "2.0.999", "1.9.9"])
def test_old_claude_versions_are_unsupported(text):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_claude_version(text)
    assert excinfo.value.code == "CLAUDE_RUNTIME_VERSION_UNSUPPORTED"


@pytest.mark.parametrize("text", ["", "unknown", "vNEXT"])
def test_unparseable_claude_versions_are_invalid(text):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_claude_version(text)
    assert excinfo.value.code == "CLAUDE_RUNTIME_VERSION_INVALID"


def test_non_linux_platforms_are_unsupported():
    crs.verify_platform("Linux")
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_platform("Windows")
    assert excinfo.value.code == "CLAUDE_RUNTIME_OS_UNSUPPORTED"


# ------------------------------------------------- environment prereqs ---


def test_production_marker_must_exist_with_exact_content(tmp_path):
    marker = tmp_path / "daytrade-production-runtime"
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_production_marker(marker, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_PRODUCTION_MARKER_MISSING"

    marker.write_text("SOMETHING ELSE\n", encoding="utf-8")
    with pytest.raises(crs.RuntimeSecurityError):
        crs.verify_production_marker(marker, expected_uid=UID)

    marker.write_text(crs.PRODUCTION_MARKER_CONTENT + "\n", encoding="utf-8")
    os.chmod(marker, 0o644)
    crs.verify_production_marker(marker, expected_uid=UID)


# --------------------------------------- root-owned attestation markers ---


def _marker(tmp_path, name, content, mode=0o644):
    path = tmp_path / name
    path.write_text(content + "\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_the_production_marker_must_be_root_owned(tmp_path):
    marker = _marker(
        tmp_path, "daytrade-production-runtime", crs.PRODUCTION_MARKER_CONTENT
    )
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_production_marker(marker, expected_uid=UID + 4242)
    assert excinfo.value.code == "CLAUDE_PRODUCTION_MARKER_PERMISSION_INVALID"


@pytest.mark.parametrize("mode", [0o666, 0o646, 0o664])
def test_a_group_or_world_writable_production_marker_fails(tmp_path, mode):
    marker = _marker(
        tmp_path, "daytrade-production-runtime", crs.PRODUCTION_MARKER_CONTENT, mode
    )
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_production_marker(marker, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_PRODUCTION_MARKER_PERMISSION_INVALID"


def test_a_production_marker_directory_is_not_a_marker(tmp_path):
    directory = tmp_path / "daytrade-production-runtime"
    directory.mkdir()
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_production_marker(directory, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_PRODUCTION_MARKER_MISSING"


def test_the_seccomp_attestation_marker_contract():
    assert crs.SECCOMP_MARKER_PATH == Path("/etc/daytrade-seccomp-verified")
    assert crs.SECCOMP_MARKER_CONTENT == "DAYTRADE_SECCOMP_VERIFIED_V2"


def test_a_valid_seccomp_marker_passes(tmp_path):
    marker = _marker(tmp_path, "daytrade-seccomp-verified", crs.SECCOMP_MARKER_CONTENT)
    crs.verify_seccomp_marker(marker, expected_uid=UID)


def test_a_missing_seccomp_marker_is_unverified(tmp_path):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_seccomp_marker(tmp_path / "absent", expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_a_seccomp_marker_with_wrong_content_is_unverified(tmp_path):
    marker = _marker(tmp_path, "daytrade-seccomp-verified", "SECCOMP_PROBABLY_FINE")
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_seccomp_marker(marker, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_a_stale_v1_seccomp_marker_is_not_migrated_and_fails_closed(tmp_path):
    """V1 -> V2 is not an automatic migration: an old V1 marker must fail closed."""
    marker = _marker(
        tmp_path, "daytrade-seccomp-verified", "DAYTRADE_SECCOMP_VERIFIED_V1"
    )
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_seccomp_marker(marker, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_a_non_root_owned_seccomp_marker_is_unverified(tmp_path):
    marker = _marker(tmp_path, "daytrade-seccomp-verified", crs.SECCOMP_MARKER_CONTENT)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_seccomp_marker(marker, expected_uid=UID + 4242)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


@pytest.mark.parametrize("mode", [0o666, 0o646, 0o664])
def test_a_writable_seccomp_marker_is_unverified(tmp_path, mode):
    marker = _marker(
        tmp_path, "daytrade-seccomp-verified", crs.SECCOMP_MARKER_CONTENT, mode
    )
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_seccomp_marker(marker, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_a_seccomp_marker_directory_is_unverified(tmp_path):
    directory = tmp_path / "daytrade-seccomp-verified"
    directory.mkdir()
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_seccomp_marker(directory, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_the_repository_never_creates_the_seccomp_marker():
    """Section 12: the marker is a Human Runtime Acceptance artifact only."""
    for path in (
        PROJECT_ROOT / "src" / "claude_runtime_security.py",
        PROJECT_ROOT / "src" / "claude_production_launcher.py",
        PROJECT_ROOT / "scripts" / "claude-production",
        PROJECT_ROOT / "scripts" / "deploy-claude-managed-policy",
    ):
        text = path.read_text("utf-8")
        for creator in ("write_text(", "write_bytes(", "touch("):
            for line in text.splitlines():
                if creator in line and "seccomp" in line.lower():
                    raise AssertionError(f"{path.name} may create the marker: {line}")


def test_seccomp_is_never_guessed_from_the_running_kernel():
    text = (PROJECT_ROOT / "src" / "claude_production_launcher.py").read_text("utf-8")
    assert "verify_seccomp_marker" in text
    for guess in ("SCMP_", "prctl", "/proc/self/status"):
        assert guess not in text


# ------------------------------------------------------------------ wsl ---


def test_wsl2_is_detected_from_existing_kernel_strings():
    assert crs.detect_wsl2("5.15.153.1-microsoft-standard-WSL2", "") is True
    assert crs.detect_wsl2("", "Linux version 5.15.0 (Microsoft@WSL)") is True
    assert crs.detect_wsl2("6.6.0-generic", "Linux version 6.6.0 (ubuntu)") is False


def test_native_linux_also_requires_the_seccomp_attestation():
    """Section 14: one rule for every Linux host -- the marker is mandatory."""
    text = (PROJECT_ROOT / "src" / "claude_production_launcher.py").read_text("utf-8")
    assert "detect_wsl2" not in text
    assert "read_wsl2_marker" not in text


def test_missing_sandbox_dependencies_halt_instead_of_installing():
    crs.verify_sandbox_dependencies({"bwrap": "/usr/bin/bwrap", "socat": "/usr/bin/socat"})
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_sandbox_dependencies({"bwrap": "/usr/bin/bwrap", "socat": None})
    assert excinfo.value.code == "CLAUDE_SANDBOX_DEPENDENCY_MISSING"


@pytest.mark.parametrize("state", [None, False])
def test_unverified_seccomp_is_a_hard_stop_not_an_assumption(state):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_sandbox_seccomp(state)
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_unverified_seccomp_message_points_to_v2_runtime_acceptance():
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_sandbox_seccomp(False)

    message = excinfo.value.message
    assert "Human Runtime Acceptance v2" in message
    assert "Sandbox-outside" in message
    assert "Sandbox-inside" in message
    assert "differential probe" in message
    assert "/sandbox Dependencies" not in message


def test_verified_seccomp_passes():
    crs.verify_sandbox_seccomp(True)


def test_http_user_agent_must_be_configured():
    assert crs.verify_http_user_agent({"DAYTRADE_HTTP_USER_AGENT": "ua/1"}) is True
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_http_user_agent({})
    assert excinfo.value.code == "CLAUDE_HTTP_USER_AGENT_MISSING"


def test_runtime_profile_must_be_production():
    crs.verify_runtime_profile({"DAYTRADE_RUNTIME_PROFILE": "production"})
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_runtime_profile({"DAYTRADE_RUNTIME_PROFILE": "development"})
    assert excinfo.value.code == "CLAUDE_RUNTIME_PROFILE_MISSING"


# ------------------------------------------------------- dirty git tree ---


@pytest.mark.parametrize(
    "line",
    [
        " M daytrade-sbi/src/ranking.py",
        "?? daytrade-sbi/config/evil.yaml",
        " D daytrade-sbi/schemas/ranking.schema.json",
        " M daytrade-sbi/ops/claude/daytrade_runtime_guard.py",
        " M daytrade-sbi/scripts/claude-production",
        " M .claude/settings.json",
        " M CLAUDE.md",
        " M daytrade-sbi/AGENTS.md",
    ],
)
def test_a_dirty_protected_tree_halts_the_run(line):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_source_tree_clean(line + "\n")
    assert excinfo.value.code == "CLAUDE_SOURCE_TREE_DIRTY"


def test_run_output_alone_does_not_make_the_tree_dirty():
    crs.verify_source_tree_clean("?? daytrade-sbi/runs/2026-08-14/working/x.json\n")
    crs.verify_source_tree_clean("")


# ------------------------------------------------- installed policy check ---


def test_a_correctly_installed_policy_passes(managed_root, rendered):
    sha = crs.verify_installed_managed_settings(
        managed_root / crs.MANAGED_SETTINGS_FILENAME,
        expected=rendered,
        expected_uid=UID,
    )
    assert len(sha) == 64


def test_missing_managed_settings_fail(tmp_path, rendered):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_managed_settings(
            tmp_path / "managed-settings.json", expected=rendered, expected_uid=UID
        )
    assert excinfo.value.code == "CLAUDE_MANAGED_SETTINGS_MISSING"


def test_a_wrongly_owned_managed_settings_file_fails(managed_root, rendered):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_managed_settings(
            managed_root / crs.MANAGED_SETTINGS_FILENAME,
            expected=rendered,
            expected_uid=UID + 4242,
        )
    assert excinfo.value.code == "CLAUDE_MANAGED_SETTINGS_PERMISSION_INVALID"


@pytest.mark.parametrize("mode", [0o666, 0o646])
def test_a_group_or_world_writable_managed_settings_file_fails(
    managed_root, rendered, mode
):
    path = managed_root / crs.MANAGED_SETTINGS_FILENAME
    os.chmod(path, mode)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_managed_settings(path, expected=rendered, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_MANAGED_SETTINGS_PERMISSION_INVALID"


def test_semantic_comparison_ignores_json_key_order(managed_root, rendered):
    path = managed_root / crs.MANAGED_SETTINGS_FILENAME
    path.write_text(json.dumps(rendered, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o644)
    crs.verify_installed_managed_settings(path, expected=rendered, expected_uid=UID)


def test_a_semantically_different_policy_fails(managed_root, rendered):
    tampered = json.loads(json.dumps(rendered))
    tampered["disableAgentView"] = False
    path = _install(managed_root, tampered)
    os.chmod(path, 0o644)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_managed_settings(path, expected=rendered, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"


def _tamper(rendered, mutate):
    payload = json.loads(json.dumps(rendered))
    mutate(payload)
    return payload


@pytest.mark.parametrize(
    "mutate,code",
    [
        (
            lambda p: p["sandbox"]["network"].__setitem__("strictAllowlist", False),
            "CLAUDE_NETWORK_STRICT_ALLOWLIST_DISABLED",
        ),
        (
            lambda p: p["sandbox"]["network"].__setitem__("allowManagedDomainsOnly", False),
            "CLAUDE_NETWORK_MANAGED_DOMAIN_LOCK_DISABLED",
        ),
        (
            lambda p: p.__setitem__("allowManagedHooksOnly", False),
            "CLAUDE_HOOKS_NOT_MANAGED_ONLY",
        ),
        (
            lambda p: p.__setitem__("allowManagedPermissionRulesOnly", False),
            "CLAUDE_PERMISSIONS_NOT_MANAGED_ONLY",
        ),
        (
            lambda p: p.__setitem__("allowedMcpServers", ["github"]),
            "CLAUDE_MCP_NOT_LOCKED_DOWN",
        ),
        (
            lambda p: p.__setitem__("allowManagedMcpServersOnly", False),
            "CLAUDE_MCP_NOT_LOCKED_DOWN",
        ),
        (
            lambda p: p["sandbox"].__setitem__("excludedCommands", ["git"]),
            "CLAUDE_SANDBOX_EXCLUDED_COMMANDS_PRESENT",
        ),
        (
            lambda p: p["sandbox"]["filesystem"].__setitem__("disabled", True),
            "CLAUDE_SANDBOX_FILESYSTEM_DISABLED",
        ),
        (
            lambda p: p["sandbox"].__setitem__("enabled", False),
            "CLAUDE_SANDBOX_NOT_REQUIRED",
        ),
        (
            lambda p: p["sandbox"].__setitem__("failIfUnavailable", False),
            "CLAUDE_SANDBOX_NOT_REQUIRED",
        ),
        (
            lambda p: p["sandbox"].__setitem__("allowUnsandboxedCommands", True),
            "CLAUDE_SANDBOX_ESCAPE_ENABLED",
        ),
        (
            lambda p: p["sandbox"].__setitem__("enableWeakerNestedSandbox", True),
            "CLAUDE_SANDBOX_ESCAPE_ENABLED",
        ),
        (
            lambda p: p["sandbox"].__setitem__("enableWeakerNetworkIsolation", True),
            "CLAUDE_SANDBOX_ESCAPE_ENABLED",
        ),
        (
            lambda p: p["permissions"].__setitem__("defaultMode", "acceptEdits"),
            "CLAUDE_MANAGED_POLICY_INVALID",
        ),
        (
            lambda p: p.__setitem__("requiredMinimumVersion", "2.0.0"),
            "CLAUDE_MANAGED_POLICY_INVALID",
        ),
        (
            lambda p: p["permissions"]["deny"].remove("Agent"),
            "CLAUDE_MANAGED_POLICY_INVALID",
        ),
        (
            lambda p: p["permissions"]["deny"].remove("mcp__*"),
            "CLAUDE_MANAGED_POLICY_INVALID",
        ),
        (
            lambda p: p["sandbox"]["network"]["allowedDomains"].append("github.com"),
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
        ),
        (
            lambda p: p["sandbox"]["network"]["allowedDomains"].pop(),
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
        ),
        (
            lambda p: p["sandbox"]["network"]["allowedDomains"].append("*.example.com"),
            "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
        ),
        (lambda p: p.__setitem__("hooks", {}), "CLAUDE_MANAGED_POLICY_INVALID"),
    ],
)
def test_every_weakened_managed_policy_is_rejected(rendered, mutate, code):
    payload = _tamper(rendered, mutate)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_managed_settings_contract(
            payload, expected_domains=CURRENT_EXPECTED_DOMAINS
        )
    assert excinfo.value.code == code


@pytest.mark.parametrize(
    "host", ["github.com", "api.github.com", "pypi.org", "npmjs.org"]
)
def test_a_single_extra_developer_host_fails_the_domain_sync(host):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_domain_sync(
            list(CURRENT_EXPECTED_DOMAINS) + [host], CURRENT_EXPECTED_DOMAINS
        )
    assert excinfo.value.code == "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH"


def test_domain_sync_accepts_the_exact_set():
    assert crs.verify_domain_sync(
        list(reversed(CURRENT_EXPECTED_DOMAINS)), CURRENT_EXPECTED_DOMAINS
    ) == CURRENT_EXPECTED_DOMAINS


# ---------------------------------------------------------- guard check ---


def test_the_installed_guard_must_match_the_repository_source(managed_root):
    sha = crs.verify_installed_runtime_guard(
        managed_root / crs.MANAGED_GUARD_FILENAME, expected_uid=UID
    )
    assert sha == crs.sha256_file(crs.RUNTIME_GUARD_SOURCE_PATH)


def test_a_missing_installed_guard_fails(tmp_path):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_runtime_guard(tmp_path / "guard.py", expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_MANAGED_GUARD_MISSING"


def test_a_tampered_installed_guard_fails(managed_root):
    path = managed_root / crs.MANAGED_GUARD_FILENAME
    path.write_text("print('pwned')\n", encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_runtime_guard(path, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_MANAGED_GUARD_MISMATCH"


def test_a_world_writable_guard_fails(managed_root):
    path = managed_root / crs.MANAGED_GUARD_FILENAME
    os.chmod(path, 0o666)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_runtime_guard(path, expected_uid=UID)
    assert excinfo.value.code == "CLAUDE_MANAGED_GUARD_PERMISSION_INVALID"


def test_a_wrongly_owned_guard_fails(managed_root):
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.verify_installed_runtime_guard(
            managed_root / crs.MANAGED_GUARD_FILENAME, expected_uid=UID + 4242
        )
    assert excinfo.value.code == "CLAUDE_MANAGED_GUARD_PERMISSION_INVALID"


# --------------------------------------------------------------- deploy ---


def test_deploy_installs_both_files_into_a_fixture_root(tmp_path, rendered):
    target = tmp_path / "claude-code"
    result = crs.deploy_managed_policy(target_root=target, rendered_settings=rendered)
    settings = target / crs.MANAGED_SETTINGS_FILENAME
    guard = target / crs.MANAGED_GUARD_FILENAME
    assert settings.is_file() and guard.is_file()
    assert result["managed_runtime_guard_sha256"] == crs.sha256_file(
        crs.RUNTIME_GUARD_SOURCE_PATH
    )
    assert not (settings.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    assert not (guard.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    crs.verify_installed_managed_settings(settings, expected=rendered, expected_uid=UID)


def test_deploy_refuses_to_overwrite_an_existing_managed_policy(tmp_path, rendered):
    target = tmp_path / "claude-code"
    target.mkdir()
    existing = target / crs.MANAGED_SETTINGS_FILENAME
    existing.write_text('{"organisation": "policy"}', encoding="utf-8")
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.deploy_managed_policy(target_root=target, rendered_settings=rendered)
    assert excinfo.value.code == "EXISTING_MANAGED_POLICY_PRESENT"
    assert existing.read_text(encoding="utf-8") == '{"organisation": "policy"}'


def test_deploy_refuses_a_divergent_existing_guard(tmp_path, rendered):
    target = tmp_path / "claude-code"
    target.mkdir()
    guard = target / crs.MANAGED_GUARD_FILENAME
    guard.write_text("print('old')\n", encoding="utf-8")
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        crs.deploy_managed_policy(target_root=target, rendered_settings=rendered)
    assert excinfo.value.code == "EXISTING_MANAGED_POLICY_PRESENT"
    assert guard.read_text(encoding="utf-8") == "print('old')\n"


def test_deploy_refuses_a_weakened_policy(tmp_path, rendered):
    weakened = _tamper(
        rendered, lambda p: p["sandbox"]["network"].__setitem__("strictAllowlist", False)
    )
    with pytest.raises(crs.RuntimeSecurityError):
        crs.deploy_managed_policy(
            target_root=tmp_path / "claude-code", rendered_settings=weakened
        )


def test_the_deploy_script_has_no_force_option():
    text = (PROJECT_ROOT / "scripts" / "deploy-claude-managed-policy").read_text("utf-8")
    assert 'add_argument("--force"' not in text
    assert "geteuid" in text


def test_deploy_script_targets_etc_claude_code_by_default():
    assert crs.DEFAULT_MANAGED_ROOT == Path("/etc/claude-code")
    assert crs.MANAGED_GUARD_PATH == Path("/etc/claude-code/daytrade-runtime-guard.py")


# ------------------------------------------------------------- artifact ---


def _passing_checks():
    return {name: "PASS" for name in crs.RUNTIME_SECURITY_CHECKS}


def _document(**overrides):
    kwargs = dict(
        target_date="2026-08-14",
        platform_name="Linux-6.6.0-x86_64",
        claude_code_version="2.1.219",
        git_head_sha="0" * 40,
        production_python=PRODUCTION_PYTHON,
        managed_settings_sha256="a" * 64,
        managed_runtime_guard_sha256="b" * 64,
        allowed_domains=CURRENT_EXPECTED_DOMAINS,
        checks=_passing_checks(),
        http_user_agent_present=True,
    )
    kwargs.update(overrides)
    return crs.build_runtime_security_document(**kwargs)


def test_runtime_security_document_matches_its_schema():
    from src.contracts import validate_json_document

    document = _document()
    validate_json_document(document, "runtime_security.schema.json")
    assert document["schema_version"] == 1
    assert document["runtime_profile"] == "production"
    assert set(document["checks"]) == set(crs.RUNTIME_SECURITY_CHECKS)


def test_the_artifact_is_only_built_when_every_check_passed():
    checks = _passing_checks()
    checks["domain_sync"] = "FAIL"
    with pytest.raises(crs.RuntimeSecurityError):
        _document(checks=checks)
    partial = {k: "PASS" for k in list(crs.RUNTIME_SECURITY_CHECKS)[:3]}
    with pytest.raises(crs.RuntimeSecurityError):
        _document(checks=partial)


def test_the_artifact_never_records_a_secret_or_the_user_agent_value():
    document = _document()
    blob = json.dumps(document)
    assert "DAYTRADE_HTTP_USER_AGENT" not in blob
    assert document["http_user_agent_present"] is True
    for banned in ("token", "api_key", "cookie", "credential", "password"):
        assert banned not in blob.lower()


# ------------------------------------------------------ launcher core ---


HEAD_SHA = "c" * 40


@pytest.fixture()
def production_world(tmp_path):
    """A complete fixture stand-in for a provisioned production host."""
    from src import claude_production_launcher as launcher

    daytrade_root = tmp_path / "repo" / "daytrade-sbi"
    (daytrade_root / "runs").mkdir(parents=True)

    etc = tmp_path / "etc"
    managed = etc / "claude-code"
    managed.mkdir(parents=True)
    guard_path = managed / crs.MANAGED_GUARD_FILENAME
    guard_path.write_bytes(crs.RUNTIME_GUARD_SOURCE_PATH.read_bytes())
    os.chmod(guard_path, 0o755)

    settings_payload = crs.render_managed_settings(
        project_root=daytrade_root.parent,
        daytrade_root=daytrade_root,
        production_python=PRODUCTION_PYTHON,
        guard_path=guard_path,
    )
    settings_path = managed / crs.MANAGED_SETTINGS_FILENAME
    settings_path.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")
    os.chmod(settings_path, 0o644)

    production_marker = _marker(
        etc, "daytrade-production-runtime", crs.PRODUCTION_MARKER_CONTENT
    )
    seccomp_marker = _marker(
        etc, "daytrade-seccomp-verified", crs.SECCOMP_MARKER_CONTENT
    )

    calls: list[list[str]] = []

    def run_command(argv, cwd):
        calls.append(list(argv))
        if argv[:2] == ["git", "status"]:
            return ""
        if argv[:2] == ["git", "rev-parse"]:
            return HEAD_SHA + "\n"
        if argv[0] == "claude":
            return "2.1.219 (Claude Code)\n"
        raise AssertionError(f"unexpected command {argv}")

    kwargs = dict(
        daytrade_root=daytrade_root,
        managed_settings_path=settings_path,
        managed_guard_path=guard_path,
        production_marker_path=production_marker,
        seccomp_marker_path=seccomp_marker,
        expected_uid=UID,
        environ={
            "DAYTRADE_PRODUCTION_PYTHON": PRODUCTION_PYTHON,
            "DAYTRADE_HTTP_USER_AGENT": "daytrade/1.0",
        },
        run_command=run_command,
        which=lambda name: f"/usr/bin/{name}",
        platform_system=lambda: "Linux",
        platform_name=lambda: "Linux-6.6.0-x86_64",
    )
    return {
        "launcher": launcher,
        "kwargs": kwargs,
        "daytrade_root": daytrade_root,
        "etc": etc,
        "tmp_path": tmp_path,
        "calls": calls,
        "seccomp_marker": seccomp_marker,
        "production_marker": production_marker,
    }


def _tree(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def test_the_launcher_preflight_passes_on_a_provisioned_host(production_world):
    from src.contracts import validate_json_document

    world = production_world
    result = world["launcher"].preflight(target_date="2026-08-15", **world["kwargs"])

    artifact = world["daytrade_root"] / "runs" / "2026-08-15" / "working" / "runtime_security.json"
    assert artifact.is_file()
    document = json.loads(artifact.read_text("utf-8"))
    validate_json_document(document, "runtime_security.schema.json")
    assert document["checks"]["sandbox_seccomp"] == "PASS"
    assert set(document["checks"]) == set(crs.RUNTIME_SECURITY_CHECKS)
    assert document["target_date"] == "2026-08-15"
    assert document["git_head_sha"] == HEAD_SHA
    assert result["run_dir"] == artifact.parent.parent


def test_the_launcher_records_sandbox_seccomp_as_a_runtime_check():
    assert "sandbox_seccomp" in crs.RUNTIME_SECURITY_CHECKS
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "runtime_security.schema.json").read_text("utf-8")
    )
    assert "sandbox_seccomp" in schema["properties"]["checks"]["required"]


def test_without_the_seccomp_marker_nothing_is_written_and_claude_never_starts(
    production_world,
):
    world = production_world
    world["seccomp_marker"].unlink()
    before = _tree(world["daytrade_root"])
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        world["launcher"].preflight(target_date="2026-08-15", **world["kwargs"])
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"
    assert _tree(world["daytrade_root"]) == before
    assert ["claude", "--version"] not in world["calls"]


def test_a_tampered_seccomp_marker_halts_the_launcher(production_world):
    world = production_world
    world["seccomp_marker"].write_text("DAYTRADE_SECCOMP_VERIFIED_V0\n", encoding="utf-8")
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        world["launcher"].preflight(target_date="2026-08-15", **world["kwargs"])
    assert excinfo.value.code == "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED"


def test_a_wrongly_owned_production_marker_halts_the_launcher(production_world):
    world = production_world
    world["kwargs"]["expected_uid"] = UID + 4242
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        world["launcher"].preflight(target_date="2026-08-15", **world["kwargs"])
    assert excinfo.value.code == "CLAUDE_PRODUCTION_MARKER_PERMISSION_INVALID"


def test_the_seccomp_marker_is_checked_before_claude_or_git(production_world):
    """Section 16: platform, production marker, seccomp marker, then the rest."""
    world = production_world
    world["seccomp_marker"].unlink()
    with pytest.raises(crs.RuntimeSecurityError):
        world["launcher"].preflight(target_date="2026-08-15", **world["kwargs"])
    assert world["calls"] == []


@pytest.mark.parametrize(
    "value", ["../config", "../../src", "/tmp", "2026-8-15", "2026-02-30", " 2026-08-15"]
)
def test_launcher_traversal_target_dates_are_rejected_before_any_write(
    production_world, value
):
    world = production_world
    before = _tree(world["tmp_path"])
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        world["launcher"].preflight(target_date=value, **world["kwargs"])
    assert excinfo.value.code == "CLAUDE_TARGET_DATE_INVALID"
    assert _tree(world["tmp_path"]) == before
    assert world["calls"] == []


def test_launcher_main_returns_non_zero_for_a_traversal_target_date(production_world):
    world = production_world
    repo_root = world["daytrade_root"].parent
    config_dir = world["daytrade_root"] / "config"
    config_dir.mkdir()
    before = _tree(world["tmp_path"])

    code = world["launcher"].main(["--target-date", "../config"], **world["kwargs"])

    assert code != 0
    assert not (config_dir / "working" / "runtime_security.json").exists()
    assert not (repo_root / "config").exists()
    assert _tree(world["tmp_path"]) == before
    assert list((world["daytrade_root"] / "runs").iterdir()) == []


def test_launcher_main_preflight_only_does_not_start_claude(production_world):
    world = production_world
    code = world["launcher"].main(
        ["--target-date", "2026-08-15", "--preflight-only"], **world["kwargs"]
    )
    assert code == 0
    assert ["claude", "--version"] in world["calls"]


def test_a_schema_invalid_runtime_security_document_is_never_written(
    production_world, monkeypatch
):
    world = production_world
    launcher = world["launcher"]

    def broken(**kwargs):
        document = crs.build_runtime_security_document(**kwargs)
        document["git_head_sha"] = "not-a-sha"
        return document

    monkeypatch.setattr(launcher, "build_runtime_security_document", broken)
    with pytest.raises(crs.RuntimeSecurityError) as excinfo:
        launcher.preflight(target_date="2026-08-15", **world["kwargs"])
    assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"
    assert not (
        world["daytrade_root"] / "runs" / "2026-08-15" / "working"
        / "runtime_security.json"
    ).exists()


def test_the_launcher_writes_the_artifact_atomically():
    text = (PROJECT_ROOT / "src" / "claude_production_launcher.py").read_text("utf-8")
    assert "atomic_write_text" in text
    assert "validate_json_document" in text
    assert ".write_text(" not in text


def test_the_launcher_cli_exposes_no_security_boundary_override():
    parser_source = (
        PROJECT_ROOT / "src" / "claude_production_launcher.py"
    ).read_text("utf-8")
    for forbidden in ("--managed-settings", "--managed-guard", "--marker"):
        assert f'add_argument("{forbidden}"' not in parser_source
    script = (PROJECT_ROOT / "scripts" / "claude-production").read_text("utf-8")
    for forbidden in ("--managed-settings", "--managed-guard", "--marker"):
        assert forbidden not in script

    from src import claude_production_launcher as launcher

    options = {
        option
        for action in launcher.build_parser()._actions
        for option in action.option_strings
    }
    assert options == {"-h", "--help", "--target-date", "--preflight-only"}


def test_the_launcher_script_stays_a_thin_entry_point():
    script = (PROJECT_ROOT / "scripts" / "claude-production").read_text("utf-8")
    assert "from src.claude_production_launcher import main" in script
    assert "os.execvpe" not in script


# ---------------------------------------------------------- vocabulary ---


REQUIRED_ERROR_CODES = (
    "CLAUDE_RUNTIME_OS_UNSUPPORTED",
    "CLAUDE_RUNTIME_VERSION_INVALID",
    "CLAUDE_RUNTIME_VERSION_UNSUPPORTED",
    "CLAUDE_PRODUCTION_MARKER_MISSING",
    "CLAUDE_PRODUCTION_MARKER_PERMISSION_INVALID",
    "CLAUDE_TARGET_DATE_INVALID",
    "CLAUDE_SANDBOX_DEPENDENCY_MISSING",
    "CLAUDE_SANDBOX_SECCOMP_UNVERIFIED",
    "CLAUDE_MANAGED_SETTINGS_MISSING",
    "CLAUDE_MANAGED_SETTINGS_PERMISSION_INVALID",
    "CLAUDE_MANAGED_POLICY_INVALID",
    "CLAUDE_MANAGED_GUARD_MISSING",
    "CLAUDE_MANAGED_GUARD_PERMISSION_INVALID",
    "CLAUDE_MANAGED_GUARD_MISMATCH",
    "CLAUDE_NETWORK_STRICT_ALLOWLIST_DISABLED",
    "CLAUDE_NETWORK_MANAGED_DOMAIN_LOCK_DISABLED",
    "CLAUDE_NETWORK_DOMAIN_SET_MISMATCH",
    "CLAUDE_MCP_NOT_LOCKED_DOWN",
    "CLAUDE_HOOKS_NOT_MANAGED_ONLY",
    "CLAUDE_PERMISSIONS_NOT_MANAGED_ONLY",
    "CLAUDE_SANDBOX_NOT_REQUIRED",
    "CLAUDE_SANDBOX_ESCAPE_ENABLED",
    "CLAUDE_SANDBOX_EXCLUDED_COMMANDS_PRESENT",
    "CLAUDE_SANDBOX_FILESYSTEM_DISABLED",
    "CLAUDE_HTTP_USER_AGENT_MISSING",
    "CLAUDE_SOURCE_TREE_DIRTY",
    "CLAUDE_RUNTIME_PROFILE_MISSING",
    "CLAUDE_PRODUCTION_BASH_DENIED",
    "CLAUDE_PRODUCTION_WRITE_DENIED",
    "CLAUDE_PRODUCTION_CONFIG_CHANGE_DENIED",
    "CLAUDE_PRODUCTION_COMMAND_NOT_CANONICAL",
    "CLAUDE_PRODUCTION_PATH_OUTSIDE_RUN",
)


@pytest.mark.parametrize("code", REQUIRED_ERROR_CODES)
def test_the_full_error_code_vocabulary_exists(code):
    assert code in crs.ERROR_CODES


# ---------------------------------------------------- project defense ---


def test_the_project_network_guard_is_kept_as_defense_in_depth():
    assert (REPOSITORY_ROOT / ".claude" / "hooks" / "network_guard.py").is_file()


def test_the_project_settings_do_not_claim_to_be_the_production_boundary():
    settings = json.loads(
        (REPOSITORY_ROOT / ".claude" / "settings.json").read_text("utf-8")
    )
    network = settings["sandbox"]["network"]
    assert "strictAllowlist" not in network
    assert "allowManagedDomainsOnly" not in network
    assert settings["permissions"]["defaultMode"] == "default"


# ------------------------------------ FIX-R2-004B canonical python identity ---


@pytest.fixture()
def python_symlink(tmp_path):
    """``python3 -> real_python``: one interpreter, two path spellings."""
    real_python = tmp_path / "bin" / "real_python"
    real_python.parent.mkdir(parents=True)
    real_python.write_bytes(Path(sys.executable).resolve().read_bytes())
    os.chmod(real_python, 0o755)
    alias = tmp_path / "bin" / "python3"
    alias.symlink_to(real_python)
    return {"real": real_python, "alias": alias}


def test_canonical_production_python_resolves_a_symlink_alias(python_symlink):
    alias = python_symlink["alias"]
    real = python_symlink["real"]
    assert alias.is_symlink()
    assert str(alias) != str(real)
    assert crs.canonical_production_python(alias) == str(real.resolve())
    assert crs.canonical_production_python(real) == str(real.resolve())


def test_canonical_production_python_rejects_relative_and_missing(tmp_path):
    for bad in ("python3", "bin/python3", "", "   "):
        with pytest.raises(crs.RuntimeSecurityError) as excinfo:
            crs.canonical_production_python(bad)
        assert excinfo.value.code == "CLAUDE_MANAGED_POLICY_INVALID"
    with pytest.raises(crs.RuntimeSecurityError):
        crs.canonical_production_python("/nonexistent/python3")
    with pytest.raises(crs.RuntimeSecurityError):
        # a directory is not a regular file
        crs.canonical_production_python(tmp_path)


def test_canonical_production_python_is_idempotent(python_symlink):
    once = crs.canonical_production_python(python_symlink["alias"])
    assert crs.canonical_production_python(once) == once


def _managed_python_paths(payload):
    bash_rules = [
        rule
        for rule in payload["permissions"]["allow"]
        if str(rule).startswith("Bash(")
    ]
    hook_commands = [
        hook["command"]
        for event in ("PreToolUse", "ConfigChange")
        for entry in payload["hooks"][event]
        for hook in entry["hooks"]
    ]
    return bash_rules, hook_commands


def test_render_managed_settings_canonicalizes_a_symlinked_python(python_symlink):
    real = str(python_symlink["real"].resolve())
    payload = crs.render_managed_settings(
        project_root=REPOSITORY_ROOT,
        daytrade_root=PROJECT_ROOT,
        production_python=python_symlink["alias"],
    )
    bash_rules, hook_commands = _managed_python_paths(payload)
    assert bash_rules == [f"Bash({real} -B -m src.cli *)"]
    assert hook_commands == [real, real]
    assert str(python_symlink["alias"]) not in json.dumps(payload)


def test_preflight_uses_one_canonical_python_everywhere(production_world, python_symlink):
    """Section 6: ``which('python3')`` returns a symlink; everything resolves."""
    launcher = production_world["launcher"]
    daytrade_root = production_world["daytrade_root"]
    kwargs = dict(production_world["kwargs"])

    real = str(python_symlink["real"].resolve())
    alias = str(python_symlink["alias"])

    # Reinstall the managed policy for this interpreter identity.
    settings_path = Path(kwargs["managed_settings_path"])
    payload = crs.render_managed_settings(
        project_root=daytrade_root.parent,
        daytrade_root=daytrade_root,
        production_python=real,
        guard_path=kwargs["managed_guard_path"],
    )
    settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(settings_path, 0o644)

    # No DAYTRADE_PRODUCTION_PYTHON: the launcher falls back to which("python3"),
    # which here answers with the *symlink alias*.
    kwargs["environ"] = {"DAYTRADE_HTTP_USER_AGENT": "daytrade/1.0"}
    kwargs["which"] = lambda name: alias if name == "python3" else f"/usr/bin/{name}"

    result = launcher.preflight(target_date="2026-08-14", **kwargs)

    assert result["production_python"] == real
    assert result["document"]["production_python"] == real

    written = json.loads(Path(result["artifact_path"]).read_text("utf-8"))
    assert written["production_python"] == real

    bash_rules, hook_commands = _managed_python_paths(payload)
    assert bash_rules == [f"Bash({real} -B -m src.cli *)"]
    assert hook_commands == [real, real]
    assert alias not in json.dumps(payload)


def test_preflight_canonicalizes_an_env_supplied_symlink(production_world, python_symlink):
    launcher = production_world["launcher"]
    daytrade_root = production_world["daytrade_root"]
    kwargs = dict(production_world["kwargs"])
    real = str(python_symlink["real"].resolve())

    settings_path = Path(kwargs["managed_settings_path"])
    payload = crs.render_managed_settings(
        project_root=daytrade_root.parent,
        daytrade_root=daytrade_root,
        production_python=real,
        guard_path=kwargs["managed_guard_path"],
    )
    settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(settings_path, 0o644)

    kwargs["environ"] = {
        "DAYTRADE_PRODUCTION_PYTHON": str(python_symlink["alias"]),
        "DAYTRADE_HTTP_USER_AGENT": "daytrade/1.0",
    }
    result = launcher.preflight(target_date="2026-08-14", **kwargs)
    assert result["production_python"] == real


def test_the_deploy_script_relies_on_internal_canonicalization():
    """Section 8: a human may pass ``$(command -v python3)`` unchanged."""
    text = (PROJECT_ROOT / "scripts" / "deploy-claude-managed-policy").read_text("utf-8")
    assert "readlink" not in text
    doc = (PROJECT_ROOT / "docs" / "nightly-operation.md").read_text("utf-8")
    assert "readlink -f" not in doc
    assert '--production-python "$(command -v python3)"' in doc
