"""Manage the human-readable rules.md filing rules file."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RULES_FILENAME = "rules.md"


def rules_path(config: dict) -> Path:
    """Return the path to rules.md."""
    explicit = config.get("rules_file")
    if explicit:
        return Path(os.path.expanduser(explicit))
    return Path(__file__).parent.parent.parent / "config" / DEFAULT_RULES_FILENAME


def load_rules_md(config: dict) -> str:
    """Read the rules.md file and return its content."""
    path = rules_path(config)
    if not path.exists():
        return ""
    return path.read_text()


def parse_rules_md(content: str) -> list[dict]:
    """Parse rules.md into structured dicts for programmatic use.

    Each H2 section becomes a rule dict with keys:
        name, institution, doc_types, account_hints, entity_hints,
        file_to, filename, notes
    """
    if not content.strip():
        return []

    rules = []
    # Split on ## headings
    sections = re.split(r"^## ", content, flags=re.MULTILINE)

    for section in sections[1:]:  # Skip preamble before first ##
        lines = section.strip().split("\n")
        name = lines[0].strip()
        rule = {"name": name}

        for line in lines[1:]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Parse "- **Label**: value" format
            m = re.match(r"-\s*\*\*(.+?)\*\*\s*:\s*(.+)", line)
            if not m:
                continue

            label = m.group(1).lower().strip()
            value = m.group(2).strip()

            if label == "institution":
                rule["institution"] = value
            elif label == "document types" or label == "doc types":
                rule["doc_types"] = [v.strip() for v in value.split(",")]
            elif label == "account hints":
                rule["account_hints"] = [v.strip() for v in value.split(",")]
            elif label == "entity hints":
                rule["entity_hints"] = [v.strip() for v in value.split(",")]
            elif label == "file to":
                rule["file_to"] = value
            elif label == "filename":
                rule["filename"] = value
            elif label == "notes":
                rule["notes"] = value

        rules.append(rule)

    return rules


def append_rule(config: dict, rule_name: str, rule_content: str) -> None:
    """Append a new rule section to rules.md."""
    path = rules_path(config)
    existing = path.read_text() if path.exists() else ""

    new_section = f"\n\n## {rule_name}\n{rule_content}"
    path.write_text(existing.rstrip() + new_section + "\n")
    logger.info("Appended rule '%s' to %s", rule_name, path)


def update_rule(config: dict, rule_name: str, new_content: str) -> bool:
    """Update an existing rule section in rules.md. Returns True if found."""
    path = rules_path(config)
    if not path.exists():
        return False

    content = path.read_text()
    # Find the section for this rule
    pattern = re.compile(
        r"(## " + re.escape(rule_name) + r"\n)(.*?)(?=\n## |\Z)",
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return False

    updated = content[:match.start()] + f"## {rule_name}\n{new_content}\n" + content[match.end():]
    path.write_text(updated)
    logger.info("Updated rule '%s' in %s", rule_name, path)
    return True


def migrate_yaml_rules_to_md(config: dict) -> str:
    """Convert YAML filing_rules to rules.md format. Returns the markdown content."""
    rules = config.get("filing_rules", [])
    if not rules:
        return "# DocFlow Filing Rules\n\nNo rules defined yet.\n"

    sections = ["# DocFlow Filing Rules\n"]
    sections.append("These rules tell DocFlow where to file each document. "
                     "The AI reads these rules and uses them as guidance when classifying scanned documents.\n")
    sections.append("Each rule describes a type of document and where it should be filed. "
                     "When you correct a filing, DocFlow will add or update rules here automatically.\n")

    for rule in rules:
        match = rule.get("match", {})
        rule_id = rule.get("id", "unknown")

        # Build a human-readable name from the rule ID
        name = rule_id.replace("_", " ").title()

        lines = []

        # Institution
        inst = match.get("institution")
        if inst:
            if isinstance(inst, list):
                lines.append(f"- **Institution**: {', '.join(str(i) for i in inst)}")
            else:
                lines.append(f"- **Institution**: {inst}")

        # Doc types
        doc_type = match.get("doc_type")
        if doc_type:
            if isinstance(doc_type, list):
                lines.append(f"- **Document types**: {', '.join(str(d) for d in doc_type)}")
            else:
                lines.append(f"- **Document types**: {doc_type}")

        # Account hints
        account_hints = match.get("account_hints")
        if account_hints:
            lines.append(f"- **Account hints**: {', '.join(account_hints)}")

        # Entity hints
        entity_hints = match.get("entity_hints")
        if entity_hints:
            lines.append(f"- **Entity hints**: {', '.join(entity_hints)}")

        # File to
        file_to = rule.get("file_to", "")
        if file_to:
            lines.append(f"- **File to**: {file_to}")

        # Filename template
        filename = rule.get("filename_template", "")
        if filename:
            lines.append(f"- **Filename**: {filename}")

        sections.append(f"## {name}\n" + "\n".join(lines))

    return "\n\n".join(sections) + "\n"
