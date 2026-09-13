"""Config learning: record corrections and update rules.md dynamically."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CORRECTIONS_FILENAME = "corrections_log.json"


def record_correction(
    original: dict,
    corrected_filename: str,
    corrected_directory: str,
    config: dict,
    gateway=None,
) -> None:
    """Record a filing correction and update rules.md if appropriate.

    Args:
        original: The original queue item dict.
        corrected_filename: The corrected filename.
        corrected_directory: The corrected directory (relative to archive root).
        config: Pipeline config.
        gateway: Optional CloudPromptGateway; one is created per correction otherwise.
    """
    archive_root = Path(os.path.expanduser(
        config.get("archive_root", "~/DocFlowExample/archive")
    ))
    log_path = archive_root / CORRECTIONS_FILENAME

    existing: list[dict] = []
    if log_path.exists():
        with open(log_path) as f:
            existing = json.load(f).get("corrections", [])

    # Normalize directory to be relative to archive root
    rel_dir = corrected_directory
    archive_str = str(archive_root)
    if rel_dir.startswith(archive_str):
        rel_dir = rel_dir[len(archive_str):].lstrip("/\\")

    correction = {
        "timestamp": datetime.now().isoformat(),
        "institution": original.get("institution", "unknown"),
        "doc_type": original.get("doc_type"),
        "original_filename": original.get("suggested_filename"),
        "original_directory": original.get("suggested_directory"),
        "corrected_filename": corrected_filename,
        "corrected_directory": rel_dir,
        "raw_text_preview": original.get("raw_text_preview", "")[:200],
    }
    existing.append(correction)

    with open(log_path, "w") as f:
        json.dump({"corrections": existing}, f, indent=2)

    logger.info("Recorded correction: %s → %s", original.get("suggested_filename"),
                corrected_filename)

    # Try to add a rule to rules.md using LLM
    _learn_rule_from_correction(correction, config, gateway)


def _learn_rule_from_correction(correction: dict, config: dict, gateway=None) -> None:
    """Ask the model (through CloudPromptGateway) whether a correction implies a new rule."""
    from docflow.config.rules_manager import append_rule, load_rules_md, parse_rules_md
    from docflow.llm.gateway import CloudPromptGateway, LocalRule
    from docflow.privacy.types import NoModelResult

    rules_md = load_rules_md(config)
    if not rules_md:
        return

    gateway = gateway or CloudPromptGateway(config, rules_md=rules_md)
    rules = [LocalRule.from_rules_md(r) for r in parse_rules_md(rules_md)]
    try:
        proposal = gateway.learn_rule(correction, rules)
    except NoModelResult as exc:
        logger.info("Rule learning unavailable (%s)", exc.code)
        return

    if proposal is not None:
        append_rule(config, proposal.rule_name, proposal.body)
        logger.info("Learned new rule from correction: %s", proposal.rule_name)


def suggest_rules(config: dict) -> list[dict]:
    """Analyze corrections and suggest new filing rules.

    A rule is suggested when 2+ corrections route the same institution
    to the same directory.

    Returns a list of suggested rule dicts (legacy format for CLI output).
    """
    archive_root = Path(os.path.expanduser(
        config.get("archive_root", "~/DocFlowExample/archive")
    ))
    log_path = archive_root / CORRECTIONS_FILENAME

    if not log_path.exists():
        return []

    with open(log_path) as f:
        corrections = json.load(f).get("corrections", [])

    if not corrections:
        return []

    # Group corrections by (institution, corrected_directory)
    from collections import Counter
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in corrections:
        key = (c.get("institution", "unknown"), c.get("corrected_directory", ""))
        groups.setdefault(key, []).append(c)

    suggestions = []
    existing_rules = config.get("filing_rules", [])
    existing_ids = {r["id"] for r in existing_rules}

    for (institution, directory), group in groups.items():
        if len(group) < 2:
            continue

        already_covered = any(
            r.get("match", {}).get("institution") == institution
            for r in existing_rules
        )
        if already_covered:
            continue

        doc_types = [c.get("doc_type") for c in group if c.get("doc_type")]
        common_doc_type = Counter(doc_types).most_common(1)[0][0] if doc_types else None

        rule_id = f"learned_{institution}_{len(suggestions)}"
        if rule_id in existing_ids:
            rule_id = f"learned_{institution}_{len(suggestions)}_{len(group)}"

        rel_dir = directory
        archive_str = str(archive_root)
        if rel_dir.startswith(archive_str):
            rel_dir = rel_dir[len(archive_str):].lstrip("/\\")

        filenames = [c.get("corrected_filename", "") for c in group]
        template = filenames[0] if filenames else f"{institution}{{period}}.pdf"

        suggestion = {
            "id": rule_id,
            "match": {"institution": institution},
            "file_to": rel_dir,
            "filename_template": template,
            "based_on": f"{len(group)} corrections",
        }
        if common_doc_type:
            suggestion["match"]["doc_type"] = [common_doc_type]

        suggestions.append(suggestion)

    return suggestions
