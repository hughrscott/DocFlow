# OCR text, page preview and intake API contract

Status: implemented in the sanitized workspace (Linux-tested only; Mac acceptance pending).

This is the complete frontend contract for the three repairs in this change: per-page
**text extraction**, per-page **previews**, and the single **Add scanned mail** intake.
It extends, and never breaks, `docs/phase-4-api-contract.md` (durable review actions and
undo) and `docs/phase-3-api-contract.md` (`GET /api/v1/jobs/{id}` and retry). Everything
those documents say about conventions — loopback-only serving, explicit
`archive_scope_id`, strict JSON bodies, idempotency keys, archive-relative paths, the
`{"error": {"code", "message"}}` envelope, and the safe-rendering rules of section 2 —
applies here unchanged.

A client that implements this document needs no follow-up question to build the review
text pane, the page preview pane, and the dashboard intake flow.

---

## 1. What is new

| Route | Purpose |
|---|---|
| `GET /api/v1/review-items` (extended) | adds `source_page_range`, `text_extraction` and an optional `job_id` filter |
| `GET /api/v1/review-items/{item}/pages/{page}/text` | the locally extracted text of one physical source page |
| `GET /api/v1/review-items/{item}/pages/{page}/preview` | a PNG render of one physical source page |
| `POST /api/process` (extended) | accepts `submission_key`; returns `reused` |
| `GET /api/process/status/{id}` (extended) | acknowledged filename, truthful stages, completion counts, job-scoped review URL, capability text |

### Page numbering — read this first

Everywhere in this contract, **`{page}` is the 1-based physical page number of the source
PDF**, exactly as `page_numbers` reports it. It is never an index into the review item.
An item covering `page_numbers: [3, 4]` is addressed as `/pages/3/…` and `/pages/4/…`;
`/pages/1/…` is `404 page_not_found` even though page 1 exists in the same job.

Label pages for the user the same way (`Source page 4 of 3-4`), never `Page 2 of 2`.

### Privacy boundary

Raw OCR text is local application state. It is stored in exactly one place
(`job_pages.ocr_text` in the local SQLite state, outside the archive) and leaves that
state through exactly one response: the page-text route below. It never appears in
candidate JSON, review-item listings, archive files, logs, filing journals, model
payloads or model artifacts. Preview renders and OCR/render temporary files live under
application state (`<state>/cache/…`) and never in the archive.

---

## 2. `GET /api/v1/review-items` (extended, backward compatible)

```
GET /api/v1/review-items?archive_scope_id=<id>&status=pending&job_id=<optional>
```

`status` is optional (default `pending`; one of `pending`, `approved`, `corrected`,
`skipped`). `job_id` is optional, 1–128 characters. Read-only; no idempotency key.

Every Phase 4 field is unchanged. Two fields are added to each item, and the filter is
echoed at the top level:

```json
200 {
  "archive_scope_id": "3f2b…",
  "status": "pending",
  "job_id": "6b0d…",
  "items": [{
    "id": "c1…", "job_id": "6b0d…", "status": "pending",
    "source_name": "scan.pdf",
    "page_numbers": [3, 4],
    "source_page_range": "3-4",
    "text_extraction": "mixed",
    "reason": "unmatched",
    "near_duplicate_of": null,
    "doc_type": null, "period": null,
    "suggested_filename": null,
    "suggested_relative_directory": null,
    "confidence": null,
    "actions": ["correct", "skip"],
    "created_at": "2026-09-13T21:01:20+00:00",
    "updated_at": "2026-09-13T21:01:20+00:00"
  }]
}
```

| Field | Meaning |
|---|---|
| `job_id` (top level) | the filter that was applied, or `null` when none was sent |
| `source_page_range` | compact label for `page_numbers`: `"2"`, `"3-4"`, `"1, 3-5"`. `null` when the item has no pages. Consecutive runs are collapsed; the separator is an ASCII hyphen |
| `text_extraction` | aggregate OCR state of the item's pages (below) |

`confidence` is `null` for this item because `reason` is `unmatched`: no filing rule
matched and no model result was accepted, so nothing ever scored it. See
`docs/phase-4-api-contract.md` for the field; a document that really was classified —
including a low-confidence cloud classification — keeps its number.

**Aggregation rule for `text_extraction`** — deterministic, in this order:

