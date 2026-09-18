"""Ingestion: load a PDF, convert pages to PIL Images, and fingerprint rendered pages."""
from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image

logger = logging.getLogger(__name__)

DPI = 150
PAGE_HASH_DOMAIN = b"docflow.page.v1\0"
DOCUMENT_HASH_DOMAIN = b"docflow.document.v1\0"
NEAR_DUPLICATE_MAX_DISTANCE = 2  # dHash bits; a review signal, never exact dedup


@dataclass(frozen=True)
class PageFingerprint:
    page_number: int  # 1-indexed
    content_sha256: str
    dhash64: str  # 16 hex digits


@dataclass(frozen=True)
class SourceFingerprint:
    raw_sha256: str  # evidence only; never the duplicate key
    page_count: int
    pages: tuple[PageFingerprint, ...]
    document_sha256: str


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


def page_content_sha256(image: Image.Image) -> str:
    """SHA-256 over framed pixels: domain, mode, width, height, byte length, bytes."""
    pixels = image.tobytes()
    mode = image.mode.encode("ascii")
    digest = hashlib.sha256(PAGE_HASH_DOMAIN)
    digest.update(struct.pack(">B", len(mode)) + mode)
    digest.update(struct.pack(">QQQ", image.width, image.height, len(pixels)))
    digest.update(pixels)
    return digest.hexdigest()


def document_sha256(page_hashes: list[str]) -> str:
    """SHA-256 over the page count and each 32-byte page hash in page order."""
    digest = hashlib.sha256(DOCUMENT_HASH_DOMAIN)
    digest.update(struct.pack(">Q", len(page_hashes)))
    for page_hash in page_hashes:
        digest.update(bytes.fromhex(page_hash))
    return digest.hexdigest()


def dhash64(image: Image.Image) -> str:
    """64-bit difference hash: 9x8 grayscale, one bit per horizontal gradient."""
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = pixels[row * 9 + col], pixels[row * 9 + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:016x}"


def hamming_distance(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


def raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_pdf(pdf_path: Path) -> SourceFingerprint:
    """Fingerprint canonical 150 DPI grayscale renderings of every page."""
    images = [image if image.mode == "L" else image.convert("L")
              for image in convert_from_path(str(pdf_path), dpi=DPI, grayscale=True)]
    pages = tuple(PageFingerprint(n, page_content_sha256(image), dhash64(image))
                  for n, image in enumerate(images, start=1))
    return SourceFingerprint(raw_sha256(pdf_path), len(pages), pages,
                             document_sha256([p.content_sha256 for p in pages]))
