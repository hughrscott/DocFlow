"""Review queue: FastAPI server for reviewing low-confidence filing decisions."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pypdf import PdfReader, PdfWriter

from docflow.review.queue import load_review_queue, update_queue_item
from docflow.filing.filer import ensure_directory

logger = logging.getLogger(__name__)

app = FastAPI(title="Mail Archiver — Review Queue")

# Config is set at startup via configure()
_config: dict = {}
_source_pdfs: dict[str, Path] = {}  # item_id → source PDF path


def configure(config: dict) -> None:
    """Set the config for the review server."""
    global _config
    _config = config


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the review queue HTML page."""
    items = load_review_queue(_config)
    return _render_html(items)


@app.get("/queue")
async def get_queue():
    """Return all pending review items as JSON."""
    items = load_review_queue(_config)
    return {"pending": len(items), "items": items}


@app.post("/approve/{item_id}")
async def approve(item_id: str):
    """Approve a filing decision — extract and file as suggested."""
    item = update_queue_item(_config, item_id, {"status": "approved"})
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")

    _extract_item(item, item["suggested_filename"], item["suggested_directory"])
    return {"status": "approved", "filename": item["suggested_filename"]}


@app.post("/correct/{item_id}")
async def correct(item_id: str, request: Request):
    """Correct a filing decision with new filename/directory, then file."""
    body = await request.json()
    filename = body.get("filename", "").strip()
    directory = body.get("directory", "").strip()

    if not filename or not directory:
        raise HTTPException(400, "Both 'filename' and 'directory' are required")

    archive_root = os.path.expanduser(_config.get("archive_root", "~/ElectronicFiles"))
    target_dir = os.path.join(archive_root, directory)

    item = update_queue_item(_config, item_id, {
        "status": "corrected",
        "corrected_filename": filename,
        "corrected_directory": target_dir,
    })
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")

    _extract_item(item, filename, target_dir)

    # Record the correction for rule learning
    from docflow.config.learner import record_correction
    record_correction(item, filename, target_dir, _config)

    return {"status": "corrected", "filename": filename, "directory": target_dir}


@app.post("/skip/{item_id}")
async def skip(item_id: str):
    """Skip an item — move to a holding directory."""
    archive_root = os.path.expanduser(_config.get("archive_root", "~/ElectronicFiles"))
    holding_dir = os.path.join(archive_root, "_Skipped")

    item = update_queue_item(_config, item_id, {"status": "skipped"})
    if not item:
        raise HTTPException(404, f"Item {item_id} not found")

    _extract_item(item, item["suggested_filename"], holding_dir)
    return {"status": "skipped"}


def _extract_item(item: dict, filename: str, target_dir: str) -> Path:
    """Extract pages from source PDF and write to target."""
    source_pdf = Path(item["source_pdf"])
    if not source_pdf.exists():
        raise HTTPException(400, f"Source PDF not found: {source_pdf}")

    target_path = Path(target_dir)
    ensure_directory(target_path)

    output_path = target_path / filename
    # Handle collision
    if output_path.exists():
        stem = output_path.stem
        suffix = output_path.suffix
        counter = 2
        while output_path.exists():
            output_path = target_path / f"{stem}_{counter}{suffix}"
            counter += 1

    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()
    for page_num in item["pages"]:
        writer.add_page(reader.pages[page_num - 1])

    with open(output_path, "wb") as f:
        writer.write(f)

    logger.info("Review extracted: pages %s → %s", item["pages"], output_path)
    return output_path


