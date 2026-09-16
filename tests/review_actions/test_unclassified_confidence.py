"""A run that never classified anything reports no confidence, not a confident zero.

Local-only mode performs text extraction and no AI classification, so a document that
matched no filing rule has no confidence to report. The server says so — ``null`` — and
never a numeric ``0.0`` the UI would render as a red 0% match. A decision that really was
classified keeps its number.

The pipeline runs end to end against synthetic OCR, a temp archive and temp application
state; the model transport is deny-all and no network is used.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.reliability.synthetic import write_image_pdf
from tests.review_actions.test_processing_bridge import (  # noqa: F401  (fixture)
    run_ui,
    synthetic_ocr,
    wait_for,
    write_config,
)


def _run(tmp_path: Path, monkeypatch, **config_overrides) -> dict:
    """Process one synthetic scan and return the status body plus its review items."""
    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    archive.mkdir()
    inbox.mkdir()
    scan = write_image_pdf(tmp_path / "scanner/scan.pdf", [1, 2])
    config = write_config(tmp_path, archive, inbox, **config_overrides)
    captured: dict = {}

    def process(client) -> None:
        uploaded = client.post("/api/upload", files={
            "file": ("scan.pdf", scan.read_bytes(), "application/pdf")})
        started = client.post("/api/process", json={"path": uploaded.json()["path"]})
        assert started.status_code == 200, started.text
        captured["state"] = wait_for(client, started.json()["job_id"])
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        captured["items"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id, "status": "pending"}).json()["items"]

    run_ui(config, monkeypatch, process)
    assert captured["state"]["status"] == "completed", captured["state"]
    return captured


@pytest.fixture()
def unclassified(tmp_path: Path, isolated_home, monkeypatch, synthetic_ocr) -> dict:  # noqa: F811
    """Local-only, no filing rules: nothing can be classified."""
    return _run(tmp_path, monkeypatch, filing_rules=[])


def test_status_reports_no_confidence_for_a_document_that_was_never_classified(
    unclassified,
) -> None:
    documents = unclassified["state"]["documents"]
    assert documents, unclassified["state"]
    assert [d["rule"] for d in documents] == ["none"]
    assert [d["confidence"] for d in documents] == [None]


def test_review_items_report_no_confidence_for_an_unmatched_document(unclassified) -> None:
    (item,) = unclassified["items"]
    assert item["reason"] == "unmatched"
    assert item["confidence"] is None


def test_a_rule_matched_document_keeps_its_numeric_confidence(
    tmp_path: Path, isolated_home, monkeypatch, synthetic_ocr,  # noqa: F811
) -> None:
    # The default synthetic rule matches, and the above-1.0 threshold keeps the
    # decision in review — so a real, low, numeric confidence still reaches the UI.
    captured = _run(tmp_path, monkeypatch)

    (document,) = captured["state"]["documents"]
    assert document["rule"] == "synthetic_statement"
    assert isinstance(document["confidence"], float)
    assert 0.0 < document["confidence"] <= 1.0

    (item,) = captured["items"]
    assert item["reason"] == "low_confidence"
    assert item["confidence"] == document["confidence"]
