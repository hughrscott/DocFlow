# Phase 1 — Core CLI Engine

## Goal
A working command-line tool that processes a single scan PDF and correctly files all
documents it contains. Hardcoded for Morgan Redwood's specific setup. No UI, no web server,
no database. Just a pipeline that runs and produces correctly organised files.

## Prerequisites
- Python 3.11+ installed
- Tesseract installed (`brew install tesseract` / `apt install tesseract-ocr`)
- Ollama running locally with at least one model pulled (`ollama pull llama3.2`)
- `poppler` installed for pdf2image (`brew install poppler`)

## Definition of Done
Running `python main.py --input Scan03282026-3.pdf` on Hugh's test scan:
- Produces correctly named, correctly placed PDF files for all 13 documents
- Produces `MailArchivingSummary.xlsx` and `MailArchivingSummary.txt`
- Moves the original scan to `ToBeOrganized/BeenOrganized[mmddyy]/`
- Logs every filing decision with the rule that triggered it
- Zero documents silently misfiled (low-confidence items printed to console for review)

---

## Task List

### 1. Project Scaffolding
- [ ] Create `requirements.txt` with: `pypdf`, `pdf2image`, `pytesseract`, `Pillow`,
      `ollama`, `openpyxl`, `pyyaml`, `rich` (for console output), `click` (CLI)
