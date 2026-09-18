# Phase 3 API contract — durable ingestion, filing, page accounting and retry

Status: implemented in the sanitized workspace (Linux-tested only; macOS `UF_DATALESS`
and iCloud placeholder behavior are tested through injected stat results and synthetic
placeholder files, and remain pending Mac acceptance). This contract covers backend behavior
a frontend may rely on. It does not design UI and does not include undo (Phase 4).

## 1. Storage planes

| Plane | Holds | Never holds |
|---|---|---|
| Archive (user-selected, may be iCloud) | filed PDFs; retained original scans | filing logs, hash indexes, job state, journals, staging files, caches |
| Application state (`~/Library/Application Support/DocFlow/` on macOS, `${XDG_DATA_HOME:-~/.local/share}/docflow/` on Linux) | `state.sqlite3` (jobs, job pages, file records, operations, operation steps), `cache/staging/<operation_id>/` | archive documents |

One exception is bounded and named: when the application-state volume and the archive
volume differ (`EXDEV`), a verified copy is written to a hidden
`.docflow-<operation_id>-<ordinal>.partial` file beside its destination, hard-linked into
place and removed immediately. Leftovers from a crash are deleted on the next resume of that
operation.

## 2. Guarantees

1. **Stabilized input.** A source is admitted only after bounded observations show unchanged
   size and mtime, the PDF opens with the same positive page count each time, and the bytes
   do not change while hashed. Inputs that are missing, zero-byte, malformed, still changing,
   partial downloads (`*.crdownload`, `*.part`, `*.partial`, `*.download`, or such a
   sibling), iCloud placeholders (`.<name>.icloud`, either the path itself or a sibling) or
   macOS dataless files (`st_flags & UF_DATALESS`, never opened) are **retryable** and left
   byte-identical. No job row is created for them.
2. **Fingerprints.** Pages are rendered at 150 DPI, 8-bit grayscale (`L`).
   `page content SHA-256 = SHA-256("docflow.page.v1\0" ‖ u8 len(mode) ‖ mode ‖ u64be width ‖
   u64be height ‖ u64be len(pixels) ‖ pixels)`.
   `document SHA-256 = SHA-256("docflow.document.v1\0" ‖ u64be page_count ‖ page hashes in
   order)`. A 64-bit dHash per page is stored as 16 hex digits. Raw-file SHA-256 is kept as
   evidence only. Text, page dimensions, filenames and page counts are never duplicate keys.
3. **Exact duplicates** (identical document SHA-256, verified by re-rendering the existing
   file) create no second filed PDF. **Near duplicates** (same page count, every page dHash
   Hamming distance ≤ 2 to a filed document created by a Phase 3 job, not identical) are
   never deduplicated: the document goes to review with
   `reason: "near_duplicate_candidate"`. Imported or indexed records have no stored page
   dHashes and are compared only for exact duplicates.
4. **No overwrite.** Names are resolved `Name.pdf`, `Name_2.pdf`, `Name_3.pdf`, … A candidate
   is skipped when a file exists there (unless it renders identically, which is an exact
   duplicate) or when a file record already claims the path. Placement is a hard link, which
   fails rather than replacing a file that appeared after the check; the next name is then
   journaled and tried. Originals use the same naming and compare raw bytes.
5. **Staged and verified.** Each output is written to application-state staging and fsynced,
   then reopened and checked for PDF validity, page count and document SHA-256, placed
   atomically, re-hashed at the destination, and only then recorded.
6. **Journal before mutation.** The whole plan (every document, outcome and step) is committed
   before any archive or source mutation. Every destination name is committed before its
   placement.
7. **Page accounting.** Every page ends exactly once as `filed`, `review`, `skipped` or
   `blocked`. Duplicate, missing, out-of-range, non-integer or empty assignments are rejected
   before anything is journaled or written.
8. **Source last, never lost.** The source is removed from the watch/upload folder only after
   every output and the retained original verify against the journal. A source already
   inside the archive (`archive:` locator) is recorded in place and never removed. A changed
   source is never touched.

