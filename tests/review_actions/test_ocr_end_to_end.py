"""Real local OCR: Tesseract reads a synthetic image-only PDF and the text persists.

This test deliberately uses no mocks for OCR. It proves the fixture is legible to
the installed Tesseract and that processing stores one durable outcome per page.
"""
from __future__ import annotations

import pytest

from docflow.ingestion import loader
from docflow.ocr import analyzer
from tests.reliability.synthetic import write_text_pdf
from tests.review_actions.support import text_review_job

STATEMENT = ["SYNTHETIC UTILITY STATEMENT", "PERIOD FEBRUARY 2026", "TOTAL DUE 42.00"]
NOTICE = ["SYNTHETIC RENEWAL NOTICE", "PLEASE REPLY BY MARCH 2026"]


def test_real_tesseract_reads_the_synthetic_text_fixture(tmp_path) -> None:
    pdf = write_text_pdf(tmp_path / "scan.pdf", [STATEMENT, []])

    records = analyzer.analyze_pages(loader.load_pdf(pdf))

    assert len(records) == 2
    first = records[0].raw_text.upper()
    for line in STATEMENT:
        assert line in first, records[0].raw_text
    assert records[1].raw_text.strip() == ""  # the blank page yields no text


def test_processing_persists_one_ocr_outcome_per_page_and_serves_it(env, client,
                                                                     monkeypatch) -> None:
    """A real OCR run of a four-page scan; page 4 is blank, so it is ``no_text``."""
    job_id, items = text_review_job(env, [STATEMENT, NOTICE, STATEMENT, []])
    source = env.filer().source_path(env.scope_id, f"archive:{_original(env, job_id)}")
    records = analyzer.analyze_pages(loader.load_pdf(source))
    env.store.jobs.record_ocr(env.scope_id, job_id, analyzer.ocr_outcomes(records))

    item = items[(3, 4)]
    third = client.get(f"/api/v1/review-items/{item.id}/pages/3/text",
                       params={"archive_scope_id": env.scope_id}).json()
    fourth = client.get(f"/api/v1/review-items/{item.id}/pages/4/text",
                        params={"archive_scope_id": env.scope_id}).json()

    assert third["status"] == "extracted"
    assert "SYNTHETIC UTILITY STATEMENT" in third["text"].upper()
    assert fourth == {"review_item_id": item.id, "job_id": job_id, "page_number": 4,
                      "status": "no_text", "text": None, "error_code": None}

    listed = {tuple(i["page_numbers"]): i
              for i in client.get("/api/v1/review-items",
                                  params={"archive_scope_id": env.scope_id}).json()["items"]}
    assert listed[(3, 4)]["text_extraction"] == "mixed"
    assert listed[(2,)]["text_extraction"] == "extracted"


def test_an_incomplete_analysis_leaves_no_partial_ocr_batch(env, monkeypatch) -> None:
    job_id, _ = text_review_job(env, [STATEMENT, NOTICE])
    before = [tuple(r) for r in env.db.connection.execute(
        "SELECT page_number, ocr_status, ocr_text FROM job_pages WHERE job_id = ?", (job_id,))]

    with pytest.raises(Exception):
        env.store.jobs.record_ocr(env.scope_id, job_id, analyzer.ocr_outcomes(
            analyzer.analyze_pages([])))  # analysis produced no pages at all

    assert [tuple(r) for r in env.db.connection.execute(
        "SELECT page_number, ocr_status, ocr_text FROM job_pages WHERE job_id = ?",
        (job_id,))] == before


def _original(env, job_id: str) -> str:
    (record,) = [r for r in env.store.files.list_for_job(env.scope_id, job_id)
                 if r.role == "original"]
    return record.relative_path
