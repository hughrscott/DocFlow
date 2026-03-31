"""Filing: split FilingDecisions into auto-file and review-queue buckets."""
from __future__ import annotations

import logging

from src.classification.classifier import FilingDecision

logger = logging.getLogger(__name__)


def gate_decisions(
    decisions: list[FilingDecision], config: dict
) -> tuple[list[FilingDecision], list[FilingDecision]]:
    """Return (auto_file, review_queue) split by confidence threshold."""
    threshold = config.get("confidence_threshold", 0.75)
    auto_file: list[FilingDecision] = []
    review_queue: list[FilingDecision] = []

    for decision in decisions:
        if decision.confidence >= threshold and decision.rule_matched != "none":
            decision.auto_file = True
            auto_file.append(decision)
        else:
            decision.auto_file = False
            review_queue.append(decision)

    logger.info(
        "Confidence gate (threshold=%.2f): %d auto-file, %d review",
        threshold, len(auto_file), len(review_queue),
    )
    return auto_file, review_queue
