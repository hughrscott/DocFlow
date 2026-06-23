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


@app.middleware("http")
async def no_cache_html_js(request: Request, call_next):
    """Prevent browser caching of HTML and JS files during development."""
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".html", ".js")) or path in ("/", "/review", "/archive", "/settings"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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


@app.get("/api/process/active")
async def active_job():
    """Return the most recent active or completed job."""
    # Find most recent non-error job
    for job_id, state in sorted(_processing_state.items(), reverse=True):
        if state.get("status") in ("starting", "processing"):
            return {"job_id": job_id, "pdf": state.get("pdf"), "status": state["status"]}
    # Return most recent completed job if nothing is active
    for job_id, state in sorted(_processing_state.items(), reverse=True):
        if state.get("status") == "completed":
            return {"job_id": job_id, "pdf": state.get("pdf"), "status": "completed"}
    return {"job_id": None}


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
        last_json = None
        keepalive_counter = 0
        while True:
            state = _processing_state.get(job_id)
            if not state:
                break
            current_json = json.dumps(state, sort_keys=True)
            if current_json != last_json:
                yield {"event": "update", "data": current_json}
                last_json = current_json
                keepalive_counter = 0
            else:
                keepalive_counter += 1
                # Send a keepalive comment every ~3s (10 * 0.3s) to prevent
                # browsers/proxies from silently dropping the connection
                if keepalive_counter >= 10:
                    yield {"comment": "keepalive"}
                    keepalive_counter = 0
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

        # 0. Build dedup index on first run
        from src.filing.dedup import is_empty, build_initial_index
        if is_empty():
            state.update({"step": "Building duplicate index (first run)", "progress": 2, "status": "processing"})
            await asyncio.to_thread(
                build_initial_index,
                _config.get("archive_root", "~/ElectronicFiles"),
            )

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

        # Build document list immediately so it's available even if later steps fail
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
            "step": "Filing documents",
            "progress": 80,
            "auto_filed": len(auto_file),
            "review_queue": len(review_queue),
            "documents": documents,
        })

        # 6. Extract
        state.update({"step": "Writing files", "progress": 85})
        await asyncio.to_thread(extract_documents, pdf_path, auto_file, _config)

        # 7. Summary (non-fatal — cosmetic step)
        state.update({"step": "Generating summary", "progress": 90})
        try:
            await asyncio.to_thread(generate_summary, auto_file, review_queue, _config)
        except Exception as exc:
            logger.warning("Summary generation failed (non-fatal): %s", exc)

        # 8. Archive
        state.update({"step": "Archiving original", "progress": 95})
        archived_path = await asyncio.to_thread(archive_original, pdf_path, _config)

        # If the original also exists in the watch folder (user uploaded a copy),
        # remove it so it doesn't get processed again
        watch_folder = Path(os.path.expanduser(
            _config.get("scan_watch_folder", "~/ElectronicFiles/ToBeOrganized")
        ))
        watch_copy = watch_folder / pdf_path.name
        if watch_copy.exists() and watch_copy != pdf_path:
            watch_copy.unlink()
            logger.info("Removed watch folder copy: %s", watch_copy)

        # Save review queue
        if review_queue:
            save_review_queue(review_queue, archived_path, _config)

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
# API: Unmatched / Skipped Files
# ---------------------------------------------------------------------------

@app.get("/api/unmatched")
async def get_unmatched():
    """List all PDFs in _Unmatched and _Skipped folders."""
    root = _archive_root()
    files = []
    for folder_name in ("_Unmatched", "_Skipped"):
        folder = root / folder_name
        if not folder.exists():
            continue
        for pdf in sorted(folder.glob("*.pdf")):
            try:
                reader = PdfReader(str(pdf))
                page_count = len(reader.pages)
            except Exception:
                page_count = 0
            files.append({
                "name": pdf.name,
                "path": str(pdf),
                "folder": folder_name,
                "size": pdf.stat().st_size,
                "modified": datetime.fromtimestamp(pdf.stat().st_mtime).isoformat(),
                "pages": page_count,
            })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"files": files, "count": len(files)}


@app.post("/api/unmatched/reclassify")
async def reclassify_unmatched(request: Request):
    """Manually reclassify an unmatched/skipped file."""
    body = await request.json()
    source_path = body.get("path", "").strip()
    filename = body.get("filename", "").strip()
    directory = body.get("directory", "").strip()

    if not source_path or not filename or not directory:
        raise HTTPException(400, "path, filename, and directory are required")

    source = Path(source_path)
    if not source.exists():
        raise HTTPException(404, f"File not found: {source_path}")

    target_dir = _archive_root() / directory
    ensure_directory(target_dir)
    target = target_dir / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        counter = 2
        while target.exists():
            target = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.move(str(source), str(target))

    from src.config.learner import record_correction
    record_correction(
        {"suggested_filename": source.name, "suggested_directory": str(source.parent)},
        filename, str(target_dir), _config,
    )

    return {"status": "reclassified", "target": str(target)}


