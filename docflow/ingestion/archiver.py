"""Ingestion: archive the original scan PDF after a successful run."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def archive_original(pdf_path: Path, config: dict) -> Path:
    """Move *pdf_path* to {scan_watch_folder}/BeenOrganized{mmddyy}/.

    Returns the new path of the archived file.
    """
    scan_watch = Path(os.path.expanduser(
        config.get("scan_watch_folder", "~/ElectronicFiles/ToBeOrganized")
    ))

    date_str = datetime.now().strftime("%m%d%y")
    archive_dir = scan_watch / f"BeenOrganized{date_str}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    dest = archive_dir / pdf_path.name
    shutil.move(str(pdf_path), str(dest))

    logger.info("Archived original: %s → %s", pdf_path, dest)
    return dest
