"""Tests for src/config/ — validator, scanner, and learner."""
from __future__ import annotations

import json

import pytest
import yaml
from pathlib import Path

from docflow.config.validator import validate_config
from docflow.config.scanner import scan_existing_archive
from docflow.config.learner import record_correction, suggest_rules


class TestValidator:
    def test_valid_config(self, tmp_path):
        config = {
            "archive_root": str(tmp_path),
            "scan_watch_folder": str(tmp_path),
            "confidence_threshold": 0.75,
            "filing_rules": [
                {
                    "id": "test_rule",
                    "match": {"institution": "test"},
                    "file_to": "Test/Dir",
                    "filename_template": "Test{period}.pdf",
                },
            ],
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        results = validate_config(config_path)
        failures = [r for r in results if not r["passed"]]
        # Only openrouter key might fail
        non_key_failures = [r for r in failures if "api_key" not in r["check"]]
        assert len(non_key_failures) == 0

    def test_missing_config(self, tmp_path):
        results = validate_config(tmp_path / "nonexistent.yaml")
        assert not results[0]["passed"]

    def test_duplicate_rule_ids(self, tmp_path):
        config = {
            "archive_root": str(tmp_path),
            "filing_rules": [
                {"id": "dup", "match": {}, "file_to": "A", "filename_template": "A.pdf"},
                {"id": "dup", "match": {}, "file_to": "B", "filename_template": "B.pdf"},
            ],
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        results = validate_config(config_path)
        dup_results = [r for r in results if "duplicate" in r.get("check", "")]
        assert len(dup_results) == 1
        assert not dup_results[0]["passed"]

    def test_bad_threshold(self, tmp_path):
        config = {"archive_root": str(tmp_path), "confidence_threshold": 1.5}
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        results = validate_config(config_path)
        threshold = [r for r in results if r["check"] == "confidence_threshold"]
        assert len(threshold) == 1
        assert not threshold[0]["passed"]


class TestScanner:
    def test_scan_empty_archive(self, tmp_path):
        result = scan_existing_archive(tmp_path)
        assert result["stats"]["total_pdfs"] == 0
        assert result["inferred_rules"] == []

    def test_scan_with_pdfs(self, tmp_path):
        # Create a mock archive structure
        pnc_dir = tmp_path / "PNC" / "Personal"
        pnc_dir.mkdir(parents=True)
        (pnc_dir / "PNCBankStatementJanuary2026.pdf").write_bytes(b"%PDF-1.4")
        (pnc_dir / "PNCBankStatementFebruary2026.pdf").write_bytes(b"%PDF-1.4")

        guardian_dir = tmp_path / "Insurance" / "Guardian"
        guardian_dir.mkdir(parents=True)
        (guardian_dir / "GuardianEOBMarch2026.pdf").write_bytes(b"%PDF-1.4")

        result = scan_existing_archive(tmp_path)
        assert result["stats"]["total_pdfs"] == 3
        assert len(result["inferred_rules"]) >= 1

        # Check PNC rule was inferred
        pnc_rules = [r for r in result["inferred_rules"]
                     if r["match"].get("institution") == "pnc"]
        assert len(pnc_rules) >= 1

    def test_scan_nonexistent(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            scan_existing_archive(tmp_path / "nonexistent")


class TestLearner:
    def test_record_and_suggest(self, tmp_path):
        config = {"archive_root": str(tmp_path)}

        # Record 2 corrections for the same institution + directory
        for i in range(2):
            record_correction(
                original={"institution": "new_bank", "doc_type": "statement",
                           "suggested_filename": f"Old{i}.pdf",
                           "suggested_directory": "/old/path"},
                corrected_filename=f"NewBankStatement{i}.pdf",
                corrected_directory=str(tmp_path / "Finance" / "NewBank"),
                config=config,
                log_path=tmp_path / "state" / "corrections_log.json",
            )

        suggestions = suggest_rules(config, log_path=tmp_path / "state" / "corrections_log.json")
        assert len(suggestions) >= 1
        assert suggestions[0]["match"]["institution"] == "new_bank"

    def test_no_suggestions_below_threshold(self, tmp_path):
        config = {"archive_root": str(tmp_path)}

        # Only 1 correction — not enough for a suggestion
        record_correction(
            original={"institution": "one_off", "doc_type": "receipt",
                       "suggested_filename": "Old.pdf",
                       "suggested_directory": "/old"},
            corrected_filename="Fixed.pdf",
            corrected_directory="/new",
            config=config,
            log_path=tmp_path / "state" / "corrections_log.json",
        )

        suggestions = suggest_rules(config, log_path=tmp_path / "state" / "corrections_log.json")
        assert len(suggestions) == 0

    def test_empty_corrections(self, tmp_path):
        config = {"archive_root": str(tmp_path)}
        suggestions = suggest_rules(config, log_path=tmp_path / "state" / "corrections_log.json")
        assert suggestions == []
