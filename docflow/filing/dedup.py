"""Content deduplication for filed documents, backed by application-state SQLite.

The duplicate key is the canonical rendered-pixel document fingerprint (150 DPI,
8-bit grayscale, page order and count). Extracted text, page dimensions, file names
and page counts are never sufficient. The legacy ``~/.docflow/content_hashes.json``
index is no longer read or written; Phase 1 migration inventories it and leaves it
untouched. Without a state store these adapters are inert rather than falling back
to a JSON file.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path

from pypdf import PdfWriter

from docflow.filing.operations import confined
from docflow.ingestion.loader import fingerprint_pdf
from docflow.state.repositories import StateStore, UnsafeValueError

logger = logging.getLogger(__name__)


def hash_pdf_content(pdf_path: Path) -> str:
    """Rendered-pixel document fingerprint of every page of *pdf_path*, in order."""
    return fingerprint_pdf(Path(pdf_path)).document_sha256


def hash_pages(reader, page_numbers: list[int]) -> str:
    """Rendered-pixel fingerprint of 1-indexed *page_numbers* from a PdfReader."""
    writer = PdfWriter()
    for page in page_numbers:
        writer.add_page(reader.pages[page - 1])
    buffer = io.BytesIO()
    writer.write(buffer)
    with tempfile.TemporaryDirectory(prefix="docflow-hash-") as scratch:
        path = Path(scratch) / "pages.pdf"
        path.write_bytes(buffer.getvalue())
        return hash_pdf_content(path)


def _scope_root(store: StateStore, scope_id: str) -> Path:
    return Path(store.scopes.get(scope_id).canonical_root)


def is_duplicate(
    pdf_path: Path, *, store: StateStore | None = None, scope_id: str | None = None
) -> tuple[bool, str | None]:
    """(True, archive-relative path) when a recorded filed PDF still renders identically."""
    if store is None:
        return False, None
    expected = hash_pdf_content(pdf_path)
    root = _scope_root(store, scope_id)
    for record in store.files.find_by_content(scope_id, expected, "filed"):
        try:
            path = confined(root, record.relative_path)
        except UnsafeValueError:
            continue
        if path.is_file() and not path.is_symlink() and hash_pdf_content(path) == expected:
            return True, record.relative_path
    return False, None


def register_file(
    pdf_path: Path,
    *,
    store: StateStore | None = None,
    scope_id: str | None = None,
    job_id: str | None = None,
    page_numbers: list[int] | None = None,
) -> None:
    """Record an archive PDF as filed; no-op without a state store."""
    if store is None:
        return
    root = _scope_root(store, scope_id)
    fingerprint = fingerprint_pdf(Path(pdf_path))
    relative = Path(pdf_path).resolve().relative_to(root).as_posix()
    store.files.add(scope_id, job_id=job_id, relative_path=relative,
                    content_sha256=fingerprint.document_sha256,
                    page_numbers=page_numbers or list(range(1, fingerprint.page_count + 1)),
                    role="filed")


def is_empty(*, store: StateStore | None = None, scope_id: str | None = None) -> bool:
    """True only when a scope has no file records; without state there is nothing to build."""
    if store is None:
        return False
    store.files.require_scope(scope_id)
    row = store.db.connection.execute(
        "SELECT 1 FROM file_records WHERE archive_scope_id = ? LIMIT 1", (scope_id,)
    ).fetchone()
    return row is None


def build_initial_index(
    archive_root: str | Path,
    progress_callback=None,
    *,
    store: StateStore | None = None,
    scope_id: str | None = None,
) -> int:
    """Record existing archive PDFs as filed file records; returns how many were added.

    Skips ``_``/``.`` folders, symlinks and paths that already have a record, so
    migrated history is never altered. Without a state store nothing is indexed.
    """
    if store is None:
        return 0
    root = _scope_root(store, scope_id)
    if Path(os.path.expanduser(str(archive_root))).resolve() != root:
        raise UnsafeValueError("archive root does not match the archive scope")
    pdfs = sorted(
        pdf for pdf in root.rglob("*.pdf")
        if not any(part.startswith(("_", ".")) for part in pdf.relative_to(root).parts)
        and not pdf.is_symlink() and pdf.is_file()
    )
    indexed = 0
    for position, pdf in enumerate(pdfs, start=1):
        relative = pdf.relative_to(root).as_posix()
        if store.files.get_by_path(scope_id, relative) is None:
            try:
                register_file(pdf, store=store, scope_id=scope_id)
                indexed += 1
            except Exception as exc:  # noqa: BLE001 - unreadable PDFs are reported, not indexed
                logger.warning("Failed to fingerprint %s: %s", relative, type(exc).__name__)
        if progress_callback:
            progress_callback(position, len(pdfs))
    logger.info("Indexed %d archive PDF(s) into application state", indexed)
    return indexed
