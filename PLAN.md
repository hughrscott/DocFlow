# DocFlow UI Improvement Plan

## 1. Architecture / Code Quality

### 1.1 Extract shared `<head>` boilerplate
- Every HTML file (dashboard, review, archive, settings) duplicates the full Tailwind config, color palette, font imports, and Material Symbols link
- Move to a shared layout — either a server-rendered Jinja2 base template or a JS-injected `<head>` block in `shared.js`
- Eliminates ~30 duplicated lines per page
- Makes theme changes a single-line update

### 1.2 Replace Tailwind CDN with a build step
- Current: `cdn.tailwindcss.com` loads ~300KB of JS evaluated at runtime (meant for prototyping only)
- Target: proper `tailwindcss` build with CSS purging produces ~10KB CSS file
- Eliminates flash-of-unstyled-content on page load
- Add to build/dev scripts in `package.json` or a Makefile

### 1.3 Switch from polling to SSE for processing updates
- `/api/process/stream/{job_id}` SSE endpoint already exists but is unused
- Dashboard currently uses `setTimeout(poll, 500)` which hammers the server
- Replace with `EventSource` for real-time push updates
- Fall back to polling only if SSE connection fails

---

## 2. UX Flow Issues

### 2.1 Add toast/snackbar error feedback system
- Currently: upload failure or invalid file drop produces no visible feedback — drop zone resets silently
- Add a global toast/snackbar component (rendered via `shared.js`)
- Show transient messages for: upload errors, invalid file types, network failures, processing errors
- Auto-dismiss after 5s with manual dismiss option

### 2.2 Batch queue visibility
- File input supports `multiple` but `handleFiles` processes serially with no queue indicator
- If someone drops 5 PDFs, they only see the last one processing
- Add a batch queue sidebar or stacked progress cards showing all queued/active/completed items
- Show per-file status (queued, processing, done, error)

### 2.3 Inline review from dashboard
- After processing finishes with review items, user must navigate away to `/review`
- Loses context of the batch they just processed
- Add an inline review drawer or modal so low-confidence items can be resolved without leaving the dashboard
- Still keep the full `/review` page for working through the backlog

### 2.4 Add "undo" and "re-process" affordances
- Once a scan is processed, there's no way to re-run it or undo a filing decision from the dashboard
- Add a "Re-process" button on completed batches
- Add an "Undo filing" action on individual documents (moves file back, removes from log)

---

## 3. Visual Design

### 3.1 Expand the progress stepper
- Current: 3 steps (OCR, Clustering, Classification) hide the reality of 8 pipeline stages
- Replace with a vertical timeline that expands as each stage completes
- Show per-document progress within each stage where applicable
- Stages: Loading PDF, OCR, Clustering, Classification, Confidence Gate, Extraction/Filing, Summary, Archive Original

### 3.2 Confidence badge granularity
- Currently binary: auto-filed vs. review
- Add a color gradient: green (>0.9) > yellow (0.75-0.9) > orange (0.6-0.75) > red (<0.6)
- Show numeric score alongside the color indicator
- Consistent badge component used across dashboard, review, and archive pages

### 3.3 Fix global "Scan Mail" button
- Left nav "Scan Mail" button triggers `document.getElementById('upload-input')` which only exists on the dashboard
- On `/review`, `/archive`, or `/settings`, clicking it does nothing
- Fix: either navigate to `/` first, or make the upload input + modal a global component rendered on every page

### 3.4 Consistent empty states
- Review page has a nice "All Clear!" zero-state
- Dashboard and archive lack equivalent zero-states for new users
- Add friendly empty states with call-to-action for each page:
  - Dashboard: "Drop your first scan to get started"
  - Archive: "No documents filed yet — process a scan to populate your archive"

---

## 4. Accessibility & Responsiveness

### 4.1 Responsive layout for tablet/mobile
- Current fixed 64px sidebar + `ml-64` main content breaks entirely below ~1024px
- Add responsive breakpoints:
  - < 768px: sidebar collapses to bottom tab bar or hamburger menu
  - 768-1024px: sidebar collapses to icon-only rail (no labels)
  - > 1024px: full sidebar as today
- Review page split-pane should stack vertically on narrow screens