@app.post("/api/unmatched/suggest")
async def suggest_classification(request: Request):
    """Ask the LLM to suggest classification for an unmatched file."""
    body = await request.json()
    source_path = body.get("path", "").strip()
    if not source_path:
        raise HTTPException(400, "path is required")

    source = Path(source_path)
    if not source.exists():
        raise HTTPException(404, f"File not found: {source_path}")

    # OCR the first page
    from pdf2image import convert_from_path
    import pytesseract

    images = await asyncio.to_thread(
        convert_from_path, str(source), first_page=1, last_page=1, dpi=150,
    )
    raw_text = ""
    if images:
        raw_text = await asyncio.to_thread(pytesseract.image_to_string, images[0])

    from src.llm.client import chat_json
    from src.config.rules_manager import load_rules_md

    document_summary = {
        "pages": list(range(1, len(PdfReader(str(source)).pages) + 1)),
        "institution": "unknown",
        "doc_type": "unknown",
        "period": "unknown",
        "account": "unknown",
        "raw_text_preview": raw_text[:500],
    }

    rules_md = load_rules_md(_config)
    if rules_md:
        from src.llm.prompts import build_rules_md_classification_prompt
        prompt = build_rules_md_classification_prompt(
            document_summary,
            rules_md,
            _config.get("entities", []),
            _config.get("family", []),
            _config.get("user", {}),
        )
    else:
        from src.llm.prompts import build_classification_prompt
        prompt = build_classification_prompt(
            document_summary,
            _config.get("filing_rules", []),
            _config.get("entities", []),
            _config.get("family", []),
            _config.get("user", {}),
        )

    try:
        result = await asyncio.to_thread(chat_json, prompt, config=_config)
        return result
    except Exception as exc:
        logger.exception("LLM suggestion failed")
        raise HTTPException(500, f"LLM error: {str(exc)}")


