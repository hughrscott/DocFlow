"""Real-browser geometry: the shipped pages must reflow onto a phone, not zoom out.

VIS-B01. Every other browser test in this directory runs with the stylesheet denied,
which is fine when the assertion is about a class the page itself toggles. Layout is
different: ``min-w-[1060px]`` is an arbitrary Tailwind utility, so with the stylesheet
denied it has no effect and the defect cannot even be observed. These tests therefore
let the browser reach the two public asset hosts ``dashboard.html`` and friends already
reference, and assert on computed geometry once the real stylesheet has compiled. No
CSS is injected or substituted: what is measured is what ships.

The mobile profile is a real device emulation (390x844, ``mobile=true``, DPR 2), not a
narrow window. That distinction is the whole defect: with ``mobile=true`` a page that
cannot fit is zoomed out into a wider layout viewport, so before the fix the review
page reported ``innerWidth`` 1060 against a 390px screen.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.review_actions.browser_harness import (
    ASSET_HOSTS,
    STYLES_READY,
    Browser,
    StubServer,
    browser_binary,
)

SCOPE = "scope-responsive-synthetic"
BATCH = "job-responsive"

# (width, height, mobile, deviceScaleFactor)
MOBILE = (390, 844, True, 2)
DESKTOP = (1440, 1000, False, 1)

READY = {"/": "typeof handleFiles === 'function'",
         "/review": "typeof loadQueue === 'function'",
         "/archive": "typeof loadLogs === 'function'",
         "/settings": "typeof loadSettings === 'function'"}


# ---------------------------------------------------------------------------
# Session guards
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def assets_reachable() -> None:
    """Skip rather than lie: without the real stylesheet these assertions are vacuous."""
    for host in ("cdn.tailwindcss.com", "fonts.googleapis.com"):
        try:
            socket.create_connection((host, 443), timeout=8).close()
        except OSError as exc:
            pytest.skip(f"the shipped stylesheet host {host} is unreachable: {exc}")


# ---------------------------------------------------------------------------
# Synthetic page data
# ---------------------------------------------------------------------------

def _item(item_id: str = "item-1", **overrides) -> dict:
    return {
        "id": item_id, "job_id": BATCH, "status": "pending",
        "source_name": "synthetic-scan.pdf", "page_numbers": [3, 4],
        "source_page_range": "3-4", "text_extraction": "extracted",
        "reason": "unmatched", "near_duplicate_of": None, "doc_type": "statement",
        "period": "2026-08", "suggested_filename": "SynthbankStatementAugust2026.pdf",
        "suggested_relative_directory": "Household/Utilities", "confidence": 0.42,
        "actions": ["correct", "skip"], "created_at": "2026-09-16T00:00:00+00:00",
        "updated_at": "2026-09-16T00:00:00+00:00", **overrides,
    }


def _routes() -> list[dict]:
    items = [_item(), _item("item-2", page_numbers=[5], source_page_range="5",
                            suggested_filename=None, confidence=None)]
    return [
        {"method": "GET", "url": r"^/api/v1/archive-scopes/active",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": r"^/api/v1/review-items",
         "body": {"archive_scope_id": SCOPE, "status": "pending", "job_id": None,
                  "items": items}},
        {"method": "GET", "url": r"^/api/v1/review-items/[^/]+/pages/\d+/text",
         "body": {"review_item_id": "item-1", "job_id": BATCH, "page_number": 3,
                  "status": "extracted", "text": "SYNTHETIC PAGE TEXT",
                  "error_code": None}},
        {"method": "GET", "url": r"^/api/unmatched", "body": {"files": [], "count": 0}},
        {"method": "GET", "url": r"^/api/process/active", "body": {"job_id": None}},
        {"method": "GET", "url": r"^/api/archive/logs", "body": {"logs": [
            {"filename": "SynthbankStatementAugust2026.pdf",
             "target_directory": "/synthetic/archive/Household/Utilities",
             "rule_matched": "synthetic_rule", "confidence": 0.93,
             "timestamp": "2026-09-16T00:00:00"}]}},
        {"method": "GET", "url": r"^/api/archive/tree", "body": {"tree": [
            {"name": "Household", "path": "Household", "children": [
                {"name": "Utilities", "path": "Household/Utilities", "children": []}]}]}},
        {"method": "GET", "url": r"^/api/health", "body": {
            "watch_folder": "/synthetic/inbox", "archive_root": "/synthetic/archive",
            "privacy_mode": "local_only", "text_extraction": True,
            "ai_classification": False, "llm_ready": False, "llm_status": "local_only",
            "llm_status_label": "Local Only", "llm_status_level": "neutral",
            "llm_status_detail": "OCR runs locally. AI classification is off.",
            "model": "local", "threshold": 0.75}},
        {"method": "GET", "url": r"^/api/settings/rules", "body": {"rules": [
            {"id": "synthetic_rule", "file_to": "Household/Utilities",
             "filename_template": "SynthbankStatement{period}.pdf"}]}},
        {"method": "GET", "url": r"^/api/settings/entities", "body": {
            "entities": [{"id": "synthetic_entity", "name": "Synthetic Entity",
                          "type": "business"}],
            "family": [{"name": "Synthetic Person", "relation": "spouse"}]}},
        {"method": "GET", "url": r"^/api/settings/family", "body": {"family": []}},
        {"method": "GET", "url": r"^/api/settings", "body": {
            "archive_root": "/synthetic/archive",
            "scan_watch_folder": "/synthetic/inbox", "confidence_threshold": 0.75,
            "llm_provider": "none", "llm_model": "local", "llm_base_url": "",
            "llm_api_key": ""}},
    ]


@pytest.fixture()
def stub():
    server = StubServer(_routes())
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def browser(tmp_path: Path, stub, assets_reachable):
    binary = browser_binary()
    profile = tmp_path / "browser-profile"
    profile.mkdir()
    session = Browser(binary, profile, f"{stub.origin}/", allow_hosts=ASSET_HOSTS)
    try:
        yield session
    finally:
        session.close()


def visit(browser: Browser, stub: StubServer, path: str, profile) -> None:
    """Adopt the device profile, load the page, and wait for the real stylesheet."""
    browser.emulate(*profile)
    browser.open(f"{stub.origin}{path}", marker=READY[path])
    browser.wait_for(STYLES_READY, timeout=25,
                     what="the shipped Tailwind stylesheet to compile")


# ---------------------------------------------------------------------------
# Geometry probes, evaluated in the page
# ---------------------------------------------------------------------------

# Anything sticking out past the layout viewport, ignoring what is legitimately inside
# a horizontal scroller that itself fits, and ignoring what is not rendered at all.
OVERFLOW = """
    const de = document.documentElement;
    const limit = de.clientWidth;
    const name = (el) => el.tagName.toLowerCase()
        + (el.id ? '#' + el.id : '')
        + '.' + (el.getAttribute('class') || '').slice(0, 70);
    // Only a container that *asks* for a sideways scroller excuses its children. A
    // computed ``overflow-x`` of auto is not enough: ``overflow-y: auto`` alone forces
    // it, so .df-body would otherwise excuse the whole page for scrolling sideways —
    // which is the very thing this defect is about.
    const classes = (el) => (el.getAttribute('class') || '').split(/\\s+/);
    const scrollable = (el) => {
        for (let p = el.parentElement; p; p = p.parentElement) {
            if (classes(p).some((c) => c === 'overflow-x-auto' || c === 'overflow-x-scroll')
                && p.getBoundingClientRect().right <= limit + 1) return true;
        }
        return false;
    };
    const wide = [], clipped = [];
    for (const el of document.querySelectorAll('body *')) {
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        // Content cut off inside its own box by a horizontal clip. An ellipsis is a
        // deliberate affordance for a long filename, so it is not a clipping defect.
        const ox = style.overflowX;
        // A text field scrolls its own value by design, and an ellipsis is a
        // deliberate affordance for a long filename; neither is a layout defect.
        const formControl = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
        if ((ox === 'hidden' || ox === 'clip') && el.scrollWidth > el.clientWidth + 1
            && el.clientWidth > 0 && style.textOverflow !== 'ellipsis'
            && !formControl) {
            clipped.push([name(el), el.scrollWidth, el.clientWidth]);
        }
        if (scrollable(el)) continue;
        if (r.right > limit + 1 || r.left < -1) {
            wide.push([name(el), Math.round(r.left), Math.round(r.right)]);
        }
    }
    return {innerWidth: window.innerWidth, clientWidth: de.clientWidth,
            scrollWidth: de.scrollWidth, dpr: window.devicePixelRatio,
            wide: wide.slice(0, 8), clipped: clipped.slice(0, 8)};
