"""Tests for src/extraction/extractor."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from src.clustering.clusterer import DocumentCandidate
from src.classification.classifier import FilingDecision
from src.extraction.extractor import extract_documents


@pytest.fixture()
def three_page_pdf(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "source.pdf"
    writer = PdfWriter()
    for i in range(3):
        # Use unique dimensions so dedup hash doesn't collide with real archive files
        writer.add_blank_page(width=612 + i, height=792 + i)
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path


def _make_decision(
    pages: list[int],
    filename: str,
    target_dir: str,
) -> FilingDecision:
    candidate = DocumentCandidate(
        pages=pages, institution="pnc", account="1236",
        period="February2026", doc_type="statement",
        clustering_confidence=0.9,
    )
    return FilingDecision(
        candidate=candidate,
        filename=filename,
        target_directory=target_dir,
        rule_matched="test_rule",
        confidence=0.9,
        auto_file=True,
        notes=None,
    )


class TestExtractDocuments:
    def test_extracts_correct_pages(self, three_page_pdf, tmp_path):
        target = str(tmp_path / "output")
        decisions = [
            _make_decision([1, 2], "doc1.pdf", target),
            _make_decision([3], "doc2.pdf", target),
        ]
        config = {"archive_root": str(tmp_path / "archive")}
        written = extract_documents(three_page_pdf, decisions, config)

        assert len(written) == 2
        assert written[0].name == "doc1.pdf"
        assert written[1].name == "doc2.pdf"
        assert written[0].exists()
        assert written[1].exists()

    def test_creates_target_directories(self, three_page_pdf, tmp_path):
        target = str(tmp_path / "deep" / "nested" / "dir")
        decisions = [_make_decision([1], "test.pdf", target)]
        config = {"archive_root": str(tmp_path / "archive")}
        extract_documents(three_page_pdf, decisions, config)
        assert Path(target).is_dir()

    def test_filing_log_written(self, three_page_pdf, tmp_path):
        target = str(tmp_path / "output")
        archive = tmp_path / "archive"
        decisions = [_make_decision([1], "test.pdf", target)]
        config = {"archive_root": str(archive)}
        extract_documents(three_page_pdf, decisions, config)

        logs = list(archive.glob("filing_log_*.json"))
        assert len(logs) == 1
        data = json.loads(logs[0].read_text())
        assert len(data["entries"]) == 1
        assert data["entries"][0]["filename"] == "test.pdf"
