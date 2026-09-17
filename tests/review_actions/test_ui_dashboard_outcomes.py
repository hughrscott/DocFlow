"""Dashboard contract: one intake, one typed outcome, labelled scopes, real history.

The shipped ``dashboard.html`` script and ``shared.js`` run in Node against the recording
DOM and scripted ``fetch`` of ``ui_harness.js``. Nothing is served.

What is pinned here is what the page is allowed to *say*:

* there is one intake, called Add scanned mail;
* a finished run reports the server's typed outcome, never a guess from a count;
* an already-imported scan never reads as a new batch;
* global metrics say ``All documents`` and the current run says ``This batch``;
* history comes from durable state, so it is never "nothing processed yet" while
  batches exist.
"""
from __future__ import annotations

import re

from tests.review_actions.test_ui_contract import SCOPE, STATIC
from tests.review_actions.test_ui_ocr_intake_contract import (
    SELECT,
    _document,
    _rendered,
    _status,
    run_dashboard,
)

ACTIVITY = "^/api/v1/activity\\?"


def _batch(job_id: str = "durable-1", **overrides) -> dict:
    return {"job_id": job_id, "source_name": "monthly-mail.pdf", "status": "review",
            "created_at": "2026-09-16T10:00:00+00:00",
            "updated_at": "2026-09-16T10:01:00+00:00", "page_count": 4,
            "pages": {"pending": 0, "filed": 1, "review": 3, "skipped": 0, "blocked": 0},
            "pending_review": 1, "filed_documents": 1, "last_error_code": None, **overrides}


def _activity(listed: list[dict] | None = None, **totals) -> dict:
    listed = [] if listed is None else listed
    body = {
        "archive_scope_id": SCOPE,
        "totals": {"pending_review": 0, "filed_documents": 0, "filed_today": 0,
                   "batches": len(listed), **totals},
        "newest_batch": listed[0] if listed else None,
        "batches": listed,
    }
    return {"method": "GET", "url": ACTIVITY, "body": body}


def _page(extra: list[dict] = (), activity: dict | None = None) -> list[dict]:
    """The dashboard's default world, with activity answered from durable state."""
    from tests.review_actions.test_ui_ocr_intake_contract import _dashboard

    return _dashboard([*extra, activity or _activity()])


HALTED_STAGES = [
    {"id": "checking", "label": "Checking the scan", "status": "done"},
    {"id": "ocr", "label": "Reading pages with OCR", "status": "skipped"},
    {"id": "grouping", "label": "Grouping pages into documents", "status": "skipped"},
    {"id": "classifying", "label": "Matching filing rules", "status": "skipped"},
    {"id": "filing", "label": "Filing documents", "status": "skipped"},
    {"id": "done", "label": "Done", "status": "skipped"},
]


def _duplicate(**detail) -> dict:
    return _status(
        status="completed", outcome="duplicate", documents=[], documents_total=0,
        auto_filed=0, review_queue=0, durable_job_id=None, review_url=None,
        step="Already imported", stage="checking", stages=HALTED_STAGES,
        outcome_detail={"job_id": "durable-earlier", "source_name": "monthly-mail.pdf",
                        "status": "review", "created_at": "2026-09-16T10:00:00+00:00",
                        "updated_at": "2026-09-16T10:00:00+00:00", "pending_review": 1,
                        "filed_documents": 0, "filed_paths": [],
                        "review_url": "/review?job_id=durable-earlier",
                        "message": "This scan was already imported. Nothing was added.",
                        **detail})


# ---------------------------------------------------------------------------
# One intake, named the same everywhere
# ---------------------------------------------------------------------------

def test_the_intake_control_is_called_add_scanned_mail_everywhere() -> None:
    dashboard = (STATIC / "dashboard.html").read_text()
    shared = (STATIC / "shared.js").read_text()

    assert "Add scanned mail" in dashboard
    assert "Add scanned mail" in shared
    assert "Upload scanned mail" not in dashboard
    assert ">Scan Mail<" not in shared and "Scan Mail</span>" not in shared


def test_the_dashboard_offers_no_control_that_can_only_fail() -> None:
    dashboard = (STATIC / "dashboard.html").read_text()

    assert "/api/reprocess" not in dashboard
    assert "Re-process" not in dashboard


# ---------------------------------------------------------------------------
# Labelled scopes
# ---------------------------------------------------------------------------

