"""
Proposals API for DocFlow Pipeline v2.

Provides endpoints for the interactive accept/modify/reject workflow
for sub-document filing proposals.
"""

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.settings import settings
from database.database import get_db
from database.models import (
    Document,
    SubDocument,
    FilingProposal,
    LearningCorrection,
    Page,
)
from services.file_manager import FileManager
from services.document_splitter import DocumentSplitter
from services.pdf_processor import PDFProcessor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])


# ── Response models ───────────────────────────────────────────────────

class SubDocumentResponse(BaseModel):
    id: str
    document_id: str
    start_page: int
    end_page: int
    page_count: int
    document_type: Optional[str] = None
    document_category: Optional[str] = None
    institution: Optional[str] = None
    classification_metadata: Optional[Dict[str, Any]] = None
    confidence_score: Optional[float] = None
    split_rationale: Optional[str] = None
    proposed_folder: Optional[str] = None
    proposed_filename: Optional[str] = None
    filing_rationale: Optional[str] = None
    final_folder: Optional[str] = None
    final_filename: Optional[str] = None
    status: str
    llm_provider_used: Optional[str] = None


class FilingProposalResponse(BaseModel):
    id: str
    sub_document_id: str
    proposed_folder: str
    proposed_filename: str
    is_new_folder: bool
    rationale: Optional[str] = None
    alternatives_considered: Optional[list] = None
    confidence: Optional[float] = None
    decision: Optional[str] = None
    user_folder: Optional[str] = None
    user_filename: Optional[str] = None


class DocumentProposalsResponse(BaseModel):
    document_id: str
    status: str
    sub_documents: List[SubDocumentResponse]
    proposals: List[FilingProposalResponse]


class ModifyRequest(BaseModel):
    folder: Optional[str] = None
    filename: Optional[str] = None
    document_type: Optional[str] = None
    rationale: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str


class AcceptAllRequest(BaseModel):
    sub_document_ids: Optional[List[str]] = None


class AcceptAllResponse(BaseModel):
    accepted: int
    errors: List[str]


class ReclassifyResponse(BaseModel):
    sub_document_id: str
    document_type: Optional[str] = None
    institution: Optional[str] = None
    confidence_score: Optional[float] = None
    classification_metadata: Optional[Dict[str, Any]] = None


class RefileResponse(BaseModel):
    sub_document_id: str
    proposed_folder: Optional[str] = None
    proposed_filename: Optional[str] = None
    rationale: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/{document_id}", response_model=DocumentProposalsResponse)
