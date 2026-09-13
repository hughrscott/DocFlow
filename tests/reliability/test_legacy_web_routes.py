"""Legacy web routes touched by Phase 3 lint cleanup keep their observable behavior."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docflow.web import app as web_app
from tests.reliability.synthetic import write_image_pdf


@pytest.fixture()
def archive(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(web_app, "_config", {"archive_root": str(root)})
    return root


def test_upload_writes_exact_bytes_and_reports_size(archive: Path) -> None:
    payload = write_image_pdf(archive.parent / "scan.pdf", [1, 2]).read_bytes()
    response = TestClient(web_app.app).post(
        "/api/upload", files={"file": ("scan.pdf", payload, "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert (body["filename"], body["size"]) == ("scan.pdf", len(payload))
    assert Path(body["path"]).read_bytes() == payload
    rejected = TestClient(web_app.app).post(
        "/api/upload", files={"file": ("scan.txt", b"x", "text/plain")})
    assert rejected.status_code == 400


def test_search_logs_and_files_without_duplicates(archive: Path) -> None:
    (archive / "filing_log_20260330_101500.json").write_text(json.dumps({"entries": [
        {"filename": "Statement.pdf", "target_directory": "/synthetic/Household/PNC",
         "rule_matched": "synthetic_rule", "confidence": 0.9}]}))
    write_image_pdf(archive / "Household/PNC/Statement.pdf", [1])
    write_image_pdf(archive / "Tax/StatementCopy.pdf", [2])
    write_image_pdf(archive / "_Unmatched/StatementHidden.pdf", [3])
    client = TestClient(web_app.app)

    results = client.get("/api/search", params={"q": "statement"}).json()["results"]
    logs = client.get("/api/archive/logs").json()["logs"]

    assert results == [
        {"filename": "Statement.pdf", "directory": "Household/PNC", "confidence": 0.9,
         "rule": "synthetic_rule", "icon": "description", "url": "/archive"},
        {"filename": "StatementCopy.pdf", "directory": "Tax", "icon": "folder_open",
         "url": "/archive"},
    ]
    assert logs == [json.loads((archive / "filing_log_20260330_101500.json").read_text())]


def test_listings_keep_naive_local_iso_modified_times(archive: Path) -> None:
    stamp = 1_775_000_000.123456
    files = [write_image_pdf(archive / "Docs/A.pdf", [1]),
             write_image_pdf(archive / "_Unmatched/B.pdf", [2])]
    for path in files:
        os.utime(path, (stamp, stamp))
    expected = datetime.fromtimestamp(stamp).isoformat()  # noqa: DTZ006 - legacy format oracle
    client = TestClient(web_app.app)

    listed = client.get("/api/archive/files", params={"path": "Docs"}).json()["files"]
    unmatched = client.get("/api/unmatched").json()["files"]

    assert [(f["name"], f["modified"]) for f in listed] == [("A.pdf", expected)]
    assert [(f["name"], f["modified"], f["pages"]) for f in unmatched] == [
        ("B.pdf", expected, 1)]