def test_global_metrics_say_all_documents_and_come_from_durable_activity(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page(activity=_activity(
        [_batch()], pending_review=3, filed_documents=7, filed_today=2, batches=4)),
        ["__text('stat-review')", "__text('stat-filed')", "__text('stat-total')",
         "__text('stat-batches')"])

    assert [snap["value"] for snap in result["snapshots"]] == ["3", "2", "7", "4"]
    assert any("/api/v1/activity" in call["url"] for call in result["fetches"])
    # Every tile names its scope. The labels are static markup, which the recording DOM
    # does not carry, so they are read from the shipped page (and rendered for real in
    # the browser end-to-end run).
    assert (STATIC / "dashboard.html").read_text().count("All documents") >= 4


def test_the_running_batch_is_labelled_this_batch() -> None:
    dashboard = (STATIC / "dashboard.html").read_text()

    label = re.search(r'id="batch-scope-label"[^>]*>([^<]*)<', dashboard)
    assert label and label.group(1).strip() == "This batch"


def test_the_live_badge_reports_the_state_the_run_is_actually_in(tmp_path) -> None:
    running = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(status="processing", progress=45, stage="grouping", outcome=None)},
    ]), [SELECT, "__text('batch-state')"])
    finished = run_dashboard(tmp_path, _page(), [SELECT, "__tick()", "__text('batch-state')"])
    failed = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(status="error", outcome="failed", step="Error: synthetic",
                         documents_total=0, review_queue=0, auto_filed=0,
                         durable_job_id=None, review_url=None,
                         outcome_detail={"message": "broken.pdf could not be processed."})},
    ]), [SELECT, "__tick()", "__text('batch-state')"])
    duplicate = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _duplicate()},
    ]), [SELECT, "__tick()", "__text('batch-state')"])

    assert running["snapshots"][1]["value"] == "Processing live"
    assert finished["snapshots"][2]["value"] == "Batch complete"
    assert failed["snapshots"][2]["value"] == "Batch failed"
    assert duplicate["snapshots"][2]["value"] == "Already imported"


# ---------------------------------------------------------------------------
# Typed outcomes
# ---------------------------------------------------------------------------

def test_a_new_batch_states_what_was_added_and_links_its_own_review(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page(), [SELECT, "__tick()",
                                              "__text('completion-summary')", "__hrefs()"])

    summary = result["snapshots"][2]["value"]
    assert "Added 2 documents from monthly-mail.pdf" in summary
    assert "1 filed" in summary and "1 needs review" in summary
    assert any(h["href"] == "/review?job_id=durable-1" and "Review this batch" in h["text"]
               for h in result["snapshots"][3]["value"])


def test_an_already_imported_scan_never_reads_as_a_new_batch(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _duplicate()},
    ]), [SELECT, "__tick()", "__text('completion-summary')", "__hrefs()"])

    summary = result["snapshots"][2]["value"]
    assert "Already imported" in summary
    assert "Added" not in summary
    assert "0 documents" not in summary
    hrefs = result["snapshots"][3]["value"]
    assert any(h["href"] == "/review?job_id=durable-earlier"
               and "Open existing batch" in h["text"] for h in hrefs), hrefs
    assert [h for h in hrefs if "Review this batch" in h["text"]] == []


def test_a_duplicate_of_a_fully_filed_batch_states_where_it_already_is(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _duplicate(
            status="completed", pending_review=0, filed_documents=1,
            filed_paths=["Household/Utilities/Electric.pdf"], review_url=None)},
    ]), [SELECT, "__tick()", "__text('completion-summary')", "__hrefs()"])

    summary = result["snapshots"][2]["value"]
    assert "Already filed" in summary
    assert "Household/Utilities/Electric.pdf" in summary
    hrefs = result["snapshots"][3]["value"]
    assert [h for h in hrefs if "batch" in h["text"].lower()] == []


def test_a_failed_batch_explains_itself_and_offers_no_batch_link(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(status="error", outcome="failed", step="Error: synthetic",
                         documents=[], documents_total=0, review_queue=0, auto_filed=0,
                         durable_job_id=None, review_url=None,
                         outcome_detail={"message": "broken.pdf could not be processed, "
                                                    "so nothing was added."})},
    ]), [SELECT, "__tick()", "__text('completion-summary')", "__hrefs()"])

    summary = result["snapshots"][2]["value"]
    assert "could not be processed" in summary
    assert "filed" not in summary
    assert [h for h in result["snapshots"][3]["value"]
            if "batch" in h["text"].lower()] == []


def test_a_processed_batch_with_no_documents_says_exactly_that(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(outcome="new_empty", documents=[], documents_total=0,
                         auto_filed=0, review_queue=0,
                         outcome_detail={"message": "monthly-mail.pdf was read, but no "
                                                    "document was found in it."})},
    ]), [SELECT, "__tick()", "__text('completion-summary')"])

    summary = result["snapshots"][2]["value"]
    assert "no document was found" in summary
    assert "Already imported" not in summary


# ---------------------------------------------------------------------------
# The counts agree with each other
# ---------------------------------------------------------------------------

