"""The shipped ``docflow ui`` bootstrap wires durable, archive-scoped state for the local UI.

``uvicorn.run`` is replaced by an in-process ``TestClient`` driver, so no server or socket
is started. HOME, the archive, inbox and application state are synthetic temp paths.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import uvicorn
from click.testing import CliRunner
from fastapi.testclient import TestClient

from docflow.cli import cli
from docflow.web import app as web_app


def _config(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("".join(f"{key}: {value}\n" for key, value in
                            {"privacy_mode": "local_only", **values}.items()))
    return path


def _serve(monkeypatch: pytest.MonkeyPatch, driver) -> list[dict]:
    """Replace the server with ``driver(client)`` run against the configured app."""
    calls: list[dict] = []

    def run(app, **kwargs):
        calls.append(kwargs)
        driver(TestClient(app))

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def test_ui_command_configures_durable_state_and_active_scope(
        tmp_path, isolated_home, monkeypatch) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    seen: dict = {}

    def driver(client: TestClient) -> None:
        seen["scope"] = client.get("/api/v1/archive-scopes/active")
        scope_id = (seen["scope"].json().get("archive_scope") or {}).get("id", "")
        seen["items"] = client.get("/api/v1/review-items",
                                   params={"archive_scope_id": scope_id, "status": "pending"})

    calls = _serve(monkeypatch, driver)
    result = CliRunner().invoke(cli, ["--config", str(_config(tmp_path, archive_root=archive)),
                                      "ui"])

    assert result.exit_code == 0, result.output
    assert [call["host"] for call in calls] == ["127.0.0.1"]
    assert seen["scope"].status_code == 200, seen["scope"].text
    assert seen["items"].status_code == 200, seen["items"].text
    assert seen["items"].json()["items"] == []
    assert str(archive) not in seen["scope"].text
    assert (isolated_home / ".local/share/docflow/state.sqlite3").is_file()
    assert list(archive.iterdir()) == []
    assert web_app._state_store is None  # released when the server returns


@pytest.mark.parametrize(("values", "message"), [
    ({}, "archive_root is not set"),
    ({"archive_root": "''"}, "archive_root is not set"),
    ({"archive_root": "missing-archive"}, "archive root does not exist"),
    ({"archive_root": "HOME"}, "outside every archive root"),
])
def test_ui_command_fails_closed_without_a_usable_archive_root(
        tmp_path, isolated_home, monkeypatch, values, message) -> None:
    resolved = {key: (str(isolated_home) if value == "HOME" else
                      str(tmp_path / value) if value == "missing-archive" else value)
                for key, value in values.items()}
    calls = _serve(monkeypatch, lambda client: None)

    result = CliRunner().invoke(cli, ["--config", str(_config(tmp_path, **resolved)), "ui"])

    assert result.exit_code != 0
    assert message in result.output
    assert calls == []
    assert web_app._state_store is None
    assert not (isolated_home / "DocFlowExample").exists()
    assert not (tmp_path / "missing-archive").exists()
    assert not (isolated_home / ".local/share/docflow").exists()


def _seed_review_item(scan: str) -> str:
    """Admit and file a synthetic scan through the bootstrap-configured durable filer."""
    from docflow.filing.operations import DocumentAssignment
    from tests.reliability.harness import quick_observe

    store, filer = web_app._state_store, web_app._filer
    scope_id = web_app._active_scope_id
    filer.observe = quick_observe
    job_id = filer.admit(scope_id, f"watch:{scan}").job_id
    assert store.jobs.transition(scope_id, job_id, expected="ready", new="ocr")
    assert store.jobs.transition(scope_id, job_id, expected="ocr", new="classified")
    result = filer.file_job(scope_id, job_id, [
        DocumentAssignment((1,), "filed", "Docs", "Filed.pdf"),
        DocumentAssignment((2,), "review", "Review", "Suggested.pdf", reason="low_confidence",
                           confidence=0.4)], original_relative_directory="BeenOrganized091326")
    assert result.job_status == "review"
    return job_id


def test_localhost_review_workflow_approve_then_undo_after_restart(
        tmp_path, isolated_home, monkeypatch) -> None:
    from tests.reliability.synthetic import write_image_pdf

    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    archive.mkdir()
    write_image_pdf(inbox / "scan.pdf", [1, 2])
    config = _config(tmp_path, archive_root=archive, scan_watch_folder=inbox)
    first: dict = {}
    second: dict = {}

    def before_restart(client: TestClient) -> None:
        first["job_id"] = _seed_review_item("scan.pdf")
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        listed = client.get("/api/v1/review-items",
                            params={"archive_scope_id": scope_id, "status": "pending"}).json()
        (item,) = listed["items"]
        approved = client.post(f"/api/v1/review-items/{item['id']}/approve", json={
            "archive_scope_id": scope_id, "idempotency_key": "ui-approve-1"})
        first.update(scope_id=scope_id, item=item, approved=approved)

    _serve(monkeypatch, before_restart)
    assert CliRunner().invoke(cli, ["--config", str(config), "ui"]).exit_code == 0
    assert (first["item"]["page_numbers"], first["item"]["actions"]) == (
        [2], ["approve", "correct", "skip"])
    assert first["approved"].status_code == 200, first["approved"].text
    operation_id = first["approved"].json()["operation"]["id"]
    assert (archive / "Review/Suggested.pdf").is_file()
    assert web_app._state_store is None

    def after_restart(client: TestClient) -> None:
        second["scope"] = client.get("/api/v1/archive-scopes/active").json()
        scope_id = second["scope"]["archive_scope"]["id"]
        second["pending_before"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id, "status": "pending"}).json()["items"]
        second["undo"] = client.post(f"/api/v1/operations/{operation_id}/undo", json={
            "archive_scope_id": scope_id, "idempotency_key": "ui-undo-1"})
        second["pending_after"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id, "status": "pending"}).json()["items"]
        second["job"] = client.get(f"/api/v1/jobs/{first['job_id']}",
                                   params={"archive_scope_id": scope_id}).json()

    _serve(monkeypatch, after_restart)
    assert CliRunner().invoke(cli, ["--config", str(config), "ui"]).exit_code == 0

    assert second["scope"] == {"archive_scope": {"id": first["scope_id"]}}
    assert second["pending_before"] == []
    assert second["undo"].status_code == 200, second["undo"].text
    assert (second["undo"].json()["outcome"], second["undo"].json()["operation"]["status"]) == (
        "undone", "undone")
    assert [i["id"] for i in second["pending_after"]] == [first["item"]["id"]]
    assert not (archive / "Review/Suggested.pdf").exists()
    assert (archive / "Docs/Filed.pdf").is_file()
    assert (archive / "BeenOrganized091326/scan.pdf").is_file()
    assert [p["status"] for p in second["job"]["page_accounting"]["pages"]] == ["filed", "review"]
    assert second["job"]["job"]["status"] == "review"
