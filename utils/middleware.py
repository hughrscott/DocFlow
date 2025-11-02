"""
Custom middleware for DocFlow.

Adds per-request ID and timing logs.
"""

import time
import uuid
from typing import Callable
from fastapi import Request, Response
import logging

logger = logging.getLogger(__name__)


async def request_id_and_timing_middleware(request: Request, call_next: Callable) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.perf_counter()
    # Attach to state for handlers if needed
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            f"rid={request_id} {request.method} {request.url.path} -> {getattr(request, 'client', None)} {duration_ms:.1f}ms"
        )
    # Include header
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-ms"] = f"{duration_ms:.1f}"
    return response

