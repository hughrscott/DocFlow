"""DocFlow web application: FastAPI backend for the full UI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader, PdfWriter
from sse_starlette.sse import EventSourceResponse

from docflow.filing.filer import ensure_directory
from docflow.review.queue import load_review_queue, save_review_queue, update_queue_item

logger = logging.getLogger(__name__)

app = FastAPI(title="DocFlow — The Digital Archivist")

# Config and state
_config: dict = {}
_processing_state: dict = {}  # Active processing state for SSE
_state_store = None  # docflow.state.repositories.StateStore backing /api/v1
_filer = None  # docflow.filing.operations.DurableFiler backing job retry

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


def configure(config: dict, config_path: Path | None = None) -> None:
    """Set the config for the web app."""
    global _config, _config_path
    _config = config
    if config_path:
        _config_path = config_path


_config_path: Path | None = None


def configure_state(store, filer=None) -> None:
    """Attach the local application-state store (and durable filer) used by /api/v1 routes."""
    global _state_store, _filer
    _state_store = store
    _filer = filer


def _persist_config() -> None:
    """Write current config back to the YAML file so changes survive restart."""
    if not _config_path or not _config_path.exists():
        return
    try:
        # Read existing file to preserve comments and structure
        with open(_config_path) as f:
            existing = yaml.safe_load(f) or {}

        # Update scalar settings
        persist_keys = {
            "confidence_threshold", "archive_root", "scan_watch_folder",
            "llm_provider", "llm_model", "llm_base_url", "llm_api_key", "privacy_mode",
        }
        for key in persist_keys:
            if key in _config and _config[key] not in ("", "••••••••", None):
                existing[key] = _config[key]

        # Also persist structured data (entities, family, filing_rules, user)
        for key in ("entities", "family", "filing_rules", "user"):
            if key in _config:
                existing[key] = _config[key]

        with open(_config_path, "w") as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        logger.info("Settings persisted to %s", _config_path)
    except Exception as exc:  # noqa: BLE001 - legacy best-effort persist; settings save must not fail
        logger.warning("Failed to persist config: %s", exc)


def _local_iso(timestamp: float | None = None) -> str:
    """Naive local-time ISO string (the legacy UI format), derived from an aware time."""
    moment = datetime.now(UTC) if timestamp is None else datetime.fromtimestamp(timestamp, UTC)
    return moment.astimezone().replace(tzinfo=None).isoformat()


def _archive_root() -> Path:
    return Path(os.path.expanduser(_config.get("archive_root", "~/DocFlowExample/archive")))


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
async def upload_pdf(file: Annotated[UploadFile, File()]):
    """Upload a PDF for processing."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    upload_path = _upload_dir() / file.filename
    content = await file.read()
    await asyncio.to_thread(upload_path.write_bytes, content)

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
        "started": _local_iso(),
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
    state = _processing_state[job_id]

    try:
        from docflow.classification.classifier import classify_candidates
        from docflow.clustering.clusterer import cluster_pages
        from docflow.extraction.extractor import extract_documents
        from docflow.filing.confidence_gate import gate_decisions
        from docflow.filing.dedup import build_initial_index, is_empty
        from docflow.ingestion.archiver import archive_original
        from docflow.ingestion.loader import load_pdf
        from docflow.ocr.analyzer import analyze_pages
        from docflow.summary.generator import generate_summary

        # 0. Build dedup index on first run
        if is_empty():
            state.update({"step": "Building duplicate index (first run)", "progress": 2, "status": "processing"})
            await asyncio.to_thread(
                build_initial_index,
                _config.get("archive_root", "~/DocFlowExample/archive"),
            )

        # 1. Ingestion
        state.update({"step": "Loading PDF", "progress": 5, "status": "processing"})
        page_images = await asyncio.to_thread(load_pdf, pdf_path)
        state.update({"step": f"Loaded {len(page_images)} pages", "progress": 15})

        # 2. OCR
        state.update({"step": "Running OCR", "progress": 20})
        page_records = await asyncio.to_thread(analyze_pages, page_images)
        state.update({"step": "OCR complete", "progress": 40})

        # One gateway per job: placeholders are consistent within this scan only.
        from docflow.llm.gateway import CloudPromptGateway
        gateway = CloudPromptGateway(_config)

        # 3. Clustering
        state.update({"step": "Clustering documents (AI)", "progress": 45})
        candidates = await asyncio.to_thread(cluster_pages, page_records, _config, gateway)
        state.update({"step": f"Found {len(candidates)} documents", "progress": 60})

        # 4. Classification
        state.update({"step": "Classifying documents", "progress": 65})
        decisions = await asyncio.to_thread(classify_candidates, candidates, _config, gateway)
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
        except Exception as exc:  # noqa: BLE001 - legacy cosmetic step, documented as non-fatal
            logger.warning("Summary generation failed (non-fatal): %s", exc)

        # 8. Archive
        state.update({"step": "Archiving original", "progress": 95})
        archived_path = await asyncio.to_thread(archive_original, pdf_path, _config)

        # If the watch folder holds a byte-identical copy of the retained original,
        # remove it so it isn't processed again. A different file is never removed.
        from docflow.ingestion.loader import raw_sha256

        watch_folder = Path(os.path.expanduser(
            _config.get("scan_watch_folder", "~/DocFlowExample/inbox")
        ))
        watch_copy = watch_folder / pdf_path.name
        if (watch_copy.is_file() and not watch_copy.is_symlink() and watch_copy != pdf_path
                and watch_copy != archived_path
                and raw_sha256(watch_copy) == raw_sha256(archived_path)):
            watch_copy.unlink()
            logger.info("Removed identical watch folder copy: %s", watch_copy.name)

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

    from docflow.config.learner import record_correction
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
            except Exception:  # noqa: BLE001 - legacy listing shows 0 pages for unreadable PDFs
                page_count = 0
            files.append({
                "name": pdf.name,
                "path": str(pdf),
                "folder": folder_name,
                "size": pdf.stat().st_size,
                "modified": _local_iso(pdf.stat().st_mtime),
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

    from docflow.config.learner import record_correction
    record_correction(
        {"suggested_filename": source.name, "suggested_directory": str(source.parent)},
        filename, str(target_dir), _config,
    )

    return {"status": "reclassified", "target": str(target)}


def _confined_archive_pdf(roots: list[Path], candidate: Path) -> Path:
    """Resolve a PDF beneath one of ``roots``, rejecting traversal and symlink escape."""
    resolved = candidate.resolve()
    inside = any(root.resolve() in resolved.parents for root in roots)
    if not inside or candidate.is_symlink():
        raise HTTPException(403, "Access denied")
    if not resolved.is_file():
        raise HTTPException(404, "File not found")
    if resolved.suffix.lower() != ".pdf":
        raise HTTPException(400, "Only PDF files are supported")
    return resolved


def _ocr_first_page(source: Path) -> tuple[str, int]:
    """Local OCR of page 1. Images never leave this function."""
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(str(source), first_page=1, last_page=1, dpi=150)
    raw_text = pytesseract.image_to_string(images[0]) if images else ""
    return raw_text, len(PdfReader(str(source)).pages)


def _suggest_filing(gateway, raw_text: str, page_count: int, feature) -> dict:
    """Validated filing suggestion for one local PDF via CloudPromptGateway."""
    from docflow.classification.classifier import _extract_year, _generate_filename
    from docflow.clustering.clusterer import DocumentCandidate
    from docflow.config.rules_manager import load_rules_md, parse_rules_md
    from docflow.llm.gateway import LocalRule
    from docflow.llm.schemas import (
        InvalidModelOutput,
        validate_filename,
        validate_relative_directory,
    )

    rules_md = load_rules_md(_config)
    rules = ([LocalRule.from_rules_md(r) for r in parse_rules_md(rules_md)] if rules_md
             else [LocalRule.from_yaml(r) for r in _config.get("filing_rules", [])])
    document = {"pages": list(range(1, page_count + 1)), "text_preview": raw_text[:500]}
    result = gateway.classify(document, rules, feature=feature)
    filename, directory = result.filename, result.relative_directory
    rule = result.rule
    if rule is not None and rule.file_to and rule.filename_template:
        candidate = DocumentCandidate(document["pages"], "unknown", None, result.period,
                                      result.doc_type, 0.0, {})
        try:
            filename = validate_filename(_generate_filename(rule.filename_template, candidate))
            directory = gateway.confine(validate_relative_directory(
                rule.file_to.format(year=_extract_year(result.period))
                if "{year}" in rule.file_to else rule.file_to))
        except (KeyError, IndexError, ValueError, InvalidModelOutput):
            filename = directory = None
    return {
        "rule_matched": rule.key if rule else None,
        "suggested_filename": filename,
        "suggested_directory": directory,
        "doc_type": result.doc_type,
        "period": result.period,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
    }


def _refusal_status(exc) -> int:
    from docflow.llm.gateway import LocalOnlyMode, TransportFailure

    if isinstance(exc, LocalOnlyMode):
        return 409
    if isinstance(exc, TransportFailure):
        return 502
    return 422


@app.post("/api/unmatched/suggest")
async def suggest_classification(request: Request):
    """Ask the model (through CloudPromptGateway) to suggest filing for an archive PDF."""
    from docflow.llm.gateway import CloudPromptGateway, Feature
    from docflow.privacy.types import NoModelResult

    body = await request.json()
    source_path = body.get("path", "").strip()
    if not source_path:
        raise HTTPException(400, "path is required")
    watch_folder = Path(os.path.expanduser(
        _config.get("scan_watch_folder", "~/DocFlowExample/inbox")
    ))
    source = _confined_archive_pdf([_archive_root(), watch_folder], Path(source_path))

    gateway = CloudPromptGateway(_config)
    if gateway.local_only:
        raise HTTPException(409, {"code": "local_only"})
    try:
        raw_text, page_count = await asyncio.to_thread(_ocr_first_page, source)
        return await asyncio.to_thread(_suggest_filing, gateway, raw_text, page_count,
                                       Feature.UNMATCHED_SUGGESTION)
    except NoModelResult as exc:
        raise HTTPException(_refusal_status(exc), {"code": exc.code}) from None


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
            data = json.loads(await asyncio.to_thread(log_file.read_text))
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
                        "url": "/archive",
                    })
        except (json.JSONDecodeError, KeyError):
            continue

    # Search archive files by name
    if root.exists():
        for pdf in root.rglob("*.pdf"):
            if pdf.name.startswith(".") or "/_" in str(pdf):
                continue
            # Avoid duplicates from log search
            if query in pdf.name.lower() and not any(
                r["filename"] == pdf.name for r in results
            ):
                results.append({
                    "filename": pdf.name,
                    "directory": str(pdf.parent.relative_to(root)),
                    "icon": "folder_open",
                    "url": "/archive",
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
                "modified": _local_iso(f.stat().st_mtime),
            })
    return {"files": files, "path": path}


