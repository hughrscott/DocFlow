"""
Folder Router Service for DocFlow.

Analyzes existing folder structures, learns filing patterns, and intelligently
routes documents to appropriate folders. Can work with existing structures or
create new ones from scratch.
"""

import os
import logging
from typing import Dict, Any, Tuple, Optional, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class FolderRouter:
    """
    Intelligently routes documents to folders based on analysis results.
    
    Responsibilities:
    - Scan and analyze existing folder structure
    - Learn filing patterns from existing folders
    - Propose appropriate folder for new documents
    - Create new folder structures intelligently
    - Generate appropriate filenames
    """
    
    def __init__(self, root_documents_dir: str, max_depth: int = 4):
        """
        Initialize folder router with root directory.
        
        Args:
            root_documents_dir: Root directory for all documents (e.g., ~/Documents/MyDocuments)
            max_depth: Maximum folder depth to create (prevents creating too deep structures)
        """
        self.root_dir = Path(root_documents_dir)
        self.max_depth = max_depth
        
        # Ensure root directory exists
        self.root_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"FolderRouter initialized with root: {self.root_dir}")
        logger.info(f"Max folder depth: {max_depth}")
        
        # Cache for folder structure analysis
        self.folder_structure = None
        self.folder_patterns = {}
    
    def analyze_folder_structure(self) -> Dict[str, Any]:
        """
        Analyze existing folder structure and learn patterns.
        
        Scans the root directory recursively to understand:
        - Folder organization
        - Naming conventions
        - Document types in each folder
        - Depth of folder hierarchy
        
        Returns:
            Dictionary with folder analysis:
            {
                "total_folders": int,
                "max_depth": int,
                "folders": {
                    "Banking": {
                        "depth": 1,
                        "subfolders": ["Personal", "Business"],
                        "file_count": 5,
                        "patterns": {...}
                    }
                }
            }
        """
        try:
            logger.info(f"Analyzing folder structure at {self.root_dir}")
            
            folders_info = {}
            total_folders = 0
            max_depth_found = 0
            
            # Walk through directory
            for root, dirs, files in os.walk(self.root_dir):
                # Calculate depth
                depth = len(Path(root).relative_to(self.root_dir).parts)
                
                if depth > max_depth_found:
                    max_depth_found = depth
                
                # Get relative folder name
                rel_path = Path(root).relative_to(self.root_dir)
                folder_name = str(rel_path)
                
                if folder_name == '.':
                    continue  # Skip root level
                
                total_folders += 1
                
                # Extract patterns from folder names
                patterns = self._extract_folder_patterns(folder_name)
                
                folders_info[folder_name] = {
                    "depth": depth,
                    "subfolders": dirs,
                    "file_count": len(files),
                    "patterns": patterns
                }
                
                logger.debug(f"Found folder: {folder_name} (depth={depth}, files={len(files)})")
            
            structure = {
                "total_folders": total_folders,
                "max_depth": max_depth_found,
                "folders": folders_info
            }
            
            self.folder_structure = structure
            logger.info(f"Analysis complete: {total_folders} folders found")
            
            return structure
        
        except Exception as e:
            logger.error(f"Error analyzing folder structure: {e}")
            return {
                "total_folders": 0,
                "max_depth": 0,
                "folders": {}
            }
    
    def _extract_folder_patterns(self, folder_path: str) -> Dict[str, Any]:
        """
        Extract naming patterns from a folder path.
        
        Args:
            folder_path: Folder path (e.g., "Banking/Personal/PNC")
            
        Returns:
            Dictionary with pattern information
        """
        parts = folder_path.split('/')
        
        return {
            "parts": parts,
            "levels": len(parts),
            "top_level": parts[0] if parts else None,
            "keywords": [p.lower() for p in parts],
        }
    
    def propose_folder(
        self,
        analysis_result: Dict[str, Any],
        create_if_missing: bool = True
    ) -> Tuple[str, float]:
        """
        Propose a folder for a document based on analysis results.
        
        Uses document metadata to intelligently place it in the folder structure.
        Can create new folders if they don't exist.
        
        Args:
            analysis_result: Result from DocumentAnalyzer containing:
                - document_type: Type of document
                - institution: Organization name
                - date: Document date
                - account_type: Type of account
                - extracted_metadata: Full metadata
            create_if_missing: Whether to create folder if it doesn't exist
            
        Returns:
            Tuple of (folder_path, confidence_score)
            Example: ("Banking/Personal/PNC-Checking", 0.95)
        """
        try:
            doc_type = analysis_result.get("document_type", "unknown").lower()
            institution = analysis_result.get("institution", "Unknown").title()
            account_type = analysis_result.get("account_type", "").lower()
            
            logger.info(f"Proposing folder for: type={doc_type}, institution={institution}")
            
            # Build folder path based on document type
            folder_path = self._build_folder_path(
                doc_type=doc_type,
                institution=institution,
                account_type=account_type,
                metadata=analysis_result.get("extracted_metadata", {})
            )
            
            # Calculate confidence
            confidence = analysis_result.get("confidence_score", 0.5)
            
            # Create folder if requested
            if create_if_missing:
                self._create_folder_if_needed(folder_path)
            
            logger.info(f"Proposed folder: {folder_path} (confidence={confidence:.2f})")
            
            return folder_path, confidence
        
        except Exception as e:
            logger.error(f"Error proposing folder: {e}")
            # Default to generic folder on error
            return "Documents/Unorganized", 0.0
    
    def _build_folder_path(
        self,
        doc_type: str,
        institution: str,
        account_type: str,
        metadata: Dict[str, Any]
    ) -> str:
        """
        Build intelligent folder path based on document characteristics.
        
        Examples:
        - Bank statement → "Banking/Personal/PNC-Checking"
        - Tax bill → "Taxes/Property/Harris-County"
        - Insurance → "Insurance/Auto/State-Farm"
        - Utility bill → "Utilities/Electric/Oncor"
        
        Args:
            doc_type: Document type
            institution: Institution name
            account_type: Type of account
            metadata: Full extracted metadata
            
        Returns:
            Folder path as string
        """
        
        # Map document types to top-level categories
        category_map = {
            "bank_statement": "Banking",
            "credit_card_statement": "Banking",
            "mortgage_statement": "Banking",
            "loan_document": "Banking",
            
            "tax_bill": "Taxes",
            "property_tax": "Taxes",
            "income_tax": "Taxes",
            
            "utility_bill": "Utilities",
            "electric_bill": "Utilities",
            "gas_bill": "Utilities",
            "water_bill": "Utilities",
            
            "insurance_claim": "Insurance",
            "insurance_policy": "Insurance",
            "health_insurance": "Insurance",
            "auto_insurance": "Insurance",
            
            "medical_record": "Medical",
            "prescription": "Medical",
            "health_report": "Medical",
            
            "invoice": "Finance",
            "receipt": "Finance",
            "payment_record": "Finance",
        }
        
        # Get top-level category
        top_category = category_map.get(doc_type, "Documents")
        
        # Build sub-path based on account type and institution
        sub_paths = [top_category]
        
        # Add account type if available (e.g., "Personal", "Business")
        if account_type:
            sub_paths.append(account_type.title())
        
        # Add institution name (e.g., "PNC", "IRS")
        if institution and institution != "Unknown":
            # Clean institution name
            clean_institution = self._clean_institution_name(institution)
            sub_paths.append(clean_institution)
        
        # Combine into path
        folder_path = "/".join(sub_paths)
        
        # Limit depth
        parts = folder_path.split("/")
        if len(parts) > self.max_depth:
            parts = parts[:self.max_depth]
            folder_path = "/".join(parts)
        
        return folder_path
    
    def _clean_institution_name(self, institution: str) -> str:
        """
        Clean and standardize institution names.
        
        Args:
            institution: Raw institution name
            
        Returns:
            Cleaned name suitable for folder
        """
        # Remove common suffixes
        suffixes = ["Bank", "Inc", "LLC", "Corp", "Corporation", "Company"]
        name = institution.strip()
        
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
        
        # Keep only alphanumeric and spaces
        name = "".join(c if c.isalnum() or c.isspace() else "" for c in name)
        
        # Replace spaces with hyphens
        name = "-".join(name.split())
        
        return name
    
    def _create_folder_if_needed(self, folder_path: str) -> bool:
        """
        Create folder at specified path if it doesn't exist.
        
        Args:
            folder_path: Relative path from root (e.g., "Banking/Personal/PNC")
            
        Returns:
            True if created or already exists, False if error
        """
        try:
            full_path = self.root_dir / folder_path
            full_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Folder ready: {full_path}")
            return True
        
        except Exception as e:
            logger.error(f"Error creating folder {folder_path}: {e}")
            return False
    
    def generate_filename(
        self,
        analysis_result: Dict[str, Any],
        page_number: int = 1,
        extension: str = "pdf"
    ) -> str:
        """
        Generate intelligent filename based on document metadata.
        
        Examples:
        - "PNC-BankStatement-2025-01-Page1.pdf"
        - "IRS-IncomeTax-2024-Page1.pdf"
        - "StateFarm-AutoInsurance-Claim-2025-01-Page1.pdf"
        
        Args:
            analysis_result: Result from DocumentAnalyzer
            page_number: Page number if multi-page
            extension: File extension (default: pdf)
            
        Returns:
            Generated filename
        """
        try:
            parts = []
            
            # Start with institution
            institution = analysis_result.get("institution", "Document").title()
            clean_inst = self._clean_institution_name(institution)
            if clean_inst:
                parts.append(clean_inst)
            
            # Add document type
            doc_type = analysis_result.get("document_type", "document").lower()
            doc_type_clean = doc_type.replace("_", "-").title()
            parts.append(doc_type_clean)
            
            # Add date if available
            date_str = analysis_result.get("date")
            if date_str:
                parts.append(date_str)
            
            # Add account number if available (masked)
            account_masked = analysis_result.get("extracted_metadata", {}).get("account_number_masked")
            if account_masked:
                parts.append(account_masked)
            
            # Add page number if multi-page
            if page_number > 1:
                parts.append(f"Page{page_number}")
            
            # Join with hyphens and add extension
            filename = "-".join(str(p) for p in parts if p)
            filename = f"{filename}.{extension}" if extension else filename
            
            logger.debug(f"Generated filename: {filename}")
            return filename
        
        except Exception as e:
            logger.error(f"Error generating filename: {e}")
            # Fallback to generic name
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return f"Document-{timestamp}-Page{page_number}.{extension}"
    
    def get_folder_suggestions(self) -> List[Dict[str, Any]]:
        """
        Get a list of existing folders as suggestions for organization.
        
        Useful for UI to show user common filing locations.
        
        Returns:
            List of folder suggestions with metadata
        """
        if not self.folder_structure:
            self.analyze_folder_structure()
        
        suggestions = []
        
        for folder_path, info in self.folder_structure.get("folders", {}).items():
            suggestions.append({
                "path": folder_path,
                "depth": info["depth"],
                "file_count": info["file_count"],
                "subfolders": info["subfolders"]
            })
        
        return sorted(suggestions, key=lambda x: x["file_count"], reverse=True)
    
    def full_path_for_document(
        self,
        folder_path: str,
        filename: str
    ) -> str:
        """
        Get the full file path for a document.
        
        Args:
            folder_path: Relative folder path
            filename: Filename
            
        Returns:
            Full absolute path
        """
        return str(self.root_dir / folder_path / filename)