### 4.2 ARIA labels and semantic markup
- Drop zone lacks `role="button"` and `aria-label`
- Notification bell and "more_vert" buttons lack `aria-label` attributes
- Nav links should indicate `aria-current="page"` for active item
- Progress bar needs `role="progressbar"` with `aria-valuenow`/`aria-valuemax`
- Review actions need associated labels

### 4.3 Keyboard shortcuts for review queue
- Review is a repetitive task — keyboard shortcuts drastically speed it up
- Proposed shortcuts:
  - `A` — Approve as-is
  - `S` — Skip
  - `C` — Focus "Correct & File" (then Enter to confirm)
  - `N` / `P` — Next / Previous item in queue
  - `Tab` — Cycle between filename and directory edit fields
- Show shortcut hints on hover or in a help tooltip

---

## 5. Feature Gaps

### 5.1 PDF thumbnail preview in review queue
- Current: review page shows `<pre>` OCR text only
- Users need to see the actual rendered PDF page to make confident decisions
- `pdf2image` is already in the stack — serve page thumbnails via a new API endpoint (`GET /api/preview/{job_id}/{page}`)
- Display as a scrollable image gallery in the left pane (replacing or alongside the OCR text)
- Toggle between "OCR Text" and "Page Image" views

### 5.2 Wire up search functionality
- Search input in the header is decorative — no JS connects it to anything
- Add `GET /api/search?q=...` endpoint that searches:
  - Filing log entries (filename, directory, rule matched)
  - Archived file names
  - OCR text (if stored/indexed)
- Show results in a dropdown or navigate to a search results view
- Support keyboard shortcut (`Cmd+K` or `/`) to focus search

### 5.3 Settings validation and connectivity testing
- Currently: invalid paths or API keys produce no feedback until the next processing run fails
- Add inline validation:
  - "Test Connection" button for LLM endpoint (hit `/api/health` or Ollama `/api/tags`)
  - Path existence check on blur for archive root and watch folder
  - API key format validation
- Show green checkmark or red X inline next to each field

### 5.4 Dark mode
- Material 3 color system is already defined — adding dark surface variants is straightforward
- Add a toggle in settings (or auto-detect from `prefers-color-scheme`)
- Define dark palette in Tailwind config:
  - Surface: dark navy/slate
  - On-surface: light gray/white
  - Containers: darker variants of current colors
- Persist preference in `localStorage`

---

## 6. Bug Fixes

### 6.1 Move (not copy) originals out of ToBeOrganized
- Current behavior copies the scan PDF to `BeenOrganized/` but leaves the original in place
- This means the same file can be processed again on a subsequent run, resulting in duplicate filings
- Fix: use move (`shutil.move` / `os.rename`) instead of copy when archiving the original scan to `BeenOrganized{mmddyy}/`
- The original must not remain in the watch folder after successful processing

---

## Priority Order (highest impact, lowest effort first)

| Priority | Item | Status |
|----------|------|--------|
| 1 | 6.1 — Move (not copy) originals | DONE |
| 2 | 1.3 — SSE → reverted to polling (SSE unreliable) | DONE |
| 3 | 2.1 — Toast/error feedback | DONE |
| 4 | 5.1 — PDF preview in review | DONE |
| 5 | 4.3 — Keyboard shortcuts for review | DONE |
| 6 | 1.1 — Extract shared boilerplate (config.js) | DONE |
| 7 | 4.1 — Responsive layout (sidebar, mobile tabs) | DONE |
| 8 | 3.3 — Global upload modal | DONE |
| 9 | 2.2 — Batch queue visibility | DONE |
| 10 | 5.2 — Wire up search | DONE |
| 11 | 3.2 — Confidence badge granularity (4 tiers) | DONE |
| 12 | 3.4 — Consistent empty states | DONE |
| 13 | 5.3 — Settings validation (test connection, verify paths) | DONE |
| 14 | 2.3 — Inline review from dashboard | SKIPPED (review page + keyboard shortcuts sufficient) |
| 15 | 3.1 — Expanded progress stepper (8 stages) | DONE |
| 16 | 1.2 — Tailwind build step | SKIPPED (CDN fine for current scale) |
| 17 | 4.2 — ARIA labels | DONE |
| 18 | 5.4 — Dark mode | DONE |
| 19 | 2.4 — Undo / re-process | DONE |