- [ ] Create `main.py` as the CLI entry point using `click`
- [ ] Create empty module files for each `src/` subdirectory with `__init__.py`
- [ ] Create `config/default_config.yaml` pre-populated with Hugh's full config
      (copy the schema from ARCHITECTURE.md and fill in all Hugh's entities and rules)
- [ ] Write `pytest` smoke test that imports all modules without error

### 2. Ingestion Module (`src/ingestion/`)
- [ ] `loader.py` — accepts a PDF path, returns list of page images via `pdf2image`
      at 150 DPI (balance between OCR accuracy and speed)
- [ ] Handle PDFs with rotated pages: try 0°, if OCR confidence < 0.3, retry at 90°/180°
- [ ] Unit test: load a known PDF, assert correct page count

### 3. OCR + Signal Extraction (`src/ocr/`)
- [ ] `analyzer.py` — runs Tesseract on each page image, returns `PageRecord`
- [ ] Signal extractor: scan raw text for institution names, account hints, period hints,
      "Page X of N" patterns, doc type keywords
- [ ] Build a `SIGNAL_LIBRARY` dict of known institutions and their keyword variants:
      ```python
      SIGNAL_LIBRARY = {
          "pnc": ["pnc bank", "pncbank", "@pncbank"],
          "guardian": ["guardian life", "guardian anytime", "po box 981572"],
          "lloyds": ["lloyds bank", "lloyds bank international"],
          "bettencourt": ["bettencourt tax", "bta"],
          "pesikoff": ["pesikoff", "core primary care", "privia"],
          "frost": ["frost bank", "cullen/frost"],
          "transnational": ["transnational", "celero", "9550 west higgins"],
          "houston_alarm": ["city of houston", "houston emergency center",
                            "burglar alarm administration"],
          "bluecross": ["blue cross", "blue shield", "bcbs"],
          # Add more as discovered
      }
      ```
- [ ] Period extractor: regex for common date formats found in statements:
      MM/DD/YYYY, Month YYYY, MM-YY, "For the period", "Statement Date"
- [ ] Unit test with sample page text strings covering each signal type

### 4. Document Clustering (`src/clustering/`)
- [ ] `clusterer.py` — groups `PageRecord` list into `DocumentCandidate` list
- [ ] Rule-based pass first (fast, no LLM):
      - Pages with same institution + same account hint + sequential "Page X of N"
        → strong candidate for same document
      - Blank pages → assign to the document they immediately follow
      - "Page 1 of N" → always starts a new document
- [ ] Ollama pass second (for ambiguous groups only):
      - If rule-based pass yields confidence < 0.7 for a group, send to Ollama
      - Prompt template: see `.claude/rules/ollama-prompts.md` → Clustering Prompt
- [ ] IMPORTANT: Every page must be assigned to exactly one candidate. Assert this.
- [ ] Unit test: given a list of PageRecords with known boundaries, assert correct groupings

### 5. Classification + Routing (`src/classification/`)
- [ ] `classifier.py` — takes a `DocumentCandidate`, returns a `FilingDecision`
- [ ] Rule matching: iterate `filing_rules` in config order, first match wins
- [ ] Match logic per rule: check institution signal, account hints, doc type hints
      (all must match if specified; unspecified fields are wildcards)
- [ ] Filename generation: fill `filename_template` from candidate fields
      - `{period}` → format as MonthYYYY (e.g. "February2026") or "YYYY" for annual docs
      - `{person}` → family member name if detected, else omit
      - `{doc_type}` → "Invoice", "Statement", "EOB", etc.
      - `{year}` → 4-digit year, used for tax directory routing
- [ ] If no rule matches → confidence = 0.0, send to review queue with full candidate data
- [ ] Ollama fallback: if rule-based confidence < 0.6, ask Ollama to suggest a rule
      Prompt template: see `.claude/rules/ollama-prompts.md` → Classification Prompt
- [ ] Unit test: given known DocumentCandidates, assert correct FilingDecisions

### 6. Extraction + Filing (`src/extraction/` and `src/filing/`)
- [ ] `extractor.py` — uses `pypdf` to extract pages and write named PDFs to target dirs
- [ ] `filer.py` — creates target directories if they don't exist, moves files
- [ ] `confidence_gate.py` — separates FilingDecisions into auto_file=True and False
      Threshold read from config (`confidence_threshold`, default 0.75)
- [ ] Low-confidence decisions: print to console in a clear format, do NOT file them
      (Phase 2 will add a proper review UI)
- [ ] Decision log: write `filing_log_[timestamp].json` to archive root after each run
      Each entry: filename, target_dir, rule_matched, confidence, pages_extracted
- [ ] Unit test: given FilingDecisions, assert correct files are written to correct dirs

### 7. Summary Generation (`src/summary/`)
- [ ] `generator.py` — reads the filing log and produces:
  - `MailArchivingSummary.xlsx` with columns:
    File Name | Folder Location | Document Type | Time Period | Size (MB) | Pages | Rule Matched | Confidence
  - `MailArchivingSummary.txt` — human-readable version
  - Both written to archive root
- [ ] If a previous summary exists, append new entries rather than overwrite
- [ ] Unit test: given a filing log, assert correct summary content

### 8. Archive Original (`src/ingestion/archiver.py`)
- [ ] After successful run, move original scan PDF to:
      `{scan_watch_folder}/BeenOrganized{mmddyy}/`
- [ ] Date format: mmddyy (e.g., 033026 for March 30, 2026)
- [ ] If `BeenOrganized{mmddyy}/` already exists, append to it (don't error)

### 9. Integration + End-to-End Test
- [ ] Run the full pipeline on `Scan03282026-3.pdf` (Hugh's test scan)
- [ ] Verify all 13 expected documents are produced (see `docs/TEST_CASES.md`)
- [ ] Verify `MailArchivingSummary.xlsx` and `.txt` are created
- [ ] Verify original PDF moved to `BeenOrganized033026/`
- [ ] Review console output for any low-confidence items
- [ ] Fix any incorrect filings by updating `default_config.yaml` rules

---

## Known Edge Cases from Real Data
These were discovered during the manual archiving session that preceded this project.
Handle them explicitly:

| Edge Case | What to Do |
|---|---|
| Blue Shield/BCBS pages follow PNC pages directly | Signals for each are distinct — never let PNC signal bleed into adjacent pages |
| Bettencourt has two separate documents in same scan | Different page counts (1 page vs 2 pages) — flag as possible duplicate, don't merge |
| Houston Alarm docs are interspersed with PNC pages | Houston Emergency Center signal overrides PNC — file under Businesses/BluebirdSolar |
| Sulis Solar is a PNC account but NOT personal | Any PNC page with "Sulis Solar" or account #7364 → Household/PNC, never PNC/Personal |
| Maryland Registered Agent → Northstar Holdings | No "Northstar Holdings" text on the invoice — rule must match on "Corporate Filings LLC" or "Maryland" + invoice type |
| Pages with no OCR text (blank or all-image) | Assign to preceding document; log as low-confidence |
| Upside-down pages (OCR returns garbage) | Retry at 180°; if still garbage, assign to nearest document and flag for review |

---

## Phase 1 Completion Checklist
- [ ] `pytest tests/ -v` passes with zero failures
- [ ] End-to-end test on real scan produces all 13 expected files
- [ ] No document is silently misfiled (every low-confidence item surfaces to console)
- [ ] Filing log JSON is written after every run
- [ ] Code passes `ruff check src/` with zero errors
- [ ] `README.md` written with install + usage instructions

## Advance to Phase 2 When
All items above are checked. See @PHASE-2.md.
