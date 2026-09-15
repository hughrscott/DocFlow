"""UI contract for page text, page previews and the single Add-scanned-mail flow.

The shipped pages run in Node against a recording DOM and a scripted ``fetch``
(``ui_harness.js``); nothing is served and no file is read from disk.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.review_actions.test_ui_contract import KEY, SCOPE, STATIC, run_ui

PAYLOAD = '<img src=x onerror="window.__xss=1">'
MARKERS = ("<img", "onerror", "<svg", "__xss")


def _item(item_id: str = "item-1", **overrides) -> dict:
    return {
        "id": item_id, "job_id": "job-1", "status": "pending", "source_name": "scan.pdf",
        "page_numbers": [3, 4], "source_page_range": "3-4", "text_extraction": "mixed",
        "reason": "unmatched", "near_duplicate_of": None, "doc_type": "Statement",
        "period": "2026-08", "suggested_filename": "Suggested.pdf",
        "suggested_relative_directory": "Review", "confidence": 0.4,
        "actions": ["correct", "skip"], "created_at": "2026-09-13T00:00:00+00:00",
        "updated_at": "2026-09-13T00:00:00+00:00", **overrides,
    }


def _text_body(page: int, status: str = "extracted", text: str | None = "PAGE TEXT",
               error_code: str | None = None) -> dict:
    return {"review_item_id": "item-1", "job_id": "job-1", "page_number": page,
            "status": status, "text": text, "error_code": error_code}


def _review(items: list[dict], extra: list[dict] = (), job_id=None) -> list[dict]:
    return [
        {"method": "GET", "url": "^/api/v1/archive-scopes/active$",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": "^/api/v1/review-items\\?", "body": {
            "archive_scope_id": SCOPE, "status": "pending", "job_id": job_id, "items": items}},
        {"method": "GET", "url": "^/api/unmatched$", "body": {"files": [], "count": 0}},
        {"method": "GET", "url": "^/api/health$", "body": {}},
        *extra,
    ]


def run_review(tmp_path, responses, steps, storage=None, search=""):
    return run_ui(tmp_path, responses, steps, storage,
                  page="review.html", location={"pathname": "/review", "search": search})


def _gets(result: dict, fragment: str) -> list[dict]:
    return [f for f in result["fetches"] if f["method"] == "GET" and fragment in f["url"]]


def _rendered(result: dict) -> str:
    return "\n".join([*result["texts"],
                      *(t for s in result["snapshots"] for t in s["texts"])])


# ---------------------------------------------------------------------------
# Review page: scoped v1 routes for per-page text and preview
# ---------------------------------------------------------------------------

def test_review_page_applies_the_job_id_query_filter(tmp_path) -> None:
    result = run_review(tmp_path, _review([_item()], [
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?",
         "body": _text_body(3)},
    ]), [], search="?job_id=job-7")

    listed = _gets(result, "/api/v1/review-items?")
    assert listed and all(f"archive_scope_id={SCOPE}" in call["url"] for call in listed)
    queue = [call for call in listed if "job_id=job-7" in call["url"]]
    assert len(queue) == 1
    assert "status=pending" in queue[0]["url"]


def test_no_job_id_query_means_no_job_filter(tmp_path) -> None:
    result = run_review(tmp_path, _review([_item()], [
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?",
         "body": _text_body(3)},
    ]), [])

    listed = _gets(result, "/api/v1/review-items?")
    assert listed and not any("job_id=" in call["url"] for call in listed)


def test_each_selected_physical_page_fetches_its_own_scoped_text_and_preview(tmp_path) -> None:
    result = run_review(tmp_path, _review([_item()], [
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?",
         "body": _text_body(3, text="THIRD PAGE TEXT")},
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/4/text\\?",
         "body": _text_body(4, text="FOURTH PAGE TEXT")},
    ]), ["__imgSrc('preview-image')", "nextPage()", "__imgSrc('preview-image')"])

    third, fourth = _gets(result, "/pages/3/text"), _gets(result, "/pages/4/text")
    assert len(third) == len(fourth) == 1
    for call in (*third, *fourth):
        assert f"archive_scope_id={SCOPE}" in call["url"]
    rendered = _rendered(result)
    assert "THIRD PAGE TEXT" in rendered and "FOURTH PAGE TEXT" in rendered
    # The preview <img> points at the scoped v1 route for the physical page.
    assert result["snapshots"][0]["value"] == (
        f"/api/v1/review-items/item-1/pages/3/preview?archive_scope_id={SCOPE}")
    assert result["snapshots"][2]["value"] == (
        f"/api/v1/review-items/item-1/pages/4/preview?archive_scope_id={SCOPE}")


@pytest.mark.parametrize(("body", "fragment"), [
    (_text_body(3, "extracted", "READABLE PAGE TEXT"), "READABLE PAGE TEXT"),
    (_text_body(3, "no_text", None), "no text"),
    (_text_body(3, "failed", None, "ocr_engine_error"), "could not be read"),
    (_text_body(3, "missing", None), "not been read yet"),
])
def test_every_extraction_state_renders_its_own_message(tmp_path, body, fragment) -> None:
    result = run_review(tmp_path, _review([_item()], [
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?", "body": body},
    ]), ["setPreviewMode('text')"])

    assert fragment in _rendered(result)


def test_distinct_extraction_states_never_render_the_same_message(tmp_path) -> None:
    messages = set()
    for status, text, code in [("no_text", None, None), ("failed", None, "ocr_engine_error"),
                               ("missing", None, None)]:
        result = run_review(tmp_path, _review([_item()], [
            {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?",
             "body": _text_body(3, status, text, code)},
        ]), ["setPreviewMode('text')"])
        shown = [t for t in result["texts"] if "page" in t.lower() or "read" in t.lower()]
        messages.add(json.dumps(sorted(shown)))
    assert len(messages) == 3


def test_labels_name_the_physical_source_page_range(tmp_path) -> None:
    result = run_review(tmp_path, _review([_item()], [
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?",
         "body": _text_body(3)},
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/4/text\\?",
         "body": _text_body(4)},
    ]), ["nextPage()"])

    rendered = _rendered(result)
    assert "Source pages 3-4" in rendered
    # The page navigator names the physical page, not an offset inside the item.
    assert "Source page 4 of 3-4" in rendered
    assert "Page 1 of 2" not in rendered


def test_page_text_and_error_codes_are_rendered_as_text_never_markup(tmp_path) -> None:
    hostile = _item(source_page_range=PAYLOAD, text_extraction=PAYLOAD)
    result = run_review(tmp_path, _review([hostile], [
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/3/text\\?",
         "body": _text_body(3, "failed", None, PAYLOAD)},
        {"method": "GET", "url": "^/api/v1/review-items/item-1/pages/4/text\\?",
         "body": _text_body(4, "extracted", PAYLOAD)},
    ]), ["setPreviewMode('text')", "nextPage()"])

    unsafe = [w["html"] for w in result["html_writes"] if any(m in w["html"] for m in MARKERS)]
    assert unsafe == []
    assert PAYLOAD in _rendered(result)


def test_review_page_no_longer_uses_the_retired_preview_route() -> None:
    review = (STATIC / "review.html").read_text()
    assert "/api/preview/${" not in review
    assert not re.search(r"/api/preview/\$\{item\.id\}", review)
    assert "raw_text_preview" not in review


# ---------------------------------------------------------------------------
# Dashboard: one Add-scanned-mail flow
# ---------------------------------------------------------------------------

STATUS_STAGES = [
    {"id": "checking", "label": "Checking the scan", "status": "done"},
    {"id": "ocr", "label": "Reading pages with OCR", "status": "done"},
    {"id": "grouping", "label": "Grouping pages into documents", "status": "done"},
    {"id": "classifying", "label": "Matching filing rules", "status": "done"},
    {"id": "filing", "label": "Filing documents", "status": "done"},
    {"id": "done", "label": "Done", "status": "done"},
]


def _status(**overrides) -> dict:
    return {
        "status": "completed", "filename": "monthly-mail.pdf", "pdf": "monthly-mail.pdf",
        "progress": 100, "step": "Done", "stage": "done", "stages": STATUS_STAGES,
        "documents": [], "documents_total": 2, "auto_filed": 1, "review_queue": 1,
        "pages_total": 4, "durable_job_id": "durable-1",
        "review_url": "/review?job_id=durable-1",
        "capabilities": {"text_extraction": True, "ai_classification": False,
                         "summary": "Text extraction is enabled. "
                                    "AI classification is disabled."},
        "started": "2026-09-15T00:00:00", **overrides,
    }


def _dashboard(extra: list[dict] = ()) -> list[dict]:
    # Extras come first: the harness takes the first matching rule, so a test that
    # overrides a default (for example the status body) really does override it.
    return [
        *extra,
        {"method": "GET", "url": "^/api/v1/archive-scopes/active$",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": "^/api/v1/review-items\\?", "body": {
            "archive_scope_id": SCOPE, "status": "pending", "job_id": None, "items": []}},
        {"method": "GET", "url": "^/api/archive/logs$", "body": {"logs": []}},
        {"method": "GET", "url": "^/api/health$", "body": {}},
        {"method": "GET", "url": "^/api/process/active$", "body": {"job_id": None}},
        {"method": "POST", "url": "^/api/upload$",
         "body": {"filename": "monthly-mail.pdf", "path": "/state/uploads/monthly-mail.pdf",
                  "size": 10}},
        {"method": "POST", "url": "^/api/process$", "body": {"job_id": "p1", "reused": False}},
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status()},
    ]


def run_dashboard(tmp_path, responses, steps, storage=None):
    return run_ui(tmp_path, responses, steps, storage,
                  page="dashboard.html", location={"pathname": "/", "search": ""})


SELECT = "handleFiles([{name: 'monthly-mail.pdf'}])"


def test_selecting_a_file_acknowledges_its_name_before_processing_finishes(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "POST", "url": "^/api/upload$", "status": 500, "body": {"detail": "nope"}},
    ]), [SELECT])

    # The acknowledgment happens on selection, so it survives a failed upload.
    assert "monthly-mail.pdf" in _rendered(result)


def test_one_submission_sends_one_upload_and_one_process_with_a_submission_key(
    tmp_path,
) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [SELECT])

    uploads = [f for f in result["fetches"] if f["url"] == "/api/upload"]
    processes = [f for f in result["fetches"] if f["url"] == "/api/process"]
    assert len(uploads) == len(processes) == 1
    assert processes[0]["body"]["path"] == "/state/uploads/monthly-mail.pdf"
    assert KEY.match(processes[0]["body"]["submission_key"])


def test_a_duplicate_submission_while_one_is_in_flight_is_suppressed(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [f"{SELECT}; {SELECT}; {SELECT}"])

    assert len([f for f in result["fetches"] if f["url"] == "/api/process"]) == 1
    assert len([f for f in result["fetches"] if f["url"] == "/api/upload"]) == 1


def test_the_file_input_is_disabled_while_a_submission_is_in_flight(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(status="processing", progress=45, stage="grouping")},
    ]), [SELECT, "__disabled('upload-input')", "__tick()"])

    assert result["snapshots"][1]["value"] is True


def test_the_dashboard_renders_only_the_stages_the_server_reports(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [SELECT, "__tick()"])

    rendered = _rendered(result)
    for stage in STATUS_STAGES:
        assert stage["label"] in rendered
    assert "Summary" not in rendered
    assert "Gate" not in rendered


def test_completion_shows_counts_and_a_job_scoped_review_this_batch_action(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [SELECT, "__tick()", "__hrefs()"])

    rendered = _rendered(result)
    assert "1 filed" in rendered and "1 needs review" in rendered
    hrefs = result["snapshots"][2]["value"]
    assert any(h["href"] == "/review?job_id=durable-1" and "Review this batch" in h["text"]
               for h in hrefs), hrefs


def test_local_only_capability_copy_is_shown_and_promises_no_ai_titles(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [SELECT, "__tick()"])

    rendered = _rendered(result)
    assert "Text extraction is enabled. AI classification is disabled." in rendered
    assert "AI title" not in rendered and "AI name" not in rendered


def _document(**overrides) -> dict:
    return {"filename": "Statement.pdf", "directory": "Household/Utilities",
            "institution": "synthbank", "doc_type": "statement", "period": "2026-08",
            "pages": [1, 2], "rule": "synthetic", "confidence": 0.4, "auto_filed": False,
            "notes": "low_confidence", "reasoning": "", **overrides}


def test_identified_documents_are_listed_with_a_scoped_review_action(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status(
            documents=[_document(), _document(filename="Filed.pdf", auto_filed=True,
                                              confidence=0.95)])},
    ]), [SELECT, "__tick()", "__hrefs()"])

    rendered = _rendered(result)
    assert "Statement.pdf" in rendered and "Filed.pdf" in rendered
    assert "Household/Utilities" in rendered
    assert "40% Match" in rendered and "95% Match" in rendered
    # A document still awaiting review offers a review action scoped to this job.
    hrefs = result["snapshots"][2]["value"]
    assert [h["href"] for h in hrefs if "Review" in h["text"]] == [
        "/review?job_id=durable-1", "/review?job_id=durable-1"]


def test_document_filenames_and_folders_are_rendered_as_text_never_markup(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status(
            documents=[_document(filename=f"{PAYLOAD}.pdf",
                                 directory=f"Household/{PAYLOAD}")])},
    ]), [SELECT, "__tick()"])

    unsafe = [w["html"] for w in result["html_writes"] if any(m in w["html"] for m in MARKERS)]
    assert unsafe == []
    assert f"{PAYLOAD}.pdf" in _rendered(result)


def test_completion_summary_is_rendered_as_text_never_markup(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status(
            filename=PAYLOAD, step=PAYLOAD,
            stages=[{"id": "ocr", "label": PAYLOAD, "status": "done"}],
            capabilities={"text_extraction": True, "ai_classification": False,
                          "summary": PAYLOAD},
            review_url="javascript:__xss()")},
    ]), [SELECT, "__tick()", "__hrefs()"])

    unsafe = [w["html"] for w in result["html_writes"] if any(m in w["html"] for m in MARKERS)]
    assert unsafe == []
    assert not any("javascript:" in h["href"] for h in result["snapshots"][2]["value"])


# ---------------------------------------------------------------------------
# One Add flow: the global Scan Mail button never submits by itself
# ---------------------------------------------------------------------------

def test_global_scan_mail_routes_to_the_dashboard_input_and_never_submits() -> None:
    shared = (STATIC / "shared.js").read_text()
    assert "/api/upload" not in shared
    assert "/api/process" not in shared


def test_scan_mail_from_another_page_navigates_to_the_single_dashboard_flow(tmp_path) -> None:
    result = run_review(tmp_path, _review([]), ["globalUpload()", "String(window.location)"])

    assert [f for f in result["fetches"] if f["method"] == "POST"] == []
    assert result["snapshots"][1]["value"] == "/"


def test_scan_mail_on_the_dashboard_opens_the_one_shared_input(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [
        "let clicked = 0; document.getElementById('upload-input').click = () => { clicked += 1; };"
        " globalUpload(); clicked",
    ])

    assert result["snapshots"][0]["value"] == 1
