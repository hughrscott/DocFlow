# Phase 4 API contract — durable review actions, undo, and local-only access

Status: implemented in the sanitized workspace (Linux-tested only; Mac acceptance pending).
This is the complete frontend contract for the review queue: listing review items,
approve / correct / skip / batch actions, durable undo, the active-scope lookup, job
creation by handle, and the loopback bind rule. Phase 3 contracts
(`docs/phase-3-api-contract.md`) still apply to `GET /api/v1/jobs/{id}` and retry.

## 1. Conventions (all Phase 4 routes)

- **Local only.** The server binds to loopback (section 10). No route is reachable remotely.
- **Explicit scope.** Every request names `archive_scope_id` (1–128 characters). The server
  never infers scope from a path. Get the UI's scope from section 3.
- **Strict JSON bodies.** `Content-Type: application/json`; exactly the documented fields,
  strict types, no extras. Anything else is `422 invalid_request`.
- **Idempotency.** Every mutation carries `idempotency_key` matching
  `^[A-Za-z0-9._:-]{1,128}$`. Generate a **new key per user action** (the UI uses
  `<action>-<crypto.randomUUID()>`). Replaying the same key returns the stored result and
  never mutates again (section 8).
- **Paths.** Every returned path is archive-relative POSIX (`Folder/Sub/Name.pdf`): no
  leading `/`, `~` or drive, no `\`, no `.`/`..` segment. No absolute path is ever returned.
- **Errors.** Always `{"error": {"code": "<code>", "message": "<fixed text>"}}` with no
  other top-level keys. Messages are fixed strings; branch on `code`.
- **Common errors** on every route below:

| HTTP | `code` | Message |
|---|---|---|
| 503 | `state_unavailable` | `Local application state is not configured.` |
| 422 | `invalid_request` | `Request body does not match the contract.` (query-string errors use `Request does not match the contract.`) |
| 404 | `scope_not_found` | `Archive scope is not registered.` |

Check order for mutations: state configured (503) → body shape (422) → scope (404) →
idempotency replay (section 8) → route-specific checks.

## 2. Safe rendering (required of every client)

Every string in these responses that came from a document, OCR, classification, a model or
the archive — `source_name`, `reason`, `doc_type`, `period`, `near_duplicate_of`,
`suggested_filename`, `suggested_relative_directory`, `relative_path`, `error.message`,
labels built from them, folder names and search results — is **data, never markup**.

- Render with `textContent`, `createTextNode`, `input.value` or equivalent DOM APIs.
- Never pass such values to `innerHTML`, `insertAdjacentHTML`, `outerHTML`,
  `document.write`, inline `on*` attribute strings, or `eval`/`Function`.
- Build click handlers with `addEventListener` closures, never by interpolating IDs into
  markup (`onclick="toggle('${id}')"` is forbidden).
- Put IDs into URLs with `encodeURIComponent`.
- Only link to same-origin paths that start with a single `/`; anything else becomes `#`.
- If a legacy helper must return an HTML string, escape interpolations
  (`escapeHtml` in `shared.js`).

The shipped `review.html`/`shared.js` follow these rules and are tested with hostile
`<img onerror>` / `<svg onload>` payloads (`tests/review_actions/test_ui_contract.py`).

## 3. `GET /api/v1/archive-scopes/active`

Returns the scope the local UI works in. No body, no query.

```json
200 {"archive_scope": {"id": "3f2b0c9e-0000-5000-8000-000000000000"}}
```

Errors: `503 state_unavailable`; `404 scope_not_found` when no active scope is configured.
The root path is never returned. Backend wiring: `configure_state(store, filer, scope_id=...)`.

## 4. `GET /api/v1/review-items?archive_scope_id=<id>&status=pending`

`status` is optional, default `pending`; one of `pending`, `approved`, `corrected`,
`skipped`. Read-only; no idempotency key.

```json
200 {
  "archive_scope_id": "3f2b…",
  "status": "pending",
  "items": [{
    "id": "c1…", "job_id": "6b0d…", "status": "pending",
    "source_name": "scan.pdf",
    "page_numbers": [3, 4],
    "reason": "low_confidence",
    "near_duplicate_of": null,
    "doc_type": null, "period": null,
    "suggested_filename": "Suggested.pdf",
    "suggested_relative_directory": "Review",
    "confidence": 0.4,
    "actions": ["approve", "correct", "skip"],
    "created_at": "2026-09-13T21:01:20+00:00",
    "updated_at": "2026-09-13T21:01:20+00:00"
  }]
}
```