@app.get("/api/archive/logs")
async def archive_logs():
    """Return all filing logs."""
    root = _archive_root()
    logs = []
    for f in sorted(root.glob("filing_log_*.json"), reverse=True):
        logs.append(json.loads(await asyncio.to_thread(f.read_text)))
    return {"logs": logs}


# ---------------------------------------------------------------------------
# API: Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def get_settings():
    """Return current config (mask sensitive data) plus the privacy mode and warning."""
    from docflow.llm.gateway import PSEUDONYMIZATION_WARNING

    safe = dict(_config)
    # Mask API key — just indicate if one is set
    if safe.get("llm_api_key"):
        safe["llm_api_key"] = "••••••••"
    safe.pop("openrouter_api_key", None)
    safe.setdefault("privacy_mode", "cloud")
    safe["privacy_warning"] = PSEUDONYMIZATION_WARNING
    return safe


@app.post("/api/settings")
async def update_settings(request: Request):
    """Update config values."""
    body = await request.json()
    if "privacy_mode" in body and body["privacy_mode"] not in ("cloud", "local_only"):
        raise HTTPException(400, "privacy_mode must be 'cloud' or 'local_only'")
    allowed = {
        "confidence_threshold", "archive_root", "scan_watch_folder",
        "llm_provider", "llm_model", "llm_base_url", "llm_api_key",
        "openrouter_model", "privacy_mode",
    }
    for key in body:
        if key in allowed:
            # Don't overwrite a real API key with the masked placeholder
            if key == "llm_api_key" and body[key] in ("", "••••••••"):
                continue
            _config[key] = body[key]
    # Keep openrouter_model in sync with llm_model for backwards compat
    if "llm_model" in body:
        _config["openrouter_model"] = body["llm_model"]

    # Persist to YAML so changes survive restart
    _persist_config()

    return {"status": "updated"}