"""


def reach(ids: list[str]) -> str:
    """Scroll each control into view and report whether a finger could actually hit it."""
    return f"""
    const de = document.documentElement;
    const out = {{}};
    for (const id of {list(ids)}) {{
        const el = document.getElementById(id);
        if (!el) {{ out[id] = {{found: false}}; continue; }}
        el.scrollIntoView({{block: 'center', inline: 'nearest'}});
        const r = el.getBoundingClientRect();
        // A zero box means an ancestor is hidden, which the element's own
        // computed style does not reveal.
        if (r.width === 0 && r.height === 0) {{
            out[id] = {{found: true, rendered: false}};
            continue;
        }}
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const hit = document.elementFromPoint(cx, cy);
        out[id] = {{found: true, rendered: true, width: Math.round(r.width),
                    height: Math.round(r.height),
                    inside: r.left >= -1 && r.right <= de.clientWidth + 1,
                    hittable: !!hit && (el.contains(hit) || hit.contains(el)),
                    coveredBy: !hit ? null : hit.tagName.toLowerCase()
                        + (hit.id ? '#' + hit.id : '')
                        + '.' + (hit.getAttribute('class') || '').slice(0, 50)}};
    }}
    return out;
    """


BOX = """
    const el = document.getElementById(%s);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: Math.round(r.left), right: Math.round(r.right),
            top: Math.round(r.top), width: Math.round(r.width),
            height: Math.round(r.height)};
