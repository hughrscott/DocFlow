# Security

Audience: contributors and operators
Owner: maintainers
Last Updated: 2025-11-03

- Settings write requires Basic auth (`DEFAULT_USERNAME`, `DEFAULT_PASSWORD`)
- Avoid committing secrets; `.env` is gitignored
- Claude API key via env var; do not hardcode in repo
- Documents remain local unless cloud LLMs are used; assess data sensitivity

