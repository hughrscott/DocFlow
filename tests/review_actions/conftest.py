"""Hermetic fixtures for Phase 4 review-action and undo tests.

HOME and XDG_DATA_HOME are redirected under ``tmp_path``; every archive, state
and watch root is a temporary directory. No model transport or network is used.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docflow.web import app as web_app
from tests.reliability.harness import make_env

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


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


@pytest.fixture()
def client(env, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The local app wired exactly as ``docflow ui`` wires it, including the scope."""
    monkeypatch.setattr(web_app, "_state_store", env.store)
    monkeypatch.setattr(web_app, "_filer", env.filer())
    monkeypatch.setattr(web_app, "_active_scope_id", env.scope_id)
    return TestClient(web_app.app)
