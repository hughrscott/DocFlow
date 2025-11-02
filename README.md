# DocFlow - Intelligent Document Management System

DocFlow is an AI-powered document management system that automatically analyzes, categorizes, and files your documents. It uses advanced machine learning to understand document types and intelligently organize them into folders.

## Features

✨ **Intelligent Document Analysis**
- AI-powered document type recognition (bank statements, tax bills, utilities, etc.)
- Works with new institutions and document types automatically
- Extracts rich metadata (dates, account info, amounts, etc.)

📁 **Smart File Organization**
- Analyzes existing folder structure to learn your filing preferences
- Automatically routes documents to appropriate folders
- Creates new folders intelligently when needed
- Intelligent file naming based on extracted metadata

🧠 **Learning System**
- Improves accuracy over time as you use it
- Learns your filing preferences from your existing structure
- Tracks all decisions for continuous improvement
- Provides confidence scores for categorization

🔐 **Privacy & Security**
- Local-first processing - your documents stay on your machine
- Optional cloud LLM integration (Claude, Ollama)
- Secure handling of sensitive financial documents
- Authentication for web interface

🔄 **Multi-LLM Support**
- Claude API (cloud, highly accurate)
- Ollama (local, free, offline)
- Extensible architecture for other providers
- Automatic fallback if primary provider fails

## Architecture

```
┌─────────────────────────────────────────┐
│     React Frontend (Web Interface)      │
│  - Upload PDFs                          │
│  - View folder structure                │
│  - Monitor progress                     │
└────────────────┬────────────────────────┘
                 │ HTTP/REST API
┌────────────────▼────────────────────────┐
│         FastAPI Backend                 │
├─────────────────────────────────────────┤
│  • PDF Processing & Splitting           │
│  • Document Analysis (AI)               │
│  • Folder Routing & Intelligence        │
│  • Learning Engine                      │
│  • File Management                      │
│  • Authentication & Security            │
└────────────────┬────────────────────────┘
        ┌────────┼────────┐
        │        │        │
        ▼        ▼        ▼
    ┌─────┐ ┌───────┐ ┌──────────┐
    │File │ │SQLite │ │Claude/   │
    │Sys  │ │  DB   │ │Ollama LLM│
    └─────┘ └───────┘ └──────────┘
```

## Installation

### Prerequisites

- Python 3.9+
- Node.js 16+ (for frontend)
- Optional: Ollama (for local AI)
- Optional: Claude API key (for cloud AI)

### Backend Setup

1. **Clone and navigate to project:**
```bash
cd docflow
```

2. **Create and activate virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Set up environment variables:**
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. **Configure LLM providers:**
```bash
# Edit config/llm_config.yaml to choose Claude, Ollama, or both
nano config/llm_config.yaml
```

6. **Initialize database:**
```bash
python -c "from database.database import init_db; init_db()"
```

7. **Run backend:**
```bash
uvicorn main:app --reload
```

Backend will be available at `http://localhost:8000`

8. **Test the upload API (MVP):**
- Swagger UI: open `http://localhost:8000/docs`, use `POST /api/v1/documents/upload`
  - Set `analyze=false` for a quick test (skips AI)
  - Upload `test_document.pdf`
- Or curl:
```
curl -F file=@test_document.pdf 'http://127.0.0.1:8000/api/v1/documents/upload?analyze=false&dpi=120'
```
Then fetch details:
```
curl http://127.0.0.1:8000/api/v1/documents/<document_id>
```

### Frontend Setup

This repo includes a minimal React + Vite frontend scaffold under `frontend/`.

1. **Navigate to frontend directory:**
```bash
cd frontend
```

2. **Install dependencies:**
```bash
npm install
```

3. **Start development server:**
```bash
npm run dev
```

- Frontend runs at `http://localhost:5173` (Vite dev server)
- API proxy is configured for `/api` to `http://localhost:8000` (no CORS needed)
- Optionally set `VITE_API_BASE` in `frontend/.env` to override API base URL

UI features (MVP)
- Upload a PDF with analyze/background/dpi options
- View recent documents (filename, uploaded time, status, pages)
- View document details including per-page results (type, institution, date, confidence, folder/filename, provider/model, status)

