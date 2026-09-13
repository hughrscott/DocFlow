# Phase 2 API contract — model features behind CloudPromptGateway

Status: implemented in the sanitized workspace (Linux-tested only). This contract covers
the backend behavior a frontend may rely on. It does not design UI.

> **Pseudonymization is not anonymity.** DocFlow replaces detected names, entities,
> addresses, phone numbers, emails, account numbers, rules and paths with random
> placeholders before any model call, but detection is not perfect and undetected
> personal data can still reach the model provider. Use local-only mode for especially
> sensitive documents.
>
> The exact string is returned as `privacy.warning` by both `/api/v1` routes below and as
> `privacy_warning` by `GET /api/settings`. The UI should show it wherever cloud mode can
> be enabled or a model feature is triggered.

## 1. Guarantees behind every model route

All model use in the application goes through `docflow.llm.gateway.CloudPromptGateway`.
Only `docflow/llm/client.py` (the gateway's provider adapter) may import or call a provider
SDK; a test guard enforces this and enumerates every gateway call site, `Feature`, and web
route that reaches the gateway.

| Step | Behavior |
|---|---|
| Local first | OCR and signal extraction run locally. Page images never leave `_ocr_first_page`/OCR modules. |
| Detection | Layered local detection: the user's own configured values (names, entities, addresses, account hints, paths, rule IDs/paths/templates, including `rules.md`), typed patterns (email, phone, account-like digit runs, street, PO box and ZIP addresses, absolute, home-relative (`~`) and traversal paths, legal-entity suffixes, labeled or titled person names), then optional local recognizers. Text is NFKC-normalized, invisible format characters are removed and common Cyrillic/Greek confusables are folded before detection. |
| Policy | Identifiers are replaced. Dates, amounts and years are kept only when unambiguous. Values right after account-style labels (`account`, `acct`, `card`, `ending in`, `policy`, `member`, `no.`, `#`, masked `xx`/`**`) are masked as accounts. Birth dates are masked. Numeric strings that are not a valid date/amount/year (for example `03152026`) are masked. Government IDs (SSN pattern), API keys, private keys and credential URLs are never sent: the request is refused. |
| Placeholders | `TYPE_` + 12 random characters from `bcdfghjkmnpqrstvwxz` (for example `PERSON_…`, `ENTITY_…`, `ADDRESS_…`, `PHONE_…`, `EMAIL_…`, `ACCOUNT_…`, `PATH_…`, `RULE_…`, `DATE_…`). Generated with `secrets`. Stable within one job/request, different across jobs/requests. The lookup exists only in process memory, refuses pickling/copying, and is never logged, persisted, or returned. |
| Rules and settings | Rules leave only as `{rule_id: RULE_…, institution, doc_types}`. File-to paths, filename templates, account/entity hints and `rules.md` notes never leave. People, addresses, entities and entity directories leave only as typed placeholders. |
| Egress validation | The final serialized request is parsed back and checked: exact envelope (`model`, `temperature`, `messages`, optional `response_format: {"type": "json_object"}`), fixed system prompt per feature, user message exactly `{"task", "data"}`, safe model-name charset with no configured sensitive value, every string leaf re-scanned (anything still detectable fails), no image markers or long base64 runs, at most 256 KB. |
| Untrusted input | Document text is placed only inside the JSON `data` string. It cannot change the system prompt. |
| Sealing | The adapter sends only requests sealed (HMAC, process-local key) by the gateway. Forged or modified requests raise `gateway_bypass` before any I/O. |
| Retries | Provider SDK automatic retries are disabled (`max_retries=0`). The gateway makes at most 2 attempts, only for retryable failures (timeout, connection error, HTTP 429/5xx). A retry sends the byte-identical body with the same `Idempotency-Key` header (`<job_id>:<feature>:<sha256 prefix>`). Malformed replies are never retried. |
| Reply validation | Exactly one JSON object; no code fences, duplicate keys, `NaN`/`Infinity`, extra or missing required fields (strict Pydantic); confidence must be a finite number in 0..1 (not a string or boolean); no instruction-like text (for example "ignore previous instructions", `<system>`, `assistant:`); every placeholder must be one this job issued, of an allowed type; rule IDs must be ones sent; clustering pages must be in range, unique and complete; filenames and directories must pass the constraints in section 4 and stay inside the archive or scope root after symlink resolution. |
| Failure | No partial acceptance. Pre-egress failures make zero provider calls. When durable job state is attached (`JobContext`), refused or rejected calls move the job to `privacy_blocked` and open a pending local review item with `candidate = {"blocked_reason": "<code>"}`. |

## 2. `POST /api/v1/ask-ai`

Scoped successor of the review page's "Ask AI" button: a filing suggestion for one PDF
already inside a registered archive scope. It does not accept free-form questions.
The call is read-only (no files or state are modified), so it takes no idempotency key.

### Request

`Content-Type: application/json`. Exactly these fields, strict types, no extras:

```json
{
  "archive_scope_id": "3f2b0c9e-0000-5000-8000-000000000000",
  "relative_path": "_Unmatched/Scan-synthetic.pdf"
}
```

| Field | Constraint |
|---|---|
| `archive_scope_id` | string, 1–128 characters, registered in local application state |
| `relative_path` | string, 1–1024 characters, archive-relative POSIX path (section 4) naming an existing `.pdf` inside the scope root |

### Processing order

1. Local state not configured → `503 state_unavailable`.
2. Body invalid → `422 invalid_request`.
3. Unknown scope → `404 scope_not_found`.
4. Path invalid, symlinked, outside the scope, or not `.pdf` → `400 invalid_path`. Missing
   file → `404 file_not_found`.
5. Local-only → `200` with `status: "local_only"`. No OCR and no model call.
6. Local OCR of page 1, then the gateway (`Feature.ASK_AI`). Only the first 500 characters of
   pseudonymized OCR text, the page count, pseudonymized user context and tokenized rules
   (from `rules.md`, otherwise YAML `filing_rules`) are sent.

### Response `200`

```json
{
  "status": "suggested",
  "error_code": null,
  "suggestion": {
    "rule_matched": null,
    "suggested_filename": "SyntheticStatement.pdf",
    "suggested_relative_directory": "Statements/Synthetic",
    "doc_type": "statement",
    "period": "March2026",
    "confidence": 0.7,
    "reasoning": "one short sentence"
  },
  "privacy": {
    "mode": "cloud",
    "warning": "Pseudonymization is not anonymity. …"
  }
}
```

| Field | Values |
|---|---|
| `status` | `suggested` \| `local_only` \| `blocked` \| `invalid_model_output` \| `unavailable` |
| `error_code` | `null` when `suggested`; otherwise see the outcome table |
| `suggestion` | object when `suggested`, otherwise `null` |
| `suggestion.rule_matched` | the matched local rule's `rules.md` heading or YAML rule `id`, or `null` |
| `suggestion.suggested_filename` | safe filename (section 4) or `null` |
| `suggestion.suggested_relative_directory` | safe scope-relative directory (section 4) or `null` |
| `suggestion.doc_type` | `^[a-z][a-z0-9_]{0,39}$` or `null` |
| `suggestion.period` | `^(?:[A-Z][a-z]{2,8})?(?:19\|20)\d{2}$` (for example `March2026`, `2026`) or `null` |
| `suggestion.confidence` | finite number, 0 ≤ x ≤ 1 |
| `suggestion.reasoning` | model text, at most 500 characters, rehydrated locally. It may contain the user's own names. Render as plain text, never HTML. |
| `privacy.mode` | `cloud` \| `local_only` |
| `privacy.warning` | the exact pseudonymization warning above |

When a rule matches, the filename and directory are built locally from that rule's template
and `File to`/`file_to` (`{year}` from `period`), then validated like model output. If the
rule's values are unsafe (absolute, traversal, symlink escape, bad characters), both fields
are `null` and `rule_matched` is still set.

### Outcomes (`200`)

| `status` | `error_code` | Provider calls |
|---|---|---|
| `suggested` | `null` | 1 (2 if the first attempt had a retryable failure) |
| `local_only` | `local_only` | 0 |
| `blocked` | `unresolved_sensitive`, `detector_failure`, `serialization_failure`, `egress_validation_failure`, `gateway_bypass` | 0 |
| `invalid_model_output` | `invalid_model_output` | 1 |
| `unavailable` | `transport_unavailable`, `timeout`, `transport_error` | 0–2 |

### Errors (non-`200`)

Body: `{"error": {"code": "<code>", "message": "<fixed text>"}}`. Messages never include
request values, paths, OCR text or provider errors.

| HTTP | `code` |
|---|---|
| 400 | `invalid_path` |
| 404 | `scope_not_found`, `file_not_found` |
| 422 | `invalid_request` |
| 503 | `state_unavailable` |

## 3. `POST /api/v1/connection-test`

Checks provider connectivity for a scope with a fixed synthetic probe. The only content sent
is the fixed system prompt, the configured model name and
`{"task": "connection_test", "data": {"probe": "connectivity"}}`. The only accepted reply is
exactly `{"status": "ok"}`.

### Request

```json
{"archive_scope_id": "3f2b0c9e-0000-5000-8000-000000000000"}
```

Exactly this field (string, 1–128 characters, registered). No extras.

### Response `200`

```json
{
  "status": "connected",
  "model": "configured-model-id",
  "error_code": null,
  "privacy": {"mode": "cloud", "warning": "Pseudonymization is not anonymity. …"}
}
```

| `status` | `model` | `error_code` |
|---|---|---|
| `connected` | configured model ID | `null` |
| `local_only` | `null` | `local_only` (0 provider calls) |
| `error` | `null` | `transport_unavailable`, `timeout`, `transport_error`, `invalid_model_output`, `egress_validation_failure` |

Errors: `422 invalid_request`, `404 scope_not_found`, `503 state_unavailable`, with the same
error body as section 2.

## 4. Local-only resolution and value constraints

**Local-only mode** applies when either:

- config `privacy_mode` is not exactly `"cloud"` (missing means `"cloud"`; unknown values fail
  closed to local-only), or
- the scope setting `privacy_mode` equals `"local_only"`.

In local-only mode no provider transport is constructed and no network call is made. The
pipeline still runs locally: rule-based clustering, YAML rule fast path, and unmatched items
go to the review queue.

**Archive-relative request paths** (`relative_path`): no NUL or backslash; must not start
with `~`, `/`, a drive letter or UNC prefix; no `..` segments (`.` segments are dropped); the
final component must not be a symlink; the path resolved with symlinks followed must be
strictly inside the scope's canonical root; must be an existing regular file ending in `.pdf`
(case-insensitive).

**Suggested filename**: one component matching `^[A-Za-z0-9_][A-Za-z0-9 ._()&,'+-]{0,119}$`,
ending in `.pdf`, with no `..`. There are no braces, slashes or control characters, and
values are never silently repaired.

**Suggested relative directory**: `/`-separated components, each matching the filename
component pattern above. Trailing `/` is trimmed. No leading `/`, backslash, empty, `.` or
`..` component. After resolution it must stay inside the root: the scope's canonical root
for `/api/v1`, or config `archive_root` for legacy routes.

**Placeholders in model replies**: filenames may contain only `PERSON_`/`ENTITY_`
placeholders; directories only `PATH_`/`ENTITY_`/`PERSON_`. Values are rehydrated locally
and validated again after rehydration.

## 5. Preserved legacy routes (adapters over the same gateway)

These stay until Phase 4 migrates the UI. They share the gateway, validation and local-only
behavior above.

### `POST /api/unmatched/suggest`

Request: `{"path": "<absolute path>"}`. The path must resolve inside the configured
`archive_root` or `scan_watch_folder` (where archived originals live). The final component
must not be a symlink, and the file must be an existing `.pdf`.

| HTTP | Body |
|---|---|
| 200 | `{"rule_matched", "suggested_filename", "suggested_directory", "doc_type", "period", "confidence", "reasoning"}` (same value constraints as `suggestion` in section 2; `suggested_directory` is archive-relative) |
| 400 | `{"detail": "path is required"}` or `{"detail": "Only PDF files are supported"}` |
| 403 | `{"detail": "Access denied"}` (outside both roots, traversal, symlink) |
| 404 | `{"detail": "File not found"}` |
| 409 | `{"detail": {"code": "local_only"}}` (no OCR, no call) |
| 422 | `{"detail": {"code": "<blocked code or invalid_model_output>"}}` |
| 502 | `{"detail": {"code": "transport_unavailable" \| "timeout" \| "transport_error"}}` |

### `POST /api/settings/test-connection`

No body. Response is always `200`:

- `{"status": "connected", "reply": "OK", "model": "<configured model id>"}`
- `{"status": "local_only" | "error", "error_code": "<code>", "error": "<fixed human message>"}`

Raw exception or provider text is never returned.

### `GET /api/settings` / `POST /api/settings`

`GET` additionally returns `privacy_mode` (`cloud` when unset) and `privacy_warning`. `POST`
accepts `privacy_mode` ∈ {`cloud`, `local_only`} and persists it. Any other value returns
`400` and leaves settings unchanged.

## 6. Not part of Phase 2

- No HTTP route registers archive scopes yet. Scopes come from the local state layer
  (`StateStore.scopes.register`).
- No UI changes. Undo, durable filing, and migrating `review.html`/`settings.html` to these
  routes are Phase 3/4 work.
- No free-form question answering.
- Detection is best effort. The warning above is part of the contract, not a disclaimer to
  hide.