1. the item has no pages → `"missing"`;
2. **any** page is `missing` → `"missing"`;
3. otherwise all pages share one status → that status (`extracted`, `no_text`, `failed`);
4. otherwise → `"mixed"`.

`missing` dominates deliberately: a partially-read document must never look finished.

**Job filter semantics.** An unknown `job_id`, or one belonging to a different archive
scope, is not an error. The response is a normal, correctly scoped, **empty** collection
(`"items": []`) that echoes the requested `job_id`. This never reveals whether a job
exists in another scope.

No raw OCR text is returned by this route.

---

## 3. `GET /api/v1/review-items/{item}/pages/{page}/text`

```
GET /api/v1/review-items/c1…/pages/3/text?archive_scope_id=3f2b…
```

`{page}` must be a physical source page belonging to `item.page_numbers` (see §1).

```json
200 {
  "review_item_id": "c1…",
  "job_id": "6b0d…",
  "page_number": 3,
  "status": "extracted",
  "text": "SYNTHETIC UTILITY STATEMENT\nPERIOD FEBRUARY 2026",
  "error_code": null
}
```

The four states are exhaustive and mutually exclusive. A client must render a **distinct**
message for each; they are not interchangeable.

| `status` | `text` | `error_code` | Meaning | Suggested UI text |
|---|---|---|---|---|
| `extracted` | non-blank string | `null` | OCR read this page | render the text |
| `no_text` | `null` | `null` | OCR ran and found no text (blank or purely an image) | this page has no text to show |
| `failed` | `null` | short stable code | OCR failed on this page; no text was stored | this page could not be read |
| `missing` | `null` | `null` | no OCR result is stored for this page yet | this page has not been read yet |

`error_code` is a short, stable, path-free token (`^[a-z][a-z0-9_]{0,63}$`, for example
`ocr_engine_error`). It is safe to show, and safe to branch on. It never contains a file
path, a user name or a provider message.

`text` is document content: render it with `textContent` / `createTextNode`, never with
`innerHTML`. So is `error_code`.

**Errors**

| HTTP | `code` | Message |
|---|---|---|
| 422 | `invalid_request` | `Request does not match the contract.` (missing/oversized `archive_scope_id`, non-numeric `{page}`) |
| 404 | `scope_not_found` | `Archive scope is not registered.` |
| 404 | `review_item_not_found` | `Review item not found in this scope.` |
| 404 | `page_not_found` | `Page not found in this review item.` |
| 503 | `state_unavailable` | `Local application state is not configured.` |

A review item that exists in a **different** scope returns exactly the same body as one
that does not exist at all (`review_item_not_found`), so cross-scope existence is never
disclosed.

Results are durable: the text is still there after a process restart and a browser reload.

---

## 4. `GET /api/v1/review-items/{item}/pages/{page}/preview`

```
GET /api/v1/review-items/c1…/pages/3/preview?archive_scope_id=3f2b…
```

`200` returns `image/png` bytes (`Content-Type: image/png`) — the page rendered at the
canonical 150 DPI grayscale used for page fingerprints. Set it directly as an `<img>`
`src`; it is a plain same-origin GET.

**What the server checks, in this order.** The order matters and is part of the contract:

1. **The active scope is authorized first**, before the item is looked up. Only the scope
   returned by `GET /api/v1/archive-scopes/active` may render previews. Any other value —
   registered elsewhere or not registered at all — is `404 scope_not_found` with an
   identical body, so nothing about the item is revealed.
2. `{page}` must be numeric, must belong to `item.page_numbers`, and must be a page of the
   job (bounds).
3. The retained original is resolved **only** through scope → job → file records. No
   caller-supplied path is ever accepted, and no absolute path is ever returned.
4. Its archive-relative path must resolve beneath the scope's canonical root with **no
   symlinked component**, and the target must be a **regular, non-symlink file**.
5. Its bytes must still match the digest recorded when the scan was admitted.
6. The rendered page must still match the per-page fingerprint recorded at admission.

Any failure of 3–6 is one fixed error, `409 source_unavailable`, with no detail about
which check failed.

**Caching and publication.** A successful render is cached in application state under
`<state>/cache/review-previews/<scope>/<job>/<page>-<digest>.png`, where `<digest>` is the
page fingerprint recorded at admission — so a changed original can never be served from an
earlier render. The file is written to a unique `.partial` name in the same directory and
published with an atomic rename, so a reader never observes a half-written PNG. No preview
byte, temporary file or directory is ever written into the archive.