| Field | Meaning |
|---|---|
| `page_numbers` | source pages of the job this item covers (1-based, ascending); `[]` if the item has none |
| `reason` | short code from classification/review (`low_confidence`, `unmatched`, `near_duplicate_candidate`, a privacy block reason…) or `null` |
| `suggested_*` | may be `null`; directory is archive-relative, filename a single name |
| `confidence` | number in `[0, 1]` |
| `actions` | what is currently allowed: `approve` needs a valid suggestion and an available retained original; `correct` needs the original; `skip` needs only pages awaiting review. `[]` means the item cannot be acted on (for example a privacy-blocked item with no pages, a legacy item without page fingerprints, or a job no longer in `review`). Disable buttons not listed. |

No raw OCR text is ever returned. Page previews are not part of this contract.

## 5. Single-item actions

```
POST /api/v1/review-items/{id}/approve   body: {archive_scope_id, idempotency_key}
POST /api/v1/review-items/{id}/skip      body: {archive_scope_id, idempotency_key}
POST /api/v1/review-items/{id}/correct   body: {archive_scope_id, idempotency_key,
                                                relative_directory, filename}
```

| Field | Constraint |
|---|---|
| `relative_directory` | string 1–1024 (see destination rules) |
| `filename` | string 1–255 (see destination rules) |

**Semantics**

- **approve** extracts the item's pages from the job's retained original (verified against
  the admitted page fingerprints), stages and verifies the PDF in application state, and
  places it at `suggested_relative_directory/suggested_filename` without replacing
  anything (`Name.pdf`, `Name_2.pdf`, …). Pages become `filed`, item `approved`.
- **correct** does the same at the client's destination, records a `corrections` row, item
  becomes `corrected`.
- **skip** changes no files. Pages become `skipped`, item `skipped`.
- When the job has no page left in `review`, the job moves `review → completed`.

**Destination rules (correct)** — rejected with `400 invalid_destination` **before any
mutation** (no operation is stored, no directory is created):
absolute, `~` or drive paths; `..`, `.` or empty components (`a//b`, trailing `/`);
components starting with `.` or `~`; backslashes or control characters; the archive root
itself; filenames that contain `/`, start with `.` or `~`, or do not end in `.pdf`
(case-insensitive); names the local state guard refuses (secret-like tokens); any existing
component that is a symlink (escape) or not a directory; a destination name that exists
and is not a regular file. Everything resolves beneath the scope's canonical root.

**Response `200`**

```json
{
  "operation": {"id": "9a1c…", "kind": "review_approve", "status": "completed",
                "created_at": "…", "completed_at": "…", "undone_at": null},
  "outcome": "completed",
  "items": [{"review_item_id": "c1…", "job_id": "6b0d…", "action": "approve",
             "result": "filed", "error_code": null,
             "relative_path": "Review/Suggested.pdf", "page_numbers": [2]}],
  "jobs": [{"id": "6b0d…", "status": "review",
            "page_accounting": {"page_count": 4, "pending": 0, "filed": 2, "review": 2,
                                "skipped": 0, "blocked": 0, "complete": true}}]
}
```

`operation.kind`: `review_approve`, `review_correct`, `review_skip`. **Store
`operation.id`**: it is the only handle needed to undo, including after a reload.
`items[].result`: `filed` (with `relative_path`) or `skipped` (`relative_path: null`).
`jobs[]` is a snapshot taken when the operation completed.

**Errors**

| HTTP | `code` | Message | Stored for the key? |
|---|---|---|---|
| 400 | `invalid_destination` | `Destination must be a relative archive folder and a visible .pdf name.` | no |
| 404 | `review_item_not_found` | `Review item not found in this scope.` | no |
| 409 | `review_item_not_pending` | `Review item is no longer pending.` | yes |
| 409 | `review_item_not_actionable` | `Review item has no pages awaiting review.` | yes |
| 409 | `source_unavailable` | `The retained original for these pages is unavailable.` | yes |
| 409 | `destination_required` | `The suggestion cannot be filed; send a correction.` (approve only) | yes |
| 409 | `idempotency_key_reused` | `This idempotency key was used for a different request.` | no |
| 409 | `output_verification_failed`, `collision_limit_exceeded`, `unsafe_destination`, `state_changed`, `io_error` | `The review action could not be completed.` | yes |

The last row is a failure while executing; nothing was recorded as filed and the item stays
`pending`. Retry with a **new** key after refreshing the list.

## 6. `POST /api/v1/review-items/batch`

```json
{"archive_scope_id": "3f2b…", "idempotency_key": "batch-…",
 "action": "approve", "review_item_ids": ["c1…", "c2…"]}
```

`action`: `approve` or `skip`. `review_item_ids`: 1–100 unique strings (1–128 chars);
duplicates → `422 invalid_request`. Corrections are single-item only.

One durable operation (`kind: "review_batch"`) covers every accepted item. Each item is
validated independently; invalid items are **rejected without affecting the others**.
Always `200` when the request itself is valid:

