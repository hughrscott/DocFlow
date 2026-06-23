"""Content-hash deduplication for filed documents."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

HASH_DB_FILENAME = "content_hashes.json"


def _hash_db_path() -> Path:
    """Return path to the hash database."""
    db_dir = Path.home() / ".docflow"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / HASH_DB_FILENAME


def _load_hashes() -> dict[str, str]:
    """Load the hash database. Returns {hash: filepath}."""
    path = _hash_db_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return {}


def _save_hashes(hashes: dict[str, str]) -> None:
    """Save the hash database."""
    with open(_hash_db_path(), "w") as f:
        json.dump(hashes, f, indent=2)


def hash_pdf_content(pdf_path: Path) -> str:
    """Compute a content hash for a PDF file.

    Uses SHA-256 of the raw file bytes. Fast and reliable for
    detecting exact duplicates.
    """
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def is_duplicate(pdf_path: Path) -> tuple[bool, str | None]:
    """Check if a PDF is a duplicate of an already-filed document.

    Returns (is_dup, existing_path) where existing_path is the path
    of the original file if it's a duplicate.
    """
    content_hash = hash_pdf_content(pdf_path)
    hashes = _load_hashes()

    if content_hash in hashes:
        existing = hashes[content_hash]
        # Verify the original still exists
        if Path(existing).exists():
            return True, existing
        else:
            # Original was deleted, remove stale entry
            del hashes[content_hash]
            _save_hashes(hashes)

    return False, None


def register_file(pdf_path: Path) -> None:
    """Register a filed document in the hash database."""
    content_hash = hash_pdf_content(pdf_path)
    hashes = _load_hashes()
    hashes[content_hash] = str(pdf_path)
    _save_hashes(hashes)


def is_empty() -> bool:
    """Check if the hash database is empty or doesn't exist."""
    hashes = _load_hashes()
    return len(hashes) == 0


def build_initial_index(archive_root: str | Path, progress_callback=None) -> int:
    """Scan the entire archive and build the hash database from scratch.

    Only runs when the hash database is empty. Returns the number of
    files indexed.

    Args:
        archive_root: Root directory of the archive.
        progress_callback: Optional callable(indexed, total) for progress.
    """
    root = Path(os.path.expanduser(str(archive_root)))
    if not root.exists():
        return 0

    # Collect all PDFs (skip system folders like _Unmatched, _Skipped, _cache)
    pdfs = []
    for pdf in root.rglob("*.pdf"):
        rel_parts = pdf.relative_to(root).parts
        if any(part.startswith("_") or part.startswith(".") for part in rel_parts):
            continue
        pdfs.append(pdf)

    if not pdfs:
        return 0

    hashes = _load_hashes()
    indexed = 0

    for i, pdf in enumerate(pdfs):
        try:
            content_hash = hash_pdf_content(pdf)
            if content_hash not in hashes:
                hashes[content_hash] = str(pdf)
                indexed += 1
            if progress_callback:
                progress_callback(i + 1, len(pdfs))
            # Save every 50 files so progress isn't lost
            if indexed % 50 == 0 and indexed > 0:
                _save_hashes(hashes)
        except Exception as exc:
            logger.warning("Failed to hash %s: %s", pdf, exc)

    _save_hashes(hashes)
    logger.info("Built initial hash index: %d files indexed from %s", indexed, root)
    return indexed
