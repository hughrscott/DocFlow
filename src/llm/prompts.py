"""Prompt templates for LLM-assisted clustering and classification."""
from __future__ import annotations


def build_clustering_prompt(page_summaries: list[dict]) -> str:
    """Build a prompt asking the LLM to group pages into documents.

    Args:
        page_summaries: List of dicts with keys:
            page_number, first_lines, institution_hint, account_hint,
            period_hint, doc_type_hint, page_of_n
    """
    pages_text = ""
    for p in page_summaries:
        signals = []
        if p.get('institution_hint'):
            signals.append(f"institution={p['institution_hint']}")
        if p.get('account_hint'):
            signals.append(f"account={p['account_hint']}")
        if p.get('doc_type_hint'):
            signals.append(f"doc_type={p['doc_type_hint']}")
        if p.get('period_hint'):
            signals.append(f"period={p['period_hint']}")
        if p.get('page_of_n'):
            signals.append(f"pagination={p['page_of_n']}")

        signal_str = " | ".join(signals) if signals else "no signals detected"
        text = p.get('first_lines', '(blank)')

        pages_text += f"""
--- PAGE {p['page_number']} ---
Signals: {signal_str}
Text:
{text}
"""

    return f"""You are a document boundary detection system. You are analyzing a scanned PDF
that contains MULTIPLE unrelated pieces of mail that were batch-scanned together.

Your task: determine where one document ends and the next begins.

Each page below has been OCR'd. You also see extracted "signals" (institution name,
account number, document type, date period, pagination) that were detected by keyword
matching — these are helpful hints but can be wrong or missing.

{pages_text}

RULES FOR GROUPING:
1. Each page must belong to exactly one document. No page left unassigned.
2. This is batch-scanned mail — expect MANY separate documents (typically 1-3 pages each).
3. A group of 5+ pages is unusual — only for long statements with explicit "Page X of Y".
4. "Page 1 of N" ALWAYS starts a new document. "Page 2 of 3" continues the current one.
5. Blank pages (no/minimal text) belong to the document immediately before them.
6. Same letterhead/header on consecutive pages = same document (continuation).
7. Different institution or completely different content = different document.
8. Two documents from the SAME institution CAN appear in the same scan (e.g., two
   separate PNC statements for different accounts). Look at account numbers and dates.
9. W-2 tax forms come as multiple copies (Copy A, B, C, D) — group ALL copies together.
10. A tax bill with an attached payment stub or comparison table = one document.
11. A letter followed by a reply envelope page = one document.
12. Court filings: an affidavit + the business record it's attached to = one document.
13. When genuinely uncertain, prefer SPLITTING over merging.

Respond with JSON:
{{
  "documents": [
    {{
      "pages": [1, 2],
      "institution": "institution name or null",
      "doc_type": "statement | invoice | letter | form | legal | eob | receipt | notice | w2 | tax_bill | insurance | mortgage | payroll | other",
      "period": "MonthYYYY or YYYY or null",
      "confidence": 0.9,
      "reasoning": "one sentence explaining why these pages belong together"
    }}
  ]
}}"""


def build_rules_md_classification_prompt(
    document_summary: dict,
    rules_md: str,
    entities: list[dict],
    family: list[dict],
    user: dict,
) -> str:
    """Build a classification prompt using the human-readable rules.md file.

    This is the primary classification prompt. The LLM reads the rules.md
    as natural language and decides where to file the document.
    """
    entities_text = "\n".join(
        f"  - {e['name']} (type: {e.get('type', '?')}, dir: {e.get('directory', '?')})"
        for e in entities
    )
    family_text = "\n".join(
        f"  - {f['name']} ({f.get('relation', '?')})"
        for f in family
    )

    return f"""You are classifying a scanned document for filing into a personal/business archive.

DOCUMENT TO CLASSIFY:
  Pages: {document_summary['pages']}
  Institution (from OCR signals): {document_summary.get('institution', 'unknown')}
  Document type (from OCR signals): {document_summary.get('doc_type', 'unknown')}
  Period: {document_summary.get('period', 'unknown')}
  Account hint: {document_summary.get('account', 'unknown')}

  Text preview (first ~500 chars of page 1):
  {document_summary.get('raw_text_preview', '(no text)')}

USER CONTEXT:
  Name: {user.get('name', 'Unknown')}
  Address: {user.get('address', 'Unknown')}
  Family: {family_text or '  (none)'}
  Business entities: {entities_text or '  (none)'}

FILING RULES:
The following rules describe known document types and where they should be filed.
Read them carefully and match the document to the best rule. If no rule fits,
suggest a new sensible filing location.

---
{rules_md}
---

INSTRUCTIONS:
1. Read the document's text preview carefully. Determine its TRUE nature — do NOT
   rely solely on the OCR signal hints (they use simple keyword matching and can be wrong).
2. Match the document to the most appropriate rule from the Filing Rules above.
   If a rule matches, use its "File to" path and "Filename" template.
3. Fill in template variables: {{period}} = MonthYYYY format (e.g., "March2026"),
   {{year}} = 4-digit year, {{doc_type}} = document type, {{person}} = family member name.
4. If NO existing rule matches, suggest a sensible filing directory and filename.
   Use the same conventions as the existing rules.
5. Assess your confidence (0.0 to 1.0) in the filing decision.
6. The "rule_matched" field should be the rule heading (e.g., "Pnc Sulis Solar Checking")
   if you matched an existing rule, or null if suggesting a new location.

Respond with JSON:
{{
  "rule_matched": "rule heading from Filing Rules, or null if new",
  "institution": "institution name (cleaned up from OCR)",
  "doc_type": "document type",
  "period": "time period in MonthYYYY format if found, otherwise null",
  "person": "family member name if applicable, otherwise null",
  "suggested_filename": "final filename with template variables filled in, e.g. PNCBankSulisSolarCheckingMarch2026.pdf",
  "suggested_directory": "relative path under archive root from the rule's File to, e.g. SulisSolar/PNC",
  "confidence": 0.85,
  "reasoning": "brief explanation of the classification decision"
}}"""


