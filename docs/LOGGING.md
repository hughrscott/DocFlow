# Logging

Audience: developers and operators
Owner: maintainers
Last Updated: 2025-11-03

- Configure format with `LOG_FORMAT` env var: `json` or `text`
- Request middleware adds `X-Request-ID` and `X-Response-Time-ms`, logs method/path/duration
- Prefer propagating the request ID to service logs for correlation

Enable JSON Logs
- `LOG_FORMAT=json uvicorn main:app --reload`

## Audit Trail (Planned)
- Maintain an audit log of file moves to enable full revert:
  - Fields: `page_id`, `decision_id`, `old_folder`, `old_filename`, `new_folder`, `new_filename`, `moved_at`, `moved_by` (system/user), `reason`.
  - Stored in DB table (e.g., `file_move_audit`) and optionally echoed to logs as JSON.
  - Provide a small admin endpoint/CLI to revert a move using the latest audit record.

