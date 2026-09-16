"""DocFlow web application: FastAPI backend for the full UI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="DocFlow — The Digital Archivist")

# Config and state
_config: dict = {}
_processing_state: dict = {}  # Active processing state for SSE
_state_store = None  # docflow.state.repositories.StateStore backing /api/v1
_filer = None  # docflow.filing.operations.DurableFiler backing job retry
_active_scope_id: str | None = None  # registered scope the local UI works in
# One submission -> one progress job. Repeated UI events, retries and rapid or
# concurrent duplicate calls resolve to the job already started for that key.
_submissions: dict[str, str] = {}
_submission_lock = threading.Lock()

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


def configure_state(store, filer=None, *, scope_id: str | None = None) -> None:
    """Attach the local state store, durable filer and the UI's active archive scope."""
    global _state_store, _filer, _active_scope_id
    _state_store = store
    _filer = filer
    _active_scope_id = scope_id


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
    """The durable filer's ``upload`` root in application state (never the archive)."""
    roots = getattr(_filer, "source_roots", {})
    if "upload" not in roots:
        raise HTTPException(503, "Local application state is not configured.")
    d = roots["upload"]
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    from docflow.filing.operations import collision_candidates
    from docflow.state.repositories import UnsafeValueError, safe_name

    try:
        name = safe_name(file.filename or "")
    except UnsafeValueError:
        name = ""
    if not name.lower().endswith(".pdf") or name.startswith((".", "~")) or ":" in name:
        raise HTTPException(400, "Only PDF files with a plain file name are supported")

    content = await file.read()
    upload_dir = _upload_dir()

    def write_new() -> Path:
        """Write into the upload folder under a new name; never replace an existing upload."""
        for relative in collision_candidates(".", name):
            path = upload_dir / Path(relative).name
            try:
                with open(path, "xb") as fh:
                    fh.write(content)
                return path
            except FileExistsError:
                continue
        raise HTTPException(409, "Too many uploads with this name")

    upload_path = await asyncio.to_thread(write_new)
    return {"filename": upload_path.name, "path": str(upload_path), "size": len(content)}


def _legacy_process_locator(raw: object) -> str:
    """Map an absolute path from ``/api/upload`` or the watch folder to a source handle.

    Only files beneath the durable filer's configured ``upload``/``watch`` roots are
    accepted (no traversal, no symlinks); any other caller-supplied path is rejected.
    """
    from docflow.filing.operations import confined
    from docflow.state.repositories import UnsafeValueError, validate_source_locator

    candidate = Path(raw) if isinstance(raw, str) and raw else None
    roots = _filer.source_roots if candidate and candidate.is_absolute() else {}
    for scheme in ("upload", "watch"):
        if scheme not in roots:
            continue
        try:
            relative = candidate.relative_to(roots[scheme]).as_posix()
            locator = validate_source_locator(f"{scheme}:{relative}")
            path = confined(roots[scheme], relative)
        except (ValueError, UnsafeValueError):
            continue
        if path.is_file() and path.suffix.lower() == ".pdf":
            return locator
    raise HTTPException(400, "PDF must be an uploaded file or in the configured watch folder")


# The rule label the classifier records when nothing classified the document.
UNCLASSIFIED_RULE = "none"


def _capabilities() -> dict:
    """What this local install really does, in words that promise nothing more."""
    from docflow.llm.gateway import CloudPromptGateway

    classification = not CloudPromptGateway(_config).local_only
    return {
        "text_extraction": True,
        "ai_classification": classification,
        "summary": "Text extraction is enabled. AI classification is "
                   + ("enabled." if classification else "disabled."),
    }


