# DocFlow Privacy-First Reliability Plan

Status: Reviewed and approved by Phase 0.5 Mixture of Agents gate (DeepSeek V4 Flash + Gemini 3.6 Flash). All 6 blocking findings (PLAN-B01 through PLAN-B06) and 5 non-blocking findings (PLAN-N01 through PLAN-N05) have been incorporated.

## 1. Product brief

### User and job
DocFlow is a single-user personal mailroom running on the user's Mac laptop. The user scans a pile, receives faithfully retained PDFs in an iCloud-synced archive, and reviews only genuine exceptions. The web interface is local at localhost; no remote UI is exposed.

### Current release goal
Make the existing application safe and dependable enough to become the default path for personal mail filing:

1. protect document content before every optional cloud-model call;
2. keep operational state outside the iCloud archive;
3. make ingestion, deduplication, filing, recovery, and undo durable and duplicate-safe;
4. preserve the existing product and local-browser workflow rather than rewrite it.

### Explicit non-goals
No commercial packaging, multiuser support, hosted service, public UI, broad redesign, automatic payment, automatic reply, or action-tracking implementation in this release.

### Later milestone only
Actionable requests, deadlines, overdue flags, a short batch email, and persistent done/dismiss/defer actions may follow after this release. Every action must link to its source document. An amount due does not prove a bill is unpaid. No automatic payment or response is permitted.

## 2. Verified current-tree findings

Verified locally against main at 8da0c3d66c0bebe7cee9f6505731563d5338dd5b. These are planning observations, not claims that fixes have run.

- `docflow/llm/client.py` sends prompts directly through an OpenAI-compatible client. No shared privacy gate exists. The installed OpenAI SDK 2.24.0 defines `DEFAULT_MAX_RETRIES = 2`; the current client does not override it, so automatic retries do exist.
- `docflow/clustering/clusterer.py` sends OCR text and extracted signals to the model and falls back to local rules on model failure. It checks set equality for page assignment but does not independently reject duplicate page numbers before the final assertion.
- `docflow/classification/classifier.py` sends raw previews plus rules, entities, family, and user settings. Returned confidence and destination values are insufficiently typed and constrained; destination construction can accept model-supplied path components.
- `docflow/web/app.py` contains additional direct model use for unmatched suggestions, accepts caller-supplied filesystem paths for processing/reclassification, stores active jobs only in memory, puts uploads/previews/log-derived state beneath the archive, and deletes a watch-folder copy after archiving.
- `docflow/filing/dedup.py` hashes extracted text and page dimensions. Distinct image-only pages of equal dimensions can collide because image bytes are omitted.
- `docflow/extraction/extractor.py` treats an existing same-name PDF with the same page count as a likely duplicate, even without content equivalence.
- `docflow/ingestion/archiver.py` moves originals to a date-named directory without collision-safe naming; an existing same-name destination is not protected.
- `docflow/review/queue.py` persists queue state and a raw text preview as JSON in the archive root.
- `docflow/web/static/review.html` invokes `showUndoSnackbar(..., null)` for filing/skip actions, so the visible Undo control has no durable operation behind it.

## 3. Safety invariants

These are release-blocking contracts.

1. The server never accesses a live mail archive, iCloud mount, actual scan, auth material, or lookup map during development or testing.
2. The Mac application binds only to loopback by default. No remote/public listener is part of this release.
3. Local OCR runs before any cloud call. Images, raw OCR text, rules, paths, entities, corrections, and learned examples never bypass the privacy gateway.
4. One gateway is the only application boundary allowed to call a cloud model. Classification, clustering, learning, Ask AI, connection tests, and future model features must use it.
5. The gateway uses random typed placeholders, consistent within one job. The lookup is memory-only by default and is never written to the archive or logs. If the application process crashes or restarts while a job is in awaiting_model or classified status and the memory-only lookup map is lost, the job automatically transitions back to ocr/privacy_blocked to re-execute local OCR and privacy pseudonymization with fresh memory-only placeholders. Re-execution preserves privacy while allowing safe idempotency and retry without un-pseudonymized leak or corruption.
6. No raw image or raw-text fallback is allowed. Privacy errors or unresolved detected sensitive content fail closed into a local review path.
7. Pseudonymization is not anonymity. Undetected PII remains a threat. A local-only mode is available and is the recommended mode for especially sensitive documents.
8. Task-relevant dates and amounts are preserved when safe; detectors must not claim perfection. Sensitive identifiers are replaced; dates/amounts are retained only under typed, tested policy.
9. Model output is untrusted data. It cannot introduce instructions, absolute paths, traversal, unknown rule/entity IDs, duplicate/out-of-range pages, non-finite confidence, or destinations outside the selected archive scope.
10. Every input page is accounted for exactly once as filed, pending review, intentionally skipped, or blocked with a visible reason.
11. Originals and successfully filed PDFs remain in the archive. Operational state, caches, logs, queues, and the live database remain outside iCloud.
12. Filing and undo are journaled, idempotent operations. No source deletion occurs until destination writes and state commits are verified.
13. No implementation stage may use Claude Code against the source repository history. Claude Code sees only the verified sanitized export with a synthetic one-commit history.

