# Filing Domain Rules

These rules encode the core domain knowledge of the mail archiving system.
Read this before writing any classification, clustering, or routing code.

## The Cardinal Rule
**Same account + same time period = one file.**
Everything else is a separate file. This is non-negotiable.

| Scenario | Correct Action |
|---|---|
| Same bank, same account, same month | ONE file |
| Same bank, same account, different month | SEPARATE files |
| Same bank, different accounts | SEPARATE files |
| Same company, different services | SEPARATE files |

## Business vs Personal — The Most Common Error

The engine MUST distinguish business accounts from personal accounts.
Filing a business document under a personal directory (or vice versa) is a hard error.

**Business accounts belong under the business entity, not the bank:**
- Sulis Solar LLC checking at PNC → `SulisSolar/PNC/`, NOT `PNC/Personal/`
- Flagstore LLC (SOR Heights) at Frost → `Frost/FlagstoreLLC/Checking/`, NOT personal
- SOR Houston SW at Frost → `Frost/SORHoustonSW/Checking/`

**Business signals to watch for in OCR text:**
- Company name on the account (e.g., "SULIS SOLAR, LLC")
- Business address (742 E 20th St = SOR Heights; 3615 Robinhood = personal/home)
- "Business Checking" label on statement
- EIN/Tax ID instead of SSN

## Page Boundaries Are Not Document Boundaries

Scanners produce pages. Documents are made of one or more pages.
Never assume one page = one document.
Never assume adjacent pages belong to the same document.

**"Page X of N" is the strongest signal.** If page 1 says "Page 1 of 4", pages 2-4
belong to the same document (unless a different institution appears on those pages,
which means the scanner mixed things up).

**Blank pages** belong to the document they immediately follow. Do not create a
single-page document from a blank page.

## Confidence Is Mandatory

Every filing decision must have a confidence score between 0.0 and 1.0.
Never file anything without a score. If the score cannot be computed, default to 0.0
and send to the review queue.

Confidence is composed of:
- OCR quality (how clean was the text extraction)
- Signal strength (how unambiguous were the institution/account signals)
- Rule match certainty (did the rule match on specific vs generic criteria)

## Known Tricky Documents

These are cases from real data where the obvious answer is wrong:

**Blue Cross/Blue Shield after PNC pages:**
BCBS statements can follow PNC pages directly in a scan. The institution signals are
completely different — trust the signals, not the page order.
BCBS → `Insurance/BlueShield/`, regardless of what preceded it.

**Houston Emergency Center / Burglar Alarm:**
These look like utility bills but belong to the School of Rock The Heights location.
Signal: "City of Houston", "Houston Emergency Center", "Burglar Alarm Administration"
→ File under `SOR/SORHeights/`, not `Utilities/`.

**Maryland Registered Agent invoice:**
The invoice is from "Corporate Filings LLC" in Maryland and does NOT mention
Together Solar by name. It is nonetheless a Together Solar document.
Signal: "Maryland", "Registered Agent", "Corporate Filings LLC" → `Investments/Together Solar/`

**Transnational / Celero credit card processing:**
These are statements for the School of Rock's credit card processing, not bank statements.
They go under `Frost/SORHoustonSW/Checking/`, not under a payments or merchant category.

**Bettencourt Tax Advisors — two documents in one scan:**
Two Bettencourt documents in one scan (one 1-page invoice, one 2-page statement)
should be kept as SEPARATE files. Flag as possible duplicate only if page counts
AND content are nearly identical. Do not merge just because the institution is the same.

## Filename Convention

`{Institution}{AccountType}{EntityName}{MonthYear}.pdf`

Rules:
- No spaces in filenames — use CamelCase
- Month names spelled out: `February2026`, not `02-2026` or `Feb2026`
- Include the person's name for medical/insurance: `...January2026Kirstie.pdf`
- Include the account type when the institution has multiple: `Checking`, `LOC`, `Savings`
- Annual documents (fee schedules, tax forms): use the year only, e.g. `2025`
- Never use generic names like `Statement.pdf` or `Document1.pdf`

## Archive Date Stamp Format

When moving the original scan to the archive folder:
- Folder name: `BeenOrganized{mmddyy}`
- Date format: month-day-year, no separators
- Example: March 30 2026 → `BeenOrganized033026`
