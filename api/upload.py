"""
Upload API for DocFlow MVP.

Provides endpoints to upload PDFs, split into pages, attempt AI analysis
(if providers are available), propose folders/filenames, and organize files
into the documents directory. Persists documents, pages, and decisions.
"""

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from pathlib import Path
import io
import os
import uuid
import logging

from config.settings import settings
from database.database import get_db, SessionLocal
from database.models import Document, Page
from sqlalchemy.orm import Session

from services.pdf_processor import PDFProcessor
from services.document_analyzer import DocumentAnalyzer
from services.folder_router import FolderRouter
from services.file_manager import FileManager
from services.learning_engine import LearningEngine
from llm.llm_manager import LLMManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class PageResult(BaseModel):
    page_number: int
    document_type: str
    institution: Optional[str] = None
    date: Optional[str] = None
    confidence_score: float
    folder: str
    filename: str
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


class UploadResponse(BaseModel):
    document_id: str
    original_filename: str
    total_pages: int
    results: List[PageResult]


class DocumentDetails(BaseModel):
    document_id: str
    original_filename: str
    status: str
    total_pages: int
    error_message: Optional[str] = None
    pages: List[PageResult]


def _ensure_dirs() -> None:
    Path(settings.uploads_temp_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.documents_dir).mkdir(parents=True, exist_ok=True)


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    analyze: bool = True,
    dpi: int = 150,
    background: bool = False,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    """
    Upload a PDF, split into pages, analyze (if possible), and organize files.
    Returns per-page destinations and metadata.
    """
    _ensure_dirs()

    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Read file bytes
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    # Init helpers
    pdf = PDFProcessor(temp_dir=settings.uploads_temp_dir)
    folder_router = FolderRouter(settings.documents_dir)
    file_manager = FileManager(settings.documents_dir)

    # LLM manager may fail if providers not configured; handle gracefully
    analyzer = None
    if analyze:
        try:
            llm_manager = LLMManager(settings.llm_config_path)
            analyzer = DocumentAnalyzer(llm_manager, confidence_threshold=settings.min_confidence_threshold)
        except Exception as e:
            logger.warning(f"LLMManager not available; proceeding without AI analysis: {e}")
            analyzer = None  # Fallback to non-AI flow

    # Validate and count pages
    is_valid, err = await pdf.validate_pdf(data)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid PDF: {err}")
    page_count = await pdf.get_page_count(data)

    # Persist Document row
    document_id = str(uuid.uuid4())
    doc = Document(
        id=document_id,
        original_filename=file.filename or "upload.pdf",
        total_pages=page_count,
        status="processing",
        file_size_bytes=len(data),
    )
    db.add(doc)
    db.commit()

    results: List[PageResult] = []

    # Background processing path
    if background:
        temp_pdf_path = str(Path(settings.uploads_temp_dir) / f"{document_id}_upload.pdf")
        with open(temp_pdf_path, "wb") as f:
            f.write(data)

        # Schedule background processing
        if background_tasks is not None:
            background_tasks.add_task(
                _process_document_background,
                document_id=document_id,
                temp_pdf_path=temp_pdf_path,
                analyze=analyze,
                dpi=dpi,
            )

        logger.info(f"Scheduled background processing for document {document_id}")
        return UploadResponse(
            document_id=document_id,
            original_filename=doc.original_filename,
            total_pages=page_count,
            results=[],
        )

    # Split into single-page PDFs
    try:
        pages = await pdf.split_pdf(data)
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to split PDF: {e}")

    # Process each page synchronously for MVP
    learning = LearningEngine(db)

    for idx, page_pdf_bytes in enumerate(pages, start=1):
        # Try to convert to image for AI; if fails, skip AI
        image_bytes: Optional[bytes] = None
        try:
            if analyze:
                image_bytes = await pdf.pdf_page_to_image(page_pdf_bytes, dpi=dpi)
        except Exception as e:
            logger.warning(f"Page {idx}: image conversion unavailable: {e}")

        # Analyze (if analyzer available and image ready)
        if analyzer and image_bytes:
            analysis = await analyzer.analyze_page(image_bytes, page_number=idx)
        else:
            # Fallback result with minimal info
            analysis = {
                "success": False,
                "error": None if image_bytes else "image_unavailable",
                "document_type": "unknown",
                "institution": None,
                "date": None,
                "confidence_score": 0.0,
                "extracted_metadata": {},
                "provider_used": None,
                "model_used": None,
            }

        # Propose folder and filename
        folder_path, _conf = folder_router.propose_folder(analysis, create_if_missing=True)
        filename = folder_router.generate_filename(analysis, page_number=idx, extension="pdf")

        # Write one-page PDF to temp and organize
        temp_page_path = str(Path(settings.uploads_temp_dir) / f"{document_id}_page_{idx}.pdf")
        with open(temp_page_path, "wb") as f:
            f.write(page_pdf_bytes)

        org_result = file_manager.organize_document(
            source_file=temp_page_path,
            folder_path=folder_path,
            filename=filename,
            move=True,
        )

        # Persist Page row
        page_row = Page(
            document_id=document_id,
            page_number=idx,
            document_type=analysis.get("document_type", "unknown"),
            institution=analysis.get("institution"),
            confidence_score=analysis.get("confidence_score", 0.0),
            extracted_metadata=analysis.get("extracted_metadata", {}),
            assigned_folder=folder_path,
            output_filename=filename,
            processing_status="completed" if org_result.get("success") else "failed",
            processing_error=None if org_result.get("success") else org_result.get("message"),
            llm_provider_used=analysis.get("provider_used") or "",
            llm_model_used=analysis.get("model_used") or "",
        )
        db.add(page_row)
        db.commit()

        # Record decision
        learning.record_decision(
            page_id=page_row.id,
            proposed_folder=folder_path,
            proposed_filename=filename,
            proposed_confidence=analysis.get("confidence_score", 0.0),
        )

        results.append(
            PageResult(
                page_number=idx,
                document_type=page_row.document_type,
                institution=page_row.institution,
                date=analysis.get("date"),
                confidence_score=page_row.confidence_score,
                folder=folder_path,
                filename=filename,
                provider_used=analysis.get("provider_used"),
                model_used=analysis.get("model_used"),
                success=org_result.get("success", False),
                error=None if org_result.get("success") else org_result.get("message"),
            )
        )

    # Mark document complete
    doc.status = "completed"
    db.commit()

    return UploadResponse(
        document_id=document_id,
        original_filename=doc.original_filename,
        total_pages=page_count,
        results=results,
    )


