"""Tests for src/clustering/clusterer."""
from __future__ import annotations

import pytest

from docflow.ocr.analyzer import PageRecord
from docflow.clustering.clusterer import cluster_pages, DocumentCandidate


def _make_record(
    page_number: int,
    institution: str | None = None,
    account_hint: str | None = None,
    period_hint: str | None = None,
    page_of_n: str | None = None,
    doc_type_hint: str | None = None,
    raw_text: str = "Some text content for confidence",
    confidence: float = 0.8,
) -> PageRecord:
    return PageRecord(
        page_number=page_number,
        raw_text=raw_text,
        institution=institution,
        account_hint=account_hint,
        period_hint=period_hint,
        page_of_n=page_of_n,
        doc_type_hint=doc_type_hint,
        confidence=confidence,
        rotation_applied=0,
    )


class TestClusterPages:
    def test_single_document(self):
        records = [
            _make_record(1, institution="pnc", page_of_n="Page 1 of 2"),
            _make_record(2, institution="pnc", page_of_n="Page 2 of 2"),
        ]
        candidates = cluster_pages(records, {})
        assert len(candidates) == 1
        assert candidates[0].pages == [1, 2]
        assert candidates[0].institution == "pnc"

    def test_two_documents_different_institutions(self):
        records = [
            _make_record(1, institution="pnc"),
            _make_record(2, institution="pnc"),
            _make_record(3, institution="guardian"),
            _make_record(4, institution="guardian"),
        ]
        candidates = cluster_pages(records, {})
        assert len(candidates) == 2
        assert candidates[0].pages == [1, 2]
        assert candidates[0].institution == "pnc"
        assert candidates[1].pages == [3, 4]
        assert candidates[1].institution == "guardian"

    def test_page_1_of_n_starts_new_document(self):
        records = [
            _make_record(1, institution="pnc", page_of_n="Page 1 of 2"),
            _make_record(2, institution="pnc", page_of_n="Page 2 of 2"),
            _make_record(3, institution="pnc", page_of_n="Page 1 of 1"),
        ]
        candidates = cluster_pages(records, {})
        assert len(candidates) == 2
        assert candidates[0].pages == [1, 2]
        assert candidates[1].pages == [3]

    def test_blank_page_attached_to_preceding(self):
        records = [
            _make_record(1, institution="pnc"),
            _make_record(2, raw_text="", confidence=0.0),  # blank
            _make_record(3, institution="guardian"),
        ]
        candidates = cluster_pages(records, {})
        assert len(candidates) == 2
        assert candidates[0].pages == [1, 2]  # blank attached to pnc
        assert candidates[1].pages == [3]

    def test_different_accounts_same_institution_split(self):
        records = [
            _make_record(1, institution="pnc", account_hint="1236"),
            _make_record(2, institution="pnc", account_hint="5678"),
        ]
        candidates = cluster_pages(records, {})
        assert len(candidates) == 2

    def test_all_pages_assigned(self):
        records = [
            _make_record(1, institution="pnc"),
            _make_record(2, institution="guardian"),
            _make_record(3, institution="frost"),
        ]
        candidates = cluster_pages(records, {})
        all_pages = sorted(p for c in candidates for p in c.pages)
        assert all_pages == [1, 2, 3]

    def test_empty_input(self):
        assert cluster_pages([], {}) == []

    def test_continuation_page_no_institution(self):
        """A page with no institution signal stays with the current group."""
        records = [
            _make_record(1, institution="pnc"),
            _make_record(2, institution=None),  # continuation
            _make_record(3, institution="guardian"),
        ]
        candidates = cluster_pages(records, {})
        assert len(candidates) == 2
        assert candidates[0].pages == [1, 2]
        assert candidates[1].pages == [3]

    def test_majority_signals(self):
        records = [
            _make_record(1, institution="pnc", period_hint="February2026"),
            _make_record(2, institution="pnc", period_hint="February2026"),
        ]
        candidates = cluster_pages(records, {})
        assert candidates[0].period == "February2026"
        assert candidates[0].institution == "pnc"
