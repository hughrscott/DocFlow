"""
Per-request logging context (ContextVars) for DocFlow.

Holds request-scoped identifiers like request_id so log formatter
can include them automatically across the call stack and background jobs.
"""

from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(value: Optional[str]) -> None:
    _request_id.set(value)


def get_request_id() -> Optional[str]:
    return _request_id.get()

