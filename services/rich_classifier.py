"""
Rich Classifier for DocFlow Pipeline v2.

Two-phase classification:
  1. Send first-page image → identify document type
  2. Select type-specific prompt → extract deep, structured metadata

Supports learning injection: recent corrections from the
``learning_corrections`` table are included as in-context examples.
"""

import json
import logging
from typing import Dict, Any, List, Optional

from llm.llm_manager import LLMManager
from llm.base import LLMResponse
from services.pii_protector import should_redact, redact_text
from config.settings import settings

logger = logging.getLogger(__name__)


# ── Type-specific prompt schemas ──────────────────────────────────────────

TYPE_SCHEMAS: Dict[str, str] = {
    "bank_statement": """{
  "bank_name": "<string>",
  "account_type": "<checking|savings|money_market|cd|other>",
  "account_last4": "<last 4 digits>",
  "statement_period": "<YYYY-MM or date range>",
  "opening_balance": "<number or null>",
  "closing_balance": "<number or null>",
  "total_deposits": "<number or null>",
  "total_withdrawals": "<number or null>"
}""",
    "letter": """{
  "sender": "<string>",
  "sender_type": "<company|government|individual|unknown>",
  "recipient": "<string or null>",
  "subject": "<brief subject>",
  "date": "<YYYY-MM-DD or null>",
  "reason": "<brief reason/purpose>",
  "action_required": <true|false>
}""",
    "invoice": """{
  "vendor": "<string>",
  "invoice_number": "<string or null>",
  "date": "<YYYY-MM-DD or null>",
  "due_date": "<YYYY-MM-DD or null>",
  "line_items_summary": "<brief summary of items>",
  "subtotal": "<number or null>",
  "tax": "<number or null>",
  "total": "<number or null>"
}""",
    "tax_form": """{
  "form_type": "<W-2|1099|1040|other>",
  "tax_year": "<YYYY>",
  "employer_or_payer": "<string or null>",
  "total_income": "<number or null>",
  "tax_withheld": "<number or null>"
}""",
    "utility_bill": """{
  "utility_type": "<electric|gas|water|internet|phone|other>",
  "provider": "<string>",
  "service_address": "<string or null>",
  "billing_period": "<date range or month>",
  "amount_due": "<number or null>",
  "due_date": "<YYYY-MM-DD or null>"
}""",
    "legal_document": """{
  "case_number": "<string or null>",
  "court": "<string or null>",
  "parties": "<brief description>",
  "document_subtype": "<contract|court_filing|agreement|other>"
}""",
    "insurance_document": """{
  "policy_number": "<string or null>",
  "insurer": "<string>",
  "coverage_type": "<auto|home|health|life|other>",
  "premium": "<number or null>",
  "effective_date": "<YYYY-MM-DD or null>"
}""",
    "medical_document": """{
  "provider": "<string>",
  "patient": "<string or null>",
  "visit_date": "<YYYY-MM-DD or null>",
  "document_subtype": "<lab_results|eob|bill|records|other>"
}""",
    "generic": """{
  "title": "<string>",
  "author_or_source": "<string or null>",
  "date": "<YYYY-MM-DD or null>",
  "summary": "<1-2 sentence summary>"
}""",
}

# Map common LLM responses to canonical type names
TYPE_ALIASES: Dict[str, str] = {
    "bank statement": "bank_statement",
    "credit card statement": "bank_statement",
    "mortgage statement": "bank_statement",
    "invoice": "invoice",
    "bill": "invoice",
    "receipt": "invoice",
    "letter": "letter",
    "correspondence": "letter",
    "tax form": "tax_form",
    "w-2": "tax_form",
    "w2": "tax_form",
    "1099": "tax_form",
    "1040": "tax_form",
    "utility bill": "utility_bill",
    "electric bill": "utility_bill",
    "gas bill": "utility_bill",
    "water bill": "utility_bill",
    "internet bill": "utility_bill",
    "phone bill": "utility_bill",
    "legal": "legal_document",
    "contract": "legal_document",
    "court": "legal_document",
    "insurance": "insurance_document",
    "policy": "insurance_document",
    "medical": "medical_document",
    "lab results": "medical_document",
    "eob": "medical_document",
}


