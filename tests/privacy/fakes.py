"""Intercepting fake model transport and synthetic page helpers."""
from __future__ import annotations

import json
from collections.abc import Callable

from docflow.ocr.analyzer import PageRecord
from docflow.privacy.placeholders import PLACEHOLDER_RE
from tests.privacy import sentinels as s


class FakeTransport:
    """Records every sealed request; replies from a scripted queue."""

    def __init__(self, *responses: str | Exception | Callable) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def send(self, request) -> str:
        self.requests.append(request)
        reply = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(reply, Exception):
            raise reply
        return reply(request) if callable(reply) else reply


def messages(request) -> list[dict]:
    return json.loads(request.body)["messages"]


def data(request) -> dict:
    return json.loads(messages(request)[1]["content"])["data"]


def tokens(request, kind: str) -> list[str]:
    content = messages(request)[1]["content"]
    return [t for t in PLACEHOLDER_RE.findall(content) if t.startswith(f"{kind}_")]


def pages(count: int = 2) -> list[PageRecord]:
    return [
        PageRecord(
            page_number=n,
            raw_text=s.page_text(),
            institution="pnc",
            account_hint=s.ACCOUNT,
            period_hint=s.PERIOD,
            page_of_n=f"Page {n} of {count}",
            doc_type_hint="statement",
            confidence=0.9,
            rotation_applied=0,
        )
        for n in range(1, count + 1)
    ]
