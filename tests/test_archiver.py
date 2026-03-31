"""Tests for src/ingestion/archiver."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.ingestion.archiver import archive_original


class TestArchiveOriginal:
    def test_moves_file(self, tmp_path):
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-fake")
        watch = tmp_path / "watch"
        watch.mkdir()

        config = {"scan_watch_folder": str(watch)}
        dest = archive_original(pdf, config)

        assert dest.exists()
        assert not pdf.exists()
        assert dest.name == "scan.pdf"

    def test_correct_directory_name(self, tmp_path):
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-fake")
        watch = tmp_path / "watch"
        watch.mkdir()

        config = {"scan_watch_folder": str(watch)}
        dest = archive_original(pdf, config)

        date_str = datetime.now().strftime("%m%d%y")
        assert f"BeenOrganized{date_str}" in str(dest.parent)

    def test_appends_to_existing_dir(self, tmp_path):
        watch = tmp_path / "watch"
        date_str = datetime.now().strftime("%m%d%y")
        existing_dir = watch / f"BeenOrganized{date_str}"
        existing_dir.mkdir(parents=True)

        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-fake")

        config = {"scan_watch_folder": str(watch)}
        dest = archive_original(pdf, config)
        assert dest.exists()
