"""Static contract tests for the GitHub Actions CI workflow."""

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYTHON_VERSION = PROJECT_ROOT / ".python-version"


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
    assert job["runs-on"] == "ubuntu-latest"
    assert "self-hosted" not in job["runs-on"]
    assert int(job["timeout-minutes"]) > 0
    assert job["defaults"]["run"]["working-directory"] == "daytrade-sbi"

    uses = [step.get("uses") for step in job["steps"] if step.get("uses")]
    assert uses == ["actions/checkout@v7.0.1", "actions/setup-python@v7.0.0"]


def test_ci_011_to_014_python_and_full_pytest_contract():
    data = _workflow()
    steps = data["jobs"]["pytest"]["steps"]
    setup = next(step for step in steps if step.get("name") == "Set up Python")
    assert setup["with"]["python-version-file"] == "daytrade-sbi/.python-version"
    assert PYTHON_VERSION.read_text(encoding="utf-8").strip() == "3.14.4"

    commands = [step.get("run", "") for step in steps]
    assert any(
        "python -m pip install --disable-pip-version-check -r requirements-dev.txt"
        == command
        for command in commands
    )
    assert "python -B -m pytest -q" in commands


def test_ci_015_no_write_or_network_side_effect_commands():
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
