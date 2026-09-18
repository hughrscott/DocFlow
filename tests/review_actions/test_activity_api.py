"""Persisted activity: the dashboard reads durable jobs, never archive log files.

``GET /api/v1/activity`` is the only source for the dashboard's global metrics and
its recent-batch list. It must report batches that really exist in local state, so
the page can never claim "no documents processed yet" while durable jobs are stored.
"""
from __future__ import annotations

from tests.review_actions.support import review_job


def _activity(client, env, **params):
    response = client.get("/api/v1/activity",
                          params={"archive_scope_id": env.scope_id, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_fresh_scope_reports_no_batches_and_zero_totals(env, client) -> None:
    payload = _activity(client, env)

    assert payload["archive_scope_id"] == env.scope_id
    assert payload["batches"] == []
    assert payload["newest_batch"] is None
    assert payload["totals"] == {"pending_review": 0, "filed_documents": 0,
                                 "filed_today": 0, "batches": 0}


def test_a_stored_job_is_reported_as_a_batch_with_its_own_counts(env, client) -> None:
    job_id, _ = review_job(env, name="scan-a.pdf")

    payload = _activity(client, env)

    assert payload["totals"] == {"pending_review": 2, "filed_documents": 1,
                                 "filed_today": 1, "batches": 1}
    (batch,) = payload["batches"]
    assert batch["job_id"] == job_id
    assert batch["source_name"] == "scan-a.pdf"
    assert batch["status"] == "review"
    assert batch["page_count"] == 4
    assert batch["pending_review"] == 2
    assert batch["filed_documents"] == 1
    assert batch["pages"] == {"pending": 0, "filed": 1, "review": 3, "skipped": 0,
                              "blocked": 0}
    assert payload["newest_batch"] == batch


def test_batches_are_newest_first_and_the_newest_batch_is_the_latest_job(env, client) -> None:
    older, _ = review_job(env, name="older.pdf")
    newer, _ = review_job(env, name="newer.pdf", marks=(5, 6, 7, 8))

    payload = _activity(client, env)

    assert [b["job_id"] for b in payload["batches"]] == [newer, older]
    assert payload["newest_batch"]["job_id"] == newer
    assert payload["totals"]["batches"] == 2
    assert payload["totals"]["pending_review"] == 4


def test_the_batch_list_is_bounded_by_an_explicit_limit(env, client) -> None:
    review_job(env, name="one.pdf")
    newer, _ = review_job(env, name="two.pdf", marks=(5, 6, 7, 8))

    payload = _activity(client, env, limit=1)

    assert [b["job_id"] for b in payload["batches"]] == [newer]
    assert payload["totals"]["batches"] == 2  # totals always span every batch


def test_activity_never_leaks_absolute_paths_or_raw_text(env, client) -> None:
    review_job(env, name="scan-a.pdf")

    import json

    body = json.dumps(_activity(client, env))

    assert str(env.archive) not in body
    assert str(env.home) not in body
    assert "ocr_text" not in body


def test_activity_requires_a_registered_scope(env, client) -> None:
    assert client.get("/api/v1/activity").status_code == 422
    unknown = client.get("/api/v1/activity", params={"archive_scope_id": "nope"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "scope_not_found"
