"""Every finished submission reports one typed outcome, never a guess from a count.

``/api/process/status`` carries ``outcome``: ``new``, ``new_empty``, ``duplicate`` or
``failed``. The pipeline is the real one here — real Tesseract, real durable state — so a
duplicate really is recognised from the stored bytes rather than from the file name.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docflow.web import app as web_app
from tests.reliability.synthetic import write_text_pdf

STATEMENT = ["SYNTHETIC UTILITY STATEMENT", "PERIOD FEBRUARY 2026", "TOTAL DUE 42.00"]
NOTICE = ["SYNTHETIC RENEWAL NOTICE", "PLEASE REPLY BY MARCH 2026"]
MALFORMED = b"%PDF-1.4\nthis synthetic fixture is not a readable PDF body\n"


@pytest.fixture()
def live(env, monkeypatch: pytest.MonkeyPatch):
    """The shipped app wired to real local state and the real durable pipeline."""
    uploads = env.state.cache / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    filer = env.filer()
    filer.source_roots["upload"] = uploads
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", filer)
    monkeypatch.setattr(web_app, "_active_scope_id", env.scope_id)
    monkeypatch.setattr(web_app, "_config", {
        "archive_root": str(env.archive), "scan_watch_folder": str(env.watch),
        "privacy_mode": "local_only", "confidence_threshold": 0.75})
    monkeypatch.setattr(web_app, "_processing_state", {})
    monkeypatch.setattr(web_app, "_submissions", {})
    env.uploads = uploads
    return env


@pytest.fixture()
def app_client(live):
    with TestClient(web_app.app) as client:
        yield client


def _pdf(tmp_path: Path, name: str, pages: list[list[str]]) -> bytes:
    return write_text_pdf(tmp_path / name, pages).read_bytes()


def _submit(client: TestClient, name: str, content: bytes, key: str) -> dict:
    upload = client.post("/api/upload",
                         files={"file": (name, content, "application/pdf")})
    assert upload.status_code == 200, upload.text
    started = client.post("/api/process", json={"path": upload.json()["path"],
                                                "submission_key": key})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    for _ in range(3000):
        state = client.get(f"/api/process/status/{job_id}").json()
        if state["status"] in ("completed", "error"):
            return state
        time.sleep(0.02)
    raise AssertionError("the pipeline never finished")


def _pdfs(env) -> list[Path]:
    return sorted(p for p in env.archive.rglob("*.pdf"))


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------

def test_a_genuinely_new_batch_reports_the_new_outcome(live, app_client, tmp_path) -> None:
    state = _submit(app_client, "scan-one.pdf",
                    _pdf(tmp_path, "scan-one.pdf", [STATEMENT, NOTICE]), "s1")

    assert state["status"] == "completed"
    assert state["outcome"] == "new"
    assert state["documents_total"] >= 1
    assert state["durable_job_id"]
    assert state["review_url"] == f"/review?job_id={state['durable_job_id']}"
    assert live.store.jobs.count(live.scope_id) == 1


# ---------------------------------------------------------------------------
# duplicate: the same bytes, under the same name and under a new one
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("second_name", ["scan-one.pdf", "renamed-copy.pdf"])
def test_the_same_bytes_offered_again_are_already_imported(live, app_client, tmp_path,
                                                           second_name) -> None:
    content = _pdf(tmp_path, "scan-one.pdf", [STATEMENT, NOTICE])
    first = _submit(app_client, "scan-one.pdf", content, "s1")
    files_after_first = [p.relative_to(live.archive).as_posix() for p in _pdfs(live)]
    pending_after_first = len(live.store.reviews.list(live.scope_id, "pending"))

    second = _submit(app_client, second_name, content, "s2")

    assert second["outcome"] == "duplicate"
    assert second["status"] == "completed"
    detail = second["outcome_detail"]
    assert detail["job_id"] == first["durable_job_id"]
    assert detail["source_name"] == "scan-one.pdf"
    assert detail["created_at"] and detail["status"] in ("review", "completed")
    # Nothing new was created: no second job, no second review item, no second file.
    assert live.store.jobs.count(live.scope_id) == 1
    assert len(live.store.reviews.list(live.scope_id, "pending")) == pending_after_first
    assert [p.relative_to(live.archive).as_posix() for p in _pdfs(live)] == files_after_first
    # The run reports no documents of its own and no stale link to a new batch.
    assert second["documents"] == [] and second["documents_total"] == 0
    assert second["durable_job_id"] is None and second["review_url"] is None


def test_a_duplicate_offers_the_existing_batch_when_it_still_needs_review(
    live, app_client, tmp_path,
) -> None:
    content = _pdf(tmp_path, "scan-one.pdf", [STATEMENT, NOTICE])
    first = _submit(app_client, "scan-one.pdf", content, "s1")
    assert len(live.store.reviews.list(live.scope_id, "pending")) > 0

    detail = _submit(app_client, "scan-one.pdf", content, "s2")["outcome_detail"]

    assert detail["pending_review"] > 0
    assert detail["review_url"] == f"/review?job_id={first['durable_job_id']}"


def test_a_duplicate_reports_only_the_stages_that_really_ran(live, app_client,
                                                              tmp_path) -> None:
    content = _pdf(tmp_path, "scan-one.pdf", [STATEMENT, NOTICE])
    _submit(app_client, "scan-one.pdf", content, "s1")

    stages = {s["id"]: s["status"] for s in _submit(app_client, "scan-one.pdf", content,
                                                    "s2")["stages"]}

    assert stages["checking"] == "done"
    assert stages["ocr"] == "skipped"
    assert stages["filing"] == "skipped"
    assert stages["done"] == "skipped"


def test_a_duplicate_leaves_no_second_copy_staged_in_application_state(
    live, app_client, tmp_path,
) -> None:
    content = _pdf(tmp_path, "scan-one.pdf", [STATEMENT, NOTICE])
    _submit(app_client, "scan-one.pdf", content, "s1")

    _submit(app_client, "renamed-copy.pdf", content, "s2")

    assert sorted(p.name for p in live.uploads.iterdir()) == []


# ---------------------------------------------------------------------------
# failed
# ---------------------------------------------------------------------------

def test_an_unreadable_scan_reports_the_failed_outcome_with_no_stale_links(
    live, app_client, tmp_path,
) -> None:
    good = _pdf(tmp_path, "scan-one.pdf", [STATEMENT, NOTICE])
    _submit(app_client, "scan-one.pdf", good, "s1")

    state = _submit(app_client, "broken.pdf", MALFORMED, "s2")

    assert state["status"] == "error"
    assert state["outcome"] == "failed"
    assert state["filename"] == "broken.pdf"
    assert state["durable_job_id"] is None and state["review_url"] is None
    assert state["documents"] == [] and state["documents_total"] == 0
    assert state["outcome_detail"]["message"]
    # The failed attempt recorded no batch of its own.
    assert live.store.jobs.count(live.scope_id) == 1


# ---------------------------------------------------------------------------
# new_empty is a processed batch that produced nothing, never a reused one
# ---------------------------------------------------------------------------

def test_outcome_typing_distinguishes_an_empty_run_from_a_reused_one() -> None:
    assert web_app.intake_outcome(finished=True, documents_total=2) == "new"
    assert web_app.intake_outcome(finished=True, documents_total=0) == "new_empty"
    assert web_app.intake_outcome(finished=False, documents_total=0) == "failed"
    assert web_app.intake_outcome(finished=False, documents_total=3) == "failed"


def test_an_empty_processed_batch_explains_itself(live, app_client, tmp_path,
                                                   monkeypatch) -> None:
    """A run that really processed the scan but found no document says exactly that."""
    from docflow.filing import processing
    from docflow.filing.processing import ProcessingResult

    def empty_run(store, filer, scope_id, locator, config, *, progress=None):
        return ProcessingResult("durable-empty", "completed", [], [], {"files": []})

    monkeypatch.setattr(processing, "process_scan", empty_run)
    state = _submit(app_client, "scan-one.pdf",
                    _pdf(tmp_path, "scan-one.pdf", [STATEMENT]), "s1")

    assert state["outcome"] == "new_empty"
    assert state["documents_total"] == 0
    assert "no document" in state["outcome_detail"]["message"].lower()