@app.get("/api/settings/rules")
async def get_rules():
    return {"rules": _config.get("filing_rules", [])}


@app.get("/api/settings/rules-md")
async def get_rules_md():
    """Return the rules.md content for viewing/editing."""
    from docflow.config.rules_manager import load_rules_md, rules_path
    content = load_rules_md(_config)
    return {"content": content, "path": str(rules_path(_config))}


@app.post("/api/settings/rules-md")
async def update_rules_md(request: Request):
    """Save updated rules.md content."""
    from docflow.config.rules_manager import rules_path
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


# ---------------------------------------------------------------------------
# CRUD: Entities
# ---------------------------------------------------------------------------

@app.post("/api/settings/entities")
async def add_entity(request: Request):
    """Add a new business/investment entity."""
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(400, "Entity name is required")
    entity = {
        "id": body.get("id") or body["name"].lower().replace(" ", "_"),
        "name": body["name"],
        "type": body.get("type", "business"),
    }
    for key in ("directory", "legal_name", "address", "banks", "account_hints"):
        if body.get(key):
            entity[key] = body[key]
    entities = _config.setdefault("entities", [])
    # Check for duplicate id
    if any(e.get("id") == entity["id"] for e in entities):
        raise HTTPException(409, f"Entity '{entity['id']}' already exists")
    entities.append(entity)
    _persist_config()
    return {"status": "created", "entity": entity}