def test_global_counts_are_refreshed_when_a_run_finishes(tmp_path) -> None:
    """The queue tile may not still read 0 while the batch panel reports an item."""
    empty = _activity()
    empty["once"] = True
    result = run_dashboard(tmp_path, _page([empty], activity=_activity(
        [_batch()], pending_review=1)), [SELECT, "__tick()", "__text('stat-review')",
                                         "__text('completion-summary')"])

    assert result["snapshots"][2]["value"] == "1"
    assert "1 needs review" in result["snapshots"][3]["value"]


# ---------------------------------------------------------------------------
# Persisted history
# ---------------------------------------------------------------------------

def test_recent_activity_lists_the_batches_local_state_really_holds(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page(activity=_activity([
        _batch("durable-2", source_name="newest-scan.pdf"),
        _batch("durable-1", source_name="older-scan.pdf", status="completed",
               pending_review=0, filed_documents=2),
    ], pending_review=1, filed_documents=3, batches=2)), ["__text('recent-logs')"])

    listed = result["snapshots"][0]["value"]
    assert "newest-scan.pdf" in listed and "older-scan.pdf" in listed
    assert "No documents processed yet" not in _rendered(result)


def test_recent_activity_is_empty_only_when_no_batch_exists(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page(), ["__text('recent-logs')"])

    assert "No documents added yet" in result["snapshots"][0]["value"]


def test_the_newest_batch_is_reachable_from_the_history_list(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page(activity=_activity([
        _batch("durable-2", source_name="newest-scan.pdf", pending_review=2),
    ], pending_review=2, batches=1)), ["__hrefs()"])

    hrefs = result["snapshots"][0]["value"]
    assert any(h["href"] == "/review?job_id=durable-2" for h in hrefs), hrefs


# ---------------------------------------------------------------------------
# Neutral document labels
# ---------------------------------------------------------------------------

def test_a_document_with_no_name_is_labelled_by_its_source_pages(tmp_path) -> None:
    result = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status(documents=[
            _document(filename=None, directory=None, pages=[3, 4], confidence=None,
                      rule="none", notes="unmatched"),
            _document(filename=None, directory=None, pages=[5], confidence=None,
                      rule="none", notes="unmatched")])},
    ]), [SELECT, "__tick()", "__text('documents-list')"])

    listed = result["snapshots"][2]["value"]
    assert "Document from pages 3-4" in listed
    assert "Document from page 5" in listed
    assert "null" not in listed and "undefined" not in listed


STAGE_ICONS = ("(() => Array.from(document.getElementById('stage-list').children)"
               ".map((cell) => cell.children[0].children[0].textContent))()")


def test_a_stage_that_never_ran_is_not_shown_as_complete(tmp_path) -> None:
    halted = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _duplicate()},
    ]), [SELECT, "__tick()", STAGE_ICONS])
    waiting = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(status="processing", progress=20, stage="ocr", outcome=None,
                         stages=[{"id": "checking", "label": "Checking the scan",
                                  "status": "done"},
                                 {"id": "ocr", "label": "Reading pages with OCR",
                                  "status": "active"},
                                 {"id": "done", "label": "Done", "status": "waiting"}])},
    ]), [SELECT, "__tick()", STAGE_ICONS])

    icons = halted["snapshots"][2]["value"]
    # "checking" really completed; nothing after it may look complete, and a stage that
    # was never run must not look like one that is still to come.
    assert icons[0] == "check_circle"
    assert set(icons[1:]) and "check_circle" not in icons[1:]
    still_to_come = waiting["snapshots"][2]["value"][-1]
    assert still_to_come not in icons[1:]


def test_a_run_that_created_no_batch_is_not_labelled_this_batch(tmp_path) -> None:
    duplicate = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _duplicate()},
    ]), [SELECT, "__hidden('batch-scope-label')", "__tick()", "__hidden('batch-scope-label')"])
    failed = run_dashboard(tmp_path, _page([
        {"method": "GET", "url": "^/api/process/status/p1$",
         "body": _status(status="error", outcome="failed", documents_total=0,
                         review_queue=0, auto_filed=0, durable_job_id=None, review_url=None,
                         outcome_detail={"message": "broken.pdf could not be processed."})},
    ]), [SELECT, "__tick()", "__hidden('batch-scope-label')"])
    new = run_dashboard(tmp_path, _page(), [SELECT, "__tick()", "__hidden('batch-scope-label')"])

    assert duplicate["snapshots"][1]["value"] is False  # while it runs, it is this batch
    assert duplicate["snapshots"][3]["value"] is True
    assert failed["snapshots"][2]["value"] is True
    assert new["snapshots"][2]["value"] is False
