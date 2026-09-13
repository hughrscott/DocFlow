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
| 503 | `state_unavailable` | `Local application state is not configured.` (not expected under `docflow ui`, which refuses to start without state; section 13) |
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

Errors: `503 state_unavailable` / `404 scope_not_found` only when the app runs without the
`docflow ui` bootstrap (section 13), for example embedded in tests. The root path is never
returned.

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

Dashboard adapter: `POST /api/process {"path": ...}` maps the path returned by
`POST /api/upload` (or a PDF in the configured watch folder) to an `upload:`/`watch:`
handle and runs the durable processing service of section 14; any other path is `400`.

## 10. Local bind (X03)

`docflow ui` and `docflow review` (an alias that serves the same durable UI; open `/review`)
bind to `127.0.0.1` by default. `--host` accepts only
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

## 12. Limitations (honest status)

- Linux-tested only; Mac acceptance (launch on loopback, durable undo after a browser
  reload, iCloud behavior) is pending human execution.
- Pre-existing legacy `review_queue.json` items (from releases before this one) are not
  imported at startup and are not listed; the Phase 1 migration remains an explicit,
  non-destructive backend step. Everything processed now is durable (section 14).
- No page previews for durable review items (the preview pane shows its placeholder); no
  undo of `file_job` operations; no actions for items without page references
  (privacy-blocked). `POST /api/reprocess/{id}` returns `409`; retry failed jobs with
  `POST /api/v1/jobs/{id}/retry`.
- The run summary files (`MailArchivingSummary.xlsx/.txt`) are no longer produced; the
  durable job record (`GET /api/v1/jobs/{id}`, CLI console output) replaces them.
- Interrupted filing jobs are not auto-resumed at UI startup; use
  `POST /api/v1/jobs/{id}/retry`. Interrupted review actions and undos resume automatically
  on the next review action or undo.
- Action tracking, deadlines, email and payments are not part of this release.

## 13. Local bootstrap and configuration (`docflow ui`)

`docflow ui` (also launched by `docflow start` and the macOS LaunchAgent) wires durable state
before serving; routes are not reachable until this succeeds:

1. Load the config file (`--config`, `DOCFLOW_CONFIG`, `~/.docflow/config.yaml`, …).
2. **`archive_root` is required** and must name an existing directory (`~` is expanded).
   There is no default archive path. It must not be `/`, a system directory, the home
   directory, or overlap the application-state directory.
3. Resolve application state outside every archive root: macOS
   `~/Library/Application Support/DocFlow/`, Linux `${XDG_DATA_HOME:-~/.local/share}/docflow/`
   (never iCloud). The archive root is validated **before** any state file is created.
4. Take the single-writer lock, open/migrate `state.sqlite3`, and register the archive root
   as an archive scope (idempotent: the same root keeps the same scope ID across restarts).
   That scope becomes the active scope returned by section 3.
5. Configure the durable filer with the upload folder (`<state>/cache/uploads`, the folder
   used by `POST /api/upload`) and, if set, `scan_watch_folder` as the only job-source roots.
6. Bind to loopback (section 10) and serve. On exit the state is released and the lock
   dropped.

Startup fails closed, prints an actionable message and starts no server when:

| Condition | Message starts with |
|---|---|
| `archive_root` missing or blank | `archive_root is not set in the config file.` |
| archive root missing, a file, a system/home directory, or overlapping state | `archive_root in the config file cannot be used:` / `Local application state is unsafe:` |
| another DocFlow process holds the state lock | `Another DocFlow process is using the local state.` |

HTTP callers can never choose the archive root, the state location or the scope; the scope
ID is only read from section 3 and echoed back. Covered by
`tests/review_actions/test_localhost_bootstrap.py` (bootstrap, fail-closed cases, and
list → approve → restart → undo through the configured app).

## 14. Processing workflow and storage planes

There is one processing path. The dashboard upload, `docflow --input <pdf>`, `docflow batch`
and `docflow watch` all run `docflow.filing.processing.process_scan`:

1. **Handle.** Web: `POST /api/upload` stores the bytes in `<state>/cache/uploads` under a
   plain `.pdf` name that never replaces an existing upload (names with `/`, `..`, a leading
   `.`/`~`, or not ending in `.pdf` → `400`); `POST /api/process` turns that path, or a PDF
   inside `scan_watch_folder`, into an `upload:`/`watch:` handle (anything else → `400`;
   no state → `503`). CLI: a file inside the watch folder uses `watch:`; any other input is
   copied (never linked or moved) into the upload folder and left untouched.
2. **Admission** by `DurableFiler.admit` (stability checks, page fingerprints, job `ready`).
3. **Local OCR, clustering, classification and confidence gate** (model calls only through
   `CloudPromptGateway`; local-only mode makes none). Job `ocr → classified`; a failure moves
   the job to `failed` with `last_error_code: "processing_failed"`.
4. **Journaled filing** by `DurableFiler.file_job`: auto-filed documents whose destination
   is a valid archive-relative `.pdf` become `filed`; everything else becomes a durable
   review item with page references (`reason`: `low_confidence` with the rule's suggestion,
   `unmatched` without a suggestion, or `unsafe_destination`). The original is retained,
   collision-safe, in `<archive_root>/BeenOrganized<mmddyy>/`; a watch-folder or upload
   source is removed only after every output verifies.
5. Review items are immediately visible in `GET /api/v1/review-items` and actionable with
   approve/correct/skip/batch and undo (sections 4–7). `GET /api/process/status/{id}` is
   in-memory progress only; its `durable_job_id` names the durable job.

**Archive plane** receives only filed PDFs and retained originals. **Application state**
holds jobs, pages, review items, operations, corrections (`state.sqlite3`), the upload
folder, preview cache (`cache/previews`) and the corrections log used by `docflow learn`
(`logs/corrections_log.json`). No `review_queue.json`, `MailArchivingSummary.*`,
`filing_log_*.json`, hash index, preview cache or corrections log is written to the archive.

**Retired legacy state system.** `/api/queue`, `/api/queue/{approve,correct,skip}/{id}`,
`/api/preview/{item}/{page}`, the separate legacy review server and the archive queue and
summary writers are removed. Existing legacy files in the archive (`review_queue.json`,
summaries, filing logs, preview caches) are never read as active state, never modified and
never deleted at startup or during processing; `/api/search` and `/api/archive/logs` may
still read old filing logs for display.

**Unmatched tab.** `POST /api/unmatched/reclassify` accepts only a PDF inside
`<archive_root>/_Unmatched` or `_Skipped` (otherwise `403`/`404`), validates
`directory`/`filename` with the section 5 destination rules (`400`), places the file under a
collision-safe name without replacing anything, removes the source only after the copy
verifies, and returns an archive-relative `target`.

Covered by `tests/review_actions/test_processing_bridge.py`.
