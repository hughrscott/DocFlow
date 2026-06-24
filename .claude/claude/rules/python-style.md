# Python Style Rules

## Formatter & Linter
- Formatter: `ruff format` (not black)
- Linter: `ruff check` — zero errors required before any commit
- Type checking: `mypy src/` — aim for zero errors; new code must be fully typed

## Code Style

- Python 3.11+ features are fine (`match` statements, `tomllib`, `ExceptionGroup`)
- Use `@dataclass` for all data models (`PageRecord`, `DocumentCandidate`, `FilingDecision`)
- Use `pathlib.Path` everywhere — never `os.path.join` or raw string paths
- Use `pyyaml` for config loading — always validate with `validator.py` after loading
- Prefer explicit over implicit — no magic, no metaclass tricks
- Functions do one thing. If a function needs a comment to explain what it does, split it.

## Error Handling

- Never swallow exceptions silently. Log them and either re-raise or send to review queue.
- OCR failures: log the page number and reason, assign page to nearest document with confidence 0.0
- Ollama failures (timeout, invalid JSON): log, retry once, then fall back to review queue
- File system errors (permission denied, disk full): raise immediately with a clear message

## Logging

- Use Python's stdlib `logging` module — not `print()` in library code
- `main.py` and the CLI are the only places `print()` is acceptable (via `rich`)
- Log levels:
  - `DEBUG`: OCR text, signal extraction details, Ollama prompt/response
  - `INFO`: each document identified, each filing decision made
  - `WARNING`: low confidence decisions, OCR rotation retries, rule ambiguities
  - `ERROR`: Ollama failures, file system errors, unparseable pages
- All logs go to both console (INFO+) and `mailarchiver.log` (DEBUG+)

## Testing

- Test file for `src/foo/bar.py` lives at `tests/test_foo_bar.py`
- Use `pytest` with `pytest-cov` — aim for >80% coverage on `src/`
- Fixtures for common objects (`PageRecord`, `DocumentCandidate`, etc.) go in `tests/conftest.py`
- Never test implementation details — test behaviour and outputs
- Tests must not make real Ollama calls — mock `ollama_client.py` in all tests

## Dependencies

- Add to `requirements.txt` with pinned major versions: `pypdf>=4.0`
- Dev dependencies go in `requirements-dev.txt`: `pytest`, `ruff`, `mypy`, `pytest-cov`
- No dependency should require a network connection at runtime (local-first principle)
