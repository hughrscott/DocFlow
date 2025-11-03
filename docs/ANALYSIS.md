# Analysis Strategy (Prompts + Grouping)

Audience: contributors
Owner: maintainers
Last Updated: 2025-11-03

Goals
- Improve document classification accuracy via clearer prompts and taxonomy.
- Detect multi‑page documents and relate pages using layout/metadata heuristics.

## Prompt Design (LLM)

Return ONLY valid JSON with these fields:
{
  "issuer": { "name": string | null, "confidence": number },
  "recipient": { "name": string | null, "confidence": number },
  "document_type": { "value": string, "confidence": number },
  "document_subtype": string | null,
  "period": { "start_date": "YYYY-MM-DD"|null, "end_date": "YYYY-MM-DD"|null, "month": "YYYY-MM"|null },
  "identifiers": { "account_last4": string|null, "policy": string|null, "invoice": string|null, "statement_id": string|null },
  "amounts": { "total_due": number|null, "balance": number|null, "payment": number|null, "currency": string|null },
  "addressed_to": string | null,
  "is_continuation_of_previous": { "value": boolean, "confidence": number, "rationale": string },
  "page_markers": { "page_number_text": string|null, "total_pages_text": string|null },
  "layout_fingerprints": { "fonts": string[], "header_signature": string|null, "footer_signature": string|null },
  "proposed_filename": string,
  "hints": string[]
}

Document Type Taxonomy (value)
- banking: bank_statement, credit_card_statement, mortgage_statement, loan_document
- taxes: tax_bill, property_tax, income_tax
- utilities: electric_bill, gas_bill, water_bill, internet_bill, mobile_bill
- insurance: insurance_policy, insurance_claim, auto_insurance, health_insurance
- medical: medical_record, prescription, health_report
- employment: paystub, employment_contract
- finance: invoice, receipt, payment_record
- legal: affidavit, legal_notice, court_document
- other: other, unknown

Heuristics for Legal/Affidavit
- Look for headers like "Affidavit", "Affiant", and notary blocks ("Subscribed and sworn", seal/stamp, notary name/commission, state/county preamble).
- Extract parties (issuer/recipient), including d/b/a forms ("d/b/a", "doing business as").

Filename Guidance
- Use: `<Institution>-<Type>-<YYYY-MM or range>-<AccountLast4?>-Page<N?>.pdf`

Heuristics to Answer is_continuation_of_previous
- Page numbering text like "Page X of Y" or "X/Y".
- Matching header/footer signatures across consecutive pages.
- Stable font family set and layout grid similarity.
- Repeated identifiers (account/policy/invoice).
- Continuous date range or statement ID.

## Multi‑Page Grouping (Design)
- During upload or reanalysis, compute a lightweight fingerprint per page:
  - `header_signature`, `footer_signature`, `dominant_fonts`, `page_number_text`, `identifiers`.
- Compare each page to previous page; if >= threshold similarity OR explicit `is_continuation_of_previous.value` is true, mark as continuation.
- Persist a `sequence_id` inside each page's `extracted_metadata` to tie pages that belong together (no DB migration required).
- UI: show grouped pages and allow “merge/move as a set”.

## Implementation Plan
- Update `DocumentAnalyzer._create_analysis_prompt` to request the structured fields above.
- Parse and map into existing fields; store extra data in `Page.extracted_metadata`.
- Add grouping utility to compute layout fingerprints from LLM output + heuristics (initially minimal font/header/footer signatures from LLM metadata and page text like page markers).
- Record proposals as today; grouping affects UI affordances and potential batch actions.