def _cleanup_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _process_document_background(
    document_id: str,
    temp_pdf_path: str,
    analyze: bool,
    dpi: int,
) -> None:
    """Background task to process a document end-to-end."""
    db: Session = SessionLocal()
    try:
        with open(temp_pdf_path, "rb") as f:
            data = f.read()

        pdf = PDFProcessor(temp_dir=settings.uploads_temp_dir)
        folder_router = FolderRouter(settings.documents_dir)
        file_manager = FileManager(settings.documents_dir)

        analyzer = None
        if analyze:
            try:
                llm_manager = LLMManager(settings.llm_config_path)
                analyzer = DocumentAnalyzer(llm_manager, confidence_threshold=settings.min_confidence_threshold)
            except Exception as e:
                logger.warning(f"BG: LLMManager unavailable: {e}")

        # Split
        pages = []
        try:
            pages = __import__('asyncio').get_event_loop().run_until_complete(pdf.split_pdf(data))
        except RuntimeError:
            # If no running loop in BG context, use new loop
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            pages = loop.run_until_complete(pdf.split_pdf(data))

        learning = LearningEngine(db)
        
        # Process
        for idx, page_pdf_bytes in enumerate(pages, start=1):
            image_bytes = None
            if analyze:
                try:
                    import asyncio
                    try:
                        image_bytes = asyncio.get_event_loop().run_until_complete(
                            pdf.pdf_page_to_image(page_pdf_bytes, dpi=dpi)
                        )
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        image_bytes = loop.run_until_complete(
                            pdf.pdf_page_to_image(page_pdf_bytes, dpi=dpi)
                        )
                except Exception as e:
                    logger.warning(f"BG: Page {idx} image conversion failed: {e}")

            if analyzer and image_bytes:
                try:
                    import asyncio
                    try:
                        analysis = asyncio.get_event_loop().run_until_complete(
                            analyzer.analyze_page(image_bytes, page_number=idx)
                        )
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        analysis = loop.run_until_complete(
                            analyzer.analyze_page(image_bytes, page_number=idx)
                        )
                except Exception as e:
                    logger.warning(f"BG: Analysis failed page {idx}: {e}")
                    analysis = {
                        "success": False,
                        "document_type": "unknown",
                        "institution": None,
                        "date": None,
                        "confidence_score": 0.0,
                        "extracted_metadata": {},
                        "provider_used": None,
                        "model_used": None,
                    }
            else:
                analysis = {
                    "success": False,
                    "document_type": "unknown",
                    "institution": None,
                    "date": None,
                    "confidence_score": 0.0,
                    "extracted_metadata": {},
                    "provider_used": None,
                    "model_used": None,
                }

            folder_path, _ = folder_router.propose_folder(analysis, create_if_missing=True)
            filename = folder_router.generate_filename(analysis, page_number=idx, extension="pdf")

            temp_page_path = str(Path(settings.uploads_temp_dir) / f"{document_id}_bg_page_{idx}.pdf")
            with open(temp_page_path, "wb") as f:
                f.write(page_pdf_bytes)

            org_result = file_manager.organize_document(temp_page_path, folder_path, filename, move=True)

            page_row = Page(
                document_id=document_id,
                page_number=idx,
                document_type=analysis.get("document_type", "unknown"),
                institution=analysis.get("institution"),
                confidence_score=analysis.get("confidence_score", 0.0),
                extracted_metadata=analysis.get("extracted_metadata", {}),
                assigned_folder=folder_path,
                output_filename=filename,
                processing_status="completed" if org_result.get("success") else "failed",
                processing_error=None if org_result.get("success") else org_result.get("message"),
                llm_provider_used=analysis.get("provider_used") or "",
                llm_model_used=analysis.get("model_used") or "",
            )
            db.add(page_row)
            db.commit()

            learning.record_decision(
                page_id=page_row.id,
                proposed_folder=folder_path,
                proposed_filename=filename,
                proposed_confidence=analysis.get("confidence_score", 0.0),
            )

        # Mark document complete
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "completed"
            db.commit()

    except Exception as e:
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if doc:
                doc.status = "failed"
                doc.error_message = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        _cleanup_file(temp_pdf_path)
        db.close()


