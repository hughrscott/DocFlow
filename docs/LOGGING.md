# Logging

Audience: developers and operators
Owner: maintainers
Last Updated: 2025-11-11

- Default format is JSON (override with `LOG_FORMAT=text`).
- Request middleware adds `X-Request-ID` and `X-Response-Time-ms`, logs method/path/duration.
- JSON logs include `request_id` for both request handlers and background tasks via context propagation.

Enable Text Logs
- `LOG_FORMAT=text uvicorn main:app --reload`

## Audit Trail (Planned)
- Maintain an audit log of file moves to enable full revert:
  - Fields: `page_id`, `decision_id`, `old_folder`, `old_filename`, `new_folder`, `new_filename`, `moved_at`, `moved_by` (system/user), `reason`.
  - Stored in DB table (e.g., `file_move_audit`) and optionally echoed to logs as JSON.
  - Provide a small admin endpoint/CLI to revert a move using the latest audit record.
