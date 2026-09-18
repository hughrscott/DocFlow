"""Real-browser regression: the terminal review state may not contradict itself.

VIS-B02. ``renderView()`` in the shipped ``review.html`` returned as soon as the queue
was empty, before the progress bar and its sentence were recomputed. Emptying the queue
with the last Skip or the last File therefore left the empty pane saying *All caught up /
No documents are waiting for review* while the header above it still said
*1 left in All documents · 0 of 1 reviewed* — a count that had just become false.

These tests drive the shipped page in a real headless Chromium through the real click
handlers, against routed synthetic API responses on the loopback stub. Nothing is mocked
in the page: the DOM, the event loop, ``fetch`` and every handler are the browser's own
and the markup is the file that ships. As everywhere else in this directory the browser
is sealed off from the network, so visibility is asserted on the ``hidden`` class the
page itself toggles; the contradiction this pins is textual and numeric state written by
the page's own JavaScript, so it is fully observable without the CDN stylesheet.

Covered for both queue scopes (*This batch* and *All documents*):

* the last Skip and the last File each leave a terminal state whose progress agrees with
  the empty pane rather than still claiming work is left;
* Undo puts the document back and restores the correct nonempty progress;
* a queue that was empty to begin with still says nothing, rather than gaining a
  progress sentence it has no session to describe.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.review_actions.browser_harness import Browser, StubServer, browser_binary

SCOPE = "scope-terminal-progress-synthetic"
BATCH = "job-b02"
ITEM = "item-1"
FILENAME = "SynthbankStatementAugust2026.pdf"
DIRECTORY = "Household/Utilities"

REVIEW_READY = "typeof loadQueue === 'function'"
BATCH_QUERY = r"^/api/v1/review-items\?(?=.*job_id=job-b02)"
GLOBAL_QUERY = r"^/api/v1/review-items\?(?!.*job_id=)"

# What the scope switch calls each queue, and how the deep link selects it.
SCOPES = {"batch": ("This batch", f"?job_id={BATCH}"),
          "all": ("All documents", "")}
# Which button ends the queue, and which endpoint that button posts to.
ACTIONS = {"skip": ("btn-skip", f"^/api/v1/review-items/{ITEM}/skip$", "review_skip"),
           "approve": ("btn-approve", f"^/api/v1/review-items/{ITEM}/approve$",
                       "review_approve")}


# ---------------------------------------------------------------------------
# Synthetic page data
# ---------------------------------------------------------------------------

def _item(item_id: str = ITEM) -> dict:
    return {
        "id": item_id, "job_id": BATCH, "status": "pending",
        "source_name": "synthetic-scan.pdf", "page_numbers": [3, 4],
        "source_page_range": "3-4", "text_extraction": "extracted",
        "reason": "unmatched", "near_duplicate_of": None, "doc_type": "statement",
        "period": "2026-08", "suggested_filename": FILENAME,
        "suggested_relative_directory": DIRECTORY, "confidence": 0.42,
        "actions": ["correct", "skip"], "created_at": "2026-09-16T00:00:00+00:00",
        "updated_at": "2026-09-16T00:00:00+00:00",
    }


def _operation(operation_id: str, kind: str) -> dict:
    return {"id": operation_id, "kind": kind, "status": "completed",
            "created_at": "2026-09-16T00:00:00+00:00",
            "completed_at": "2026-09-16T00:00:00+00:00", "undone_at": None}


def _listing(items: list[dict], job_id: str | None) -> dict:
    return {"archive_scope_id": SCOPE, "status": "pending", "job_id": job_id,
            "items": items}


def _routes(batch_items: list[dict], global_items: list[dict]) -> list[dict]:
    return [
        {"method": "GET", "url": r"^/api/v1/archive-scopes/active",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": BATCH_QUERY, "body": _listing(batch_items, BATCH)},
        {"method": "GET", "url": GLOBAL_QUERY, "body": _listing(global_items, None)},
        {"method": "GET", "url": r"^/api/v1/review-items/[^/]+/pages/\d+/text",
         "body": {"review_item_id": ITEM, "job_id": BATCH, "page_number": 3,
                  "status": "extracted", "text": "SYNTHETIC PAGE TEXT",
                  "error_code": None}},
        {"method": "GET", "url": r"^/api/unmatched", "body": {"files": [], "count": 0}},
        {"method": "GET", "url": r"^/api/health", "body": {
            "watch_folder": "/synthetic/inbox", "archive_root": "/synthetic/archive",
            "privacy_mode": "local_only", "text_extraction": True,
            "ai_classification": False, "llm_ready": False, "llm_status": "local_only",
            "llm_status_label": "Local Only", "llm_status_level": "neutral",
            "llm_status_detail": "OCR runs locally. AI classification is off.",
            "model": "local", "threshold": 0.75}},
        # Both terminal actions and the compensation for either of them.
        {"method": "POST", "url": ACTIONS["skip"][1],
         "body": {"operation": _operation("op-1", "review_skip"),
                  "outcome": "completed", "items": [], "jobs": []}},
        {"method": "POST", "url": ACTIONS["approve"][1],
         "body": {"operation": _operation("op-1", "review_approve"),
                  "outcome": "completed", "items": [], "jobs": []}},
        {"method": "POST", "url": r"^/api/v1/operations/op-1/undo$",
         "body": {"undo_operation": _operation("undo-1", "review_undo"),
                  "operation": _operation("op-1", "review_skip"),
                  "outcome": "undone", "steps": [], "jobs": []}},
    ]


@pytest.fixture()
def stub():
    server = StubServer(_routes([_item()], [_item()]))
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
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Probes, evaluated in the page
# ---------------------------------------------------------------------------

# Everything the reviewer can read about how much is left, captured in one pass so the
# empty pane and the progress header are observed in the same rendered state.
SNAPSHOT = """
    const at = (id) => document.getElementById(id);
    return {
        emptyShown: !at('empty-state').classList.contains('hidden'),
        workspaceShown: !at('workspace').classList.contains('hidden'),
        emptyTitle: at('empty-title').textContent.trim(),
        emptySub: at('empty-sub').textContent.trim(),
        progressShown: !at('progress-area').classList.contains('hidden'),
        progressText: at('review-progress-text').textContent.trim(),
        progressWidth: at('review-progress').style.width,
        queueCount: at('queue-count').textContent.trim(),
        queueList: at('queue-list').textContent.trim(),
        scopeLabel: at('queue-scope-label').textContent.trim(),
    };
