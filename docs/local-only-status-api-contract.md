# Local-only health and status API contract

**Status:** Implemented in the sanitized workspace. Verified on Linux. Mac acceptance pending.

This document is the authoritative contract between the backend status endpoints and the
frontends (shared footer, Settings → System Health). The server is the single source of truth
for status wording and severity; frontends render what the server sends and never re-derive it.

---

## GET /api/health

Returns the current runtime posture of the app: where it reads and writes, whether privacy
mode is on, which capabilities are active, and whether the configured LLM is usable.

### Response fields

| Field | Type | Description |
|---|---|---|
| `watch_folder` | string | Absolute path of the folder watched for incoming scans. |
| `archive_root` | string | Absolute path of the archive root where filed documents are written. |
| `privacy_mode` | string | Active privacy posture, e.g. `local_only` or `cloud`. Determines which LLM status cases below can occur. |
| `text_extraction` | bool | Whether OCR / text extraction is enabled. Independent of AI classification. |
| `ai_classification` | bool | Whether LLM-backed classification is enabled. `false` in local-only mode. |
| `llm_ready` | bool | Whether a usable LLM is configured **and** reachable for classification. Capability signal only — see the warning below. |
| `llm_status` | string | Machine-readable status code: `local_only`, `missing_key`, or `ready`. |
| `llm_status_label` | string | Human-readable short label for display: `Local Only`, `No API Key`, or `Ready`. |
| `llm_status_level` | string | Severity for styling: `neutral`, `warning`, or `ok`. |
| `llm_status_detail` | string | Full sentence(s) explaining the status to the user. Rendered verbatim. |
| `model` | string | Identifier of the configured model (or the local provider/model in local-only mode). |
| `threshold` | number | Confidence threshold used by the confidence gate; decisions below it go to the review queue. |

> **`llm_ready` is not a key-presence signal.** It is `false` in local-only mode purely because
> AI classification is off by design — nothing is missing and nothing is wrong. Frontends must
> never infer "missing API key" from `llm_ready === false`.

### Status cases

#### 1. Local-only mode

```json
{
  "privacy_mode": "local_only",
  "text_extraction": true,
  "ai_classification": false,
  "llm_ready": false,
  "llm_status": "local_only",
  "llm_status_label": "Local Only",
  "llm_status_level": "neutral",
  "llm_status_detail": "OCR runs locally. AI classification is off."
}
```

- `llm_ready` is `false`.
- `llm_status` is `local_only`.
- `llm_status_label` is `Local Only`.
- `llm_status_level` is `neutral` — this is an informational state, **not** an error or warning.
- `llm_status_detail` is exactly: `OCR runs locally. AI classification is off.`

#### 2. Cloud provider, required API key missing

```json
{
  "llm_ready": false,
  "llm_status": "missing_key",
  "llm_status_label": "No API Key",
  "llm_status_level": "warning",
  "llm_status_detail": "Add an API key to enable AI classification."
}
```

- `llm_status` is `missing_key`.
- `llm_status_label` is `No API Key`.
- `llm_status_level` is `warning`.
- `llm_status_detail` is exactly: `Add an API key to enable AI classification.`

This case is reachable **only** when the configured provider actually requires a key and that
key is absent.

#### 3. Ready

Applies to both of these configurations:

- A cloud provider with a valid API key present.
- A **keyless local provider** (a local provider that requires no API key at all).

```json
{
  "llm_ready": true,
  "llm_status": "ready",
  "llm_status_label": "Ready",
  "llm_status_level": "ok"
}
```

- `llm_status` is `ready`.
- `llm_status_label` is `Ready`.
- `llm_status_level` is `ok`.

A keyless local provider must never surface `missing_key`: there is no key to be missing.

---

## Frontend rendering rules

These apply to **both** the shared footer and Settings → System Health.

1. **Render the server's values.** Display `llm_status_label` as the label, style from
   `llm_status_level`, and show `llm_status_detail` as the explanatory text. Do not
   re-word, re-map, or locally reconstruct any of the three.
2. **Never infer a missing key from `llm_ready`.** The only signal for a missing key is
   `llm_status === "missing_key"`. A `false` value of `llm_ready` by itself means only
   "AI classification is not currently available".
3. **Level → presentation mapping:**
   - `neutral` — informational styling. No alert affordance, no warning icon, no error colour.
   - `warning` — warning styling with the actionable detail text.
   - `ok` — success/ready styling.
4. **Local-only is never an alarm.** In `local_only`, the footer and System Health panel must
   read as a deliberate, healthy configuration.
5. **Unknown codes degrade safely.** If `llm_status` carries a code the frontend does not
   recognise, render `llm_status_label` / `llm_status_detail` as-is and fall back to
   `neutral` styling rather than inventing an error state.

---

## POST /api/settings/test-connection

Used by Settings to verify the configured provider on demand.

In **local-only** mode the endpoint returns `status: "local_only"`. The frontend must present
this as a neutral, non-alert result — the same informational treatment as the `neutral` level
above. It is not a failed connection test: there is no remote endpoint to reach, by design.
Do not show a failure toast, error colour, or retry-as-if-broken affordance for this response.

---

## Tests

The behaviour described here is covered by:

- `tests/privacy/test_web_cloud_paths.py`
- `tests/review_actions/test_ui_status_contract.py`

Any change to status codes, labels, levels, or the exact detail strings above must be made in
lockstep with those tests.
