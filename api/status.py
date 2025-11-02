"""
Status and health endpoints for DocFlow.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from database.database import get_db
from database.models import Document, Page
from config.settings import settings
from llm.llm_manager import LLMManager

router = APIRouter(prefix="/api/v1", tags=["status"]) 


class HealthResponse(BaseModel):
    status: str
    time: str
    database_ok: bool
    total_documents: int
    active_providers: Dict[str, str]


@router.get("/health", response_model=HealthResponse)
async def health(db: Session = Depends(get_db)):
    """Basic healthcheck with DB and provider info."""
    # DB check: simple count query
    try:
        total_documents = db.query(func.count(Document.id)).scalar() or 0
        database_ok = True
    except Exception:
        total_documents = 0
        database_ok = False

    # Provider info (non-blocking)
    active_providers: Dict[str, str] = {}
    try:
        manager = LLMManager(settings.llm_config_path)
        active_providers = manager.get_active_providers()
    except Exception:
        active_providers = {}

    return HealthResponse(
        status="ok",
        time=datetime.utcnow().isoformat(),
        database_ok=database_ok,
        total_documents=total_documents,
        active_providers=active_providers,
    )


class PageBrief(BaseModel):
    page_number: int
    document_type: str
    confidence_score: float


class DocumentBrief(BaseModel):
    id: str
    original_filename: str
    upload_date: str
    total_pages: int
    status: str
    sample_pages: List[PageBrief] = []


class DocumentsListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[DocumentBrief]


@router.get("/documents", response_model=DocumentsListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List recent documents with brief info and a few page samples."""
    total = db.query(func.count(Document.id)).scalar() or 0
    items: List[DocumentBrief] = []

    if total > 0:
        docs = (
            db.query(Document)
            .order_by(Document.upload_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        for d in docs:
            pages = (
                db.query(Page)
                .filter(Page.document_id == d.id)
                .order_by(Page.page_number.asc())
                .limit(3)
                .all()
            )
            items.append(
                DocumentBrief(
                    id=d.id,
                    original_filename=d.original_filename,
                    upload_date=d.upload_date.isoformat() if d.upload_date else "",
                    total_pages=d.total_pages,
                    status=d.status,
                    sample_pages=[
                        PageBrief(
                            page_number=p.page_number,
                            document_type=p.document_type,
                            confidence_score=p.confidence_score,
                        )
                        for p in pages
                    ],
                )
            )

    return DocumentsListResponse(total=total, page=page, page_size=page_size, items=items)

