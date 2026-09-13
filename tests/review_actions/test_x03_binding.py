"""X03: the local server binds to loopback only; no server is started by these tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import uvicorn
from click.testing import CliRunner

from docflow.cli import cli


@pytest.fixture()
def runs(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    return calls


@pytest.fixture()
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(f"archive_root: {tmp_path / 'archive'}\nprivacy_mode: local_only\n")
    return path


@pytest.mark.parametrize("command", ["ui", "review"])
def test_default_bind_is_loopback(runs, config, command) -> None:
    result = CliRunner().invoke(cli, ["--config", str(config), command, "--port", "18765"])

    assert result.exit_code == 0, result.output
    assert [call["host"] for call in runs] == ["127.0.0.1"]


@pytest.mark.parametrize("command", ["ui", "review"])
@pytest.mark.parametrize("host", ["127.0.0.2", "::1", "localhost"])
def test_explicit_loopback_hosts_are_allowed(runs, config, command, host) -> None:
    result = CliRunner().invoke(cli, ["--config", str(config), command, "--host", host])

    assert result.exit_code == 0, result.output
    assert [call["host"] for call in runs] == [host]


@pytest.mark.parametrize("command", ["ui", "review"])
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5",
                                  "example.test", "", "127.0.0.1.example.test"])
def test_non_loopback_bind_is_rejected_before_serving(runs, config, command, host) -> None:
    result = CliRunner().invoke(cli, ["--config", str(config), command, "--host", host])

    assert result.exit_code != 0
    assert "loopback" in result.output
    assert runs == []
