"""Clustering: group PageRecords into DocumentCandidates.

Strategy: LLM-first with rule-based fallback.
The LLM sees OCR text + extracted signals and determines document boundaries.
Rule-based clustering is only used when the LLM is unavailable.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from docflow.ocr.analyzer import PageRecord

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


def _build_candidates_from_groups(
    groups: list[list[PageRecord]],
    source: str = "rules",
) -> list[DocumentCandidate]:
    """Convert page groups into DocumentCandidate objects."""
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

        clustering_confidence = avg_confidence
        non_none_institutions = [i for i in institutions if i is not None]
        if non_none_institutions and len(set(non_none_institutions)) == 1:
            clustering_confidence = min(clustering_confidence + 0.1, 1.0)
        if institution == "unknown":
            clustering_confidence = max(clustering_confidence - 0.2, 0.0)

        raw_signals = {
            "institutions": institutions,
            "accounts": accounts,
            "periods": periods,
            "doc_types": doc_types,
            "page_of_n": [r.page_of_n for r in group],
            "raw_texts": [r.raw_text for r in group],
            "clustering_source": source,
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

    return candidates


# ---------------------------------------------------------------------------
# Main entry point: LLM-first, rule-based fallback
# ---------------------------------------------------------------------------

def cluster_pages(page_records: list[PageRecord], config: dict) -> list[DocumentCandidate]:
    """Group *page_records* into document candidates.

    Strategy: LLM-first with rule-based fallback.
    1. Always attempt LLM clustering — it sees all pages with OCR text
       and extracted signals, and determines document boundaries.
    2. If LLM fails (network error, bad response), fall back to rule-based.
    3. For single-page PDFs, skip LLM (nothing to cluster).
    """
    if not page_records:
        return []

    # Single page — no clustering needed
    if len(page_records) == 1:
        return _build_candidates_from_groups([page_records], source="single")

    # Try LLM clustering first
    llm_candidates = _llm_cluster(page_records, config)
    if llm_candidates:
        candidates = llm_candidates
    else:
        # Fallback to rule-based clustering
        logger.info("Using rule-based clustering fallback")
        candidates = _rule_based_cluster(page_records, config)

    # Assertion: every page must be assigned to exactly one candidate
    assigned_pages = [p for c in candidates for p in c.pages]
    all_pages = [r.page_number for r in page_records]
    assert sorted(assigned_pages) == sorted(all_pages), (
        f"Page assignment mismatch: assigned={sorted(assigned_pages)}, "
        f"expected={sorted(all_pages)}"
    )

    return candidates


# ---------------------------------------------------------------------------
# LLM clustering (primary)
# ---------------------------------------------------------------------------

def _llm_cluster(
    page_records: list[PageRecord], config: dict
) -> list[DocumentCandidate] | None:
    """Use OpenRouter LLM to cluster pages into documents.

    Sends all pages with their OCR text and extracted signals.
    Returns a list of DocumentCandidates, or None if the LLM call fails.
    """
    from docflow.llm.client import chat_json
    from docflow.llm.prompts import build_clustering_prompt

    # Build rich page summaries for the prompt
    page_summaries = []
    for r in page_records:
        text = r.raw_text.strip()
        # Give the LLM substantial text — 800 chars is enough to identify
        # letterheads, institutions, account numbers, and document type
        first_lines = text[:800] if text else "(blank page)"

        page_summaries.append({
            "page_number": r.page_number,
            "first_lines": first_lines,
            "institution_hint": r.institution,
            "account_hint": r.account_hint,
            "period_hint": r.period_hint,
            "doc_type_hint": r.doc_type_hint,
            "page_of_n": r.page_of_n,
        })

    prompt = build_clustering_prompt(page_summaries)

    try:
        result = chat_json(prompt, config=config)
    except Exception:
        logger.exception("LLM clustering call failed")
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

        # Prefer OCR-extracted institution, fall back to LLM's guess
        institution = (
            _majority(institutions)
            or doc.get("institution")
            or "unknown"
        )
        period = _majority(periods) or doc.get("period")
        doc_type = _majority(doc_types) or doc.get("doc_type")

        # LLM provides its own confidence per document
        llm_confidence = doc.get("confidence", 0.75)

        raw_signals = {
            "institutions": institutions,
            "accounts": accounts,
            "periods": periods,
            "doc_types": doc_types,
            "page_of_n": [r.page_of_n for r in doc_records],
            "raw_texts": [r.raw_text for r in doc_records],
            "clustering_source": "llm",
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
            clustering_confidence=round(llm_confidence, 3),
            raw_signals=raw_signals,
        )
        candidates.append(candidate)
        logger.info(
            "LLM cluster: pages %s → %s (doc_type=%s, conf=%.2f, reason=%s)",
            candidate.pages, candidate.institution,
            candidate.doc_type, llm_confidence,
            doc.get("reasoning", "")[:80],
        )

    # Verify all pages are assigned
    expected = {r.page_number for r in page_records}
    if all_assigned != expected:
        missing = expected - all_assigned
        logger.warning("LLM missed pages %s — falling back to rule-based", missing)
        return None

    return candidates


# ---------------------------------------------------------------------------
# Rule-based clustering (fallback)
# ---------------------------------------------------------------------------

def _rule_based_cluster(
    page_records: list[PageRecord], config: dict
) -> list[DocumentCandidate]:
    """Group pages using heuristic rules. Used when LLM is unavailable.

    Rules:
    1. 'Page 1 of N' always starts a new document.
    2. 'Page N of N' (last page) means next page is a new document.
    3. Institution change = new document.
    4. Same institution, different account = new document.
    5. Blank pages attach to current group.
    6. No institution + doc_type change = new document.
    7. After 3+ pages with institution, a no-institution page = new document.
    """
    groups: list[list[PageRecord]] = []
    current_group: list[PageRecord] = []

    for record in page_records:
        page_x, _page_total = _parse_page_of_n(record.page_of_n)

        # Blank pages attach to the current group
        if _is_blank_page(record):
            if current_group:
                current_group.append(record)
            else:
                current_group = [record]
            continue

        # "Page 1 of N" always starts a new document
        if page_x == 1 and current_group:
            groups.append(current_group)
            current_group = [record]
            continue

        # If previous page was "Page N of N" (last page), start new doc
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

        prev_non_blank = next(
            (r for r in reversed(current_group) if not _is_blank_page(r)),
            None,
        )

        if prev_non_blank is None:
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

        # Institution changed → split
        if record.institution is not None and not same_institution:
            groups.append(current_group)
            current_group = [record]
            continue

        # Same institution, different account → split
        if same_institution:
            if (record.account_hint is not None
                    and prev_non_blank.account_hint is not None
                    and not same_account):
                groups.append(current_group)
                current_group = [record]
                continue
            current_group.append(record)
            continue

        # No institution — check for doc_type change
        if prev_non_blank.institution is None:
            prev_doc_type = prev_non_blank.doc_type_hint
            curr_doc_type = record.doc_type_hint
            if (prev_doc_type is not None
                    and curr_doc_type is not None
                    and prev_doc_type != curr_doc_type):
                groups.append(current_group)
                current_group = [record]
                continue

        # After 3+ pages with institution, no-institution page = new doc
        if prev_non_blank.institution is not None and record.institution is None:
            non_blank_count = sum(1 for r in current_group if not _is_blank_page(r))
            if non_blank_count >= 3:
                groups.append(current_group)
                current_group = [record]
                continue

        current_group.append(record)

    if current_group:
        groups.append(current_group)

    return _build_candidates_from_groups(groups, source="rules")
