# ADR-0002: Audit Trail for Moves and Multi‑Page Grouping

Status: proposed
Date: 2025-11-03

Context
- Need reliable history of file moves for reversibility.
- Better classification requires understanding multi‑page sequences.

Decision
- Add DB table `file_move_audit` to record old/new paths with timestamps and decision metadata.
- Add multi‑page grouping heuristic using page markers and layout fingerprints; persist `sequence_id` on `Page`.

Consequences
- Enables revert of erroneous moves and better debugging.
- Group actions in UI; improved per‑document coherence.

