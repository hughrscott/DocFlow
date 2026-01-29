"""
Upload API for DocFlow MVP.

Provides endpoints to upload PDFs, split into pages, attempt AI analysis
(if providers are available), propose folders/filenames, and organize files
into the documents directory. Persists documents, pages, and decisions.
"""

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
import io
import os
import uuid
import logging
from datetime import datetime

from config.settings import settings
from database.database import get_db, SessionLocal
from database.models import Document, Page, ProcessingDecision, FileMoveAudit
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
    sequence_id: Optional[str] = None
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
    last_error_at: Optional[str] = None
    last_error: Optional[str] = None
    pages: List[PageResult]
    doc_level: Optional[Dict[str, Any]] = None


class ReanalyzeDocumentResponse(BaseModel):
    document_id: str
    status: str
    pages_updated: int = 0
    background: bool = False


class SequenceApplyResponse(BaseModel):
    document_id: str
    sequence_id: str
    pages_total: int
    pages_moved: int
    missing_proposals: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    audit_ids: List[str] = Field(default_factory=list)


class RevertMoveRequest(BaseModel):
    audit_id: Optional[str] = None


class RevertMoveResponse(BaseModel):
    success: bool
    reverted_audit_id: Optional[str] = None
    restored_to: Optional[Dict[str, str]] = None


class FolderSuggestion(BaseModel):
    path: str
    depth: int
    file_count: int
    subfolders: List[str]


class FolderSuggestionsResponse(BaseModel):
    suggestions: List[FolderSuggestion]
    generated_at: Optional[str] = None


def _ensure_dirs() -> None:
    Path(settings.uploads_temp_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.documents_dir).mkdir(parents=True, exist_ok=True)


def _safe_get(d: Dict[str, Any], path: List[str]) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _is_continuation(prev_meta: Optional[Dict[str, Any]], curr_meta: Dict[str, Any]) -> bool:
    """Heuristic: whether current page likely continues previous.

    Signals considered: explicit continuation flag, matching header/footer signatures,
    font overlap, matching identifiers, compatible page markers.
    """
    if not prev_meta or not isinstance(prev_meta, dict) or not isinstance(curr_meta, dict):
        # if curr says explicit continuation, allow
        explicit = _safe_get(curr_meta, ["is_continuation_of_previous", "value"]) or False
        return bool(explicit)

    score = 0
    # Explicit
    if _safe_get(curr_meta, ["is_continuation_of_previous", "value"]):
        score += 2

    # Header/Footer signatures
    prev_head = _safe_get(prev_meta, ["layout_fingerprints", "header_signature"])
    curr_head = _safe_get(curr_meta, ["layout_fingerprints", "header_signature"])
    prev_foot = _safe_get(prev_meta, ["layout_fingerprints", "footer_signature"])
    curr_foot = _safe_get(curr_meta, ["layout_fingerprints", "footer_signature"])
    if prev_head and curr_head and prev_head == curr_head:
        score += 1
    if prev_foot and curr_foot and prev_foot == curr_foot:
        score += 1

    # Fonts overlap
    prev_fonts = set(_safe_get(prev_meta, ["layout_fingerprints", "fonts"]) or [])
    curr_fonts = set(_safe_get(curr_meta, ["layout_fingerprints", "fonts"]) or [])
    if prev_fonts and curr_fonts:
        inter = len(prev_fonts.intersection(curr_fonts))
        denom = max(len(prev_fonts), 1)
        if inter / denom >= 0.5:
            score += 1

    # Identifiers match
    for key in ("account_last4", "policy", "invoice", "statement_id", "case_number"):
        prev_id = _safe_get(prev_meta, ["identifiers", key])
        curr_id = _safe_get(curr_meta, ["identifiers", key])
        if prev_id and curr_id and prev_id == curr_id:
            score += 1
            break

    # Page markers (simple check)
    prev_total = _safe_get(prev_meta, ["page_markers", "total_pages_text"]) or ""
    curr_total = _safe_get(curr_meta, ["page_markers", "total_pages_text"]) or ""
    if prev_total and curr_total and prev_total == curr_total:
        score += 1

    return score >= 2


