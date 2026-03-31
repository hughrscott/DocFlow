# Phase 2 — Config Layer, Learning & Review Queue

## Goal
Make the engine usable by anyone, not just Hugh. The hardcoded config becomes a
user-editable system. A directory scanner infers the user's existing filing structure.
A review UI replaces the "print to console" approach for low-confidence decisions.
The system learns from corrections.

## Prerequisites
- Phase 1 complete and passing all tests
- End-to-end test on real scan confirmed correct

---

## Task List

### 1. Directory Inference Scanner (`src/config/scanner.py`)
The most powerful onboarding feature: instead of asking the user to write a config
file, read their existing archive and infer the rules automatically.

- [ ] `scan_existing_archive(root_path)` — walks the directory tree and builds a
      draft `user_config.yaml` from what it finds
- [ ] Institution detection: look at existing PDF filenames for known institution names
      (PNC, Frost, Lloyds, Guardian, etc.) and map them to directories
- [ ] Entity detection: look for business-sounding directory names
      (LLC, Solar, Rock, Consulting, etc.) and classify as business vs personal
- [ ] Output: a draft config with a confidence score per rule — uncertain rules
      are flagged with `needs_review: true`
- [ ] CLI command: `python main.py --scan-archive ~/Documents/ElectronicFiles`
      Writes `config/user_config.yaml` and prints a summary of what was inferred
- [ ] Unit test: given a mock directory tree, assert correct config is generated

### 2. Config Validation (`src/config/validator.py`)
- [ ] `validate_config(config_path)` — loads and validates user_config.yaml
- [ ] Check: archive_root exists and is writable
- [ ] Check: all `file_to` directories are valid relative paths (no `..` traversal)
- [ ] Check: Ollama is reachable at configured host
- [ ] Check: configured Ollama model is available (`ollama list`)
- [ ] Check: no two filing rules have identical match conditions (ambiguity warning)
- [ ] CLI command: `python main.py --validate-config config/user_config.yaml`
      Prints pass/fail for each check with clear error messages

### 3. Config Learning — Correction Feedback Loop (`src/config/learner.py`)
When a user corrects a filing decision in the review queue, the system should learn
from it.

- [ ] `record_correction(original_decision, corrected_decision)` — writes to
      `config/corrections_log.json`
- [ ] `suggest_rule_update(corrections_log)` — analyses corrections and suggests
      new or modified rules
      - If 3+ corrections all move the same institution to the same directory,
        propose a new rule
      - Uses Ollama to generate the rule YAML snippet
- [ ] CLI command: `python main.py --review-corrections`
      Shows suggested rule updates, user confirms/rejects each, config updated
- [ ] Unit test: given a corrections log, assert correct rule suggestions

### 4. Review Queue UI (`src/review/`)
Replace the Phase 1 "print to console" approach with a simple local web UI.
Use FastAPI + minimal HTML (no heavy frontend framework needed).

- [ ] `src/review/server.py` — FastAPI app, starts on `localhost:8765`
- [ ] `GET /queue` — returns all pending review items as JSON
- [ ] `POST /approve/{decision_id}` — approves a filing decision, triggers extraction
- [ ] `POST /correct/{decision_id}` — accepts corrected `target_dir` and `filename`,
      records the correction, triggers extraction with corrected values
- [ ] `POST /skip/{decision_id}` — marks as skipped (files to a holding directory)
- [ ] `GET /` — serves a simple HTML page listing all pending items with approve/correct/skip buttons
- [ ] Each item shows: suggested filename, suggested directory, page thumbnails (first page),
      confidence score, and the rule (or lack of one) that produced the suggestion
- [ ] After all items actioned, show a completion summary
- [ ] CLI integration: after auto-filing, if review queue is non-empty, print:
      "X items need review. Run: python main.py --review  or open http://localhost:8765"
- [ ] Unit test: FastAPI routes return correct status codes and payloads

### 5. Watch Folder Mode (`src/ingestion/watcher.py`)
- [ ] `python main.py --watch` — polls `scan_watch_folder` every 60 seconds
- [ ] When a new PDF appears, automatically triggers the pipeline
- [ ] Uses a `.processed` file to track which scans have already been handled
- [ ] Sends a desktop notification when processing completes (via `plyer`)
- [ ] Errors are written to `mailarchiver_errors.log` and also shown as notifications

### 6. Multi-PDF Batch Mode
- [ ] `python main.py --input-dir /path/to/folder` — processes all PDFs in a directory
- [ ] Processes sequentially (not parallel) to keep Ollama load manageable
- [ ] Single combined `MailArchivingSummary.xlsx` for the batch

### 7. Improved Filename Collision Handling
- [ ] If a file with the target name already exists in the target directory:
      - Check if it's the same document (same page count, similar text on page 1)
      - If same: skip, log as duplicate
      - If different: append `_2`, `_3`, etc. to filename
- [ ] Surface all collisions in the review queue regardless of confidence

### 8. Updated Test Suite
- [ ] Integration test for directory scanner using a mock archive tree
- [ ] Integration test for review queue: simulate approvals and corrections
- [ ] Regression test: run on original scan, assert same 13 files as Phase 1

---

## Phase 2 Completion Checklist
- [ ] `python main.py --scan-archive` generates a valid config from an existing archive
- [ ] `python main.py --validate-config` passes on Hugh's config
- [ ] Review queue UI accessible at `localhost:8765` with all pending items visible
- [ ] Corrections are recorded and `--review-corrections` suggests rule updates
- [ ] Watch folder mode processes new files automatically
- [ ] `pytest tests/ -v` passes with zero failures
- [ ] `ruff check src/` zero errors

## Advance to Phase 3 When
All items above are checked. The tool works correctly for Hugh and could be configured
for any technically-minded user who can edit a YAML file. See @PHASE-3.md.
