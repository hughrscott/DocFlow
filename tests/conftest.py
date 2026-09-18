"""Shared pytest fixtures."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest
from pypdf import PdfWriter


@pytest.fixture()
def minimal_pdf(tmp_path: Path) -> Path:
    """Write a 3-page blank PDF to a temp directory and return its path."""
    pdf_path = tmp_path / "test_scan.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)  # letter size in points
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path


_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Deny all DNS and IP socket egress for every test; record attempts by kind only."""
    attempts: list[str] = []

    def refuse_lookup(*args, **kwargs):
        attempts.append("dns")
        raise socket.gaierror("network access is disabled in tests")

    def guarded(real):
        def connect(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                attempts.append("connect")
                raise ConnectionRefusedError("network access is disabled in tests")
            return real(self, address)
        return connect

    monkeypatch.setattr(socket, "getaddrinfo", refuse_lookup)
    monkeypatch.setattr(socket.socket, "connect", guarded(_REAL_CONNECT))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(_REAL_CONNECT_EX))
    return attempts


@pytest.fixture(autouse=True)
def model_transport_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the provider transport with a recording deny-all fake for every test."""
    import dotenv

    from docflow.llm import gateway

    calls: list = []

    class DenyAllTransport:
        def send(self, request):
            calls.append(request)
            raise gateway.TransportFailure("no model transport in tests",
                                           code="transport_unavailable")

    monkeypatch.setattr(gateway, "transport_factory", lambda config: DenyAllTransport())
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return calls