## 4. Canonical architecture decisions

### 4.1 Two storage planes

Archive plane (user-selected, iCloud-synced):
- original scans;
- final filed PDFs;
- no queue JSON, filing logs, preview cache, hash database, job state, placeholder map, API key, or live SQLite database.

Application-state plane (local Mac storage, outside iCloud):
- macOS: `~/Library/Application Support/DocFlow/`;
- Linux tests: `${XDG_DATA_HOME:-~/.local/share}/docflow/`;
- canonical path resolution supplied by `platformdirs` or an equivalent tested platform abstraction;
- `state.sqlite3`, a preview/cache directory, structured local logs with content redaction, exports/backups, and a process lock.

Secrets belong in the macOS Keychain or environment, not SQLite/YAML. Existing user configuration is imported non-destructively; shipped defaults and synthetic examples are distinct from user-owned settings.

### 4.2 Archive-scoped state schema

Use SQLite with foreign keys enabled and versioned migrations. One application process owns writes; a process lock prevents concurrent writers. WAL may be used only in local application storage, never in iCloud.

Required logical tables (names are canonical; implementation may add internal indexes/columns but may not change their meaning):

- `schema_migrations(version, applied_at, checksum)`
- `archive_scopes(id, canonical_root, root_fingerprint, created_at, last_seen_at)`
- `settings(archive_scope_id, key, value_json, updated_at)` excluding secrets
- `jobs(id, archive_scope_id, source_fingerprint, source_name, source_locator, status, attempt, created_at, updated_at, last_error_code)`
- `job_pages(job_id, page_number, content_sha256, perceptual_fingerprint, status)`; `(job_id, page_number)` unique. Canonical enum values for `status` are: `pending`, `filed`, `review`, `skipped`, `blocked`.
- `review_items(id, archive_scope_id, job_id, candidate_json, suggested_filename, suggested_relative_directory, confidence, status, created_at, updated_at)` with no raw OCR preview. Explicitly includes `archive_scope_id NOT NULL REFERENCES archive_scopes(id)`. When a job transitions to `privacy_blocked`, a corresponding `review_item` is created with status `pending` to permit local review, correction, approval, or skip via `/api/v1/review-items/{id}/*`.
- `file_records(id, archive_scope_id, job_id, relative_path, content_sha256, page_numbers_json, role, created_at)` where role is `original` or `filed`. Explicitly includes `archive_scope_id NOT NULL REFERENCES archive_scopes(id)` and `UNIQUE(archive_scope_id, relative_path)`.
- `operations(id, archive_scope_id, job_id, kind, status, request_json, result_json, created_at, completed_at, undone_at)` explicitly including `archive_scope_id NOT NULL REFERENCES archive_scopes(id)`.
- `operation_steps(id, archive_scope_id, operation_id, ordinal, kind, source_locator, destination_relative_path, expected_sha256, status, error_code)` explicitly including `archive_scope_id NOT NULL REFERENCES archive_scopes(id)`.
- `corrections(id, archive_scope_id, review_item_id, normalized_features_json, chosen_rule_id, chosen_relative_directory, created_at)`; no raw OCR or placeholder lookup.

