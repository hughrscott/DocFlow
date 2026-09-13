# Architecture — Mail Archiver

## Design Philosophy

1. **Local-first, privacy by default.** No document content ever leaves the machine. OCR, LLM reasoning, and filing all run locally via Ollama + Tesseract. When cloud LLMs are supported (Phase 3+), sensitive data is redacted via a local hash table before any external call — see *Cloud LLM Redaction Strategy* below.
2. **Config-driven, not code-driven.** All filing rules, entity mappings, and directory structures live in `user_config.yaml`. The engine is generic; the intelligence is in the config.
3. **Contextual differentiation.** The core problem this solves: the same *type* of document (e.g. bank statement, electric bill) must be filed differently based on *context* — which account, which person, which address, which entity. The differentiating fields vary by document type: bank statements differentiate on account number/entity, medical EOBs on patient name, utility bills on service address. Signal extraction and filing rules must support this dimension.
4. **Confidence-gated.** Every filing decision has a confidence score. High confidence → auto-file. Low confidence → human review queue. Nothing is silently wrong.
5. **Traceable.** Every output file logs which rule caused it to be filed where. A human can always audit why a decision was made.
6. **Designed for productisation.** The personal use case (Morgan Redwood) is Phase 1. The product (any user) is Phase 3. Architecture decisions must support both.

---

## Pipeline Overview

```
[Scan PDF]
     │
     ▼
┌─────────────────────────────────┐
│  1. INGESTION                   │
│  Load PDF, convert pages to     │
│  images via pdf2image           │
└──────────────┬──────────────────┘
               │  List[PageImage]
               ▼
┌─────────────────────────────────┐
│  2. OCR + PAGE ANALYSIS         │
│  Tesseract → raw text per page  │
│  Signal extractor → structured  │
│  PageRecord JSON per page       │
└──────────────┬──────────────────┘
               │  List[PageRecord]
               ▼
┌─────────────────────────────────┐
│  3. DOCUMENT CLUSTERING         │
│  Group pages into candidate     │
│  documents using signals +      │
│  Ollama reasoning pass          │
└──────────────┬──────────────────┘
               │  List[DocumentCandidate]
               ▼
┌─────────────────────────────────┐
│  4. CLASSIFICATION + ROUTING    │
│  Match each candidate to a      │
│  filing rule in user_config.    │
│  Ollama resolves ambiguous cases│
│  Assign: target_dir + filename  │
│  + confidence score             │
└──────────────┬──────────────────┘
               │  List[FilingDecision]
               ▼
┌─────────────────────────────────┐
│  5. CONFIDENCE GATE             │
│  score >= threshold → auto-file │
│  score <  threshold → review Q  │
└──────────┬──────────────────────┘
           │              │
    [Auto-file]     [Review Queue]
           │              │
           ▼              ▼
┌─────────────────┐  ┌──────────────────────────┐
│  6. EXTRACTION  │  │  Phase 2: Review UI       │
│  pypdf writes   │  │  Human approves/corrects  │
│  named PDFs to  │  │  decisions, then files    │
│  target dirs    │  └──────────────────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│  7. SUMMARY GENERATION          │
│  MailArchivingSummary.xlsx      │
│  MailArchivingSummary.txt       │
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│  8. ARCHIVE ORIGINAL            │
│  Move scan PDF to               │
│  ToBeOrganized/BeenOrganized    │
│  [mmddyy]/                      │
└─────────────────────────────────┘
```

---

## Data Models

### PageRecord
Produced by the OCR + Signal Extraction step. One per page.

```python
@dataclass
class PageRecord:
    page_number: int          # 1-indexed
    raw_text: str             # Full Tesseract output
    institution: str | None   # e.g. "PNC Bank", "Guardian Life"
    account_hint: str | None  # e.g. "Sulis Solar", "Personal LOC", last 4 digits
    period_hint: str | None   # e.g. "February 2026", "01/21/26"
    page_of_n: str | None     # e.g. "Page 2 of 4"
    doc_type_hint: str | None # e.g. "Statement", "Invoice", "EOB", "Receipt"
    confidence: float         # 0.0–1.0 — how clean the OCR was
    rotation_applied: int     # 0, 90, 180, 270
```

### DocumentCandidate
Produced by the Clustering step. One per identified document.