@app.get("/api/preview/file")
async def preview_file(path: str, page: int = 1):
    """Render any PDF page as PNG (for unmatched file preview)."""
    source = Path(path)
    if not source.exists():
        raise HTTPException(404, "File not found")

    # Security: only allow files under archive root
    try:
        source.resolve().relative_to(_archive_root().resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")

    cache_dir = _archive_root() / "_cache" / "previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{source.stem}_{page}.png"
    cached = cache_dir / cache_key
    if cached.exists():
        return FileResponse(str(cached), media_type="image/png")

    from pdf2image import convert_from_path
    images = await asyncio.to_thread(
        convert_from_path, str(source),
        first_page=page, last_page=page, dpi=150,
    )
    if not images:
        raise HTTPException(500, "Failed to render page")

    await asyncio.to_thread(images[0].save, str(cached), "PNG")
    return FileResponse(str(cached), media_type="image/png")


# ---------------------------------------------------------------------------
# API: Page Preview
# ---------------------------------------------------------------------------

@app.get("/api/preview/{item_id}/{page_num}")
async def preview_page(item_id: str, page_num: int):
    """Render a single PDF page as a PNG thumbnail for the review UI."""
    items = load_review_queue(_config)
    item = next((i for i in items if i["id"] == item_id), None)
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")
    if page_num not in item["pages"]:
        raise HTTPException(400, f"Page {page_num} not in this item")

    source = Path(item["source_pdf"])
    if not source.exists():
        raise HTTPException(404, "Source PDF not found")

    # Cache rendered pages to avoid repeated conversions
    cache_dir = _archive_root() / "_cache" / "previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{source.stem}_{page_num}.png"
    cached = cache_dir / cache_key
    if cached.exists():
        return FileResponse(str(cached), media_type="image/png")

    from pdf2image import convert_from_path
    images = await asyncio.to_thread(
        convert_from_path, str(source),
        first_page=page_num, last_page=page_num, dpi=150,
    )
    if not images:
        raise HTTPException(500, "Failed to render page")

    await asyncio.to_thread(images[0].save, str(cached), "PNG")
    return FileResponse(str(cached), media_type="image/png")


# ---------------------------------------------------------------------------
# API: Search
# ---------------------------------------------------------------------------

@app.get("/api/search")
async def search_archive(q: str = ""):
    """Search filing logs and archived filenames."""
    query = q.strip().lower()
    if len(query) < 2:
        return {"results": []}

    results = []
    root = _archive_root()

    # Search filing logs
    for log_file in root.glob("filing_log_*.json"):
        try:
            with open(log_file) as fh:
                data = json.load(fh)
            for entry in data.get("entries", []):
                filename = entry.get("filename", "")
                directory = entry.get("target_directory", "")
                rule = entry.get("rule_matched", "")
                if query in filename.lower() or query in directory.lower() or query in rule.lower():
                    results.append({
                        "filename": filename,
                        "directory": "/".join(directory.split("/")[-2:]) if "/" in directory else directory,
                        "confidence": entry.get("confidence"),
                        "rule": rule,
                        "icon": "description",
                        "url": f"/archive",
                    })
        except (json.JSONDecodeError, KeyError):
            continue

    # Search archive files by name
    if root.exists():
        for pdf in root.rglob("*.pdf"):
            if pdf.name.startswith(".") or "/_" in str(pdf):
                continue
            if query in pdf.name.lower():
                # Avoid duplicates from log search
                if not any(r["filename"] == pdf.name for r in results):
                    rel = str(pdf.relative_to(root))
                    results.append({
                        "filename": pdf.name,
                        "directory": str(pdf.parent.relative_to(root)),
                        "icon": "folder_open",
                        "url": f"/archive",
                    })

    # Limit results
    return {"results": results[:20]}


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


@app.get("/api/settings/rules-md")
async def get_rules_md():
    """Return the rules.md content for viewing/editing."""
    from src.config.rules_manager import load_rules_md, rules_path
    content = load_rules_md(_config)
    return {"content": content, "path": str(rules_path(_config))}


@app.post("/api/settings/rules-md")
async def update_rules_md(request: Request):
    """Save updated rules.md content."""
    from src.config.rules_manager import rules_path
    body = await request.json()
    content = body.get("content", "")
    path = rules_path(_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {"status": "saved", "path": str(path)}


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


@app.post("/api/settings/test-connection")
async def test_connection():
    """Test LLM connectivity with a trivial prompt."""
    try:
        from src.llm.client import _get_client
        client = _get_client(_config)
        # Send a trivial prompt to verify connectivity
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=_config.get("llm_model") or _config.get("openrouter_model", "google/gemini-2.0-flash-001"),
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=5,
        )
        reply = response.choices[0].message.content.strip() if response.choices else ""
        return {"status": "connected", "reply": reply, "model": response.model}
    except Exception as exc:
        return JSONResponse(
            status_code=200,
            content={"status": "error", "error": str(exc)},
        )


@app.post("/api/settings/validate-paths")
async def validate_paths():
    """Validate that configured paths exist and are writable."""
    results = {}
    for key, label in [("archive_root", "Archive Root"), ("scan_watch_folder", "Watch Folder")]:
        path_str = _config.get(key, "")
        if not path_str:
            results[key] = {"status": "not_set", "label": label}
            continue
        path = Path(os.path.expanduser(path_str))
        if not path.exists():
            results[key] = {"status": "missing", "label": label, "path": str(path)}
        elif not os.access(str(path), os.W_OK):
            results[key] = {"status": "not_writable", "label": label, "path": str(path)}
        else:
            results[key] = {"status": "ok", "label": label, "path": str(path)}
    return results


# ---------------------------------------------------------------------------
# API: Re-process
# ---------------------------------------------------------------------------

@app.post("/api/reprocess/{job_id}")
async def reprocess_job(job_id: str):
    """Re-process a previously completed job's PDF."""
    state = _processing_state.get(job_id)
    if not state:
        raise HTTPException(404, "Job not found")
    pdf_path = state.get("pdf")
    if not pdf_path:
        raise HTTPException(400, "No PDF path for this job")

    # The original might have been archived — check BeenOrganized folders
    source = Path(pdf_path)
    if not source.exists():
        # Search in BeenOrganized folders
        watch_folder = Path(os.path.expanduser(
            _config.get("scan_watch_folder", "~/ElectronicFiles/ToBeOrganized")
        ))
        for been_dir in watch_folder.glob("BeenOrganized*"):
            candidate = been_dir / source.name
            if candidate.exists():
                source = candidate
                break
        if not source.exists():
            raise HTTPException(404, f"PDF not found: {source.name}")

    # Copy back to uploads for reprocessing
    upload_path = _upload_dir() / source.name
    shutil.copy2(str(source), str(upload_path))

    # Start new job
    new_job_id = str(uuid.uuid4())[:8]
    _processing_state[new_job_id] = {
        "status": "starting",
        "pdf": str(upload_path),
        "progress": 0,
        "step": "Initializing (re-process)",
        "documents": [],
        "auto_filed": 0,
        "review_queue": 0,
        "started": datetime.now().isoformat(),
    }
    asyncio.create_task(_run_pipeline_async(new_job_id, upload_path))
    return {"job_id": new_job_id}
