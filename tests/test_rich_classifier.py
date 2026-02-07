"""Tests for the rich classifier service."""

import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.rich_classifier import RichClassifier, TYPE_SCHEMAS, TYPE_ALIASES
from llm.base import LLMResponse, ProviderType


def _mock_llm(provider_name="claude"):
    mgr = MagicMock()
    mgr.get_provider_name.return_value = provider_name
    return mgr


class TestRichClassifier:
    def test_type_schemas_exist(self):
        """All documented types have schemas."""
        expected = [
            "bank_statement", "letter", "invoice", "tax_form",
            "utility_bill", "legal_document", "insurance_document",
            "medical_document", "generic",
        ]
        for t in expected:
            assert t in TYPE_SCHEMAS

    def test_type_aliases_resolve(self):
        """Common aliases map to canonical types."""
        assert TYPE_ALIASES["bank statement"] == "bank_statement"
        assert TYPE_ALIASES["w-2"] == "tax_form"
        assert TYPE_ALIASES["receipt"] == "invoice"
        assert TYPE_ALIASES["electric bill"] == "utility_bill"

    def test_classify_two_phase(self):
        """End-to-end classify with mocked LLM calls."""
        mgr = _mock_llm("claude")

        # Phase 1 response
        phase1 = LLMResponse(
            content=json.dumps({
                "document_type": "bank_statement",
                "confidence": 0.92,
                "brief_reason": "PNC header visible",
            }),
            model="test",
            provider=ProviderType.CLAUDE,
            success=True,
        )
        # Phase 2 response
        phase2 = LLMResponse(
            content=json.dumps({
                "bank_name": "PNC Bank",
                "account_type": "checking",
                "account_last4": "1234",
                "statement_period": "2025-01",
                "opening_balance": 5000.00,
                "closing_balance": 4800.00,
                "total_deposits": 1000.00,
                "total_withdrawals": 1200.00,
            }),
            model="test",
            provider=ProviderType.CLAUDE,
            success=True,
        )
        mgr.analyze_image = AsyncMock(side_effect=[phase1, phase2])
        mgr.analyze_images = AsyncMock(return_value=phase2)

        classifier = RichClassifier(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            classifier.classify(page_images=[b"fake_image"])
        )
        assert result["document_type"] == "bank_statement"
        assert result["institution"] == "PNC Bank"
        assert result["confidence"] == 0.92
        assert result["classification_metadata"]["account_last4"] == "1234"

    def test_classify_with_text_fallback(self):
        """If vision fails, falls back to text analysis."""
        mgr = _mock_llm("ollama")

        fail_response = LLMResponse(
            content="", model="test", provider=ProviderType.OLLAMA,
            error="vision failed", success=False,
        )
        text_response = LLMResponse(
            content=json.dumps({
                "document_type": "invoice",
                "confidence": 0.7,
                "brief_reason": "vendor and amount",
            }),
            model="test",
            provider=ProviderType.OLLAMA,
            success=True,
        )
        mgr.analyze_image = AsyncMock(return_value=fail_response)
        mgr.process_text = AsyncMock(return_value=text_response)
        mgr.analyze_images = AsyncMock(return_value=text_response)

        classifier = RichClassifier(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            classifier.classify(
                page_images=[b"fake"],
                extracted_text="Invoice #1234 from Acme Corp. Total: $500",
            )
        )
        assert result["document_type"] == "invoice"

    def test_classify_no_images(self):
        """No images returns empty result."""
        mgr = _mock_llm()
        classifier = RichClassifier(mgr)
        result = asyncio.get_event_loop().run_until_complete(
            classifier.classify(page_images=[])
        )
        assert result["document_type"] == "generic"
        assert result["confidence"] == 0.0

    def test_learning_injection(self):
        """Corrections are injected into prompts."""
        classifier = RichClassifier(_mock_llm())
        corrections = [
            {
                "correction_type": "classification",
                "institution": "PNC",
                "proposed_document_type": "letter",
                "actual_document_type": "bank_statement",
                "user_rationale": "this is clearly a statement",
            }
        ]
        context = classifier._inject_learning_context(corrections, "classification")
        assert "PAST CORRECTIONS" in context
        assert "PNC" in context
        assert "bank_statement" in context

    def test_learning_injection_empty(self):
        classifier = RichClassifier(_mock_llm())
        assert classifier._inject_learning_context(None, "classification") == ""
        assert classifier._inject_learning_context([], "classification") == ""

    def test_extract_institution(self):
        assert RichClassifier._extract_institution("bank_statement", {"bank_name": "Chase"}) == "Chase"
        assert RichClassifier._extract_institution("invoice", {"vendor": "Acme"}) == "Acme"
        assert RichClassifier._extract_institution("generic", {}) is None

    def test_category_for_type(self):
        assert RichClassifier._category_for_type("bank_statement") == "Banking"
        assert RichClassifier._category_for_type("tax_form") == "Taxes"
        assert RichClassifier._category_for_type("unknown_thing") == "Documents"

    def test_representative_indices(self):
        assert RichClassifier._representative_indices(1) == [0]
        assert RichClassifier._representative_indices(2) == [0, 1]
        assert RichClassifier._representative_indices(10) == [0, 5, 9]

    def test_parse_json_with_wrapper(self):
        """Parser extracts JSON from markdown code blocks."""
        content = '```json\n{"document_type": "letter", "confidence": 0.8}\n```'
        result = RichClassifier._parse_json(content)
        assert result is not None
        assert result["document_type"] == "letter"