**Errors**

| HTTP | `code` | Message |
|---|---|---|
| 422 | `invalid_request` | `Request does not match the contract.` |
| 404 | `scope_not_found` | `Archive scope is not registered.` |
| 404 | `review_item_not_found` | `Review item not found in this scope.` |
| 404 | `page_not_found` | `Page not found in this review item.` |
| 409 | `source_unavailable` | `The retained original for these pages is unavailable.` |
| 409 | `preview_unavailable` | `The page preview could not be rendered.` (the renderer failed; nothing was cached) |
| 503 | `state_unavailable` | `Local application state is not configured.` |

---

## 5. `POST /api/process` — one submission, one job

```json
{"path": "<path returned by POST /api/upload>", "submission_key": "submission-<uuid>"}
```

`path` is unchanged from Phase 4 §9: the path returned by `POST /api/upload`, or a PDF
inside the configured watch folder. Anything else is `400`. `submission_key` is new and
optional: a string of 1–128 characters identifying **one user submission**.

```json
200 {"job_id": "9f3c1a2b", "reused": false}
```

`job_id` is the in-memory progress ID (poll it at `/api/process/status/{job_id}`).
`reused` is `true` when the call was answered with a job that already exists.

**Guard semantics**

- With a `submission_key`: the key maps to exactly one job, for as long as the process
  lives. Repeated events, retries and rapid or concurrent duplicate calls all return that
  same `job_id` with `reused: true` and start **no** second pipeline run — including after
  the run has finished.
- Without a `submission_key`: the caller supplied no submission identity, so only a run
  that is **still in flight** for the same resolved source is reused. Submitting the same
  source again after it finished is treated as a deliberate new submission.
- A deliberate new submission always remains possible: send a new `submission_key`.
- The claim and the pipeline start happen under one lock, so simultaneous duplicate
  requests cannot both start a job.
- An invalid `path` is rejected (`400`) **before** any submission is recorded.

Clients should generate one key per user action, exactly like `idempotency_key`:
`submission-${crypto.randomUUID()}`.

`POST /api/upload`, `GET /api/process/active`, `GET /api/process/stream/{id}` and the
`/api/v1/jobs` routes are unchanged. No new absolute path is exposed by any of them.

---

## 6. `GET /api/process/status/{job_id}` (extended)

```json
200 {
  "status": "completed",
  "filename": "monthly-mail.pdf",
  "pdf": "monthly-mail.pdf",
  "progress": 100,
  "step": "Done",
  "stage": "done",
  "stages": [
    {"id": "checking",    "label": "Checking the scan",             "status": "done"},
    {"id": "ocr",         "label": "Reading pages with OCR",        "status": "done"},
    {"id": "grouping",    "label": "Grouping pages into documents", "status": "done"},
    {"id": "classifying", "label": "Matching filing rules",         "status": "done"},
    {"id": "filing",      "label": "Filing documents",              "status": "done"},
    {"id": "done",        "label": "Done",                          "status": "done"}
  ],
  "documents": [],
  "documents_total": 2,
  "auto_filed": 1,
  "review_queue": 1,
  "durable_job_id": "6b0d…",
  "review_url": "/review?job_id=6b0d…",
  "capabilities": {
    "text_extraction": true,
    "ai_classification": false,
    "summary": "OCR runs locally. AI classification is off."
  },
  "started": "2026-09-15T21:01:20"
}
```

| Field | Meaning |
|---|---|
| `filename` | the accepted file name, present from the first status read; `pdf` is a legacy alias |
| `stage` / `stages` | see below |
| `documents_total`, `auto_filed`, `review_queue` | completion counts |
| `documents[].confidence` | the decision's confidence, or `null` when nothing classified the document (`rule` is `none`) |
| `durable_job_id` | the durable job, once it exists; `null` before that |
| `review_url` | a job-scoped review URL (`/review?job_id=…`), or `null` |
| `capabilities` | what this install actually does |

**Stages are truthful.** `stages` is derived from the stage table the pipeline itself
executes (`docflow.filing.processing.PROCESS_STAGES`), so the UI cannot advertise a step
that does not happen. There are exactly six IDs — `checking`, `ocr`, `grouping`,
`classifying`, `filing`, `done` — and no `summary` or `archive` stage, because run summary
files are no longer produced and retaining the original is part of filing. Each entry's
`status` is `done`, `active` or `waiting`; reaching `done` marks every stage `done`.
**Render the stage list from this array**; do not hard-code stage names or infer them from
`progress`.

