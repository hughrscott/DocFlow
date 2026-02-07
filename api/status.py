"""
Status and health endpoints for DocFlow.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, case
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
    pipeline_version: int = 1
    sub_document_count: Optional[int] = None
    pages_done: int = 0
    pages_failed: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[str] = None
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
        doc_ids = [d.id for d in docs]

        stats_map: Dict[str, Dict[str, int]] = {doc_id: {"processed": 0, "failed": 0} for doc_id in doc_ids}
        if doc_ids:
            counts = (
                db.query(
                    Page.document_id.label("doc_id"),
                    func.count(Page.id).label("processed"),
                    func.sum(
                        case(
                            (Page.processing_status == "failed", 1),
                            else_=0,
                        )
                    ).label("failed"),
                )
                .filter(Page.document_id.in_(doc_ids))
                .group_by(Page.document_id)
                .all()
            )
            for row in counts:
                stats_map[row.doc_id] = {
                    "processed": int(row.processed or 0),
                    "failed": int(row.failed or 0),
                }

        error_map: Dict[str, Dict[str, Optional[str]]] = {}
        if doc_ids:
            error_rows = (
                db.query(Page.document_id, Page.processing_error, Page.analysis_date)
                .filter(Page.document_id.in_(doc_ids))
                .filter(Page.processing_error.isnot(None))
                .order_by(Page.document_id.asc(), Page.analysis_date.desc())
                .all()
            )
            for row in error_rows:
                doc_id = row.document_id
                if doc_id in error_map:
                    continue
                timestamp = row.analysis_date.isoformat() if row.analysis_date else None
                error_map[doc_id] = {"message": row.processing_error, "timestamp": timestamp}

        for d in docs:
            stats = stats_map.get(d.id, {"processed": 0, "failed": 0})
            err = error_map.get(d.id, {})
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
                    pipeline_version=d.pipeline_version or 1,
                    sub_document_count=d.sub_document_count,
                    pages_done=stats["processed"],
                    pages_failed=stats["failed"],
                    last_error=err.get("message"),
                    last_error_at=err.get("timestamp"),
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


class ProviderInfo(BaseModel):
    name: str
    enabled: bool
    configured: bool
    reachable: bool
    details: Dict[str, Any] = {}


class ProvidersReadinessResponse(BaseModel):
    vision_provider: str
    text_provider: str
    providers: Dict[str, ProviderInfo]


@router.get("/providers/readiness", response_model=ProvidersReadinessResponse)
async def providers_readiness():
    """Report configured providers and live readiness checks.

    Non-fatal: if config missing or health checks fail, returns reachable=false for those providers.
    """
    # Load config (may raise; handle gracefully)
    vision = "unknown"
    text = "unknown"
    providers_out: Dict[str, ProviderInfo] = {}
    try:
        manager = LLMManager(settings.llm_config_path)
        cfg = manager.config or {}
        llm_cfg = cfg.get("llm", {})
        vision = llm_cfg.get("vision_provider", "unknown")
        text = llm_cfg.get("text_provider", "unknown")
        prov_cfg = cfg.get("providers", {})

        # For each known provider, summarize
        for name, pconf in prov_cfg.items():
            enabled = bool(pconf.get("enabled", False))
            configured = True
            details: Dict[str, Any] = {}
            if name == "claude":
                details = {"model": pconf.get("vision_model"), "has_api_key": bool(pconf.get("api_key"))}
                configured = bool(pconf.get("api_key"))
            elif name == "ollama":
                details = {"base_url": pconf.get("base_url"), "vision_model": pconf.get("vision_model"), "text_model": pconf.get("text_model")}
                configured = bool(pconf.get("base_url"))

            reachable = False
            try:
                if name in manager.providers:
                    reachable = await manager.providers[name].health_check()
            except Exception:
                reachable = False

            providers_out[name] = ProviderInfo(
                name=name,
                enabled=enabled,
                configured=configured,
                reachable=reachable,
                details=details,
            )
    except Exception:
        # No config present; return empty map
        providers_out = {}

    return ProvidersReadinessResponse(
        vision_provider=vision,
        text_provider=text,
        providers=providers_out,
    )