def _stage_view(stage: str) -> list[dict]:
    """The stages this pipeline really runs, marked against the one now reached."""
    from docflow.filing.processing import DONE_STAGE, PROCESS_STAGES

    order = [*PROCESS_STAGES, DONE_STAGE]
    if stage == DONE_STAGE[0]:  # the run finished: every stage really did complete
        return [{"id": key, "label": label, "status": "done"} for key, label, _p in order]
    reached = next((i for i, s in enumerate(order) if s[0] == stage), -1)
    return [{"id": key, "label": label,
             "status": "done" if index < reached else "active" if index == reached
             else "waiting"}
            for index, (key, label, _percent) in enumerate(order)]


def _initial_progress_state(filename: str) -> dict:
    return {
        "status": "starting",
        "filename": filename,   # acknowledged as soon as the submission is accepted
        "pdf": filename,        # legacy alias kept for older clients
        "progress": 0,
        "step": "Checking the scan",
        "stage": "checking",
        "stages": _stage_view("checking"),
        "documents": [],
        "documents_total": 0,
        "auto_filed": 0,
        "review_queue": 0,
        "durable_job_id": None,
        "review_url": None,
        "capabilities": _capabilities(),
        "started": _local_iso(),
    }


@app.post("/api/process")
async def process_pdf(request: Request):
    """Start durable processing of an uploaded or watch-folder PDF; returns a progress ID.

    One submission starts one job. ``submission_key`` (optional) identifies the user's
    submission; without it the resolved source handle is the key. Repeated, retried or
    concurrent calls for the same key return the job already running or finished for it
    and start no second job. A deliberate new submission uses a new key.

    Poll ``/api/process/status/{job_id}``; the result is a durable job whose review
    items appear in ``GET /api/v1/review-items``.
    """
    if _state_store is None or _filer is None or _active_scope_id is None:
        raise HTTPException(503, "Local application state is not configured.")
    body = await request.json()
    locator = _legacy_process_locator(body.get("path"))
    key = body.get("submission_key")
    if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128):
        raise HTTPException(400, "submission_key must be a short string")
    filename = locator.partition(":")[2].rsplit("/", 1)[-1]

    # Claim the submission and start the pipeline under one lock, so even simultaneous
    # duplicate calls cannot both pass the check.
    lookup = key or f"source:{locator}"
    with _submission_lock:
        existing = _submissions.get(lookup)
        state = _processing_state.get(existing) if existing else None
        # A named submission always maps to its one job, so a retry is never a second
        # run. Without a key the caller gave no identity, so only a run still in flight
        # is reused: submitting the same source again later is a deliberate new job.
        if state is not None and (key is not None
                                  or state.get("status") in ("starting", "processing")):
            return {"job_id": existing, "reused": True}
        job_id = str(uuid.uuid4())[:8]
        _submissions[lookup] = job_id
        _processing_state[job_id] = _initial_progress_state(filename)
        asyncio.create_task(_run_pipeline_async(job_id, locator))
    return {"job_id": job_id, "reused": False}


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


