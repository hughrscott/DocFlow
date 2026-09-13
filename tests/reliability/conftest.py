"""Hermetic fixtures for Phase 3 reliability tests.

HOME and XDG_DATA_HOME are redirected under ``tmp_path``; every archive, state
and watch root is a temporary directory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REAL_HOME = Path.home()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert Path.home() == home
    assert REAL_HOME not in home.parents
    return home