def _move_page_to_destination(
    *,
    page: Page,
    dest_folder: str,
    dest_filename: str,
    db: Session,
    folder_router: FolderRouter,
    file_manager: FileManager,
    reason: str,
    moved_by: str,
    decision: Optional[ProcessingDecision] = None,
) -> Dict[str, Any]:
    """Move a page's PDF to a new folder/filename and record audit."""
    current_path = folder_router.full_path_for_document(page.assigned_folder, page.output_filename)
    success, dest_path, msg = file_manager.move_file(current_path, dest_folder, dest_filename)
    if not success:
        return {"success": False, "message": msg}

    final_filename = Path(dest_path).name if dest_path else dest_filename
    old_folder = page.assigned_folder
    old_filename = page.output_filename

    page.assigned_folder = dest_folder
    page.output_filename = final_filename
    page.processing_status = "completed"
    page.processing_error = None

    audit = FileMoveAudit(
        page_id=page.id,
        decision_id=decision.id if decision else None,
        old_folder=old_folder,
        old_filename=old_filename,
        new_folder=dest_folder,
        new_filename=final_filename,
        moved_by=moved_by,
        reason=reason,
    )

    try:
        db.add(audit)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise

    return {
        "success": True,
        "destination": dest_path,
        "final_folder": dest_folder,
        "final_filename": final_filename,
        "audit": audit,
        "message": msg,
    }


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    analyze: bool = True,
    dpi: int = 150,
    background: bool = False,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    request: Request = None,
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
                request_id=getattr(getattr(request, 'state', None), 'request_id', None),
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

    prev_meta: Optional[Dict[str, Any]] = None
    current_seq_idx = 1
    current_seq = f"seq-{current_seq_idx}"

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

        # Assign sequence id in extracted metadata
        meta = analysis.get("extracted_metadata", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        if _is_continuation(prev_meta, meta):
            # same sequence
            pass
        else:
            # new sequence
            if idx != 1:
                current_seq_idx += 1
                current_seq = f"seq-{current_seq_idx}"
        meta["sequence_id"] = current_seq
        analysis["extracted_metadata"] = meta
        prev_meta = meta

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
                sequence_id=(analysis.get("extracted_metadata") or {}).get("sequence_id"),
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
    request_id: str | None = None,
) -> None:
    """Background task to process a document end-to-end."""
    from utils import log_context
    if request_id:
        try:
            log_context.set_request_id(request_id)
        except Exception:
            pass
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
        
        # Process with sequence grouping
        prev_meta: Optional[Dict[str, Any]] = None
        current_seq_idx = 1
        current_seq = f"seq-{current_seq_idx}"

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

            # Assign sequence id in extracted metadata
            meta = analysis.get("extracted_metadata", {}) or {}
            if not isinstance(meta, dict):
                meta = {}
            if _is_continuation(prev_meta, meta):
                pass
            else:
                if idx != 1:
                    current_seq_idx += 1
                    current_seq = f"seq-{current_seq_idx}"
            meta["sequence_id"] = current_seq
            analysis["extracted_metadata"] = meta
            prev_meta = meta

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


@router.get("/folder_suggestions", response_model=FolderSuggestionsResponse)
async def get_folder_suggestions():
    folder_router = FolderRouter(settings.documents_dir)
    suggestions = folder_router.get_folder_suggestions()
    structure = folder_router.folder_structure or {}
    return FolderSuggestionsResponse(
        suggestions=[FolderSuggestion(**s) for s in suggestions],
        generated_at=structure.get("generated_at"),
    )


@router.post("/folder_suggestions/refresh", response_model=FolderSuggestionsResponse)
async def refresh_folder_suggestions():
    folder_router = FolderRouter(settings.documents_dir)
    folder_router.analyze_folder_structure()
    suggestions = folder_router.get_folder_suggestions()
    structure = folder_router.folder_structure or {}
    return FolderSuggestionsResponse(
        suggestions=[FolderSuggestion(**s) for s in suggestions],
        generated_at=structure.get("generated_at"),
    )


@router.get("/{document_id}", response_model=DocumentDetails)
async def get_document(document_id: str, db: Session = Depends(get_db)):
    """Fetch a document and its page results."""
    doc: Optional[Document] = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pages = db.query(Page).filter(Page.document_id == document_id).order_by(Page.page_number.asc()).all()
    pages_done = len(pages)
    last_error = None
    last_error_at = None
    for p in reversed(pages):
        if p.processing_error:
            last_error = p.processing_error
            try:
                last_error_at = p.analysis_date.isoformat() if p.analysis_date else None
            except Exception:
                last_error_at = None
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
                sequence_id=(p.extracted_metadata or {}).get("sequence_id"),
                success=(p.processing_status == "completed"),
                error=p.processing_error,
                decision_id=(latest_decision.id if latest_decision else None),
            )
        )

    # Build document-level aggregation summary (in-memory)
    doc_level = _aggregate_document_summary(pages)

    return DocumentDetails(
        document_id=doc.id,
        original_filename=doc.original_filename,
        status=doc.status,
        total_pages=doc.total_pages,
        error_message=doc.error_message,
        pages_done=pages_done,
        last_error=last_error,
        last_error_at=last_error_at,
        pages=page_results,
        doc_level=doc_level,
    )