```python
@dataclass
class DocumentCandidate:
    pages: list[int]              # 1-indexed page numbers in this document
    institution: str              # Best guess from majority signal
    account: str | None
    period: str | None
    doc_type: str | None
    clustering_confidence: float  # How certain we are these pages belong together
    raw_signals: dict             # Debug: all signals that drove this decision
```

### FilingDecision
Produced by the Classification step. One per DocumentCandidate.

```python
@dataclass
class FilingDecision:
    candidate: DocumentCandidate
    filename: str              # e.g. "PNCBankBluebirdSolarCheckingFebruary2026.pdf"
    target_directory: str      # Absolute path
    rule_matched: str          # e.g. "pnc_business_checking"
    confidence: float          # 0.0–1.0
    auto_file: bool            # True if above threshold
    notes: str | None          # Populated when Ollama flags uncertainty
```

---

## Configuration Schema (user_config.yaml)

```yaml
# Global settings
archive_root: ~/DocFlowExample/archive
scan_watch_folder: ~/DocFlowExample/inbox
confidence_threshold: 0.75       # Below this → review queue
ollama_model: llama3.2
ollama_host: http://localhost:11434

# User identity (helps LLM resolve ambiguous names)
user:
  name: Morgan Redwood
  address: 100 Example Avenue, Sampleton, TX 77005

# Family members — used to assign medical/personal documents
family:
  - name: Jordan Redwood
    relation: spouse
  - name: Casey Redwood
    relation: son
  - name: Riley Redwood
    relation: daughter

# Business entities — CRITICAL for business vs personal routing
entities:
  - id: redwood_household
    name: Redwood Household
    type: business
    directory: Household
    banks: [pnc]
    account_hints: ["4102", "7364"]

  - id: bluebird_solar
    name: Bluebird Solar
    legal_name: Bluebird Solar LLC
    type: business
    directory: Businesses/BluebirdSolar
    banks: [frost]
    address: "200 Example Road, Sampleton, MD 20001"

  - id: cedar_lane_music
    name: Cedar Lane Music
    legal_name: Cedar Lane Music LLC
    type: business
    directory: Businesses/CedarLaneMusic
    banks: [frost]

  - id: northstar_holdings
    name: Northstar Holdings
    type: investment
    directory: Investments/NorthstarHoldings

  - id: sampleton_foundation
    name: Sampleton Foundation
    type: business
    directory: Giving/SampletonFoundation

# Filing rules — evaluated top to bottom, first match wins
filing_rules:
  - id: pnc_bluebird_solar_checking
    match:
      institution: pnc
      account_hints: ["4102", "solar", "7364"]
      doc_type: [statement, checking]
    file_to: Household/PNC
    filename_template: "PNCBankBluebirdSolarChecking{period}.pdf"

  - id: pnc_personal_loc
    match:
      institution: pnc
      doc_type: [line_of_credit, loc]
    file_to: PNC/Personal/LOC
    filename_template: "PNCPersonalLineOfCredit{period}.pdf"

  - id: frost_sor_houston_sw
    match:
      institution: frost
      entity_hints: ["sor houston", "742 e 20th", "school of rock"]
    file_to: Frost/SORHoustonSW/Checking
    filename_template: "FrostSORHoustonSWChecking{period}.pdf"

  - id: guardian_insurance_eob
    match:
      institution: guardian
      doc_type: [eob, explanation_of_benefits]
    file_to: Insurance/Guardian Health
    filename_template: "GuardianLifeInsuranceEOB{period}{person}.pdf"

  - id: houston_alarm
    match:
      institution: ["city of houston", "houston emergency", "burglar alarm"]
    file_to: Businesses/BluebirdSolar
    filename_template: "HoustonEmergencyAlarmFeeSchedule{period}.pdf"

  - id: bettencourt_tax
    match:
      institution: bettencourt
      doc_type: [invoice, statement]
    file_to: "Tax/{year} Taxes"
    filename_template: "BettencourtTaxAdvisors{doc_type}{period}.pdf"

  - id: lloyds_uk
    match:
      institution: lloyds
    file_to: UK Finance/Lloyds
    filename_template: "LloydsBankAccountStatement{period}.pdf"

  - id: medical_pesikoff
    match:
      institution: ["pesikoff", "core primary care", "privia"]
      doc_type: [bill, statement, visit_summary]
    file_to: Medical/Pesikoff
    filename_template: "PesikoffCoreVisitBill{period}{person}.pdf"
```

