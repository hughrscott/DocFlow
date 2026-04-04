"""Config validation: check that user_config.yaml is valid and usable."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def validate_config(config_path: Path) -> list[dict]:
    """Validate a config file and return a list of check results.

    Each result is a dict with keys: check, passed, message.
    """
    results: list[dict] = []

    # 1. File exists and is valid YAML
    if not config_path.exists():
        results.append({"check": "file_exists", "passed": False,
                        "message": f"Config file not found: {config_path}"})
        return results

    results.append({"check": "file_exists", "passed": True,
                    "message": f"Config file found: {config_path}"})

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        results.append({"check": "valid_yaml", "passed": False,
                        "message": f"Invalid YAML: {exc}"})
        return results

    results.append({"check": "valid_yaml", "passed": True,
                    "message": "Valid YAML"})

    if not isinstance(config, dict):
        results.append({"check": "valid_structure", "passed": False,
                        "message": "Config must be a YAML mapping (dict)"})
        return results

    # 2. Archive root exists and is writable
    archive_root = config.get("archive_root")
    if archive_root:
        expanded = Path(os.path.expanduser(archive_root))
        if expanded.exists() and expanded.is_dir():
            if os.access(expanded, os.W_OK):
                results.append({"check": "archive_root", "passed": True,
                                "message": f"Archive root writable: {expanded}"})
            else:
                results.append({"check": "archive_root", "passed": False,
                                "message": f"Archive root not writable: {expanded}"})
        else:
            results.append({"check": "archive_root", "passed": False,
                            "message": f"Archive root does not exist: {expanded}"})
    else:
        results.append({"check": "archive_root", "passed": False,
                        "message": "No archive_root defined"})

    # 3. Scan watch folder exists
    watch_folder = config.get("scan_watch_folder")
    if watch_folder:
        expanded = Path(os.path.expanduser(watch_folder))
        if expanded.exists():
            results.append({"check": "scan_watch_folder", "passed": True,
                            "message": f"Watch folder exists: {expanded}"})
        else:
            results.append({"check": "scan_watch_folder", "passed": False,
                            "message": f"Watch folder does not exist: {expanded}"})
    else:
        results.append({"check": "scan_watch_folder", "passed": False,
                        "message": "No scan_watch_folder defined"})

    # 4. LLM connectivity (OpenRouter)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.environ.get("OPENROUTER_API_KEY")
        except ImportError:
            pass

    if api_key:
        results.append({"check": "openrouter_api_key", "passed": True,
                        "message": "OPENROUTER_API_KEY is set"})
    else:
        results.append({"check": "openrouter_api_key", "passed": False,
                        "message": "OPENROUTER_API_KEY not found in environment or .env"})

    # 5. Filing rules validation
    rules = config.get("filing_rules", [])
    if not rules:
        results.append({"check": "filing_rules", "passed": False,
                        "message": "No filing rules defined"})
    else:
        results.append({"check": "filing_rules", "passed": True,
                        "message": f"{len(rules)} filing rules defined"})

        # Check for required fields
        for i, rule in enumerate(rules):
            if "id" not in rule:
                results.append({"check": f"rule_{i}_id", "passed": False,
                                "message": f"Rule {i} missing 'id' field"})
            if "match" not in rule:
                results.append({"check": f"rule_{rule.get('id', i)}_match", "passed": False,
                                "message": f"Rule '{rule.get('id', i)}' missing 'match' field"})
            if "file_to" not in rule:
                results.append({"check": f"rule_{rule.get('id', i)}_file_to", "passed": False,
                                "message": f"Rule '{rule.get('id', i)}' missing 'file_to' field"})
            if "filename_template" not in rule:
                results.append({"check": f"rule_{rule.get('id', i)}_template", "passed": False,
                                "message": f"Rule '{rule.get('id', i)}' missing 'filename_template'"})

        # Check for duplicate rule IDs
        ids = [r.get("id") for r in rules if "id" in r]
        seen = set()
        for rid in ids:
            if rid in seen:
                results.append({"check": "duplicate_rule_id", "passed": False,
                                "message": f"Duplicate rule ID: '{rid}'"})
            seen.add(rid)

        if len(ids) == len(seen):
            results.append({"check": "unique_rule_ids", "passed": True,
                            "message": "All rule IDs are unique"})

        # Check for file_to paths with directory traversal
        for rule in rules:
            file_to = rule.get("file_to", "")
            if ".." in file_to:
                results.append({"check": f"rule_{rule.get('id')}_traversal", "passed": False,
                                "message": f"Rule '{rule.get('id')}' has '..' in file_to path"})

    # 6. Entities validation
    entities = config.get("entities", [])
    if entities:
        results.append({"check": "entities", "passed": True,
                        "message": f"{len(entities)} entities defined"})
    else:
        results.append({"check": "entities", "passed": True,
                        "message": "No entities defined (optional)"})

    # 7. Confidence threshold
    threshold = config.get("confidence_threshold")
    if threshold is not None:
        if 0.0 <= threshold <= 1.0:
            results.append({"check": "confidence_threshold", "passed": True,
                            "message": f"Confidence threshold: {threshold}"})
        else:
            results.append({"check": "confidence_threshold", "passed": False,
                            "message": f"Threshold must be 0.0-1.0, got: {threshold}"})
    else:
        results.append({"check": "confidence_threshold", "passed": True,
                        "message": "Using default threshold (0.75)"})

    return results
