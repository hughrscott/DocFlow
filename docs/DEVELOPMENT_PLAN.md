# DocFlow Development Plan

Audience: contributors
Owner: maintainers
Last Updated: 2026-02-06

## Overview

This document outlines the development plan for DocFlow. The current priority is a major overhaul of the backend document processing pipeline — replacing the page-by-page approach with AI-powered intelligent splitting, rich classification, directory-aware filing, and an interactive proposal workflow. The frontend redesign will follow once the new backend pipeline is stable.

## Current State

- PDF uploads are split page-by-page (every page becomes its own file)
- Each page is analyzed independently by the LLM with a fixed metadata schema
- Folder routing uses hardcoded category-to-folder mappings with basic signal matching
- Heuristic-based `_is_continuation()` tries to group pages after the fact
- Learning engine tracks decisions and adjusts folder mapping confidence scores
- Frontend has basic upload, document list, and early carousel review components

## Problem

The current pipeline produces poor results because:
1. Multi-page documents (bank statements, letters, etc.) are broken into individual pages instead of being kept as logical units
2. Metadata extraction uses a one-size-fits-all schema — no type-specific depth
3. Folder routing doesn't reason about the actual directory structure
4. No interactive review workflow where users can approve/modify/reject with feedback

## Pipeline Overhaul (Phases 1–4)

### Phase 1: Foundation (Database + LLM Layer)

**New database tables** (`database/models.py`):

- **`sub_documents`** — a logical document found within a PDF (e.g., pages 3–12 are a PNC bank statement). Fields: `document_id` (FK), `start_page`, `end_page`, `page_count`, `document_type`, `document_category`, `institution`, `classification_metadata` (JSON with type-specific fields), `confidence_score`, `split_rationale`, `proposed_folder`, `proposed_filename`, `filing_rationale`, `final_folder`, `final_filename`, `output_path`, `status` (proposed → accepted/modified/rejected → filed), LLM provider info, timestamps.

- **`filing_proposals`** — tracks each proposal's lifecycle. Fields: `sub_document_id` (FK), `proposed_folder`, `proposed_filename`, `is_new_folder`, `rationale`, `alternatives_considered` (JSON), `confidence`, `decision` (accepted/modified/rejected/null), `user_folder`, `user_filename`, `user_rationale`.

- **`learning_corrections`** — structured correction data fed back into future LLM prompts. Fields: `sub_document_id` (FK), proposed vs actual values (document_type, folder, filename), `user_rationale`, `correction_type` (classification/filing/splitting/filename), `key_signals` (JSON), `used_in_prompts` counter.

**Modify existing tables:**
- `documents`: add `pipeline_version` (1=legacy, 2=new), `sub_document_count`, phase-tracking booleans
- `pages`: add `sub_document_id` (FK), `text_content`

**Multi-image LLM support:**
- Add `analyze_images(images, prompt, image_labels, max_tokens)` to `llm/base.py` VisionProvider
- Implement in `llm/claude_provider.py` — sends multiple image content blocks in one API call
- Add passthrough in `llm/llm_manager.py`
- Ollama keeps single-image only; splitting uses text fallback for Ollama

**Config additions** (`config/settings.py`):
- `pii_protection_mode`: auto | always | never (default: auto — redact for cloud only)
- `default_pipeline_version`: 2
- `max_pages_per_split_batch`: 20
- `split_thumbnail_dpi`: 100
- `max_learning_corrections_in_prompt`: 5
- `filing_directory_scan_max_entries`: 200

### Phase 2: Core Services

**`services/pii_protector.py`** — PII detection and redaction:
- Regex-based detection: SSNs, full account numbers, DOBs
- `redact_text()` replaces PII with `[REDACTED]` before sending to cloud LLMs
- `should_redact(provider_name)` returns True for cloud providers (claude, openai)
- Bypassed entirely for Ollama (local processing, data never leaves machine)
- Logs redactions to DB for audit

