"""``POST /api/v1/review-items/batch``: one durable operation for many review items."""
from __future__ import annotations

from docflow.web import app as web_app
from tests.review_actions.support import body, page_statuses, review_job


def _restart(env, monkeypatch) -> None:
    env.reopen()
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", env.filer())


def _two_jobs(env):
    first_job, first = review_job(env)
    second_job, second = review_job(env, name="scan2.pdf", marks=(5, 6, 7, 8))
    return first_job, first, second_job, second


def test_batch_approve_is_one_durable_operation_and_replays(env, client, monkeypatch) -> None:
    first_job, first, second_job, second = _two_jobs(env)
    ids = [first[(2,)].id, second[(2,)].id]

    response = client.post("/api/v1/review-items/batch",
                           json=body(env, "ui-b-1", action="approve", review_item_ids=ids))

    assert response.status_code == 200
    payload = response.json()
    assert (payload["operation"]["kind"], payload["operation"]["status"],
            payload["outcome"]) == ("review_batch", "completed", "completed")
    assert [(i["review_item_id"], i["result"], i["relative_path"]) for i in payload["items"]] == [
        (ids[0], "filed", "Review/Suggested.pdf"), (ids[1], "filed", "Review/Suggested_2.pdf")]
    assert [j["id"] for j in payload["jobs"]] == [first_job, second_job]

    _restart(env, monkeypatch)
    replay = client.post("/api/v1/review-items/batch",
                         json=body(env, "ui-b-1", action="approve", review_item_ids=ids))
    assert replay.json() == payload
    assert sorted(p.name for p in (env.archive / "Review").iterdir()) == [
        "Suggested.pdf", "Suggested_2.pdf"]
    assert [env.store.reviews.get(env.scope_id, i).status for i in ids] == ["approved"] * 2
    assert page_statuses(env, first_job)[2] == page_statuses(env, second_job)[2] == "filed"


def test_batch_partial_failure_reports_each_item_and_files_only_valid_ones(env, client) -> None:
    first_job, first, _, second = _two_jobs(env)
    client.post(f"/api/v1/review-items/{second[(2,)].id}/skip", json=body(env, "ui-s"))
    ids = [second[(2,)].id, first[(3, 4)].id, "unknown-item", first[(2,)].id]

    response = client.post("/api/v1/review-items/batch",
                           json=body(env, "ui-b-2", action="approve", review_item_ids=ids))

    assert response.status_code == 200
    payload = response.json()
    assert (payload["operation"]["status"], payload["outcome"]) == ("completed", "partial_failure")
    assert [(i["review_item_id"], i["result"], i["error_code"]) for i in payload["items"]] == [
        (ids[0], "rejected", "review_item_not_pending"),
        (ids[1], "rejected", "destination_required"),
        (ids[2], "rejected", "review_item_not_found"),
        (ids[3], "filed", None),
    ]
    assert [j["id"] for j in payload["jobs"]] == [first_job]
    assert page_statuses(env, first_job) == {1: "filed", 2: "filed", 3: "review", 4: "review"}
    assert env.store.reviews.get(env.scope_id, first[(3, 4)].id).status == "pending"
    assert client.post("/api/v1/review-items/batch", json=body(
        env, "ui-b-2", action="approve", review_item_ids=ids)).json() == payload


def test_batch_with_no_valid_item_is_a_failed_operation(env, client) -> None:
    _, first, _, _ = _two_jobs(env)

    response = client.post("/api/v1/review-items/batch", json=body(
        env, "ui-b-3", action="approve", review_item_ids=[first[(3, 4)].id]))

    assert response.status_code == 200
    assert (response.json()["operation"]["status"], response.json()["outcome"]) == (
        "failed", "failed")
    undo = client.post(f"/api/v1/operations/{response.json()['operation']['id']}/undo",
                       json=body(env, "ui-u"))
    assert (undo.status_code, undo.json()["error"]["code"]) == (409, "operation_not_undoable")


def test_batch_undo_partial_failure_then_retry_after_restore(env, client, monkeypatch) -> None:
    first_job, first, second_job, second = _two_jobs(env)
    ids = [first[(2,)].id, second[(2,)].id]
    batch = client.post("/api/v1/review-items/batch",
                        json=body(env, "ui-b", action="approve", review_item_ids=ids))
    operation_id = batch.json()["operation"]["id"]
    kept = env.archive / "Review/Suggested_2.pdf"
    created = kept.read_bytes()
    kept.write_bytes(created + b"% edited by user")

    _restart(env, monkeypatch)
    partial = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u-1"))

    payload = partial.json()
    assert (payload["outcome"], payload["operation"]["status"]) == (
        "partial_failure", "partially_undone")
    assert [(s["review_item_id"], s["status"], s["error_code"]) for s in payload["steps"]] == [
        (ids[0], "compensated", None), (ids[1], "failed", "target_modified")]
    assert not (env.archive / "Review/Suggested.pdf").exists()
    assert kept.read_bytes() == created + b"% edited by user"
    assert [env.store.reviews.get(env.scope_id, i).status for i in ids] == [
        "pending", "approved"]
    assert (page_statuses(env, first_job)[2], page_statuses(env, second_job)[2]) == (
        "review", "filed")

    kept.write_bytes(created)
    _restart(env, monkeypatch)
    retried = client.post(f"/api/v1/operations/{operation_id}/undo", json=body(env, "ui-u-2"))

    assert (retried.json()["outcome"], retried.json()["operation"]["status"]) == (
        "undone", "undone")
    assert [(s["status"], s["error_code"]) for s in retried.json()["steps"]] == [
        ("skipped", None), ("compensated", None)]
    assert not kept.exists()
    assert [env.store.reviews.get(env.scope_id, i).status for i in ids] == ["pending"] * 2
    assert page_statuses(env, second_job) == {1: "filed", 2: "review", 3: "review", 4: "review"}


def test_batch_skip_undo_restores_every_item(env, client) -> None:
    first_job, first, second_job, second = _two_jobs(env)
    ids = [first[(3, 4)].id, second[(2,)].id]
    batch = client.post("/api/v1/review-items/batch",
                        json=body(env, "ui-b", action="skip", review_item_ids=ids))
    assert [i["result"] for i in batch.json()["items"]] == ["skipped", "skipped"]

    undo = client.post(f"/api/v1/operations/{batch.json()['operation']['id']}/undo",
                       json=body(env, "ui-u"))

    assert undo.json()["outcome"] == "undone"
    assert [s["kind"] for s in undo.json()["steps"]] == ["reopen_skipped", "reopen_skipped"]
    assert [env.store.reviews.get(env.scope_id, i).status for i in ids] == ["pending"] * 2
    assert page_statuses(env, first_job) == page_statuses(env, second_job) == {
        1: "filed", 2: "review", 3: "review", 4: "review"}
