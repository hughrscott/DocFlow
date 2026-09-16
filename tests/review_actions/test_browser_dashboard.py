"""Real-browser coverage of the dashboard intake, recovery and unclassified output.

A headless Chromium loads the shipped ``dashboard.html`` from a loopback stub that answers
with synthetic JSON only. The browser cannot resolve any host but the stub, so the run makes
no outbound request; every process it starts is bounded and torn down.

These cover the paths the Node DOM harness cannot: a real ``<input type=file>`` selection, a
real reload and a real cross-page navigation, and real timers driving the poll loop.
"""
from __future__ import annotations

import multiprocessing
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.review_actions.browser_harness import (
    Browser,
    StubServer,
    anchor_texts,
    browser_binary,
    hidden,
    select_file,
    text_of,
)
from tests.review_actions.test_ui_ocr_intake_contract import STATUS_STAGES

SCOPE = "scope-synthetic-browser"


def _status(**overrides) -> dict:
    return {
        "status": "completed", "filename": "synthetic-one.pdf", "pdf": "synthetic-one.pdf",
        "progress": 100, "step": "Done", "stage": "done", "stages": STATUS_STAGES,
        "documents": [], "documents_total": 2, "auto_filed": 1, "review_queue": 1,
        "durable_job_id": "durable-1", "review_url": "/review?job_id=durable-1",
        "capabilities": {"text_extraction": True, "ai_classification": False,
                         "summary": "Text extraction is enabled. "
                                    "AI classification is disabled."},
        "started": "2026-09-15T00:00:00", **overrides,
    }


def _processing(**overrides) -> dict:
    return _status(status="processing", progress=45, step="Grouping pages into documents",
                   stage="grouping", documents_total=0, auto_filed=0, review_queue=0,
                   durable_job_id=None, review_url=None, **overrides)


def _document(**overrides) -> dict:
    return {"filename": "Statement.pdf", "directory": "Household/Utilities",
            "institution": "synthbank", "doc_type": "statement", "period": "2026-08",
            "pages": [1, 2], "rule": "synthetic", "confidence": 0.4, "auto_filed": False,
            "notes": "low_confidence", "reasoning": "", **overrides}


def _routes(extra: list[dict] = ()) -> list[dict]:
    return [
        *extra,
        {"method": "GET", "url": r"^/api/v1/archive-scopes/active",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": r"^/api/v1/review-items",
         "body": {"archive_scope_id": SCOPE, "status": "pending", "job_id": None,
                  "items": []}},
        {"method": "GET", "url": r"^/api/archive/logs", "body": {"logs": []}},
        {"method": "GET", "url": r"^/api/archive/tree", "body": {"tree": []}},
        {"method": "GET", "url": r"^/api/health", "body": {
            "watch_folder": "/synthetic/inbox", "archive_root": "/synthetic/archive",
            "privacy_mode": "local_only", "text_extraction": True,
            "ai_classification": False, "llm_ready": False, "llm_status": "local_only",
            "llm_status_label": "Local Only", "llm_status_level": "neutral",
            "llm_status_detail": "Text extraction is enabled. AI classification is "
                                 "disabled.",
            "model": "local", "threshold": 0.75}},
        {"method": "GET", "url": r"^/api/process/active", "body": {"job_id": None}},
        {"method": "POST", "url": r"^/api/upload",
         "body": {"filename": "synthetic-one.pdf",
                  "path": "/state/uploads/synthetic-one.pdf", "size": 18}},
        {"method": "POST", "url": r"^/api/process",
         "body": {"job_id": "p1", "reused": False}},
    ]


@pytest.fixture()
def stub():
    server = StubServer([])
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def browser(tmp_path: Path, stub):
    binary = browser_binary()
    profile = tmp_path / "browser-profile"
    profile.mkdir()
    session = Browser(binary, profile, f"{stub.origin}/")
    try:
        session.wait_ready()
        yield session
    finally:
        session.close()