```json
{
  "operation": {"id": "b7…", "kind": "review_batch", "status": "completed", "…": "…"},
  "outcome": "partial_failure",
  "items": [
    {"review_item_id": "c2…", "job_id": null, "action": "approve", "result": "rejected",
     "error_code": "review_item_not_pending", "relative_path": null, "page_numbers": []},
    {"review_item_id": "c1…", "job_id": "6b0d…", "action": "approve", "result": "filed",
     "error_code": null, "relative_path": "Review/Suggested.pdf", "page_numbers": [2]}
  ],
  "jobs": [{"id": "6b0d…", "status": "review", "page_accounting": {"…": "…"}}]
}
```

- `items` are in request order. `result`: `filed`, `skipped`, `rejected` (never started:
  `review_item_not_found`, `review_item_not_pending`, `review_item_not_actionable`,
  `source_unavailable`, `destination_required`) or `failed` (execution codes from 5).
- `outcome`: `completed` (all succeeded), `partial_failure` (some succeeded), `failed`
  (none succeeded; `operation.status` is `failed` and it cannot be undone).
- Offer Undo whenever at least one item is `filed`/`skipped`; list rejected/failed items
  with their codes. `jobs[]` contains only jobs of accepted items.
- Other errors: `409 idempotency_key_reused`.

## 7. `POST /api/v1/operations/{id}/undo`

```json
{"archive_scope_id": "3f2b…", "idempotency_key": "undo-…"}
```

Undoable: operations of kind `review_approve`, `review_correct`, `review_skip`,
`review_batch` whose status is `completed` or `partially_undone` (or `undone`, which
returns the stored result). Filing-job operations (`file_job`) are not undoable here.

**What undo does, per journaled step of the target operation**

| Target step | Undo step `kind` | Compensation |
|---|---|---|
| file written by the action | `remove_filed` | verify the file still has the exact bytes written (SHA-256), atomically detach it to a hidden name in the same folder, re-verify, journal, delete it; then in one transaction remove its file record, pages `filed → review`, item → `pending`, delete the correction (correct), job `completed → review` if needed |
| item skipped | `reopen_skipped` | pages `skipped → review`, item → `pending`, job `completed → review` if needed |
| nothing to compensate (already compensated, or never succeeded) | `none` or the original kind | reported `skipped` |

Undo never touches the retained original, other filed documents, or any file it did not
create. It **never deletes** a target that is missing, modified, a symlink, not a regular
file, under a symlinked folder, or whose state no longer matches; those steps fail with a
code and leave the file, its record, and the item/page/job state unchanged.

**Response `200`** (also for partial failure — branch on `outcome`)

```json
{
  "undo_operation": {"id": "u1…", "kind": "operation_undo", "status": "failed",
                     "created_at": "…", "completed_at": "…", "undone_at": null},
  "operation": {"id": "b7…", "kind": "review_batch", "status": "partially_undone",
                "created_at": "…", "completed_at": "…", "undone_at": null},
  "outcome": "partial_failure",
  "steps": [
    {"ordinal": 0, "review_item_id": "c1…", "job_id": "6b0d…", "action": "approve",
     "kind": "remove_filed", "status": "compensated", "error_code": null,
     "relative_path": "Review/Suggested.pdf"},
    {"ordinal": 1, "review_item_id": "c3…", "job_id": "7a1e…", "action": "approve",
     "kind": "remove_filed", "status": "failed", "error_code": "target_modified",
     "relative_path": "Review/Suggested_2.pdf"}
  ],
  "jobs": [{"id": "6b0d…", "status": "review", "page_accounting": {"…": "…"}}]
}
```

| Field | Values |
|---|---|
| `outcome` | `undone` (every compensable step is compensated) or `partial_failure` (at least one step failed) |
| `operation.status` | `undone` (all compensated; `undone_at` set), `partially_undone` (some compensated), `completed` (none compensated) |
| `undo_operation.status` | `completed` (no step failed) or `failed` |
| `steps[].status` | `compensated`, `failed`, `skipped` |

**Per-step error codes** (show the path and a message; all mean "kept, nothing deleted")

| `error_code` | Meaning | Suggested UI text |
|---|---|---|
| `target_missing` | the filed copy is gone | the filed copy is missing; nothing was deleted |
| `target_modified` | bytes differ from what was filed | the filed copy was changed after filing; it was kept |
| `target_unsafe` | now a symlink, a directory, or under a symlinked folder | the path is now a link or not a regular file; it was kept |
| `target_conflict` | a new file appeared at the name while restoring a changed file | a new file appeared at that name; both were kept |
| `state_changed` | review/page/file-record state no longer shows this action | the review state changed; nothing was undone |
| `io_error` | filesystem error | the archive could not be read or written; retry later |

**Repeat and retry**

- Same `idempotency_key` → the identical stored body, no work.
- New key on an `undone` operation → the stored body of the undo that completed it; no work.
- New key on `completed`/`partially_undone` → a new attempt that retries only steps not yet
  compensated (for example after the user restores the original bytes). Already
  compensated steps are reported `skipped`.
