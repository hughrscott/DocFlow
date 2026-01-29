# Changelog

All notable changes to this project will be documented here.

## 2025-11-03
- Reorganized documentation into `docs/` with index, API, operations, testing, logging, security
- Added Codex Kickoff and Playbook; moved status into `docs/STATUS.md`
- Created Roadmap and initial ADR folder
- Upgraded analyzer prompt to structured schema (issuer/recipient/type/period/identifiers) and added normalization for compatibility

## 2025-11-11
- Added agent-oriented docs: `AGENTS.md` and `docs/TODO.md` with clear priorities
- Enhanced analyzer prompt with few-shot examples for West University/West U and The Heights
- FolderRouter: support for internet/mobile bills; account_type passthrough
- New endpoint: `GET /api/v1/providers/readiness` with tests
- Document details include `last_error_at` for better progress reporting
- Multi-page grouping polish: `sequence_id` now survives reanalyze, API/UI surface sequences, and bulk "Move to Proposed" endpoint applies proposals in one click
- File move audit trail added (`file_move_audit` table) with revert endpoint plus UI controls; rerun `python -c "from database.database import init_db; init_db()"` to create the new table
- Provider readiness indicators now appear in the React UI alongside cached folder suggestions backed by the new `/folder_suggestions` API (with refresh)
- FolderRouter now inspects existing directory structures and file naming patterns to reuse suitable folders before creating new ones
