# DocFlow – Kickoff Prompt for New Codex Session

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
11) requirements.txt, .gitignore

What to Deliver (no code changes yet)
1) One‑paragraph summary of the repo and current MVP status.
2) List the implemented API endpoints with brief purpose and key params.
3) How to run and test quickly (Swagger and curl) with both analyze=false and analyze=true.
4) Known limitations and current behavior; note Poppler/Ollama/Claude setup caveats.
5) Prioritized next steps (3–6) with short rationale (e.g., background tasks, logging, auth, UI).

Notes
- Do not run installs or modify code in this step — just read and report.
- Keep the response concise and structured so we can immediately proceed to implementation.

---

Context Snapshot (as of v0.1.0)
- MVP endpoints working: upload (with analyze/dpi), get document by id, correction, health, list documents, settings (get/post).
- Optional AI analysis via Ollama or Claude; fast path with analyze=false.
- PDF splitting and file organization working; learning engine records decisions/corrections.
- Next focus: background processing for large PDFs, logging, basic auth, simple frontend.

