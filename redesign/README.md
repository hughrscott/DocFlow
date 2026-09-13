# Handoff: DocFlow Redesign

## Overview
DocFlow is an AI-powered "digital archivist" that watches a scan/inbox folder, OCRs incoming mail, classifies each document with an LLM, and files it into a structured folder tree. This handoff covers a full visual + UX redesign of the desktop app across four screens — **Dashboard**, **Review Queue**, **Archive Browser**, and **Settings** — plus three modals (folder picker, page zoom, undo snackbar).

The redesign keeps DocFlow's existing data model and behavior (queue, confidence scores, filing rules, OpenRouter/Gemini engine) and re-skins/re-organizes the UI around a warm "paper archive" aesthetic with a keyboard-driven review flow.

## About the Design Files
The file in this bundle (`DocFlow Redesign.dc.html`) is a **design reference created in HTML** — a working prototype that shows the intended look, layout, and interaction behavior. **It is not production code to copy directly.**

Your task is to **recreate this design in DocFlow's existing codebase**, using its established framework, component patterns, state management, and libraries. Match the visuals and interactions pixel-closely, but express them in the app's real architecture (e.g. its existing React/Electron/Vue components, routing, and data layer) — do not ship the HTML prototype as-is. If a given screen already exists in the app, refactor it toward this design rather than bolting on a parallel implementation.

> Note: the prototype is built as a self-contained HTML "Design Component." It uses a small custom template runtime (`<x-dc>`, `<sc-for>`, `<sc-if>`, `{{ }}` holes) and a `Component extends DCLogic` class. **Ignore that runtime** — it is just the prototyping harness. What matters is the markup structure, the inline styles (exact colors/spacing/typography), and the logic in the `Component` class (state shape, actions, keyboard handlers), which you should translate into your codebase's idioms.

## Fidelity
**High-fidelity (hifi).** Colors, typography, spacing, border radii, shadows, and interactions are all final and intentional. Recreate the UI pixel-closely using DocFlow's existing component library. Treat the hex values, font sizes, and radii in the Design Tokens section as authoritative.

---

## Global Shell

A fixed two-pane layout, full viewport height:

- **Sidebar** — `248px` fixed width, `#ECE7DC` background, `1px solid #E0D9CB` right border, `26px 18px` padding, sticky full-height.
  - Brand block: `28px` rounded-`8px` `#15171C` square with a filled `inventory_2` icon in `#F4F1EA`, next to "DocFlow" (Manrope 800, 21px, letter-spacing -.02em). Below: tagline "THE DIGITAL ARCHIVIST" (9.5px, 700, letter-spacing .18em, uppercase, `#9A9482`).
  - **Scan Mail** primary button: `#15171C` bg, `#F4F1EA` text, radius 12px, 13px padding, `add` icon + label, shadow `0 6px 16px rgba(20,23,28,.16)`, hover lifts `translateY(-1px)`.
  - **Nav** (vertical, gap 4px): Dashboard, Review Queue (with count badge in `#B5751F`/white pill), Archive Browser, Settings. Active item = `#15171C` bg + `#F4F1EA` text + filled icon; inactive = transparent + `#5A5C66` text + outlined icon. Each: flex row, gap 12px, 11px/14px padding, radius 12px, font 600 13.5px.
  - **Footer nav** (above a `1px #E0D9CB` divider): Dark Mode, Support — ghost buttons, `#6B6D78` text, hover bg `#E3DCCD`.
- **Main column** — flex column, min-width 0, 100vh.
  - **Header** — flex space-between, `20px 34px` padding, bottom border `1px #E6E0D4`, translucent bg `rgba(244,241,234,.86)` + `backdrop-filter: blur(10px)`, z-index 20. Left: screen title (Manrope 700, 20px). Right: search input (white, radius 999px, 240px, left-inset `search` icon, placeholder "Search archive…") + notification button (40×40, radius 12px, white, `notifications` icon with a `#BE4029` unread dot).
  - **Scroll body** — `flex:1; overflow-y:auto`, holds the active screen. Custom scrollbar (`.df-scroll`): 10px wide, thumb `rgba(20,23,28,.14)` radius 8px with 3px transparent border (inset look), hover `rgba(20,23,28,.26)`.
  - **Footer status bar** — `8px 34px`, top border `1px #E6E0D4`, bg `#EFEADF`. Left: "Watch Folder: Active • LLM Status: Ready" (10.5px 600 uppercase, status words in `#0E8A5E`). Right: "System Logs" link.