async def _run_pipeline_async(job_id: str, locator: str) -> None:
    """Process one scan through the durable service, publishing in-memory progress only."""
    from docflow.filing.processing import process_scan
    from docflow.ingestion.loader import raw_sha256

    from docflow.filing.processing import PROCESS_STAGES

    state = _processing_state[job_id]
    loop = asyncio.get_running_loop()
    stage_by_label = {label: key for key, label, _percent in PROCESS_STAGES}

    def progress(step: str, percent: int) -> None:
        stage = stage_by_label.get(step, state.get("stage", "checking"))
        loop.call_soon_threadsafe(state.update, {
            "step": step, "progress": percent, "status": "processing",
            "stage": stage, "stages": _stage_view(stage)})

    try:
        result = await asyncio.to_thread(process_scan, _state_store, _filer, _active_scope_id,
                                         locator, _config, progress=progress)
        documents = [{
            "filename": a.filename,
            "directory": a.relative_directory,
            "institution": d.candidate.institution,
            "doc_type": d.candidate.doc_type,
            "period": d.candidate.period,
            "pages": list(a.pages),
            "rule": d.rule_matched,
            # No rule matched and no model result was accepted, so nothing classified
            # this document: report no confidence rather than a 0.0 the UI would
            # render as a confident zero. A real classification keeps its number.
            "confidence": None if d.rule_matched == UNCLASSIFIED_RULE else d.confidence,
            "auto_filed": a.outcome == "filed",
            "notes": a.reason,
            "reasoning": d.candidate.raw_signals.get("llm_reasoning", ""),
        } for d, a in zip(result.decisions, result.assignments)]

        # A byte-identical copy of the retained original left in the watch folder (for
        # example the scanner's copy of an uploaded file) is removed so it is not processed
        # again. A different file is never removed.
        from docflow.filing.operations import confined

        watch_root = _filer.source_roots.get("watch")
        root = Path(_state_store.scopes.get(_active_scope_id).canonical_root)
        originals = [f["relative_path"] for f in result.payload.get("files", [])
                     if f["role"] == "original"]
        if watch_root is not None and originals and locator.startswith("upload:"):
            watch_copy = confined(watch_root, locator.partition(":")[2])
            retained = confined(root, originals[0])
            if (watch_copy.is_file() and not watch_copy.is_symlink()
                    and raw_sha256(watch_copy) == raw_sha256(retained)):
                watch_copy.unlink()
                logger.info("Removed identical watch folder copy")

        finished = result.job_status in {"completed", "review"}
        state.update({
            "status": "completed" if finished else "error",
            "step": "Done" if finished else f"Job is {result.job_status}",
            "progress": 100,
            "stage": "done" if finished else state.get("stage", "filing"),
            "stages": _stage_view("done" if finished else state.get("stage", "filing")),
            "documents": documents,
            "documents_total": len(documents),
            "auto_filed": sum(a.outcome == "filed" for a in result.assignments),
            "review_queue": sum(a.outcome == "review" for a in result.assignments),
            "durable_job_id": result.job_id,
            "review_url": f"/review?job_id={result.job_id}" if result.job_id else None,
            "capabilities": _capabilities(),
        })
    except Exception as exc:
        logger.exception("Pipeline error for job %s", job_id)
        state.update({
            "status": "error",
            "step": f"Error: {str(exc)[:200]}",
            "progress": state.get("progress", 0),
        })


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
    """File a PDF from ``_Unmatched``/``_Skipped`` at a validated archive-relative destination."""
    from docflow.config.learner import CORRECTIONS_FILENAME, record_correction
    from docflow.filing.operations import (
        collision_candidates,
        ensure_parent,
        fsync_directory,
        partial_path,
        place_without_replacing,
    )
    from docflow.filing.review_actions import validate_review_destination
    from docflow.ingestion.loader import raw_sha256
    from docflow.state.repositories import UnsafeValueError

    if _state_store is None or _active_scope_id is None:
        raise HTTPException(503, "Local application state is not configured.")
    body = await request.json()
    source_path, filename, directory = (str(body.get(key) or "").strip()
                                        for key in ("path", "filename", "directory"))
    if not source_path or not filename or not directory:
        raise HTTPException(400, "path, filename, and directory are required")

    root = Path(_state_store.scopes.get(_active_scope_id).canonical_root)
    source = _confined_archive_pdf([root / "_Unmatched", root / "_Skipped"], Path(source_path))
    try:
        validate_review_destination(root, directory, filename)
    except UnsafeValueError:
        raise HTTPException(400, "Destination must be a relative archive folder and a "
                                 "visible .pdf name.") from None

    def move() -> str:
        expected = raw_sha256(source)
        for relative in collision_candidates(directory, filename):
            target = ensure_parent(root, relative)
            if os.path.lexists(target):
                continue
            try:
                place_without_replacing(source, target,
                                        partial_path(target.parent, "reclassify", 0))
            except FileExistsError:
                continue
            fsync_directory(target.parent)
            if raw_sha256(target) != expected:
                raise HTTPException(500, "The filed copy did not verify; the source was kept.")
            source.unlink()
            fsync_directory(source.parent)
            return relative
        raise HTTPException(409, "Too many files with this name")

    try:
        relative = await asyncio.to_thread(move)
    except UnsafeValueError:
        raise HTTPException(400, "Destination must be a relative archive folder and a "
                                 "visible .pdf name.") from None
    record_correction(
        {"suggested_filename": source.name, "suggested_directory": source.parent.name},
        filename, directory, {**_config, "archive_root": str(root)},
        log_path=_state_store.db.paths.logs / CORRECTIONS_FILENAME,
    )
    return {"status": "reclassified", "target": relative}


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

    if _state_store is None:
        raise HTTPException(503, "Local application state is not configured.")
    cache_dir = _state_store.db.paths.cache / "previews"  # application state, never the archive
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


