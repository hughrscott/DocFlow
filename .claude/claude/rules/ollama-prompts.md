# Ollama Prompt Templates

These are the exact prompt templates used for LLM calls. When modifying the
classification or clustering logic, update the prompts here first, then update
the code that references them. Never hardcode prompt text in Python files.

## General Guidance for All Prompts

- Always request JSON output. The pipeline cannot parse free-form text.
- Include a `confidence` field (0.0–1.0) in every response schema.
- Include a `reasoning` field — it goes in the filing log and helps with debugging.
- Keep the input as short as possible. Ollama runs locally; context length affects speed.
- Never include raw OCR text longer than 20 lines per page in a prompt.
- Temperature: 0.1 for classification (deterministic). 0.3 for clustering (allows reasoning).

---

## Clustering Prompt

**Purpose:** Given a batch of pages from a scan, group them into documents.

**When used:** After OCR, before classification. Called only for pages where the
rule-based clusterer produces confidence < 0.7.

**Input format:**
```python
CLUSTERING_SYSTEM_PROMPT = """
You are a document archivist. You will be given a list of pages from a scanned PDF.
Each page has been extracted from a multi-page scan that may contain many different
documents mixed together. Your job is to group the pages into separate documents.

Rules:
- Pages from the same document share the same institution, account, and time period.
- "Page X of N" markers are strong signals — trust them.
- A blank page belongs to the document immediately before it.
- If you are uncertain whether pages belong together, keep them separate and set confidence low.

Respond with valid JSON only. No explanation outside the JSON.
"""

CLUSTERING_USER_TEMPLATE = """
Here are the pages to group. Each entry shows: page number, first 3 lines of text,
institution detected, account hint, period hint.

{page_summaries}

Group these pages into documents. For each group, provide:
- pages: list of page numbers
- institution: best guess at institution name
- account: account name or number hint (null if unknown)
- period: statement period (null if unknown)
- confidence: 0.0-1.0 (how certain you are these pages belong together)
- reasoning: one sentence explaining why these pages are grouped

Return a JSON array of document groups.
"""
```

**Expected output:**
```json
[
  {
    "pages": [1, 2],
    "institution": "PNC Bank",
    "account": "Sulis Solar",
    "period": "February 2026",
    "confidence": 0.92,
    "reasoning": "Both pages show PNC Bank header with Redwood Household account name, page 1 of 2 marker on page 1."
  },
  {
    "pages": [3, 4],
    "institution": "Core Primary Care",
    "account": null,
    "period": "January 2026",
    "confidence": 0.85,
    "reasoning": "Both pages reference patient Riley Redwood and Core Primary Care PLLC with January 2026 service dates."
  }
]
```

---

## Classification Prompt

**Purpose:** Given a document candidate, determine where to file it.

**When used:** After clustering, for any candidate where rule-based matching
produces confidence < 0.6 or no rule is matched at all.

**Input format:**
```python
CLASSIFICATION_SYSTEM_PROMPT = """
You are a document archivist helping to file scanned mail documents.
You will be given a description of a document and a list of filing rules.
Your job is to match the document to the best filing rule and generate a filename.

Filing rules are evaluated in order — use the first matching rule.
If no rule matches, suggest a new directory based on the document type and institution.

Respond with valid JSON only. No explanation outside the JSON.
"""

CLASSIFICATION_USER_TEMPLATE = """
Document to classify:
- Institution: {institution}
- Account/Entity: {account}
- Document type: {doc_type}
- Time period: {period}
- Additional signals: {signals}

User's entities (businesses and family):
{entities_summary}

Filing rules (in priority order):
{rules_summary}

Provide:
- rule_id: the ID of the matching rule, or "new_rule" if none match
- filename: suggested filename following the convention InstitutionAccountTypePeriod.pdf
- target_directory: the directory path relative to archive root
- confidence: 0.0-1.0
- reasoning: one sentence explaining the match
- notes: any concerns or ambiguities (null if none)

Return a single JSON object.
"""
```

**Expected output:**
```json
{
  "rule_id": "pnc_bluebird_solar_checking",
  "filename": "PNCBankBluebirdSolarCheckingFebruary2026.pdf",
  "target_directory": "Household/PNC",
  "confidence": 0.95,
  "reasoning": "PNC Bank statement with Redwood Household as account holder matches rule pnc_bluebird_solar_checking.",
  "notes": null
}
```

---

## Rule Suggestion Prompt

**Purpose:** When a user corrects a filing decision, suggest a new or updated rule.

**When used:** In Phase 2, after the user submits a correction via the review UI.
Called by `src/config/learner.py`.

```python
RULE_SUGGESTION_SYSTEM_PROMPT = """
You are helping a user improve their document filing rules.
You will be shown a filing decision that was corrected by the user.
Your job is to suggest a new YAML rule that would have produced the correct result.

The rule must follow this schema:
  id: unique_snake_case_id
  match:
    institution: string or list of strings (null = match any)
    account_hints: list of strings (null = match any)
    doc_type: list of strings (null = match any)
  file_to: relative directory path
  filename_template: template string using {period}, {person}, {doc_type}, {year}

Respond with valid YAML only. No explanation outside the YAML.
"""

RULE_SUGGESTION_USER_TEMPLATE = """
Original (incorrect) decision:
- Filename: {original_filename}
- Directory: {original_directory}
- Rule matched: {original_rule}

User's correction:
- Correct filename: {correct_filename}
- Correct directory: {correct_directory}

Document signals:
- Institution: {institution}
- Account: {account}
- Doc type: {doc_type}
- Period: {period}

Suggest a YAML rule that would produce the correct result.
"""
```

---

## Implementation Notes

1. All prompt templates are stored as Python string constants in `src/classification/prompts.py`.
2. Never call `ollama.chat()` directly in business logic — always go through
   `src/classification/ollama_client.py` which handles retries, timeout, and JSON parsing.
3. If Ollama returns invalid JSON, retry once with a stricter prompt, then fall back
   to confidence = 0.0 and send to review queue.
4. Log every Ollama call (model, prompt length, response time, output) to
   `ollama_calls.log` for debugging.
