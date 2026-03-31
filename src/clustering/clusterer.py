"""Clustering: group PageRecords into DocumentCandidates."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from src.ocr.analyzer import PageRecord

logger = logging.getLogger(__name__)


@dataclass
class DocumentCandidate:
    pages: list[int]
    institution: str
    account: str | None
    period: str | None
    doc_type: str | None
    clustering_confidence: float
    raw_signals: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_page_of_n(page_of_n: str | None) -> tuple[int | None, int | None]:
    """Extract (page_num, total_pages) from a 'Page X of Y' string."""
    if not page_of_n:
        return None, None
    import re
    m = re.search(r"(\d+)\s+of\s+(\d+)", page_of_n, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _majority(values: list[str | None]) -> str | None:
    """Return the most common non-None value, or None."""
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return Counter(filtered).most_common(1)[0][0]


def _is_blank_page(record: PageRecord) -> bool:
    """A page with very little text is treated as blank."""
    return len(record.raw_text.strip()) < 20


# ---------------------------------------------------------------------------
# Rule-based clustering
# ---------------------------------------------------------------------------

def cluster_pages(page_records: list[PageRecord], config: dict) -> list[DocumentCandidate]:
    """Group *page_records* into document candidates.

    Strategy (rule-based, no LLM):
    1. 'Page 1 of N' always starts a new document.
    2. Pages with the same institution + account hint are grouped together,
       as long as they are contiguous.
    3. Blank pages are assigned to the document they immediately follow.
    4. A change in institution signal starts a new document.
    """
    if not page_records:
        return []

    # Each group is a list of PageRecords
    groups: list[list[PageRecord]] = []
    current_group: list[PageRecord] = []

    for record in page_records:
        page_x, _page_total = _parse_page_of_n(record.page_of_n)

        # Blank pages attach to the current group
        if _is_blank_page(record):
            if current_group:
                current_group.append(record)
            else:
                # Blank page at the very start — hold it for now
                current_group = [record]
            continue

        # "Page 1 of N" always starts a new document
        if page_x == 1 and current_group:
            groups.append(current_group)
            current_group = [record]
            continue

        # If we have no current group, start one
        if not current_group:
            current_group = [record]
            continue

        # Check if this page belongs with the current group
        # by comparing institution and account hints
        prev_non_blank = next(
            (r for r in reversed(current_group) if not _is_blank_page(r)),
            None,
        )

        if prev_non_blank is None:
            # Current group is all blank pages — attach this page
            current_group.append(record)
            continue

        same_institution = (
            record.institution is not None
            and record.institution == prev_non_blank.institution
        )
        same_account = (
            record.account_hint is not None
            and record.account_hint == prev_non_blank.account_hint
        )

        # If institution changed and we have a new institution signal, split
        if record.institution is not None and not same_institution:
            groups.append(current_group)
            current_group = [record]
            continue

        # If same institution, or current page has no institution (continuation)
        # keep it in the current group
        if same_institution or record.institution is None:
            # But if we have different account hints on the same institution,
            # that's a different document (e.g. two PNC accounts)
            if (same_institution
                    and record.account_hint is not None
                    and prev_non_blank.account_hint is not None
                    and not same_account):
                groups.append(current_group)
                current_group = [record]
                continue

            current_group.append(record)
            continue

        # Default: new group
        groups.append(current_group)
        current_group = [record]

    # Don't forget the last group
    if current_group:
        groups.append(current_group)

    # Convert groups to DocumentCandidates
    candidates = []
    for group in groups:
        institutions = [r.institution for r in group]
        accounts = [r.account_hint for r in group]
        periods = [r.period_hint for r in group]
        doc_types = [r.doc_type_hint for r in group]
        confidences = [r.confidence for r in group if not _is_blank_page(r)]

        institution = _majority(institutions) or "unknown"
        avg_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )

        # Confidence adjustments
        clustering_confidence = avg_confidence
        # Boost if all pages agree on institution
        non_none_institutions = [i for i in institutions if i is not None]
        if non_none_institutions and len(set(non_none_institutions)) == 1:
            clustering_confidence = min(clustering_confidence + 0.1, 1.0)
        # Penalise if institution is unknown
        if institution == "unknown":
            clustering_confidence = max(clustering_confidence - 0.2, 0.0)

        raw_signals = {
            "institutions": institutions,
            "accounts": accounts,
            "periods": periods,
            "doc_types": doc_types,
            "page_of_n": [r.page_of_n for r in group],
        }

        candidate = DocumentCandidate(
            pages=[r.page_number for r in group],
            institution=institution,
            account=_majority(accounts),
            period=_majority(periods),
            doc_type=_majority(doc_types),
            clustering_confidence=round(clustering_confidence, 3),
            raw_signals=raw_signals,
        )
        candidates.append(candidate)

        logger.info(
            "Cluster: pages %s → %s (account=%s, period=%s, confidence=%.2f)",
            candidate.pages, candidate.institution, candidate.account,
            candidate.period, candidate.clustering_confidence,
        )

    # Assertion: every page must be assigned to exactly one candidate
    assigned_pages = [p for c in candidates for p in c.pages]
    all_pages = [r.page_number for r in page_records]
    assert sorted(assigned_pages) == sorted(all_pages), (
        f"Page assignment mismatch: assigned={sorted(assigned_pages)}, "
        f"expected={sorted(all_pages)}"
    )

    return candidates
