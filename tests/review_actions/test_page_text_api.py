"""Review-item text extraction: list summaries, the job filter, and per-page text.

``{page}`` is always the 1-based physical page of the source PDF, never an offset
inside the review item.
"""
from __future__ import annotations

import pytest

from docflow.state.repositories import PageOcr
from tests.review_actions.support import review_job

TEXT_URL = "/api/v1/review-items/{item}/pages/{page}/text"


def _record(env, job_id: str, **by_page: PageOcr) -> None:
    outcomes = [by_page[f"p{n}"] for n in sorted(int(k[1:]) for k in by_page)]
    env.store.jobs.record_ocr(env.scope_id, job_id, outcomes)


def _extracted(page: int, text: str) -> PageOcr:
    return PageOcr(page, "extracted", text, None)


def _listing(client, env, **params) -> dict:
    response = client.get("/api/v1/review-items",
                          params={"archive_scope_id": env.scope_id, **params})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# GET /api/v1/review-items — compact page range and aggregated extraction state
# ---------------------------------------------------------------------------

def test_list_reports_a_compact_source_page_range_and_aggregated_extraction(env, client) -> None:
    job_id, items = review_job(env)
    _record(env, job_id, p1=_extracted(1, "FILED PAGE"), p2=_extracted(2, "SUGGESTED PAGE"),
            p3=_extracted(3, "THIRD PAGE"), p4=PageOcr(4, "no_text", None, None))

    listed = {tuple(i["page_numbers"]): i for i in _listing(client, env)["items"]}

    assert listed[(2,)]["source_page_range"] == "2"
    assert listed[(2,)]["text_extraction"] == "extracted"
    assert listed[(3, 4)]["source_page_range"] == "3-4"
    assert listed[(3, 4)]["text_extraction"] == "mixed"
    # Backward compatible: every Phase 4 field is still present and unchanged.
    assert listed[(2,)]["suggested_filename"] == "Suggested.pdf"
    assert listed[(2,)]["actions"] == ["approve", "correct", "skip"]
    assert "ocr_text" not in listed[(2,)] and "SUGGESTED PAGE" not in client.get(
        "/api/v1/review-items", params={"archive_scope_id": env.scope_id}).text


@pytest.mark.parametrize(("third", "fourth", "expected"), [
    (PageOcr(3, "missing", None, None), PageOcr(4, "extracted", "T", None), "missing"),
    (PageOcr(3, "extracted", "T", None), PageOcr(4, "missing", None, None), "missing"),
    (PageOcr(3, "failed", None, "ocr_engine_error"), PageOcr(4, "missing", None, None), "missing"),
    (PageOcr(3, "no_text", None, None), PageOcr(4, "no_text", None, None), "no_text"),
    (PageOcr(3, "failed", None, "ocr_engine_error"),
     PageOcr(4, "failed", None, "ocr_engine_error"), "failed"),
    (PageOcr(3, "failed", None, "ocr_engine_error"), PageOcr(4, "no_text", None, None), "mixed"),
])
def test_any_missing_page_dominates_the_aggregated_extraction_state(
    env, client, third: PageOcr, fourth: PageOcr, expected: str,
) -> None:
    job_id, _ = review_job(env)
    _record(env, job_id, p1=PageOcr(1, "missing", None, None),
            p2=PageOcr(2, "missing", None, None), p3=third, p4=fourth)

    listed = {tuple(i["page_numbers"]): i for i in _listing(client, env)["items"]}

    assert listed[(3, 4)]["text_extraction"] == expected


def test_list_can_be_filtered_to_one_durable_job(env, client) -> None:
    first_job, _ = review_job(env, name="first.pdf", marks=(1, 2, 3, 4))
    second_job, _ = review_job(env, name="second.pdf", marks=(5, 6, 7, 8))

    scoped = _listing(client, env, job_id=first_job)

    assert scoped["job_id"] == first_job
    assert {i["job_id"] for i in scoped["items"]} == {first_job}
    assert {i["job_id"] for i in _listing(client, env)["items"]} == {first_job, second_job}


@pytest.mark.parametrize("job_id", ["not-a-job", "00000000-0000-0000-0000-000000000000"])
def test_an_unknown_job_filter_returns_an_empty_scoped_collection(env, client, job_id) -> None:
    review_job(env)

    payload = _listing(client, env, job_id=job_id)

    assert payload == {"archive_scope_id": env.scope_id, "status": "pending",
                       "job_id": job_id, "items": []}


def test_a_job_from_another_scope_is_not_visible_through_the_filter(env, client,
                                                                    tmp_path) -> None:
    job_id, _ = review_job(env)
    other_root = tmp_path / "other-archive"
    other_root.mkdir()
    other_scope = env.store.scopes.register(other_root, home=env.home).id

    response = client.get("/api/v1/review-items",
                          params={"archive_scope_id": other_scope, "job_id": job_id})

    assert response.status_code == 200
    assert response.json() == {"archive_scope_id": other_scope, "status": "pending",
                               "job_id": job_id, "items": []}


