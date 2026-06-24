"""Ingestion: load a PDF and convert pages to PIL Images."""
from __future__ import annotations

import logging
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image

logger = logging.getLogger(__name__)

DPI = 150


def load_pdf(pdf_path: Path) -> list[Image.Image]:
    """Convert each page of *pdf_path* to a PIL Image at 150 DPI.

    Raises:
        FileNotFoundError: if *pdf_path* does not exist.
        RuntimeError: if pdf2image / poppler fails to convert the file.

    Note:
        Rotation detection (retry at 90°/180° based on OCR confidence) is
        handled in Task 3 (src/ocr/analyzer.py) once Tesseract is available.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("Loading PDF: %s", pdf_path)

    try:
        images: list[Image.Image] = convert_from_path(str(pdf_path), dpi=DPI)
    except Exception as exc:
        raise RuntimeError(f"pdf2image failed on {pdf_path}: {exc}") from exc

    logger.info("Loaded %d page(s) from %s", len(images), pdf_path.name)
    return images
