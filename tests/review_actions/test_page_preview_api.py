"""Scoped page previews rendered from the retained original of a durable job.

The route resolves the original only through scope/job/file records, proves the
page still matches its admitted fingerprint, caches in application state, and
publishes atomically. The archive is never written to.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from tests.reliability.synthetic import tree_digest
from tests.review_actions.support import ORIGINALS, review_job

PREVIEW_URL = "/api/v1/review-items/{item}/pages/{page}/preview"


def _preview(client, env, item_id: str, page: int | str, scope: str | None = None):
    return client.get(PREVIEW_URL.format(item=item_id, page=page),
                      params={"archive_scope_id": scope or env.scope_id})


def _original(env, job_id: str) -> Path:
    (record,) = [r for r in env.store.files.list_for_job(env.scope_id, job_id)
                 if r.role == "original"]
    return env.archive / record.relative_path


def _cache_files(env) -> list[Path]:
    """Everything the preview cache holds; it lives in state, never in the archive."""
    cache = env.state.cache / "review-previews"
    return sorted(p for p in cache.rglob("*") if p.is_file()) if cache.is_dir() else []


def test_preview_renders_a_real_image_only_pdf_page_and_caches_it_in_state(env, client) -> None:
    job_id, items = review_job(env)
    item = items[(3, 4)]
    archive_before = tree_digest(env.archive)

    first = _preview(client, env, item.id, 3)
    second = _preview(client, env, item.id, 3)

    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(first.content))
    image.load()
    assert image.format == "PNG" and image.size[0] > 0
    assert second.status_code == 200 and second.content == first.content  # served from cache
    # Cached under application state, never in the archive, and keyed per page.
    cached = _cache_files(env)
    assert cached and all(env.archive not in p.parents for p in cached)
    assert any(p.suffix == ".png" for p in cached)
    assert tree_digest(env.archive) == archive_before


def test_each_physical_page_renders_its_own_distinct_image(env, client) -> None:
    _, items = review_job(env)
    item = items[(3, 4)]

    third = _preview(client, env, item.id, 3)
    fourth = _preview(client, env, item.id, 4)

    assert third.status_code == fourth.status_code == 200
    assert third.content != fourth.content  # page 3 is not page 4


@pytest.mark.parametrize(("page", "code"), [
    (1, "page_not_found"),      # a page of the job, but not of this item
    (2, "page_not_found"),
    (0, "page_not_found"),
    (99, "page_not_found"),
    ("two", "invalid_request"),
])
def test_preview_validates_page_membership_and_bounds(env, client, page, code) -> None:
    _, items = review_job(env)

    response = _preview(client, env, items[(3, 4)].id, page)

    assert response.status_code in (404, 422)
    assert response.json()["error"]["code"] == code


def test_preview_refuses_unknown_items_and_scopes_without_disclosing_them(
    env, client, tmp_path, monkeypatch,
) -> None:
    _, items = review_job(env)
    other_root = tmp_path / "other-archive"
    other_root.mkdir()
    other_scope = env.store.scopes.register(other_root, home=env.home).id

    unknown = _preview(client, env, "no-such-item", 3)
    cross = _preview(client, env, items[(3, 4)].id, 3, scope=other_scope)
    unregistered = _preview(client, env, items[(3, 4)].id, 3, scope="not-a-scope")

    assert unknown.status_code == cross.status_code == unregistered.status_code == 404
    assert unknown.json()["error"]["code"] == "review_item_not_found"
    # A scope other than the active one is refused before the item is looked up, so a
    # registered-but-inactive scope and an unregistered one are indistinguishable and
    # neither reveals whether the item exists.
    assert cross.json() == unregistered.json()
    assert cross.json()["error"]["code"] == "scope_not_found"
    assert items[(3, 4)].id not in cross.text


def test_preview_authorizes_the_active_scope_before_looking_the_item_up(
    env, client, tmp_path, monkeypatch,
) -> None:
    from docflow.web import app as web_app

    _, items = review_job(env)
    other_root = tmp_path / "other-archive"
    other_root.mkdir()
    other_scope = env.store.scopes.register(other_root, home=env.home).id
    monkeypatch.setattr(web_app, "_active_scope_id", other_scope)

    response = _preview(client, env, items[(3, 4)].id, 3)

    assert (response.status_code, response.json()["error"]["code"]) == (404, "scope_not_found")


@pytest.mark.parametrize("damage", ["missing", "modified", "symlink", "directory"])
def test_preview_refuses_an_original_that_no_longer_matches_its_record(
    env, client, tmp_path, damage: str,
) -> None:
    job_id, items = review_job(env)
    item = items[(3, 4)]
    assert _preview(client, env, item.id, 3).status_code == 200
    for stale in _cache_files(env):
        stale.unlink()

    original = _original(env, job_id)
    original.unlink()
    if damage == "modified":
        original.write_bytes(b"%PDF-1.4 not the admitted scan\n")
    elif damage == "symlink":
        outside = tmp_path / "elsewhere.pdf"
        outside.write_bytes(b"%PDF-1.4 outside the archive\n")
        original.symlink_to(outside)
    elif damage == "directory":
        original.mkdir()

    response = _preview(client, env, item.id, 3)

    assert (response.status_code, response.json()["error"]["code"]) == (409, "source_unavailable")
    assert response.json()["error"]["message"] == "The retained original for these pages is " \
                                                  "unavailable."
    assert _cache_files(env) == []  # nothing unverified was ever published


def test_an_original_under_a_symlinked_folder_is_never_followed(env, client, tmp_path) -> None:
    """The recorded path stays the same, but its folder becomes a link out of the archive."""
    job_id, items = review_job(env)
    assert _preview(client, env, items[(3, 4)].id, 3).status_code == 200
    for stale in _cache_files(env):
        stale.unlink()

    original = _original(env, job_id)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    original.replace(elsewhere / original.name)
    original.parent.rmdir()
    (env.archive / ORIGINALS).symlink_to(elsewhere)
    assert (env.archive / ORIGINALS / original.name).is_file()  # readable if followed

    response = _preview(client, env, items[(3, 4)].id, 3)

    assert (response.status_code, response.json()["error"]["code"]) == (409, "source_unavailable")
    assert _cache_files(env) == []


def test_preview_publishes_atomically_and_leaves_no_partial_files(env, client,
                                                                   monkeypatch) -> None:
    from docflow.web import app as web_app

    _, items = review_job(env)
    replacements: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        replacements.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(web_app.os, "replace", spy)

    response = _preview(client, env, items[(3, 4)].id, 3)

    assert response.status_code == 200
    published = [p for p in _cache_files(env) if p.suffix == ".png"]
    assert len(published) == 1
    (src, dst) = replacements[-1]
    assert dst == str(published[0])
    assert src != dst and Path(src).parent == published[0].parent  # same-directory rename
    assert not [p for p in _cache_files(env) if p.suffix != ".png"]


def test_a_failed_render_publishes_nothing_and_does_not_touch_the_archive(
    env, client, monkeypatch,
) -> None:
    from docflow.web import app as web_app

    _, items = review_job(env)
    archive_before = tree_digest(env.archive)

    def explode(*args, **kwargs):
        raise RuntimeError("poppler is unavailable")

    monkeypatch.setattr(web_app, "_render_preview_page", explode)

    response = _preview(client, env, items[(3, 4)].id, 3)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "preview_unavailable"
    assert _cache_files(env) == []
    assert tree_digest(env.archive) == archive_before