"""


def box(browser: Browser, element_id: str):
    return browser.evaluate(BOX % repr(element_id))


def rail(browser: Browser):
    """The queue rail itself — the card around the scrolling list, not the list."""
    return browser.evaluate("""
        const el = document.getElementById('queue-list').parentElement;
        const r = el.getBoundingClientRect();
        return {left: Math.round(r.left), right: Math.round(r.right),
                top: Math.round(r.top), width: Math.round(r.width),
                height: Math.round(r.height)};
    """)


def assert_fits(report: dict, where: str) -> None:
    assert report["wide"] == [], f"{where}: content overflows the viewport: {report}"
    assert report["clipped"] == [], f"{where}: content is clipped horizontally: {report}"
    assert report["scrollWidth"] <= report["clientWidth"] + 1, (
        f"{where}: the document scrolls horizontally: {report}")


# ---------------------------------------------------------------------------
# The stylesheet really is the shipped one
# ---------------------------------------------------------------------------

def test_the_pages_are_measured_with_their_own_shipped_stylesheet(stub, browser) -> None:
    """A guard on every other test here: no real Tailwind means no real geometry."""
    visit(browser, stub, "/review", DESKTOP)
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');", what="the review workspace")
    # bg-canvas resolves only through the shipped config.js token table.
    assert browser.evaluate(
        "return getComputedStyle(document.body).backgroundColor;") == "rgb(244, 241, 234)"
    # An arbitrary-value utility compiled by the CDN's JIT, which is what the defect is.
    assert browser.evaluate(
        "return getComputedStyle(document.getElementById('decision-panel')).width;"
    ) == "362px"


# ---------------------------------------------------------------------------
# Mobile: every page fits the real 390px viewport
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/review", "/archive", "/settings"])
def test_a_page_fits_the_phone_viewport_it_is_shown_on(stub, browser, path) -> None:
    visit(browser, stub, path, MOBILE)
    report = browser.evaluate(OVERFLOW)

    # The layout viewport is the screen. Before the fix the review page answered 1060
    # here: the desktop layout kept its width and the whole page was zoomed out.
    assert report["innerWidth"] == 390, (
        f"{path}: the page was zoomed out to a {report['innerWidth']}px "
        f"layout viewport instead of laid out for the 390px screen: {report}")
    assert report["clientWidth"] == 390, report
    assert report["dpr"] == 2, report
    assert_fits(report, path)


def test_the_review_workspace_stacks_into_readable_panels_on_a_phone(stub, browser) -> None:
    visit(browser, stub, "/review", MOBILE)
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');", what="the review workspace")

    panel = box(browser, "decision-panel")
    queue = rail(browser)
    assert panel and queue

    # Reflowed, not shrunk into a column of slivers: each panel gets the screen's width.
    for label, measured in (("decision panel", panel), ("queue", queue)):
        assert measured["width"] >= 300, (
            f"the {label} is {measured['width']}px wide on a 390px screen: {measured}")
        assert measured["right"] <= 391, f"the {label} runs off-screen: {measured}"

    # Stacked rather than side by side: the decision panel is below the queue.
    assert panel["top"] > queue["top"], (
        f"the panels are still side by side on a phone: queue={queue} panel={panel}")


def test_the_review_controls_are_reachable_on_a_phone(stub, browser) -> None:
    """Validation, File, Skip and Undo must all be usable, not merely present."""
    visit(browser, stub, "/review", MOBILE)
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');", what="the review workspace")

    # Drive the shipped validation path so the error element really has content.
    browser.evaluate("""
        const dir = document.getElementById('edit-directory');
        dir.value = '../escape';
        updateDestination();
        return null;
    """)
    assert browser.evaluate(
        "return document.getElementById('destination-error').textContent.trim();"), (
        "the destination validation message never rendered")

    # Controls the reviewer has to hit with a thumb, and the message they have to read.
    tappable = ["edit-directory", "edit-filename", "btn-approve", "btn-skip"]
    readable = ["destination-error"]
    found = browser.evaluate(reach(tappable + readable))

    for name in tappable + readable:
        state = found[name]
        assert state["found"], f"{name} is missing from the phone layout"
        assert state["rendered"], f"{name} is not rendered on the phone: {state}"
        assert state["inside"], f"{name} is off the side of the screen: {state}"
        assert state["hittable"], f"{name} is covered by something else: {state}"

    for name in tappable:
        state = found[name]
        # Not a WCAG claim: just big enough to be a deliberate target rather than a
        # sliver, which is what a squeezed desktop panel degrades into.
        assert state["width"] >= 44 and state["height"] >= 28, (
            f"{name} is too small to tap on a phone: {state}")


def test_the_undo_snackbar_is_usable_on_a_phone(stub, browser) -> None:
    """Undo floats over the page, so it gets its own check: on-screen and tappable.

    It is deliberately not asserted to miss the decision panel — a snackbar is a
    transient overlay and is supposed to sit above the content it refers to.
    """
    visit(browser, stub, "/review", MOBILE)
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');", what="the review workspace")

    # The real control, created by the shipped snackbar helper with a real label.
    browser.evaluate("""
        showUndoSnackbar('Filed SynthbankStatementAugust2026.pdf to '
                         + 'Household/Utilities', () => {}, {persistent: true});
        return null;
    """)

    # The entry animation moves the bar, so settle it before measuring anything.
    browser.wait_for("return document.getElementById('undo-snackbar')"
                     ".getAnimations().every((a) => a.playState === 'finished');",
                     what="the snackbar entry animation to finish")

    state = browser.evaluate(reach(["undo-snackbar"]))["undo-snackbar"]
    assert state["found"] and state["rendered"], "the Undo snackbar never rendered"
    assert state["inside"], f"the Undo snackbar runs off the screen: {state}"
    assert state["hittable"], f"the Undo snackbar is not tappable: {state}"

    # It clears the fixed bottom tab bar rather than hiding underneath it.
    bar = box(browser, "mobile-tabs")
    snack = box(browser, "undo-snackbar")
    assert bar and snack
    assert snack["top"] + snack["height"] <= bar["top"] + 1, (
        f"the Undo snackbar collides with the mobile tab bar: snack={snack} bar={bar}")

    # And it does not drag the page sideways.
    assert_fits(browser.evaluate(OVERFLOW), "review with the Undo snackbar shown")


def test_the_dashboard_intake_and_metrics_reflow_on_a_phone(stub, browser) -> None:
    visit(browser, stub, "/", MOBILE)
    browser.evaluate("""
        document.getElementById('review-callout').classList.remove('hidden');
        return null;
    """)
    report = browser.evaluate(OVERFLOW)
    assert_fits(report, "dashboard with the review callout shown")

    controls = ["drop-zone", "review-callout", "stats-grid"]
    found = browser.evaluate(reach(controls))
    for name in controls:
        assert found[name]["found"] and found[name]["rendered"], (
            f"{name} is not rendered on the phone: {found[name]}")
        assert found[name]["inside"], (
            f"{name} does not fit the phone: {found[name]}")

    # The four metric tiles wrap instead of being squeezed into unreadable slivers.
    tiles = browser.evaluate(
        "return Array.from(document.getElementById('stats-grid').children)"
        ".map((t) => Math.round(t.getBoundingClientRect().width));")
    assert len(tiles) == 4, tiles
    assert min(tiles) >= 140, f"the metric tiles are too narrow to read: {tiles}"


# ---------------------------------------------------------------------------
# Desktop: the three-pane hierarchy is unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/review", "/archive", "/settings"])
def test_a_page_still_fits_the_desktop_viewport(stub, browser, path) -> None:
    visit(browser, stub, path, DESKTOP)
    report = browser.evaluate(OVERFLOW)
    assert report["innerWidth"] == 1440, report
    assert_fits(report, f"{path} at desktop width")


def test_the_desktop_review_layout_keeps_its_three_panes_side_by_side(stub, browser) -> None:
    visit(browser, stub, "/review", DESKTOP)
    browser.wait_for("return !document.getElementById('workspace')"
                     ".classList.contains('hidden');", what="the review workspace")

    queue = rail(browser)
    panel = box(browser, "decision-panel")
    assert queue and panel
    # Same row, decision panel on the right: the desktop hierarchy is preserved.
    assert abs(queue["top"] - panel["top"]) <= 24, (
        f"the desktop panes are no longer on one row: queue={queue} panel={panel}")
    assert panel["left"] > queue["right"], (
        f"the decision panel is no longer to the right of the queue: "
        f"queue={queue} panel={panel}")
    assert panel["width"] == 362, f"the desktop decision panel changed width: {panel}"
