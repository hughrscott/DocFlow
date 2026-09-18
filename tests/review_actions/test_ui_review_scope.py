"""Review contract: unmistakable scope, safe destinations, named undo, honest AI state.

``review.html`` and ``shared.js`` run in Node against the recording DOM and scripted
``fetch`` of ``ui_harness.js``. Nothing is served.

Pinned here:

* the queue always says whether it is showing **This batch** or **All documents**;
* a document nothing named is identified by its source pages, never by an invented title;
* a destination that could not be filed cannot be submitted, and says why;
* Undo names the action, the document and where it went;
* with AI classification off, the page does not offer to ask an AI.
"""
from __future__ import annotations

import json
import re

from tests.review_actions.test_ui_contract import SCOPE, STATIC, run_ui

BATCH = "job-7"
BATCH_QUERY = "^/api/v1/review-items\\?(?=.*job_id=job-7)"
GLOBAL_QUERY = "^/api/v1/review-items\\?(?!.*job_id=)"


def _item(item_id: str = "item-1", **overrides) -> dict:
    return {
        "id": item_id, "job_id": BATCH, "status": "pending", "source_name": "scan.pdf",
        "page_numbers": [3, 4], "source_page_range": "3-4", "text_extraction": "extracted",
        "reason": "unmatched", "near_duplicate_of": None, "doc_type": None, "period": None,
        "suggested_filename": None, "suggested_relative_directory": None, "confidence": None,
        "actions": ["correct", "skip"], "created_at": "2026-09-16T00:00:00+00:00",
        "updated_at": "2026-09-16T00:00:00+00:00", **overrides,
    }


def _health(ai: bool = False) -> dict:
    return {"method": "GET", "url": "^/api/health$", "body": {
        "watch_folder": "/synthetic/inbox", "archive_root": "/synthetic/archive",
        "privacy_mode": "local_only" if not ai else "cloud", "text_extraction": True,
        "ai_classification": ai, "llm_ready": ai,
        "llm_status": "ready" if ai else "local_only",
        "llm_status_label": "Ready" if ai else "Local Only",
        "llm_status_level": "ok" if ai else "neutral",
        "llm_status_detail": "OCR runs locally. AI classification is "
                             + ("on." if ai else "off."),
        "model": "synthetic", "threshold": 0.75}}


def _page(batch_items: list[dict], global_items: list[dict] | None = None,
          extra: list[dict] = (), ai: bool = False) -> list[dict]:
    listed = batch_items if global_items is None else global_items
    return [
        *extra,
        {"method": "GET", "url": "^/api/v1/archive-scopes/active$",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": BATCH_QUERY, "body": {
            "archive_scope_id": SCOPE, "status": "pending", "job_id": BATCH,
            "items": batch_items}},
        {"method": "GET", "url": GLOBAL_QUERY, "body": {
            "archive_scope_id": SCOPE, "status": "pending", "job_id": None,
            "items": listed}},
        {"method": "GET", "url": "^/api/unmatched$", "body": {"files": [], "count": 0}},
        {"method": "GET", "url": "^/api/v1/review-items/[^/]+/pages/\\d+/text\\?",
         "body": {"review_item_id": "item-1", "job_id": BATCH, "page_number": 3,
                  "status": "extracted", "text": "SYNTHETIC PAGE TEXT",
                  "error_code": None}},
        _health(ai),
    ]


def run_review(tmp_path, responses, steps, search=f"?job_id={BATCH}", storage=None):
    return run_ui(tmp_path, responses, steps, storage, page="review.html",
                  location={"pathname": "/review", "search": search})


def _posts(result: dict) -> list[dict]:
    return [f for f in result["fetches"] if f["method"] == "POST"]


def _listings(result: dict) -> list[str]:
    return [f["url"] for f in result["fetches"]
            if f["method"] == "GET" and f["url"].startswith("/api/v1/review-items?")]


# ---------------------------------------------------------------------------
# Scope is always stated
# ---------------------------------------------------------------------------

def test_a_job_filtered_queue_says_it_is_showing_one_batch(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()]),
                        ["__text('queue-scope-label')", "__text('review-progress-text')",
                         "__hidden('scope-switch')"])

    assert result["snapshots"][0]["value"] == "This batch"
    assert "This batch" in result["snapshots"][1]["value"]
    assert result["snapshots"][2]["value"] is False


def test_the_unfiltered_queue_says_it_is_showing_every_pending_document(tmp_path) -> None:
    result = run_review(tmp_path, _page([], [_item(), _item("item-2")]),
                        ["__text('queue-scope-label')", "__text('review-progress-text')"],
                        search="")

    assert result["snapshots"][0]["value"] == "All documents"
    assert "All documents" in result["snapshots"][1]["value"]
    assert not any("job_id=" in url for url in _listings(result))


