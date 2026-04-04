"""Extraction: use pypdf to extract pages and write named PDFs."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from src.classification.classifier import FilingDecision
from src.filing.filer import ensure_directory

logger = logging.getLogger(__name__)


def _is_likely_duplicate(existing_path: Path, new_page_count: int) -> bool:
    """Check if an existing file is likely the same document.

    Compares page count as a quick heuristic. Same page count from same
    filename template = likely duplicate.
    """
    try:
        existing_reader = PdfReader(str(existing_path))
        return len(existing_reader.pages) == new_page_count
    except Exception:
        return False


def _unique_path(path: Path, page_count: int = 0) -> Path | None:
    """Resolve filename collisions.

    If *path* already exists:
    - If it's a likely duplicate (same page count), return None to skip.
    - If it's a different document, append _2, _3, etc.

    Returns the path to write to, or None if it's a duplicate to skip.
    """
    if not path.exists():
        return path

    if page_count > 0 and _is_likely_duplicate(path, page_count):
        logger.info("Skipping likely duplicate: %s", path)
        return None

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def extract_documents(
    source_pdf: Path,
    decisions: list[FilingDecision],
    config: dict,
) -> list[Path]:
    """Extract pages from *source_pdf* and write each FilingDecision to disk.

    Returns a list of paths to the written files.
    """
    reader = PdfReader(str(source_pdf))
    written_files: list[Path] = []

    for decision in decisions:
        target_dir = Path(decision.target_directory)
        ensure_directory(target_dir)

        page_count = len(decision.candidate.pages)
        output_path = _unique_path(
            target_dir / decision.filename, page_count=page_count
        )

        if output_path is None:
            # Duplicate detected — skip extraction
            logger.info(
                "Skipped duplicate: pages %s → %s",
                decision.candidate.pages, decision.filename,
            )
            written_files.append(target_dir / decision.filename)
            continue

        writer = PdfWriter()

        for page_num in decision.candidate.pages:
            # page_num is 1-indexed, pypdf uses 0-indexed
            writer.add_page(reader.pages[page_num - 1])

        with open(output_path, "wb") as f:
            writer.write(f)

        written_files.append(output_path)
        logger.info(
            "Extracted: pages %s → %s",
            decision.candidate.pages, output_path,
        )

    # Write filing log
    _write_filing_log(decisions, written_files, config)

    return written_files


def _write_filing_log(
    decisions: list[FilingDecision],
    written_files: list[Path],
    config: dict,
) -> Path:
    """Write a JSON filing log to the archive root."""
    import os
    archive_root = Path(os.path.expanduser(
        config.get("archive_root", "~/ElectronicFiles")
    ))
    ensure_directory(archive_root)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = archive_root / f"filing_log_{timestamp}.json"

    entries = []
    for decision, file_path in zip(decisions, written_files):
        entries.append({
            "filename": decision.filename,
            "target_directory": decision.target_directory,
            "rule_matched": decision.rule_matched,
            "confidence": decision.confidence,
            "pages_extracted": decision.candidate.pages,
            "institution": decision.candidate.institution,
            "doc_type": decision.candidate.doc_type,
            "period": decision.candidate.period,
            "file_size_bytes": file_path.stat().st_size if file_path.exists() else 0,
        })

    with open(log_path, "w") as f:
        json.dump({"timestamp": timestamp, "entries": entries}, f, indent=2)

    logger.info("Filing log written: %s", log_path)
    return log_path
