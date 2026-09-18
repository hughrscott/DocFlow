# DocFlow Delivery Runbook

This runbook governs the privacy-first reliability delivery rooted at `/home/ubuntu/projects/docflow-delivery`. <!-- privacy-preflight: allow-test-fixture -->

## Workspace layout

- `repo/` — source repository clone and eventual PR branch. Cloud coding agents must never inspect its `.git`, history, diffs containing unsanitized ancestors, or pre-sanitization tree.
- `sanitized-workspace/` — Phase 0 verified export with no source history and one synthetic baseline commit. This is the only workspace Claude Code and external model reviewers may inspect.
- `docs/implementation-plan.md` — canonical PM plan. Phase 0.5 replaces draft status with reviewed status and records review dispositions.
- `artifacts/privacy-*` — deterministic Phase 0 reports. Reports contain category/path/count only, never raw matches.
- `artifacts/plan-review/` — frozen MoA input, hash, isolated preset description, trace, complete model outputs, consolidated verdict, and finding-disposition matrix.
- `artifacts/phase-N/` — per-phase sanitized diff/stat, test commands/results, scan result, and review verdict.
- `artifacts/release/` — final exact-head evidence, install/update/rollback instructions, and Mac checklist.

Do not create state under a live archive or iCloud path. Do not copy auth files, `.env` files, real scans/PDFs, private lookup maps, or raw privacy matches into this workspace.

## Naming and branch conventions

Source branches:

- `chore/privacy-preflight` — Phase 0 current-tree sanitization.
- `feature/privacy-first-reliability` — controlled imports of reviewed Claude-produced commits plus final plan/runbook.

Sanitized-workspace commits are sequential and small:

- `docs: record reviewed delivery plan`
- `feat(state): add local archive-scoped state`
- `feat(privacy): enforce cloud prompt gateway`
- `fix(filing): make ingestion and recovery durable`
- `feat(review): add durable undo operations`
- remediation commits use `fix(review): <finding-id> <short description>`.

Artifact filenames use lowercase kebab case. Model findings use stable IDs `PLAN-B01`, `P1-B01`, etc.; `B` is blocking and `N` non-blocking.

## Handoff protocol

Every specialist begins with `kanban_show()` and reads parent summaries before touching files.

Implementation workers:

1. verify the expected parent commit and a clean workspace;
2. work only in `sanitized-workspace` when prompting Claude Code;
3. invoke `/home/ubuntu/.local/bin/claude`, authenticated with the existing Claude Max login, and do not print auth status fields beyond logged-in/subscription confirmation; <!-- privacy-preflight: allow-test-fixture -->
4. give Claude only sanitized plan text, sanitized files, synthetic tests, and sanitized test output;
5. require TDD and a bounded scope for the current phase;
6. run focused tests, the full safe suite, privacy preflight, and staged-diff secret/PII scan;
7. capture Claude session ID/version, exact commit, changed files, commands, and real results without prompts containing personal values;
8. import changes into `repo/` only through a backend-controlled patch/commit path after scanning the patch; Claude never opens `repo/.git` or source history;
9. keep source and sanitized commits mapped in the phase artifact;
10. call `kanban_complete` with exact paths and counts. Planned checks are not reported as passed.

Reviewers:

1. use a fresh model/session not used to implement the phase;
2. review the exact commit and complete, non-truncated artifacts;
3. never access unsanitized source history or raw personal values;
4. return `APPROVE` or `REQUEST_CHANGES` with stable finding IDs and file/line evidence;
5. treat any privacy boundary, data loss, path escape, page-accounting, migration rollback, undo-integrity, or test-evidence defect as blocking;
6. if models split, the request-changes verdict carries until the finding is resolved on its merits;
7. do not mark macOS behavior verified from Linux evidence.

Backend remediation:

- resolve all blocking findings in one bounded Claude Code pass where practical;
- rerun affected focused tests, full safe suite, preflight scans, and independent review;
- maximum three review passes per phase; a non-decreasing blocking count is a stall and must be surfaced, not hidden;
- never use a human-input block for an internal reviewer finding.

