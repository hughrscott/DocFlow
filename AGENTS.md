# DocFlow — Agent Guide (Start Here)

Audience: AI coding agents (Codex CLI)
Owner: maintainers
Last Updated: 2025-11-11

Purpose
- Provide a single, agent-friendly entrypoint with clear goals, rules, and links so agents can execute tasks safely and efficiently without hunting across docs.

Primary Objectives (in priority order)
1) Keep upload → analyze → route flow correct and stable.
2) Improve analysis quality (prompt/schema, grouping, corrections).
3) Enhance operability (readiness checks, logging/traceability, progress visibility).
4) Maintain safety (no destructive actions; preserve user data and history).

Startup Checklist
- Read: docs/STATUS.md (Done/In‑Progress/Next), ROADMAP.md (near‑term)
- Confirm tests run: `venv/bin/python -m pytest -q`
- Verify local config: `config/llm_config.yaml` and `.env` (optional for tests)

Repo Map (agent-relevant)
- API: `api/` (upload, status, settings)
- Services: `services/` (document_analyzer, folder_router, pdf_processor, learning_engine, file_manager)
- LLM: `llm/` (manager + providers)
- Docs: `docs/` (STATUS, ROADMAP, API, OPERATIONS, LOGGING, TESTING, SECURITY, ADRs)
- Frontend: `frontend/` (minimal scaffold)
- Tests: `tests/` (health/settings/upload; add more next)

Execution Rules
- Be surgical: change only what’s needed. Never remove user data or move files unintentionally.
- Favor root-cause fixes over patches. Add tests for new logic.
- Don’t install packages or use network unless explicitly requested/approved.
- Respect sandbox: write only inside workspace; avoid destructive commands.
- Use structured logging and propagate request_id when modifying request flows.

Task Intake and Planning
- Single source of truth for tasks: `docs/TODO.md` (use it to align steps).
- Keep plans short and sequential; run tests after each meaningful change.
- For multi-step work: land minimal vertical slices that keep API stable and tests green.

Key Schemas and Conventions
- Analyzer JSON: issuer/recipient/type/period/identifiers/amounts/account_type/business_context + grouping fields (see `services/document_analyzer.py::_create_analysis_prompt`).
- Routing: `services/folder_router.py` maps `document_type` → top category and uses `account_type` for Personal/Business sublevel; filenames derive from institution/type/date/masked last4.
- Grouping: `sequence_id` is attached in `api/upload.py` via heuristics (continuations, page markers, fonts, identifiers).

High-Value Tasks (from STATUS/ROADMAP)
- Carousel-based document review interface with visual classification confirmation and approval workflow.
- Doc-level aggregator polish + Essentials UI surfacing confirmed class, period, and key identifiers.
- Integration tests for reanalyze/correct flows plus broader analyzer/router unit coverage.
- Frontend redesign + readiness/progress visualizations tuned for desktop/app-store delivery with carousel UI.
- Packaging/install experience so DocFlow can ship as a desktop application (bundled backend + UI).
- Search/tagging + OCR/full-text experiments once core flows are hardened.

Runbook (common commands)
- Run tests: `venv/bin/python -m pytest -q`
- Format (if configured): `black <paths>`; Lint: `flake8` (don’t add new formatters).
- Start API (local): `uvicorn main:app --reload`

Guardrails
- Do not restructure user directories or delete files outside `documents/` and `uploads_temp/` flows.
- Do not commit secrets; keep `.env` local.
- Keep database migrations minimal; prefer additive changes.

Links
- Status: `docs/STATUS.md`
- Roadmap: `ROADMAP.md`
- API: `docs/API.md`
- Logging: `docs/LOGGING.md`
- Testing: `docs/TESTING.md`
- ADRs: `docs/decisions/`