# Label and severity for each model status. Local-only is a configured state, so it is
# never styled or worded as a fault; only a mode that really needs a key can warn.
_LLM_STATUS = {
    "ready": ("Ready", "ok"),
    "local_only": ("Local Only", "neutral"),
    "missing_key": ("No API Key", "warning"),
}
_MISSING_KEY_DETAIL = "Add an API key to enable AI classification."


def _model_key_available() -> bool:
    """True when the configured provider has an API key, or needs none (local provider)."""
    from docflow.llm.client import PROVIDERS

    preset = PROVIDERS.get(_config.get("llm_provider", "openrouter"))
    if preset is None:  # an unrecognised provider is never silently treated as usable
        return False
    if preset["env_key"] is None:
        return True
    if _config.get("llm_api_key") not in ("", "••••••••", None):
        return True

    def from_env() -> bool:
        return bool(os.environ.get(preset["env_key"]) or os.environ.get("OPENROUTER_API_KEY"))

    if not from_env():
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            return False
    return from_env()


def _llm_status() -> dict:
    """Mode-aware model status for every health/status surface."""
    capabilities = _capabilities()
    if not capabilities["ai_classification"]:
        status = "local_only"
    elif _model_key_available():
        status = "ready"
    else:
        status = "missing_key"
    label, level = _LLM_STATUS[status]
    return {
        "privacy_mode": "cloud" if capabilities["ai_classification"] else "local_only",
        "text_extraction": capabilities["text_extraction"],
        "ai_classification": capabilities["ai_classification"],
        "llm_ready": status == "ready",
        "llm_status": status,
        "llm_status_label": label,
        "llm_status_level": level,
        "llm_status_detail": (_MISSING_KEY_DETAIL if status == "missing_key"
                              else capabilities["summary"]),
    }


