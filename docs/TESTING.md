# Testing

Audience: contributors
Owner: maintainers
Last Updated: 2025-11-03

Running Tests
- `pytest -q`

Focus Areas
- Unit: `services/folder_router.py`, `services/document_analyzer.py` (mock LLMManager)
- Integration: upload fast path (`analyze=false`), per-page persistence, list and detail endpoints
- Learning flow: record decision on upload; record correction on `POST /pages/{page_id}/correct`

Mocking Providers
- Mock `llm.llm_manager.LLMManager.analyze_image` to return deterministic JSON content
- Avoid real network calls to Claude/Ollama in unit tests

Fixtures
- Add DB fixture to init a temporary SQLite DB and clean up tables per test module

