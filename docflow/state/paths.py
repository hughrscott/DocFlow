"""Application-state path resolution.

State (database, WAL, lock, logs, cache, exports, backups) lives in local
platform storage and must never overlap an archive root or iCloud storage.
"""
from __future__ import annotations

import os
import platform
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ICLOUD_MARKERS = ("Mobile Documents", "com~apple~CloudDocs", "iCloud Drive", "CloudStorage")
SYSTEM_DIRECTORIES = frozenset(
    Path(p) for p in (
        "/bin", "/boot", "/dev", "/etc", "/lib", "/opt", "/proc", "/root", "/sbin",
        "/sys", "/tmp", "/usr", "/var", "/private", "/System", "/Library",
        "/Applications", "/Volumes", "/Users", "/home",
    )
)


class UnsafePathError(ValueError):
    """Raised when a path would place state or archives in an unsafe location."""


@dataclass(frozen=True)
class StatePaths:
    root: Path

    @property
    def database(self) -> Path:
        return self.root / "state.sqlite3"

    @property
    def wal(self) -> Path:
        return self.root / "state.sqlite3-wal"

    @property
    def shm(self) -> Path:
        return self.root / "state.sqlite3-shm"

    @property
    def lock(self) -> Path:
        return self.root / "docflow.lock"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def all_paths(self) -> tuple[Path, ...]:
        return (self.database, self.wal, self.shm, self.lock, self.logs,
                self.cache, self.exports, self.backups)

    def ensure(self) -> None:
        for directory in (self.root, self.logs, self.cache, self.exports, self.backups):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)


def default_state_root(
    *,
    system: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the platform state root: macOS Application Support or Linux XDG data."""
    system = system or platform.system()
    home = Path.home() if home is None else home
    environ = os.environ if environ is None else environ
    if not home.is_absolute() or home == Path(home.anchor):
        raise UnsafePathError("home directory must be an absolute non-root path")
    if system == "Darwin":
        return home / "Library" / "Application Support" / "DocFlow"
    xdg = environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / "docflow"


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def is_icloud_path(path: Path) -> bool:
    text = path.as_posix()
    return any(marker in text for marker in ICLOUD_MARKERS)


def resolve_state_paths(
    archive_roots: Iterable[Path],
    *,
    root: Path | None = None,
    system: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    forbidden_roots: Iterable[Path] = (),
) -> StatePaths:
    """Resolve and validate state paths; fail closed on any overlap."""
    state_root = root if root is not None else default_state_root(
        system=system, home=home, environ=environ
    )
    state_root = state_root.expanduser().resolve()
    if is_icloud_path(state_root):
        raise UnsafePathError("application state must not live in iCloud storage")
    forbidden = [Path(p).expanduser().resolve() for p in forbidden_roots]
    archives = [Path(p).expanduser().resolve() for p in archive_roots]
    for guarded in forbidden:
        if _is_within(state_root, guarded) or any(_is_within(a, guarded) for a in archives):
            raise UnsafePathError("path resolves beneath a forbidden root")
    for archive in archives:
        if _is_within(state_root, archive) or _is_within(archive, state_root):
            raise UnsafePathError("application state must be outside every archive root")
    return StatePaths(state_root)


def validate_archive_root(candidate: Path, *, home: Path, state_root: Path) -> Path:
    """Validate a user-selected archive root for ``POST /api/v1/archive-scopes``."""
    path = Path(candidate).expanduser()
    if not path.exists():
        raise UnsafePathError("archive root does not exist")
    if not path.is_dir():
        raise UnsafePathError("archive root must be a directory")
    resolved = path.resolve()
    state = state_root.expanduser().resolve()
    if resolved == Path(resolved.anchor) or resolved in SYSTEM_DIRECTORIES:
        raise UnsafePathError("archive root must not be a system directory")
    if resolved == home.expanduser().resolve():
        raise UnsafePathError("archive root must not be the home directory")
    if _is_within(resolved, state) or _is_within(state, resolved):
        raise UnsafePathError("archive root must not overlap application state")
    return resolved
