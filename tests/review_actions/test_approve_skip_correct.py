"""Approve, correct and skip are durable operations that survive a restart."""
from __future__ import annotations

from docflow.ingestion.loader import fingerprint_pdf
from docflow.web import app as web_app
from tests.review_actions.support import body, page_statuses, review_job


def _restart(env, monkeypatch) -> None:
    env.reopen()
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", env.filer())


def test_approve_files_pages_and_persists_through_restart(env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]

    response = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a-1"))

    assert response.status_code == 200
    payload = response.json()
    operation_id = payload["operation"]["id"]
    assert payload["operation"]["kind"] == "review_approve"
    assert payload["operation"]["status"] == "completed"
    assert payload["outcome"] == "completed"
    assert payload["items"] == [{
        "review_item_id": item.id, "job_id": job_id, "action": "approve", "result": "filed",
        "error_code": None, "relative_path": "Review/Suggested.pdf", "page_numbers": [2]}]
    written = env.archive / "Review/Suggested.pdf"
    assert fingerprint_pdf(written).page_count == 1

    _restart(env, monkeypatch)
    operation = env.store.operations.get(env.scope_id, operation_id)
    assert (operation.kind, operation.status) == ("review_approve", "completed")
    assert env.store.reviews.get(env.scope_id, item.id).status == "approved"
    assert page_statuses(env, job_id) == {1: "filed", 2: "filed", 3: "review", 4: "review"}
    assert env.store.jobs.get(env.scope_id, job_id).status == "review"
    record = env.store.files.get_by_path(env.scope_id, "Review/Suggested.pdf")
    assert (record.job_id, record.role, record.page_numbers) == (job_id, "filed", [2])


def test_replaying_an_approve_key_returns_stored_result_without_mutating(
        env, client, monkeypatch) -> None:
    _, items = review_job(env)
    url = f"/api/v1/review-items/{items[(2,)].id}/approve"
    first = client.post(url, json=body(env, "ui-a-2"))
    before = sorted(p.relative_to(env.archive).as_posix() for p in env.archive.rglob("*.pdf"))
    operations = env.rows("SELECT COUNT(*) FROM operations")

    _restart(env, monkeypatch)
    replay = client.post(url, json=body(env, "ui-a-2"))

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert sorted(p.relative_to(env.archive).as_posix()
                  for p in env.archive.rglob("*.pdf")) == before
    assert env.rows("SELECT COUNT(*) FROM operations") == operations


def test_skip_marks_pages_skipped_without_files_and_persists(env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(3, 4)]
    before = sorted(p.relative_to(env.archive).as_posix() for p in env.archive.rglob("*"))

    response = client.post(f"/api/v1/review-items/{item.id}/skip", json=body(env, "ui-s-1"))

    assert response.status_code == 200
    payload = response.json()
    assert (payload["operation"]["kind"], payload["outcome"]) == ("review_skip", "completed")
    assert payload["items"][0]["result"] == "skipped"
    assert payload["items"][0]["relative_path"] is None
    assert sorted(p.relative_to(env.archive).as_posix() for p in env.archive.rglob("*")) == before

    _restart(env, monkeypatch)
    assert env.store.operations.get(env.scope_id, payload["operation"]["id"]).status == "completed"
    assert env.store.reviews.get(env.scope_id, item.id).status == "skipped"
    assert page_statuses(env, job_id) == {1: "filed", 2: "review", 3: "skipped", 4: "skipped"}
    replay = client.post(f"/api/v1/review-items/{item.id}/skip", json=body(env, "ui-s-1"))
    assert replay.json() == payload


def test_correct_files_at_corrected_destination_and_records_correction(
        env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(3, 4)]

    response = client.post(f"/api/v1/review-items/{item.id}/correct", json=body(
        env, "ui-c-1", relative_directory="Household/Utilities", filename="Electric.pdf"))

    assert response.status_code == 200
    payload = response.json()
    assert (payload["operation"]["kind"], payload["outcome"]) == ("review_correct", "completed")
    assert payload["items"][0]["relative_path"] == "Household/Utilities/Electric.pdf"
    assert fingerprint_pdf(env.archive / "Household/Utilities/Electric.pdf").page_count == 2

    _restart(env, monkeypatch)
    assert env.store.reviews.get(env.scope_id, item.id).status == "corrected"
    assert page_statuses(env, job_id) == {1: "filed", 2: "review", 3: "filed", 4: "filed"}
    assert env.rows("SELECT review_item_id, chosen_relative_directory FROM corrections") == [
        (item.id, "Household/Utilities")]
    replay = client.post(f"/api/v1/review-items/{item.id}/correct", json=body(
        env, "ui-c-1", relative_directory="Household/Utilities", filename="Electric.pdf"))
    assert replay.json() == payload
    assert env.rows("SELECT COUNT(*) FROM corrections") == [(1,)]
