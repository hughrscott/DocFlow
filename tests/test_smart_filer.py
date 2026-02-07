"""Tests for the smart filer service."""

import json
import asyncio
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock

from services.smart_filer import SmartFiler
from llm.base import LLMResponse, ProviderType


def _mock_llm():
    mgr = MagicMock()
    mgr.get_provider_name.return_value = "claude"
    return mgr


class TestSmartFiler:
    def test_scan_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = _mock_llm()
            filer = SmartFiler(mgr, documents_dir=tmpdir)
            tree = filer.scan_directory_tree()
            assert "[root]" in tree

    def test_scan_populated_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some folders and files
            banking = os.path.join(tmpdir, "Banking", "PNC")
            os.makedirs(banking)
            for name in ["statement-2025-01.pdf", "statement-2025-02.pdf"]:
                open(os.path.join(banking, name), "w").close()

            taxes = os.path.join(tmpdir, "Taxes")
            os.makedirs(taxes)
            open(os.path.join(taxes, "w2-2024.pdf"), "w").close()

            mgr = _mock_llm()
            filer = SmartFiler(mgr, documents_dir=tmpdir)
            tree = filer.scan_directory_tree()

            assert "Banking" in tree
            assert "PNC" in tree
            assert "statement-2025-01.pdf" in tree
            assert "Taxes" in tree

    def test_propose_filing_success(self):
        """Successful LLM response is parsed into a proposal."""
        mgr = _mock_llm()
        response = LLMResponse(
            content=json.dumps({
                "proposed_folder": "Banking/Personal/PNC",
                "proposed_filename": "PNC-Statement-2025-01.pdf",
                "rationale": "Matches existing PNC folder under Banking/Personal",
                "alternatives": [
                    {"folder": "Banking/PNC", "reason": "shorter path"}
                ],
                "confidence": 0.9,
            }),
            model="test",
            provider=ProviderType.CLAUDE,
            success=True,
        )
        mgr.process_text = AsyncMock(return_value=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            filer = SmartFiler(mgr, documents_dir=tmpdir)
            metadata = {
                "document_type": "bank_statement",
                "document_category": "Banking",
                "institution": "PNC",
                "classification_metadata": {"account_last4": "1234"},
            }
            result = asyncio.get_event_loop().run_until_complete(
                filer.propose_filing(metadata)
            )
            assert result["proposed_folder"] == "Banking/Personal/PNC"
            assert result["proposed_filename"] == "PNC-Statement-2025-01.pdf"
            assert result["confidence"] == 0.9
            assert result["is_new_folder"] is True  # folder doesn't exist in tmpdir

    def test_propose_filing_existing_folder(self):
        """is_new_folder is False when folder exists."""
        mgr = _mock_llm()
        response = LLMResponse(
            content=json.dumps({
                "proposed_folder": "Banking",
                "proposed_filename": "doc.pdf",
                "rationale": "existing folder",
                "confidence": 0.8,
            }),
            model="test",
            provider=ProviderType.CLAUDE,
            success=True,
        )
        mgr.process_text = AsyncMock(return_value=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "Banking"))
            filer = SmartFiler(mgr, documents_dir=tmpdir)
            result = asyncio.get_event_loop().run_until_complete(
                filer.propose_filing({"document_type": "bank_statement"})
            )
            assert result["is_new_folder"] is False

    def test_propose_filing_llm_failure_fallback(self):
        """When LLM fails, uses fallback proposal."""
        mgr = _mock_llm()
        fail = LLMResponse(
            content="", model="test", provider=ProviderType.CLAUDE,
            error="API down", success=False,
        )
        mgr.process_text = AsyncMock(return_value=fail)

        with tempfile.TemporaryDirectory() as tmpdir:
            filer = SmartFiler(mgr, documents_dir=tmpdir)
            result = asyncio.get_event_loop().run_until_complete(
                filer.propose_filing({
                    "document_type": "invoice",
                    "document_category": "Finance",
                    "institution": "Acme Corp",
                    "classification_metadata": {},
                })
            )
            assert result["proposed_folder"] == "Finance/Acme-Corp"
            assert result["confidence"] == 0.3

    def test_correction_injection(self):
        mgr = _mock_llm()
        filer = SmartFiler(mgr)
        corrections = [
            {
                "correction_type": "filing",
                "proposed_folder": "Banking/PNC",
                "actual_folder": "Banking/Personal/PNC-Checking",
                "user_rationale": "separate checking and savings",
            }
        ]
        context = filer._inject_filing_corrections(corrections, {})
        assert "PAST FILING CORRECTIONS" in context
        assert "PNC-Checking" in context

    def test_correction_injection_empty(self):
        mgr = _mock_llm()
        filer = SmartFiler(mgr)
        assert filer._inject_filing_corrections(None, {}) == ""
        assert filer._inject_filing_corrections([], {}) == ""

    def test_parse_filing_response_invalid(self):
        assert SmartFiler._parse_filing_response("garbage") is None
        # Missing required fields
        assert SmartFiler._parse_filing_response('{"rationale": "test"}') is None

    def test_fallback_proposal_no_institution(self):
        result = SmartFiler._fallback_proposal({
            "document_type": "generic",
            "document_category": "Documents",
            "institution": None,
            "classification_metadata": {},
        })
        assert result["proposed_folder"] == "Documents"
        assert result["proposed_filename"].endswith(".pdf")
