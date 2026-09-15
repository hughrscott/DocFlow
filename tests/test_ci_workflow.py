"""Contract for the GitHub Actions CI workflow: synthetic-only, least privilege."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
ALLOWED_APT_PACKAGES = {"tesseract-ocr", "poppler-utils"}


def load_ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return workflow.get("on", workflow.get(True))


def job(workflow: dict) -> dict:
    jobs = workflow["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))


def steps_using(action: str) -> list[dict]:
    return [s for s in job(load_ci())["steps"] if s.get("uses", "").startswith(action + "@")]


def run_script() -> str:
    return "\n".join(s["run"] for s in job(load_ci())["steps"] if "run" in s)


def test_ci_is_the_only_workflow() -> None:
    assert sorted(p.name for p in WORKFLOWS.iterdir()) == ["ci.yml"]


def test_runs_only_on_pull_request_and_push() -> None:
    assert set(triggers(load_ci())) == {"pull_request", "push"}


def test_least_privilege_bounded_and_cancellable() -> None:
    workflow = load_ci()
    verify = job(workflow)
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in verify
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert "${{ github.workflow }}" in workflow["concurrency"]["group"]
    assert verify["runs-on"].startswith("ubuntu-")
    assert isinstance(verify["timeout-minutes"], int)
    assert 0 < verify["timeout-minutes"] <= 30


def test_checks_out_exact_head_without_persisted_credentials() -> None:
    (checkout,) = steps_using("actions/checkout")
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' in run_script()


def test_python_311_without_dependency_cache() -> None:
    (setup,) = steps_using("actions/setup-python")
    assert setup["with"] == {"python-version": "3.11"}


def test_uses_no_secrets_artifacts_caches_or_extra_actions() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "GITHUB_TOKEN" not in text
    assert not re.search(r"(?i)api[_-]?key|password|token:", text)
    used = {s["uses"].split("@")[0] for s in job(load_ci())["steps"] if "uses" in s}
    assert used == {"actions/checkout", "actions/setup-python"}


def test_installs_only_declared_dependencies_and_ocr_runtime() -> None:
    script = run_script()
    apt = re.findall(r"apt-get install\s+(.*)", script)
    assert len(apt) == 1
    packages = {tok for tok in apt[0].split() if not tok.startswith("-")}
    assert packages == ALLOWED_APT_PACKAGES
    pip = re.findall(r"pip install\s+(.*)", script)
    assert pip == ['-e ".[dev]"']
    assert not re.search(r"\b(curl|wget|docker|ollama)\b", script)


def test_runs_privacy_preflight_lint_and_full_pytest() -> None:
    commands = [line.strip() for line in run_script().splitlines()]
    assert "python tools/privacy_preflight.py" in commands
    assert "ruff check docflow tools" in commands
    assert "python -m pytest -p no:cacheprovider" in commands
    assert not any(" -k " in c or "--deselect" in c or "--ignore" in c for c in commands)