## 3. Job, page, operation and step states

**Job statuses touched by Phase 3:** `discovered → stabilizing → ready` (admission, one
transaction); `classified → filing` (plan committed); `filing → completed` (no review
pages) or `filing → review` (at least one review page); `classified|filing → failed`
(fail closed); `failed → ready` (retry requeue). Restart behavior from Phase 2 is unchanged:
`awaiting_model` and `classified` reset to `ocr`. `filing` is durable and is resumed, not
reset.

**Page statuses:** `pending`, `filed`, `review`, `skipped`, `blocked`. Filed pages change when
their document commits; other outcomes change when the operation finalizes.

**Operation `kind: "file_job"` statuses:** `running` (resumable) → `completed` or `failed`.

**Operation `kind: "job_retry"`:** one row per `(archive_scope_id, job_id, idempotency_key)`;
`running` → `completed` (stores the response) or `failed` (stores the rejection code).

**Steps of a `file_job`** (ordinal order; one `write_filed` per filed document, in plan
order, then `retain_original`, then `cleanup_source`):

| `kind` | `status` values | `destination_relative_path` | `expected_sha256` meaning |
|---|---|---|---|
| `write_filed` | `planned`, `done` (written), `skipped` (exact duplicate already present), `failed` | journaled candidate; final written path or the existing duplicate | document SHA-256 of the pages |
| `retain_original` | `planned`, `done`, `failed` | retained original path | raw-file SHA-256 of the source |
| `cleanup_source` | `planned`, `done` (removed, or already gone), `skipped` (source is in the archive), `failed` | `null` | raw-file SHA-256 of the source |

**Durable boundaries** (a crash may happen after any): `plan_committed`, `staged`,
`destination_recorded`, `placed`, `step_committed`, `duplicate_committed`,
`original_destination_recorded`, `original_placed`, `original_committed`, `source_removed`,
`cleanup_committed`, `finalized`. Restart reconciliation (`DurableFiler.reconcile`) purges
staging, deletes this operation's partial files, re-verifies the source, and resumes each
`running` operation:

