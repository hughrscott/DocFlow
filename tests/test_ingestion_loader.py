"""Tests for src/ingestion/loader.py."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from src.ingestion.loader import load_pdf


def test_load_pdf_returns_correct_page_count(minimal_pdf: Path) -> None:
    pages = load_pdf(minimal_pdf)
    assert len(pages) == 3


def test_load_pdf_returns_pil_images(minimal_pdf: Path) -> None:
    pages = load_pdf(minimal_pdf)
    for page in pages:
        assert isinstance(page, Image.Image)


def test_load_pdf_images_are_150_dpi(minimal_pdf: Path) -> None:
    pages = load_pdf(minimal_pdf)
    # pdf2image stores DPI in the image info dict when available
    for page in pages:
        dpi = page.info.get("dpi")
        if dpi:
            assert dpi == (150, 150)


def test_load_pdf_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pdf(tmp_path / "does_not_exist.pdf")


def test_load_pdf_raises_for_invalid_file(tmp_path: Path) -> None:
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf")
    with pytest.raises(RuntimeError):
        load_pdf(bad_pdf)