@router.get("/{document_id}", response_model=DocumentDetails)
async def get_document(document_id: str, db: Session = Depends(get_db)):
    """Fetch a document and its page results."""
    doc: Optional[Document] = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pages = db.query(Page).filter(Page.document_id == document_id).order_by(Page.page_number.asc()).all()
    page_results: List[PageResult] = []
    for p in pages:
        page_results.append(
            PageResult(
                page_number=p.page_number,
                document_type=p.document_type,
                institution=p.institution,
                date=(p.extracted_metadata or {}).get("date"),
                confidence_score=p.confidence_score,
                folder=p.assigned_folder,
                filename=p.output_filename,
                provider_used=p.llm_provider_used or None,
                model_used=p.llm_model_used or None,
                success=(p.processing_status == "completed"),
                error=p.processing_error,
            )
        )

    return DocumentDetails(
        document_id=doc.id,
        original_filename=doc.original_filename,
        status=doc.status,
        total_pages=doc.total_pages,
        error_message=doc.error_message,
        pages=page_results,
    )


class CorrectionRequest(BaseModel):
    decision_id: str
    actual_folder: str
    actual_filename: str


class CorrectionResponse(BaseModel):
    success: bool
    message: str


@router.post("/decisions/correct", response_model=CorrectionResponse)
async def correct_decision(
    correction: CorrectionRequest,
    db: Session = Depends(get_db)
):
    """
    Record a user correction to improve future routing decisions.
    """
    try:
        engine = LearningEngine(db)
        result = engine.record_correction(
            decision_id=correction.decision_id,
            actual_folder=correction.actual_folder,
            actual_filename=correction.actual_filename,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("message", "failed"))
        return CorrectionResponse(success=True, message="Correction recorded")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Correction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
