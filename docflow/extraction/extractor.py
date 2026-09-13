"""Extraction: write named PDFs for filing decisions without overwriting or duplicating.

This is the legacy (non-journaled) adapter used by the CLI and web pipelines. Outputs
are staged outside the archive, reopened and verified against rendered-pixel page
fingerprints, then placed atomically under a collision-safe name. No filing log or
hash index is written to the archive; durable jobs use ``docflow.filing.operations``.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from pypdf import PdfReader

from docflow.classification.classifier import FilingDecision
from docflow.filing.filer import ensure_directory
from docflow.filing.operations import (
    FilingError,
    check_page_numbers,
    collision_candidates,
    partial_path,
    place_without_replacing,
    verified_fingerprint,
    write_pdf_pages,
)
from docflow.ingestion.loader import document_sha256, fingerprint_pdf

logger = logging.getLogger(__name__)


def _renders_as(path: Path, expected: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return fingerprint_pdf(path).document_sha256 == expected
    except Exception:  # noqa: BLE001 - any unreadable file is a different document
        return False


def extract_documents(
    source_pdf: Path,
    decisions: list[FilingDecision],
    config: dict,
) -> list[Path]:
    """Extract pages from *source_pdf* for each FilingDecision.

    Returns, per decision, the path now holding that document: a newly written file,
    or an existing file whose rendered pages are identical (exact duplicate).
    Duplicate, out-of-range or empty page assignments raise before anything is written.
    """
    with open(source_pdf, "rb") as fh:
        page_count = len(PdfReader(fh).pages)
    check_page_numbers(page_count, [d.candidate.pages for d in decisions], require_all=False)
    source = fingerprint_pdf(source_pdf)
    hashes = {p.page_number: p.content_sha256 for p in source.pages}
    written: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="docflow-stage-") as staging:
        for index, decision in enumerate(decisions):
            pages = decision.candidate.pages
            expected = document_sha256([hashes[n] for n in pages])
            target_dir = Path(decision.target_directory)
            ensure_directory(target_dir)
            staged = Path(staging) / f"{index}.pdf"
            write_pdf_pages(source_pdf, pages, staged)
            verified_fingerprint(staged, len(pages), expected)
            for relative in collision_candidates(".", decision.filename):
                candidate = target_dir / Path(relative).name
                if os.path.lexists(candidate):
                    if _renders_as(candidate, expected):
                        logger.info("Duplicate: pages %s already filed as %s", pages, candidate)
                        decision.notes = f"Duplicate — already filed as {candidate.name}"
                        break
                    continue
                try:
                    place_without_replacing(staged, candidate,
                                            partial_path(target_dir, "extract", index))
                except FileExistsError:
                    continue
                logger.info("Extracted: pages %s → %s", pages, candidate)
                break
            else:
                raise FilingError("collision_limit_exceeded")
            written.append(candidate)

    return written
