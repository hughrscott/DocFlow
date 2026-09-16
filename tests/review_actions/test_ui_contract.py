"""Review UI integration: durable operation IDs, real Undo, and text-only rendering.

Behavioral checks run ``review.html``'s script and ``shared.js`` in Node against a
minimal recording DOM and a scripted ``fetch`` (``ui_harness.js``); nothing is served.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "docflow" / "web" / "static"


def test_no_undo_snackbar_is_shown_without_an_undo_operation() -> None:
    null_callback = re.compile(r"showUndoSnackbar\((?:[^()]|\([^()]*\))*,\s*null\s*[,)]")
    offenders = [path.name for path in sorted(STATIC.glob("*")) if path.suffix in {".html", ".js"}
                 and null_callback.search(path.read_text())]
    assert offenders == []


HARNESS = Path(__file__).with_name("ui_harness.js")
SCOPE = "scope-synthetic-1"
KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _item(item_id: str = "item-1", **overrides) -> dict:
    return {
        "id": item_id, "job_id": "job-1", "status": "pending", "source_name": "scan.pdf",
        "page_numbers": [2], "reason": "low_confidence", "near_duplicate_of": None,
        "doc_type": "Statement", "period": "2026-08", "suggested_filename": "Suggested.pdf",
        "suggested_relative_directory": "Review", "confidence": 0.4,
        "actions": ["approve", "correct", "skip"], "created_at": "2026-09-13T00:00:00+00:00",
        "updated_at": "2026-09-13T00:00:00+00:00", **overrides,
    }


def _action_response(operation_id: str, kind: str = "review_approve", **item) -> dict:
    return {
        "operation": {"id": operation_id, "kind": kind, "status": "completed",
                      "created_at": "t", "completed_at": "t", "undone_at": None},
        "outcome": "completed",
        "items": [{"review_item_id": "item-1", "job_id": "job-1", "action": "approve",
                   "result": "filed", "error_code": None,
                   "relative_path": "Review/Suggested.pdf", "page_numbers": [2], **item}],
        "jobs": [],
    }


def _undo_response(operation_id: str, outcome: str = "undone", steps=None) -> dict:
    return {
        "undo_operation": {"id": "undo-1", "kind": "operation_undo",
                           "status": "completed" if outcome == "undone" else "failed",
                           "created_at": "t", "completed_at": "t", "undone_at": None},
        "operation": {"id": operation_id, "kind": "review_approve",
                      "status": "undone" if outcome == "undone" else "completed",
                      "created_at": "t", "completed_at": "t", "undone_at": None},
        "outcome": outcome,
        "steps": steps or [{"ordinal": 0, "review_item_id": "item-1", "job_id": "job-1",
                            "action": "approve", "kind": "remove_filed",
                            "status": "compensated", "error_code": None,
                            "relative_path": "Review/Suggested.pdf"}],
        "jobs": [],
    }


def _page(items: list[dict], extra: list[dict] = ()) -> list[dict]:
    return [
        {"method": "GET", "url": "^/api/v1/archive-scopes/active$",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": "^/api/v1/review-items\\?", "body": {
            "archive_scope_id": SCOPE, "status": "pending", "items": items}},
        {"method": "GET", "url": "^/api/unmatched$", "body": {"files": [], "count": 0}},
        {"method": "GET", "url": "^/api/health$", "body": {}},
        *extra,
    ]


def run_ui(tmp_path: Path, responses: list[dict], steps: list[str],
           storage: dict | None = None, *, page: str = "review.html",
           location: dict | None = None, session: dict | None = None) -> dict:
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"responses": responses, "steps": steps,
                                    "storage": storage or {}, "page": page,
                                    "session": session or {},
                                    "location": location or {"pathname": "/review"}}))
    completed = subprocess.run([shutil.which("node") or "node", str(HARNESS), str(scenario)],
                               capture_output=True, text=True, timeout=60, check=True)
    result = json.loads(completed.stdout)
    assert result["errors"] == []
    return result


def _posts(result: dict) -> list[dict]:
    return [f for f in result["fetches"] if f["method"] == "POST"]


def test_approve_receives_operation_id_and_undo_calls_the_undo_endpoint(tmp_path) -> None:
    result = run_ui(tmp_path, _page([_item()], [
        {"method": "POST", "url": "^/api/v1/review-items/item-1/approve$",
         "body": _action_response("op-approve-1")},
        {"method": "POST", "url": "^/api/v1/operations/op-approve-1/undo$",
         "body": _undo_response("op-approve-1")},
    ]), ["doAction('approve')", "__clickText('Undo')"])

    approve, undo = _posts(result)
    assert approve["url"] == "/api/v1/review-items/item-1/approve"
    assert approve["body"]["archive_scope_id"] == SCOPE
    assert KEY.match(approve["body"]["idempotency_key"])
    assert set(approve["body"]) == {"archive_scope_id", "idempotency_key"}
    assert "op-approve-1" in json.dumps(result["snapshots"][0]["storage"])
    assert undo["url"] == "/api/v1/operations/op-approve-1/undo"
    assert undo["body"]["archive_scope_id"] == SCOPE
    assert KEY.match(undo["body"]["idempotency_key"])
    assert undo["body"]["idempotency_key"] != approve["body"]["idempotency_key"]
    assert not any("/api/queue" in f["url"] for f in result["fetches"])


def test_undo_survives_browser_reload_and_keyboard_shortcut_uses_it(tmp_path) -> None:
    stored = {"docflow_review_undo": json.dumps({
        "operation_id": "op-before-reload", "archive_scope_id": SCOPE,
        "kind": "review_correct", "label": 'Filed "Electric.pdf"'})}
    undo_rule = {"method": "POST", "url": "^/api/v1/operations/op-before-reload/undo$",
                 "body": _undo_response("op-before-reload")}

    reloaded = run_ui(tmp_path, _page([_item()], [undo_rule]), [], stored)
    clicked = run_ui(tmp_path, _page([_item()], [undo_rule]), ["__clickText('Undo')"], stored)
    keyed = run_ui(tmp_path, _page([_item()], [undo_rule]), ["__key('u')"], stored)

    assert 'Filed "Electric.pdf"' in reloaded["texts"]
    assert _posts(reloaded) == []
    for result in (clicked, keyed):
        (undo,) = _posts(result)
        assert undo["url"] == "/api/v1/operations/op-before-reload/undo"
        assert undo["body"]["archive_scope_id"] == SCOPE
        assert "docflow_review_undo" not in result["storage"]


def test_partial_undo_failure_is_visible_and_can_be_retried(tmp_path) -> None:
    failed_step = {"ordinal": 0, "review_item_id": "item-1", "job_id": "job-1",
                   "action": "approve", "kind": "remove_filed", "status": "failed",
                   "error_code": "target_modified", "relative_path": "Review/Suggested.pdf"}
    result = run_ui(tmp_path, _page([_item()], [
        {"method": "POST", "url": "^/api/v1/review-items/item-1/approve$",
         "body": _action_response("op-1")},
        {"method": "POST", "url": "^/api/v1/operations/op-1/undo$", "once": True,
         "body": _undo_response("op-1", "partial_failure", [failed_step])},
        {"method": "POST", "url": "^/api/v1/operations/op-1/undo$",
         "body": _undo_response("op-1")},
    ]), ["doAction('approve')", "__clickText('Undo')", "__clickText('Try undo again')"])

    _, first_undo, second_undo = _posts(result)
    assert first_undo["url"] == second_undo["url"] == "/api/v1/operations/op-1/undo"
    assert first_undo["body"]["idempotency_key"] != second_undo["body"]["idempotency_key"]
    after_failure = result["snapshots"][1]
    assert "op-1" in json.dumps(after_failure["storage"])
    assert "docflow_review_undo" not in result["storage"]


def test_partial_undo_failure_details_are_rendered(tmp_path) -> None:
    failed_step = {"ordinal": 0, "review_item_id": "item-1", "job_id": "job-1",
                   "action": "approve", "kind": "remove_filed", "status": "failed",
                   "error_code": "target_missing", "relative_path": "Review/Suggested.pdf"}
    result = run_ui(tmp_path, _page([_item()], [
        {"method": "POST", "url": "^/api/v1/review-items/item-1/approve$",
         "body": _action_response("op-1")},
        {"method": "POST", "url": "^/api/v1/operations/op-1/undo$",
         "body": _undo_response("op-1", "partial_failure", [failed_step])},
    ]), ["doAction('approve')", "__clickText('Undo')"])

    details = [t for t in result["texts"] if "Review/Suggested.pdf" in t]
    assert any("target_missing" in t and "nothing was deleted" in t for t in details)
    assert any("Undo could not finish" in t for t in result["texts"])


PAYLOAD = '<img src=x onerror="window.__xss=1">'
MARKERS = ("<img", "onerror", "<svg", "__xss")


def test_model_and_document_strings_render_as_text_never_markup(tmp_path) -> None:
    hostile = _item(
        "item-1", source_name=f"scan{PAYLOAD}.pdf", doc_type=f"Statement{PAYLOAD}",
        reason=f"low{PAYLOAD}", period=PAYLOAD, suggested_filename=f"{PAYLOAD}.pdf",
        suggested_relative_directory="Review/<svg onload=__xss()>", near_duplicate_of=PAYLOAD)
    second = _item("item-2\"><img src=x onerror=__xss()>", source_name=PAYLOAD)
    failed_step = {"ordinal": 0, "review_item_id": "item-1", "job_id": "job-1",
                   "action": "approve", "kind": "remove_filed", "status": "failed",
                   "error_code": "target_modified", "relative_path": f"Review/{PAYLOAD}.pdf"}
    result = run_ui(tmp_path, _page([hostile, second], [
        {"method": "POST", "url": "^/api/v1/review-items/item-1/approve$", "body": {
            **_action_response("op-1"), "operation": {
                **_action_response("op-1")["operation"], "kind": PAYLOAD}}},
        {"method": "POST", "url": "^/api/v1/operations/op-1/undo$", "status": 409,
         "body": {"error": {"code": "operation_not_undoable", "message": PAYLOAD}}},
        {"method": "GET", "url": "^/api/search\\?", "body": {"results": [
            {"filename": PAYLOAD, "directory": PAYLOAD, "icon": PAYLOAD,
             "url": "javascript:__xss()", "confidence": 0.9}]}},
        {"method": "GET", "url": "^/api/archive/tree$", "body": {"tree": [
            {"name": PAYLOAD, "path": PAYLOAD, "pdf_count": PAYLOAD, "children": []}]}},
        {"method": "POST", "url": "^/api/v1/operations/op-2/undo$",
         "body": _undo_response("op-2", "partial_failure", [failed_step])},
    ]), [
        "selectItem(1)", "selectItem(0)", "toggleSelect(activeItems()[1].id)",
        "doAction('approve')", "__clickText('Undo')",
        "runSearch('ab')", "openDirectoryPicker(() => {})",
        "undoOperation({operation_id: 'op-2', archive_scope_id: 'scope-synthetic-1'})",
    ])

    unsafe = [w["html"] for w in result["html_writes"] if any(m in w["html"] for m in MARKERS)]
    assert unsafe == []
    rendered = "\n".join(text for snapshot in result["snapshots"] for text in snapshot["texts"])
    for fragment in (f"scan{PAYLOAD}.pdf", f"Statement{PAYLOAD}", f"low{PAYLOAD}",
                     f'Filed "{PAYLOAD}.pdf"'):
        assert fragment in rendered
    assert "<svg onload=__xss()>" in result["texts"]  # a folder chip, as text
    assert rendered.count(PAYLOAD) >= 8
    assert not any("javascript:" in json.dumps(f) for f in result["fetches"])


def test_bulk_approve_uses_batch_endpoint_and_offers_durable_undo(tmp_path) -> None:
    batch = _action_response("op-batch-1", kind="review_batch")
    batch["outcome"] = "partial_failure"
    batch["items"].append({"review_item_id": "item-2", "job_id": None, "action": "approve",
                           "result": "rejected", "error_code": "destination_required",
                           "relative_path": None, "page_numbers": []})
    result = run_ui(tmp_path, _page([_item("item-1"), _item("item-2")], [
        {"method": "POST", "url": "^/api/v1/review-items/batch$", "body": batch},
        {"method": "POST", "url": "^/api/v1/operations/op-batch-1/undo$",
         "body": _undo_response("op-batch-1")},
    ]), ["onSelectAll()", "onBulkApprove()", "__clickText('Undo')"])

    posted, undo = _posts(result)
    assert posted["url"] == "/api/v1/review-items/batch"
    assert (posted["body"]["archive_scope_id"], posted["body"]["action"],
            posted["body"]["review_item_ids"]) == (SCOPE, "approve", ["item-1", "item-2"])
    assert KEY.match(posted["body"]["idempotency_key"])
    assert undo["url"] == "/api/v1/operations/op-batch-1/undo"
    assert any("destination_required" in t for t in result["snapshots"][1]["texts"])
    assert not any("/api/queue" in f["url"] for f in result["fetches"])


def test_edited_destination_sends_a_correction(tmp_path) -> None:
    result = run_ui(tmp_path, _page([_item()], [
        {"method": "POST", "url": "^/api/v1/review-items/item-1/correct$",
         "body": _action_response("op-correct-1", kind="review_correct")},
    ]), ["document.getElementById('edit-directory').value = 'Household/Utilities'",
         "document.getElementById('edit-filename').value = 'Electric.pdf'",
         "doAction('approve')"])

    (correct,) = _posts(result)
    assert correct["url"] == "/api/v1/review-items/item-1/correct"
    assert {k: v for k, v in correct["body"].items() if k != "idempotency_key"} == {
        "archive_scope_id": SCOPE, "relative_directory": "Household/Utilities",
        "filename": "Electric.pdf"}
    assert "op-correct-1" in json.dumps(result["storage"])


def test_shared_markup_helpers_escape_document_derived_values(tmp_path) -> None:
    result = run_ui(tmp_path, _page([]), [
        f"document.getElementById('footer').innerHTML = ruleBadge({json.dumps(PAYLOAD)})"])

    written = [w["html"] for w in result["html_writes"] if w["id"] == "footer"][-1]
    assert "<img" not in written
    assert "&lt;img src=x onerror=&quot;window.__xss=1&quot;&gt;" in written


def test_no_static_page_uses_the_retired_legacy_queue_routes() -> None:
    offenders = [path.name for path in sorted(STATIC.glob("*"))
                 if path.suffix in {".html", ".js"} and "/api/queue" in path.read_text()]
    assert offenders == []
