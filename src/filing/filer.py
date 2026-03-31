"""Filing: create target directories and move extracted PDFs."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_directory(path: Path) -> None:
    """Create *path* and any missing parents."""
    path.mkdir(parents=True, exist_ok=True)
    logger.debug("Ensured directory: %s", path)