## Configuration

### LLM Provider Configuration

Edit `config/llm_config.yaml` to configure which LLM providers to use:

**Option 1: Use Claude (Cloud, Recommended for accuracy)**
```yaml
llm:
  vision_provider: "claude"
  text_provider: "claude"

providers:
  claude:
    enabled: true
    api_key: "${CLAUDE_API_KEY}"
    model: "claude-3-5-sonnet-20241022"
```

**Option 2: Use Ollama (Local, Free)**
First install Ollama:
```bash
# Download from https://ollama.ai
ollama pull llava:latest  # For image analysis
ollama pull mistral:latest  # For text processing
```

Then configure:
```yaml
llm:
  vision_provider: "ollama"
  text_provider: "ollama"

providers:
  ollama:
    enabled: true
    base_url: "http://localhost:11434"
    vision_model: "llava:latest"
    text_model: "mistral:latest"
```

Run an analyzed upload once Poppler and Ollama are ready:
```
curl -F file=@test_document.pdf 'http://127.0.0.1:8000/api/v1/documents/upload?analyze=true&dpi=120'
```

**Option 3: Use Both with Fallback**
```yaml
llm:
  vision_provider: "claude"
  text_provider: "ollama"
  fallback_providers:
    - "ollama"
    - "claude"
```

### Document Directory

Set where your documents are stored in `.env`:
```bash
DOCUMENTS_DIR=~/MyDocuments/DocFlow
UPLOADS_TEMP_DIR=./uploads_temp
```

## Usage

### Basic Workflow

1. **Start DocFlow:**
   - Backend: `uvicorn main:app --reload`
   - Frontend: `npm start`
   - Open http://localhost:3000

2. **Upload Documents:**
   - Drag and drop PDF files into the upload area
   - Or click to browse and select files
   - System will begin analysis immediately

3. **Monitor Progress:**
   - See real-time status of document processing
   - View confidence scores for categorization
   - Check assigned folders and filenames

4. **Review Organization:**
   - Documents are automatically filed into folders
   - View the folder structure
   - System learns from corrections you make

### Example Scenario

**You have these documents to upload:**
- `statements.pdf` (2 pages: PNC bank statement + property tax bill)
- `insurance.pdf` (1 page: auto insurance claim)

**What DocFlow does:**
1. Splits `statements.pdf` into 2 pages
2. Analyzes each page:
   - Page 1: Bank statement from PNC, January 2025 → Files to `Banking/Personal/PNC-Checking/PNC_Statement_Jan2025.pdf`
   - Page 2: Property tax bill, Harris County 2025 → Files to `Taxes/Property/PropertyTax_HarrisCounty_2025.pdf`
3. Analyzes `insurance.pdf`:
   - Auto insurance claim → Files to `Insurance/Auto/AutoIns_Claim_Jan2025.pdf`

All done automatically!

## API Endpoints

- `GET /` — API info
- `GET /api/v1/status` — API status
- `GET /api/v1/health` — Healthcheck (DB + providers)
- `GET /api/v1/documents` — List recent documents (page, page_size)
- `POST /api/v1/documents/upload` — Upload PDF; query params: `analyze` (bool, default true), `dpi` (int), `background` (bool, default false)
- `GET /api/v1/documents/{document_id}` — Get document + page results
  - Includes `pages_done`, `total_pages`, and latest `last_error` if any
- `POST /api/v1/documents/decisions/correct` — Record a routing correction
- `GET /api/v1/settings` — Read LLM settings (YAML)
- `POST /api/v1/settings` — Update provider selection/config (Basic auth when `AUTH_ENABLED=true`)

## Learning System

DocFlow learns and improves over time:

- **Tracks every decision** about document categorization
- **Learns folder patterns** from your existing directory structure
- **Improves confidence scores** as accuracy increases
- **Adapts to new document types** automatically
- **Handles corrections** - if you move a file, the system learns

Check the database for learning metrics:
```bash
sqlite3 docflow.db
> SELECT * FROM learning_metrics;
```

## Security

### Local Processing
- PDF files are processed locally on your machine
- Only necessary data is sent to LLM providers
- No sensitive data is stored permanently

