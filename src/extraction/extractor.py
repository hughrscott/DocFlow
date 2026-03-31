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

        output_path = target_dir / decision.filename
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