def build_classification_prompt(
    document_summary: dict,
    filing_rules: list[dict],
    entities: list[dict],
    family: list[dict],
    user: dict,
) -> str:
    """Build a prompt asking the LLM to classify a document and decide filing.

    Args:
        document_summary: Dict with keys: pages, institution, doc_type, period,
            account, raw_text_preview
        filing_rules: The filing_rules list from config.
        entities: The entities list from config.
        family: The family list from config.
        user: The user dict from config.
    """
    rules_text = "\n".join(
        f"  - id: {r['id']}\n"
        f"    match: {r.get('match', {})}\n"
        f"    file_to: {r.get('file_to', '?')}\n"
        f"    filename_template: {r.get('filename_template', '?')}"
        for r in filing_rules
    )

    entities_text = "\n".join(
        f"  - {e['name']} (type: {e.get('type', '?')}, dir: {e.get('directory', '?')})"
        for e in entities
    )

    family_text = "\n".join(
        f"  - {f['name']} ({f.get('relation', '?')})"
        for f in family
    )

    return f"""You are classifying a scanned document for filing into a personal/business archive.

DOCUMENT:
  Pages: {document_summary['pages']}
  Institution (from OCR signals): {document_summary.get('institution', 'unknown')}
  Document type (from OCR signals): {document_summary.get('doc_type', 'unknown')}
  Period: {document_summary.get('period', 'unknown')}
  Account hint: {document_summary.get('account', 'unknown')}

  Text preview (first ~500 chars of page 1):
  {document_summary.get('raw_text_preview', '(no text)')}

USER CONTEXT:
  Name: {user.get('name', 'Unknown')}
  Address: {user.get('address', 'Unknown')}
  Family: {family_text or '  (none)'}
  Business entities: {entities_text or '  (none)'}

AVAILABLE FILING RULES:
{rules_text}

INSTRUCTIONS:
1. First, determine the TRUE nature of this document from the text preview. Do NOT rely
   solely on the OCR signal hints — they use simple keyword matching and are often wrong.
   For example, a court filing that mentions "line of credit" is a LEGAL document, not a
   line of credit statement.
2. Only match an existing filing rule if the document genuinely belongs to that institution
   AND document type. If the institution is "unknown" or doesn't match the rule's institution,
   do NOT suggest that rule — suggest a new filing location instead.
3. If no existing rule matches, suggest a sensible filing location and filename.
4. Assess your confidence (0.0 to 1.0) in the filing decision.
5. If you can identify a specific person (from family list) that this document relates to, include them.

Respond with JSON:
{{
  "rule_matched": "rule id if an existing rule matches, otherwise null",
  "institution": "institution name (cleaned up from OCR)",
  "doc_type": "document type (statement, invoice, letter, form, legal, eob, receipt, notice, tax, etc.)",
  "period": "time period in MonthYYYY format if found, otherwise null",
  "person": "family member name if applicable, otherwise null",
  "suggested_filename": "descriptive filename like InstitutionDocTypePeriod.pdf",
  "suggested_directory": "relative path under archive root, e.g. Tax/2025 Taxes or Insurance/Guardian Health",
  "confidence": 0.85,
  "reasoning": "brief explanation of the classification decision"
}}"""
