"""Prompt templates for LLM-assisted clustering and classification."""
from __future__ import annotations


def build_clustering_prompt(page_summaries: list[dict]) -> str:
    """Build a prompt asking the LLM to group pages into documents.

    Args:
        page_summaries: List of dicts with keys:
            page_number, first_lines, institution_hint, period_hint, doc_type_hint
    """
    pages_text = "\n".join(
        f"  Page {p['page_number']}: "
        f"institution={p.get('institution_hint', 'unknown')} | "
        f"doc_type={p.get('doc_type_hint', 'unknown')} | "
        f"period={p.get('period_hint', 'unknown')}\n"
        f"    Text preview: {p.get('first_lines', '(blank)')}"
        for p in page_summaries
    )

    return f"""Analyze these scanned pages from a multi-document PDF. This is a batch of
unrelated mail that was scanned together. Each page was OCR'd separately.
Your job is to identify where one document ends and the next begins.

PAGES:
{pages_text}

CRITICAL RULES:
- Each page must belong to exactly one document group.
- This is a batch of SEPARATE mail items scanned together. Expect MANY distinct documents
  (typically 1-3 pages each). A group of 5+ pages should be rare — only for long statements.
- A blank page (empty text) should be assigned to the document it immediately follows.
- Look for clues: same letterhead, same institution, "Page X of Y", continuation of content.
- Different institutions or different document types almost always mean different documents.
- Two pages from the same institution CAN be different documents (e.g., two separate statements).
- W-2 tax forms often come as multiple copies (Copy A, B, C, D) — these are ONE document.
- A tax bill and a payment stub are ONE document even if they look different.
- When in doubt, SPLIT rather than merge. It's better to over-split than to merge unrelated docs.

Respond with JSON:
{{
  "documents": [
    {{
      "pages": [1, 2],
      "institution": "best guess institution name or null",
      "doc_type": "best guess type (statement, invoice, letter, form, legal, eob, receipt, notice, w2, tax_bill, etc.) or null",
      "period": "time period if found (e.g. March2026) or null",
      "reasoning": "brief explanation of why these pages belong together"
    }}
  ]
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