Screen switching is local state (`screen: 'dashboard' | 'review' | 'archive' | 'settings'`). In the real app this should map to the existing router/navigation.

---

## Screens / Views

### 1. Dashboard
**Purpose:** Landing screen — upload mail, jump into pending review, see at-a-glance stats and recent filings.

**Layout:** centered column, `max-width: 1180px`, `34px` padding.
- **Upload drop bar** — full-width, `1.5px dashed #D6CDB9` border, bg `#FBFAF6`, radius 14px, 13px/16px padding, clickable. Left: 40px rounded icon tile (`#F1ECE0`) with `upload_file`. Middle: "Upload scanned mail" (700, 14px) + helper "Drag & drop a batch PDF, or click to browse · PDF, batch enabled" (`#9A9482`, 12px). Right: dark "Browse" pill with `folder_open` icon. Hover: border → `#15171C`, bg → `#fff`.
- **Review callout** (only when pending > 0) — warm card, bg `#FBF1DF`, border `1px #EBD9B6`, radius 18px, 20px/24px padding, clickable → Review Queue. Icon tile `#F2E2C4` with filled `rate_review` in `#B5751F`. Text: "{N} documents need your review" (700, 15px) + "Low-confidence filings waiting for a quick approve or correction." (`#9C7B3C`, 12.5px). Right: dark "Open Review Queue →" pill. Hover lifts.
- **Stats grid** — 4 equal columns, gap 14px. Each card: white, border `1px #EDE7DA`, radius 16px, 18px/20px padding, subtle shadow. Top row = colored icon + uppercase label (10.5px, 700, letter-spacing .1em, `#9A9482`); below = value (Manrope 800, 26px). Stats: **To Review** (`rate_review`, `#B5751F`), **Filed Today** (`task_alt`, `#0E8A5E`), **Total Archived** = "141" (`inventory_2`, `#15171C`), **Auto-file Rate** = "91%" (`bolt`, `#34508C`).
- **Recent Activity** — section header "Recent Activity" (Manrope 700, 16px) + "View all →" ghost button → Archive. List of up to 6 rows: white card, border `1px #EDE7DA`, radius 13px, 13px/16px padding. Each: 36px icon tile + filename (JetBrains Mono 500, 12.5px, truncated) + dir line (`folder` icon + path, `#9A9482`, 11.5px) + timestamp + a "{N}% Match" confidence pill.

### 2. Review Queue
**Purpose:** The core workflow — triage low-confidence filings one at a time, approve/correct/skip, keyboard-driven. Min-width `1060px` (three-pane workspace).

**Top bar:** segmented tabs (`#ECE7DC` track, 4px padding, radius 13px) — **Pending Review** (count) and **Unmatched & Skipped** (count). Active tab = white pill + shadow. When Pending: a progress bar (max 300px, 7px tall, `#E2DBCC` track, `#0E8A5E` fill) + "{remaining} left · {reviewed} of {total} reviewed" (12px 600 `#8A8B72`).

**Three-pane workspace** (`16px 28px 22px` padding, flex, min-height 0):

- **Left rail — Queue** (`266px`): bg `#EFEADF`, border `1px #E6E0D4`, radius 16px, 12px padding. Header "QUEUE · {n}" + a "Select all"/"Clear" toggle. When any selected: a dark "Approve {n} selected" bar (`done_all` icon). Scrollable list of queue cards (gap 5px). Each card: a checkbox (20px, radius 6px; checked = `#15171C` fill + white `check`, unchecked = `1.5px #D6CFBF` border on white), an institution label (10px uppercase `#A09A88`) over doc type (13px 700), a 4px confidence mini-bar, and a confidence pill. **Active card** = white bg, `1px #15171C` border, shadow `0 4px 14px rgba(20,23,28,.08)`.
- **Center — Preview** (flex 1): bg `#EFEADF`, border, radius 16px, 14px padding. Top: a Page/OCR-Text segmented toggle (`#E2DBCC` track) + (for multi-page docs) prev/next page chevrons with "Page X of Y". Body scrolls and centers a faux document:
  - **Page mode**: white "sheet" (max 520px, radius 6px, shadow `0 10px 34px rgba(20,23,28,.14)`, 44px/46px padding), `cursor: zoom-in` → opens zoom modal. Header: institution (Manrope 800, 19px) + "doc_type · period" + an `account_balance` icon tile. Body: 9 grey skeleton "text lines" of varying widths + two 54px placeholder blocks. (This stands in for the real scanned-page image / PDF render — wire to the actual page raster.)
  - **OCR Text mode**: white sheet with `<pre>` of OCR text (JetBrains Mono 12.5px, line-height 1.7, `#3A3D47`, pre-wrap).
