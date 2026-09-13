"""X03: job creation takes upload or configured watch-folder handles, never filesystem paths."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow.filing.operations import DurableFiler
from docflow.web import app as web_app
from tests.reliability.harness import quick_observe
from tests.reliability.synthetic import write_image_pdf
from tests.review_actions.support import body


@pytest.fixture()
def uploads(env, monkeypatch) -> Path:
    root = env.tmp / "uploads"
    root.mkdir()
    monkeypatch.setattr(web_app, "_filer", DurableFiler(
        env.store, source_roots={"watch": env.watch, "upload": root}, observe=quick_observe))
    return root


def _jobs(env) -> list[tuple]:
    return env.rows("SELECT source_locator, status FROM jobs ORDER BY created_at")


def test_arbitrary_filesystem_paths_are_rejected_without_creating_jobs(
        env, client, uploads) -> None:
    outside = write_image_pdf(env.tmp / "outside/private.pdf", [1])
    write_image_pdf(uploads / "scan.pdf", [1])
    (env.watch / "linked.pdf").symlink_to(outside)
    invalid = (422, "invalid_request")
    bad_source = (400, "invalid_source")
    cases = [
        ({"path": str(outside)}, invalid),
        ({"source": {"kind": "path", "path": str(outside)}}, invalid),
        ({"source": {"kind": "upload", "name": "scan.pdf", "path": str(outside)}}, invalid),
        ({"source": {"kind": "watch", "relative_path": str(outside)}}, bad_source),
        ({"source": {"kind": "watch", "relative_path": "../outside/private.pdf"}}, bad_source),
        ({"source": {"kind": "watch", "relative_path": "~/private.pdf"}}, bad_source),
        ({"source": {"kind": "watch", "relative_path": "linked.pdf"}}, bad_source),
        ({"source": {"kind": "upload", "name": "../outside/private.pdf"}}, bad_source),
        ({"source": {"kind": "upload", "name": str(outside)}}, bad_source),
        ({"source": {"kind": "upload", "name": "notes.txt"}}, bad_source),
    ]
    for extra, (status, code) in cases:
        response = client.post("/api/v1/jobs", json=body(env, "job-1", **extra))
        assert (response.status_code, response.json()["error"]["code"]) == (status, code), extra
    assert _jobs(env) == []


def test_upload_and_watch_handles_create_ready_jobs_idempotently(env, client, uploads) -> None:
    write_image_pdf(uploads / "scan.pdf", [1, 2])
    write_image_pdf(env.watch / "batch/scan.pdf", [3])

    uploaded = client.post("/api/v1/jobs", json=body(
        env, "job-u", source={"kind": "upload", "name": "scan.pdf"}))
    watched = client.post("/api/v1/jobs", json=body(
        env, "job-w", source={"kind": "watch", "relative_path": "batch/scan.pdf"}))
    replay = client.post("/api/v1/jobs", json=body(
        env, "job-u", source={"kind": "upload", "name": "scan.pdf"}))
    reused = client.post("/api/v1/jobs", json=body(
        env, "job-u", source={"kind": "watch", "relative_path": "batch/scan.pdf"}))
    missing = client.post("/api/v1/jobs", json=body(
        env, "job-m", source={"kind": "watch", "relative_path": "later.pdf"}))

    assert uploaded.status_code == watched.status_code == 200
    assert uploaded.json()["admission"] == {"state": "ready", "retryable": False}
    assert (uploaded.json()["job"]["status"], uploaded.json()["job"]["source_name"],
            uploaded.json()["page_accounting"]["page_count"]) == ("ready", "scan.pdf", 2)
    assert watched.json()["page_accounting"]["page_count"] == 1
    assert replay.json() == uploaded.json()
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "idempotency_key_reused")
    assert (missing.status_code, missing.json()["error"]["code"]) == (409, "source_unavailable")
    assert _jobs(env) == [("upload:scan.pdf", "ready"), ("watch:batch/scan.pdf", "ready")]
    assert str(env.tmp) not in uploaded.text


def test_legacy_process_accepts_only_upload_or_watch_folder_files(
        env, client, monkeypatch) -> None:
    started: list[Path] = []

    async def record(job_id, pdf_path):
        started.append(pdf_path)

    monkeypatch.setattr(web_app, "_run_pipeline_async", record)
    monkeypatch.setattr(web_app, "_processing_state", {})
    monkeypatch.setattr(web_app, "_config", {"archive_root": str(env.archive),
                                             "scan_watch_folder": str(env.watch)})
    outside = write_image_pdf(env.tmp / "outside/private.pdf", [1])
    payload = outside.read_bytes()
    uploaded = client.post("/api/upload", files={"file": ("scan.pdf", payload, "application/pdf")})
    upload_path = Path(uploaded.json()["path"])
    (upload_path.parent / "linked.pdf").symlink_to(outside)
    watch_file = write_image_pdf(env.watch / "inbox-scan.pdf", [2])

    rejected = [
        str(outside),
        str(upload_path.parent / ".." / ".." / "outside" / "private.pdf"),
        str(upload_path.parent / "linked.pdf"),
        str(env.archive / "Docs"),
        "relative/scan.pdf",
        "",
    ]
    for candidate in rejected:
        response = client.post("/api/process", json={"path": candidate})
        assert response.status_code == 400, candidate
    assert started == []

    for candidate in (upload_path, watch_file):
        assert client.post("/api/process", json={"path": str(candidate)}).status_code == 200
    assert started == [upload_path, watch_file]