---

## Ollama Integration

Two distinct LLM calls are made. Both use the same local Ollama endpoint.

### Call 1 — Clustering prompt
Given a batch of PageRecords, ask the model to group them into documents.
- Input: List of (page_number, first_3_lines_of_text, institution_hint, period_hint)
- Output: JSON array of page groupings with confidence

### Call 2 — Classification prompt
Given a DocumentCandidate, ask the model to match it to a filing rule.
- Input: DocumentCandidate summary + full list of entity names + filing rules
- Output: JSON with rule_id, filename, target_dir, confidence, notes

Full prompt templates live in `.claude/rules/ollama-prompts.md`.

---

## Contextual Differentiation

This is the core intelligence of the system. Two documents of the same type from the same institution in the same month must still be filed differently if they belong to different contexts.

**The differentiating fields depend on document type:**

| Document Type | Primary Differentiator | Example |
|---|---|---|
| Bank statement | Account number + entity | PNC Sulis Solar vs PNC Personal LOC |
| Medical EOB | Patient name | Guardian EOB for Hugh vs Vivian |
| Utility bill | Service address / account holder | Electric bill for home vs SOR Heights |
| Tax invoice | Entity / tax year | Bettencourt for personal vs Sulis Solar |
| Insurance | Policy holder / dependent | BCBS EOB for Brodie vs Kirstie |

**How it works in the pipeline:**
1. **Signal extraction** (step 2) pulls all available differentiators from OCR text: institution, account numbers, person names, addresses, entity names
2. **Filing rules** (step 4) match on whichever differentiators are relevant for that document type — `account_hints`, `entity_hints`, `person_hints`, `address_hints`
3. **Ollama fallback** — when rule-based matching can't differentiate, the LLM receives the full extracted signals plus the user's entity/family config and reasons about which context applies
4. **Review queue** — when even the LLM is uncertain, the human decides, and the correction feeds back into rule learning (Phase 2)

---

## Cloud LLM Redaction Strategy (Phase 3+)

When cloud LLMs are supported alongside local Ollama, document content must be redacted before leaving the machine. The approach uses a **local hash table** for redaction:

1. **Signal extraction always runs locally** — OCR, regex, keyword matching never need a cloud LLM
2. **Build a redaction hash table** mapping sensitive values to opaque tokens:
   - Personal names → random hash (e.g. `"Morgan Redwood" → "H7x9k"`)
   - Addresses → random hash
   - Account numbers → random hash
   - Entity names → random hash
3. **Before any cloud LLM call**, replace all sensitive values in the text with their hash tokens
4. **Preserve category labels** so the LLM can still reason about structure:
   `"This is a [STATEMENT] from [INSTITUTION:pnc] for [ACCOUNT:H7x9k] belonging to [ENTITY:A3m2p]"`
5. **After the cloud response**, map tokens back to real values locally

**Key decisions:**
- Institution names are likely safe to send unredacted (they're public companies)
- Account numbers, personal names, addresses, and entity names are always redacted
- The hash table lives only on the user's machine and is never transmitted
- Users can choose local-only mode to avoid this entirely

---

## Future: Action Tracking (Phase 3+)

Beyond filing, the system will surface actionable items from scanned documents:

- **Bills due** — flag documents with payment due dates, surface them as reminders
- **Paperwork requests** — detect requests for information or required actions
- **Integration with reminder tools** (e.g. macOS Reminders) to create follow-up items

The system **flags and reminds only** — it never takes payment or other actions on behalf of the user.

---

## Productisation Considerations

The following architectural decisions are made specifically to support a multi-user product:

| Decision | Reason |
|---|---|
| All rules in user_config.yaml, never hardcoded | Any user can have their own config |
| Entity model is first-class | Business vs personal is the most common source of error |
| Contextual differentiators vary by doc type | Same institution can have multiple accounts/people |
| Confidence gating with review queue | Non-technical users need a safety net |
| Rule ID logged with every filing decision | Supports "why did it do that?" UX |
| Archive root is configurable | Users have different folder structures |
| Ollama model is configurable | Users have different hardware capabilities |
| Cloud LLM support with local redaction | Users can choose cloud LLMs without exposing PII |
| Directory inference scanner (Phase 2) | Builds config from existing archive automatically |
| Onboarding wizard (Phase 3) | Non-technical users can't edit YAML |