- **Right — Decision panel** (`362px`): white, border, radius 16px, shadow `0 8px 26px rgba(20,23,28,.05)`, overflow hidden.
  - **Confidence header** (bg tinted by confidence band): a 58px **conic-gradient ring** showing confidence % (inner disc holds "{pct}%" in Manrope 800) — OR a bar, controlled by the `confidenceStyle` prop. Next to it: confidence label ("Confident" / "Needs a look" / "Low confidence" / "Unclassified") + a one-line "why" explanation.
  - **Files to** — destination. If a directory is set, show breadcrumb chips (`folder` icon + each path segment as a `#F4F1EA` mono chip). Editable directory input (mono) + a folder-picker button (`folder_open`) opening the picker modal.
  - **Filename** — editable mono input.
  - **Facts** — 3-up grid (Type / Period / Pages), each a `#F7F5EF` rounded tile.
  - **Archivist Reasoning** — a blue info card (bg `#F0F2F9`, border `1px #E1E6F3`, radius 13px) with `neurology` icon + "ARCHIVIST REASONING" label + the LLM's reasoning paragraph (12.5px, line-height 1.55).
  - **Action footer** (bg `#FBFAF6`, top border): big primary **Approve & File** button (`#15171C`, Manrope 800, 14.5px, `check_circle` icon, shows shortcut "A"). Below, two half-width buttons: **Skip** (white outline, shortcut "S") and **Ask AI** (`#EEF1F9` bg, `#34508C` text, `auto_awesome` icon). In the Unmatched tab the labels change to "File Document" / "Ask AI to classify".
- **Empty state** (when a tab is cleared): centered green `task_alt` badge + "All caught up" / "Nothing unmatched" + subtext.

### 3. Archive Browser
**Purpose:** Browse the filed-document tree and review filing history. Min-width `1020px`.

**Layout:** two columns, gap 28px, `30px 34px` padding, max-width 1180px.
- **Left — File System tree** (`236px`, sticky): uppercase "FILE SYSTEM" label, then folder rows indented by depth (`padding-left: 10 + depth*18` px). Each: `folder` icon (`#C0A86E`), name, and a count. Hover bg `#ECE7DC`.
- **Right — Filing History**: breadcrumb ("🏠 Archive › All filings"), title "Filing History" (Manrope 800, 24px), summary "141 documents filed across 41 sessions". Then a table card (white, border, radius 16px): a `#F7F5EF` header row with 4 columns — **Document** (`minmax(0,2.1fr)`), **Filing Path** (`minmax(0,1.5fr)`), **Rule** (`168px`), **Confidence** (`104px`, right-aligned). Each data row: `description` icon + mono filename; mono path; a colored dot + rule name (dot color: `#0E8A5E` for a rule match, `#34508C` for "AI Suggested", `#BE4029` for none); a "{pct}% Match" pill. Row hover bg `#FBFAF6`.

### 4. Settings
**Purpose:** Configure entities, storage paths, the AI engine, filing rules, and view system health. Min-width `1020px`.

**Layout:** two columns (left flex 1, right `316px`), gap 28px, `32px 34px` padding.
- **Left column:**
  - **Entities & Family Members** — heading + subtext, then a 2-col grid of entity cards (white, border, radius 14px). Each: icon tile (people = `#E9EDF7`/`#34508C` `person`; businesses = `#F6E9D3`/`#B5751F` `apartment`) + name (700, 13.5px) + relation (uppercase 10px `#A09A88`). Entities include Vivian/Brodie/Riley Redwood and several LLCs.
  - **Storage & Paths** — two labeled mono inputs: Archive Root Directory and Scan Watch Folder (iCloud-style paths).
  - **AI Processing Engine** — a **dark card** (`#15171C`, radius 18px, 28px padding, `#F4F1EA` text) with a gold `auto_awesome` icon + heading. 2-col grid of fields (translucent white tiles): Provider ("OpenRouter", with `expand_more`), Model ("google/gemini-2.0-flash-001"), API Endpoint ("https://openrouter.ai/api/v1"), API Key ("••••••• configured"). Below: **Confidence Threshold** slider (50–100, gold `accent-color: #C0A86E`, value shown in gold Manrope 800) with "More auto-filing ←→ More review" end labels. Buttons: Save Settings (light), Test Connection (`cable`), Verify Paths (`folder_open`).