@app.get("/api/health")
async def health():
    """System health check, worded for the privacy mode this install actually runs in."""
    return {
        "watch_folder": _config.get("scan_watch_folder", ""),
        "archive_root": _config.get("archive_root", ""),
        **_llm_status(),
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


class _UploadSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["upload"]
    name: str = Field(min_length=1, max_length=255)


class _WatchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["watch"]
    relative_path: str = Field(min_length=1, max_length=1024)


class _JobCreateRequest(_RetryRequest):
    source: Annotated[_UploadSource | _WatchSource, Field(discriminator="kind")]


@app.post("/api/v1/jobs")
async def v1_create_job(request: Request):
    """Admit an uploaded file or configured watch-folder PDF by handle, never by path."""
    from docflow.filing.operations import RetryRejected
    from docflow.state.repositories import UnsafeValueError

    _v1_require_state()
    if _filer is None:
        raise _V1Error(503, "state_unavailable", "Local application state is not configured.")
    body = await _v1_body(request, _JobCreateRequest)
    scope = _v1_scope(body.archive_scope_id)
    source = body.source
    locator = (f"upload:{source.name}" if isinstance(source, _UploadSource)
               else f"watch:{source.relative_path}")
    try:
        return await asyncio.to_thread(_filer.create_job, scope.id, locator,
                                       idempotency_key=body.idempotency_key)
    except UnsafeValueError:
        raise _V1Error(400, "invalid_source", "Source must be an uploaded file or a configured "
                       "watch-folder PDF.") from None
    except RetryRejected as exc:
        if exc.code == "idempotency_key_reused":
            raise _V1Error(409, exc.code, "This idempotency key was used for a different "
                           "request.") from None
        raise _v1_job_error(exc.code) from None


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


# ---------------------------------------------------------------------------
# API v1: durable review actions and undo
# ---------------------------------------------------------------------------

_REVIEW_STATUSES = ("pending", "approved", "corrected", "skipped")


def _v1_review_actions():
    from docflow.filing.review_actions import ReviewActions

    return ReviewActions(_state_store)


class _ReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive_scope_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


_REVIEW_ERRORS = {
    "review_item_not_found": (404, "Review item not found in this scope."),
    "page_not_found": (404, "Page not found in this review item."),
    "preview_unavailable": (409, "The page preview could not be rendered."),
    "review_item_not_pending": (409, "Review item is no longer pending."),
    "review_item_not_actionable": (409, "Review item has no pages awaiting review."),
    "source_unavailable": (409, "The retained original for these pages is unavailable."),
    "destination_required": (409, "The suggestion cannot be filed; send a correction."),
    "invalid_destination": (
        400, "Destination must be a relative archive folder and a visible .pdf name."),
    "operation_not_found": (404, "Operation not found in this scope."),
    "idempotency_key_reused": (409, "This idempotency key was used for a different request."),
    "operation_not_undoable": (409, "This operation cannot be undone."),
}


def _v1_review_error(code: str) -> _V1Error:
    status, message = _REVIEW_ERRORS.get(
        code, (409, "The review action could not be completed."))
    if code == "invalid_idempotency_key":
        return _V1Error(422, "invalid_request", "Request body does not match the contract.")
    return _V1Error(status, code, message)


async def _v1_review_call(func, *args, **kwargs):
    from docflow.filing.review_actions import ReviewActionRejected

    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except ReviewActionRejected as exc:
        raise _v1_review_error(exc.code) from None


@app.post("/api/v1/review-items/{item_id}/approve")
async def v1_approve_review_item(item_id: str, request: Request):
    """File the item's pages at its stored suggestion; returns a durable operation."""
    _v1_require_state()
    body = await _v1_body(request, _ReviewActionRequest)
    scope = _v1_scope(body.archive_scope_id)
    return await _v1_review_call(_v1_review_actions().approve, scope.id, item_id,
                                 idempotency_key=body.idempotency_key)


class _ReviewCorrectRequest(_ReviewActionRequest):
    relative_directory: str = Field(min_length=1, max_length=1024)
    filename: str = Field(min_length=1, max_length=255)


@app.post("/api/v1/review-items/{item_id}/correct")
async def v1_correct_review_item(item_id: str, request: Request):
    """File the item's pages at a strictly validated archive-relative destination."""
    _v1_require_state()
    body = await _v1_body(request, _ReviewCorrectRequest)
    scope = _v1_scope(body.archive_scope_id)
    return await _v1_review_call(_v1_review_actions().correct, scope.id, item_id,
                                 idempotency_key=body.idempotency_key,
                                 relative_directory=body.relative_directory,
                                 filename=body.filename)


@app.post("/api/v1/review-items/{item_id}/skip")
async def v1_skip_review_item(item_id: str, request: Request):
    """Intentionally skip the item's pages; no archive change; returns a durable operation."""
    _v1_require_state()
    body = await _v1_body(request, _ReviewActionRequest)
    scope = _v1_scope(body.archive_scope_id)
    return await _v1_review_call(_v1_review_actions().skip, scope.id, item_id,
                                 idempotency_key=body.idempotency_key)


class _ReviewBatchRequest(_ReviewActionRequest):
    action: Literal["approve", "skip"]
    review_item_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=100)


