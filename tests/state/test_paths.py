"""S01/X01: application-state roots are platform-correct and outside archives."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow.state.paths import (
    StatePaths,
    UnsafePathError,
    default_state_root,
    resolve_state_paths,
    validate_archive_root,
)


def test_macos_state_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = default_state_root(system="Darwin", home=home, environ={})
    assert root == home / "Library" / "Application Support" / "DocFlow"


def test_linux_state_root_defaults_to_local_share(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert default_state_root(system="Linux", home=home, environ={}) == (
        home / ".local" / "share" / "docflow"
    )


def test_linux_state_root_honours_absolute_xdg_data_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    environ = {"XDG_DATA_HOME": str(xdg)}
    assert default_state_root(system="Linux", home=home, environ=environ) == xdg / "docflow"
    # Relative XDG values are invalid per spec and are ignored.
    relative = {"XDG_DATA_HOME": "relative/data"}
    assert default_state_root(system="Linux", home=home, environ=relative) == (
        home / ".local" / "share" / "docflow"
    )


def test_default_resolution_uses_isolated_home(isolated_home: Path) -> None:
    root = default_state_root()
    assert isolated_home in root.parents


def test_unsafe_home_fails_closed() -> None:
    with pytest.raises(UnsafePathError):
        default_state_root(system="Linux", home=Path("/"), environ={})
    with pytest.raises(UnsafePathError):
        default_state_root(system="Linux", home=Path("relative-home"), environ={})


def test_state_paths_layout(tmp_path: Path) -> None:
    paths = StatePaths(tmp_path / "state")
    assert paths.database.name == "state.sqlite3"
    assert paths.wal.name == "state.sqlite3-wal"
    assert paths.lock.name == "docflow.lock"
    for p in (paths.database, paths.wal, paths.lock, paths.logs, paths.cache,
              paths.exports, paths.backups):
        assert paths.root in p.parents


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_all_state_paths_outside_archive(system: str, archive_root: Path, isolated_home: Path) -> None:
    paths = resolve_state_paths([archive_root], system=system, home=isolated_home, environ={})
    paths.ensure()
    for p in paths.all_paths():
        assert archive_root.resolve() not in p.resolve().parents


def test_state_root_inside_archive_rejected(archive_root: Path) -> None:
    with pytest.raises(UnsafePathError):
        resolve_state_paths([archive_root], root=archive_root / "app-state")


def test_archive_inside_state_root_rejected(tmp_path: Path) -> None:
    state = tmp_path / "state"
    archive = state / "archive"
    archive.mkdir(parents=True)
    with pytest.raises(UnsafePathError):
        resolve_state_paths([archive], root=state)


def test_icloud_state_root_rejected(tmp_path: Path, archive_root: Path) -> None:
    icloud = tmp_path / "home" / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    with pytest.raises(UnsafePathError):
        resolve_state_paths([archive_root], root=icloud / "DocFlow")


def test_forbidden_real_home_rejected(tmp_path: Path, archive_root: Path) -> None:
    forbidden = tmp_path / "pretend-real-home"
    with pytest.raises(UnsafePathError):
        resolve_state_paths([archive_root], root=forbidden / "state", forbidden_roots=[forbidden])
    with pytest.raises(UnsafePathError):
        resolve_state_paths([forbidden / "archive"], root=tmp_path / "s", forbidden_roots=[forbidden])


def test_validate_archive_root(tmp_path: Path, archive_root: Path, isolated_home: Path) -> None:
    state = tmp_path / "state"
    assert validate_archive_root(archive_root, home=isolated_home, state_root=state) == (
        archive_root.resolve()
    )
    bad = [
        tmp_path / "missing",
        archive_root / "Tax" / "2026 Taxes" / "TaxInvoice.pdf",
        Path("/"),
        isolated_home,
        Path("/etc"),
    ]
    for candidate in bad:
        with pytest.raises(UnsafePathError):
            validate_archive_root(candidate, home=isolated_home, state_root=state)
    state.mkdir()
    with pytest.raises(UnsafePathError):
        validate_archive_root(state, home=isolated_home, state_root=state)