@app.put("/api/settings/entities/{entity_id}")
async def update_entity(entity_id: str, request: Request):
    """Update an existing entity."""
    body = await request.json()
    entities = _config.get("entities", [])
    for i, e in enumerate(entities):
        if e.get("id") == entity_id:
            entities[i] = {**e, **body}
            _persist_config()
            return {"status": "updated", "entity": entities[i]}
    raise HTTPException(404, f"Entity '{entity_id}' not found")


@app.delete("/api/settings/entities/{entity_id}")
async def delete_entity(entity_id: str):
    """Delete an entity."""
    entities = _config.get("entities", [])
    before = len(entities)
    _config["entities"] = [e for e in entities if e.get("id") != entity_id]
    if len(_config["entities"]) == before:
        raise HTTPException(404, f"Entity '{entity_id}' not found")
    _persist_config()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# CRUD: Family members
# ---------------------------------------------------------------------------

@app.post("/api/settings/family")
async def add_family(request: Request):
    """Add a new family member."""
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(400, "Name is required")
    member = {"name": body["name"], "relation": body.get("relation", "")}
    family = _config.setdefault("family", [])
    if any(f.get("name") == member["name"] for f in family):
        raise HTTPException(409, f"Family member '{member['name']}' already exists")
    family.append(member)
    _persist_config()
    return {"status": "created", "member": member}


@app.put("/api/settings/family/{name}")
async def update_family(name: str, request: Request):
    """Update a family member."""
    body = await request.json()
    family = _config.get("family", [])
    for i, f in enumerate(family):
        if f.get("name") == name:
            family[i] = {**f, **body}
            _persist_config()
            return {"status": "updated", "member": family[i]}
    raise HTTPException(404, f"Family member '{name}' not found")


@app.delete("/api/settings/family/{name}")
async def delete_family(name: str):
    """Delete a family member."""
    family = _config.get("family", [])
    before = len(family)
    _config["family"] = [f for f in family if f.get("name") != name]
    if len(_config["family"]) == before:
        raise HTTPException(404, f"Family member '{name}' not found")
    _persist_config()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# CRUD: Filing rules
# ---------------------------------------------------------------------------

@app.post("/api/settings/rules")
async def add_rule(request: Request):
    """Add a new filing rule."""
    body = await request.json()
    if not body.get("id"):
        raise HTTPException(400, "Rule id is required")
    rules = _config.setdefault("filing_rules", [])
    if any(r.get("id") == body["id"] for r in rules):
        raise HTTPException(409, f"Rule '{body['id']}' already exists")
    rule = {"id": body["id"]}
    if body.get("match"):
        rule["match"] = body["match"]
    if body.get("file_to"):
        rule["file_to"] = body["file_to"]
    if body.get("filename_template"):
        rule["filename_template"] = body["filename_template"]
    rules.append(rule)
    _persist_config()
    return {"status": "created", "rule": rule}


