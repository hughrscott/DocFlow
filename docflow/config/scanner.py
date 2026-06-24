"""Directory inference scanner: read an existing archive to generate filing rules."""
from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from docflow.ocr.analyzer import SIGNAL_LIBRARY

logger = logging.getLogger(__name__)

# Reverse map: keyword → institution key
_KEYWORD_TO_INSTITUTION: dict[str, str] = {}
for inst_key, variants in SIGNAL_LIBRARY.items():
    for variant in variants:
        _KEYWORD_TO_INSTITUTION[variant.lower()] = inst_key


def scan_existing_archive(root_path: Path) -> dict:
    """Walk an existing archive and infer a draft config.

    Returns a dict with:
        - inferred_rules: list of filing rule dicts
        - inferred_entities: list of entity dicts
        - stats: summary statistics
    """
    root_path = Path(os.path.expanduser(str(root_path)))
    if not root_path.exists():
        raise FileNotFoundError(f"Archive root not found: {root_path}")

    # Collect all PDFs with their relative directory paths
    pdf_entries: list[dict] = []
    for pdf in root_path.rglob("*.pdf"):
        rel_dir = str(pdf.parent.relative_to(root_path))
        pdf_entries.append({
            "filename": pdf.name,
            "rel_dir": rel_dir,
            "stem": pdf.stem,
        })

    logger.info("Scanned %d PDFs in %s", len(pdf_entries), root_path)

    # Group PDFs by directory
    dir_groups: dict[str, list[dict]] = defaultdict(list)
    for entry in pdf_entries:
        dir_groups[entry["rel_dir"]].append(entry)

    # Infer rules from directory + filename patterns
    rules = []
    seen_institutions: set[str] = set()

    for rel_dir, entries in sorted(dir_groups.items()):
        if rel_dir == ".":
            continue

        # Try to identify institution from filenames
        institution_counts: Counter[str] = Counter()
        for entry in entries:
            inst = _detect_institution_from_filename(entry["stem"])
            if inst:
                institution_counts[inst] += 1

        if not institution_counts:
            # Try to detect from directory name
            inst = _detect_institution_from_filename(rel_dir)
            if inst:
                institution_counts[inst] = len(entries)

        if not institution_counts:
            continue

        top_institution = institution_counts.most_common(1)[0][0]
        seen_institutions.add(top_institution)

        # Detect doc type from filenames
        doc_type = _detect_doc_type_from_filenames(entries)

        # Build a filename template from common patterns
        template = _infer_filename_template(entries, top_institution)

        rule_id = f"inferred_{top_institution}_{rel_dir.replace('/', '_').replace(' ', '_').lower()}"
        rule_id = re.sub(r"[^a-z0-9_]", "", rule_id)

        rule = {
            "id": rule_id,
            "match": {"institution": top_institution},
            "file_to": rel_dir,
            "filename_template": template,
            "confidence": round(institution_counts[top_institution] / len(entries), 2),
            "sample_count": len(entries),
        }
        if doc_type:
            rule["match"]["doc_type"] = [doc_type]

        rules.append(rule)

    # Infer entities from directory structure
    entities = _infer_entities(root_path, dir_groups)

    return {
        "inferred_rules": rules,
        "inferred_entities": entities,
        "stats": {
            "total_pdfs": len(pdf_entries),
            "total_directories": len(dir_groups),
            "rules_inferred": len(rules),
            "institutions_found": list(seen_institutions),
        },
    }


def _detect_institution_from_filename(text: str) -> str | None:
    """Try to match an institution from a filename or directory name."""
    text_lower = text.lower().replace("_", " ").replace("-", " ")
    for keyword, inst_key in _KEYWORD_TO_INSTITUTION.items():
        if keyword in text_lower:
            return inst_key
    return None


def _detect_doc_type_from_filenames(entries: list[dict]) -> str | None:
    """Detect the most common document type from filenames."""
    type_keywords = {
        "statement": "statement",
        "eob": "eob",
        "invoice": "invoice",
        "bill": "bill",
        "receipt": "receipt",
        "checking": "checking",
        "w2": "w2",
        "w-2": "w2",
        "tax": "tax_notice",
    }
    counts: Counter[str] = Counter()
    for entry in entries:
        stem_lower = entry["stem"].lower()
        for keyword, doc_type in type_keywords.items():
            if keyword in stem_lower:
                counts[doc_type] += 1
                break

    if counts:
        return counts.most_common(1)[0][0]
    return None


def _infer_filename_template(entries: list[dict], institution: str) -> str:
    """Build a filename template from common filename patterns."""
    if not entries:
        return f"{institution}{{period}}.pdf"

    # Look for period patterns in filenames
    has_period = any(
        re.search(r"(january|february|march|april|may|june|july|august|"
                  r"september|october|november|december|\d{4})",
                  e["stem"].lower())
        for e in entries
    )

    # Use the most common filename structure as a template
    # Strip dates/numbers to find the constant prefix
    sample = entries[0]["stem"]
    # Remove trailing date patterns
    prefix = re.sub(
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\d{4}.*",
        "", sample, flags=re.IGNORECASE
    ).strip()
    if not prefix:
        prefix = institution.replace("_", "").title()

    if has_period:
        return f"{prefix}{{period}}.pdf"
    return f"{prefix}.pdf"


def _infer_entities(root_path: Path, dir_groups: dict[str, list[dict]]) -> list[dict]:
    """Infer business entities from directory names."""
    entity_keywords = {
        "llc": "business",
        "inc": "business",
        "solar": "business",
        "consulting": "business",
        "rock": "business",
        "investment": "investment",
    }

    entities = []
    seen = set()

    for rel_dir in dir_groups:
        parts = rel_dir.split("/")
        for part in parts:
            part_lower = part.lower()
            for keyword, entity_type in entity_keywords.items():
                if keyword in part_lower and part not in seen:
                    seen.add(part)
                    entities.append({
                        "name": part,
                        "type": entity_type,
                        "directory": rel_dir.split(part)[0] + part if part in rel_dir else rel_dir,
                        "inferred": True,
                    })
                    break

    return entities
