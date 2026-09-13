"""Fixed error bodies, stored conflicts and strict request validation."""
from __future__ import annotations

from tests.review_actions.support import body, page_statuses, review_job


def test_conflict_is_stored_and_replayed_for_the_same_key(env, client) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    approved = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "k-1"))
    conflict = client.post(f"/api/v1/review-items/{item.id}/skip", json=body(env, "k-2"))
    client.post(f"/api/v1/operations/{approved.json()['operation']['id']}/undo",
                json=body(env, "k-3"))

    replay = client.post(f"/api/v1/review-items/{item.id}/skip", json=body(env, "k-2"))

    assert conflict.status_code == replay.status_code == 409
    assert conflict.json() == replay.json() == {"error": {
        "code": "review_item_not_pending", "message": "Review item is no longer pending."}}
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert page_statuses(env, job_id)[2] == "review"


def test_reusing_a_key_for_a_different_request_is_rejected_without_mutation(env, client) -> None:
    job_id, items = review_job(env)
    first = client.post(f"/api/v1/review-items/{items[(3, 4)].id}/correct", json=body(
        env, "k-1", relative_directory="Utilities", filename="Electric.pdf"))
    assert first.status_code == 200

    other_item = client.post(f"/api/v1/review-items/{items[(2,)].id}/skip", json=body(env, "k-1"))
    other_destination = client.post(f"/api/v1/review-items/{items[(3, 4)].id}/correct", json=body(
        env, "k-1", relative_directory="Utilities", filename="Water.pdf"))

    for response in (other_item, other_destination):
        assert (response.status_code, response.json()) == (409, {"error": {
            "code": "idempotency_key_reused",
            "message": "This idempotency key was used for a different request."}})
    assert env.store.reviews.get(env.scope_id, items[(2,)].id).status == "pending"
    assert page_statuses(env, job_id) == {1: "filed", 2: "review", 3: "filed", 4: "filed"}
    assert not (env.archive / "Utilities/Water.pdf").exists()


def test_request_validation_and_fixed_error_bodies(env, client, monkeypatch) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    file_job = env.store.operations.latest_for_job(env.scope_id, job_id, "file_job")
    approve = f"/api/v1/review-items/{item.id}/approve"
    invalid = {"error": {"code": "invalid_request",
                         "message": "Request body does not match the contract."}}

    cases = [
        (client.post(approve, json={**body(env, "k"), "path": "/etc"}), 422, invalid),
        (client.post(approve, json=body(env, "bad key!")), 422, invalid),
        (client.post(approve, json={"idempotency_key": "k"}), 422, invalid),
        (client.post(approve, json=body(env, 7)), 422, invalid),
        (client.post(f"/api/v1/review-items/{item.id}/correct",
                     json=body(env, "k", relative_directory="Docs")), 422, invalid),
        (client.post("/api/v1/review-items/batch", json=body(
            env, "k", action="delete", review_item_ids=[item.id])), 422, invalid),
        (client.post("/api/v1/review-items/batch", json=body(
            env, "k", action="skip", review_item_ids=[item.id, item.id])), 422, invalid),
        (client.post("/api/v1/review-items/batch", json=body(
            env, "k", action="skip", review_item_ids=[])), 422, invalid),
        (client.post(approve, json={**body(env, "k"), "archive_scope_id": "nope"}), 404,
         {"error": {"code": "scope_not_found", "message": "Archive scope is not registered."}}),
        (client.post("/api/v1/review-items/missing/approve", json=body(env, "k")), 404,
         {"error": {"code": "review_item_not_found",
                    "message": "Review item not found in this scope."}}),
        (client.post("/api/v1/operations/missing/undo", json=body(env, "k")), 404,
         {"error": {"code": "operation_not_found",
                    "message": "Operation not found in this scope."}}),
        (client.post(f"/api/v1/operations/{file_job.id}/undo", json=body(env, "k")), 409,
         {"error": {"code": "operation_not_undoable",
                    "message": "This operation cannot be undone."}}),
        (client.post(f"/api/v1/review-items/{items[(3, 4)].id}/approve",
                     json=body(env, "k-none")), 409,
         {"error": {"code": "destination_required",
                    "message": "The suggestion cannot be filed; send a correction."}}),
        (client.get("/api/v1/review-items", params={"archive_scope_id": env.scope_id,
                                                   "status": "done"}), 422, None),
        (client.get("/api/v1/review-items"), 422, None),
    ]
    for response, status, expected in cases:
        assert response.status_code == status, response.text
        assert set(response.json()) == {"error"}
        if expected is not None:
            assert response.json() == expected
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert env.rows("SELECT COUNT(*) FROM operations WHERE kind LIKE 'review_%' "
                    "AND status <> 'failed'") == [(0,)]

    monkeypatch.setattr(__import__("docflow.web.app", fromlist=["app"]), "_state_store", None)
    for response in (client.post(approve, json=body(env, "k")),
                     client.get("/api/v1/review-items", params={"archive_scope_id": "x"}),
                     client.post("/api/v1/operations/x/undo", json=body(env, "k"))):
        assert (response.status_code, response.json()["error"]["code"]) == (
            503, "state_unavailable")
