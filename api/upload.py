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
from database.models import Document, Page, ProcessingDecision
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
    page_id: Optional[str] = None
    page_number: int
    document_type: str
    institution: Optional[str] = None
    date: Optional[str] = None
    confidence_score: float
    folder: str
    filename: str
    proposed_folder: Optional[str] = None
    proposed_filename: Optional[str] = None
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    decision_id: Optional[str] = None


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
    pages_done: int
    last_error: Optional[str] = None
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
        if not org_result.get("success"):
            try:
                Path(temp_page_path).unlink(missing_ok=True)
            except Exception:
                pass

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
            if not org_result.get("success"):
                _cleanup_file(temp_page_path)

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
    pages_done = len(pages)
    last_error = None
    for p in reversed(pages):
        if p.processing_error:
            last_error = p.processing_error
            break
    page_results: List[PageResult] = []
    for p in pages:
        # Fetch latest decision, if any
        latest_decision = (
            db.query(ProcessingDecision)
            .filter(ProcessingDecision.page_id == p.id)
            .order_by(ProcessingDecision.timestamp.desc())
            .first()
        )
        page_results.append(
            PageResult(
                page_id=p.id,
                page_number=p.page_number,
                document_type=p.document_type,
                institution=p.institution,
                date=(p.extracted_metadata or {}).get("date"),
                confidence_score=p.confidence_score,
                folder=p.assigned_folder,
                filename=p.output_filename,
                proposed_folder=(latest_decision.proposed_folder if latest_decision else None),
                proposed_filename=(latest_decision.proposed_filename if latest_decision else None),
                provider_used=p.llm_provider_used or None,
                model_used=p.llm_model_used or None,
                success=(p.processing_status == "completed"),
                error=p.processing_error,
                decision_id=(latest_decision.id if latest_decision else None),
            )
        )

    return DocumentDetails(
        document_id=doc.id,
        original_filename=doc.original_filename,
        status=doc.status,
        total_pages=doc.total_pages,
        error_message=doc.error_message,
        pages_done=pages_done,
        last_error=last_error,
        pages=page_results,
    )


class ReanalyzeResponse(BaseModel):
    page: PageResult


