"""Real-browser regression: what the review page claims must survive being checked.

Two defects in the shipped review UI, both of them a claim the page made from volatile
in-page state rather than from the archive's own answer:

**REV-SUMMARY-01** — ``review.html`` counted ``sessionFiled`` / ``sessionSkipped`` in
page variables and printed them as the batch-complete summary. Nothing reconciled them:
Undo never took an outcome back off the tally, and a reload or a server restart reset
both to zero. One document skipped, undone and then filed therefore read
*1 filed · 1 skipped* — two outcomes for one document — while the archive held one filed
and none skipped, and the same screen after a reload read *0 filed · 0 skipped*.

**REV-BADGE-02** — ``updateReviewBadge()`` in ``shared.js`` was called once, from
``initPage()``. The Review Queue badge in the sidebar is visible from the review page
itself, so filing, skipping or undoing left it showing a count the server had already
moved past — including a badge still claiming work when nothing at all was pending.

These tests drive the shipped pages in a real headless Chromium through the real click
handlers, against routed synthetic JSON on the loopback stub. Nothing in the page is
mocked: the DOM, the event loop, ``fetch`` and every handler are the browser's own, and
the markup is the file that ships. Every wait is on authoritative state — the queue the
server answered with, or the badge the page wrote from it — never on a snackbar
disappearing, which happens before the mutation it describes has settled.

Covered: Skip→Undo→File and File→Undo→Skip in both queue scopes; terminal File and
terminal Skip; Undo; a page reload; a genuine server restart under an open page; and the
visible badge against the global pending count the API reports.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.review_actions.browser_harness import Browser, StubServer, browser_binary

SCOPE = "scope-review-reconciliation-synthetic"
BATCH = "job-rev"
OTHER_BATCH = "job-other"
ITEM_A = "item-a"
ITEM_B = "item-b"
FILENAME = "SynthbankStatementAugust2026.pdf"
DIRECTORY = "Household/Utilities"

REVIEW_READY = "typeof loadQueue === 'function'"
BATCH_QUERY = rf"^/api/v1/review-items\?(?=.*job_id={BATCH})"
GLOBAL_QUERY = r"^/api/v1/review-items\?(?!.*job_id=)"

# What the scope switch calls each queue, and how the deep link selects it.
SCOPES = {"batch": ("This batch", f"?job_id={BATCH}"),
          "all": ("All documents", "")}
# Which button ends the queue, which endpoint it posts to, and what the archive holds
# afterwards. The tally is what a truthful summary may claim; anything else is invented.
ACTIONS = {
    "skip": {"button": "btn-skip", "url": f"^/api/v1/review-items/{ITEM_A}/skip$",
             "kind": "review_skip", "persisted": {"filed": 0, "skipped": 1}},
    "approve": {"button": "btn-approve", "url": f"^/api/v1/review-items/{ITEM_A}/approve$",
                "kind": "review_approve", "persisted": {"filed": 1, "skipped": 0}},
}
SEQUENCES = {"skip-undo-file": ("skip", "approve"),
             "file-undo-skip": ("approve", "skip")}


# ---------------------------------------------------------------------------
# Synthetic page data
# ---------------------------------------------------------------------------

def _item(item_id: str = ITEM_A, job_id: str = BATCH) -> dict:
    return {
        "id": item_id, "job_id": job_id, "status": "pending",
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
         "body": {"review_item_id": ITEM_A, "job_id": BATCH, "page_number": 3,
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
        {"method": "POST", "url": ACTIONS["skip"]["url"],
         "body": {"operation": _operation("op-1", "review_skip"),
                  "outcome": "completed", "items": [], "jobs": []}},
        {"method": "POST", "url": ACTIONS["approve"]["url"],
         "body": {"operation": _operation("op-1", "review_approve"),
                  "outcome": "completed", "items": [], "jobs": []}},
        {"method": "POST", "url": r"^/api/v1/operations/op-1/undo$",
         "body": {"undo_operation": _operation("undo-1", "review_undo"),
                  "operation": _operation("op-1", "review_skip"),
                  "outcome": "undone", "steps": [], "jobs": []}},
    ]


def serve(stub: StubServer, batch_items: list[dict], global_items: list[dict]) -> None:
    """Make every later listing call answer with this pending set, as a server would."""
    stub.respond({"method": "GET", "url": BATCH_QUERY,
                  "body": _listing(batch_items, BATCH)})
    stub.respond({"method": "GET", "url": GLOBAL_QUERY,
                  "body": _listing(global_items, None)})


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
# terminal summary, the queue and the sidebar badge are observed in the same state.
SNAPSHOT = """
    const at = (id) => document.getElementById(id);
    const badge = at('nav-review-badge');
    return {
        emptyShown: !at('empty-state').classList.contains('hidden'),
        workspaceShown: !at('workspace').classList.contains('hidden'),
        emptyTitle: at('empty-title').textContent.trim(),
        emptySub: at('empty-sub').textContent.trim(),
        progressText: at('review-progress-text').textContent.trim(),
        queueCount: at('queue-count').textContent.trim(),
        queueList: at('queue-list').textContent.trim(),
        scopeLabel: at('queue-scope-label').textContent.trim(),
        badgePresent: !!badge,
        badgeShown: !!badge && !badge.classList.contains('hidden'),
        badgeText: badge ? badge.textContent.trim() : null,
    };