Database connection rules: Every SQLite connection handle must execute `PRAGMA foreign_keys = ON;` upon initialization. Single-writer concurrency is enforced via an OS-level file lock (`filelock` / `fcntl.flock`) on `docflow.lock` located in the application-state directory.

Job statuses are: `discovered`, `stabilizing`, `ready`, `ocr`, `privacy_blocked`, `awaiting_model`, `classified`, `filing`, `review`, `completed`, `failed`, `undoing`, `undone`. Transitions are explicit and compare-and-set; restart resumes only idempotent steps.

`archive_scope_id` is required on every stateful record. API calls may never infer scope from an arbitrary client path.

### 4.3 Local HTTP API

Keep FastAPI and the existing local UI. Introduce `/api/v1` contracts while maintaining temporary adapters for existing endpoints until tests and UI migrate.

Canonical endpoints:

- `POST /api/v1/archive-scopes` selects and validates one configured archive root. Validation explicitly rejects non-existent paths, files, system root (`/`), user home root (`~`), system directories, and the application-state directory (`~/Library/Application Support/DocFlow`).
- `POST /api/v1/jobs` accepts an uploaded PDF handle or configured watch-folder candidate, not an arbitrary filesystem path.
- `GET /api/v1/jobs/{job_id}` returns durable status and page-accounting totals.
- `GET /api/v1/review-items?archive_scope_id=...&status=pending` returns local review data.
- `POST /api/v1/review-items/{id}/approve`
- `POST /api/v1/review-items/{id}/correct`
- `POST /api/v1/review-items/{id}/skip`
- `POST /api/v1/ask-ai` accepts pseudonymized queries with explicit `archive_scope_id`, routing exclusively through `CloudPromptGateway`.
- `POST /api/v1/connection-test` verifies model provider connectivity with a synthetic test payload with explicit `archive_scope_id`, routing through `CloudPromptGateway`.
- `POST /api/v1/operations/{id}/undo` performs durable compensating actions and reports partial failure.
- `POST /api/v1/jobs/{id}/retry` retries from the last safe checkpoint without duplicating files.
- `POST /api/v1/state/export` creates a redacted support export and a separately requested local state backup.

All mutating requests carry an idempotency key. Returned paths are archive-relative. The server resolves them beneath the selected canonical archive root and rejects symlink/path escape. UI content is escaped; document text is data, never executable prompt or HTML.

### 4.4 Privacy and model boundary

Create one `CloudPromptGateway` (name is canonical) and prohibit direct SDK imports outside its adapter package.

Pipeline:

1. local OCR and local structural signal extraction;
2. local sensitive-value detection using layered deterministic patterns plus a local recognizer where available;
3. type-aware policy deciding what must be replaced and what task-relevant date/amount may remain;
4. cryptographically random typed placeholders such as person/entity/account/address/phone/email identifiers, consistent for the job;
5. egress validation over the final serialized request, including rules and metadata;
6. cloud call with SDK automatic retries disabled (`max_retries=0`); any explicit retry must reuse the same job/idempotency metadata and sanitized payload;
7. strict response schema validation;
8. allowlist validation for placeholder IDs, page numbers, rule IDs, filenames, and relative destinations;
9. local rehydration only after validation.

The gateway never sends image bytes. Any detector/gateway failure or unresolved sensitive token blocks cloud use and returns a local review result. Local-only mode bypasses cloud calls entirely.

### 4.5 Durable filing, deduplication, and undo

- Stabilize an input before ingest: size/mtime unchanged across a bounded interval, PDF opens, page count is stable, and iCloud/download placeholders (detected via `.icloud` filename prefixes or macOS `UF_DATALESS` stat flag) are reported as unavailable rather than processed.
- Compute source and page fingerprints from canonical rendered page pixels (rendered at 150 DPI 8-bit grayscale PNG) plus page order. Exact page content fingerprinting uses SHA-256 of rendered page pixel bytes. Perceptual image fingerprinting uses 64-bit difference hashing (dHash); Hamming distance $\le 2$ identifies duplicate image candidates and routes to review. Raw-file SHA-256 remains evidence, not the sole semantic duplicate key.
- A same filename/page count is never sufficient for duplicate classification.
- Stage outputs in application-local temporary storage or a destination-local atomic staging file; verify PDF/page count/hash before atomic rename.
- Resolve filename collisions deterministically without overwrite. Preserve the original with a unique collision-safe name.
- Journal planned steps before file mutation. Commit file records only after verification. On restart, reconcile journal, filesystem, and hashes and continue or roll back without duplicates.
- Undo is a durable compensating operation. It moves only files created by the named operation, verifies hashes, restores review/job state, and reports partial failures without deleting unknown or modified files.