**`services/document_splitter.py`** — AI-powered boundary detection:
- **Claude path**: convert pages to low-res thumbnails (100 DPI), send batches of up to 20 page images in a single multi-image API call. Claude returns JSON: `[{start_page, end_page, document_type_hint, confidence, rationale}]`. For PDFs >20 pages, use overlapping batches (2-page overlap) and merge results.
- **Ollama path**: extract text from all pages via pypdf, send full text block with page markers to Ollama's text model for boundary detection. For ambiguous boundaries, confirm with single-page image calls.
- **Fallback**: text-only analysis when vision fails.
- `extract_sub_document_pdf(pdf_data, start_page, end_page)` — extracts a page range into a new PDF.

**`services/rich_classifier.py`** — two-phase type-specific classification:
- Phase 1: send first page image → identify document type
- Phase 2: select type-specific prompt template → send representative pages → extract deep metadata
- Type-specific schemas:
  - `bank_statement`: bank_name, account_type, account_last4, statement_period, balances
  - `letter`: sender, sender_type, recipient, subject, reason, action_required
  - `invoice`: vendor, invoice_number, line_items, total, due_date
  - `tax_form`: form_type, tax_year, employer, amounts
  - `utility_bill`: utility_type, provider, service_address, billing_period, amount_due
  - `legal_document`: case_number, court, parties, document_subtype
  - `insurance_document`: policy_number, insurer, coverage_type, premium
  - `medical_document`: provider, patient, visit_date, document_subtype
  - `generic`: best-effort extraction for unknown types
- `_inject_learning_context(prompt, doc_type)` — injects up to 5 recent relevant corrections into the prompt as in-context examples

**`services/smart_filer.py`** — directory-aware LLM filing:
- `scan_directory_tree()` — walks the filesystem under `DOCUMENTS_DIR`, produces a text tree representation (capped at ~200 entries, samples 3 files per folder)
- `propose_filing(metadata, directory_tree)` — sends metadata + directory tree to LLM, returns `{proposed_folder, proposed_filename, is_new_folder, rationale, alternatives, confidence}`
- `_inject_filing_corrections(prompt, metadata)` — injects relevant past filing corrections into prompt
- Replaces hardcoded `_category_for_type()` mapping in current `folder_router.py`

**`services/pipeline_orchestrator.py`** — coordinates the full flow:
1. Split: `DocumentSplitter.split_document()` → boundaries
2. Classify: for each sub-document, `RichClassifier.classify()` with PII protection
3. Propose filing: scan directory tree, `SmartFiler.propose_filing()` per sub-document
4. Store: create `SubDocument` + `FilingProposal` records, set document status to `awaiting_review`
- Loads recent `LearningCorrection` records and passes them to classifier and filer

### Phase 3: API Layer

**New router `api/proposals.py`** (`/api/v1/proposals`):
- `GET /{document_id}` — get all sub-documents with their proposals
- `POST /sub_documents/{sub_doc_id}/accept` — accept as-is, move file to proposed location
- `POST /sub_documents/{sub_doc_id}/modify` — change folder/filename/type, move file, record LearningCorrection
- `POST /sub_documents/{sub_doc_id}/reject` — record rejection + reason as LearningCorrection
- `POST /{document_id}/accept_all` — bulk accept all (or specified) proposals
- `GET /sub_documents/{sub_doc_id}/preview?page=1&dpi=150` — page image preview
- `POST /sub_documents/{sub_doc_id}/reclassify` — re-run classification
- `POST /sub_documents/{sub_doc_id}/refile` — re-run filing proposal

**Modify `api/upload.py`**:
- Add `pipeline_version: int = 2` query parameter
- v2: route to `PipelineOrchestrator`, always background, return document_id immediately
- v1: keep existing page-by-page flow for backward compatibility

**Register new routers in `main.py`**.

### Phase 4: Learning Loop