- **Right column:**
  - **Filing Rules** card — header "Filing Rules" (`rule` icon) + a "{n} Active" green pill. Scrollable list (max-height 360px) of rule chips: `#F7F5EF` rounded, mono rule id + its `file_to` path.
  - **System Health** card — "LLM Provider" → model name; "API Status" → green dot + "Connected".

---

## Modals & Overlays

- **Folder Picker** — centered modal (440px, radius 18px, bg `#FBFAF6`), backdrop `rgba(20,23,28,.42)` + blur. Header with title "Choose Filing Location" + close button + a filter input. Scrollable indented folder list (same tree as Archive); clicking a folder sets the decision panel's directory and closes.
- **Page Zoom** — full-screen overlay (`rgba(20,23,28,.86)` + blur, `cursor: zoom-out`), close button top-right, a larger (640px) version of the faux document sheet. Click anywhere closes.
- **Undo snackbar** — fixed bottom-center pill (`#15171C`, radius 14px, shadow), green filled `check_circle` + message ("Filed "…"" / "Skipped "…"" / "Filed N documents") + an Undo button (shortcut "U"). Auto-dismisses after **6000ms**.

---

## Interactions & Behavior

- **Navigation:** sidebar items switch screens; Scan Mail and dashboard upload bar → Dashboard (in real app, open the file/scan dialog); review callout & "View all" → Review Queue / Archive.
- **Review actions:**
  - **Approve & File** — removes current item from its list, increments "Filed Today", shows an undo snackbar.
  - **Skip** — removes current item (marked skipped), shows undo snackbar.
  - **Approve selected** — bulk-files all checked items.
  - **Ask AI** — re-runs classification: on a pending item it raises confidence (+0.22, capped 0.9) and rewrites the "why"/reasoning; on an unmatched item it proposes a filename + directory. (In the real app, call the LLM.)
  - **Undo** — re-inserts removed items at their original positions and reverts the Filed-Today counter.
  - Editing **filename** / **directory** inputs updates the current item's intended destination.
  - **Select all / Clear** toggles selection of every item in the active tab.
- **Auto-advance** (`autoAdvance` prop, default true): after approve/skip, selection advances to the next item's position; when false it stays at the same index (now the following item).
- **Preview:** toggle Page/OCR-Text; prev/next paging for multi-page docs; clicking the page opens the zoom modal.
- **Keyboard shortcuts** (active only on Review screen, ignored while typing in an input):
  - `A` = approve & file · `S` = skip · `U` = undo
  - `↓`/`J` = next item · `↑`/`K` = previous item
  - `V` = toggle Page/OCR view · `Esc` = blur focused input
- **Confidence bands** (drives label, text color, and tint background):
  - `≥ 0.75` → "Confident", text `#0E8A5E`, bg `#E2F1E9`
  - `≥ 0.60` → "Needs a look", text `#B5751F`, bg `#F6E9D3`
  - `< 0.60` → "Low confidence", text `#BE4029`, bg `#F7E3DD`
  - `null` (unmatched) → "Unclassified", text `#8A8B72`, bg `#ECE7DC`
- **Hover/lift:** primary buttons and clickable cards translate up 1px on hover; ghost rows tint their background.
- **Transitions:** ~.12s on background/transform; progress/confidence bar width .3s; subtle entrance animations available (`dfup`, `dffade`).

