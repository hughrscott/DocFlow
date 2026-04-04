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

        # If the PREVIOUS page was "Page N of N" (last page), this starts a new doc
        prev = current_group[-1] if current_group else None
        if prev:
            prev_x, prev_total = _parse_page_of_n(prev.page_of_n)
            if prev_x is not None and prev_total is not None and prev_x == prev_total:
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

        # If same institution, check for different accounts
        if same_institution:
            if (record.account_hint is not None
                    and prev_non_blank.account_hint is not None
                    and not same_account):
                groups.append(current_group)
                current_group = [record]
                continue
            current_group.append(record)
            continue

        # Institution is None — page has no institution signal.
        # Check if the previous page also had no institution.
        if prev_non_blank.institution is None:
            # Both pages lack institution. Look for other boundary signals:
            # - Different doc_type hints suggest different documents
            # - Previous page had a period but this one has a different one
            prev_doc_type = prev_non_blank.doc_type_hint
            curr_doc_type = record.doc_type_hint
            if (prev_doc_type is not None
                    and curr_doc_type is not None
                    and prev_doc_type != curr_doc_type):
                groups.append(current_group)
                current_group = [record]
                continue

        # If previous had an institution but current doesn't, it could be
        # a continuation OR a new document. Use a heuristic: if the current
        # group already has 3+ pages, a page with no institution signal
        # is more likely a new document than a continuation.
        if prev_non_blank.institution is not None and record.institution is None:
            non_blank_count = sum(1 for r in current_group if not _is_blank_page(r))
            if non_blank_count >= 3:
                groups.append(current_group)
                current_group = [record]
                continue

        current_group.append(record)
        continue

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
            "raw_texts": [r.raw_text for r in group],
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

    # Determine if LLM re-clustering is needed
    needs_llm = False

    # Single blob with unknown institution
    if (len(candidates) == 1
            and len(candidates[0].pages) > 1
            and candidates[0].institution == "unknown"):
        needs_llm = True

    # Any candidate with too many pages is suspicious
    oversized = [c for c in candidates if len(c.pages) > 4]
    if oversized:
        needs_llm = True

    # Low clustering confidence
    low_conf = [c for c in candidates if c.clustering_confidence < 0.5]
    if low_conf:
        needs_llm = True

    if needs_llm:
        logger.info("Rule-based clustering insufficient — calling LLM")
        llm_candidates = _llm_cluster(page_records, config)
        if llm_candidates:
            candidates = llm_candidates

    # Assertion: every page must be assigned to exactly one candidate
    assigned_pages = [p for c in candidates for p in c.pages]
    all_pages = [r.page_number for r in page_records]
    assert sorted(assigned_pages) == sorted(all_pages), (
        f"Page assignment mismatch: assigned={sorted(assigned_pages)}, "
        f"expected={sorted(all_pages)}"
    )

    return candidates


def _llm_cluster(
    page_records: list[PageRecord], config: dict
) -> list[DocumentCandidate] | None:
    """Use OpenRouter LLM to cluster pages into documents.

    Returns a list of DocumentCandidates, or None if the LLM call fails.
    """
    from src.llm.client import chat_json
    from src.llm.prompts import build_clustering_prompt

    # Build page summaries for the prompt
    page_summaries = []
    for r in page_records:
        first_lines = r.raw_text.strip()[:500] if r.raw_text.strip() else "(blank)"
        page_summaries.append({
            "page_number": r.page_number,
            "first_lines": first_lines,
            "institution_hint": r.institution,
            "period_hint": r.period_hint,
            "doc_type_hint": r.doc_type_hint,
        })

    prompt = build_clustering_prompt(page_summaries)

    try:
        result = chat_json(prompt, config=config)
    except Exception:
        logger.exception("LLM clustering call failed — falling back to rule-based")
        return None

    # Parse LLM response into DocumentCandidates
    documents = result.get("documents", [])
    if not documents:
        logger.warning("LLM returned no documents")
        return None

    candidates = []
    all_assigned: set[int] = set()

    for doc in documents:
        pages = doc.get("pages", [])
        if not pages:
            continue

        all_assigned.update(pages)

        # Cross-reference with PageRecords to get raw signals
        doc_records = [r for r in page_records if r.page_number in pages]
        institutions = [r.institution for r in doc_records]
        accounts = [r.account_hint for r in doc_records]
        periods = [r.period_hint for r in doc_records]
        doc_types = [r.doc_type_hint for r in doc_records]

        institution = (
            _majority(institutions)
            or doc.get("institution")
            or "unknown"
        )
        period = _majority(periods) or doc.get("period")
        doc_type = _majority(doc_types) or doc.get("doc_type")

        raw_signals = {
            "institutions": institutions,
            "accounts": accounts,
            "periods": periods,
            "doc_types": doc_types,
            "page_of_n": [r.page_of_n for r in doc_records],
            "raw_texts": [r.raw_text for r in doc_records],
            "llm_reasoning": doc.get("reasoning", ""),
            "llm_institution": doc.get("institution"),
            "llm_doc_type": doc.get("doc_type"),
        }

        candidate = DocumentCandidate(
            pages=sorted(pages),
            institution=institution,
            account=_majority(accounts),
            period=period,
            doc_type=doc_type,
            clustering_confidence=0.7,  # LLM-based gets a reasonable default
            raw_signals=raw_signals,
        )
        candidates.append(candidate)
        logger.info(
            "LLM cluster: pages %s → %s (doc_type=%s, period=%s)",
            candidate.pages, candidate.institution,
            candidate.doc_type, candidate.period,
        )

    # Verify all pages are assigned
    expected = {r.page_number for r in page_records}
    if all_assigned != expected:
        missing = expected - all_assigned
        logger.warning("LLM missed pages %s — falling back to rule-based", missing)
        return None

    return candidates
