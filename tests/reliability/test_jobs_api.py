"""``GET /api/v1/jobs/{job_id}`` and ``POST /api/v1/jobs/{job_id}/retry`` adapters."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docflow.filing.operations import DocumentAssignment as A
from docflow.web import app as web_app
from tests.reliability.harness import classified_job, make_env

ORIGINALS = "BeenOrganized033026"


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", env.filer())
    return TestClient(web_app.app)


def _interrupted(env) -> str:
    job_id = classified_job(env, [1, 2])

    def archive_down(src, dst):
        if Path(dst).parent.name == ORIGINALS:
            raise OSError(5, "Input/output error")
        return os.link(src, dst)

    env.filer(link=archive_down).file_job(env.scope_id, job_id,
                                          [A((1, 2), "filed", "Docs", "Letter.pdf")],
                                          original_relative_directory=ORIGINALS)
    return job_id


def test_get_job_returns_durable_page_accounting(env, client) -> None:
    job_id = _interrupted(env)
    response = client.get(f"/api/v1/jobs/{job_id}", params={"archive_scope_id": env.scope_id})
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["status"] == "filing"
    assert body["page_accounting"]["filed"] == 2
    assert body["recovery"] == {"state": "resumable", "retryable": True}


def test_get_job_errors(env, client, monkeypatch) -> None:
    job_id = _interrupted(env)
    missing_scope = client.get(f"/api/v1/jobs/{job_id}")
    unknown_scope = client.get(f"/api/v1/jobs/{job_id}", params={"archive_scope_id": "nope"})
    unknown_job = client.get("/api/v1/jobs/unknown", params={"archive_scope_id": env.scope_id})
    assert (missing_scope.status_code, missing_scope.json()["error"]["code"]) == (
        422, "invalid_request")
    assert (unknown_scope.status_code, unknown_scope.json()["error"]["code"]) == (
        404, "scope_not_found")
    assert (unknown_job.status_code, unknown_job.json()) == (
        404, {"error": {"code": "job_not_found", "message": "Job not found in this scope."}})
    monkeypatch.setattr(web_app, "_state_store", None)
    offline = client.get(f"/api/v1/jobs/{job_id}", params={"archive_scope_id": env.scope_id})
    assert (offline.status_code, offline.json()["error"]["code"]) == (503, "state_unavailable")


def test_retry_route_is_idempotent_and_reports_conflicts(env, client) -> None:
    job_id = _interrupted(env)
    body = {"archive_scope_id": env.scope_id, "idempotency_key": "ui-retry-1"}

    first = client.post(f"/api/v1/jobs/{job_id}/retry", json=body)
    replay = client.post(f"/api/v1/jobs/{job_id}/retry", json=body)
    again = client.post(f"/api/v1/jobs/{job_id}/retry",
                        json={**body, "idempotency_key": "ui-retry-2"})
    extra = client.post(f"/api/v1/jobs/{job_id}/retry", json={**body, "path": "/etc"})
    no_key = client.post(f"/api/v1/jobs/{job_id}/retry", json={"archive_scope_id": env.scope_id})

    assert first.status_code == 200
    assert (first.json()["action"], first.json()["job"]["status"]) == ("resumed", "completed")
    assert replay.json() == first.json()
    assert (again.status_code, again.json()["error"]["code"]) == (409, "job_not_retryable")
    assert (extra.status_code, extra.json()["error"]["code"]) == (422, "invalid_request")
    assert (no_key.status_code, no_key.json()["error"]["code"]) == (422, "invalid_request")
    assert (env.archive / "Docs/Letter.pdf").exists()
    assert not (env.archive / "Docs/Letter_2.pdf").exists()