## State Management
Local component state in the prototype (translate to the app's store / view-models):
- `screen` — active screen.
- `tab` — `'pending' | 'unmatched'` within Review.
- `queue` — array of pending docs: `{ id, institution, doc_type, period, pages[], confidence (0–1), filename, directory, why, reasoning, conflict?, low? }`.
- `unmatched` — array of unfiled files: `{ id, name, folder, pages, size, kind: 'unmatched'|'skipped' }`.
- `currentIndex` — selected item in the active list.
- `selected` — `{ [id]: bool }` for bulk selection.
- `previewMode` — `'image' | 'text'`; `pageIdx` — current page.
- `editFilename`, `editDirectory` — in-progress edits for the current item (re-seeded when selection/tab changes).
- `filedToday`, `autoFileRate` — dashboard counters.
- `undo` — `{ type, removed:[{item,pos}], label, key }` with a 6s auto-clear timer.
- `pickerOpen`, `pickerFilter`, `zoomOpen`, `threshold`.

**Data fetching to wire up in the real app:** the live document queue + OCR text + page rasters from the scan/inbox folder; filing rules and entities from config; the LLM call behind "Ask AI" and the auto-classification pass (OpenRouter / Gemini per Settings); persisting approve/skip results to the actual filesystem archive; the watch-folder + LLM status indicators in the footer.

## Design Tokens

**Colors**
- Canvas / app bg: `#F4F1EA`
- Sidebar / rail bg: `#ECE7DC`; secondary panel bg: `#EFEADF`
- Card surface: `#FFFFFF`; inset field bg: `#F7F5EF`; tile bg: `#F1ECE0`; soft hover: `#FBFAF6`
- Borders: `#E6E0D4`, `#E0D9CB`, `#EDE7DA`, `#F2EDE2`; dashed `#D6CDB9`; check border `#D6CFBF`
- Ink / primary dark: `#15171C`; text secondary `#5A5C66` / `#6B6D78`; muted `#8A8B72` / `#9A9482`; faint `#A9A595` / `#A09A88`; skeleton `#EDE7DA`
- Accent gold/amber: `#B5751F` (badge/icons), `#C0A86E` (slider/folder icons), warm callout bg `#FBF1DF` / border `#EBD9B6`
- Green (success/confident): `#0E8A5E`, bg `#E2F1E9`
- Amber (mid confidence): `#B5751F`, bg `#F6E9D3`
- Red (low / alerts): `#BE4029`, bg `#F7E3DD`
- Blue (AI / reasoning): `#34508C`, bg `#F0F2F9` / `#EEF1F9`, border `#E1E6F3` / `#DCE3F4`

**Typography**
- Display / headings: **Manrope** (700–800), tight letter-spacing (−.01 to −.02em)
- Body / UI: **Inter** (400–700)
- Monospace (filenames, paths, rule ids, OCR): **JetBrains Mono** (400–600)
- Icons: **Material Symbols Outlined** (`.fill` variant for active/emphasis)

**Radii:** pills `999px`; large cards/modals `16–18px`; cards `13–14px`; inputs/buttons `10–12px`; small tiles `6–8px`.

**Shadows:** card `0 1px 2px rgba(20,23,28,.03)`; raised panel `0 8px 26px rgba(20,23,28,.05)`; active queue card `0 4px 14px rgba(20,23,28,.08)`; primary button `0 6px 16px rgba(20,23,28,.16/.18)`; document sheet `0 10px 34px rgba(20,23,28,.14)`; modal `0 24px 60px rgba(20,23,28,.32)`.

**Spacing:** screen padding `30–34px`; card padding `14–28px`; common gaps `14–28px`; tight stacks `4–9px`.

## Configurable variants (prototype props)
The prototype exposes three toggles you may or may not want as real settings:
- `autoAdvance` (bool, default true) — advance to next item after a decision.
- `confidenceStyle` (`'Ring' | 'Bar'`, default Ring) — confidence visualization in the decision panel.
- `denseQueue` (bool, default false) — denser queue rail layout.

## Assets
No external image assets. All "documents" are CSS skeleton placeholders standing in for real scanned-page rasters / PDF renders — wire those to the actual page images in the app. Icons come from the Material Symbols Outlined font; fonts (Manrope, Inter, JetBrains Mono) load from Google Fonts. Use whatever icon set / font loading the existing codebase already uses if it differs.

## Files
- `DocFlow Redesign.dc.html` — the full high-fidelity prototype (all four screens + modals). The markup and inline styles are the visual spec; the `Component` class at the bottom holds the state shape, actions, confidence logic, and keyboard handlers to translate.
