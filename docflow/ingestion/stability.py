"""Input stabilization: only ingest a scan whose bytes and page count have settled.

Checks are read-only. Any state other than ``ready`` is retryable and leaves the
input byte-identical.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pypdf import PdfReader

from docflow.ingestion.loader import raw_sha256

UF_DATALESS = 0x40000000  # macOS <sys/stat.h>: contents are not materialized locally
PARTIAL_DOWNLOAD_SUFFIXES = (".crdownload", ".part", ".partial", ".download")


class InputState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    ZERO_BYTE = "zero_byte"
    MALFORMED = "malformed"
    CHANGING = "changing"
    DOWNLOAD_NOT_READY = "download_not_ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StabilityResult:
    state: InputState
    page_count: int | None = None
    size: int | None = None
    raw_sha256: str | None = None

    @property
    def retryable(self) -> bool:
        return self.state is not InputState.READY


def _count_pages(path: Path) -> int:
    with open(path, "rb") as fh:
        return len(PdfReader(fh).pages)


def _safe_count(count_pages: Callable[[Path], int], path: Path) -> int | None:
    try:
        return count_pages(path)
    except Exception:  # noqa: BLE001 - any parser failure means "not a usable PDF yet"
        return None


def check_input(
    path: Path,
    *,
    observations: int = 3,
    interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    stat: Callable[[Path], os.stat_result] = os.stat,
    count_pages: Callable[[Path], int] = _count_pages,
) -> StabilityResult:
    """Observe ``path`` ``observations`` times, ``interval`` seconds apart."""
    path = Path(path)
    if path.name.endswith(".icloud") or (path.parent / f".{path.name}.icloud").exists():
        return StabilityResult(InputState.UNAVAILABLE)
    if path.name.endswith(PARTIAL_DOWNLOAD_SUFFIXES) or any(
        (path.parent / f"{path.name}{suffix}").exists() for suffix in PARTIAL_DOWNLOAD_SUFFIXES
    ):
        return StabilityResult(InputState.DOWNLOAD_NOT_READY)
    seen = []
    for index in range(observations):
        if index:
            sleep(interval)
        try:
            st = stat(path)
        except FileNotFoundError:
            return StabilityResult(InputState.MISSING)
        if getattr(st, "st_flags", 0) & UF_DATALESS:
            return StabilityResult(InputState.UNAVAILABLE)
        if st.st_size == 0:
            return StabilityResult(InputState.ZERO_BYTE, size=0)
        seen.append((st.st_size, st.st_mtime_ns, _safe_count(count_pages, path)))
    size, _, pages = seen[-1]
    if len({(st_size, mtime) for st_size, mtime, _ in seen}) > 1:
        return StabilityResult(InputState.CHANGING, size=size)
    if any(count is None or count < 1 for _, _, count in seen):
        return StabilityResult(InputState.MALFORMED, size=size)
    if len({count for _, _, count in seen}) > 1:
        return StabilityResult(InputState.CHANGING, size=size)
    digest = raw_sha256(path)
    try:
        after = stat(path)
    except FileNotFoundError:
        return StabilityResult(InputState.MISSING)
    if (after.st_size, after.st_mtime_ns) != seen[-1][:2]:
        return StabilityResult(InputState.CHANGING, size=after.st_size)
    return StabilityResult(InputState.READY, pages, size, digest)