"""

LEFT = re.compile(r"^(\d+) left in (.+?) · (\d+) of (\d+) reviewed$")


def snapshot(browser: Browser) -> dict:
    return browser.evaluate(SNAPSHOT)


def remaining_claimed(state: dict) -> int:
    """How many documents the progress sentence still says are waiting."""
    text = state["progressText"]
    if not text:
        return 0
    match = LEFT.match(text)
    assert match, f"the progress sentence is not in its documented shape: {text!r}"
    return int(match.group(1))


def open_queue(browser: Browser, stub: StubServer, scope: str) -> None:
    browser.open(f"{stub.origin}/review{SCOPES[scope][1]}", marker=REVIEW_READY)
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');",
                     what="the review workspace to render the queued document")


def click(browser: Browser, element_id: str) -> None:
    browser.evaluate(f"""
        const el = document.getElementById({element_id!r});
        if (!el) throw new Error({element_id!r} + ' is not on the page');
        if (el.disabled) throw new Error({element_id!r} + ' is disabled');
        el.click();
        return null;
    """)


def empty_the_queue(browser: Browser, stub: StubServer, action: str) -> dict:
    """Click the real File/Skip button on the last document and read what is left."""
    stub.respond({"method": "GET", "url": BATCH_QUERY, "body": _listing([], BATCH)})
    stub.respond({"method": "GET", "url": GLOBAL_QUERY, "body": _listing([], None)})
    click(browser, ACTIONS[action][0])
    browser.wait_for("return !document.getElementById('empty-state')"
                     ".classList.contains('hidden');",
                     what="the queue to reach its empty terminal state")
    return snapshot(browser)


def undo(browser: Browser, stub: StubServer) -> dict:
    """Click Undo on the real snackbar the action raised, and read the restored queue."""
    stub.respond({"method": "GET", "url": BATCH_QUERY, "body": _listing([_item()], BATCH)})
    stub.respond({"method": "GET", "url": GLOBAL_QUERY, "body": _listing([_item()], None)})
    browser.wait_for("return !!document.querySelector('#undo-snackbar button');",
                     what="the Undo control the action offered")
    browser.evaluate("document.querySelector('#undo-snackbar button').click(); return null;")
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');",
                     what="the undone document to return to the queue")
    return snapshot(browser)


# ---------------------------------------------------------------------------
# The terminal state of the queue
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["skip", "approve"], ids=["skip", "file"])
@pytest.mark.parametrize("scope", ["batch", "all"], ids=["this-batch", "all-documents"])
def test_emptying_the_queue_cannot_leave_progress_claiming_work_is_left(
        stub, browser, action, scope) -> None:
    label = SCOPES[scope][0]
    open_queue(browser, stub, scope)

    before = snapshot(browser)
    assert before["scopeLabel"] == label, before
    assert remaining_claimed(before) == 1, (
        f"the queue did not start with one document left in {label}: {before}")

    state = empty_the_queue(browser, stub, action)

    # The pane really is the terminal one, and it is the only thing showing. The queue
    # rail's own count is deliberately not asserted here: it lives inside #workspace,
    # which is hidden in this state, so it is not something the reviewer can read.
    assert state["emptyShown"] and not state["workspaceShown"], state
    assert state["emptyTitle"] in {"All caught up", "Batch complete"}, state

    # VIS-B02: the empty pane and the progress header are the same screen. A sentence
    # that still counts documents as waiting contradicts the pane beside it.
    assert remaining_claimed(state) == 0, (
        f"{label}: the empty pane says {state['emptyTitle']!r} while the progress "
        f"header still says {state['progressText']!r}")
    if state["progressText"]:
        assert LEFT.match(state["progressText"]).group(2) == label, (
            f"the progress sentence names a different scope than the queue: {state}")
        assert state["progressWidth"] == "100%", (
            f"{label}: every document was reviewed but the bar still shows "
            f"{state['progressWidth']!r}: {state}")


@pytest.mark.parametrize("action", ["skip", "approve"], ids=["skip", "file"])
@pytest.mark.parametrize("scope", ["batch", "all"], ids=["this-batch", "all-documents"])
def test_undo_restores_the_document_and_the_progress_that_describes_it(
        stub, browser, action, scope) -> None:
    label = SCOPES[scope][0]
    open_queue(browser, stub, scope)
    empty_the_queue(browser, stub, action)

    state = undo(browser, stub)

    assert state["workspaceShown"] and not state["emptyShown"], state
    assert state["queueCount"] == "1", state
    assert FILENAME in state["queueList"], state
    assert state["progressText"] == f"1 left in {label} · 0 of 1 reviewed", state
    assert state["progressWidth"] == "0%", state
    assert len(stub.calls("POST", "/api/v1/operations/op-1/undo")) == 1, stub.requests


def test_a_queue_that_was_empty_from_the_start_claims_no_session(stub, browser) -> None:
    """The reset must not invent a session: nothing was reviewed, so nothing is said."""
    stub.respond({"method": "GET", "url": GLOBAL_QUERY, "body": _listing([], None)})
    browser.open(f"{stub.origin}/review", marker=REVIEW_READY)
    browser.wait_for("return !document.getElementById('empty-state')"
                     ".classList.contains('hidden');", what="the empty review queue")

    state = snapshot(browser)
    assert state["emptyTitle"] == "All caught up", state
    assert state["progressText"] == "", state
    assert state["progressWidth"] == "0%", state