## Phase 0.5 MoA protocol

No MoA call occurs until Phase 0 verifies `sanitized-workspace`.

- Verify live availability of the exact requested DeepSeek V4 Flash and Gemini 3.6 Flash model IDs. Do not silently substitute Pro, older Flash, a similarly named model, or a single-model review.
- Build an isolated workspace-local `HERMES_HOME`; do not edit global or another profile's configuration.
- Freeze the complete review input in `artifacts/plan-review/frozen-input.md`. Include the full plan and sanitized code-finding bundle because reference models cannot read aggregator tool outputs.
- Run `hermes chat --provider moa --model <preset> --query-file <frozen-input> --oneshot --ignore-rules --max-turns 1 --run-budget <bounded-seconds>`.
- Enable trace saving into `artifacts/plan-review/traces/`.
- Unset `HERMES_KANBAN_TASK` and `HERMES_KANBAN_WORKSPACE` for the nested review process so it cannot mutate the parent card.
- Require distinct DeepSeek and Gemini advisor identities, nonempty outputs, a nonempty consolidated output, and no provider-error substitution.
- Hash the frozen input and preserve preset description, command without secrets, trace, full outputs, verdict, and model identities.
- Scan artifacts before they are attached or committed.
- Map every finding to `accepted`, `rejected-with-reason`, or `already-covered`, and point to the corrected plan section. Count mapping coverage programmatically.

## Git and collision rules

- Never use `git add .` or `git add -A`; stage explicit paths.
- Never force-push, rewrite history, merge, or change repository visibility without explicit user approval.
- One worker modifies shared files at a time. The board dependency chain is the concurrency control.
- If a shared file becomes a collision hotspot, comment `hotspot: <path> — <reason>` before further work.
- Before commit/push, verify staged file list, scan the staged diff without printing matched values, and compare the reviewed exact head.
- GitHub prompts, PR descriptions, review comments, logs, and attachments must use sanitized evidence only.

## Safe test environment

- Tests use `tmp_path` or a workspace-local synthetic fixture root.
- Override application-state and archive roots explicitly. A guard fails tests if a path resolves under the user's home archive defaults, iCloud, or configured live paths.
- Block all external network by default. Cloud tests use an intercepted fake transport that records serialized outgoing payloads for sentinel assertions.
- Never send real email, call a real provider with document content, or run the app against an actual scan.
- Any macOS-only behavior is either tested through path/platform abstractions on Linux or listed as pending Mac acceptance.

## Required evidence per implementation phase

Each `artifacts/phase-N/evidence.md` records:

- parent and resulting sanitized/source commit SHAs;
- changed files;
- focused test command and exact pass/fail counts;
- full safe-suite command and exact pass/fail counts;
- known pre-existing failures, if any, plus a run excluding only those failures;
- privacy preflight result;
- staged-diff secret/PII scan result;
- network interception result where applicable;
- reviewer model/version, verdict, and finding dispositions;
- explicit statement that no live archive, actual document, provider data call, or outbound email was used.

## Release handoff

The release worker prepares but does not merge a PR from `feature/privacy-first-reliability`.

Required release packet:

1. exact PR URL, branch, and head SHA;
2. required CI status for that exact head;
3. Linux test and scan evidence;
4. known residual risks, including pseudonymization limits and historical public exposure without reproduced details;
5. Mac install/update instructions;
6. rollback instructions restoring the previous code and application-state backup without altering archive PDFs;
7. small human checklist marked PENDING:
   - install dependencies and launch on loopback;
   - select a non-production synthetic archive first;
   - process one synthetic mixed PDF;
   - confirm exception review and durable undo after browser reload;
   - simulate unavailable iCloud input;
   - verify app-state files are outside iCloud;
   - enable local-only mode and confirm no cloud request;
   - only then point to the real archive after reviewing backups.

No claim of macOS verification is allowed until the user executes and reports this checklist.

## Definition of done

Done means all cards in the dependency chain completed, the final independent gate approved the exact PR head, required CI passed, the release packet exists, and the user has a safe update/rollback path. It does not mean merged, deployed, or Mac-accepted.