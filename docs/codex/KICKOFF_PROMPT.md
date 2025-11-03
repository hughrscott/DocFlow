# DocFlow – Kickoff Prompt for New Codex Session

Audience: Codex sessions
Owner: maintainers
Last Updated: 2025-11-03

Use this prompt to rapidly bring a new Codex session up to speed. Paste it as-is.

---

You are joining the DocFlow project: a FastAPI backend for AI‑assisted document organization. Read the files below (in order), then produce a short briefing and a concrete next‑step plan.

Goals
- Understand what DocFlow is, what the MVP does, and how to run it.
- Learn current endpoints and behavior with and without AI analysis.
- See what’s implemented vs. pending and propose focused next steps.

Read These Files (in order)
1) README.md
2) QUICKSTART.md
3) IMPLEMENTATION_STATUS.md
4) PROJECT_SUMMARY.md
5) main.py
6) api/upload.py, api/status.py, api/settings.py
7) services/pdf_processor.py, services/document_analyzer.py, services/folder_router.py,
   services/file_manager.py, services/learning_engine.py
8) llm/llm_manager.py, llm/ollama_provider.py, llm/claude_provider.py
9) config/settings.py, config/llm_config.yaml
10) database/models.py, database/database.py
11) frontend/src/App.tsx, frontend/src/components/UploadArea.tsx,
    frontend/src/components/SettingsView.tsx, frontend/src/services/api.ts
12) requirements.txt, .gitignore

What to Deliver (no code changes yet)
1) One‑paragraph summary of the repo and current MVP status.
2) List the implemented API endpoints with brief purpose and key params (include upload, list, health, settings, document/page reanalyze, page correct).
3) How to run and test quickly (Swagger and curl) with both analyze=false and analyze=true; include background=true for uploads and document reanalyze.
4) Known limitations and current behavior; note Poppler/Ollama/Claude setup caveats; clarify that reanalyze records proposals but does not auto-move files.
5) Prioritized next steps (3–6) with short rationale (e.g., one‑click "move to proposed", visual progress, JSON logging everywhere, more tests, frontend polish).

Notes
- Do not run installs or modify code in this step — just read and report.
- Keep the response concise and structured so we can immediately proceed to implementation.

---

Context Snapshot (as of v0.1.0)
- Backend MVP:
  - Upload endpoint with analyze/dpi/background; background tasks for uploads and document reanalyze.
  - Document details with progress (pages_done/total), last_error, and per‑page provider/model.
  - Per‑page latest decision proposals (proposed_folder/filename) surfaced alongside current location.
  - Reanalyze endpoints: document‑level and page‑level; page correction endpoint to move files and learn.
  - Health, list documents, settings (GET/POST with Basic auth) endpoints.
- Frontend MVP (Vite + React):
  - Upload UI with analyze/background/dpi; recent documents; details with per‑page results and actions.
  - Settings view to read/update providers (Basic auth for save).
  - Toast notifications and basic error handling.
- Logging & tests:
  - Request ID + timing middleware; LOG_FORMAT=json toggle available.
  - Pytest suite passing (unit + integration upload fast path); deprecations silenced/handled.