async def get_proposals(document_id: str, db: Session = Depends(get_db)):
    """Get all sub-documents and their filing proposals for a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    sub_docs = (
        db.query(SubDocument)
        .filter(SubDocument.document_id == document_id)
        .order_by(SubDocument.start_page.asc())
        .all()
    )

    sub_doc_ids = [sd.id for sd in sub_docs]
    proposals = (
        db.query(FilingProposal)
        .filter(FilingProposal.sub_document_id.in_(sub_doc_ids))
        .all()
    ) if sub_doc_ids else []

    return DocumentProposalsResponse(
        document_id=document_id,
        status=doc.status,
        sub_documents=[
            SubDocumentResponse(
                id=sd.id,
                document_id=sd.document_id,
                start_page=sd.start_page,
                end_page=sd.end_page,
                page_count=sd.page_count,
                document_type=sd.document_type,
                document_category=sd.document_category,
                institution=sd.institution,
                classification_metadata=sd.classification_metadata,
                confidence_score=sd.confidence_score,
                split_rationale=sd.split_rationale,
                proposed_folder=sd.proposed_folder,
                proposed_filename=sd.proposed_filename,
                filing_rationale=sd.filing_rationale,
                final_folder=sd.final_folder,
                final_filename=sd.final_filename,
                status=sd.status,
                llm_provider_used=sd.llm_provider_used,
            )
            for sd in sub_docs
        ],
        proposals=[
            FilingProposalResponse(
                id=p.id,
                sub_document_id=p.sub_document_id,
                proposed_folder=p.proposed_folder,
                proposed_filename=p.proposed_filename,
                is_new_folder=p.is_new_folder,
                rationale=p.rationale,
                alternatives_considered=p.alternatives_considered,
                confidence=p.confidence,
                decision=p.decision,
                user_folder=p.user_folder,
                user_filename=p.user_filename,
            )
            for p in proposals
        ],
    )


@router.post("/sub_documents/{sub_doc_id}/accept")
async def accept_proposal(sub_doc_id: str, db: Session = Depends(get_db)):
    """Accept the filing proposal as-is and move the file."""
    sub_doc = db.query(SubDocument).filter(SubDocument.id == sub_doc_id).first()
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Sub-document not found")

    proposal = (
        db.query(FilingProposal)
        .filter(FilingProposal.sub_document_id == sub_doc_id)
        .order_by(FilingProposal.created_at.desc())
        .first()
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="No proposal found")

    # Move the file
    result = _move_sub_document(sub_doc, proposal.proposed_folder, proposal.proposed_filename, db)

    # Update records
    proposal.decision = "accepted"
    proposal.decided_at = datetime.utcnow()
    sub_doc.final_folder = proposal.proposed_folder
    sub_doc.final_filename = proposal.proposed_filename
    sub_doc.status = "filed"
    sub_doc.output_path = result.get("destination", "")
    db.commit()

    return {"success": True, "destination": result.get("destination")}


@router.post("/sub_documents/{sub_doc_id}/modify")
async def modify_proposal(sub_doc_id: str, req: ModifyRequest, db: Session = Depends(get_db)):
    """Modify the proposal (change folder/filename/type), move file, record correction."""
    sub_doc = db.query(SubDocument).filter(SubDocument.id == sub_doc_id).first()
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Sub-document not found")

    proposal = (
        db.query(FilingProposal)
        .filter(FilingProposal.sub_document_id == sub_doc_id)
        .order_by(FilingProposal.created_at.desc())
        .first()
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="No proposal found")

    final_folder = req.folder or proposal.proposed_folder
    final_filename = req.filename or proposal.proposed_filename

    # Move the file
    result = _move_sub_document(sub_doc, final_folder, final_filename, db)

    # Update proposal
    proposal.decision = "modified"
    proposal.decided_at = datetime.utcnow()
    proposal.user_folder = final_folder
    proposal.user_filename = final_filename
    proposal.user_rationale = req.rationale

    # Update sub-document
    sub_doc.final_folder = final_folder
    sub_doc.final_filename = final_filename
    sub_doc.status = "filed"
    sub_doc.output_path = result.get("destination", "")

    if req.document_type:
        sub_doc.document_type = req.document_type

    # Record learning correction
    correction = LearningCorrection(
        sub_document_id=sub_doc.id,
        proposed_document_type=sub_doc.document_type if req.document_type else None,
        actual_document_type=req.document_type,
        proposed_folder=proposal.proposed_folder,
        actual_folder=final_folder,
        proposed_filename=proposal.proposed_filename,
        actual_filename=final_filename,
        user_rationale=req.rationale,
        correction_type="filing" if req.folder else "filename",
        key_signals={
            "institution": sub_doc.institution,
            "document_type": sub_doc.document_type,
        },
    )
    if req.document_type:
        correction.correction_type = "classification"

    db.add(correction)
    db.commit()

    return {"success": True, "destination": result.get("destination")}


@router.post("/sub_documents/{sub_doc_id}/reject")
async def reject_proposal(sub_doc_id: str, req: RejectRequest, db: Session = Depends(get_db)):
    """Reject the proposal, record reason as learning correction, don't move file."""
    sub_doc = db.query(SubDocument).filter(SubDocument.id == sub_doc_id).first()
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Sub-document not found")

    proposal = (
        db.query(FilingProposal)
        .filter(FilingProposal.sub_document_id == sub_doc_id)
        .order_by(FilingProposal.created_at.desc())
        .first()
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="No proposal found")

    proposal.decision = "rejected"
    proposal.decided_at = datetime.utcnow()
    proposal.user_rationale = req.reason

    sub_doc.status = "rejected"

    correction = LearningCorrection(
        sub_document_id=sub_doc.id,
        proposed_folder=proposal.proposed_folder,
        proposed_filename=proposal.proposed_filename,
        user_rationale=req.reason,
        correction_type="filing",
        key_signals={
            "institution": sub_doc.institution,
            "document_type": sub_doc.document_type,
        },
    )
    db.add(correction)
    db.commit()

    return {"success": True, "message": "Proposal rejected"}


