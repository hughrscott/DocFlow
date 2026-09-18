from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).parents[1] / "tools" / "privacy_preflight.py"


def init_repo(path: Path, files: dict[str, str]) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)


def run_preflight(path: Path, forbidden: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(TOOL), "--root", str(path), "--json"]
    if forbidden:
        command.extend(["--forbidden-file", str(forbidden)])
    return subprocess.run(command, text=True, capture_output=True, check=False)


def test_clean_synthetic_fixture_passes(tmp_path: Path) -> None:
    init_repo(
        tmp_path,
        {
            "fixture.txt": (
                "Morgan Redwood\n"
                "morgan@example.test\n"
                "100 Example Avenue, Sampleton, TX 77005\n"
                "example account ending in 4102\n"
                "(212) 555-0199\n"
            )
        },
    )

    result = run_preflight(tmp_path)

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["finding_count"] == 0
    assert report["scanned_file_count"] == 1


def test_adversarial_categories_fail_without_disclosing_values(tmp_path: Path) -> None:
    secret_value = "sk-" + "Z" * 24
    forbidden_value = "Private Fixture Person"  # privacy-preflight: allow-test-fixture
    content = "\n".join(  # noqa: FLY002
        [
            forbidden_value,
            "person@invalid.testbank",  # privacy-preflight: allow-test-fixture
            "212-867-5309",  # privacy-preflight: allow-test-fixture
            "123-45-6789",  # privacy-preflight: allow-test-fixture
            "/Users/privateuser/archive",  # privacy-preflight: allow-test-fixture
            "742 Cedar Road",  # privacy-preflight: allow-test-fixture
            secret_value,
            "routing number 123456789",  # privacy-preflight: allow-test-fixture
        ]
    )
    init_repo(tmp_path, {"unsafe.txt": content})
    forbidden = tmp_path.parent / "private-forbidden.json"
    forbidden.write_text(json.dumps({"forbidden_literals": [forbidden_value]}), encoding="utf-8")

    result = run_preflight(tmp_path, forbidden)

    assert result.returncode == 1
    report = json.loads(result.stdout)
    expected = {
        "cloud_credential",
        "email",
        "financial_identifier",
        "forbidden_literal",
        "phone",
        "private_path",
        "ssn",
        "street_address",
    }
    assert expected <= set(report["finding_counts_by_category"])
    assert forbidden_value not in result.stdout
    assert secret_value not in result.stdout
    assert "867-5309" not in result.stdout


def test_untracked_file_is_not_scanned(tmp_path: Path) -> None:
    init_repo(tmp_path, {"tracked.txt": "safe synthetic fixture"})
    (tmp_path / "untracked.txt").write_text(
        "person@invalid.testbank", encoding="utf-8"  # privacy-preflight: allow-test-fixture
    )

    result = run_preflight(tmp_path)

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["tracked_file_count"] == 1
    assert report["finding_count"] == 0


def test_prohibited_tracked_artifact_fails(tmp_path: Path) -> None:
    init_repo(tmp_path, {"scan.pdf": "synthetic but prohibited"})

    result = run_preflight(tmp_path)

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["finding_counts_by_category"] == {"prohibited_file": 1}
