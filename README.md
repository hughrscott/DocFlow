# DocFlow — The Digital Archivist

A local-first application that ingests multi-page scanned mail PDFs, identifies and separates individual documents using OCR + AI reasoning, names them meaningfully, and files them into a structured directory archive.

## Prerequisites

- Python 3.11+
- Tesseract OCR (`brew install tesseract`)
- Poppler for PDF rendering (`brew install poppler`)
- Ollama running locally (`brew install ollama && ollama pull llama3.2`)
- An OpenRouter API key (or other LLM provider) in `.env`

## Install

```bash
git clone https://github.com/hughrscott/DocFlow.git
cd DocFlow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Add your OPENROUTER_API_KEY
```

## Running DocFlow

### Quick start

```bash
./docflow start
```

Open http://localhost:8765 in your browser.

### Commands

| Command | What it does |
|---|---|
| `./docflow start` | Start DocFlow on port 8765 |
| `./docflow stop` | Stop DocFlow |
| `./docflow restart` | Restart DocFlow |
| `./docflow status` | Check if DocFlow is running |
| `./docflow logs` | Tail the log files |

### Run from anywhere

```bash
ln -s ~/Documents/Coding/DocFlow/docflow /usr/local/bin/docflow
```

Then use `docflow start`, `docflow stop`, etc. from any directory.

### Process a single PDF (CLI)

```bash
source .venv/bin/activate
python main.py --input /path/to/Scan.pdf --config config/user_config.yaml
```

## How it works

1. **Upload** a scanned PDF via the web UI or CLI
2. **OCR** extracts text from each page (Tesseract)
3. **Clustering** groups pages into individual documents (AI-assisted)
4. **Classification** matches each document to a filing rule using AI + `config/rules.md`
5. **Filing** writes named PDFs to the correct directories
6. **Review** — low-confidence items go to the review queue for human approval

## Configuration

- `config/user_config.yaml` — entities, family, LLM settings, archive paths
- `config/rules.md` — human-readable filing rules (read by the AI for classification)
- `.env` — API keys

## Filing Rules

Filing rules live in `config/rules.md` as human-readable markdown. The AI reads these rules to decide where to file each document. When you correct a filing in the review queue, DocFlow automatically learns and adds new rules.

## Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design.
