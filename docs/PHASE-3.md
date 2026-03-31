# Phase 3 — Product Shell (Multi-User, Onboarding, Distribution)

## Goal
Turn the tool into something a non-technical person can install, configure, and use
without ever editing a YAML file or touching a terminal. This is the version you
could charge for.

## Prerequisites
- Phase 2 complete and passing all tests
- At least one other person (not Hugh) has successfully used Phase 2 with their own archive

---

## What Changes in Phase 3
Phase 1 and 2 are a power-user tool. Phase 3 wraps that engine in a product shell:
- Installation is a single download, not `pip install`
- Configuration happens through a guided wizard, not YAML editing
- The primary interface is the web UI (extended from Phase 2), not the CLI
- The system is multi-user capable (each user has an isolated profile)

The Phase 1/2 pipeline engine does NOT change. Everything below is additive.

---

## Task List

### 1. Onboarding Wizard (`src/onboarding/`)
A guided setup flow served by the existing FastAPI app. Replaces manual YAML editing.

- [ ] `GET /setup` — serves the onboarding wizard HTML (multi-step form)
- [ ] Step 1: Archive location — user picks their archive root via folder browser
- [ ] Step 2: Existing archive scan — run `scan_existing_archive()` in background,
      show progress bar, present inferred entities and rules for confirmation
- [ ] Step 3: Entity editor — user can add/edit/remove businesses, family members,
      bank accounts with a simple form (no YAML visible)
- [ ] Step 4: Rule review — show inferred filing rules in plain English
      ("When I see a PNC Bank statement for Sulis Solar, I'll file it under SulisSolar/PNC/")
      User can edit or delete each rule
- [ ] Step 5: Test run — let user drop a sample scan PDF to test the config before saving
- [ ] Step 6: Complete — write `user_config.yaml`, show summary of what was configured
- [ ] Wizard state persists across browser refreshes (store in local JSON file)

### 2. Extended Web UI Dashboard (`src/ui/`)
Extend the Phase 2 review queue into a full dashboard.

- [ ] Dashboard home: stats (documents filed this week/month, pending review count,
      last run time, most common document types)
- [ ] Filing history: searchable, filterable table of all filed documents
      (reads from filing logs)
- [ ] Rules editor: visual editor for filing rules — no YAML required
      Each rule shown as: "When institution is [X] and account is [Y] → file to [Z]"
      with edit/delete buttons and a "test this rule" feature
- [ ] Archive browser: tree view of the archive directory, click to open any file
- [ ] Settings page: Ollama model selector, confidence threshold slider,
      watch folder toggle, notification preferences

### 3. Packaging for Distribution

#### macOS
- [ ] Bundle as a `.app` using PyInstaller or Briefcase
- [ ] App starts Ollama (if not running) and the FastAPI server on launch
- [ ] Opens the dashboard in the default browser automatically
- [ ] Menu bar icon showing status (idle / processing / needs review)
- [ ] Installer: standard `.dmg` with drag-to-Applications

#### Windows
- [ ] Bundle with PyInstaller as a single `.exe`
- [ ] System tray icon (not menu bar)
- [ ] Installer: NSIS or Inno Setup `.exe` installer
- [ ] Auto-detects Ollama installation; if missing, shows install instructions

#### Linux
- [ ] AppImage or Flatpak
- [ ] Systemd service file for running as a background daemon

### 4. Ollama Model Management
Non-technical users don't know how to run `ollama pull`.

- [ ] On first launch, check if configured model is available
- [ ] If not: show a setup screen explaining what Ollama is and why it's needed,
      with a "Download model" button that runs `ollama pull` and shows progress
- [ ] Model selector in Settings: list available models with plain-English descriptions
      ("Faster, less accurate" vs "Slower, more accurate")
- [ ] Recommend minimum specs per model (RAM requirements)

### 5. Profile System (Multi-User / Multi-Archive)
Some users have multiple archives (home vs business). Some households share one machine.

- [ ] Each profile has its own `user_config.yaml`, filing logs, and corrections log
- [ ] Profile switcher in the dashboard header
- [ ] "New profile" option in the onboarding wizard
- [ ] CLI: `python main.py --profile business --input Scan.pdf`

### 6. Export + Backup
- [ ] `Export summary as PDF` button — exports the filing history as a formatted PDF report
- [ ] `Backup config` button — exports `user_config.yaml` + `corrections_log.json`
      as a single `.zip` file
- [ ] `Restore config` — import a backup zip to restore settings on a new machine

### 7. Telemetry (Opt-In Only)
For product improvement. NEVER includes document content.

- [ ] Opt-in prompt shown once during onboarding (default: off)
- [ ] If opted in, collect only:
      - Document type distribution (e.g., "60% bank statements, 20% medical")
      - Confidence score distribution
      - Correction rate (how often users override the automatic filing)
      - Error types (OCR failures, unmatched rules)
      - Ollama model used, OS, approximate archive size