def test_the_scope_switch_moves_between_this_batch_and_all_documents(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()], [_item(), _item("item-2")]), [
        # Markup onclick handlers are not wired in the recording DOM; the buttons call
        # exactly these functions (the real browser run clicks them).
        "__text('queue-count')", "setScope('all')",
        "__text('queue-scope-label')", "__text('queue-count')",
        "setScope('batch')", "__text('queue-scope-label')", "__text('queue-count')",
    ])

    assert result["snapshots"][0]["value"] == "1"
    assert result["snapshots"][2]["value"] == "All documents"
    assert result["snapshots"][3]["value"] == "2"
    assert result["snapshots"][5]["value"] == "This batch"
    assert result["snapshots"][6]["value"] == "1"
    # Both scopes were really asked for, and the batch filter is the durable job id.
    assert any("job_id=job-7" in url for url in _listings(result))
    assert any("job_id=" not in url for url in _listings(result))


def test_the_scope_switch_exposes_which_scope_is_active(tmp_path) -> None:
    pressed = ("(() => ['scope-batch', 'scope-all'].map("
               "id => document.getElementById(id).getAttribute('aria-pressed')))()")
    result = run_review(tmp_path, _page([_item()], [_item(), _item("item-2")]),
                        [pressed, "setScope('all')", pressed])

    assert result["snapshots"][0]["value"] == ["true", "false"]
    assert result["snapshots"][2]["value"] == ["false", "true"]


# ---------------------------------------------------------------------------
# Neutral document identity
# ---------------------------------------------------------------------------

def test_a_document_nothing_named_is_identified_by_its_source_pages(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item(), _item("item-2", page_numbers=[5],
                                                        source_page_range="5")]),
                        ["__text('queue-list')"])

    listed = result["snapshots"][0]["value"]
    assert "Document from pages 3-4" in listed
    assert "Document from page 5" in listed
    assert "scan.pdf" in listed
    assert "undefined" not in listed and "null" not in listed


def test_a_suggested_filename_is_used_as_the_document_title(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item(suggested_filename="Electric.pdf")]),
                        ["__text('queue-list')"])

    assert "Electric.pdf" in result["snapshots"][0]["value"]
    assert "Document from pages" not in result["snapshots"][0]["value"]


def test_the_decision_panel_names_the_document_it_belongs_to(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item(), _item("item-2", page_numbers=[5],
                                                        source_page_range="5")]),
                        ["__text('destination-owner')", "selectItem(1)",
                         "__text('destination-owner')"])

    assert "Document from pages 3-4" in result["snapshots"][0]["value"]
    assert "Document from page 5" in result["snapshots"][2]["value"]


# ---------------------------------------------------------------------------
# A destination that cannot be filed cannot be submitted
# ---------------------------------------------------------------------------

def _set(field: str, value: str) -> str:
    return (f"(() => {{ const el = document.getElementById('{field}');"
            f" el.value = {value!r}; el.dispatch('input'); }})()")


def test_an_empty_destination_blocks_filing_and_says_why(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()]), [
        "__disabled('btn-approve')", "__text('destination-error')", "doAction('approve')"])

    assert result["snapshots"][0]["value"] is True
    assert result["snapshots"][1]["value"]
    assert _posts(result) == []


def test_a_destination_outside_the_archive_is_refused_before_it_is_sent(tmp_path) -> None:
    for directory in ("../escape", "/etc", "~/elsewhere", "Household/../.."):
        result = run_review(tmp_path, _page([_item()]), [
            _set("edit-directory", directory), _set("edit-filename", "Electric.pdf"),
            "__disabled('btn-approve')", "__text('destination-error')",
            "doAction('approve')"])

        assert result["snapshots"][2]["value"] is True, directory
        assert result["snapshots"][3]["value"], directory
        assert _posts(result) == [], directory


def test_a_valid_destination_previews_the_exact_relative_path(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()]), [
        _set("edit-directory", "Household/Utilities"), _set("edit-filename", "Electric"),
        "__text('destination-preview')", "__disabled('btn-approve')",
        "__text('destination-error')"])

    assert result["snapshots"][2]["value"] == "Household/Utilities/Electric.pdf"
    assert result["snapshots"][3]["value"] is False
    assert result["snapshots"][4]["value"] == ""


def test_a_name_without_an_extension_is_filed_as_a_pdf(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()], extra=[
        {"method": "POST", "url": "^/api/v1/review-items/item-1/correct$", "body": {
            "operation": {"id": "op-1", "kind": "review_correct", "status": "completed",
                          "created_at": "t", "completed_at": "t", "undone_at": None},
            "outcome": "completed", "items": [], "jobs": []}},
    ]), [_set("edit-directory", "Household/Utilities"), _set("edit-filename", "Electric"),
         "doAction('approve')"])

    (correct,) = _posts(result)
    assert correct["body"]["filename"] == "Electric.pdf"
    assert correct["body"]["relative_directory"] == "Household/Utilities"