@app.put("/api/settings/rules/{rule_id}")
async def update_rule(rule_id: str, request: Request):
    """Update an existing filing rule."""
    body = await request.json()
    rules = _config.get("filing_rules", [])
    for i, r in enumerate(rules):
        if r.get("id") == rule_id:
            rules[i] = {**r, **body}
            _persist_config()
            return {"status": "updated", "rule": rules[i]}
    raise HTTPException(404, f"Rule '{rule_id}' not found")


@app.delete("/api/settings/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Delete a filing rule."""
    rules = _config.get("filing_rules", [])
    before = len(rules)
    _config["filing_rules"] = [r for r in rules if r.get("id") != rule_id]
    if len(_config["filing_rules"]) == before:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    _persist_config()
    return {"status": "deleted"}


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


_CONNECTION_MESSAGES = {
    "local_only": "Local-only mode is on: no model connection was attempted.",
    "transport_unavailable": "The model provider is not configured or unavailable.",
    "timeout": "The model provider timed out.",
    "transport_error": "The model provider returned an error.",
    "invalid_model_output": "The model provider returned an unexpected reply.",
}


@app.post("/api/settings/test-connection")
async def test_connection():
    """Test model connectivity with a synthetic probe through CloudPromptGateway."""
    from docflow.llm.gateway import CloudPromptGateway
    from docflow.privacy.types import NoModelResult

    try:
        result = await asyncio.to_thread(CloudPromptGateway(_config).test_connection)
    except NoModelResult as exc:
        return {
            "status": "local_only" if exc.code == "local_only" else "error",
            "error_code": exc.code,
            "error": _CONNECTION_MESSAGES.get(exc.code, "The model call was refused."),
        }
    return {"status": "connected", "reply": "OK", "model": result.model}


# ---------------------------------------------------------------------------
# API v1: model features (scoped, routed exclusively through CloudPromptGateway)
# ---------------------------------------------------------------------------

class _V1Error(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@app.exception_handler(_V1Error)
async def _v1_error_handler(request: Request, exc: _V1Error):
    return JSONResponse(status_code=exc.status,
                        content={"error": {"code": exc.code, "message": exc.message}})


async def _v1_body(request: Request, model):
    try:
        return model.model_validate(await request.json(), strict=True)
    except (ValidationError, ValueError):
        raise _V1Error(422, "invalid_request", "Request body does not match the contract.") from None


def _v1_require_state() -> None:
    if _state_store is None:
        raise _V1Error(503, "state_unavailable", "Local application state is not configured.")


def _v1_scope(scope_id: str):
    from docflow.state.repositories import ScopeRequiredError

    try:
        return _state_store.scopes.get(scope_id)
    except ScopeRequiredError:
        raise _V1Error(404, "scope_not_found", "Archive scope is not registered.") from None


def _v1_gateway(scope):
    """Per-request gateway confined to the scope root; local-only per config or scope."""
    from docflow.llm.gateway import CloudPromptGateway

    config = {**_config, "archive_root": scope.canonical_root}
    if _state_store.settings.get(scope.id, "privacy_mode") == "local_only":
        config["privacy_mode"] = "local_only"
    return CloudPromptGateway(config)


def _v1_privacy(gateway) -> dict:
    from docflow.llm.gateway import PSEUDONYMIZATION_WARNING

    return {"mode": "local_only" if gateway.local_only else "cloud",
            "warning": PSEUDONYMIZATION_WARNING}


def _v1_status(exc) -> str:
    from docflow.llm.gateway import LocalOnlyMode, TransportFailure
    from docflow.llm.schemas import InvalidModelOutput

    if isinstance(exc, LocalOnlyMode):
        return "local_only"
    if isinstance(exc, TransportFailure):
        return "unavailable"
    if isinstance(exc, InvalidModelOutput):
        return "invalid_model_output"
    return "blocked"


class _AskAIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive_scope_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=1024)


class _ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive_scope_id: str = Field(min_length=1, max_length=128)


@app.post("/api/v1/ask-ai")
async def v1_ask_ai(request: Request):
    """Pseudonymized filing suggestion for one archive-relative PDF in a scope."""
    from docflow.llm.gateway import Feature
    from docflow.privacy.types import NoModelResult
    from docflow.state.repositories import UnsafeValueError, archive_relative

    _v1_require_state()
    body = await _v1_body(request, _AskAIRequest)
    scope = _v1_scope(body.archive_scope_id)
    try:
        relative = archive_relative(body.relative_path)
    except UnsafeValueError:
        raise _V1Error(400, "invalid_path", "Path must be archive-relative.") from None
    try:
        root = Path(scope.canonical_root)
        source = _confined_archive_pdf([root], root / relative)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _V1Error(404, "file_not_found", "No PDF at that path.") from None
        raise _V1Error(400, "invalid_path", "Path must resolve to a PDF inside the scope.") from None

    gateway = _v1_gateway(scope)
    result = {"status": "suggested", "error_code": None, "suggestion": None,
              "privacy": _v1_privacy(gateway)}
    if gateway.local_only:
        result.update(status="local_only", error_code="local_only")
        return result
    try:
        raw_text, page_count = await asyncio.to_thread(_ocr_first_page, source)
        suggestion = await asyncio.to_thread(_suggest_filing, gateway, raw_text, page_count,
                                             Feature.ASK_AI)
    except NoModelResult as exc:
        result.update(status=_v1_status(exc), error_code=exc.code)
        return result
    suggestion["suggested_relative_directory"] = suggestion.pop("suggested_directory")
    result["suggestion"] = suggestion
    return result


@app.post("/api/v1/connection-test")
async def v1_connection_test(request: Request):
    """Verify provider connectivity with a synthetic probe for an explicit scope."""
    from docflow.privacy.types import NoModelResult

    _v1_require_state()
    body = await _v1_body(request, _ConnectionTestRequest)
    scope = _v1_scope(body.archive_scope_id)
    gateway = _v1_gateway(scope)
    result = {"status": "connected", "model": None, "error_code": None,
              "privacy": _v1_privacy(gateway)}
    try:
        result["model"] = (await asyncio.to_thread(gateway.test_connection)).model
    except NoModelResult as exc:
        result.update(status="local_only" if exc.code == "local_only" else "error",
                      error_code=exc.code)
    return result


class _RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive_scope_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


_RETRY_CONFLICTS = {
    "job_not_retryable": "Job is not in a retryable state.",
    "source_changed": "The source no longer matches the admitted scan.",
    "source_unavailable": "The source is not available yet; retry later.",
}


def _v1_job_error(code: str) -> _V1Error:
    if code == "job_not_found":
        return _V1Error(404, "job_not_found", "Job not found in this scope.")
    if code in _RETRY_CONFLICTS:
        return _V1Error(409, code, _RETRY_CONFLICTS[code])
    return _V1Error(422, "invalid_request", "Request body does not match the contract.")


@app.get("/api/v1/jobs/{job_id}")
async def v1_get_job(job_id: str, request: Request):
    """Durable job status, page accounting, filing journal and recovery state."""
    from docflow.filing.operations import RetryRejected, job_payload

    _v1_require_state()
    scope_id = request.query_params.get("archive_scope_id", "")
    if not 1 <= len(scope_id) <= 128:
        raise _V1Error(422, "invalid_request", "archive_scope_id query parameter is required.")
    scope = _v1_scope(scope_id)
    try:
        return job_payload(_state_store, scope.id, job_id)
    except RetryRejected as exc:
        raise _v1_job_error(exc.code) from None


@app.post("/api/v1/jobs/{job_id}/retry")
async def v1_retry_job(job_id: str, request: Request):
    """Resume an interrupted filing or requeue a failed job, once per idempotency key."""
    from docflow.filing.operations import RetryRejected

    _v1_require_state()
    if _filer is None:
        raise _V1Error(503, "state_unavailable", "Local application state is not configured.")
    body = await _v1_body(request, _RetryRequest)
    scope = _v1_scope(body.archive_scope_id)
    try:
        return await asyncio.to_thread(_filer.retry, scope.id, job_id,
                                       idempotency_key=body.idempotency_key)
    except RetryRejected as exc:
        raise _v1_job_error(exc.code) from None


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
            _config.get("scan_watch_folder", "~/DocFlowExample/inbox")
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
        "started": _local_iso(),
    }
    asyncio.create_task(_run_pipeline_async(new_job_id, upload_path))
    return {"job_id": new_job_id}