# ---------------------------------------------------------------------------
# GET /api/v1/review-items/{item}/pages/{page}/text
# ---------------------------------------------------------------------------

def test_page_text_is_readable_after_a_process_restart(env, client, monkeypatch) -> None:
    from docflow.web import app as web_app

    job_id, items = review_job(env)
    _record(env, job_id, p1=_extracted(1, "ONE"), p2=_extracted(2, "SUGGESTED PAGE TEXT"),
            p3=_extracted(3, "THREE"), p4=_extracted(4, "FOUR"))
    item = items[(2,)]
    env.reopen()  # drop the writer and its lock, then reopen as a new process would
    monkeypatch.setattr(web_app, "_state_store", env.store)

    response = client.get(TEXT_URL.format(item=item.id, page=2),
                          params={"archive_scope_id": env.scope_id})

    assert response.status_code == 200
    assert response.json() == {"review_item_id": item.id, "job_id": job_id, "page_number": 2,
                               "status": "extracted", "text": "SUGGESTED PAGE TEXT",
                               "error_code": None}
    assert str(env.tmp) not in response.text


@pytest.mark.parametrize(("outcome", "expected"), [
    (PageOcr(3, "extracted", "THIRD PAGE TEXT", None),
     {"status": "extracted", "text": "THIRD PAGE TEXT", "error_code": None}),
    (PageOcr(3, "no_text", None, None),
     {"status": "no_text", "text": None, "error_code": None}),
    (PageOcr(3, "failed", None, "ocr_engine_error"),
     {"status": "failed", "text": None, "error_code": "ocr_engine_error"}),
    (PageOcr(3, "missing", None, None),
     {"status": "missing", "text": None, "error_code": None}),
])
def test_page_text_distinguishes_every_extraction_state(env, client, outcome, expected) -> None:
    job_id, items = review_job(env)
    _record(env, job_id, p1=PageOcr(1, "missing", None, None),
            p2=PageOcr(2, "missing", None, None), p3=outcome,
            p4=PageOcr(4, "missing", None, None))
    item = items[(3, 4)]

    response = client.get(TEXT_URL.format(item=item.id, page=3),
                          params={"archive_scope_id": env.scope_id})

    assert response.status_code == 200
    assert response.json() == {"review_item_id": item.id, "job_id": job_id,
                               "page_number": 3, **expected}


def test_page_numbers_are_physical_source_pages_not_item_offsets(env, client) -> None:
    """The item covers physical pages 3 and 4; ``1`` addresses neither of them."""
    job_id, items = review_job(env)
    _record(env, job_id, p1=_extracted(1, "PHYSICAL PAGE ONE"),
            p2=_extracted(2, "PHYSICAL PAGE TWO"), p3=_extracted(3, "PHYSICAL PAGE THREE"),
            p4=_extracted(4, "PHYSICAL PAGE FOUR"))
    item = items[(3, 4)]

    third = client.get(TEXT_URL.format(item=item.id, page=3),
                       params={"archive_scope_id": env.scope_id})
    first = client.get(TEXT_URL.format(item=item.id, page=1),
                       params={"archive_scope_id": env.scope_id})

    assert third.json()["text"] == "PHYSICAL PAGE THREE"
    assert (first.status_code, first.json()["error"]["code"]) == (404, "page_not_found")


@pytest.mark.parametrize("page", ["0", "-1", "99", "two", "1.5", "+2", "02x"])
def test_page_text_rejects_pages_outside_the_item(env, client, page: str) -> None:
    _, items = review_job(env)
    item = items[(2,)]

    response = client.get(TEXT_URL.format(item=item.id, page=page),
                          params={"archive_scope_id": env.scope_id})

    assert response.status_code in (404, 422)
    assert response.json()["error"]["code"] in {"page_not_found", "invalid_request"}


def test_unknown_and_cross_scope_items_are_indistinguishable(env, client, tmp_path) -> None:
    job_id, items = review_job(env)
    _record(env, job_id, p1=_extracted(1, "A"), p2=_extracted(2, "B"),
            p3=_extracted(3, "C"), p4=_extracted(4, "D"))
    other_root = tmp_path / "other-archive"
    other_root.mkdir()
    other_scope = env.store.scopes.register(other_root, home=env.home).id

    unknown = client.get(TEXT_URL.format(item="no-such-item", page=2),
                         params={"archive_scope_id": env.scope_id})
    cross = client.get(TEXT_URL.format(item=items[(2,)].id, page=2),
                       params={"archive_scope_id": other_scope})
    bad_scope = client.get(TEXT_URL.format(item=items[(2,)].id, page=2),
                           params={"archive_scope_id": "not-a-scope"})

    assert unknown.status_code == cross.status_code == 404
    assert unknown.json() == cross.json()  # existence is never disclosed across scopes
    assert unknown.json()["error"]["code"] == "review_item_not_found"
    assert (bad_scope.status_code, bad_scope.json()["error"]["code"]) == (404, "scope_not_found")
    assert "B" not in cross.json()["error"]["message"]
