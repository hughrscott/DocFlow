# DocFlow — TODOs (Single Source of Truth)

Audience: contributors and Codex sessions
Owner: maintainers
Last Updated: 2025-11-12

Usage
- Keep this list current. When you start a session, pick items from Next/Priority, create a short plan, and update status at the end.
- Keep items small and shippable (vertical slices) with tests.

Now / Priority
- Carousel-based document review interface with visual classification confirmation
- Doc-level aggregator + Essentials card surfacing confirmed class and period
- Integration tests: reanalyze document/page; correction updates learning
- Frontend redesign toward the long-term desktop/app-store experience with carousel UI
- Packaging/installer workflow so DocFlow can be shipped as a desktop app

Next
- Search/tagging and OCR/full-text index
- Postgres option & simple migration scripts
- Doc-level corrections: confirm class (iterate with learning feedback)
- Pre-commit hooks and `.http` examples

Backlog
- Provider readiness telemetry expansions (alerts, retries)
- Folder suggestions exposed via admin UX (manual overrides)

Done (recent)
- Refined analyzer prompt; business context; personal/business routing
- Prompt few-shots: West University/West U and The Heights
- Router: added internet/mobile bills; account_type passthrough
- Providers readiness endpoint + test
- Document details include last_error_at; tests passing
- Visual progress for background jobs: list endpoint now reports pages_done/failed + last_error metadata with regression test
- Multi-page grouping polished: sequences exposed in API/UI with bulk "Move to Proposed" actions
- File move audit trail with revert endpoint and coverage
- Frontend indicators for provider readiness and background progress
- Persisted folder structure analysis with API + UI suggestions