@app.post("/api/v1/review-items/batch")
async def v1_batch_review_items(request: Request):
    """Approve or skip several items in one durable, undoable operation."""
    _v1_require_state()
    body = await _v1_body(request, _ReviewBatchRequest)
    if len(set(body.review_item_ids)) != len(body.review_item_ids):
        raise _V1Error(422, "invalid_request", "Request body does not match the contract.")
    scope = _v1_scope(body.archive_scope_id)
    return await _v1_review_call(_v1_review_actions().batch, scope.id, action=body.action,
                                 review_item_ids=body.review_item_ids,
                                 idempotency_key=body.idempotency_key)


@app.post("/api/v1/operations/{operation_id}/undo")
async def v1_undo_operation(operation_id: str, request: Request):
    """Durably compensate a review action; reports per-step partial failure."""
    _v1_require_state()
    body = await _v1_body(request, _ReviewActionRequest)
    scope = _v1_scope(body.archive_scope_id)
    return await _v1_review_call(_v1_review_actions().undo, scope.id, operation_id,
                                 idempotency_key=body.idempotency_key)


@app.get("/api/v1/archive-scopes/active")
async def v1_active_scope():
    """The scope ID the local UI sends explicitly with every v1 call; no paths are returned."""
    _v1_require_state()
    if _active_scope_id is None:
        raise _V1Error(404, "scope_not_found", "Archive scope is not registered.")
    return {"archive_scope": {"id": _v1_scope(_active_scope_id).id}}


@app.get("/api/v1/review-items")
async def v1_list_review_items(request: Request):
    """Durable review items for an explicit scope; no raw OCR text, archive-relative paths."""
    _v1_require_state()
    scope_id = request.query_params.get("archive_scope_id", "")
    status = request.query_params.get("status", "pending")
    job_id = request.query_params.get("job_id")
    if not 1 <= len(scope_id) <= 128 or status not in _REVIEW_STATUSES \
            or (job_id is not None and not 1 <= len(job_id) <= 128):
        raise _V1Error(422, "invalid_request", "Request does not match the contract.")
    scope = _v1_scope(scope_id)
    return await asyncio.to_thread(_v1_review_actions().list_items, scope.id, status, job_id)


def _v1_page_request(request: Request, page_number: str) -> tuple[object, int]:
    """Validate the scope and the 1-based physical page number of a page route."""
    _v1_require_state()
    scope_id = request.query_params.get("archive_scope_id", "")
    if not 1 <= len(scope_id) <= 128:
        raise _V1Error(422, "invalid_request", "Request does not match the contract.")
    if not page_number.isdigit() or not 1 <= len(page_number) <= 9:
        raise _V1Error(422, "invalid_request", "Request does not match the contract.")
    return _v1_scope(scope_id), int(page_number)


@app.get("/api/v1/review-items/{item_id}/pages/{page_number}/text")
async def v1_review_item_page_text(item_id: str, page_number: str, request: Request):
    """Locally extracted text for one physical source page of a review item.

    ``page_number`` is the 1-based page of the source PDF and must belong to the
    item. The response distinguishes ``extracted``, ``no_text``, ``failed`` and
    ``missing``; text is document data and must be rendered as text, never markup.
    """
    scope, page = _v1_page_request(request, page_number)
    return await _v1_review_call(_v1_review_actions().page_text, scope.id, item_id, page)


@app.get("/api/v1/review-items/{item_id}/pages/{page_number}/preview")
async def v1_review_item_page_preview(item_id: str, page_number: str, request: Request):
    """Render one physical source page of a review item's retained original as PNG.

    The active scope is authorized before the item is looked up. The original is
    resolved only through scope/job/file records, must be a regular non-symlinked
    file confined to the scope root, and its page must still match the fingerprint
    recorded at admission. Renders are cached in application state, never the archive.
    """
    scope, page = _v1_page_request(request, page_number)
    if _active_scope_id is None or scope.id != _active_scope_id:
        raise _V1Error(404, "scope_not_found", "Archive scope is not registered.")
    cached = await _v1_review_call(_page_preview, scope, item_id, page)
    return FileResponse(str(cached), media_type="image/png")


