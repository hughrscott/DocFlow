# DocFlow — The Digital Archivist

Local-first application that ingests multi-page scanned mail PDFs, identifies and separates individual documents using OCR + AI, names them meaningfully, and files them into a structured directory archive.

## Prerequisites

- **Python 3.11+**
- **Tesseract OCR**: `brew install tesseract`
- **Poppler** (PDF rendering): `brew install poppler`
- **Ollama** (optional, for local LLM): `brew install ollama && ollama pull llama3.2`

## Install

```bash
git clone https://github.com/hughrscott/DocFlow.git
cd DocFlow
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## First-time setup

```bash
docflow init
```

This creates `~/.docflow/config.yaml` and walks you through setting your archive directory and LLM provider.

To use a cloud LLM (OpenRouter, OpenAI, Groq), copy `.env.example` to `.env` and add your API key.

## Verify installation

```bash
docflow check
```

## Usage

### Start the web UI

```bash
docflow start
```

Open http://localhost:8765. Upload scanned PDFs and DocFlow will OCR, classify, and file them.

### Other commands

| Command | What it does |
|---|---|
| `docflow start` | Start DocFlow in the background |
| `docflow stop` | Stop DocFlow |
| `docflow status` | Check if running |
| `docflow logs` | Tail log files |
| `docflow ui` | Run the web UI in the foreground |
| `docflow check` | Verify system dependencies |
| `docflow init` | First-time setup wizard |
| `docflow --input scan.pdf` | Process a single PDF via CLI |
| `docflow batch /path/to/dir` | Process all PDFs in a directory |
| `docflow watch` | Watch scan folder for new PDFs |
| `docflow install-service` | Auto-start on login (macOS) |
| `docflow uninstall-service` | Remove auto-start |

### Auto-start on login (macOS)

```bash
docflow install-service
```

This installs a LaunchAgent that starts DocFlow automatically when you log in.

## How it works

1. **Upload** a scanned PDF via the web UI or CLI
2. **OCR** extracts text from each page (Tesseract, with auto-rotation)
3. **Clustering** groups pages into individual documents (AI-assisted)
4. **Classification** matches each document to a filing rule using AI + `config/rules.md`
5. **Dedup** checks content hashes to skip documents already in the archive
6. **Filing** writes named PDFs to the correct directories
7. **Review** — low-confidence items go to the review queue for human approval

## Configuration

- `config/default_config.yaml` — template config (copy to `~/.docflow/config.yaml`)
- `config/rules.md` — human-readable filing rules (AI reads these for classification)
- `.env` — API keys (not tracked in git)

## Filing Rules

Rules live in `config/rules.md` as human-readable markdown. The AI reads them to decide where to file each document. When you correct a filing in the review queue, DocFlow automatically learns and adds new rules.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check docflow/
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design.
