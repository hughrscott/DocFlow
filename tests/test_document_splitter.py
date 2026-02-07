"""Tests for AI-powered document splitter."""

import json
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.document_splitter import DocumentSplitter, SplitBoundary
from llm.base import LLMResponse, ProviderType


def _make_pdf(num_pages=3):
    """Create a minimal multi-page PDF for testing."""
    from PyPDF2 import PdfWriter
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _mock_llm_manager(provider_name="claude"):
    mgr = MagicMock()
    mgr.get_provider_name.return_value = provider_name
    return mgr


class TestSplitBoundary:
    def test_repr(self):
        b = SplitBoundary(1, 3, "invoice", 0.9, "header change")
        assert "1-3" in repr(b)
        assert "invoice" in repr(b)


class TestDocumentSplitter:
    def test_single_page_pdf(self):
        """Single page PDF returns one boundary."""
        import asyncio
        pdf = _make_pdf(1)
        mgr = _mock_llm_manager()
        splitter = DocumentSplitter(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            splitter.split_document(pdf)
        )
        assert len(result) == 1
        assert result[0].start_page == 1
        assert result[0].end_page == 1

    def test_text_split_path(self):
        """When no page images provided, uses text-based splitting."""
        import asyncio

        pdf = _make_pdf(3)
        mgr = _mock_llm_manager("ollama")

        response = LLMResponse(
            content=json.dumps([
                {"start_page": 1, "end_page": 2, "document_type_hint": "letter", "confidence": 0.8, "rationale": "same header"},
                {"start_page": 3, "end_page": 3, "document_type_hint": "invoice", "confidence": 0.7, "rationale": "new header"},
            ]),
            model="test",
            provider=ProviderType.OLLAMA,
            success=True,
        )
        mgr.process_text = AsyncMock(return_value=response)

        splitter = DocumentSplitter(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            splitter.split_document(pdf)
        )
        assert len(result) == 2
        assert result[0].start_page == 1
        assert result[0].end_page == 2
        assert result[1].start_page == 3

    def test_multi_image_split_path(self):
        """Claude path sends images and parses boundaries."""
        import asyncio

        pdf = _make_pdf(4)
        page_images = [b"fake_img"] * 4
        mgr = _mock_llm_manager("claude")

        response = LLMResponse(
            content=json.dumps([
                {"start_page": 1, "end_page": 2, "document_type_hint": "bank_statement", "confidence": 0.9, "rationale": "PNC header"},
                {"start_page": 3, "end_page": 4, "document_type_hint": "letter", "confidence": 0.85, "rationale": "new letterhead"},
            ]),
            model="test",
            provider=ProviderType.CLAUDE,
            success=True,
        )
        mgr.analyze_images = AsyncMock(return_value=response)

        splitter = DocumentSplitter(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            splitter.split_document(pdf, page_images)
        )
        assert len(result) == 2
        assert result[0].document_type_hint == "bank_statement"
        assert result[1].document_type_hint == "letter"

    def test_fallback_on_failure(self):
        """If all LLM calls fail, returns single boundary for whole PDF."""
        import asyncio

        pdf = _make_pdf(5)
        mgr = _mock_llm_manager("claude")
        mgr.analyze_images = AsyncMock(side_effect=Exception("API down"))
        mgr.process_text = AsyncMock(side_effect=Exception("API down"))

        splitter = DocumentSplitter(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            splitter.split_document(pdf, [b"img"] * 5)
        )
        assert len(result) == 1
        assert result[0].start_page == 1
        assert result[0].end_page == 5

    def test_gap_filling(self):
        """Gaps between boundaries are filled."""
        splitter = DocumentSplitter(_mock_llm_manager())
        boundaries = [
            SplitBoundary(2, 3, "letter", 0.8, ""),
            SplitBoundary(6, 7, "invoice", 0.7, ""),
        ]
        filled = splitter._fill_gaps(boundaries, 7)
        pages_covered = set()
        for b in filled:
            for p in range(b.start_page, b.end_page + 1):
                pages_covered.add(p)
        assert pages_covered == {1, 2, 3, 4, 5, 6, 7}

    def test_extract_sub_document_pdf(self):
        """Extract a page range into a new PDF."""
        pdf = _make_pdf(5)
        sub_pdf = DocumentSplitter.extract_sub_document_pdf(pdf, 2, 4)
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(sub_pdf))
        assert len(reader.pages) == 3

    def test_parse_malformed_json(self):
        """Parser handles garbage gracefully."""
        splitter = DocumentSplitter(_mock_llm_manager())
        result = splitter._parse_split_response("not json at all", 1)
        assert result == []

    def test_parse_partial_items(self):
        """Items missing required keys are skipped."""
        splitter = DocumentSplitter(_mock_llm_manager())
        content = json.dumps([
            {"start_page": 1, "end_page": 2},  # valid
            {"confidence": 0.5},  # missing start/end
        ])
        result = splitter._parse_split_response(content, 1)
        assert len(result) == 1