def _aggregate_document_summary(pages: List[Page]) -> Dict[str, Any]:
    """Aggregate page-level metadata into a doc-level summary.

    Heuristics:
    - Class: majority vote of (extracted.document_type.value || page.document_type)
    - Issuer: most common of (extracted.issuer.name || page.institution)
    - Period: prefer extracted.period.month, else any date found; unify to a single string
    - Identifiers: union of known ids across pages (limit to a few)
    - Confidence: proportion of pages supporting chosen class and issuer (averaged)
    - Proposed filename: prefer extracted.proposed_filename from any page, else generate
    """
    if not pages:
        return {}

    from collections import Counter
    # Tally classes and issuers
    class_counter: Counter = Counter()
    issuer_counter: Counter = Counter()
    months: List[str] = []
    ids_union: Dict[str, Any] = {}
    proposed_filenames: List[str] = []

    def safe_get(d: Dict[str, Any], path: List[str]) -> Any:
        cur = d
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        return cur

    for p in pages:
        meta = p.extracted_metadata or {}
        override = meta.get("doc_level_override") if isinstance(meta, dict) else None
        # class
        override_class = override.get("document_class") if isinstance(override, dict) else None
        cls = (
            override_class
            or safe_get(meta, ["document_type", "value"]) 
            or meta.get("document_type") 
            or (p.document_type or "unknown")
        )
        cls = (cls or "unknown").lower()
        class_counter[cls] += 1
        # issuer
        issuer = safe_get(meta, ["issuer", "name"]) or p.institution or None
        if issuer:
            issuer_counter[issuer.strip()] += 1
        # period
        m = safe_get(meta, ["period", "month"]) or meta.get("date")
        if m:
            months.append(m)
        # identifiers
        ids = meta.get("identifiers")
        if isinstance(ids, dict):
            for k, v in ids.items():
                if v and k not in ids_union:
                    ids_union[k] = v
        # proposed filename
        pf = meta.get("proposed_filename")
        if pf:
            proposed_filenames.append(str(pf))

    total = max(len(pages), 1)
    doc_class, class_count = (class_counter.most_common(1)[0] if class_counter else ("unknown", 0))
    issuer_top, issuer_count = (issuer_counter.most_common(1)[0] if issuer_counter else (None, 0))

    # Confidence: mean of support for class and issuer
    class_conf = class_count / total if total else 0.0
    issuer_conf = issuer_count / total if total else 0.0
    confidence = round((class_conf + issuer_conf) / 2.0, 2)

    period = None
    if months:
        # choose most common month/date
        period = Counter(months).most_common(1)[0][0]

    # Proposed filename fallback
    proposed_filename = proposed_filenames[0] if proposed_filenames else None
    if not proposed_filename:
        try:
            fr = FolderRouter(settings.documents_dir)
            analysis = {"document_type": doc_class, "institution": issuer_top, "date": period}
            proposed_filename = fr.generate_filename(analysis, page_number=1, extension="pdf")
        except Exception:
            proposed_filename = None

    # Salient facts (top few items)
    salient: List[Dict[str, Any]] = []
    if issuer_top:
        salient.append({"label": "Issuer", "value": issuer_top})
    if period:
        salient.append({"label": "Period", "value": period})
    for key in ("invoice", "policy", "case_number", "statement_id", "account_last4"):
        if key in ids_union and len(salient) < 5:
            salient.append({"label": key, "value": ids_union[key]})

    return {
        "document_class": doc_class,
        "confidence": confidence,
        "issuer": issuer_top,
        "recipient": None,  # can be filled from metadata later
        "period": period,
        "identifiers": ids_union,
        "salient_facts": salient,
        "proposed_filename": proposed_filename,
        "rationale": "Aggregated from page-level classifications and issuer mentions",
    }


