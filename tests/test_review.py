"""Tests for src/review/ — queue persistence and server endpoints."""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from pathlib import Path

from src.review.queue import save_review_queue, load_review_queue, update_queue_item
from src.review.server import app, configure
from src.classification.classifier import FilingDecision
from src.clustering.clusterer import DocumentCandidate


def _make_decision(
    pages=(1,),
    institution="test_bank",
    confidence=0.5,
    filename="Test.pdf",
    target_dir="/tmp/test",
) -> FilingDecision:
    candidate = DocumentCandidate(
        pages=list(pages),
        institution=institution,
        account=None,
        period="January2026",
        doc_type="statement",
        clustering_confidence=0.6,
        raw_signals={"raw_texts": ["Sample OCR text for testing"]},
    )
    return FilingDecision(
        candidate=candidate,
        filename=filename,
        target_directory=target_dir,
        rule_matched="none",
        confidence=confidence,
        auto_file=False,
        notes="Test decision",
    )


@pytest.fixture
def tmp_config(tmp_path):
    return {"archive_root": str(tmp_path)}


class TestQueuePersistence:
    def test_save_and_load(self, tmp_config):
        decisions = [_make_decision(pages=(1, 2)), _make_decision(pages=(3,))]
        path = save_review_queue(decisions, Path("/tmp/test.pdf"), tmp_config)
        assert path is not None
        assert path.exists()

        items = load_review_queue(tmp_config)
        assert len(items) == 2
        assert items[0]["pages"] == [1, 2]
        assert items[1]["pages"] == [3]
        assert items[0]["status"] == "pending"

    def test_empty_queue_returns_none(self, tmp_config):
        result = save_review_queue([], Path("/tmp/test.pdf"), tmp_config)
        assert result is None

    def test_append_to_existing(self, tmp_config):
        save_review_queue([_make_decision(pages=(1,))], Path("/tmp/a.pdf"), tmp_config)
        save_review_queue([_make_decision(pages=(2,))], Path("/tmp/b.pdf"), tmp_config)
        items = load_review_queue(tmp_config)
        assert len(items) == 2

    def test_update_item(self, tmp_config):
        save_review_queue([_make_decision(pages=(1,))], Path("/tmp/test.pdf"), tmp_config)
        items = load_review_queue(tmp_config)
        item_id = items[0]["id"]

        updated = update_queue_item(tmp_config, item_id, {"status": "approved"})
        assert updated is not None
        assert updated["status"] == "approved"

        # Should no longer appear in pending
        pending = load_review_queue(tmp_config)
        assert len(pending) == 0

    def test_update_nonexistent_item(self, tmp_config):
        save_review_queue([_make_decision()], Path("/tmp/test.pdf"), tmp_config)
        result = update_queue_item(tmp_config, "nonexistent_id", {"status": "approved"})
        assert result is None

    def test_load_empty_queue(self, tmp_config):
        items = load_review_queue(tmp_config)
        assert items == []


class TestReviewServer:
    @pytest.fixture
    def client_with_queue(self, tmp_path):
        config = {"archive_root": str(tmp_path)}
        configure(config)

        # Create a source PDF (just a placeholder for the test)
        source_pdf = tmp_path / "test_scan.pdf"
        source_pdf.write_bytes(b"%PDF-1.4 fake pdf content")

        save_review_queue(
            [_make_decision(pages=(1,), filename="TestDoc.pdf")],
            source_pdf,
            config,
        )
        return TestClient(app), config

    def test_index_returns_html(self, client_with_queue):
        client, _ = client_with_queue
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Review Queue" in resp.text
        assert "TestDoc.pdf" in resp.text

    def test_queue_returns_json(self, client_with_queue):
        client, _ = client_with_queue
        resp = client.get("/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending"] == 1
        assert len(data["items"]) == 1

    def test_skip_item(self, client_with_queue):
        client, _ = client_with_queue
        items = client.get("/queue").json()["items"]
        item_id = items[0]["id"]

        # Mock extraction since we don't have a real PDF in tests
        with patch("src.review.server._extract_item"):
            resp = client.post(f"/skip/{item_id}")
            assert resp.status_code == 200

        # Queue should be empty
        remaining = client.get("/queue").json()
        assert remaining["pending"] == 0

    def test_approve_nonexistent(self, client_with_queue):
        client, _ = client_with_queue
        resp = client.post("/approve/nonexistent_id")
        assert resp.status_code == 404

    def test_empty_queue_page(self, tmp_path):
        config = {"archive_root": str(tmp_path)}
        configure(config)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "All clear" in resp.text