def _serve(stub: StubServer, routes: list[dict]) -> None:
    stub.routes[:] = routes


IDLE = "return !document.getElementById('upload-input').disabled;"


def _wait_calls(stub: StubServer, method: str, path: str, count: int) -> None:
    """Wait, bounded, until the page has made ``count`` calls to ``path``."""
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if len(stub.calls(method, path)) >= count:
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} was called {len(stub.calls(method, path))} times, "
                         f"expected {count}")


# ---------------------------------------------------------------------------
# Intake lock on the first submission
# ---------------------------------------------------------------------------

def test_selecting_a_file_locks_intake_until_the_run_finishes(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _processing()},
    ]))
    browser.open(f"{stub.origin}/")

    browser.evaluate(select_file("synthetic-one.pdf"))
    # The guard is set before the first await, so the input locks immediately.
    assert browser.evaluate("return document.getElementById('upload-input').disabled;")
    browser.wait_for("return sessionStorage.getItem('docflow_active_job') === 'p1';",
                     what="the submission to reach the server")

    # A second selection while the run is in flight starts no second job.
    browser.evaluate(select_file("synthetic-two.pdf"))
    assert len(stub.calls("POST", "/api/upload")) == 1
    assert len(stub.calls("POST", "/api/process")) == 1

    stub.respond({"method": "GET", "url": r"^/api/process/status/p1", "body": _status()})
    browser.wait_for("return !document.getElementById('upload-input').disabled;",
                     what="the file input to unlock")


# ---------------------------------------------------------------------------
# Reattachment: the saved job, and the job the server reports active
# ---------------------------------------------------------------------------

def test_a_reload_while_processing_reattaches_and_keeps_intake_locked(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _processing()},
    ]))
    browser.open(f"{stub.origin}/")
    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return sessionStorage.getItem('docflow_active_job') === 'p1';",
                     what="the job to be saved for recovery")

    browser.reload()

    # The saved-job branch: recovered from sessionStorage, guard held before polling.
    assert browser.evaluate("return sessionStorage.getItem('docflow_active_job');") == "p1"
    browser.wait_for("return document.getElementById('upload-input').disabled;",
                     what="intake to stay locked after the reload")
    uploads = len(stub.calls("POST", "/api/upload"))
    browser.evaluate(select_file("synthetic-two.pdf"))
    assert len(stub.calls("POST", "/api/upload")) == uploads


def test_navigating_away_and_back_while_processing_keeps_intake_locked(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _processing()},
    ]))
    browser.open(f"{stub.origin}/")
    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return sessionStorage.getItem('docflow_active_job') === 'p1';",
                     what="the job to be saved for recovery")

    browser.open(f"{stub.origin}/review", marker="typeof loadQueue === 'function'")
    browser.open(f"{stub.origin}/")

    browser.wait_for("return document.getElementById('upload-input').disabled;",
                     what="intake to stay locked after navigating back")


def test_a_server_reported_active_job_locks_intake_on_a_cold_load(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/active",
         "body": {"job_id": "server-1", "status": "processing", "pdf": "synthetic-one.pdf"}},
        {"method": "GET", "url": r"^/api/process/status/server-1", "body": _processing()},
    ]))
    # No saved job: this is the server-active recovery branch.
    browser.open(f"{stub.origin}/")
    browser.evaluate("sessionStorage.clear(); return null;")
    browser.reload()

    browser.wait_for("return document.getElementById('upload-input').disabled;",
                     what="intake to lock for the server-reported job")
    browser.evaluate(select_file("synthetic-two.pdf"))
    assert stub.calls("POST", "/api/upload") == []

    stub.respond({"method": "GET", "url": r"^/api/process/status/server-1",
                  "body": _status()})
    browser.wait_for("return !document.getElementById('upload-input').disabled;",
                     what="intake to unlock when the recovered job finishes")