class RichClassifier:
    """Two-phase document classifier with type-specific metadata extraction."""

    def __init__(self, llm_manager: LLMManager):
        self.llm = llm_manager

    # ── Public API ────────────────────────────────────────────────────

    async def classify(
        self,
        page_images: List[bytes],
        extracted_text: str = "",
        learning_corrections: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Classify a sub-document and extract rich metadata.

        Args:
            page_images: PNG images of the sub-document's pages
            extracted_text: Pre-extracted text (used for PII-redacted context)
            learning_corrections: Recent relevant corrections to inject

        Returns:
            dict with keys: document_type, document_category, institution,
            confidence, classification_metadata, provider, model
        """
        if not page_images:
            return self._empty_result("no page images provided")

        provider_name = self.llm.get_provider_name()

        # Phase 1: Identify document type from first page
        doc_type, phase1_confidence = await self._phase1_identify_type(
            page_images[0], extracted_text, provider_name, learning_corrections
        )

        # Phase 2: Extract type-specific metadata
        metadata = await self._phase2_extract_metadata(
            doc_type, page_images, extracted_text, provider_name, learning_corrections
        )

        # Determine institution from metadata
        institution = self._extract_institution(doc_type, metadata)

        return {
            "document_type": doc_type,
            "document_category": self._category_for_type(doc_type),
            "institution": institution,
            "confidence": phase1_confidence,
            "classification_metadata": metadata,
            "provider": provider_name,
            "model": "",  # filled by caller from LLM response
        }

    # ── Phase 1: Type identification ──────────────────────────────────

    async def _phase1_identify_type(
        self,
        first_page_image: bytes,
        extracted_text: str,
        provider_name: str,
        corrections: Optional[List[Dict[str, Any]]],
    ) -> tuple:
        """Identify the document type from the first page."""
        learning_context = self._inject_learning_context(corrections, "classification")

        prompt = f"""Identify the type of this document.

{learning_context}

Respond with ONLY a JSON object:
{{
  "document_type": "<one of: bank_statement, letter, invoice, tax_form, utility_bill, legal_document, insurance_document, medical_document, generic>",
  "confidence": <float 0.0-1.0>,
  "brief_reason": "<why you chose this type>"
}}

Return valid JSON only."""

        response = await self.llm.analyze_image(first_page_image, prompt, max_tokens=500)

        if not response.success:
            # Fallback: try text if available
            if extracted_text:
                text_to_send = extracted_text[:3000]
                if should_redact(provider_name):
                    text_to_send, _ = redact_text(text_to_send)
                response = await self.llm.process_text(text_to_send, prompt, max_tokens=500)

        if response.success:
            parsed = self._parse_json(response.content)
            if parsed:
                raw_type = str(parsed.get("document_type", "generic")).lower().strip()
                doc_type = TYPE_ALIASES.get(raw_type, raw_type)
                if doc_type not in TYPE_SCHEMAS:
                    doc_type = "generic"
                confidence = float(parsed.get("confidence", 0.5))
                return doc_type, confidence

        return "generic", 0.3

    # ── Phase 2: Deep metadata extraction ─────────────────────────────

    async def _phase2_extract_metadata(
        self,
        doc_type: str,
        page_images: List[bytes],
        extracted_text: str,
        provider_name: str,
        corrections: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Extract type-specific metadata using the schema for *doc_type*."""
        schema = TYPE_SCHEMAS.get(doc_type, TYPE_SCHEMAS["generic"])
        learning_context = self._inject_learning_context(corrections, doc_type)

        prompt = f"""This document is a **{doc_type.replace('_', ' ')}**.

{learning_context}

Extract the following metadata from the document pages.
Return ONLY a JSON object matching this schema:
{schema}

If a field cannot be determined, use null.  Return valid JSON only."""

        # Use representative pages (first, middle, last)
        representative = self._pick_representative_pages(page_images)

        if len(representative) > 1 and provider_name in ("claude",):
            # Multi-image call
            images_with_labels = [
                (img, f"[Page {i + 1} of {len(page_images)}]")
                for i, img in enumerate(page_images)
                if img in representative
            ]
            # Re-label properly
            images_with_labels = []
            indices = self._representative_indices(len(page_images))
            for idx in indices:
                images_with_labels.append((page_images[idx], f"[Page {idx + 1}]"))

            response = await self.llm.analyze_images(images_with_labels, prompt, max_tokens=2000)
        else:
            # Single image (first page)
            response = await self.llm.analyze_image(representative[0], prompt, max_tokens=2000)

        if not response.success and extracted_text:
            text_to_send = extracted_text[:4000]
            if should_redact(provider_name):
                text_to_send, _ = redact_text(text_to_send)
            response = await self.llm.process_text(text_to_send, prompt, max_tokens=2000)

        if response.success:
            parsed = self._parse_json(response.content)
            if parsed:
                return parsed

        return {}

    # ── Learning injection ────────────────────────────────────────────

    def _inject_learning_context(
        self,
        corrections: Optional[List[Dict[str, Any]]],
        context_type: str,
    ) -> str:
        """Build a prompt fragment from recent corrections."""
        if not corrections:
            return ""

        relevant = [
            c for c in corrections
            if c.get("correction_type") == context_type
            or c.get("actual_document_type") == context_type
            or context_type == "classification"
        ][:settings.max_learning_corrections_in_prompt]

        if not relevant:
            return ""

        lines = ["PAST CORRECTIONS (follow these patterns):"]
        for c in relevant:
            inst = c.get("institution", "document")
            proposed = c.get("proposed_document_type") or c.get("proposed_folder") or "?"
            actual = c.get("actual_document_type") or c.get("actual_folder") or "?"
            reason = c.get("user_rationale", "")
            line = f"- For {inst}: proposed '{proposed}' -> user chose '{actual}'"
            if reason:
                line += f"\n  (reason: {reason})"
            lines.append(line)

        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────

    def _pick_representative_pages(self, images: List[bytes]) -> List[bytes]:
        """Pick first, middle, last pages for analysis."""
        if len(images) <= 3:
            return images
        indices = self._representative_indices(len(images))
        return [images[i] for i in indices]

    @staticmethod
    def _representative_indices(total: int) -> List[int]:
        if total <= 3:
            return list(range(total))
        return [0, total // 2, total - 1]

    @staticmethod
    def _parse_json(content: str) -> Optional[Dict[str, Any]]:
        content = content.strip()
        # Find JSON object
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_institution(doc_type: str, metadata: Dict[str, Any]) -> Optional[str]:
        """Pull institution name from type-specific metadata."""
        candidates = [
            metadata.get("bank_name"),
            metadata.get("vendor"),
            metadata.get("provider"),
            metadata.get("insurer"),
            metadata.get("sender"),
            metadata.get("employer_or_payer"),
            metadata.get("court"),
            metadata.get("author_or_source"),
        ]
        for c in candidates:
            if c and str(c).lower() not in ("null", "none", ""):
                return str(c)
        return None

    @staticmethod
    def _category_for_type(doc_type: str) -> str:
        mapping = {
            "bank_statement": "Banking",
            "letter": "Correspondence",
            "invoice": "Finance",
            "tax_form": "Taxes",
            "utility_bill": "Utilities",
            "legal_document": "Legal",
            "insurance_document": "Insurance",
            "medical_document": "Medical",
            "generic": "Documents",
        }
        return mapping.get(doc_type, "Documents")

    @staticmethod
    def _empty_result(reason: str) -> Dict[str, Any]:
        return {
            "document_type": "generic",
            "document_category": "Documents",
            "institution": None,
            "confidence": 0.0,
            "classification_metadata": {},
            "provider": "",
            "model": "",
            "error": reason,
        }
