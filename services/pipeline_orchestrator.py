"""
Pipeline Orchestrator for DocFlow v2.

Coordinates the full processing flow:
  1. Split PDF into logical sub-documents
  2. Classify each sub-document with rich metadata
  3. Propose filing locations using real directory tree
  4. Store results and set document status to awaiting_review
"""

import io
import logging
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from config.settings import settings
from database.models import (
    Document,
    Page,
    SubDocument,
    FilingProposal,
    LearningCorrection,
    PIIRedactionLog,
)
from llm.llm_manager import LLMManager
from services.pdf_processor import PDFProcessor
from services.document_splitter import DocumentSplitter
from services.rich_classifier import RichClassifier
from services.smart_filer import SmartFiler
from services.pii_protector import should_redact, redact_text

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Runs the v2 pipeline: split → classify → propose → store."""

    def __init__(self, llm_manager: LLMManager, db: Session):
        self.llm = llm_manager
        self.db = db
        self.pdf = PDFProcessor(temp_dir=settings.uploads_temp_dir)
        self.splitter = DocumentSplitter(llm_manager)
        self.classifier = RichClassifier(llm_manager)
        self.filer = SmartFiler(llm_manager)

    # ── Public API ────────────────────────────────────────────────────

    async def process_document(self, document_id: str, pdf_data: bytes) -> Dict[str, Any]:
        """
        Run the full v2 pipeline on *pdf_data* for document *document_id*.

        Returns a summary dict with sub_document_count and status.
        """
        doc = self.db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ValueError(f"Document {document_id} not found")

        provider_name = self.llm.get_provider_name()

        # Load recent learning corrections
        corrections = self._load_recent_corrections()

        try:
            # ── Phase 1: Split ────────────────────────────────────
            self._update_status(doc, "splitting")

            page_images = await self._convert_all_pages(pdf_data)
            boundaries = await self.splitter.split_document(pdf_data, page_images)

            doc.splitting_done = True
            doc.sub_document_count = len(boundaries)
            self.db.commit()

            # ── Phase 2: Classify each sub-document ───────────────
            self._update_status(doc, "classifying")

            sub_docs: List[SubDocument] = []
            for boundary in boundaries:
                sub_doc = await self._classify_sub_document(
                    document_id=document_id,
                    pdf_data=pdf_data,
                    page_images=page_images,
                    boundary=boundary,
                    provider_name=provider_name,
                    corrections=corrections,
                )
                sub_docs.append(sub_doc)

            doc.classification_done = True
            self.db.commit()

            # ── Phase 3: Propose filing ───────────────────────────
            self._update_status(doc, "proposing")

            for sub_doc in sub_docs:
                await self._propose_filing(sub_doc, corrections)

            doc.filing_proposed = True
            self.db.commit()

            # ── Done: awaiting review ─────────────────────────────
            self._update_status(doc, "awaiting_review")

            return {
                "document_id": document_id,
                "sub_document_count": len(sub_docs),
                "status": "awaiting_review",
            }

        except Exception as e:
            logger.error(f"Pipeline failed for document {document_id}: {e}", exc_info=True)
            doc.status = "failed"
            doc.error_message = str(e)[:500]
            self.db.commit()
            raise

    # ── Internal steps ────────────────────────────────────────────────

    async def _convert_all_pages(self, pdf_data: bytes) -> List[bytes]:
        """Convert every page of the PDF to a PNG thumbnail."""
        images: List[bytes] = []
        try:
            page_pdfs = await self.pdf.split_pdf(pdf_data)
            for page_pdf in page_pdfs:
                try:
                    img = await self.pdf.pdf_page_to_image(page_pdf, dpi=settings.split_thumbnail_dpi)
                    images.append(img)
                except Exception as e:
                    logger.warning(f"Failed to convert page to image: {e}")
                    images.append(b"")
        except Exception as e:
            logger.warning(f"Failed to split PDF for image conversion: {e}")
        return images

    async def _classify_sub_document(
        self,
        document_id: str,
        pdf_data: bytes,
        page_images: List[bytes],
        boundary,
        provider_name: str,
        corrections: List[Dict[str, Any]],
    ) -> SubDocument:
        """Classify a single sub-document and persist it."""
        # Extract sub-document pages
        sub_images = []
        for i in range(boundary.start_page - 1, boundary.end_page):
            if i < len(page_images) and page_images[i]:
                sub_images.append(page_images[i])

        # Extract text for PII-safe context
        extracted_text = self._extract_text_range(pdf_data, boundary.start_page, boundary.end_page)

        # Log PII redactions if applicable
        if should_redact(provider_name) and extracted_text:
            _, redaction_records = redact_text(extracted_text)
            for rec in redaction_records:
                self.db.add(PIIRedactionLog(
                    document_id=document_id,
                    page_number=boundary.start_page,
                    redaction_type=rec["redaction_type"],
                    provider=provider_name,
                ))

        # Classify
        result = await self.classifier.classify(
            page_images=sub_images,
            extracted_text=extracted_text,
            learning_corrections=corrections,
        )

        # Create SubDocument record
        sub_doc = SubDocument(
            document_id=document_id,
            start_page=boundary.start_page,
            end_page=boundary.end_page,
            page_count=boundary.end_page - boundary.start_page + 1,
            document_type=result.get("document_type"),
            document_category=result.get("document_category"),
            institution=result.get("institution"),
            classification_metadata=result.get("classification_metadata"),
            confidence_score=result.get("confidence"),
            split_rationale=boundary.rationale,
            llm_provider_used=result.get("provider", provider_name),
            llm_model_used=result.get("model", ""),
            status="proposed",
        )
        self.db.add(sub_doc)
        self.db.commit()

        # Link pages to this sub-document
        pages = (
            self.db.query(Page)
            .filter(
                Page.document_id == document_id,
                Page.page_number >= boundary.start_page,
                Page.page_number <= boundary.end_page,
            )
            .all()
        )
        for page in pages:
            page.sub_document_id = sub_doc.id
        self.db.commit()

        return sub_doc

    async def _propose_filing(
        self,
        sub_doc: SubDocument,
        corrections: List[Dict[str, Any]],
    ) -> FilingProposal:
        """Propose a filing location for a sub-document."""
        metadata = {
            "document_type": sub_doc.document_type,
            "document_category": sub_doc.document_category,
            "institution": sub_doc.institution,
            "classification_metadata": sub_doc.classification_metadata or {},
        }

        proposal_data = await self.filer.propose_filing(metadata, corrections)

        # Update SubDocument with proposal
        sub_doc.proposed_folder = proposal_data.get("proposed_folder")
        sub_doc.proposed_filename = proposal_data.get("proposed_filename")
        sub_doc.filing_rationale = proposal_data.get("rationale")

        # Create FilingProposal record
        proposal = FilingProposal(
            sub_document_id=sub_doc.id,
            proposed_folder=proposal_data.get("proposed_folder", ""),
            proposed_filename=proposal_data.get("proposed_filename", ""),
            is_new_folder=proposal_data.get("is_new_folder", False),
            rationale=proposal_data.get("rationale"),
            alternatives_considered=proposal_data.get("alternatives"),
            confidence=proposal_data.get("confidence"),
        )
        self.db.add(proposal)
        self.db.commit()

        return proposal

    # ── Helpers ───────────────────────────────────────────────────────

    def _update_status(self, doc: Document, status: str) -> None:
        doc.status = status
        self.db.commit()
        logger.info(f"Document {doc.id} status → {status}")

    def _extract_text_range(self, pdf_data: bytes, start_page: int, end_page: int) -> str:
        """Extract text from a page range using pypdf."""
        from PyPDF2 import PdfReader
        try:
            reader = PdfReader(io.BytesIO(pdf_data))
            texts = []
            for i in range(start_page - 1, min(end_page, len(reader.pages))):
                text = reader.pages[i].extract_text() or ""
                texts.append(text)
            return "\n\n".join(texts)
        except Exception as e:
            logger.warning(f"Text extraction failed for pages {start_page}-{end_page}: {e}")
            return ""

    def _load_recent_corrections(self) -> List[Dict[str, Any]]:
        """Load recent learning corrections for prompt injection."""
        try:
            corrections = (
                self.db.query(LearningCorrection)
                .order_by(LearningCorrection.created_at.desc())
                .limit(settings.max_learning_corrections_in_prompt * 2)
                .all()
            )
            return [
                {
                    "correction_type": c.correction_type,
                    "proposed_document_type": c.proposed_document_type,
                    "actual_document_type": c.actual_document_type,
                    "proposed_folder": c.proposed_folder,
                    "actual_folder": c.actual_folder,
                    "proposed_filename": c.proposed_filename,
                    "actual_filename": c.actual_filename,
                    "user_rationale": c.user_rationale,
                    "key_signals": c.key_signals,
                }
                for c in corrections
            ]
        except Exception as e:
            logger.warning(f"Failed to load learning corrections: {e}")
            return []