def _render_preview_page(source: Path, page: int):
    """Render one page at the canonical 150 DPI grayscale used for fingerprints."""
    from pdf2image import convert_from_path

    images = convert_from_path(str(source), first_page=page, last_page=page,
                               dpi=150, grayscale=True)
    if not images:
        raise RuntimeError("the page did not render")
    image = images[0]
    return image if image.mode == "L" else image.convert("L")


def _preview_source(scope, item_id: str, page: int) -> tuple[Path, str]:
    """The confined retained original for a review item's page, plus the page digest.

    Raises ``ReviewActionRejected`` with a fixed code; cross-scope existence is never
    disclosed because the item lookup is already scoped.
    """
    from docflow.filing.operations import confined
    from docflow.filing.review_actions import ReviewActionRejected
    from docflow.ingestion.loader import raw_sha256
    from docflow.state.repositories import UnsafeValueError

    store = _state_store
    item = store.reviews.get(scope.id, item_id)
    if item is None:
        raise ReviewActionRejected("review_item_not_found")
    pages = item.candidate.get("pages")
    if not isinstance(pages, list) or page not in pages:
        raise ReviewActionRejected("page_not_found")
    rows = {row["page_number"]: row["content_sha256"]
            for row in store.jobs.pages(scope.id, item.job_id)}
    if page not in rows:
        raise ReviewActionRejected("page_not_found")
    digest = rows[page]
    job = store.jobs.get(scope.id, item.job_id)
    originals = [r for r in store.files.list_for_job(scope.id, item.job_id)
                 if r.role == "original"]
    if job is None or len(originals) != 1 or not digest:
        raise ReviewActionRejected("source_unavailable")
    try:
        source = confined(Path(scope.canonical_root), originals[0].relative_path)
    except UnsafeValueError:
        raise ReviewActionRejected("source_unavailable") from None
    if source.is_symlink() or not source.is_file():
        raise ReviewActionRejected("source_unavailable")
    # The retained original must still hold the admitted bytes before it is opened.
    try:
        unchanged = raw_sha256(source) == job.source_fingerprint.removeprefix("sha256:")
    except OSError:
        raise ReviewActionRejected("source_unavailable") from None
    if not unchanged:
        raise ReviewActionRejected("source_unavailable")
    return source, digest


def _page_preview(scope, item_id: str, page: int) -> Path:
    """Return the cached PNG for one page, rendering and verifying it if needed."""
    from docflow.filing.review_actions import ReviewActionRejected
    from docflow.ingestion.loader import page_content_sha256

    source, digest = _preview_source(scope, item_id, page)
    # Keyed by scope, job, page and the digest recorded at admission, so a changed
    # original can never be served from an earlier render.
    item = _state_store.reviews.get(scope.id, item_id)
    directory = _state_store.db.paths.cache / "review-previews" / scope.id / item.job_id
    final = directory / f"{page}-{digest}.png"
    if final.is_file():
        return final
    try:
        image = _render_preview_page(source, page)
    except Exception as exc:  # noqa: BLE001 - any render failure is reported, never guessed
        logger.warning("Page preview could not be rendered: %s", type(exc).__name__)
        raise ReviewActionRejected("preview_unavailable") from None
    if page_content_sha256(image) != digest:
        raise ReviewActionRejected("source_unavailable")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = directory / f"{final.name}.{uuid.uuid4().hex}.partial"
    try:
        image.save(str(partial), "PNG")
        os.replace(partial, final)  # atomic publication within one directory
    except OSError as exc:
        raise ReviewActionRejected("preview_unavailable") from exc
    finally:
        partial.unlink(missing_ok=True)
    return final


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
    """Re-processing is a durable retry now: ``POST /api/v1/jobs/{id}/retry``."""
    if job_id not in _processing_state:
        raise HTTPException(404, "Job not found")
    raise HTTPException(409, "Re-processing is not available; retry a failed durable job with "
                             "POST /api/v1/jobs/{id}/retry.")
