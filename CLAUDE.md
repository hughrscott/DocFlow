# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is DocFlow

DocFlow is an AI-powered document management system that automatically analyzes, categorizes, and files PDF documents. It has two pipeline versions:

- **v1 (legacy)**: Splits PDFs page-by-page, analyzes each page independently with a fixed metadata schema, routes using hardcoded category-to-folder mappings.
- **v2 (current default)**: AI-powered intelligent splitting into logical sub-documents, rich type-specific classification (9 document schemas), directory-aware LLM filing where the model sees the real folder tree, and an interactive accept/modify/reject proposal workflow. A learning loop feeds user corrections back into future LLM prompts.

## Commands

### Backend
```bash
# Run the API server (hot-reload)
uvicorn main:app --reload            # http://localhost:8000

# Run all tests (59 tests: 12 existing + 47 new)
venv/bin/python -m pytest -q

# Run a single test file
venv/bin/python -m pytest tests/test_health_and_settings.py -v

# Initialize/reset the database (DELETES existing data)
rm -f docflow.db
python -c "from database.database import init_db; init_db()"

# Formatting and linting
black <paths>
flake8 . --max-line-length=120
```

### Frontend
```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173 (proxies /api → :8000)
npm run build                        # tsc && vite build
```

### System dependency
Poppler is required for PDF-to-image conversion: `brew install poppler` (macOS) or `apt-get install poppler-utils` (Debian/Ubuntu).

## Architecture

```
React frontend (Vite, port 5173)
  └─ /api proxy ──► FastAPI backend (port 8000)
                      ├─ api/upload.py      – upload (v1 + v2 routing), correction, reanalyze, bulk-apply, revert
                      ├─ api/proposals.py   – v2 proposal review: accept/modify/reject/accept_all/preview/reclassify/refile
                      ├─ api/status.py      – health, document listing, pagination, provider readiness
                      └─ api/settings.py    – read/write LLM config (Basic auth)
                            │
                      V2 Service layer
                      ├─ services/pipeline_orchestrator.py – coordinates: split → classify → propose → store
                      ├─ services/document_splitter.py     – AI boundary detection (multi-image for Claude, text for Ollama)
                      ├─ services/rich_classifier.py       – two-phase type-specific classification (9 schemas)
                      ├─ services/smart_filer.py           – directory-aware LLM filing proposals
                      ├─ services/pii_protector.py         – PII redaction for cloud providers (SSN, accounts, DOB)
                      │
                      V1 Service layer (kept for backward compatibility)
                      ├─ services/pdf_processor.py         – split PDF, convert pages to images
                      ├─ services/document_analyzer.py     – send images to LLM, extract structured metadata
                      ├─ services/folder_router.py         – map analysis → folder path + filename
                      ├─ services/file_manager.py          – move/copy files, handle conflicts
                      └─ services/learning_engine.py       – track decisions, record corrections, build metrics
                            │
                      LLM providers (llm/)
                      ├─ llm_manager.py       – load config, init providers, manage fallback chain, analyze_images
                      ├─ claude_provider.py    – single + multi-image API calls
                      └─ ollama_provider.py    – single-image only (text fallback for splitting)
                            │
                      SQLite (docflow.db) via SQLAlchemy ORM (database/)
```

### V2 Pipeline Flow

Upload → PDF saved to temp → PipelineOrchestrator runs in background:
1. **Split**: DocumentSplitter detects logical sub-document boundaries (Claude: multi-image batches of ≤20 pages with 2-page overlap; Ollama: text-based with page markers)
2. **Classify**: RichClassifier runs two-phase classification per sub-document (Phase 1: identify type from first page; Phase 2: extract type-specific metadata using dedicated schema)
3. **Propose Filing**: SmartFiler scans real directory tree, sends metadata + tree to LLM, gets filing proposal with rationale
4. **Store**: Creates SubDocument + FilingProposal records, sets document status to `awaiting_review`
5. **Review**: User accepts/modifies/rejects each proposal via ProposalReview UI. Corrections stored as LearningCorrection records and injected into future prompts.

Status transitions: `processing` → `splitting` → `classifying` → `proposing` → `awaiting_review` → (user actions) → sub-docs become `filed`/`rejected`

### V1 Pipeline Flow (legacy, pipeline_version=1)

Upload → PDF split into pages → each page converted to image → LLM analyzes image → structured JSON → folder_router proposes path + filename → file_manager moves file → learning_engine records decision.

### Key database tables (database/models.py)

**V2 tables:**
- `sub_documents` – logical documents found within a PDF (page range, type, metadata, proposed/final filing path, status)
- `filing_proposals` – proposal lifecycle per sub-document (proposed path, rationale, alternatives, user decision)
- `learning_corrections` – structured corrections fed back into prompts (proposed vs actual values, user rationale, correction type)
- `pii_redaction_log` – audit log of PII redactions