# ---------------------------------------------------------------------------
# Sequential selection, including a failed second upload
# ---------------------------------------------------------------------------

def test_a_failed_second_upload_leaves_no_action_from_the_first_batch(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status()},
    ]))
    browser.open(f"{stub.origin}/")

    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return !document.getElementById('completion-summary')"
                     ".classList.contains('hidden');", what="the first batch to complete")
    assert "Review this batch" in browser.evaluate(text_of("completion-summary"))
    browser.wait_for(IDLE, what="the first submission to release the intake guard")

    stub.respond({"method": "POST", "url": r"^/api/upload", "status": 500,
                  "body": {"detail": "synthetic upload failure"}})
    browser.evaluate(select_file("synthetic-two.pdf"))
    # The second upload really was attempted, and really did fail.
    _wait_calls(stub, "POST", "/api/upload", 2)
    browser.wait_for(IDLE, what="the failed second submission to settle")

    assert browser.evaluate(text_of("batch-name")) == "synthetic-two.pdf"
    assert browser.evaluate(text_of("completion-summary")) == ""
    assert browser.evaluate(hidden("completion-summary")) is True
    assert browser.evaluate(text_of("documents-list")) == ""
    assert browser.evaluate(hidden("documents-section")) is True
    assert browser.evaluate(text_of("capability-note")) == ""
    assert [a for a in browser.evaluate(anchor_texts())
            if "Review this batch" in a[1]] == []


def test_a_second_successful_selection_replaces_the_first_batch(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status(
            documents=[_document()])},
    ]))
    browser.open(f"{stub.origin}/")

    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return document.getElementById('documents-list')"
                     ".textContent.includes('Statement.pdf');",
                     what="the first batch's documents")
    browser.wait_for(IDLE, what="the first submission to release the intake guard")

    stub.respond({"method": "POST", "url": r"^/api/process",
                  "body": {"job_id": "p2", "reused": False}})
    stub.respond({"method": "GET", "url": r"^/api/process/status/p2",
                  "body": _processing(filename="synthetic-two.pdf")})
    browser.evaluate(select_file("synthetic-two.pdf"))

    browser.wait_for("return document.getElementById('batch-name').textContent"
                     " === 'synthetic-two.pdf';", what="the new batch name")
    assert browser.evaluate(text_of("completion-summary")) == ""
    assert browser.evaluate(text_of("documents-list")) == ""


# ---------------------------------------------------------------------------
# Local-only: an unclassified document is neutral, never a red 0% match
# ---------------------------------------------------------------------------

DANGER = ("#BE4029", "rgb(190, 64, 41)")


def test_a_local_only_run_renders_unclassified_documents_neutrally(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status(
            documents=[_document(filename="Unknown.pdf", directory="_Unmatched",
                                 rule="none", notes="unmatched", confidence=None)],
            documents_total=1, auto_filed=0, review_queue=1)},
    ]))
    browser.open(f"{stub.origin}/")

    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return document.getElementById('documents-list')"
                     ".textContent.includes('Unknown.pdf');",
                     what="the unclassified document")

    listing = browser.evaluate(text_of("documents-list"))
    assert "Not classified" in listing
    assert "% Match" not in listing
    colours = browser.evaluate(
        "return Array.from(document.querySelectorAll('#documents-list span'))"
        ".map((s) => [s.textContent.trim(), s.style.color, s.style.background]);")
    badge = [c for c in colours if c[0] == "Not classified"]
    assert badge, colours
    assert not any(shade in str(badge) for shade in DANGER)
    # The honest capability sentence is still what the server sent.
    assert browser.evaluate(text_of("capability-note")) == (
        "Text extraction is enabled. AI classification is disabled.")


