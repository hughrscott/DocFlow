"""DocFlow web application: FastAPI backend for the full UI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader, PdfWriter
from sse_starlette.sse import EventSourceResponse

from src.review.queue import load_review_queue, update_queue_item, save_review_queue
from src.filing.filer import ensure_directory

logger = logging.getLogger(__name__)

app = FastAPI(title="DocFlow — The Digital Archivist")

# Config and state
_config: dict = {}
_processing_state: dict = {}  # Active processing state for SSE

# Mount static files
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


def configure(config: dict) -> None:
    """Set the config for the web app."""
    global _config
    _config = config


def _archive_root() -> Path:
    return Path(os.path.expanduser(_config.get("archive_root", "~/ElectronicFiles")))


def _upload_dir() -> Path:
    d = _archive_root() / "_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return FileResponse(str(_static_dir / "dashboard.html"))


@app.get("/review", response_class=HTMLResponse)
async def review_page():
    return FileResponse(str(_static_dir / "review.html"))


@app.get("/archive", response_class=HTMLResponse)
async def archive_page():
    return FileResponse(str(_static_dir / "archive.html"))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return FileResponse(str(_static_dir / "settings.html"))


# ---------------------------------------------------------------------------
# API: Upload & Process
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF for processing."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    upload_path = _upload_dir() / file.filename
    with open(upload_path, "wb") as f:
        content = await file.read()
        f.write(content)

    return {"filename": file.filename, "path": str(upload_path), "size": len(content)}


@app.post("/api/process")
async def process_pdf(request: Request):
    """Start processing a PDF. Returns immediately with a job ID.
    Use /api/process/status/{job_id} to poll for progress."""
    body = await request.json()
    pdf_path = body.get("path")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(400, f"PDF not found: {pdf_path}")

    job_id = str(uuid.uuid4())[:8]
    _processing_state[job_id] = {
        "status": "starting",
        "pdf": pdf_path,
        "progress": 0,
        "step": "Initializing",
        "documents": [],
        "auto_filed": 0,
        "review_queue": 0,
        "started": datetime.now().isoformat(),
    }

    # Run pipeline in background
    asyncio.create_task(_run_pipeline_async(job_id, Path(pdf_path)))

    return {"job_id": job_id}


@app.get("/api/process/status/{job_id}")
async def process_status(job_id: str):
    """Get processing status for a job."""
    state = _processing_state.get(job_id)
    if not state:
        raise HTTPException(404, "Job not found")
    return state


@app.get("/api/process/stream/{job_id}")
async def process_stream(job_id: str):
    """SSE stream for live processing updates."""
    async def event_generator():
        last_status = None
        while True:
            state = _processing_state.get(job_id)
            if not state:
                break
            if state != last_status:
                yield {"event": "update", "data": json.dumps(state)}
                last_status = state.copy()
            if state.get("status") in ("completed", "error"):
                break
            await asyncio.sleep(0.3)

    return EventSourceResponse(event_generator())


async def _run_pipeline_async(job_id: str, pdf_path: Path) -> None:
    """Run the processing pipeline, updating state as we go."""
    import yaml
    state = _processing_state[job_id]

    try:
        from src.ingestion.loader import load_pdf
        from src.ocr.analyzer import analyze_pages
        from src.clustering.clusterer import cluster_pages
        from src.classification.classifier import classify_candidates
        from src.filing.confidence_gate import gate_decisions
        from src.extraction.extractor import extract_documents
        from src.summary.generator import generate_summary
        from src.ingestion.archiver import archive_original

        # 1. Ingestion
        state.update({"step": "Loading PDF", "progress": 5, "status": "processing"})
        page_images = await asyncio.to_thread(load_pdf, pdf_path)
        state.update({"step": f"Loaded {len(page_images)} pages", "progress": 15})

        # 2. OCR
        state.update({"step": "Running OCR", "progress": 20})
        page_records = await asyncio.to_thread(analyze_pages, page_images)
        state.update({"step": "OCR complete", "progress": 40})

        # 3. Clustering
        state.update({"step": "Clustering documents (AI)", "progress": 45})
        candidates = await asyncio.to_thread(cluster_pages, page_records, _config)
        state.update({"step": f"Found {len(candidates)} documents", "progress": 60})

        # 4. Classification
        state.update({"step": "Classifying documents", "progress": 65})
        decisions = await asyncio.to_thread(classify_candidates, candidates, _config)
        state.update({"progress": 75})

        # 5. Confidence gate
        auto_file, review_queue = gate_decisions(decisions, _config)
        state.update({
            "step": "Filing documents",
            "progress": 80,
            "auto_filed": len(auto_file),
            "review_queue": len(review_queue),
        })

        # 6. Extract
        state.update({"step": "Writing files", "progress": 85})
        await asyncio.to_thread(extract_documents, pdf_path, auto_file, _config)

        # 7. Summary
        state.update({"step": "Generating summary", "progress": 90})
        await asyncio.to_thread(generate_summary, auto_file, review_queue, _config)

        # 8. Archive
        state.update({"step": "Archiving original", "progress": 95})
        archived_path = await asyncio.to_thread(archive_original, pdf_path, _config)

        # Save review queue
        if review_queue:
            save_review_queue(review_queue, archived_path, _config)

        # Build document list for UI
        documents = []
        for d in auto_file + review_queue:
            documents.append({
                "filename": d.filename,
                "directory": d.target_directory,
                "institution": d.candidate.institution,
                "doc_type": d.candidate.doc_type,
                "period": d.candidate.period,
                "pages": d.candidate.pages,
                "rule": d.rule_matched,
                "confidence": d.confidence,
                "auto_filed": d.auto_file,
                "notes": d.notes,
                "reasoning": d.candidate.raw_signals.get("llm_reasoning", ""),
            })

        state.update({
            "status": "completed",
            "step": "Done",
            "progress": 100,
            "documents": documents,
        })

    except Exception as exc:
        logger.exception("Pipeline error for job %s", job_id)
        state.update({
            "status": "error",
            "step": f"Error: {str(exc)[:200]}",
            "progress": state.get("progress", 0),
        })


# ---------------------------------------------------------------------------
# API: Review Queue
# ---------------------------------------------------------------------------

@app.get("/api/queue")
async def get_queue():
    items = load_review_queue(_config)
    return {"pending": len(items), "items": items}


@app.post("/api/queue/approve/{item_id}")
async def approve_item(item_id: str):
    item = update_queue_item(_config, item_id, {"status": "approved"})
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")
    _extract_review_item(item, item["suggested_filename"], item["suggested_directory"])
    return {"status": "approved"}


@app.post("/api/queue/correct/{item_id}")
async def correct_item(item_id: str, request: Request):
    body = await request.json()
    filename = body.get("filename", "").strip()
    directory = body.get("directory", "").strip()
    if not filename or not directory:
        raise HTTPException(400, "Both filename and directory required")

    target_dir = str(_archive_root() / directory)
    item = update_queue_item(_config, item_id, {
        "status": "corrected",
        "corrected_filename": filename,
        "corrected_directory": target_dir,
    })
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")
    _extract_review_item(item, filename, target_dir)

    from src.config.learner import record_correction
    record_correction(item, filename, target_dir, _config)

    return {"status": "corrected"}


@app.post("/api/queue/skip/{item_id}")
async def skip_item(item_id: str):
    item = update_queue_item(_config, item_id, {"status": "skipped"})
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")
    holding = str(_archive_root() / "_Skipped")
    _extract_review_item(item, item["suggested_filename"], holding)
    return {"status": "skipped"}


def _extract_review_item(item: dict, filename: str, target_dir: str) -> None:
    source = Path(item["source_pdf"])
    if not source.exists():
        logger.warning("Source PDF not found: %s", source)
        return
    target_path = Path(target_dir)
    ensure_directory(target_path)
    output = target_path / filename
    if output.exists():
        stem, suffix = output.stem, output.suffix
        counter = 2
        while output.exists():
            output = target_path / f"{stem}_{counter}{suffix}"
            counter += 1

    reader = PdfReader(str(source))
    writer = PdfWriter()
    for page_num in item["pages"]:
        writer.add_page(reader.pages[page_num - 1])
    with open(output, "wb") as f:
        writer.write(f)


# ---------------------------------------------------------------------------
# API: Archive Browser
# ---------------------------------------------------------------------------

@app.get("/api/archive/tree")
async def archive_tree():
    """Return the archive folder tree."""
    root = _archive_root()
    if not root.exists():
        return {"tree": []}

    def _walk(path: Path, depth: int = 0) -> list[dict]:
        if depth > 4:
            return []
        items = []
        try:
            for child in sorted(path.iterdir()):
                if child.name.startswith(".") or child.name.startswith("_"):
                    continue
                if child.is_dir():
                    children = _walk(child, depth + 1)
                    pdf_count = sum(1 for _ in child.rglob("*.pdf"))
                    items.append({
                        "name": child.name,
                        "path": str(child.relative_to(root)),
                        "type": "folder",
                        "children": children,
                        "pdf_count": pdf_count,
                    })
        except PermissionError:
            pass
        return items

    return {"tree": _walk(root)}


@app.get("/api/archive/files")
async def archive_files(path: str = ""):
    """List files in a given archive path."""
    root = _archive_root()
    target = root / path if path else root
    if not target.exists():
        raise HTTPException(404, "Path not found")

    files = []
    for f in sorted(target.iterdir()):
        if f.is_file() and f.suffix.lower() == ".pdf":
            files.append({
                "name": f.name,
                "path": str(f.relative_to(root)),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
    return {"files": files, "path": path}


@app.get("/api/archive/logs")
async def archive_logs():
    """Return all filing logs."""
    root = _archive_root()
    logs = []
    for f in sorted(root.glob("filing_log_*.json"), reverse=True):
        with open(f) as fh:
            data = json.load(fh)
            logs.append(data)
    return {"logs": logs}


# ---------------------------------------------------------------------------
# API: Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def get_settings():
    """Return current config (mask sensitive data)."""
    safe = dict(_config)
    # Mask API key — just indicate if one is set
    if safe.get("llm_api_key"):
        safe["llm_api_key"] = "••••••••"
    safe.pop("openrouter_api_key", None)
    return safe


@app.post("/api/settings")
async def update_settings(request: Request):
    """Update config values."""
    body = await request.json()
    allowed = {
        "confidence_threshold", "archive_root", "scan_watch_folder",
        "llm_provider", "llm_model", "llm_base_url", "llm_api_key",
        "openrouter_model",
    }
    for key in body:
        if key in allowed:
            _config[key] = body[key]
    # Keep openrouter_model in sync with llm_model for backwards compat
    if "llm_model" in body:
        _config["openrouter_model"] = body["llm_model"]
    return {"status": "updated"}


@app.get("/api/settings/rules")
async def get_rules():
    return {"rules": _config.get("filing_rules", [])}


@app.get("/api/settings/entities")
async def get_entities():
    return {
        "entities": _config.get("entities", []),
        "family": _config.get("family", []),
        "user": _config.get("user", {}),
    }


@app.get("/api/health")
async def health():
    """System health check."""
    api_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = bool(os.environ.get("OPENROUTER_API_KEY"))
        except ImportError:
            pass

    return {
        "watch_folder": _config.get("scan_watch_folder", ""),
        "archive_root": _config.get("archive_root", ""),
        "llm_ready": api_key,
        "model": _config.get("openrouter_model", "google/gemini-2.0-flash-001"),
        "threshold": _config.get("confidence_threshold", 0.75),
    }