class ConfirmClassRequest(BaseModel):
    document_class: str
    rationale: Optional[str] = None


@router.post("/{document_id}/confirm_class")
async def confirm_document_class(document_id: str, req: ConfirmClassRequest, db: Session = Depends(get_db)):
    """Confirm/override the document-level class by annotating all pages' metadata.

    Stores an override in each page's extracted_metadata under `doc_level_override`.
    No schema migration needed; aggregator will respect this override.
    """
    pages = db.query(Page).filter(Page.document_id == document_id).all()
    if not pages:
        raise HTTPException(status_code=404, detail="Document not found or has no pages")

    for p in pages:
        meta = p.extracted_metadata or {}
        if not isinstance(meta, dict):
            meta = {}
        meta["doc_level_override"] = {
            "document_class": req.document_class,
            "rationale": req.rationale or "user_confirmed",
        }
        p.extracted_metadata = meta
    db.commit()

    return {"success": True, "document_id": document_id, "document_class": req.document_class}


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

    existing_meta = page.extracted_metadata or {}
    existing_seq = existing_meta.get("sequence_id")

    # Update page with new analysis
    page.document_type = analysis.get("document_type", page.document_type)
    page.institution = analysis.get("institution")
    page.confidence_score = analysis.get("confidence_score", 0.0)
    meta = analysis.get("extracted_metadata", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    if existing_seq and not meta.get("sequence_id"):
        meta["sequence_id"] = existing_seq
    analysis["extracted_metadata"] = meta
    page.extracted_metadata = meta
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
            sequence_id=(page.extracted_metadata or {}).get("sequence_id"),
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
    # Find latest decision for this page
    decision = (
        db.query(ProcessingDecision)
        .filter(ProcessingDecision.page_id == page.id)
        .order_by(ProcessingDecision.timestamp.desc())
        .first()
    )
    move_result = _move_page_to_destination(
        page=page,
        dest_folder=req.folder,
        dest_filename=req.filename,
        db=db,
        folder_router=folder_router,
        file_manager=file_manager,
        reason="user_correction",
        moved_by="user",
        decision=decision,
    )
    if not move_result.get("success"):
        raise HTTPException(status_code=400, detail=move_result.get("message", "move_failed"))

    final_folder = move_result["final_folder"]
    final_filename = move_result["final_filename"]

    learning = LearningEngine(db)
    if decision:
        learning.record_correction(
            decision_id=decision.id,
            actual_folder=final_folder,
            actual_filename=final_filename,
            user_corrected=True,
        )
    else:
        learning.record_decision(
            page_id=page.id,
            proposed_folder=final_folder,
            proposed_filename=final_filename,
            proposed_confidence=page.confidence_score,
            actual_folder=final_folder,
            actual_filename=final_filename,
            user_corrected=True,
        )

    audit = move_result.get("audit")
    return {
        "success": True,
        "message": "Page corrected",
        "destination": move_result.get("destination"),
        "audit_id": audit.id if audit else None,
    }


@router.post("/{document_id}/sequences/{sequence_id}/apply_proposed", response_model=SequenceApplyResponse)
async def apply_sequence_proposed(document_id: str, sequence_id: str, db: Session = Depends(get_db)):
    """Apply proposed folder/filenames for every page in a sequence."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pages = (
        db.query(Page)
        .filter(Page.document_id == document_id)
        .order_by(Page.page_number.asc())
        .all()
    )
    sequence_pages = [
        p for p in pages if (p.extracted_metadata or {}).get("sequence_id") == sequence_id
    ]
    if not sequence_pages:
        raise HTTPException(status_code=404, detail="Sequence not found for document")

    folder_router = FolderRouter(settings.documents_dir)
    file_manager = FileManager(settings.documents_dir)
    learning = LearningEngine(db)

    page_ids = [p.id for p in sequence_pages]
    decisions = (
        db.query(ProcessingDecision)
        .filter(ProcessingDecision.page_id.in_(page_ids))
        .order_by(ProcessingDecision.timestamp.desc())
        .all()
    )
    latest_decisions: Dict[str, ProcessingDecision] = {}
    for dec in decisions:
        if dec.page_id not in latest_decisions:
            latest_decisions[dec.page_id] = dec

    missing: List[int] = []
    errors: List[str] = []
    audit_ids: List[str] = []
    moved = 0

    for page in sequence_pages:
        decision = latest_decisions.get(page.id)
        target_folder = decision.proposed_folder if decision else None
        target_filename = decision.proposed_filename if decision else None
        if not target_folder or not target_filename:
            missing.append(page.page_number)
            continue

        move_result = _move_page_to_destination(
            page=page,
            dest_folder=target_folder,
            dest_filename=target_filename,
            db=db,
            folder_router=folder_router,
            file_manager=file_manager,
            reason="sequence_apply_proposed",
            moved_by="user",
            decision=decision,
        )
        if not move_result.get("success"):
            errors.append(f"{page.page_number}:{move_result.get('message', 'move_failed')}")
            continue

        moved += 1
        audit = move_result.get("audit")
        if audit:
            audit_ids.append(audit.id)

        learning.record_correction(
            decision_id=decision.id,
            actual_folder=move_result["final_folder"],
            actual_filename=move_result["final_filename"],
            user_corrected=False,
        )

    return SequenceApplyResponse(
        document_id=document_id,
        sequence_id=sequence_id,
        pages_total=len(sequence_pages),
        pages_moved=moved,
        missing_proposals=missing,
        errors=errors,
        audit_ids=audit_ids,
    )


@router.post("/pages/{page_id}/moves/revert", response_model=RevertMoveResponse)
async def revert_page_move(page_id: str, req: Optional[RevertMoveRequest] = None, db: Session = Depends(get_db)):
    """Revert the latest (or specified) file move for a page."""
    if req is None:
        req = RevertMoveRequest()
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    query = db.query(FileMoveAudit).filter(FileMoveAudit.page_id == page_id, FileMoveAudit.reverted == False)
    if req.audit_id:
        query = query.filter(FileMoveAudit.id == req.audit_id)
    audit = query.order_by(FileMoveAudit.moved_at.desc()).first()
    if not audit:
        raise HTTPException(status_code=404, detail="No reversible move found for this page")

    folder_router = FolderRouter(settings.documents_dir)
    file_manager = FileManager(settings.documents_dir)
    decision = None
    if audit.decision_id:
        decision = db.query(ProcessingDecision).filter(ProcessingDecision.id == audit.decision_id).first()

    move_result = _move_page_to_destination(
        page=page,
        dest_folder=audit.old_folder,
        dest_filename=audit.old_filename,
        db=db,
        folder_router=folder_router,
        file_manager=file_manager,
        reason="revert_move",
        moved_by="user",
        decision=decision,
    )
    if not move_result.get("success"):
        raise HTTPException(status_code=400, detail=move_result.get("message", "revert_failed"))

    audit.reverted = True
    audit.reverted_at = datetime.utcnow()
    db.commit()

    if decision:
        learning = LearningEngine(db)
        learning.record_correction(
            decision_id=decision.id,
            actual_folder=move_result["final_folder"],
            actual_filename=move_result["final_filename"],
            user_corrected=True,
        )

    return RevertMoveResponse(
        success=True,
        reverted_audit_id=audit.id,
        restored_to={"folder": move_result["final_folder"], "filename": move_result["final_filename"]},
    )


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
        prev_meta: Optional[Dict[str, Any]] = None
        current_seq_idx = 1
        current_seq = f"seq-{current_seq_idx}"

        for idx, p in enumerate(pages, start=1):
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
                meta = analysis.get("extracted_metadata", {}) or {}
                if not isinstance(meta, dict):
                    meta = {}
                if _is_continuation(prev_meta, meta):
                    pass
                else:
                    if idx != 1:
                        current_seq_idx += 1
                        current_seq = f"seq-{current_seq_idx}"
                meta["sequence_id"] = meta.get("sequence_id") or current_seq
                analysis["extracted_metadata"] = meta
                p.extracted_metadata = meta
                prev_meta = meta
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
        prev_meta: Optional[Dict[str, Any]] = None
        current_seq_idx = 1
        current_seq = f"seq-{current_seq_idx}"
        for idx, p in enumerate(pages, start=1):
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
                meta = result.get("extracted_metadata", {}) or {}
                if not isinstance(meta, dict):
                    meta = {}
                if _is_continuation(prev_meta, meta):
                    pass
                else:
                    if idx != 1:
                        current_seq_idx += 1
                        current_seq = f"seq-{current_seq_idx}"
                meta["sequence_id"] = meta.get("sequence_id") or current_seq
                result["extracted_metadata"] = meta
                p.extracted_metadata = meta
                prev_meta = meta
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
