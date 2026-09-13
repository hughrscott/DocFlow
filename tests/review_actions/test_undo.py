"""``POST /api/v1/operations/{id}/undo`` is a journaled, hash-verified compensating operation."""
from __future__ import annotations

import pytest

from docflow.web import app as web_app
from tests.reliability.synthetic import sha256_file
from tests.review_actions.support import ORIGINALS, body, page_statuses, review_job


def _restart(env, monkeypatch) -> None:
    """Process restart plus a browser reload: only IDs kept by the client survive."""
    env.reopen()
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", env.filer())


def _hidden(env) -> list[str]:
    return sorted(p.name for p in env.archive.rglob(".*"))


def test_undo_approve_after_reload_removes_only_its_file_and_restores_state(
        env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    approved = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a"))
    operation_id = approved.json()["operation"]["id"]
    original = env.archive / ORIGINALS / "scan.pdf"
    original_hash = sha256_file(original)

    _restart(env, monkeypatch)
    response = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "undone"
    assert payload["operation"]["id"] == operation_id
    assert payload["operation"]["status"] == "undone"
    assert payload["operation"]["undone_at"]
    assert payload["undo_operation"]["kind"] == "operation_undo"
    assert payload["undo_operation"]["status"] == "completed"
    assert payload["steps"] == [{
        "ordinal": 0, "review_item_id": item.id, "job_id": job_id, "action": "approve",
        "kind": "remove_filed", "status": "compensated", "error_code": None,
        "relative_path": "Review/Suggested.pdf"}]
    assert not (env.archive / "Review/Suggested.pdf").exists()
    assert sha256_file(original) == original_hash
    assert (env.archive / "Docs/Filed.pdf").exists()
    assert _hidden(env) == []

    _restart(env, monkeypatch)
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert page_statuses(env, job_id) == {1: "filed", 2: "review", 3: "review", 4: "review"}
    assert env.store.jobs.get(env.scope_id, job_id).status == "review"
    assert env.store.files.get_by_path(env.scope_id, "Review/Suggested.pdf") is None
    assert env.store.operations.get(env.scope_id, operation_id).status == "undone"
    listed = client.get("/api/v1/review-items", params={"archive_scope_id": env.scope_id})
    assert item.id in {i["id"] for i in listed.json()["items"]}


def test_correction_then_undo_restores_queue_job_pages_and_references(
        env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    corrected_item = items[(3, 4)]
    client.post(f"/api/v1/review-items/{items[(2,)].id}/approve", json=body(env, "ui-a"))
    before_items = client.get("/api/v1/review-items",
                              params={"archive_scope_id": env.scope_id}).json()["items"]
    corrected = client.post(f"/api/v1/review-items/{corrected_item.id}/correct", json=body(
        env, "ui-c", relative_directory="Household/Utilities", filename="Electric.pdf"))
    operation_id = corrected.json()["operation"]["id"]
    assert corrected.json()["jobs"][0]["status"] == "completed"
    assert env.store.jobs.get(env.scope_id, job_id).status == "completed"

    _restart(env, monkeypatch)
    response = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u"))

    assert (response.status_code, response.json()["outcome"]) == (200, "undone")
    assert response.json()["jobs"] == [{"id": job_id, "status": "review", "page_accounting": {
        "page_count": 4, "pending": 0, "filed": 2, "review": 2, "skipped": 0, "blocked": 0,
        "complete": True}}]
    assert not (env.archive / "Household/Utilities/Electric.pdf").exists()
    _restart(env, monkeypatch)
    item = env.store.reviews.get(env.scope_id, corrected_item.id)
    assert (item.status, item.candidate, item.suggested_filename,
            item.suggested_relative_directory) == (
        "pending", corrected_item.candidate, None, None)
    assert env.store.jobs.get(env.scope_id, job_id).status == "review"
    assert page_statuses(env, job_id) == {1: "filed", 2: "filed", 3: "review", 4: "review"}
    assert env.rows("SELECT COUNT(*) FROM corrections") == [(0,)]
    assert [(r.relative_path, r.role) for r in env.store.files.list_for_job(env.scope_id, job_id)
            ] == [("Docs/Filed.pdf", "filed"), ("Review/Suggested.pdf", "filed"),
                  (f"{ORIGINALS}/scan.pdf", "original")]
    after_items = client.get("/api/v1/review-items",
                             params={"archive_scope_id": env.scope_id}).json()["items"]
    restored = next(i for i in after_items if i["id"] == corrected_item.id)
    expected = next(i for i in before_items if i["id"] == corrected_item.id)
    assert {k: v for k, v in restored.items() if k != "updated_at"} == {
        k: v for k, v in expected.items() if k != "updated_at"}

    again = client.post(f"/api/v1/review-items/{corrected_item.id}/correct", json=body(
        env, "ui-c-2", relative_directory="Household/Utilities", filename="Electric.pdf"))
    assert again.json()["items"][0]["relative_path"] == "Household/Utilities/Electric.pdf"
    assert env.store.jobs.get(env.scope_id, job_id).status == "completed"


def test_repeated_undo_returns_durable_result_and_never_touches_newer_files(
        env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    approved = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a"))
    operation_id = approved.json()["operation"]["id"]
    url = f"/api/v1/operations/{operation_id}/undo"
    first = client.post(url, json=body(env, "ui-u-1"))
    assert first.json()["outcome"] == "undone"

    refiled = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a-2"))
    assert refiled.json()["items"][0]["relative_path"] == "Review/Suggested.pdf"
    refiled_bytes = (env.archive / "Review/Suggested.pdf").read_bytes()
    counts = env.rows("SELECT COUNT(*) FROM operations")

    _restart(env, monkeypatch)
    same_key = client.post(url, json=body(env, "ui-u-1"))
    new_key = client.post(url, json=body(env, "ui-u-2"))

    assert same_key.status_code == new_key.status_code == 200
    assert same_key.json() == first.json()
    assert new_key.json() == first.json()
    assert env.rows("SELECT COUNT(*) FROM operations") == counts
    assert (env.archive / "Review/Suggested.pdf").read_bytes() == refiled_bytes
    assert env.store.reviews.get(env.scope_id, item.id).status == "approved"
    assert page_statuses(env, job_id)[2] == "filed"


def test_undo_with_missing_destination_reports_partial_failure_and_deletes_nothing(
        env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    approved = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a"))
    operation_id = approved.json()["operation"]["id"]
    (env.archive / "Review/Suggested.pdf").unlink()
    (env.archive / "Review/Unrelated.pdf").write_bytes(b"user file")
    digest = {p.relative_to(env.archive).as_posix(): sha256_file(p)
              for p in env.archive.rglob("*") if p.is_file()}

    _restart(env, monkeypatch)
    response = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "partial_failure"
    assert payload["undo_operation"]["status"] == "failed"
    assert (payload["operation"]["status"], payload["operation"]["undone_at"]) == (
        "completed", None)
    assert [(s["status"], s["error_code"], s["relative_path"]) for s in payload["steps"]] == [
        ("failed", "target_missing", "Review/Suggested.pdf")]
    assert {p.relative_to(env.archive).as_posix(): sha256_file(p)
            for p in env.archive.rglob("*") if p.is_file()} == digest
    assert env.store.reviews.get(env.scope_id, item.id).status == "approved"
    assert page_statuses(env, job_id)[2] == "filed"
    assert env.store.files.get_by_path(env.scope_id, "Review/Suggested.pdf") is not None
    assert client.post(f"/api/v1/operations/{operation_id}/undo",
                       json=body(env, "ui-u")).json() == payload


def _modify(env, how: str) -> None:
    target = env.archive / "Review/Suggested.pdf"
    outside = env.tmp / "outside"
    outside.mkdir()
    if how == "bytes":
        target.write_bytes(target.read_bytes() + b"\n% user annotation\n")
    elif how == "symlink":
        target.rename(outside / "Suggested.pdf")
        target.symlink_to(outside / "Suggested.pdf")
    elif how == "parent_symlink":
        (env.archive / "Review").rename(outside / "Review")
        (env.archive / "Review").symlink_to(outside / "Review", target_is_directory=True)
    elif how == "directory":
        target.unlink()
        target.mkdir()
        (target / "keep.txt").write_bytes(b"keep")


@pytest.mark.parametrize(("how", "code"), [
    ("bytes", "target_modified"),
    ("symlink", "target_unsafe"),
    ("parent_symlink", "target_unsafe"),
    ("directory", "target_unsafe"),
])
def test_undo_with_modified_or_unsafe_destination_reports_partial_failure_and_deletes_nothing(
        env, client, monkeypatch, how, code) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    approved = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a"))
    operation_id = approved.json()["operation"]["id"]
    _modify(env, how)
    digest = {p.relative_to(env.tmp).as_posix(): sha256_file(p)
              for p in env.tmp.rglob("*")
              if p.is_file() and not p.is_symlink() and "app-state" not in p.parts}

    _restart(env, monkeypatch)
    response = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u"))

    payload = response.json()
    assert (response.status_code, payload["outcome"]) == (200, "partial_failure")
    assert payload["operation"]["status"] == "completed"
    assert [(s["status"], s["error_code"]) for s in payload["steps"]] == [("failed", code)]
    assert {p.relative_to(env.tmp).as_posix(): sha256_file(p)
            for p in env.tmp.rglob("*")
            if p.is_file() and not p.is_symlink() and "app-state" not in p.parts} == digest
    assert _hidden(env) == []
    assert env.store.reviews.get(env.scope_id, item.id).status == "approved"
    assert page_statuses(env, job_id)[2] == "filed"


def test_undo_can_be_retried_with_a_new_key_after_the_file_is_restored(env, client) -> None:
    _, items = review_job(env)
    approved = client.post(f"/api/v1/review-items/{items[(2,)].id}/approve",
                           json=body(env, "ui-a"))
    operation_id = approved.json()["operation"]["id"]
    target = env.archive / "Review/Suggested.pdf"
    created = target.read_bytes()
    target.write_bytes(created + b"%")
    failed = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u-1"))
    assert failed.json()["steps"][0]["error_code"] == "target_modified"

    target.write_bytes(created)
    retried = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u-2"))

    assert (retried.json()["outcome"], retried.json()["operation"]["status"]) == (
        "undone", "undone")
    assert not target.exists()
    assert client.post(f"/api/v1/operations/{operation_id}/undo",
                       json=body(env, "ui-u-1")).json() == failed.json()