When a user **modifies** or **rejects** a proposal:
1. Create `LearningCorrection` record with proposed vs actual values + user rationale
2. On future pipeline runs, load recent corrections from DB
3. `RichClassifier` injects type-relevant corrections into classification prompts
4. `SmartFiler` injects filing-relevant corrections into filing prompts

Format injected into prompts:
```
PAST CORRECTIONS (follow these patterns):
- For PNC bank_statement: proposed 'Banking/PNC' → user chose 'Banking/Personal/PNC-Checking'
  (reason: separate folders for checking and savings)
```

## Phase 5: Tests & Verification

**New test files:**
- `tests/test_document_splitter.py` — boundary detection, batch merging, both provider paths
- `tests/test_rich_classifier.py` — two-phase flow, type-specific extraction, learning injection
- `tests/test_smart_filer.py` — directory tree scanning, proposal parsing, correction injection
- `tests/test_pii_protector.py` — SSN/account detection, provider-aware redaction
- `tests/test_proposal_workflow.py` — integration: upload → proposals → accept/modify/reject → verify files
- `tests/test_learning_loop.py` — upload, modify, upload similar, verify correction in prompt

**Verification steps:**
1. `venv/bin/python -m pytest -q` — all existing tests still pass
2. Upload a multi-page PDF via `POST /api/v1/documents/upload?pipeline_version=2`
3. Poll `GET /api/v1/proposals/{document_id}` — verify sub-documents with boundaries
4. Verify classification metadata is rich and type-specific
5. Verify filing rationale references actual directory structure
6. Accept one proposal, modify another, reject a third
7. Upload a similar PDF — verify corrections appear in LLM prompts

## Files Summary

**New files:**
- `services/pii_protector.py`, `services/document_splitter.py`, `services/rich_classifier.py`, `services/smart_filer.py`, `services/pipeline_orchestrator.py`
- `api/proposals.py`
- 6 new test files

**Modified files:**
- `database/models.py` — new tables + column additions
- `database/database.py` — handle new tables
- `llm/base.py` — add `analyze_images` abstract method
- `llm/claude_provider.py` — implement multi-image
- `llm/llm_manager.py` — add `analyze_images` method
- `config/settings.py` — new settings
- `api/upload.py` — route to new pipeline based on version
- `main.py` — register new routers
- `requirements.txt` — add `pdfminer.six`

**Kept as-is for v1 backward compatibility:**
- `services/document_analyzer.py`, `services/folder_router.py`, `services/learning_engine.py`

## Pipeline Implementation Status (2026-02-06)

Phases 1–5 are **complete**. All 59 tests pass (12 existing + 47 new). The backend
v2 pipeline is functional end-to-end. Next step is the frontend.

## Phase 6: Frontend Redesign

### Design Philosophy

The user's mental model: *"I have a stack of papers. Sort them into the right
folders for me."* The UI mirrors that physical experience — show what was found,
let the user quickly confirm or fix, and get out of the way.

### Three Screens

#### Screen 1: Upload + Queue ("the inbox")

- **Drag-and-drop zone** for PDFs (support batch upload)
- **Processing queue** below: list of documents with a status pill showing the
  pipeline phase (`Splitting...` → `Classifying...` → `Ready for Review`)
- Documents in `awaiting_review` get a badge count — this is the user's todo list
- Don't show sub-documents until the user clicks into a document
- Backend endpoints: `POST /api/v1/documents/upload?pipeline_version=2`,
  `GET /api/v1/documents/{id}` for polling status

#### Screen 2: Review Screen (core interaction — 90% of user time)

**Split-pane layout:**

**Left pane — Page preview carousel:**
- Large preview of the currently selected page
- Thumbnail strip along the bottom showing all pages in the PDF
- Colored brackets/highlights grouping pages into detected sub-documents
  (e.g., pages 1–3 highlighted blue = bank statement, page 4 green = letter)
