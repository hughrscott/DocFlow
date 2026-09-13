"""Hermetic fixtures for application-state tests.

Every test runs with HOME and XDG_DATA_HOME redirected into ``tmp_path`` so
no default path can resolve to the real home directory, archive, or iCloud.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pypdf import PdfWriter

REAL_HOME = Path.home()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert Path.home() == home
    assert REAL_HOME not in home.parents
    return home


def _write_pdf(path: Path, pages: int, width: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=792)
    with open(path, "wb") as fh:
        writer.write(fh)


@pytest.fixture()
def archive_root(tmp_path: Path) -> Path:
    """Synthetic archive containing only original and filed PDFs."""
    root = tmp_path / "archive"
    _write_pdf(root / "BeenOrganized033026" / "Scan-synthetic-1.pdf", 4, 612)
    _write_pdf(root / "Household" / "PNC" / "PNCBankChecking.pdf", 2, 600)
    _write_pdf(root / "Tax" / "2026 Taxes" / "TaxInvoice.pdf", 1, 590)
    return root


@pytest.fixture()
def state_root(tmp_path: Path) -> Path:
    return tmp_path / "app-state"


def tree_digest(root: Path) -> dict[str, str]:
    """Map every file under ``root`` to its SHA-256 for byte-identity checks."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