| Found after restart | Action |
|---|---|
| journaled destination exists and renders to the expected document hash (filed) or matches the raw hash (original) | adopt it: record and complete the step (no second copy) |
| journaled destination missing or different | resolve a name again and place |
| source already removed, step not committed | verify outputs, mark cleanup done |
| staging or partial leftovers | delete (state plane only, or this step's named partial) |

## 4. `GET /api/v1/jobs/{job_id}`

Read-only; no idempotency key.

### Request

`GET /api/v1/jobs/{job_id}?archive_scope_id=<id>`. `archive_scope_id` is required, 1–128
characters, and must be registered. `job_id` is opaque.

### Check order

1. Local state not configured → `503 state_unavailable`.
2. `archive_scope_id` missing or longer than 128 characters → `422 invalid_request`.
3. Unknown scope → `404 scope_not_found`.
4. Job not in that scope → `404 job_not_found`.

### Response `200`

```json
{
  "job": {
    "id": "6b0d…",
    "archive_scope_id": "3f2b0c9e-0000-5000-8000-000000000000",
    "status": "filing",
    "attempt": 1,
    "source_name": "Scan-synthetic.pdf",
    "last_error_code": "io_error",
    "created_at": "2026-09-13T21:01:20+00:00",
    "updated_at": "2026-09-13T21:01:21+00:00"
  },
  "page_accounting": {
    "page_count": 3,
    "pending": 1, "filed": 2, "review": 0, "skipped": 0, "blocked": 0,
    "complete": false,
    "pages": [
      {"page_number": 1, "status": "filed"},
      {"page_number": 2, "status": "filed"},
      {"page_number": 3, "status": "pending"}
    ]
  },
  "operation": {
    "id": "9a1c…",
    "kind": "file_job",
    "status": "running",
    "created_at": "2026-09-13T21:01:20+00:00",
    "completed_at": null,
    "assignments": [
      {"pages": [1, 2], "outcome": "filed", "reason": null,
       "relative_directory": "Household/PNC", "filename": "Statement.pdf",
       "near_duplicate_of": null},
      {"pages": [3], "outcome": "review", "reason": "low_confidence",
       "relative_directory": null, "filename": null, "near_duplicate_of": null}
    ],
    "steps": [
      {"ordinal": 0, "kind": "write_filed", "status": "done",
       "destination_relative_path": "Household/PNC/Statement.pdf", "error_code": null},
      {"ordinal": 1, "kind": "retain_original", "status": "planned",
       "destination_relative_path": "BeenOrganized091326/Scan-synthetic.pdf",
       "error_code": null},
      {"ordinal": 2, "kind": "cleanup_source", "status": "planned",
       "destination_relative_path": null, "error_code": null}
    ]
  },
  "recovery": {"state": "resumable", "retryable": true},
  "files": [
    {"relative_path": "Household/PNC/Statement.pdf", "role": "filed", "page_numbers": [1, 2]}
  ]
}
```

| Field | Meaning |
|---|---|
| `job.status` | a job status from section 3 |
| `job.attempt` | incremented each time the job enters `ocr` |
| `job.source_name` | file name only; never a path |
| `job.last_error_code` | `null` or a code from section 6 |
| `page_accounting.page_count` | number of source pages |
| `page_accounting.<status>` | count of pages in that status; the five counts sum to `page_count` |
| `page_accounting.complete` | `true` when no page is `pending` |
| `page_accounting.pages` | every page, ascending |
| `operation` | the latest `file_job` operation, or `null` before filing starts |
| `operation.assignments[].outcome` | the planned outcome after near-duplicate routing (`filed` may become `review`) |
| `operation.assignments[].reason` | short code supplied by classification/review, or `near_duplicate_candidate` |
| `operation.assignments[].near_duplicate_of` | archive-relative path, only for near-duplicate routing |
| `operation.steps[]` | journal steps, section 3 |
| `recovery.state` | `resumable` (job `filing`: retry resumes), `failed_closed` (job `failed`: retry may requeue), `none` |
| `recovery.retryable` | `true` for `resumable` and `failed_closed` |
| `files[]` | file records created for this job: `role` is `filed` or `original`; `page_numbers` are source page numbers. Exact duplicates add no file record; the step's `destination_relative_path` shows the existing file. |

Errors use the Phase 2 body: `{"error": {"code": "<code>", "message": "<fixed text>"}}`.
The `job_not_found` message is exactly `"Job not found in this scope."`.

## 5. `POST /api/v1/jobs/{job_id}/retry`

Retries from the last safe checkpoint. A `filing` job resumes its journaled plan. A `failed`
job is requeued to `ready` (local OCR and classification must run again) only if its source
is available and byte-identical to what was admitted.

### Request

`Content-Type: application/json`, exactly these fields, strict types, no extras:

```json
{"archive_scope_id": "3f2b0c9e-0000-5000-8000-000000000000", "idempotency_key": "ui-retry-0001"}
```

| Field | Constraint |
|---|---|
| `archive_scope_id` | string, 1–128 characters, registered |
| `idempotency_key` | string matching `^[A-Za-z0-9._:-]{1,128}$`; generate a new key per user action |

### Check order

1. Local state or durable filer not configured → `503 state_unavailable`.
2. Body invalid (missing or extra fields, wrong types, bad key) → `422 invalid_request`.
3. Unknown scope → `404 scope_not_found`.
4. This key was already used for this scope and job → replay (below).
5. Job not in scope → `404 job_not_found` (not stored).
6. Job `filing` → resume → `200` with `action: "resumed"`.
7. Job `failed`: source unavailable or unstable → `409 source_unavailable`; source bytes
   differ → `409 source_changed`; otherwise requeue → `200` with `action: "requeued"`.
8. Any other status (`ready`, `classified`, `review`, `completed`, …) → `409 job_not_retryable`.

### Response `200`

The section 4 payload plus `action`:

```json
{"action": "resumed", "job": {"status": "completed", "last_error_code": null, "…": "…"},
 "page_accounting": {"complete": true, "…": "…"}, "operation": {"…": "…"},
 "recovery": {"state": "none", "retryable": false}, "files": ["…"]}
```

A resume can end in `filing` again when a transient condition persists (for example
`last_error_code: "source_unavailable"`, `recovery.state: "resumable"`). Retry later with a
**new** key.

| `action` | Job afterwards |
|---|---|
| `resumed` | `completed`, `review`, `filing` (still transient) or `failed` (fail-closed during resume) |
| `requeued` | `ready`, `last_error_code: null` |

### Idempotency

- The same `(archive_scope_id, job_id, idempotency_key)` returns the **stored** outcome: the
  identical `200` body captured at completion, or the same `409` code. It does not run again,
  even if the job or source has changed since. Use a new key to try again.
- `422`, `404` and `503` responses are not stored.
- If the process stops mid-retry, the same key runs the retry again; resume and requeue are
  both safe to repeat.
- Retries never create a second filed PDF or a second retained original, and never remove a
  source that has not been verified in the archive.

### Errors

| HTTP | `code` | Message |
|---|---|---|
| 404 | `scope_not_found` | `Archive scope is not registered.` |
| 404 | `job_not_found` | `Job not found in this scope.` |
| 409 | `job_not_retryable` | `Job is not in a retryable state.` |
| 409 | `source_changed` | `The source no longer matches the admitted scan.` |
| 409 | `source_unavailable` | `The source is not available yet; retry later.` |
| 422 | `invalid_request` | `Request body does not match the contract.` |
| 503 | `state_unavailable` | `Local application state is not configured.` |

## 6. Error codes on jobs and steps

| Code | Where | Retryable | Meaning |
|---|---|---|---|
| `source_unavailable` | `job.last_error_code` (job stays `filing`) | yes (resume) | source missing, iCloud placeholder or dataless, partial download, or still changing |
| `output_verification_failed` | job (`filing`) | yes | staged or copied output failed PDF, page-count or content-hash verification (for example a partial write); nothing was placed |
| `original_verification_failed` | job (`filing`) | yes | retained original bytes did not verify |
| `output_unverified` | job (`filing`) | yes | before source cleanup, a recorded output or the retained original no longer verified; the source was kept |
| `io_error` | job (`filing`) | yes | filesystem error such as an archive volume error; the source is kept |
| `collision_limit_exceeded` | job (`filing`) | yes | more than 10,000 names taken |
| `source_changed` | job and steps (`failed`) | requeue rejected with 409 | source bytes differ from admission; nothing further written, source untouched |
| `unsafe_destination` | job, and steps if planning had finished (`failed`) | requeue allowed if the source is unchanged | destination not archive-relative, not a visible `.pdf`, a review suggestion invalid, or a directory component became a symlink |
| `page_accounting_invalid` | job (`failed`) | requeue allowed if the source is unchanged | assignments rejected before journaling |

Failures never delete or overwrite files. Outputs that were already verified and recorded
stay in place; a requeued job's next filing run finds them as exact duplicates.

## 7. Path rules

- Every returned path (`destination_relative_path`, `relative_directory`, `relative_path`,
  `near_duplicate_of`) is archive-relative POSIX: no leading `/`, `~` or drive, no backslash,
  no `.` or `..` segment. Resolve against the selected scope's `canonical_root` only.
- Filed destinations: directory as above; filename a single component that ends in `.pdf`
  (case-insensitive) and does not start with `.`. Every existing component under the scope
  root must be a real directory, not a symlink, when planned and again when placed.
- Collision names insert `_<n>` (n ≥ 2) before the final extension:
  `Statement.pdf` → `Statement_2.pdf`.
- Source locators are opaque (`watch:<relative>`, `upload:<name>`, `archive:<relative>`) and
  are not returned by these routes.

## 8. Service contracts (Python, for backend callers)

| Contract | Module |
|---|---|
| `check_input(path, *, observations=3, interval=1.0, sleep, stat, count_pages) -> StabilityResult(state, page_count, size, raw_sha256)`; `state` is `ready`, `missing`, `zero_byte`, `malformed`, `changing`, `download_not_ready` or `unavailable`; `.retryable` is `state != ready` | `docflow/ingestion/stability.py` |
| `fingerprint_pdf(path) -> SourceFingerprint(raw_sha256, page_count, pages[PageFingerprint(page_number, content_sha256, dhash64)], document_sha256)`; `hamming_distance`, `NEAR_DUPLICATE_MAX_DISTANCE = 2` | `docflow/ingestion/loader.py` |
| `DurableFiler(store, *, source_roots={"watch": Path, "upload": Path})` with `admit(scope_id, locator) -> Admission(state, retryable, job_id)`, `file_job(scope_id, job_id, [DocumentAssignment], *, original_relative_directory) -> FilingResult`, `resume(scope_id, job_id)`, `reconcile(scope_id) -> [FilingResult]` (call after `StateStore.recover_after_restart`), `retry(scope_id, job_id, *, idempotency_key) -> dict` | `docflow/filing/operations.py` |
| `DocumentAssignment(pages, outcome, relative_directory=None, filename=None, reason=None, confidence=0.0)`; `validate_page_accounting(page_count, assignments)` raises `PageAccountingError` | `docflow/filing/operations.py` |
| `job_payload(store, scope_id, job_id) -> dict` (section 4 body) | `docflow/filing/operations.py` |
| `configure_state(store, filer=None)` attaches both to the web app | `docflow/web/app.py` |

Re-admitting the same unchanged source at the same locator returns the existing unfinished
job. Jobs imported by the Phase 1 legacy migration (no page fingerprints) are never reused;
their pending review items and history are left untouched.

## 9. Legacy adapters (signatures unchanged)

- `extract_documents(source_pdf, decisions, config)` stages and verifies outputs outside the
  archive, uses collision-safe names, treats only rendered-identical files as duplicates
  (returning the existing path and setting `decision.notes`), rejects duplicate,
  out-of-range or empty page lists before writing, and **no longer writes
  `filing_log_*.json` to the archive**. It does not require every page to be covered,
  because callers pass only auto-filed decisions.
- `archive_original(pdf_path, config)` never replaces an existing original: it uses
  `name_2.pdf`, … or keeps an existing byte-identical copy, and removes the source only after
  the retained copy verifies.
- `docflow.filing.dedup` no longer reads or writes `~/.docflow/content_hashes.json`. Without a
  state store: `is_empty() → False`, `build_initial_index(root) → 0`,
  `is_duplicate(path) → (False, None)`, `register_file(path) → None`. With
  `store=`/`scope_id=`, the index is `file_records` (paths with any component starting with
  `_` or `.` and symlinks are skipped; existing records are never altered), and `is_duplicate` returns
  `(True, relative_path)` only when the recorded file still renders identically.
- The legacy web pipeline removes a watch-folder file with the upload's name only if it is
  byte-identical to the retained original.
- Existing `content_hashes.json`, `review_queue.json` and `filing_log_*.json` files are left
  untouched. Phase 1 migration still inventories them for deferred cleanup.

## 10. Not part of Phase 3

- No UI changes and no UI design. `review.html` and `/api/process*` do not use these
  routes yet.
- No undo (`POST /api/v1/operations/{id}/undo`) or compensating deletes. That is Phase 4.
- No `POST /api/v1/jobs` (admission over HTTP) and no archive-scope registration route.
  Admission is a backend service call. Unstable inputs are simply re-observed later.
- The legacy CLI and web pipelines are not yet driven by `DurableFiler`. They use the safe
  adapters in section 9. They still write `review_queue.json` and `MailArchivingSummary.*`
  to the archive root; moving those to the state plane is outside this phase.
- No action tracking, no automatic payment or reply.
- Mac acceptance (real iCloud eviction, `UF_DATALESS`, APFS hard links in iCloud Drive) is
  pending human execution.
