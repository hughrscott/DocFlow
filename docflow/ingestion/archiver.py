"""Ingestion: retain the original scan after a successful run, never overwriting."""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from docflow.filing.operations import (
    FilingError,
    collision_candidates,
    fsync_directory,
    partial_path,
    place_without_replacing,
)
from docflow.ingestion.loader import raw_sha256

logger = logging.getLogger(__name__)


def archive_original(pdf_path: Path, config: dict) -> Path:
    """Retain *pdf_path* in {scan_watch_folder}/BeenOrganized{mmddyy}/.

    An existing file of the same name is never replaced: a collision-safe name
    (``scan_2.pdf``, ...) is used unless the existing file is byte-identical. The source
    is removed only after the retained copy verifies. Returns the retained path.
    """
    scan_watch = Path(os.path.expanduser(
        config.get("scan_watch_folder", "~/DocFlowExample/inbox")
    ))
    date_str = datetime.now(UTC).astimezone().strftime("%m%d%y")  # local calendar date
    archive_dir = scan_watch / f"BeenOrganized{date_str}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    expected = raw_sha256(pdf_path)
    for relative in collision_candidates(".", pdf_path.name):
        dest = archive_dir / Path(relative).name
        if os.path.lexists(dest):
            if not dest.is_symlink() and dest.is_file() and raw_sha256(dest) == expected:
                break  # these exact bytes are already retained
            continue
        try:
            place_without_replacing(pdf_path, dest, partial_path(archive_dir, "original", 0))
        except FileExistsError:
            continue
        fsync_directory(archive_dir)
        break
    else:
        raise FilingError("collision_limit_exceeded")

    if raw_sha256(dest) != expected:
        raise FilingError("original_verification_failed")
    if dest.resolve() != Path(pdf_path).resolve():
        Path(pdf_path).unlink()
        fsync_directory(Path(pdf_path).parent)
    logger.info("Archived original: %s → %s", pdf_path.name, dest)
    return dest
