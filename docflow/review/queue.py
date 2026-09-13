"""Review queue: persist and manage low-confidence filing decisions."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from docflow.classification.classifier import FilingDecision
from docflow.clustering.clusterer import DocumentCandidate

logger = logging.getLogger(__name__)

QUEUE_FILENAME = "review_queue.json"


def _serialize_decision(decision: FilingDecision, source_pdf: str) -> dict:
    """Convert a FilingDecision to a JSON-serializable dict."""
    return {
        "id": f"{decision.candidate.pages[0]}_{datetime.now().strftime('%H%M%S')}",
        "source_pdf": source_pdf,
        "pages": decision.candidate.pages,
        "institution": decision.candidate.institution,
        "doc_type": decision.candidate.doc_type,
        "period": decision.candidate.period,
        "account": decision.candidate.account,
        "suggested_filename": decision.filename,
        "suggested_directory": decision.target_directory,
        "rule_matched": decision.rule_matched,
        "confidence": decision.confidence,
        "notes": decision.notes,
        "raw_text_preview": (
            decision.candidate.raw_signals.get("raw_texts", [""])[0][:500]
            if decision.candidate.raw_signals.get("raw_texts")
            else ""
        ),
        "status": "pending",  # pending, approved, corrected, skipped
        "corrected_filename": None,
        "corrected_directory": None,
    }


def save_review_queue(
    decisions: list[FilingDecision],
    source_pdf: Path,
    config: dict,
) -> Path | None:
    """Save review queue items to a JSON file in the archive root.

    Returns the path to the queue file, or None if queue is empty.
    """
    if not decisions:
        return None

    archive_root = Path(os.path.expanduser(
        config.get("archive_root", "~/DocFlowExample/archive")
    ))
    archive_root.mkdir(parents=True, exist_ok=True)
    queue_path = archive_root / QUEUE_FILENAME

    # Load existing queue if present
    existing: list[dict] = []
    if queue_path.exists():
        with open(queue_path) as f:
            existing = json.load(f).get("items", [])

    # Add new items
    for decision in decisions:
        item = _serialize_decision(decision, str(source_pdf))
        existing.append(item)

    with open(queue_path, "w") as f:
        json.dump({
            "updated": datetime.now().isoformat(),
            "items": existing,
        }, f, indent=2)

    logger.info("Review queue saved: %d items → %s", len(existing), queue_path)
    return queue_path


def load_review_queue(config: dict) -> list[dict]:
    """Load pending review queue items."""
    archive_root = Path(os.path.expanduser(
        config.get("archive_root", "~/DocFlowExample/archive")
    ))
    queue_path = archive_root / QUEUE_FILENAME

    if not queue_path.exists():
        return []

    with open(queue_path) as f:
        data = json.load(f)

    return [item for item in data.get("items", []) if item["status"] == "pending"]


def update_queue_item(config: dict, item_id: str, updates: dict) -> dict | None:
    """Update a single queue item by ID and return it.

    Returns the updated item, or None if not found.
    """
    archive_root = Path(os.path.expanduser(
        config.get("archive_root", "~/DocFlowExample/archive")
    ))
    queue_path = archive_root / QUEUE_FILENAME

    if not queue_path.exists():
        return None

    with open(queue_path) as f:
        data = json.load(f)

    items = data.get("items", [])
    for item in items:
        if item["id"] == item_id:
            item.update(updates)
            with open(queue_path, "w") as f:
                json.dump({
                    "updated": datetime.now().isoformat(),
                    "items": items,
                }, f, indent=2)
            return item

    return None
