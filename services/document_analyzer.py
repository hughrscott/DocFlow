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
            
            # Calculate confidence
            confidence = self._calculate_confidence(extracted_data)
            
            logger.info(
                f"Page {page_number} analysis complete: "
                f"type={extracted_data.get('document_type')}, "
                f"confidence={confidence:.2f}"
            )
            
            return {
                "success": True,
                "error": None,
                "document_type": extracted_data.get("document_type", "unknown"),
                "institution": extracted_data.get("institution"),
                "date": extracted_data.get("date"),
                "confidence_score": confidence,
                "extracted_metadata": extracted_data,
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
        Create the prompt for document analysis.
        
        This prompt instructs the LLM on what to extract from the document.
        
        Returns:
            Analysis prompt string
        """
        prompt = """You are an expert document classifier and data extractor. Analyze this document image carefully and extract the following information in JSON format:

{
    "document_type": "Type of document (e.g., 'bank_statement', 'tax_bill', 'utility_bill', 'insurance_claim', 'invoice', 'medical_record', 'mortgage_statement', 'property_tax', 'credit_card_statement', 'loan_document', 'utility_bill', 'other')",
    "institution": "Name of the organization/institution that issued this document (e.g., 'PNC Bank', 'IRS', 'Harris County Tax Office', 'Blue Cross Insurance')",
    "date": "Date or date range of the document in YYYY-MM format (e.g., '2025-01', '2024-12')",
    "account_type": "Type of account if applicable (e.g., 'checking', 'savings', 'credit_card', 'mortgage', 'personal')",
    "account_holder": "Name of the account holder if visible",
    "account_number_masked": "Last 4 digits of account number if visible (e.g., '****1234')",
    "amount": "Primary amount on document if applicable (e.g., balance, total due)",
    "document_category": "Broad category (e.g., 'Financial', 'Government', 'Insurance', 'Medical', 'Utility', 'Legal')",
    "key_identifiers": "Any identifying information (reference numbers, case numbers, policy numbers, etc.)",
    "confidence_indicators": "What clues helped you classify this document?",
    "is_multipage_indicator": "Does this document appear to be part of a multi-page document?"
}

Be thorough in extraction. If information is not visible or unclear, use null. Return ONLY valid JSON, no other text."""
        
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
