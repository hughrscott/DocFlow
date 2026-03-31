# Mail Archiver — Claude Code Project

## What This Is
A local-first, Ollama-powered application that ingests multi-page scanned mail PDFs,
identifies and separates individual documents using OCR + LLM reasoning, names them
meaningfully, and files them into a structured directory archive — with no data leaving
the user's machine.

## Architecture Overview
See @docs/ARCHITECTURE.md for the full system design.

## Current Phase
**Phase 1** — See @docs/PHASE-1.md
(Update this line when advancing to Phase 2 or 3.)

## Tech Stack
- **Language**: Python 3.11+
- **OCR**: Tesseract via `pytesseract` + `pdf2image`
- **PDF manipulation**: `pypdf`
- **LLM (local)**: Ollama — default model `llama3.2` or `mistral`
- **Config format**: YAML (`user_config.yaml`)
- **Summary output**: `openpyxl` for .xlsx, plain .txt

## Project Structure
```
mailarchiver/
├── CLAUDE.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PHASE-1.md
│   ├── PHASE-2.md
│   └── PHASE-3.md
├── .claude/
│   └── rules/
│       ├── python-style.md
│       ├── ollama-prompts.md
│       └── filing-domain.md
├── src/
│   ├── ingestion/       # PDF loading, pdf2image conversion
│   ├── ocr/             # Tesseract page analysis, structured page JSON
│   ├── clustering/      # Group pages into document candidates
│   ├── classification/  # Ollama calls, filing rule matching
│   ├── extraction/      # pypdf page extraction, file writing
│   ├── filing/          # Directory routing, filename generation
│   ├── review/          # Confidence gating, human review queue
│   └── summary/         # MailArchivingSummary.xlsx + .txt generation
├── config/
│   ├── default_config.yaml      # Shipped defaults, never edited by user
│   └── user_config.yaml         # User-specific rules, directories, entities
├── tests/
└── main.py
```

## Build & Run Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Run on a scan PDF
python main.py --input /path/to/Scan.pdf --config config/user_config.yaml

# Run tests
pytest tests/ -v

# Lint
ruff check src/
```

## Critical Domain Rules
See @.claude/rules/filing-domain.md — these govern ALL filing decisions.
See @.claude/rules/ollama-prompts.md — governs how to write/modify Ollama prompts.

## Key Constraints — Read Before Writing Any Code
- NEVER send document content to any external API. All LLM calls go to local Ollama only.
- Every filing decision must be traceable: log the rule that triggered it.
- Confidence scores below the threshold go to the review queue — never auto-file silently.
- The user_config.yaml is the single source of truth for filing rules. Code must not hardcode filing logic.
- All file paths in config use `~` expansion and are OS-agnostic.