### Document Privacy
- Temporary files are securely deleted
- Authentication required for web interface
- All actions are logged

### API Keys
- Store API keys in environment variables
- Never commit `.env` to version control
- Use different keys for development vs. production

## Troubleshooting

### "No vision providers available"
- Check that Claude API key is set OR Ollama is running
- Verify `config/llm_config.yaml` has at least one provider enabled
- Check logs for provider health check failures

### "Cannot connect to Ollama"
- Make sure Ollama is running: `ollama serve`
- Verify OLLAMA_BASE_URL in `.env` is correct
- Check that required models are installed: `ollama list`

### PDF conversion errors
- Ensure `pdf2image` and `poppler` are installed
- On macOS: `brew install poppler`
- On Ubuntu: `apt-get install poppler-utils`
  
Tip: for quick tests without AI, set `analyze=false` on the upload endpoint.

### Test warnings
- SQLAlchemy deprecation about `declarative_base()`: addressed by importing from `sqlalchemy.orm`.
- Pydantic v2 deprecation about class-based Config: replaced with `model_config`.
- PyPDF2 deprecation: third‑party library emits a warning; tests filter it via `pytest.ini`. Optionally migrate to `pypdf` in the future.

### Authentication
- Settings update endpoint requires Basic auth when `AUTH_ENABLED=true`.
- Default credentials (change in `.env`): `DEFAULT_USERNAME=admin`, `DEFAULT_PASSWORD=changeme`.

### Observability
- Each response includes `X-Request-ID` and `X-Response-Time-ms` headers.
- Logs include per-request line with request id (rid), method, path, and duration.

### Database errors
- Delete `docflow.db` to reset database
- Re-run: `python -c "from database.database import init_db; init_db()"`

## Development

### Project Structure
```
docflow/
├── main.py                  # FastAPI application entry point
├── config/                  # Configuration files
│   ├── settings.py
│   └── llm_config.yaml
├── llm/                     # LLM provider implementations
│   ├── base.py
│   ├── claude_provider.py
│   └── ollama_provider.py
├── database/               # Database models and setup
│   ├── models.py
│   └── database.py
├── services/              # Business logic services
│   ├── pdf_processor.py
│   ├── document_analyzer.py
│   ├── folder_router.py
│   ├── learning_engine.py
│   └── file_manager.py
├── api/                   # FastAPI routes
│   ├── upload.py          # MVP: upload, correction, get-by-id
│   └── __init__.py
├── utils/                 # Utility functions
│   ├── logging_config.py
│   └── security.py
├── frontend/              # React application
│   └── src/
├── tests/                 # Test suite
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

### Running Tests
```bash
pytest tests/ -v
```

### Linting and Formatting
```bash
# Check code style
flake8 . --max-line-length=120

# Format code
black .

# Type checking
mypy .
```

## Performance Optimization

### For Faster Processing
- **Use Claude API** for higher accuracy but with minimal latency
- **Use Ollama locally** for free processing without API calls
- **Batch processing** multiple documents together
- **Adjust LLM timeout** in config if you have slower connection

### Scaling
- For multiple users, deploy with proper authentication
- Use PostgreSQL instead of SQLite for better concurrency
- Implement document processing queue for high volume
- Cache LLM analysis results for duplicate documents

## Future Enhancements

🚀 Planned features:
- Web-based UI for folder management
- Document search and tagging
- Bulk processing with job queue
- Desktop application (Electron)
- Mobile app support
- Advanced learning algorithms
- Document OCR and full-text search
- API for third-party integrations

## License

This project is provided as-is for personal use.

## Support

For issues, questions, or feature requests:
1. Check the troubleshooting section
2. Review existing issues
3. Create detailed bug reports with logs

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Changelog

### v0.1.0 (MVP Backend)
- FastAPI app running with status route
- Upload flow: split PDF into pages and organize files
- Optional analysis via Ollama/Claude (`analyze` + `dpi` params)
- Document fetch and correction endpoints

### v1.0.0 (Planned)
- Refined analysis and routing
- Learning system metrics and UI
- Web interface for uploads and review
- Security and authentication

---

**DocFlow** - Intelligent document management, powered by AI
