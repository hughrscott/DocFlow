"""One Add-scanned-mail submission starts exactly one job, and says so truthfully.

``POST /api/process`` is guarded server-side so repeated UI events, retries and
rapid or concurrent duplicate calls for one submission never launch a second job.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docflow.filing import processing
from docflow.filing.processing import PROCESS_STAGES, ProcessingResult
from docflow.web import app as web_app
from tests.reliability.synthetic import write_image_pdf


@pytest.fixture()
def intake(env, monkeypatch: pytest.MonkeyPatch):
    """A fully wired local app plus a recording stub for the durable pipeline.

    ``intake.gate`` is released by default; clearing it holds every pipeline run at
    its first stage so a submission can be observed while it is genuinely in flight.
    """
    uploads = env.state.cache / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    filer = env.filer()
    filer.source_roots["upload"] = uploads
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", filer)
    monkeypatch.setattr(web_app, "_active_scope_id", env.scope_id)
    monkeypatch.setattr(web_app, "_config", {"archive_root": str(env.archive),
                                             "privacy_mode": "local_only"})
    monkeypatch.setattr(web_app, "_processing_state", {})
    monkeypatch.setattr(web_app, "_submissions", {})

    runs: list[str] = []
    gate = threading.Event()
    gate.set()

    def fake_process_scan(store, f, scope_id, locator, config, *, progress=None):
        runs.append(locator)
        for _stage, label, percent in PROCESS_STAGES:
            progress(label, percent)
            assert gate.wait(timeout=10), "the pipeline gate was never released"
        return ProcessingResult("durable-" + str(len(runs)), "review", [], [],
                                {"files": []})

    monkeypatch.setattr(processing, "process_scan", fake_process_scan)
    return type("Intake", (), {"env": env, "runs": runs, "uploads": uploads,
                               "gate": gate})()


@pytest.fixture()
def app_client(intake):
    """One client, one event loop: background pipeline tasks outlive a single request."""
    with TestClient(web_app.app) as client:
        yield client


def _upload(client: TestClient, name: str = "scan.pdf", *, marks=(1, 2), tmp: Path | None = None
            ) -> str:
    source = (tmp or Path("/tmp")) / name
    write_image_pdf(source, list(marks))
    response = client.post("/api/upload",
                           files={"file": (name, source.read_bytes(), "application/pdf")})
    assert response.status_code == 200, response.text
    return response.json()["path"]


def _process(client: TestClient, path: str, key: str | None = None):
    body = {"path": path}
    if key is not None:
        body["submission_key"] = key
    return client.post("/api/process", json=body)


def _started(intake, count: int) -> None:
    """Wait, bounded, until ``count`` pipeline runs have really been entered.

    A submission is accepted before its pipeline task reaches the stub, so counting
    runs immediately after the POST would race the task rather than the guard.
    """
    for _ in range(1000):
        if len(intake.runs) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"only {len(intake.runs)} pipeline runs started, expected {count}")


def _finished(client: TestClient, job_id: str) -> dict:
    for _ in range(500):
        state = client.get(f"/api/process/status/{job_id}").json()
        if state["status"] in ("completed", "error"):
            return state
        time.sleep(0.01)
    raise AssertionError("the stubbed pipeline never finished")


# ---------------------------------------------------------------------------
# Idempotency and in-flight suppression
# ---------------------------------------------------------------------------

def test_repeated_process_calls_for_one_submission_start_a_single_job(
    app_client, intake, tmp_path,
) -> None:
    path = _upload(app_client, tmp=tmp_path)

    responses = [_process(app_client, path, "submission-1") for _ in range(4)]

    assert [r.status_code for r in responses] == [200] * 4
    job_ids = {r.json()["job_id"] for r in responses}
    assert len(job_ids) == 1
    assert [r.json()["reused"] for r in responses] == [False, True, True, True]
    _finished(app_client, job_ids.pop())
    assert intake.runs == [f"upload:{Path(path).name}"]  # the pipeline ran once


def test_repeated_calls_without_a_key_are_suppressed_only_while_one_is_in_flight(
    app_client, intake, tmp_path,
) -> None:
    """Without a key the caller gave no submission identity, so only a run still in
    flight is reused; submitting the same source again later is a deliberate new job."""
    path = _upload(app_client, tmp=tmp_path)
    intake.gate.clear()

    first, second = _process(app_client, path), _process(app_client, path)
    assert first.json()["job_id"] == second.json()["job_id"]
    assert (first.json()["reused"], second.json()["reused"]) == (False, True)
    _started(intake, 1)
    assert len(intake.runs) == 1

    intake.gate.set()
    _finished(app_client, first.json()["job_id"])
    later = _process(app_client, path)

    assert later.json()["job_id"] != first.json()["job_id"]
    assert later.json()["reused"] is False
    _finished(app_client, later.json()["job_id"])
    assert len(intake.runs) == 2


def test_a_named_submission_is_reused_even_after_it_finished(
    app_client, intake, tmp_path,
) -> None:
    """A retry of one named submission returns its job; it never re-runs the pipeline."""
    path = _upload(app_client, tmp=tmp_path)
    first = _process(app_client, path, "submission-1")
    _finished(app_client, first.json()["job_id"])

    retry = _process(app_client, path, "submission-1")

    assert retry.json() == {"job_id": first.json()["job_id"], "reused": True}
    assert len(intake.runs) == 1


def test_a_deliberate_new_submission_with_a_new_key_starts_a_new_job(
    app_client, intake, tmp_path,
) -> None:
    path = _upload(app_client, tmp=tmp_path)

    first = _process(app_client, path, "submission-1")
    second = _process(app_client, path, "submission-2")

    assert first.json()["job_id"] != second.json()["job_id"]
    _finished(app_client, first.json()["job_id"])
    _finished(app_client, second.json()["job_id"])
    assert len(intake.runs) == 2


def test_concurrent_duplicate_process_calls_start_a_single_job(intake, tmp_path) -> None:
    import httpx

    with TestClient(web_app.app) as sync_client:
        path = _upload(sync_client, tmp=tmp_path)

    async def race() -> list[dict]:
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
            calls = [client.post("/api/process",
                                 json={"path": path, "submission_key": "submission-1"})
                     for _ in range(8)]
            payloads = [r.json() for r in await asyncio.gather(*calls)]
            # Settle inside this loop: the pipeline task dies with it.
            for _ in range(500):
                status = await client.get(f"/api/process/status/{payloads[0]['job_id']}")
                if status.json()["status"] in ("completed", "error"):
                    break
                await asyncio.sleep(0.01)
            return payloads

    payloads = asyncio.run(race())

    assert len({p["job_id"] for p in payloads}) == 1
    assert sum(not p["reused"] for p in payloads) == 1
    assert len(intake.runs) == 1  # still one run after everything settled


def test_an_invalid_path_is_rejected_before_any_submission_is_recorded(
    app_client, intake,
) -> None:
    response = _process(app_client, "/etc/passwd", "submission-1")

    assert response.status_code == 400
    assert intake.runs == []
    assert web_app._submissions == {}


# ---------------------------------------------------------------------------
# Truthful status
# ---------------------------------------------------------------------------

def test_status_acknowledges_the_filename_and_reports_truthful_stages(
    app_client, intake, tmp_path,
) -> None:
    path = _upload(app_client, "monthly-mail.pdf", tmp=tmp_path)

    job_id = _process(app_client, path, "submission-1").json()["job_id"]
    state = _finished(app_client, job_id)

    assert state["filename"] == "monthly-mail.pdf"
    assert [s["id"] for s in state["stages"]] == [s[0] for s in PROCESS_STAGES] + ["done"]
    assert {s["status"] for s in state["stages"]} == {"done"}
    assert state["stage"] == "done"
    # Only stages the pipeline really runs are advertised.
    assert {s["id"] for s in state["stages"]} == {"checking", "ocr", "grouping",
                                                  "classifying", "filing", "done"}
    assert not any(s["id"] in {"summary", "archive"} for s in state["stages"])
    assert "ocr" in {s["id"] for s in state["stages"]}


def test_completion_reports_counts_and_a_job_scoped_review_url(app_client, intake,
                                                                tmp_path) -> None:
    path = _upload(app_client, tmp=tmp_path)

    job_id = _process(app_client, path, "submission-1").json()["job_id"]
    state = _finished(app_client, job_id)

    assert state["durable_job_id"] == "durable-1"
    assert state["review_url"] == "/review?job_id=durable-1"
    assert state["documents_total"] == 0
    assert state["auto_filed"] == 0 and state["review_queue"] == 0
    assert state["progress"] == 100


def test_status_states_local_only_capabilities_without_promising_ai_titles(
    app_client, intake, tmp_path,
) -> None:
    path = _upload(app_client, tmp=tmp_path)

    job_id = _process(app_client, path, "submission-1").json()["job_id"]
    capabilities = _finished(app_client, job_id)["capabilities"]

    assert capabilities["text_extraction"] is True
    assert capabilities["ai_classification"] is False
    assert capabilities["summary"] == ("OCR runs locally. "
                                       "AI classification is off.")
    assert "title" not in capabilities["summary"].lower()
    assert "name" not in capabilities["summary"].lower()


def test_cloud_mode_reports_classification_enabled(app_client, intake, tmp_path,
                                                    monkeypatch) -> None:
    monkeypatch.setitem(web_app._config, "privacy_mode", "cloud")
    path = _upload(app_client, tmp=tmp_path)

    job_id = _process(app_client, path, "submission-1").json()["job_id"]
    capabilities = _finished(app_client, job_id)["capabilities"]

    assert capabilities["ai_classification"] is True
    assert "title" not in capabilities["summary"].lower()