@router.post("/{document_id}/accept_all", response_model=AcceptAllResponse)
async def accept_all_proposals(document_id: str, req: Optional[AcceptAllRequest] = None, db: Session = Depends(get_db)):
    """Bulk accept all (or specified) proposals for a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    query = db.query(SubDocument).filter(
        SubDocument.document_id == document_id,
        SubDocument.status == "proposed",
    )
    if req and req.sub_document_ids:
        query = query.filter(SubDocument.id.in_(req.sub_document_ids))

    sub_docs = query.all()
    accepted = 0
    errors = []

    for sub_doc in sub_docs:
        proposal = (
            db.query(FilingProposal)
            .filter(FilingProposal.sub_document_id == sub_doc.id)
            .order_by(FilingProposal.created_at.desc())
            .first()
        )
        if not proposal:
            errors.append(f"{sub_doc.id}: no proposal found")
            continue

        try:
            result = _move_sub_document(sub_doc, proposal.proposed_folder, proposal.proposed_filename, db)
            proposal.decision = "accepted"
            proposal.decided_at = datetime.utcnow()
            sub_doc.final_folder = proposal.proposed_folder
            sub_doc.final_filename = proposal.proposed_filename
            sub_doc.status = "filed"
            sub_doc.output_path = result.get("destination", "")
            db.commit()
            accepted += 1
        except Exception as e:
            errors.append(f"{sub_doc.id}: {str(e)}")

    return AcceptAllResponse(accepted=accepted, errors=errors)


@router.get("/sub_documents/{sub_doc_id}/preview")
async def preview_sub_document(
    sub_doc_id: str,
    page: int = 1,
    dpi: int = 150,
    db: Session = Depends(get_db),
):
    """Get a page image from a sub-document for preview."""
    sub_doc = db.query(SubDocument).filter(SubDocument.id == sub_doc_id).first()
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Sub-document not found")

    # Calculate actual page number
    actual_page = sub_doc.start_page + page - 1
    if actual_page > sub_doc.end_page:
        raise HTTPException(status_code=400, detail=f"Page {page} out of range (max {sub_doc.page_count})")

    # Find the Page record to locate the PDF
    page_record = (
        db.query(Page)
        .filter(Page.document_id == sub_doc.document_id, Page.page_number == actual_page)
        .first()
    )

    if not page_record:
        raise HTTPException(status_code=404, detail="Page record not found")

    from services.folder_router import FolderRouter
    folder_router = FolderRouter(settings.documents_dir)
    pdf_path = folder_router.full_path_for_document(page_record.assigned_folder, page_record.output_filename)

    if not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="Page PDF not found on disk")

    pdf_processor = PDFProcessor(temp_dir=settings.uploads_temp_dir)
    pdf_bytes = Path(pdf_path).read_bytes()
    image_bytes = await pdf_processor.pdf_page_to_image(pdf_bytes, dpi=dpi)

    return StreamingResponse(io.BytesIO(image_bytes), media_type="image/png")


@router.post("/sub_documents/{sub_doc_id}/reclassify", response_model=ReclassifyResponse)
async def reclassify_sub_document(sub_doc_id: str, db: Session = Depends(get_db)):
    """Re-run classification for a sub-document."""
    sub_doc = db.query(SubDocument).filter(SubDocument.id == sub_doc_id).first()
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Sub-document not found")

    # Get page images for this sub-document
    pages = (
        db.query(Page)
        .filter(
            Page.document_id == sub_doc.document_id,
            Page.page_number >= sub_doc.start_page,
            Page.page_number <= sub_doc.end_page,
        )
        .order_by(Page.page_number.asc())
        .all()
    )

    from services.folder_router import FolderRouter
    folder_router = FolderRouter(settings.documents_dir)
    pdf_processor = PDFProcessor(temp_dir=settings.uploads_temp_dir)

    page_images = []
    for p in pages:
        pdf_path = folder_router.full_path_for_document(p.assigned_folder, p.output_filename)
        if Path(pdf_path).exists():
            pdf_bytes = Path(pdf_path).read_bytes()
            try:
                img = await pdf_processor.pdf_page_to_image(pdf_bytes, dpi=150)
                page_images.append(img)
            except Exception:
                pass

    try:
        llm_manager = LLMManager(settings.llm_config_path)
        from services.rich_classifier import RichClassifier
        classifier = RichClassifier(llm_manager)

        # Load corrections
        corrections = (
            db.query(LearningCorrection)
            .order_by(LearningCorrection.created_at.desc())
            .limit(settings.max_learning_corrections_in_prompt)
            .all()
        )
        correction_dicts = [
            {
                "correction_type": c.correction_type,
                "proposed_document_type": c.proposed_document_type,
                "actual_document_type": c.actual_document_type,
                "user_rationale": c.user_rationale,
            }
            for c in corrections
        ]

        result = await classifier.classify(page_images, learning_corrections=correction_dicts)

        sub_doc.document_type = result.get("document_type")
        sub_doc.document_category = result.get("document_category")
        sub_doc.institution = result.get("institution")
        sub_doc.classification_metadata = result.get("classification_metadata")
        sub_doc.confidence_score = result.get("confidence")
        db.commit()

        return ReclassifyResponse(
            sub_document_id=sub_doc.id,
            document_type=sub_doc.document_type,
            institution=sub_doc.institution,
            confidence_score=sub_doc.confidence_score,
            classification_metadata=sub_doc.classification_metadata,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reclassification failed: {e}")


@router.post("/sub_documents/{sub_doc_id}/refile", response_model=RefileResponse)
async def refile_sub_document(sub_doc_id: str, db: Session = Depends(get_db)):
    """Re-run the filing proposal for a sub-document."""
    sub_doc = db.query(SubDocument).filter(SubDocument.id == sub_doc_id).first()
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Sub-document not found")

    try:
        from llm.llm_manager import LLMManager
        llm_manager = LLMManager(settings.llm_config_path)
        from services.smart_filer import SmartFiler
        filer = SmartFiler(llm_manager)

        corrections = (
            db.query(LearningCorrection)
            .order_by(LearningCorrection.created_at.desc())
            .limit(settings.max_learning_corrections_in_prompt)
            .all()
        )
        correction_dicts = [
            {
                "correction_type": c.correction_type,
                "proposed_folder": c.proposed_folder,
                "actual_folder": c.actual_folder,
                "user_rationale": c.user_rationale,
            }
            for c in corrections
        ]

        metadata = {
            "document_type": sub_doc.document_type,
            "document_category": sub_doc.document_category,
            "institution": sub_doc.institution,
            "classification_metadata": sub_doc.classification_metadata or {},
        }

        proposal_data = await filer.propose_filing(metadata, correction_dicts)

        sub_doc.proposed_folder = proposal_data.get("proposed_folder")
        sub_doc.proposed_filename = proposal_data.get("proposed_filename")
        sub_doc.filing_rationale = proposal_data.get("rationale")
        sub_doc.status = "proposed"

        new_proposal = FilingProposal(
            sub_document_id=sub_doc.id,
            proposed_folder=proposal_data.get("proposed_folder", ""),
            proposed_filename=proposal_data.get("proposed_filename", ""),
            is_new_folder=proposal_data.get("is_new_folder", False),
            rationale=proposal_data.get("rationale"),
            alternatives_considered=proposal_data.get("alternatives"),
            confidence=proposal_data.get("confidence"),
        )
        db.add(new_proposal)
        db.commit()

        return RefileResponse(
            sub_document_id=sub_doc.id,
            proposed_folder=sub_doc.proposed_folder,
            proposed_filename=sub_doc.proposed_filename,
            rationale=sub_doc.filing_rationale,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refiling failed: {e}")


# ── Helpers ───────────────────────────────────────────────────────────

def _move_sub_document(
    sub_doc: SubDocument,
    dest_folder: str,
    dest_filename: str,
    db: Session,
) -> Dict[str, Any]:
    """
    Extract the sub-document's page range from the original PDF,
    write it as a single PDF, and move it to the destination.
    """
    # Find the original document to get page PDFs
    pages = (
        db.query(Page)
        .filter(
            Page.document_id == sub_doc.document_id,
            Page.page_number >= sub_doc.start_page,
            Page.page_number <= sub_doc.end_page,
        )
        .order_by(Page.page_number.asc())
        .all()
    )

    if not pages:
        raise HTTPException(status_code=404, detail="No page records found for sub-document")

    from services.folder_router import FolderRouter
    folder_router = FolderRouter(settings.documents_dir)

    # For single-page sub-documents, just move the existing file
    if len(pages) == 1:
        page = pages[0]
        source_path = folder_router.full_path_for_document(page.assigned_folder, page.output_filename)
        if not Path(source_path).exists():
            raise HTTPException(status_code=404, detail="Source PDF not found on disk")

        file_manager = FileManager(settings.documents_dir)
        success, dest_path, msg = file_manager.move_file(source_path, dest_folder, dest_filename)
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to move file: {msg}")

        page.assigned_folder = dest_folder
        page.output_filename = Path(dest_path).name

        return {"success": True, "destination": dest_path}

    # Multi-page: merge page PDFs into one
    from PyPDF2 import PdfReader, PdfWriter
    writer = PdfWriter()
    for page in pages:
        source_path = folder_router.full_path_for_document(page.assigned_folder, page.output_filename)
        if Path(source_path).exists():
            reader = PdfReader(source_path)
            for p in reader.pages:
                writer.add_page(p)

    # Write merged PDF to temp
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=settings.uploads_temp_dir) as tmp:
        writer.write(tmp)
        tmp_path = tmp.name

    # Move to destination
    file_manager = FileManager(settings.documents_dir)
    success, dest_path, msg = file_manager.move_file(tmp_path, dest_folder, dest_filename)
    if not success:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to move file: {msg}")

    return {"success": True, "destination": dest_path}