- User can visually verify "yes, those 3 pages belong together"
- Backend: `GET /api/v1/proposals/sub_documents/{id}/preview?page=N&dpi=150`

**Right pane — Proposal card for the selected sub-document:**
- Document type badge with confidence % (e.g., `Bank Statement 92%`)
- Key metadata as scannable fields: institution, date, account, amounts
  (pulled from `classification_metadata` — show top 3–4 fields, expandable
  for full details via progressive disclosure)
- **Proposed filing path**: `Banking/Personal/PNC/PNC-Statement-2025-01.pdf`
- AI rationale in muted text: *"Matched existing PNC folder under Banking/Personal"*
- Three action buttons:
  - **Accept** (primary, large, green) — single click, files the document
  - **Edit** — inline-edits folder path + filename; folder field has
    autocomplete from existing directory tree + ability to create new folders
  - **Reject** — opens a small text input for the reason
- Backend: `POST .../accept`, `POST .../modify`, `POST .../reject`

**Bulk actions toolbar:**
- **Accept All** button for high-confidence proposals
  (backend: `POST /api/v1/proposals/{doc_id}/accept_all`)
- Sort/filter by confidence (show low-confidence first — those need attention)

#### Screen 3: Filing History / Dashboard (lower priority)

- Recent activity: what was filed where, with timestamps
- Corrections log: what the user changed and why
- Accuracy trend over time (builds trust: "the system is learning")
- Backend: query `learning_corrections` and `filing_proposals` tables

### Key UX Patterns

1. **Make Accept the easy path.** If the AI is right 80% of the time, users
   should blast through reviews with mostly single clicks.

2. **Confidence-based ordering.** Low-confidence proposals surface first.
   High-confidence ones can be bulk-accepted.

3. **Progressive disclosure.** Don't dump `classification_metadata` JSON.
   Show 3–4 key fields; let users expand for details.

4. **Inline folder picker.** On Edit, show real directory tree as a
   collapsible tree (data from `scan_directory_tree()`). Allow typing to
   filter. Allow creating new folders inline.

5. **Learning visibility.** After a correction, show a toast:
   *"Got it — I'll remember this for next time."* Encourages corrections.

6. **Split correction (future).** If the AI grouped pages wrong, let users
   drag a divider to re-split. This is the hardest interaction and also the
   rarest — defer until users ask for it.

### Frontend Implementation Priority

1. Upload page with drag-and-drop + status polling
2. Review page: split-pane with page preview + proposal card + Accept/Edit/Reject
3. Folder autocomplete on Edit using `GET /folder_suggestions`
4. Accept All bulk action
5. Dashboard (later)

### Relevant Backend Endpoints

| Action | Method | Endpoint |
|--------|--------|----------|
| Upload (v2) | POST | `/api/v1/documents/upload?pipeline_version=2` |
| Poll status | GET | `/api/v1/documents/{document_id}` |
| Get proposals | GET | `/api/v1/proposals/{document_id}` |
| Accept | POST | `/api/v1/proposals/sub_documents/{id}/accept` |
| Modify | POST | `/api/v1/proposals/sub_documents/{id}/modify` |
| Reject | POST | `/api/v1/proposals/sub_documents/{id}/reject` |
| Accept all | POST | `/api/v1/proposals/{document_id}/accept_all` |
| Page preview | GET | `/api/v1/proposals/sub_documents/{id}/preview?page=1&dpi=150` |
| Reclassify | POST | `/api/v1/proposals/sub_documents/{id}/reclassify` |
| Refile | POST | `/api/v1/proposals/sub_documents/{id}/refile` |
| Folder suggestions | GET | `/api/v1/documents/folder_suggestions` |

## Future Phases

### Search, Tagging & OCR
- Full-text search across filed documents
- Manual tagging system
- OCR for scanned documents without embedded text

### Packaging & Distribution
- Desktop application installer (bundled backend + UI)
- Docker containers
- PostgreSQL support for multi-user scenarios
