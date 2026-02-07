# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is DocFlow

DocFlow is an AI-powered document management system that automatically analyzes, categorizes, and files PDF documents. It splits multi-page PDFs into individual pages, sends page images to an LLM (Claude, Ollama, or OpenAI) for classification and metadata extraction, then routes each page to an intelligent folder hierarchy. A learning engine tracks decisions and corrections to improve accuracy over time.

## Commands

### Backend
```bash
# Run the API server (hot-reload)
uvicorn main:app --reload            # http://localhost:8000

# Run all tests
venv/bin/python -m pytest -q

# Run a single test file
venv/bin/python -m pytest tests/test_health_and_settings.py -v

# Initialize/reset the database
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
                      ├─ api/upload.py    – upload, correction, reanalyze, bulk-apply, revert
                      ├─ api/status.py    – health, document listing, pagination
                      └─ api/settings.py  – read/write LLM config (Basic auth)
                            │
                      Service layer
                      ├─ services/pdf_processor.py      – split PDF, convert pages to images
                      ├─ services/document_analyzer.py   – send images to LLM, extract structured metadata
                      ├─ services/folder_router.py       – map analysis → folder path + filename
                      ├─ services/file_manager.py        – move/copy files, handle conflicts
                      └─ services/learning_engine.py     – track decisions, record corrections, build metrics
                            │
                      LLM providers (llm/)
                      ├─ llm_manager.py    – load config, init providers, manage fallback chain
                      ├─ claude_provider.py
                      └─ ollama_provider.py
                            │
                      SQLite (docflow.db) via SQLAlchemy ORM (database/)
```

### Core data flow

Upload → PDF split into pages → each page converted to image → LLM analyzes image → structured JSON (document_type, institution, date, amounts, account_type, confidence) → folder_router proposes path + filename → file_manager moves file → learning_engine records decision.

Pages within a single upload are grouped by `sequence_id` (heuristic in `api/upload.py`) for multi-page document detection. Users can bulk-apply or revert moves per sequence.

### Key database tables (database/models.py)

- `documents` – uploaded PDF metadata and processing status
- `pages` – per-page analysis results (type, institution, confidence, proposed path)
- `processing_decisions` – every categorization decision (for learning)
- `folder_mappings` – learned folder patterns
- `file_move_audit` – move history with revert capability

### Configuration

- `config/settings.py` – Pydantic BaseSettings; all settings overridable via environment variables or `.env`
- `config/llm_config.yaml` – LLM provider selection, fallback chain, model names, analysis thresholds
- Environment variables support `${VAR}` substitution in the YAML config

### Analyzer JSON schema

The LLM prompt in `services/document_analyzer.py::_create_analysis_prompt` defines the expected response fields: issuer, recipient, type, period, identifiers, amounts, account_type, business_context, plus grouping fields.

### Routing conventions

`services/folder_router.py` maps `document_type` → top-level category and uses `account_type` (Personal/Business) for sublevel. Filenames derive from institution/type/date/masked-last4.

## Development guidelines (from AGENTS.md)

- Keep the upload → analyze → route flow correct and stable (top priority).
- Be surgical: change only what's needed. Never remove user data or move files unintentionally.
- Use structured logging and propagate `request_id` when modifying request flows.
- Do not restructure user directories or delete files outside `documents/` and `uploads_temp/` flows.
- Keep database migrations minimal; prefer additive schema changes.
- Do not add new formatters or linters beyond what's already configured (black, flake8).
- Task tracking lives in `docs/TODO.md`; status in `docs/STATUS.md`.

## Testing notes

- `conftest.py` adds the project root to `sys.path` for imports.
- `pytest.ini` filters PyPDF2 deprecation warnings.
- Tests use FastAPI's `TestClient` against the app in `main.py`.
