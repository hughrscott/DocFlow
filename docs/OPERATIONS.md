# Operations & Setup

Audience: operators and developers
Owner: maintainers
Last Updated: 2025-11-03

Dependencies
- Python environment with packages from `requirements.txt`
- Poppler (for `pdf2image`): macOS `brew install poppler`, Ubuntu `apt-get install poppler-utils`
- Optional LLMs:
  - Ollama (local): install from https://ollama.ai, run `ollama serve`, pull `llava:latest` and `mistral:latest`
  - Claude (cloud): set `CLAUDE_API_KEY` and ensure `config/llm_config.yaml` provider enabled

Configuration
- App settings via env vars (`config/settings.py`) and `config/llm_config.yaml`
- Key env vars: `DOCUMENTS_DIR`, `UPLOADS_TEMP_DIR`, `DATABASE_URL`, `CLAUDE_API_KEY`, `OLLAMA_BASE_URL`, `DEFAULT_USERNAME`, `DEFAULT_PASSWORD`, `LOG_FORMAT=json`

Startup
- `pip install -r requirements.txt`
- `python -c "from database.database import init_db; init_db()"`
- `uvicorn main:app --reload`

Validation
- `GET /api/v1/health` should show `database_ok: true` and active providers when configured
- Upload with `analyze=false` to test non-AI path

Troubleshooting
- Image conversion errors: ensure Poppler installed; verify `pdf2image` installed
- Ollama connection: check `http://localhost:11434/api/tags`; verify models pulled
- Claude errors: validate API key and network; check model names in config
- Permissions: ensure process can write to `uploads_temp/` and `documents/`