"""

TALLY = {"filed": re.compile(r"(\d+)\s+filed"), "skipped": re.compile(r"(\d+)\s+skipped")}


def snapshot(browser: Browser) -> dict:
    return browser.evaluate(SNAPSHOT)


def claimed_outcomes(summary: str) -> dict:
    """The outcomes the terminal summary claims. A summary that claims none says {}."""
    found = {}
    for kind, pattern in TALLY.items():
        match = pattern.search(summary)
        if match:
            found[kind] = int(match.group(1))
    return found


def badge_count(state: dict) -> int:
    """What the sidebar badge tells the reviewer is pending, hidden meaning none."""
    assert state["badgePresent"], "the Review Queue badge is not on the page at all"
    if not state["badgeShown"]:
        return 0
    text = state["badgeText"]
    assert re.fullmatch(r"\d+", text or ""), f"the badge shows something uncountable: {text!r}"
    return int(text)


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


def act(browser: Browser, stub: StubServer, action: str, batch_items: list[dict],
        global_items: list[dict], expect_empty: bool = True) -> dict:
    """Click the real File/Skip button, then read the settled queue it left behind.

    The wait is on the queue the server answered with — the empty pane arriving, or the
    rail re-rendering — not on the success snackbar, which is raised before the reload
    of the queue that follows it has finished.
    """
    serve(stub, batch_items, global_items)
    click(browser, ACTIONS[action]["button"])
    pane = "empty-state" if expect_empty else "workspace"
    browser.wait_for(f"return !document.getElementById('{pane}')"
                     ".classList.contains('hidden');",
                     what="the queue to settle on the server's answer")
    return snapshot(browser)


def undo(browser: Browser, stub: StubServer,
         batch_items: list[dict], global_items: list[dict]) -> dict:
    """Click Undo on the real snackbar, then read the restored queue."""
    serve(stub, batch_items, global_items)
    browser.wait_for("return !!document.querySelector('#undo-snackbar button');",
                     what="the Undo control the action offered")
    browser.evaluate("document.querySelector('#undo-snackbar button').click(); return null;")
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');",
                     what="the undone document to return to the queue")
    return snapshot(browser)


def reopen(browser: Browser, stub: StubServer, scope: str, expect_empty: bool) -> dict:
    """Reload the page and wait for its queue to be rebuilt from the server."""
    browser.reload(marker=REVIEW_READY)
    pane = "empty-state" if expect_empty else "workspace"
    browser.wait_for(f"return !document.getElementById('{pane}')"
                     ".classList.contains('hidden');",
                     what="the reloaded queue to render the server's answer")
    return snapshot(browser)


def run_sequence(browser: Browser, stub: StubServer, scope: str, sequence: str) -> dict:
    """Act, undo that action, act the other way; return the terminal state that leaves."""
    first, second = SEQUENCES[sequence]
    open_queue(browser, stub, scope)
    act(browser, stub, first, [], [])
    undo(browser, stub, [_item()], [_item()])
    return act(browser, stub, second, [], [])


# ---------------------------------------------------------------------------
# REV-SUMMARY-01 — the terminal summary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sequence", list(SEQUENCES), ids=list(SEQUENCES))
@pytest.mark.parametrize("scope", ["batch", "all"], ids=["this-batch", "all-documents"])
def test_the_terminal_summary_claims_only_outcomes_the_archive_confirms(
        stub, browser, scope, sequence) -> None:
    label = SCOPES[scope][0]
    state = run_sequence(browser, stub, scope, sequence)

    assert state["emptyShown"] and not state["workspaceShown"], state
    assert state["emptyTitle"] in {"All caught up", "Batch complete"}, state

    # The summary says which queue it is describing: an empty batch and an empty
    # archive are different facts, and the reviewer can only tell them apart if the
    # terminal state names its scope the way the rail and the progress line do.
    assert label.lower() in state["emptySub"].lower(), (
        f"the terminal summary does not say which queue it describes ({label}): "
        f"{state['emptySub']!r}")

    # One document existed and it ended in exactly one outcome. Anything the summary
    # counts has to be that outcome — volatile in-page tallies survive Undo and reset
    # on reload, so they cannot be the source of a claim like this.
    persisted = ACTIONS[SEQUENCES[sequence][1]]["persisted"]
    claims = claimed_outcomes(state["emptySub"])
    for kind, claimed in claims.items():
        assert claimed == persisted[kind], (
            f"{label} after {sequence}: the archive holds {persisted[kind]} {kind} "
            f"but the summary claims {claimed}: {state['emptySub']!r}")


@pytest.mark.parametrize("sequence", list(SEQUENCES), ids=list(SEQUENCES))
@pytest.mark.parametrize("scope", ["batch", "all"], ids=["this-batch", "all-documents"])
def test_the_terminal_summary_reads_the_same_after_a_reload(
        stub, browser, scope, sequence) -> None:
    """Nothing changed on the server, so nothing on this screen may change either."""
    before = run_sequence(browser, stub, scope, sequence)
    after = reopen(browser, stub, scope, expect_empty=True)

    assert after["emptyTitle"] == before["emptyTitle"], (before, after)
    assert after["emptySub"] == before["emptySub"], (
        f"the same server state reads differently after a reload: "
        f"{before['emptySub']!r} became {after['emptySub']!r}")


def test_the_terminal_summary_reads_the_same_after_a_server_restart(
        stub, browser, tmp_path) -> None:
    """The page outlives the server here: only what the server can answer may show."""
    before = run_sequence(browser, stub, "batch", "skip-undo-file")

    port, routes = stub.port, _routes([], [])
    stub.close()
    restarted = StubServer(routes, port=port)
    try:
        after = reopen(browser, restarted, "batch", expect_empty=True)
        assert after["emptyTitle"] == before["emptyTitle"], (before, after)
        assert after["emptySub"] == before["emptySub"], (
            f"a restarted server serves a different summary for the same state: "
            f"{before['emptySub']!r} became {after['emptySub']!r}")
        assert badge_count(after) == 0, after
    finally:
        restarted.close()


# ---------------------------------------------------------------------------
# REV-BADGE-02 — the sidebar badge
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["skip", "approve"], ids=["skip", "file"])
def test_reviewing_the_last_document_clears_the_sidebar_badge(
        stub, browser, action) -> None:
    open_queue(browser, stub, "all")
    browser.wait_for("return document.getElementById('nav-review-badge')"
                     ".textContent.trim() === '1';",
                     what="the badge to show the one pending document")

    state = act(browser, stub, action, [], [])

    assert state["emptyShown"], state
    assert badge_count(state) == 0, (
        f"nothing is pending but the sidebar badge still says {state['badgeText']!r}")


def test_the_badge_counts_every_pending_document_not_only_the_scoped_queue(
        stub, browser) -> None:
    """A finished batch is not a finished archive: the badge counts the whole archive."""
    serve(stub, [_item()], [_item(), _item(ITEM_B, OTHER_BATCH)])
    open_queue(browser, stub, "batch")
    browser.wait_for("return document.getElementById('nav-review-badge')"
                     ".textContent.trim() === '2';",
                     what="the badge to show both pending documents")

    state = act(browser, stub, "skip", [], [_item(ITEM_B, OTHER_BATCH)])

    assert state["emptyTitle"] == "Batch complete", state
    assert badge_count(state) == 1, (
        f"this batch is finished but one document is still pending elsewhere; the "
        f"badge says {state['badgeText']!r}")


def test_undo_puts_the_document_back_on_the_sidebar_badge(stub, browser) -> None:
    """The badge is reloaded here, so only Undo can be what moves it afterwards."""
    open_queue(browser, stub, "all")
    act(browser, stub, "approve", [], [])

    restored = reopen(browser, stub, "all", expect_empty=True)
    assert badge_count(restored) == 0, restored

    state = undo(browser, stub, [_item()], [_item()])

    assert state["workspaceShown"], state
    assert FILENAME in state["queueList"], state
    assert badge_count(state) == 1, (
        f"the undone document is back in the queue but the badge says "
        f"{state['badgeText']!r}")
    assert len(stub.calls("POST", "/api/v1/operations/op-1/undo")) == 1, stub.requests