def _render_html(items: list[dict]) -> str:
    """Render the review queue as an HTML page."""
    if not items:
        return """<!DOCTYPE html>
<html><head><title>Mail Archiver — Review Queue</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; }
h1 { color: #2c5282; }
.done { background: #f0fff4; border: 1px solid #c6f6d5; border-radius: 8px;
        padding: 24px; text-align: center; }
</style></head>
<body><h1>Mail Archiver — Review Queue</h1>
<div class="done"><h2>All clear!</h2><p>No items pending review.</p></div>
</body></html>"""

    rows = ""
    for item in items:
        pages_str = ", ".join(str(p) for p in item["pages"])
        text_preview = (item.get("raw_text_preview", "") or "")[:200].replace("<", "&lt;")
        rows += f"""
        <div class="card" id="card-{item['id']}">
            <div class="card-header">
                <span class="confidence conf-{'high' if item['confidence'] >= 0.75 else 'low'}">
                    {item['confidence']:.0%}
                </span>
                <strong>{item.get('institution', 'Unknown')}</strong>
                — pages {pages_str}
            </div>
            <div class="card-body">
                <div class="field"><label>Suggested filename:</label>
                    <input type="text" id="fn-{item['id']}" value="{item['suggested_filename']}" />
                </div>
                <div class="field"><label>Suggested directory:</label>
                    <input type="text" id="dir-{item['id']}" value="{item.get('suggested_directory', '')}" />
                </div>
                <div class="field"><label>Rule:</label>
                    <span>{item.get('rule_matched', 'none')}</span>
                </div>
                <div class="field"><label>Notes:</label>
                    <span>{item.get('notes', '') or '—'}</span>
                </div>
                <details><summary>OCR text preview</summary>
                    <pre>{text_preview}</pre>
                </details>
                <div class="actions">
                    <button class="btn approve" onclick="approve('{item['id']}')">Approve</button>
                    <button class="btn correct" onclick="correctItem('{item['id']}')">Correct & File</button>
                    <button class="btn skip" onclick="skipItem('{item['id']}')">Skip</button>
                </div>
            </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><title>Mail Archiver — Review Queue</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 900px; margin: 40px auto; padding: 0 20px; color: #333;
       background: #f7fafc; }}
h1 {{ color: #2c5282; }}
.card {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px;
         margin-bottom: 16px; overflow: hidden; }}
.card-header {{ background: #edf2f7; padding: 12px 16px; font-size: 14px; }}
.card-body {{ padding: 16px; }}
.field {{ margin-bottom: 8px; }}
.field label {{ display: inline-block; width: 160px; font-weight: 600; font-size: 13px; }}
.field input {{ width: 60%; padding: 4px 8px; border: 1px solid #cbd5e0; border-radius: 4px; }}
.field span {{ font-size: 13px; }}
details {{ margin: 8px 0; font-size: 12px; }}
pre {{ background: #f7fafc; padding: 8px; border-radius: 4px; white-space: pre-wrap;
       font-size: 11px; max-height: 150px; overflow-y: auto; }}
.actions {{ margin-top: 12px; display: flex; gap: 8px; }}
.btn {{ padding: 8px 20px; border: none; border-radius: 4px; cursor: pointer;
        font-size: 13px; font-weight: 600; }}
.btn.approve {{ background: #48bb78; color: white; }}
.btn.approve:hover {{ background: #38a169; }}
.btn.correct {{ background: #4299e1; color: white; }}
.btn.correct:hover {{ background: #3182ce; }}
.btn.skip {{ background: #a0aec0; color: white; }}
.btn.skip:hover {{ background: #718096; }}
.confidence {{ display: inline-block; padding: 2px 8px; border-radius: 12px;
               font-size: 12px; font-weight: 700; margin-right: 8px; }}
.conf-high {{ background: #c6f6d5; color: #276749; }}
.conf-low {{ background: #fed7d7; color: #9b2c2c; }}
.done-card {{ display: none; background: #f0fff4; border: 1px solid #c6f6d5;
              border-radius: 8px; padding: 24px; text-align: center; margin-top: 16px; }}
.counter {{ color: #718096; font-size: 14px; }}
</style></head>
<body>
<h1>Mail Archiver — Review Queue</h1>
<p class="counter"><span id="count">{len(items)}</span> item(s) pending review</p>
{rows}
<div class="done-card" id="all-done">
    <h2>All done!</h2><p>All items have been reviewed.</p>
</div>
<script>
let remaining = {len(items)};

async function postAction(url, body) {{
    const resp = await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: body ? JSON.stringify(body) : undefined,
    }});
    return resp.json();
}}

function removeCard(id) {{
    document.getElementById('card-' + id).style.display = 'none';
    remaining--;
    document.getElementById('count').textContent = remaining;
    if (remaining === 0) document.getElementById('all-done').style.display = 'block';
}}

async function approve(id) {{
    await postAction('/approve/' + id);
    removeCard(id);
}}

async function correctItem(id) {{
    const filename = document.getElementById('fn-' + id).value;
    const directory = document.getElementById('dir-' + id).value;
    await postAction('/correct/' + id, {{ filename, directory }});
    removeCard(id);
}}

async function skipItem(id) {{
    await postAction('/skip/' + id);
    removeCard(id);
}}
</script>
</body></html>"""