# ---------------------------------------------------------------------------
# Undo names what happened
# ---------------------------------------------------------------------------

def _filed(operation_id: str = "op-1", kind: str = "review_correct") -> dict:
    return {"method": "POST", "url": f"^/api/v1/review-items/item-1/{'correct' if kind == 'review_correct' else 'skip'}$",
            "body": {"operation": {"id": operation_id, "kind": kind, "status": "completed",
                                   "created_at": "t", "completed_at": "t", "undone_at": None},
                     "outcome": "completed", "items": [], "jobs": []}}


def test_undo_names_the_action_the_document_and_the_destination(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()], extra=[_filed()]), [
        _set("edit-directory", "Household/Utilities"), _set("edit-filename", "Electric.pdf"),
        "doAction('approve')"])

    stored = json.loads(result["storage"]["docflow_review_undo"])["label"]
    assert stored == 'Filed "Electric.pdf" to Household/Utilities'
    assert 'Filed "Electric.pdf" to Household/Utilities' in "\n".join(result["texts"])


def test_a_skip_names_the_document_it_skipped(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()], extra=[_filed("op-2", "review_skip")]),
                        ["doAction('skip')"])

    stored = json.loads(result["storage"]["docflow_review_undo"])["label"]
    assert stored == 'Skipped "Document from pages 3-4"'


def test_an_action_notification_replaces_the_one_before_it(tmp_path) -> None:
    """An old notification may never sit beside the item the user is looking at now."""
    def refused(item_id: str, message: str) -> dict:
        return {"method": "POST", "url": f"^/api/v1/review-items/{item_id}/skip$",
                "status": 409, "body": {"error": {"code": "review_item_not_pending",
                                                  "message": message}}}

    toasts = ("(() => document.getElementById('toast-container').children"
              ".map((t) => t.textContent))()")
    result = run_review(tmp_path, _page([_item(), _item("item-2")], extra=[
        refused("item-1", "First item was already handled."),
        refused("item-2", "Second item was already handled."),
    ]), ["doAction('skip')", toasts, "selectItem(1)", "doAction('skip')", toasts])

    assert len(result["snapshots"][1]["value"]) == 1
    (only,) = result["snapshots"][4]["value"]
    assert "Second item" in only and "First item" not in only


# ---------------------------------------------------------------------------
# Finishing a batch
# ---------------------------------------------------------------------------

def test_finishing_a_batch_offers_the_rest_of_the_queue_explicitly(tmp_path) -> None:
    result = run_review(tmp_path, _page([], [_item("item-9"), _item("item-8")]), [
        "__text('empty-title')", "__text('empty-sub')", "__text('empty-action')",
        "__hidden('empty-action')", "setScope('all')",
        "__text('queue-scope-label')", "__text('queue-count')"])

    assert result["snapshots"][0]["value"] == "Batch complete"
    assert "2" in result["snapshots"][1]["value"]
    assert result["snapshots"][2]["value"] == "Review remaining pending documents (2)"
    assert result["snapshots"][3]["value"] is False
    assert result["snapshots"][5]["value"] == "All documents"
    assert result["snapshots"][6]["value"] == "2"


def test_an_empty_global_queue_reads_differently_from_a_finished_batch(tmp_path) -> None:
    result = run_review(tmp_path, _page([], []), ["__text('empty-title')",
                                                  "__text('empty-sub')"], search="")

    assert result["snapshots"][0]["value"] == "All caught up"
    assert "batch" not in result["snapshots"][1]["value"].lower()


# ---------------------------------------------------------------------------
# Honest AI state
# ---------------------------------------------------------------------------

def test_with_ai_classification_off_the_page_does_not_offer_to_ask_an_ai(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()]),
                        ["__hidden('btn-ask-ai')", "__text('ai-note')"])

    assert result["snapshots"][0]["value"] is True
    note = result["snapshots"][1]["value"]
    assert "AI classification is off" in note


def test_with_ai_classification_on_the_control_is_offered(tmp_path) -> None:
    result = run_review(tmp_path, _page([_item()], ai=True), ["__hidden('btn-ask-ai')"])

    assert result["snapshots"][0]["value"] is False


def test_the_reasoning_panel_does_not_claim_an_archivist_reasoned(tmp_path) -> None:
    review = (STATIC / "review.html").read_text()

    assert "Archivist Reasoning" not in review
    assert re.search(r"Why this needs review", review)
