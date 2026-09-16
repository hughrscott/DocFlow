"""Dashboard remediation contract: unclassified output, the intake guard, and residue.

The shipped ``dashboard.html`` script and ``shared.js`` run in Node against the recording
DOM and scripted ``fetch`` of ``ui_harness.js``. Nothing is served and no file is read.

Three independent-review corrections are pinned here:

1. a run that never classified anything reads as *not classified*, never as a red 0% match;
2. a job recovered on load (saved job or server-active) holds the intake guard until it
   reaches a terminal state, so a reload during processing cannot start a second job;
3. selecting the next file clears every previous-batch action before the upload starts,
   including when that upload then fails.
"""
from __future__ import annotations

from tests.review_actions.test_ui_contract import run_ui
from tests.review_actions.test_ui_ocr_intake_contract import (
    SELECT,
    _dashboard,
    _document,
    _rendered,
    _status,
    run_dashboard,
)

PROCESSING = _status(status="processing", progress=45, step="Grouping pages into documents",
                     stage="grouping", documents_total=0, auto_filed=0, review_queue=0,
                     durable_job_id=None, review_url=None)


def _running(job_id: str) -> dict:
    return {"method": "GET", "url": f"^/api/process/status/{job_id}$", "body": PROCESSING}


def run_reload(tmp_path, responses, steps, session=None):
    """Load the dashboard as a reload would, with whatever the tab already saved."""
    return run_ui(tmp_path, responses, steps, None, page="dashboard.html",
                  location={"pathname": "/", "search": ""}, session=session or {})


# ---------------------------------------------------------------------------
# 1. Local-only / unclassified output is neutral, never a red 0% match
# ---------------------------------------------------------------------------

def test_a_document_that_was_never_classified_reads_as_not_classified(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status(
            documents=[_document(rule="none", notes="unmatched", confidence=None)])},
    ]), [SELECT, "__tick()"])

    rendered = _rendered(result)
    assert "Not classified" in rendered
    assert "0% Match" not in rendered
    assert "% Match" not in rendered


def test_a_real_classification_confidence_is_still_shown(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/p1$", "body": _status(
            documents=[_document(confidence=0.4),
                       _document(filename="Filed.pdf", auto_filed=True, confidence=0.95),
                       _document(filename="Unknown.pdf", rule="none", notes="unmatched",
                                 confidence=None)])},
    ]), [SELECT, "__tick()"])

    badges = _rendered(result).split("\n")
    assert "40% Match" in badges and "95% Match" in badges
    assert "Not classified" in badges
    assert "0% Match" not in badges


def test_recent_activity_never_renders_an_unclassified_entry_as_zero_percent(
    tmp_path,
) -> None:
    result = run_dashboard(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/archive/logs$", "body": {"logs": [{
            "timestamp": "2026-09-15T00:00:00", "entries": [
                {"filename": "Unknown.pdf", "target_directory": "Archive/_Unmatched",
                 "rule_matched": "none", "confidence": None}]}]}},
    ]), [])

    markup = "\n".join(write["html"] for write in result["html_writes"])
    assert "Not classified" in markup
    assert "0% Match" not in markup
    assert "NaN" not in markup


# ---------------------------------------------------------------------------
# 2. A recovered run holds the intake guard
# ---------------------------------------------------------------------------

def test_a_saved_running_job_disables_intake_before_polling(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([_running("saved-1")]),
                        ["__disabled('upload-input')"],
                        session={"docflow_active_job": "saved-1"})

    assert result["snapshots"][0]["value"] is True


def test_a_reload_during_processing_cannot_start_a_second_job(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([_running("saved-1")]),
                        [SELECT, "__tick()"],
                        session={"docflow_active_job": "saved-1"})

    assert [f for f in result["fetches"] if f["url"] == "/api/upload"] == []
    assert [f for f in result["fetches"] if f["url"] == "/api/process"] == []


