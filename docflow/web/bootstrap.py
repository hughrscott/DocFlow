"""Open the local application state that backs the ``/api/v1`` routes of ``docflow ui``.

The archive root comes only from the local config file (never from HTTP callers). It must
be set explicitly and exist; there is no fallback to a default archive path. Operational
state lives in the platform application-state directory, outside the archive.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from docflow.filing.operations import DurableFiler
from docflow.state.database import StateDatabase, WriterLockError
from docflow.state.paths import (
    StatePaths,
    UnsafePathError,
    resolve_state_paths,
    validate_archive_root,
)
from docflow.state.repositories import StateStore


class StateBootstrapError(RuntimeError):
    """Local state cannot be opened; the message tells the user what to fix."""


@dataclass
class LocalState:
    db: StateDatabase
    store: StateStore
    filer: DurableFiler
    scope_id: str

    def close(self) -> None:
        self.db.close()


def _configured_path(config: dict, key: str) -> Path | None:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(os.path.expanduser(value.strip()))


def local_state_paths(config: dict, *, home: Path | None = None) -> tuple[Path, StatePaths]:
    """Validated archive root and its application-state paths; creates nothing on disk."""
    archive = _configured_path(config, "archive_root")
    if archive is None:
        raise StateBootstrapError(
            "archive_root is not set in the config file. Set it to your archive folder "
            "(for example with 'docflow init') and start DocFlow again.")
    home = Path.home() if home is None else home
    try:
        paths = resolve_state_paths([archive], home=home)
    except UnsafePathError as exc:
        raise StateBootstrapError(f"Local application state is unsafe: {exc}.") from None
    try:
        validate_archive_root(archive, home=home, state_root=paths.root)
    except UnsafePathError as exc:
        raise StateBootstrapError(
            f"archive_root in the config file cannot be used: {exc}.") from None
    return archive, paths


def open_local_state(config: dict, *, home: Path | None = None) -> LocalState:
    """Register the configured archive as the active scope and open its durable state."""
    home = Path.home() if home is None else home
    archive, paths = local_state_paths(config, home=home)  # before any state is created
    try:
        db = StateDatabase(paths).open()
    except WriterLockError:
        raise StateBootstrapError(
            "Another DocFlow process is using the local state. Stop it and try again.") from None
    try:
        store = StateStore(db)
        scope = store.scopes.register(archive, home=home)
    except UnsafePathError as exc:
        db.close()
        raise StateBootstrapError(
            f"archive_root in the config file cannot be used: {exc}.") from None
    except BaseException:
        db.close()
        raise
    roots = {"upload": paths.cache / "uploads"}  # /api/upload staging, outside the archive
    watch = _configured_path(config, "scan_watch_folder")
    if watch is not None:
        roots["watch"] = watch
    return LocalState(db, store, DurableFiler(store, source_roots=roots), scope.id)
