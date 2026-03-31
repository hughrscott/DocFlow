"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter


@pytest.fixture()
def minimal_pdf(tmp_path: Path) -> Path:
    """Write a 3-page blank PDF to a temp directory and return its path."""
    pdf_path = tmp_path / "test_scan.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)  # letter size in points
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path