def test_a_server_active_job_found_on_load_disables_intake_before_polling(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/active$",
         "body": {"job_id": "server-1", "status": "processing", "pdf": "monthly-mail.pdf"}},
        _running("server-1"),
    ]), ["__disabled('upload-input')", SELECT])

    assert result["snapshots"][0]["value"] is True
    assert [f for f in result["fetches"] if f["url"] == "/api/upload"] == []


def test_a_recovered_run_releases_the_guard_when_it_completes(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/saved-1$", "body": PROCESSING,
         "once": True},
        {"method": "GET", "url": "^/api/process/status/saved-1$", "body": _status()},
    ]), ["__disabled('upload-input')", "__tick()", "__disabled('upload-input')"],
        session={"docflow_active_job": "saved-1"})

    assert result["snapshots"][0]["value"] is True
    assert result["snapshots"][2]["value"] is False


def test_a_recovered_run_releases_the_guard_when_it_fails(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/saved-1$", "body": PROCESSING,
         "once": True},
        {"method": "GET", "url": "^/api/process/status/saved-1$",
         "body": _status(status="error", step="Error: synthetic failure")},
    ]), ["__disabled('upload-input')", "__tick()", "__disabled('upload-input')"],
        session={"docflow_active_job": "saved-1"})

    assert result["snapshots"][0]["value"] is True
    assert result["snapshots"][2]["value"] is False


def test_a_job_the_server_no_longer_knows_releases_the_guard(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/saved-1$", "body": PROCESSING,
         "once": True},
        {"method": "GET", "url": "^/api/process/status/saved-1$", "status": 404,
         "body": {"detail": "unknown job"}},
    ]), ["__disabled('upload-input')", "__tick()", "__disabled('upload-input')"],
        session={"docflow_active_job": "saved-1"})

    assert result["snapshots"][0]["value"] is True
    assert result["snapshots"][2]["value"] is False


def test_a_saved_job_that_already_finished_leaves_intake_open(tmp_path) -> None:
    result = run_reload(tmp_path, _dashboard([
        {"method": "GET", "url": "^/api/process/status/saved-1$", "body": _status()},
    ]), ["__disabled('upload-input')"], session={"docflow_active_job": "saved-1"})

    assert result["snapshots"][0]["value"] is False


# ---------------------------------------------------------------------------
# 3. The next selection clears the previous batch before its upload starts
# ---------------------------------------------------------------------------

RESIDUE = ["completion-summary", "capability-note", "documents-list"]


def test_selecting_the_next_file_clears_the_previous_batch_synchronously(tmp_path) -> None:
    steps = [SELECT, "__tick()",
             "acknowledgeSelection('second-scan.pdf')",
             *(f"__text('{element}')" for element in RESIDUE),
             "__hidden('completion-summary')", "__hrefs()"]
    result = run_dashboard(tmp_path, _dashboard(), steps)

    # The completed first batch really did leave an action behind.
    assert "Review this batch" in _rendered(result)
    # Selecting the next file clears it with no await in between.
    assert [snapshot["value"] for snapshot in result["snapshots"][3:6]] == ["", "", ""]
    assert result["snapshots"][6]["value"] is True
    assert [h for h in result["snapshots"][7]["value"]
            if "Review this batch" in h["text"]] == []


def test_a_failed_second_upload_leaves_no_previous_batch_action(tmp_path) -> None:
    result = run_dashboard(tmp_path, _dashboard(), [
        SELECT, "__tick()",
        # The second submission fails at upload; the acknowledgement must survive it
        # and the first batch's action must not.
        "__respond({method: 'POST', url: '^/api/upload$', status: 500,"
        " body: {detail: 'synthetic upload failure'}})",
        "handleFiles([{name: 'second-scan.pdf'}])",
        "__text('batch-name')", "__text('completion-summary')",
        "__hidden('completion-summary')", "__hrefs()",
    ])

    assert result["snapshots"][4]["value"] == "second-scan.pdf"
    assert result["snapshots"][5]["value"] == ""
    assert result["snapshots"][6]["value"] is True
    assert [h for h in result["snapshots"][7]["value"]
            if "Review this batch" in h["text"]] == []
