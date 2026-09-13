"""Corrected destinations are strict archive-relative paths, rejected before any mutation."""
from __future__ import annotations

import pytest

from tests.reliability.synthetic import tree_digest
from tests.review_actions.support import body, page_statuses, review_job

UNSAFE = [
    ("/tmp/outside", "Doc.pdf"),
    ("~/Documents", "Doc.pdf"),
    ("C:/Users", "Doc.pdf"),
    ("../outside", "Doc.pdf"),
    ("Docs/../../outside", "Doc.pdf"),
    ("Docs//Sub", "Doc.pdf"),
    ("Docs/", "Doc.pdf"),
    (".", "Doc.pdf"),
    ("Docs/.", "Doc.pdf"),
    (".hidden", "Doc.pdf"),
    ("Docs\\Sub", "Doc.pdf"),
    ("Docs\nSub", "Doc.pdf"),
    ("Docs", "../Doc.pdf"),
    ("Docs", "Sub/Doc.pdf"),
    ("Docs", ".pdf"),
    ("Docs", "Doc.txt"),
    ("Docs", ".."),
    ("Docs", "~draft.pdf"),
    ("Docs", "sk-" + "a" * 24 + ".pdf"),
    ("BeenOrganized091326/scan.pdf", "Doc.pdf"),
]


def _files(env) -> dict[str, str]:
    """Every file outside application state (archive, inbox, anything else under tmp)."""
    return {k: v for k, v in tree_digest(env.tmp).items() if not k.startswith("app-state/")}


def _assert_untouched(env, job_id, item, digest) -> None:
    assert env.rows("SELECT COUNT(*) FROM operations WHERE kind LIKE 'review_%'") == [(0,)]
    assert env.rows("SELECT COUNT(*) FROM corrections") == [(0,)]
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert page_statuses(env, job_id) == {1: "filed", 2: "review", 3: "review", 4: "review"}
    assert _files(env) == digest
    assert not any(p.is_dir() for p in env.archive.rglob("*") if p.name in {"Sub", "outside"})


@pytest.mark.parametrize(("directory", "filename"), UNSAFE)
def test_unsafe_corrected_destination_is_rejected_before_mutation(
        env, client, directory, filename) -> None:
    job_id, items = review_job(env)
    item = items[(3, 4)]
    digest = _files(env)

    response = client.post(f"/api/v1/review-items/{item.id}/correct", json=body(
        env, "ui-c-bad", relative_directory=directory, filename=filename))

    assert response.status_code == 400
    assert response.json() == {"error": {
        "code": "invalid_destination",
        "message": "Destination must be a relative archive folder and a visible .pdf name."}}
    _assert_untouched(env, job_id, item, digest)


@pytest.mark.parametrize(("directory", "filename"), [
    ("Linked", "Doc.pdf"),
    ("Linked/Sub", "Doc.pdf"),
    ("Docs", "Escape.pdf"),
])
def test_symlink_escape_in_corrected_destination_is_rejected_before_mutation(
        env, client, directory, filename) -> None:
    job_id, items = review_job(env)
    item = items[(3, 4)]
    outside = env.tmp / "outside"
    outside.mkdir()
    (outside / "target.pdf").write_bytes(b"not yours")
    (env.archive / "Linked").symlink_to(outside, target_is_directory=True)
    (env.archive / "Docs/Escape.pdf").symlink_to(outside / "target.pdf")
    digest = _files(env)

    response = client.post(f"/api/v1/review-items/{item.id}/correct", json=body(
        env, "ui-c-link", relative_directory=directory, filename=filename))

    assert (response.status_code, response.json()["error"]["code"]) == (
        400, "invalid_destination")
    _assert_untouched(env, job_id, item, digest)
    assert sorted(p.name for p in outside.iterdir()) == ["target.pdf"]


def test_symlinked_suggestion_cannot_be_approved(env, client) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    outside = env.tmp / "outside"
    outside.mkdir()
    (env.archive / "Review").symlink_to(outside, target_is_directory=True)
    digest = _files(env)

    response = client.post(f"/api/v1/review-items/{item.id}/approve", json=body(env, "ui-a-l"))

    assert (response.status_code, response.json()["error"]["code"]) == (
        409, "destination_required")
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert page_statuses(env, job_id) == {1: "filed", 2: "review", 3: "review", 4: "review"}
    assert _files(env) == digest
    assert list(outside.iterdir()) == []