- [ ] Sent as anonymous JSON to a configurable endpoint (self-hosted or SaaS)
- [ ] Users can view and delete their telemetry at any time

### 8. Cloud LLM Support with PII Redaction (`src/llm/`)
Allow users to choose cloud LLMs (e.g. Claude API) as an alternative to local Ollama,
with automatic PII redaction so no sensitive data leaves the machine.

#### 8a. PII Identification Layer (`src/llm/pii_detector.py`)
- [ ] Extend signal extraction to tag all sensitive fields found in OCR text:
      person names, addresses, account numbers, entity names, SSNs, DOBs, etc.
- [ ] Use local Ollama to identify PII that regex/keyword matching misses —
      this catches unknown formats, new institution account number patterns, etc.
- [ ] Output: a `PII_Map` per document — list of (value, category, position) tuples
- [ ] Unit test: given sample OCR text, assert all PII is identified and categorised

#### 8b. Redaction Hash Table (`src/llm/redactor.py`)
- [ ] `build_redaction_table(pii_map)` — generates opaque hash tokens for each
      sensitive value, stored in a local-only lookup table
- [ ] `redact_text(text, redaction_table)` — replaces all PII with hash tokens,
      preserving category labels: `[PERSON:H7x9k]`, `[ACCOUNT:N8w4q]`
- [ ] `restore_text(text, redaction_table)` — maps tokens back to real values
- [ ] Institution names pass through unredacted (public companies, needed for routing)
- [ ] Hash table persists per-session only — never written to disk or transmitted
- [ ] Unit test: round-trip redact → restore produces original text

#### 8c. LLM Provider Abstraction (`src/llm/provider.py`)
- [ ] `LLMProvider` interface with `classify()` and `cluster()` methods
- [ ] `OllamaProvider` — current local behaviour, no redaction needed
- [ ] `CloudProvider` — wraps any cloud API, applies redaction before call,
      restores after response
- [ ] Config option: `llm_provider: ollama | claude | openai`
- [ ] Config option: `redaction_mode: auto | always | never` (default: auto —
      redacts for cloud, skips for local)

### 9. Action Tracking & Reminders (`src/actions/`)
Surface actionable items from scanned documents and integrate with reminder tools.
The system **flags only** — it never takes payment or other actions.

- [ ] `action_detector.py` — during classification, identify documents that contain:
      - Payment due dates (bills, invoices)
      - Requests for information or required paperwork
      - Renewal deadlines (insurance, registrations)
      - Appointment confirmations
- [ ] `ActionItem` dataclass: document_ref, action_type, due_date, description, status
- [ ] `reminder_integration.py` — create reminders in macOS Reminders via
      `osascript` / EventKit bridge
- [ ] Action items surface in the summary output and dashboard (Phase 3 UI)
- [ ] Config option to enable/disable action tracking and reminder integration
- [ ] Unit test: given OCR text with due dates, assert correct ActionItems extracted

### 10. Licensing + Activation (If Charging)
- [ ] License key validation on startup (offline-capable — no phoning home required)
- [ ] Trial mode: 3 scans free, then license required
- [ ] License types: Personal (1 machine), Family (3 machines), Business (unlimited)
- [ ] Stripe integration for purchase flow (web only, not in the app)

---

## Product Positioning (Keep in Mind While Building)

**The pitch:** "Scan your mail, drop it in a folder. It organises itself. Everything stays on your computer."

**The differentiation:**
1. **Fully local by default** — medical records, financial statements, legal docs never touch a cloud server. Optional cloud LLMs use local hash-table redaction to protect PII.
2. **Contextual differentiation** — doesn't just identify "bank statement", it knows *which* account, *which* person, *which* entity — and files accordingly
3. **Learns your structure** — scans your existing archive to infer your filing preferences
4. **Correctable** — when it gets something wrong, you correct it once and it learns
5. **Auditable** — every filing decision is logged with the reason
6. **Actionable** — surfaces bills due, paperwork requests, and integrates with reminder tools

**Target users:**
- Small business owners who scan their own mail (primary)
- Self-employed professionals with complex finances (secondary)
- Privacy-conscious individuals with large document archives (secondary)
- Accountants and bookkeepers filing for clients (stretch — needs multi-client support)

---

## Phase 3 Completion Checklist
- [ ] A non-technical person can install the app and configure it without terminal or YAML
- [ ] Onboarding wizard produces a correct config from an existing archive
- [ ] Dashboard shows filing history, pending reviews, and rule editor
- [ ] macOS `.app` and Windows `.exe` installers both work on clean machines
- [ ] Ollama model download works from within the app
- [ ] Cloud LLM option works with PII redaction — no sensitive data in external API calls
- [ ] PII detector catches sensitive fields that regex misses (validated via Ollama locally)
- [ ] Action tracking surfaces bills due and paperwork requests with optional Reminders integration
- [ ] All Phase 1 and 2 tests still pass
- [ ] At least 3 beta testers (non-Hugh) have successfully used the app with their own archives