**Capabilities are honest.** `text_extraction` is `true` (local OCR always runs).
`ai_classification` is `false` in local-only mode and `true` otherwise, and `summary` is
the matching sentence. The copy promises text extraction and classification only — it does
**not** promise AI-generated titles or file names, and clients must not add such a claim.

---

## 7. Reference UI behavior

**One Add scanned mail flow.** There is exactly one intake control: the dashboard file
input. The global **Scan Mail** button focuses that input when the dashboard is open and
otherwise navigates to the dashboard. It never uploads or starts processing itself
(`shared.js` contains no `/api/upload` or `/api/process` call).

**Intake.** Selecting or dropping a file acknowledges its name immediately, before any
network call, and this survives a failed upload. Acknowledging a file also clears the
previous batch — completion summary, review link, capability line and document list —
synchronously, before the upload starts, so no previous-job action can remain beside the
newly selected file name even when that upload then fails. The input is disabled while a
submission is in flight and re-enabled when the run finishes, so repeated change/drop
events cannot start a second job; one `submission_key` is generated per file and the
server enforces the same rule.

**Recovery.** A page loaded while a job is running re-attaches to it — from the job the
tab saved, or from `GET /api/process/active` — and takes the same submission guard
*before* it starts polling, so a reload or a navigation back to the dashboard during
processing cannot start a second intake. The guard is released when that run reaches a
terminal state: completed, error, or a status the server no longer knows (`404`). Progress renders `stages` and `capabilities` from the server. A document the run never
classified carries `confidence: null` and is labelled *Not classified* in neutral styling,
never as a `0%` match; a document that was classified shows its real percentage. On completion the
page shows the counts and a **Review this batch** action linking to `review_url`; each
document still awaiting review links to the same scoped URL. Only same-origin paths
beginning with a single `/` are ever linked.

**Review page.** `/review` reads an optional `job_id` query parameter and applies it to
the list call, so `Review this batch` opens just that batch. Labels use
`source_page_range` (`Source pages 3-4`) and the page navigator names the physical page
(`Source page 4 of 3-4`). Selecting a page fetches that page's own scoped text and sets
the preview `<img>` to that page's scoped preview URL. Each of `extracted`, `no_text`,
`failed` and `missing` renders its own message. Every document-derived string — page text,
error codes, page ranges, file names, folders — is written with `textContent`, never as
markup.

---

## 8. Data model and migration

Schema version 2 adds three columns to `job_pages`, and nothing else:

| Column | Type | Meaning |
|---|---|---|
| `ocr_status` | `TEXT NOT NULL DEFAULT 'missing'`, checked against `missing`/`extracted`/`no_text`/`failed` | the page's OCR outcome |
| `ocr_text` | `TEXT NULL` | the extracted text; the only place raw OCR text is stored |
| `ocr_error_code` | `TEXT NULL` | a short stable code when the page failed |

- The migration is additive and backward compatible. **Every pre-existing row becomes
  `missing` with no text and no error**; there is no historical backfill, and no existing
  job, original, filed file or review item is modified or lost.
- A verified pre-upgrade copy of the database is kept under `<state>/backups/` so the
  previous DocFlow version can be restored; older code refuses a v2 file rather than
  misreading it.
- Repository writes enforce the shapes in §3: `extracted` ⇒ non-blank text and no error;
  `no_text` and `missing` ⇒ neither; `failed` ⇒ no text plus a bounded stable error code.
- OCR results are written **one complete batch per job, in a single transaction**, after
  the analyzer returns every page and before clustering. An analysis that aborts early
  commits no partial batch and preserves the real processing failure; no text is invented
  and no error is swallowed.

---

## 9. Limitations (honest status)

- Linux-tested only. macOS acceptance — launching on loopback, rendering previews with the
  Mac Poppler/Tesseract builds, and iCloud behavior — is pending human execution.
- Previews and page text cover durable review items only. The legacy Unmatched tab still
  uses `GET /api/preview/file`, and has no per-page text.
- `text_extraction` and page text reflect what OCR stored; pages processed by an earlier
  DocFlow version report `missing` until that scan is processed again.
- The retired `/api/preview/{item}/{page}` route is gone and is not reintroduced here.