@router.post("/pages/{page_id}/reanalyze", response_model=ReanalyzeResponse)
async def reanalyze_page(page_id: str, dpi: int = 150, db: Session = Depends(get_db)):
    """Re-run AI analysis for a single page from its stored PDF."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Locate PDF file on disk
    router = FolderRouter(settings.documents_dir)
    pdf_path = router.full_path_for_document(page.assigned_folder, page.output_filename)
    if not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="Stored page PDF not found on disk")

    # Convert to image and analyze
    pdf = PDFProcessor(temp_dir=settings.uploads_temp_dir)
    try:
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        image_bytes = await pdf.pdf_page_to_image(pdf_bytes, dpi=dpi)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to convert PDF to image: {e}")

    try:
        llm_manager = LLMManager(settings.llm_config_path)
        analyzer = DocumentAnalyzer(llm_manager, confidence_threshold=settings.min_confidence_threshold)
        analysis = await analyzer.analyze_page(image_bytes, page_number=page.page_number)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    # Update page with new analysis
    page.document_type = analysis.get("document_type", page.document_type)
    page.institution = analysis.get("institution")
    page.confidence_score = analysis.get("confidence_score", 0.0)
    page.extracted_metadata = analysis.get("extracted_metadata", {})
    page.llm_provider_used = analysis.get("provider_used") or ""
    page.llm_model_used = analysis.get("model_used") or ""
    db.commit()

    # Record new decision proposal
    folder_router = FolderRouter(settings.documents_dir)
    new_folder, _ = folder_router.propose_folder(analysis, create_if_missing=False)
    new_filename = folder_router.generate_filename(analysis, page_number=page.page_number, extension="pdf")
    learning = LearningEngine(db)
    learning.record_decision(
        page_id=page.id,
        proposed_folder=new_folder,
        proposed_filename=new_filename,
        proposed_confidence=page.confidence_score,
        user_corrected=False,
    )

    # Prepare response
    decision = db.query(ProcessingDecision).filter(ProcessingDecision.page_id == page.id).order_by(ProcessingDecision.timestamp.desc()).first()
    return ReanalyzeResponse(
        page=PageResult(
            page_id=page.id,
            page_number=page.page_number,
            document_type=page.document_type,
            institution=page.institution,
            date=(page.extracted_metadata or {}).get("date"),
            confidence_score=page.confidence_score,
            folder=new_folder,
            filename=new_filename,
            proposed_folder=new_folder,
            proposed_filename=new_filename,
            provider_used=page.llm_provider_used or None,
            model_used=page.llm_model_used or None,
            success=True,
            error=None,
            decision_id=decision.id if decision else None,
        )
    )


class PageCorrectionRequest(BaseModel):
    folder: str
    filename: str


@router.post("/pages/{page_id}/correct")
async def correct_page(page_id: str, req: PageCorrectionRequest, db: Session = Depends(get_db)):
    """Apply a correction for a page by moving the file and updating learning."""
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    folder_router = FolderRouter(settings.documents_dir)
    file_manager = FileManager(settings.documents_dir)
    # Current path
    current_path = folder_router.full_path_for_document(page.assigned_folder, page.output_filename)
    # Move to corrected path
    success, dest, msg = file_manager.move_file(current_path, req.folder, req.filename)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    # Update page
    page.assigned_folder = req.folder
    page.output_filename = req.filename
    page.processing_status = "completed"
    db.commit()

    # Find latest decision for this page
    decision = db.query(ProcessingDecision).filter(ProcessingDecision.page_id == page.id).order_by(ProcessingDecision.timestamp.desc()).first()
    learning = LearningEngine(db)
    if decision:
        learning.record_correction(decision_id=decision.id, actual_folder=req.folder, actual_filename=req.filename)
    else:
        # Create a decision to learn from
        learning.record_decision(
            page_id=page.id,
            proposed_folder=req.folder,
            proposed_filename=req.filename,
            proposed_confidence=page.confidence_score,
            actual_folder=req.folder,
            actual_filename=req.filename,
            user_corrected=True,
        )

    return {"success": True, "message": "Page corrected", "destination": dest}


class ReanalyzeDocumentResponse(BaseModel):
    document_id: str
    status: str
    pages_updated: int = 0
    background: bool = False


@router.post("/{document_id}/reanalyze", response_model=ReanalyzeDocumentResponse)
async def reanalyze_document(
    document_id: str,
    dpi: int = 150,
    background: bool = True,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Re-run AI analysis for all pages of a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Mark as processing
    doc.status = "processing"
    doc.error_message = None
    db.commit()

    if background and background_tasks is not None:
        background_tasks.add_task(_reanalyze_document_background, document_id=document_id, dpi=dpi)
        return ReanalyzeDocumentResponse(document_id=document_id, status="scheduled", pages_updated=0, background=True)

    # Foreground reanalysis
    pages = db.query(Page).filter(Page.document_id == document_id).order_by(Page.page_number.asc()).all()
    updated = 0
    errors = []
    try:
        llm_manager = LLMManager(settings.llm_config_path)
        analyzer = DocumentAnalyzer(llm_manager, confidence_threshold=settings.min_confidence_threshold)
        router = FolderRouter(settings.documents_dir)
        pdf = PDFProcessor(temp_dir=settings.uploads_temp_dir)
        learning = LearningEngine(db)

        for p in pages:
            pdf_path = router.full_path_for_document(p.assigned_folder, p.output_filename)
            if not Path(pdf_path).exists():
                errors.append(f"missing:{p.page_number}")
                continue
            with open(pdf_path, 'rb') as f:
                page_pdf = f.read()
            try:
                image_bytes = await pdf.pdf_page_to_image(page_pdf, dpi=dpi)
                analysis = await analyzer.analyze_page(image_bytes, page_number=p.page_number)
                p.document_type = analysis.get("document_type", p.document_type)
                p.institution = analysis.get("institution")
                p.confidence_score = analysis.get("confidence_score", 0.0)
                p.extracted_metadata = analysis.get("extracted_metadata", {})
                p.llm_provider_used = analysis.get("provider_used") or ""
                p.llm_model_used = analysis.get("model_used") or ""
                db.commit()
                # Record decision suggestion (no move)
                new_folder, _ = router.propose_folder(analysis, create_if_missing=False)
                new_filename = router.generate_filename(analysis, page_number=p.page_number, extension="pdf")
                learning.record_decision(
                    page_id=p.id,
                    proposed_folder=new_folder,
                    proposed_filename=new_filename,
                    proposed_confidence=p.confidence_score,
                    user_corrected=False,
                )
                updated += 1
            except Exception as e:
                errors.append(f"err:{p.page_number}")
                continue
        doc.status = "completed"
        doc.error_message = None if not errors else ",".join(errors)
        db.commit()
        return ReanalyzeDocumentResponse(document_id=document_id, status=doc.status, pages_updated=updated, background=False)
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Reanalysis failed: {e}")


def _reanalyze_document_background(document_id: str, dpi: int = 150) -> None:
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return
        router = FolderRouter(settings.documents_dir)
        pdf = PDFProcessor(temp_dir=settings.uploads_temp_dir)
        learning = LearningEngine(db)
        try:
            llm_manager = LLMManager(settings.llm_config_path)
            import asyncio
            analyzer = DocumentAnalyzer(llm_manager, confidence_threshold=settings.min_confidence_threshold)
        except Exception:
            doc.status = "failed"
            doc.error_message = "No available LLM provider"
            db.commit()
            return
        pages = db.query(Page).filter(Page.document_id == document_id).order_by(Page.page_number.asc()).all()
        updated = 0
        for p in pages:
            pdf_path = router.full_path_for_document(p.assigned_folder, p.output_filename)
            if not Path(pdf_path).exists():
                continue
            with open(pdf_path, 'rb') as f:
                page_pdf = f.read()
            # Convert and analyze with own loop if needed
            try:
                try:
                    image_bytes = asyncio.get_event_loop().run_until_complete(pdf.pdf_page_to_image(page_pdf, dpi=dpi))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    image_bytes = loop.run_until_complete(pdf.pdf_page_to_image(page_pdf, dpi=dpi))
                try:
                    result = asyncio.get_event_loop().run_until_complete(analyzer.analyze_page(image_bytes, page_number=p.page_number))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result = loop.run_until_complete(analyzer.analyze_page(image_bytes, page_number=p.page_number))
                p.document_type = result.get("document_type", p.document_type)
                p.institution = result.get("institution")
                p.confidence_score = result.get("confidence_score", 0.0)
                p.extracted_metadata = result.get("extracted_metadata", {})
                p.llm_provider_used = result.get("provider_used") or ""
                p.llm_model_used = result.get("model_used") or ""
                db.commit()
                # Record decision proposal
                new_folder, _ = router.propose_folder(result, create_if_missing=False)
                new_filename = router.generate_filename(result, page_number=p.page_number, extension="pdf")
                learning.record_decision(
                    page_id=p.id,
                    proposed_folder=new_folder,
                    proposed_filename=new_filename,
                    proposed_confidence=p.confidence_score,
                    user_corrected=False,
                )
                updated += 1
            except Exception:
                continue
        doc.status = "completed"
        doc.error_message = None
        db.commit()
    finally:
        db.close()


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
