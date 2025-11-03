"""
Document Analyzer Service for DocFlow.

Uses LLM to analyze document images, extract metadata, and classify document types.
This is the core intelligence engine of DocFlow.
"""

import json
import logging
from typing import Dict, Any, Optional
from llm.llm_manager import LLMManager
from llm.base import LLMResponse

logger = logging.getLogger(__name__)


class DocumentAnalyzer:
    """
    Analyzes document pages using AI to extract meaningful information.
    
    Responsibilities:
    - Send document images to LLM for analysis
    - Extract structured metadata from LLM responses
    - Calculate confidence scores
    - Handle analysis errors gracefully
    """
    
    def __init__(self, llm_manager: LLMManager, confidence_threshold: float = 0.7):
        """
        Initialize document analyzer with LLM manager.
        
        Args:
            llm_manager: LLMManager instance for provider selection
            confidence_threshold: Minimum confidence to accept classification (0-1)
        """
        self.llm_manager = llm_manager
        self.confidence_threshold = confidence_threshold
        logger.info(f"DocumentAnalyzer initialized with threshold: {confidence_threshold}")
    
    async def analyze_page(self, image_data: bytes, page_number: int = 1) -> Dict[str, Any]:
        """
        Analyze a single document page image.
        
        Args:
            image_data: Image bytes (PNG, JPEG, etc.)
            page_number: Page number for reference
            
        Returns:
            Dictionary containing:
            {
                "success": bool,
                "document_type": str,
                "institution": str,
                "date": str,
                "confidence_score": float,
                "extracted_metadata": dict,
                "provider_used": str,
                "model_used": str,
                "error": str or None
            }
        """
        try:
            logger.info(f"Starting analysis of page {page_number}")
            
            # Create analysis prompt
            analysis_prompt = self._create_analysis_prompt()
            
            # Send to LLM for analysis
            logger.debug(f"Sending page {page_number} image to LLM")
            llm_response = await self.llm_manager.analyze_image(
                image_data=image_data,
                prompt=analysis_prompt,
                max_tokens=1500
            )
            
            if not llm_response.success:
                error_msg = f"LLM analysis failed: {llm_response.error}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "document_type": "unknown",
                    "institution": None,
                    "date": None,
                    "confidence_score": 0.0,
                    "extracted_metadata": {},
                    "provider_used": llm_response.provider.value,
                    "model_used": llm_response.model,
                }
            
            # Parse LLM response
            logger.debug(f"Parsing LLM response for page {page_number}")
            extracted_data = self._parse_llm_response(llm_response.content)
            # Normalize to legacy-compatible fields
            normalized = self._normalize_extraction(extracted_data)
            
            # Calculate confidence
            confidence = self._calculate_confidence(normalized)
            
            logger.info(
                f"Page {page_number} analysis complete: "
                f"type={extracted_data.get('document_type')}, "
                f"confidence={confidence:.2f}"
            )
            
            return {
                "success": True,
                "error": None,
                "document_type": normalized.get("document_type", "unknown"),
                "institution": normalized.get("institution"),
                "date": normalized.get("date"),
                "confidence_score": confidence,
                "extracted_metadata": normalized,
                "provider_used": llm_response.provider.value,
                "model_used": llm_response.model,
            }
        
        except Exception as e:
            error_msg = f"Unexpected error analyzing page {page_number}: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "document_type": "unknown",
                "institution": None,
                "date": None,
                "confidence_score": 0.0,
                "extracted_metadata": {},
                "provider_used": "unknown",
                "model_used": "unknown",
            }
    
    def _create_analysis_prompt(self) -> str:
        """
        Create the prompt for document analysis using a structured schema.
        """
        prompt = (
            "You are an expert document classifier and data extractor. "
            "Analyze this document image and return ONLY valid JSON matching the schema below. No prose.\n\n"
            "Schema (all fields optional unless implied):\n"
            "{\n"
            "  \"issuer\": { \"name\": string|null, \"confidence\": number },\n"
            "  \"recipient\": { \"name\": string|null, \"confidence\": number },\n"
            "  \"document_type\": { \"value\": string, \"confidence\": number },\n"
            "  \"document_subtype\": string|null,\n"
            "  \"period\": { \"start_date\": \"YYYY-MM-DD\"|null, \"end_date\": \"YYYY-MM-DD\"|null, \"month\": \"YYYY-MM\"|null },\n"
            "  \"identifiers\": { \"account_last4\": string|null, \"policy\": string|null, \"invoice\": string|null, \"statement_id\": string|null },\n"
            "  \"amounts\": { \"total_due\": number|null, \"balance\": number|null, \"payment\": number|null, \"currency\": string|null },\n"
            "  \"addressed_to\": string|null,\n"
            "  \"is_continuation_of_previous\": { \"value\": boolean, \"confidence\": number, \"rationale\": string },\n"
            "  \"page_markers\": { \"page_number_text\": string|null, \"total_pages_text\": string|null },\n"
            "  \"layout_fingerprints\": { \"fonts\": string[], \"header_signature\": string|null, \"footer_signature\": string|null },\n"
            "  \"proposed_filename\": string,\n"
            "  \"hints\": string[]\n"
            "}\n\n"
            "Instructions:\n"
            "- Identify issuer and recipient.\n"
            "- Classify document_type using taxonomy: banking(bank_statement,credit_card_statement,mortgage_statement,loan_document), taxes(tax_bill,property_tax,income_tax), utilities(electric_bill,gas_bill,water_bill,internet_bill,mobile_bill), insurance(insurance_policy,insurance_claim,auto_insurance,health_insurance), medical(medical_record,prescription,health_report), employment(paystub,employment_contract), finance(invoice,receipt,payment_record), legal(affidavit,legal_notice,court_document), other(other,unknown).\n"
            "- Pay special attention to legal documents like AFFIDAVIT/NOTARY pages (keywords: 'Affidavit', 'Sworn', 'State of', 'County of', 'Subscribed and sworn', 'Notary').\n"
            "- Identify business names including d/b/a patterns (e.g., 'School of Rock d/b/a Flagstore LLC').\n"
            "- Extract period (month or start/end).\n"
            "- Include identifiers like account_last4/policy/invoice.\n"
            "- Indicate if this page likely continues the previous page using page markers, layout similarity, and identifiers.\n"
            "- Suggest a filename like <Institution>-<Type>-<YYYY-MM or range>-<Last4?>-PageN.pdf\n\n"
            "Return only JSON."
        )
        return prompt
    
    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse JSON response from LLM.
        
        Args:
            response_text: Raw text response from LLM
            
        Returns:
            Parsed dictionary of extracted data
        """
        try:
            # Try to extract JSON from response
            # LLM might include extra text, so we look for JSON object
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start == -1 or json_end == 0:
                logger.warning("No JSON found in LLM response")
                return self._default_extraction()
            
            json_str = response_text[json_start:json_end]
            extracted = json.loads(json_str)
            
            logger.debug("Successfully parsed LLM JSON response")
            return extracted
        
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            logger.debug(f"Response text: {response_text[:200]}")
            return self._default_extraction()
        
        except Exception as e:
            logger.error(f"Unexpected error parsing LLM response: {e}")
            return self._default_extraction()
    
    def _default_extraction(self) -> Dict[str, Any]:
        """
        Return default/empty extraction when parsing fails.
        
        Returns:
            Default extraction dictionary
        """
        return {
            "document_type": "unknown",
            "institution": None,
            "date": None,
            "account_type": None,
            "account_holder": None,
            "account_number_masked": None,
            "amount": None,
            "document_category": None,
            "key_identifiers": None,
            "confidence_indicators": None,
            "is_multipage_indicator": None,
        }
    
    def _calculate_confidence(self, extracted_data: Dict[str, Any]) -> float:
        """
        Calculate confidence score for the analysis.
        
        Scores based on:
        - How specific the document type is
        - Whether institution was identified
        - Whether date was found
        - Quality of extracted metadata
        
        Args:
            extracted_data: Dictionary of extracted data
            
        Returns:
            Confidence score between 0 and 1
        """
        score = 0.5  # Start at 50% baseline
        
        # Document type clarity
        doc_type = extracted_data.get("document_type", "").lower()
        if doc_type and doc_type != "unknown" and doc_type != "other":
            score += 0.2  # +20% for clear document type
        
        # Institution identified
        if extracted_data.get("institution"):
            score += 0.15  # +15% for institution
        
        # Date found
        if extracted_data.get("date"):
            score += 0.1  # +10% for date
        
        # Account information
        if extracted_data.get("account_type") or extracted_data.get("account_number_masked"):
            score += 0.05  # +5% for account info
        
        # Amount found
        if extracted_data.get("amount"):
            score += 0.05  # +5% for amount
        
        # Cap at 1.0
        confidence = min(score, 1.0)
        
        logger.debug(f"Calculated confidence score: {confidence:.2f}")
        return confidence
    
    def is_confident_enough(self, confidence_score: float) -> bool:
        """
        Check if confidence score meets threshold.
        
        Args:
            confidence_score: Confidence score to check
            
        Returns:
            True if score >= threshold, False otherwise
        """
        return confidence_score >= self.confidence_threshold
    
    async def analyze_multiple_pages(
        self,
        page_images: list
    ) -> list:
        """
        Analyze multiple document pages.
        
        Args:
            page_images: List of (page_number, image_bytes) tuples
            
        Returns:
            List of analysis results
        """
        results = []
        
        logger.info(f"Starting analysis of {len(page_images)} pages")
        
        for page_number, image_data in page_images:
            result = await self.analyze_page(image_data, page_number)
            results.append(result)
        
        logger.info(f"Completed analysis of {len(page_images)} pages")
        return results

    def _normalize_extraction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize extracted fields to the legacy keys the app expects while
        preserving the richer structured values from the prompt.
        """
        out: Dict[str, Any] = {}
        try:
            src = data or {}
            # Start with original structure
            if isinstance(src, dict):
                out.update(src)

            # institution from issuer.name
            issuer = src.get("issuer") if isinstance(src, dict) else None
            institution = None
            if isinstance(issuer, dict):
                institution = issuer.get("name")
            out["institution"] = institution

            # date from period (month -> end_date -> start_date)
            period = src.get("period") if isinstance(src, dict) else None
            date_val = None
            if isinstance(period, dict):
                date_val = period.get("month") or period.get("end_date") or period.get("start_date")
            out["date"] = date_val

            # document_type from nested value if present
            doc_t = src.get("document_type") if isinstance(src, dict) else None
            if isinstance(doc_t, dict):
                out["document_type"] = (doc_t.get("value") or "unknown").lower()
            else:
                out["document_type"] = (doc_t or "unknown").lower()

            # identifiers.account_last4 -> account_number_masked
            ids = src.get("identifiers") if isinstance(src, dict) else None
            if isinstance(ids, dict):
                last4 = ids.get("account_last4")
                if last4 and isinstance(last4, str):
                    out["account_number_masked"] = ("****" + last4[-4:])

            # amounts: single value for scoring convenience
            amts = src.get("amounts") if isinstance(src, dict) else None
            if isinstance(amts, dict):
                out["amount"] = amts.get("total_due") or amts.get("balance") or amts.get("payment")

            return out
        except Exception:
            return data or {}