### 4.6 Migration and backup

First launch with legacy state:

1. acquire the single-writer lock;
2. inventory legacy queue/hash/log/config files without mutating them;
3. create a timestamped application-state backup outside the archive;
4. extract any legacy API keys/secrets from YAML configuration files, write them to macOS Keychain or secure local environment configuration, and strip secrets from the payload imported into the SQLite `settings` table; move or disable legacy operational files (`.docflow_queue.json`, legacy logs) inside the archive root to `.migrated` state so they are ignored by operational queries;
5. import into a new database transaction with archive scope and validation;
5. reconcile referenced PDFs read-only and report missing/unavailable items;
6. mark migration complete only after row counts, foreign keys, page accounting, and checksums pass;
7. leave legacy files untouched until explicit cleanup after successful Mac acceptance;
8. rollback restores the pre-migration application state and does not alter archive PDFs.

Migration is idempotent: repeating it produces no duplicate jobs, review items, file records, or corrections. Pending reviews and history are retained.

State backup copies SQLite through its backup API while the writer is quiesced. Exported diagnostic bundles are redacted and exclude OCR text, placeholder maps, secrets, and absolute personal paths.

## 5. Delivery phases

### Phase 0 — deterministic sanitization (backend tools only)
Sanitize tracked examples into coherent synthetic fixtures, add a deterministic current-tree privacy preflight, and create a synthetic-history `sanitized-workspace`. No LLM sees repository content before this gate passes.

Exit: privacy scan passes without raw values in reports; safe tests pass; source sanitization commit exists locally; export manifest verifies; historical exposure is noted without reproducing it.

### Phase 0.5 — multi-model plan gate
Freeze this plan plus the sanitized Phase 0 code-finding bundle. Run Hermes MoA with distinct DeepSeek V4 Flash and Gemini 3.6 Flash references (or block if either exact available model cannot be verified) and a consolidating aggregator. Preserve frozen-input hash, preset/config, trace, full advisor outputs, consolidated verdict, and a finding-to-disposition matrix. Incorporate every accepted blocking correction into this file before Phase 1.

Exit: no unresolved blocking plan finding; sanitized trace audit passes; review artifacts contain no personal data; final plan is committed to the synthetic workspace.

### Phase 1 — state foundation and migration
Claude Code implements the application-state root, archive-scoped SQLite schema, single-writer lock, repository abstraction, legacy inventory/import, backup/rollback, and `/api/v1` job/review primitives. Backend orchestrates Claude against `sanitized-workspace`, verifies tests, then imports a controlled patch into the source branch without exposing source history to Claude.

Exit: migration/idempotency/rollback tests pass; no application state is written inside archive fixtures; existing settings are not erased; restart preserves pending reviews/history.

### Phase 2 — privacy gateway and validated AI paths
Claude Code implements local detection/pseudonymization, memory-only per-job lookup, egress gate, local-only/fail-closed behavior, strict response schemas, destination/rule/page validation, prompt-injection treatment, and routes every model feature through the gateway. Remove direct cloud SDK use elsewhere and disable SDK automatic retries.

Exit: intercepted outgoing-request integration tests prove that clustering, classification, unmatched suggestions, learning, Ask AI (if present), and connection tests cannot send raw text/images/rules/entities/corrections; adversarial synthetic cases fail closed; local-only mode produces zero network calls.

### Phase 3 — ingestion, filing, dedup, and recovery
Claude Code implements input stabilization, unavailable-iCloud handling, pixel/content fingerprints, exact collision handling, original retention, page accounting, staged atomic writes, operation journal, restart reconciliation, and duplicate-safe retries.

