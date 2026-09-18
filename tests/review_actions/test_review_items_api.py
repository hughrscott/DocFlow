"""``GET /api/v1/review-items`` returns durable, archive-relative review data."""
from __future__ import annotations

from tests.review_actions.support import review_job


def test_lists_pending_items_with_page_references_and_relative_paths(env, client) -> None:
    job_id, items = review_job(env)

    response = client.get("/api/v1/review-items",
                          params={"archive_scope_id": env.scope_id, "status": "pending"})

    assert response.status_code == 200
    listed = {tuple(i["page_numbers"]): i for i in response.json()["items"]}
    assert set(listed) == {(2,), (3, 4)}
    suggested = listed[(2,)]
    assert suggested["id"] == items[(2,)].id
    assert suggested["job_id"] == job_id
    assert suggested["status"] == "pending"
    assert suggested["source_name"] == "scan.pdf"
    assert suggested["reason"] == "low_confidence"
    assert suggested["suggested_relative_directory"] == "Review"
    assert suggested["suggested_filename"] == "Suggested.pdf"
    assert suggested["actions"] == ["approve", "correct", "skip"]
    assert listed[(3, 4)]["actions"] == ["correct", "skip"]
    assert str(env.tmp) not in response.text


def test_active_scope_is_discoverable_without_exposing_the_root_path(env, client,
                                                                     monkeypatch) -> None:
    from docflow.web import app as web_app

    monkeypatch.setattr(web_app, "_active_scope_id", None)
    missing = client.get("/api/v1/archive-scopes/active")
    web_app.configure_state(env.store, env.filer(), scope_id=env.scope_id)
    active = client.get("/api/v1/archive-scopes/active")
    monkeypatch.setattr(web_app, "_state_store", None)
    offline = client.get("/api/v1/archive-scopes/active")

    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "scope_not_found")
    assert (active.status_code, active.json()) == (200, {"archive_scope": {"id": env.scope_id}})
    assert str(env.archive) not in active.text
    assert (offline.status_code, offline.json()["error"]["code"]) == (503, "state_unavailable")