def test_a_cloud_classification_confidence_is_still_shown_in_the_browser(
    stub, browser,
) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status(
            documents=[_document(confidence=0.91, auto_filed=True),
                       _document(filename="Unknown.pdf", rule="none", notes="unmatched",
                                 confidence=None)])},
    ]))
    browser.open(f"{stub.origin}/")

    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return document.getElementById('documents-list')"
                     ".textContent.includes('Unknown.pdf');", what="both documents")

    listing = browser.evaluate(text_of("documents-list"))
    assert "91% Match" in listing
    assert "Not classified" in listing
    assert "0% Match" not in listing


# ---------------------------------------------------------------------------
# No egress
# ---------------------------------------------------------------------------

def test_the_browser_run_reaches_nothing_but_the_loopback_stub(stub, browser) -> None:
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status()},
    ]))
    browser.open(f"{stub.origin}/")
    browser.evaluate(select_file("synthetic-one.pdf"))
    browser.wait_for("return !document.getElementById('upload-input').disabled;",
                     what="the run to finish")

    assert stub.requests, "the page never reached the stub"
    assert all(request["path"].startswith("/") for request in stub.requests)


# ---------------------------------------------------------------------------
# Navigation readiness: open/reload must return on the new document, not the old
# ---------------------------------------------------------------------------

REVIEW_READY = "typeof loadQueue === 'function'"
NAVIGATIONS = 50  # bounded repetition: the race is rare per navigation, not per run
STALE = "window.__docflowPreviousDocument"
# One round trip, so the answer describes a single instant rather than a sequence the
# navigation could slip between.
LANDED = (f"return [document.readyState, {STALE} === true, location.pathname,"
          " document.getElementById('upload-input') !== null,"
          " document.getElementById('decision-panel') !== null].join();")
DASHBOARD_LIVE = "complete,false,/,true,false"
REVIEW_LIVE = "complete,false,/review,false,true"


@contextmanager
def _cpu_contention():
    """Bounded background load: it widens the window a readiness check can land in.

    A busy machine is slower to take a requested navigation, so a readiness check that
    the pre-navigation document can satisfy wins the race often enough to be observed
    within a bounded number of rounds.
    """
    stop = multiprocessing.Event()
    workers = [multiprocessing.Process(target=_spin, args=(stop,), daemon=True)
               for _ in range(min(4, os.cpu_count() or 1))]
    for worker in workers:
        worker.start()
    try:
        yield
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():  # pragma: no cover - only if a worker wedges
                worker.kill()


def _spin(stop) -> None:
    while not stop.is_set():
        pass


def test_a_reload_returns_only_once_the_new_document_is_live(stub, browser) -> None:
    """``reload`` must not report readiness against the document it is replacing.

    The outgoing document satisfies the readiness marker too, so each round stamps the
    live document and then requires the reload to have landed on an unstamped, fully
    parsed one with the dashboard's own elements reachable.
    """
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status()},
    ]))
    browser.open(f"{stub.origin}/")

    with _cpu_contention():
        for round_number in range(NAVIGATIONS):
            browser.evaluate(f"{STALE} = true; return null;")
            browser.reload()
            assert browser.evaluate(LANDED) == DASHBOARD_LIVE, (
                f"reload {round_number} returned before the new document was live")


def test_an_open_returns_only_once_the_requested_document_is_live(stub, browser) -> None:
    """``open`` of the URL already loaded is the case the outgoing document can satisfy."""
    _serve(stub, _routes([
        {"method": "GET", "url": r"^/api/process/status/p1", "body": _status()},
    ]))

    with _cpu_contention():
        for round_number in range(NAVIGATIONS):
            browser.evaluate(f"{STALE} = true; return null;")
            browser.open(f"{stub.origin}/")
            assert browser.evaluate(LANDED) == DASHBOARD_LIVE, (
                f"open {round_number} returned before the dashboard was live")

    # A real cross-page navigation lands on the requested page, not the one left behind.
    browser.open(f"{stub.origin}/review", marker=REVIEW_READY)
    assert browser.evaluate(LANDED) == REVIEW_LIVE