Exit: distinct image-only documents no longer collide; same-page-count collisions do not skip; original naming never overwrites; every page has one outcome; simulated sleep/restart and partial write recovery do not duplicate or lose files.

### Phase 4 — durable review actions and undo UI
Claude Code migrates the current UI to durable job/review/operation endpoints, replaces null Undo callbacks with real operation IDs, handles partial undo failures visibly, and preserves the localhost workflow. No visual redesign beyond states required for safety and recovery.

Exit: approve/correct/skip/batch filing actions can be undone durably after reload; changed files are not silently deleted; missing/modified targets surface actionable errors; UI renders model/document content safely.

### Phase 5 — independent verification and PR handoff
A non-implementing reviewer audits the exact sanitized commit and imported source commit, tests, migration evidence, cloud-egress captures, path confinement, artifact redaction, and scope. Backend resolves blocking findings through Claude Code, reruns the gate, and prepares one PR from the local source branch. Do not merge without user approval.

Exit: exact-head required CI passes; focused and full safe suites are recorded; no secret/PII findings; no real provider/document/email calls; install/update/rollback instructions and a small Mac checklist are attached. Linux testing is reported as Linux only; Mac desktop acceptance remains pending human execution.

## 6. Acceptance test catalogue

Privacy:
- P01 synthetic names, addresses, emails, phone numbers, account numbers, legal entities, rules, paths, and correction examples are consistently replaced per job.
- P02 repeated sensitive values map consistently within a job and differently across jobs.
- P03 intercepted requests across every cloud feature contain no raw synthetic sentinel and no image bytes.
- P04 unresolved sensitive content, detector failure, serialization failure, and gateway bypass attempt produce no network call and a local review item.
- P05 dates/amounts required for filing survive only under explicit policy; account/date-like ambiguity is tested.
- P06 adversarial document text that says to ignore rules or choose an absolute/traversal path cannot alter instructions or escape the archive.
- P07 responses with unknown placeholders/rules, duplicate/out-of-range pages, invalid confidence, absolute paths, traversal, symlink escape, or extra fields are rejected.
- P08 local-only mode completes local OCR/review and records zero network calls.

Storage/migration:
- S01 application database, WAL, logs, queue, cache, and backups are outside archive fixtures on macOS-path and Linux-path simulations.
- S02 migration is repeatable with identical row counts/IDs and retains pending reviews/history.
- S03 migration failure restores prior local state and leaves all archive PDFs byte-identical.
- S04 concurrent second writer is rejected cleanly.
- S05 backup/restore passes SQLite integrity and foreign-key checks and excludes secrets/maps/raw OCR.

Reliability:
- R01 two equal-dimension image-only PDFs with different pixels have different page/document fingerprints.
- R02 same filename and page count but different content produces a collision-safe second file, not a skip.
- R03 exact duplicate input is recognized without creating another filed PDF.
- R04 pre-existing original destination never overwrites; both originals remain addressable.
- R05 malformed, copying, zero-byte, changing-size, download-not-ready, and unavailable files remain retryable and unmodified.
- R06 every source page appears exactly once across terminal outcomes; missing and duplicate page assignments fail closed.
- R07 crash/sleep at each journal boundary resumes or compensates without duplicate or lost files.
- R08 approve/correct/skip/batch operations survive restart and durable undo works after browser reload.
- R09 undo with a missing or user-modified target reports partial failure and deletes nothing unverified.
- R10 AI branches are exercised with valid, malformed, empty, timeout, retryable, duplicate-page, unknown-ID, and path-escape responses.

Release/safety:
- X01 all tests use synthetic temporary roots; no default real archive path is opened.
- X02 no test sends real provider requests or outbound email.
- X03 server bind defaults to loopback and rejects arbitrary filesystem handles.
- X04 tracked-tree and staged-diff privacy/secret scans pass without printing matched values.
- X05 exact PR head matches independently reviewed commit and required CI.
- X06 Mac checklist remains explicitly PENDING until the user executes it.

## 7. Definition of done

The release is done only when the dependency chain reaches a reviewed PR handoff and all Phase 1–5 exit criteria are evidenced. A green Linux suite alone is insufficient. No card may describe planned tests as passed. No merge, public deployment, history rewrite, live archive access, real document transmission, or outbound email is authorized.