**V1 tables (still active):**
- `documents` – uploaded PDF metadata and processing status (now includes `pipeline_version`, `sub_document_count`, phase booleans)
- `pages` – per-page analysis results (now includes `sub_document_id` FK, `text_content`)
- `processing_decisions` – every categorization decision (for v1 learning)
- `folder_mappings` – learned folder patterns
- `file_move_audit` – move history with revert capability

### V2 API Endpoints (api/proposals.py)

| Action | Method | Endpoint |
|--------|--------|----------|
| Get proposals | GET | `/api/v1/proposals/{document_id}` |
| Accept | POST | `/api/v1/proposals/sub_documents/{id}/accept` |
| Modify | POST | `/api/v1/proposals/sub_documents/{id}/modify` |
| Reject | POST | `/api/v1/proposals/sub_documents/{id}/reject` |
| Accept all | POST | `/api/v1/proposals/{document_id}/accept_all` |
| Page preview | GET | `/api/v1/proposals/sub_documents/{id}/preview?page=1&dpi=150` |
| Reclassify | POST | `/api/v1/proposals/sub_documents/{id}/reclassify` |
| Refile | POST | `/api/v1/proposals/sub_documents/{id}/refile` |

Upload uses `POST /api/v1/documents/upload?pipeline_version=2` (v2 is the default when pipeline_version=0).

### Configuration

- `config/settings.py` – Pydantic BaseSettings; all settings overridable via environment variables or `.env`
- `config/llm_config.yaml` – LLM provider selection, fallback chain, model names, analysis thresholds
- Environment variables support `${VAR}` substitution in the YAML config

**V2-specific settings:**
- `pii_protection_mode`: auto | always | never (default: auto — redact for cloud providers only)
- `default_pipeline_version`: 2
- `max_pages_per_split_batch`: 20
- `split_thumbnail_dpi`: 100
- `max_learning_corrections_in_prompt`: 5
- `filing_directory_scan_max_entries`: 200

### Frontend Stack

- React 18, Vite 5, TypeScript 5
- No UI library — custom CSS with Tokyo Night dark theme (CSS variables in `styles.css`)
- State-based routing (no React Router)
- Fetch-based API client (`services/api.ts`)
- Key components:
  - `ModernUploadArea.tsx` – drag-and-drop PDF upload
  - `ProposalReview.tsx` – v2 split-pane review UI (page preview left, proposal card right, accept/edit/reject)
  - `CarouselReview.tsx` – v1 page-by-page review
  - `SettingsView.tsx` – LLM provider configuration
  - `Toast.tsx` – toast notification system (ToastProvider context)

### Type-specific Classification Schemas (services/rich_classifier.py)

bank_statement, letter, invoice, tax_form, utility_bill, legal_document, insurance_document, medical_document, generic — each with dedicated metadata fields extracted in Phase 2.

## Current Project Status

**Backend v2 pipeline: COMPLETE** — Phases 1-5 of the development plan are done. All 59 tests pass.

**Frontend v2: FUNCTIONAL BUT NEEDS DESIGN OVERHAUL** — The ProposalReview component works end-to-end (accept/edit/reject/accept-all, page preview, thumbnail strip with colored sub-document groupings, learning feedback toasts) but the visual design needs significant improvement. The user plans to create mockups (possibly via claude.ai artifacts or a design tool) and return with design direction for a UI redesign.

**Next steps:**
1. UI/UX redesign of the ProposalReview component and overall app layout based on user-provided mockups
2. Folder autocomplete in the Edit flow (using `GET /api/v1/documents/folder_suggestions`)
3. Filing History / Dashboard screen (Phase 6, Screen 3 in the dev plan)
4. See `docs/DEVELOPMENT_PLAN.md` for the full Phase 6 frontend plan

## Development guidelines

- Keep the upload → analyze → route flow correct and stable (top priority).
- Be surgical: change only what's needed. Never remove user data or move files unintentionally.
- Use structured logging and propagate `request_id` when modifying request flows.
- Do not restructure user directories or delete files outside `documents/` and `uploads_temp/` flows.
- Keep database migrations minimal; prefer additive schema changes.
- Do not add new formatters or linters beyond what's already configured (black, flake8).
- V1 pipeline files (`document_analyzer.py`, `folder_router.py`, `learning_engine.py`) are kept as-is for backward compatibility.
- If the SQLite database gets out of sync with models (new columns), delete `docflow.db` and run `init_db()`.
- The `test_upload_fastpath.py` test explicitly uses `pipeline_version=1` to test the legacy path.

## Testing notes

- `conftest.py` adds the project root to `sys.path` for imports.
- `pytest.ini` filters PyPDF2 deprecation warnings.
- Tests use FastAPI's `TestClient` against the app in `main.py`.
- V2 tests mock the LLM layer — they don't require actual LLM providers to be configured.
- Test files: `test_document_splitter.py`, `test_rich_classifier.py`, `test_smart_filer.py`, `test_pii_protector.py`, `test_proposal_workflow.py`, `test_learning_loop.py`