- Replaying the **original action's** key after undo still returns the original action
  body; it does not re-file.

**Errors**: `404 operation_not_found` (`Operation not found in this scope.`),
`409 operation_not_undoable` (`This operation cannot be undone.`).

## 8. Idempotency and durability

- Action keys are per scope and shared by approve/correct/skip/batch: the operation ID is
  derived from `(scope, key)`. Reusing a key with a different route, item(s) or destination
  → `409 idempotency_key_reused` (not stored, no mutation).
- Undo keys are per `(scope, target operation, key)`.
- Stored: `200` bodies and the `409` conflicts marked "yes" above. Not stored: `400`,
  `404`, `422`, `503`, `idempotency_key_reused`.
- Everything lives in local application state (SQLite outside the archive). Results survive
  process restart and browser reload.
- **Crash safety.** The plan and every destination are journaled before a file is placed;
  undo journals the verified detach before deleting. If the process stops mid-operation,
  the next review action/undo call (or a replay of the same key) first resumes every
  interrupted review operation: a journaled placement whose bytes match is adopted (no
  duplicate `_2` copy), a verified detach is completed, and nothing unverified is deleted.

## 9. `POST /api/v1/jobs` — creation by handle only

```json
{"archive_scope_id": "3f2b…", "idempotency_key": "job-…",
 "source": {"kind": "upload", "name": "scan.pdf"}}
{"archive_scope_id": "3f2b…", "idempotency_key": "job-…",
 "source": {"kind": "watch", "relative_path": "batch/scan.pdf"}}
```

`upload.name`: one file name (1–255) in the server's upload folder. `watch.relative_path`:
path (1–1024) relative to the configured watch folder. No field accepts a filesystem path;
extra fields (for example `path`) or other kinds → `422 invalid_request`.

`200` = `{"admission": {"state": "ready", "retryable": false}, ...GET /api/v1/jobs/{id} body}`
(job `ready`, page accounting). Re-admitting the same unchanged source returns the existing
unfinished job.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_source` (`Source must be an uploaded file or a configured watch-folder PDF.`) | absolute/`~`/traversal path, name with `/`, non-`.pdf`, symlink or symlinked folder, unconfigured root; no job is created |
| 409 | `source_unavailable` | missing, changing, partial download, iCloud placeholder or malformed; retry later with a new key (not stored) |
| 409 | `idempotency_key_reused` | same key, different source |
| 503 | `state_unavailable` | state or durable filer not configured |

Legacy adapter: `POST /api/process {"path": ...}` still exists for the dashboard, but only
accepts a regular `.pdf` inside the upload folder or the configured watch folder (no
traversal, no symlinks); anything else is `400`. `/api/upload` is unchanged.

## 10. Local bind (X03)

`docflow ui` and `docflow review` bind to `127.0.0.1` by default. `--host` accepts only
`localhost` or a loopback IP literal (`127.0.0.0/8`, `::1`); `0.0.0.0`, `::`, LAN addresses
and host names are rejected before the server starts (no DNS lookup). There is no override.

## 11. Reference UI behavior (`review.html` + `shared.js`)

1. Load: `GET /api/v1/archive-scopes/active`, then `GET /api/v1/review-items?...&status=pending`.
   On error show the fixed message; the pending tab never uses `/api/queue`.
2. Approve (fields unchanged) → approve; edited filename/folder → correct; Skip → skip;
   "Approve N selected" → batch with `action: "approve"`.
3. On `200`, persist `{operation_id, archive_scope_id, kind, label}` in `localStorage`
   (`docflow_review_undo`) and show the Undo snackbar bound to
   `POST /api/v1/operations/{operation_id}/undo`. After reload the stored entry restores a
   persistent snackbar; the `U` key undoes it. A dismiss button forgets it.
4. Undo uses a new key per click. `outcome: "undone"` → forget the entry, refresh.
   `partial_failure` → persistent alert listing `relative_path: message (error_code)` for
   every failed step, with "Try undo again" (new key) and "Dismiss"; the entry is kept.
5. No Undo control is ever shown without an operation ID (`showUndoSnackbar` ignores a
   missing callback). The legacy Unmatched tab has no durable operation, so it shows a toast.

## 12. Not part of Phase 4

- Production wiring of the state store and active scope in `docflow ui` (routes return
  `503 state_unavailable` until `configure_state` is called), and moving the legacy
  pipelines to `DurableFiler`; legacy pipelines still write `review_queue.json`, so their
  items are not listed by `/api/v1/review-items` until migrated.
- Page previews for durable review items; undo of `file_job` operations; actions for items
  with no page references (privacy-blocked).
- Action tracking, deadlines, email, payments. Mac acceptance is pending human execution.
