# DocFlow API Reference

Audience: developers
Owner: maintainers
Last Updated: 2025-11-03

Base URL: `/api/v1`

- POST `/documents/upload`
  - Query: `analyze` (bool), `dpi` (int), `background` (bool)
  - Body: `multipart/form-data` with `file`
  - Returns: `document_id`, `original_filename`, `total_pages`, `results[]`

- GET `/documents`
  - Query: `page` (int), `page_size` (int)
  - Returns: paginated list of recent documents with sample pages

- GET `/documents/{document_id}`
  - Returns: status, `pages_done/total`, `last_error`, and per-page results with latest proposals

- POST `/documents/{document_id}/reanalyze`
  - Query: `dpi` (int), `background` (bool)
  - Behavior: updates page metadata and records new proposals; does not move files

- POST `/documents/pages/{page_id}/reanalyze`
  - Query: `dpi` (int)
  - Behavior: same as above, for a single page

- POST `/documents/pages/{page_id}/correct`
  - JSON: `{ "folder": string, "filename": string }`
  - Behavior: moves file to new path, updates learning using the latest decision (or creates one)

- GET `/health`
  - Returns: `database_ok`, `total_documents`, `active_providers`, timestamp

- GET `/settings`
  - Returns: contents of `config/llm_config.yaml`

- POST `/settings` (Basic auth)
  - JSON: `{ vision_provider?, text_provider?, providers?: { claude_enabled?, ollama_enabled?, ollama_base_url?, claude_api_key? } }`
  - Behavior: persists validated changes to `config/llm_config.yaml`

Quick Tests (curl)
- Upload (no AI): `curl -F file=@test_document.pdf 'http://127.0.0.1:8000/api/v1/documents/upload?analyze=false&dpi=120&background=true'`
- Upload (AI): `curl -F file=@test_document.pdf 'http://127.0.0.1:8000/api/v1/documents/upload?analyze=true&dpi=120'`
- Reanalyze doc: `curl -X POST 'http://127.0.0.1:8000/api/v1/documents/<doc_id>/reanalyze?dpi=150&background=true'`
- Reanalyze page: `curl -X POST 'http://127.0.0.1:8000/api/v1/documents/pages/<page_id>/reanalyze?dpi=150'`
- Correct page: `curl -X POST 'http://127.0.0.1:8000/api/v1/documents/pages/<page_id>/correct' -H 'Content-Type: application/json' -d '{"folder":"Banking/Personal/PNC","filename":"PNC-Statement-2025-01.pdf"}'`

Notes
- Reanalyze endpoints record proposals; they do not move files.
- Poppler required for `pdf2image`. Ollama and models, or Claude key, must be configured.
Doc-Level Corrections

- POST `/documents/{document_id}/confirm_class`
  - JSON: `{ "document_class": string, "rationale"?: string }`
  - Behavior: stores a doc-level class override on each page's `extracted_metadata.doc_level_override`. The document details aggregation (`doc_level`) respects this override and surfaces the confirmed class in the Essentials card.

