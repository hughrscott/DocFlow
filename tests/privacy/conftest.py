"""Hermetic state fixtures for privacy tests (HOME and roots under tmp_path)."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow.state.database import StateDatabase
from docflow.state.paths import StatePaths
from docflow.state.repositories import StateStore


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return home


@pytest.fixture()
def archive_root(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    (root / "_Unmatched").mkdir(parents=True)
    return root


@pytest.fixture()
def store(tmp_path: Path):
    with StateDatabase(StatePaths(tmp_path / "app-state")) as db:
        yield StateStore(db)


@pytest.fixture()
def scope_id(store: StateStore, archive_root: Path, isolated_home: Path) -> str:
    return store.scopes.register(archive_root, home=isolated_home).id


@pytest.fixture()
def ocr_job(store: StateStore, scope_id: str):
    job = store.jobs.create(scope_id, source_fingerprint="sha256:synthetic",
                            source_name="synthetic.pdf", source_locator="upload:synthetic.pdf",
                            page_count=2)
    for expected, new in (("discovered", "stabilizing"), ("stabilizing", "ready"), ("ready", "ocr")):
        assert store.jobs.transition(scope_id, job.id, expected=expected, new=new)
    return job


@pytest.fixture()
def intercept(monkeypatch: pytest.MonkeyPatch):
    """Route the gateway's default transport to a recording fake (set ``.responses``)."""
    from docflow.llm import gateway
    from tests.privacy.fakes import FakeTransport

    transport = FakeTransport('{"unexpected": true}')
    monkeypatch.setattr(gateway, "transport_factory", lambda config: transport)
    return transport
