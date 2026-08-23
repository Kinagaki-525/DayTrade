"""Static contract tests for the GitHub Actions CI workflow."""

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYTHON_VERSION = PROJECT_ROOT / ".python-version"

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_ci_001_to_005_structure_and_triggers_are_pinned():
    assert WORKFLOW.exists()
    data = _workflow()
    assert data["name"] == "CI"
    assert data["on"]["pull_request"]["branches"] == ["main"]
    assert data["on"]["push"]["branches"] == ["main"]
    assert set(data["jobs"]) == {"pytest"}
    assert data["jobs"]["pytest"]["name"] == "pytest"


def test_ci_006_to_010_is_read_only_and_runs_from_project_directory():
    data = _workflow()
    assert data["permissions"] == {"contents": "read"}
    job = data["jobs"]["pytest"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert "self-hosted" not in job["runs-on"]
    assert job["timeout-minutes"] == "10"
    assert job["defaults"]["run"]["working-directory"] == "daytrade-sbi"

    uses = [step.get("uses") for step in job["steps"] if step.get("uses")]
    assert uses == [
        f"actions/checkout@{CHECKOUT_SHA}",
        f"actions/setup-python@{SETUP_PYTHON_SHA}",
    ]

    checkout = next(step for step in job["steps"] if step.get("name") == "Checkout")
    assert checkout["with"]["persist-credentials"] == "false"
    assert checkout["with"]["fetch-depth"] == "2"


def test_ci_011_to_015_python_full_pytest_and_diff_check_contract():
    data = _workflow()
    steps = data["jobs"]["pytest"]["steps"]
    setup = next(step for step in steps if step.get("name") == "Set up Python")
    assert setup["with"]["python-version-file"] == "daytrade-sbi/.python-version"
    assert PYTHON_VERSION.read_text(encoding="utf-8").strip() == "3.14.4"

    commands = [step.get("run", "") for step in steps]
    assert "git diff --check HEAD^1 HEAD" in commands
    assert any(
        "python -m pip install --disable-pip-version-check -r requirements-dev.txt"
        == command
        for command in commands
    )
    assert "python -B -m pytest -q" in commands


def test_ci_016_no_write_or_network_side_effect_commands():
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for forbidden in (
        ": write",
        "git push",
        "gh pr",
        "curl ",
        "wget ",
        "websearch",
        "webfetch",
        "id-token:",
        "pull-requests:",
        "issues:",
    ):
        assert forbidden not in text
