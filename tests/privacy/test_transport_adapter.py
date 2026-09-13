"""X02/P04: the provider adapter is sealed, retry-free, and cannot reach the network in tests."""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from docflow.llm import gateway as gateway_module
from docflow.llm.client import OpenAICompatibleTransport, build_transport, chat_json
from docflow.llm.gateway import (
    MAX_ATTEMPTS,
    CloudPromptGateway,
    SanitizedRequest,
    TransportFailure,
)
from docflow.privacy.types import GatewayBypassError
from tests.privacy import fakes
from tests.privacy import sentinels as s

CONFIG = {**s.synthetic_config(), "llm_api_key": "synthetic-test-key",
          "llm_base_url": "http://192.0.2.10/v1"}
COMPLETION = {
    "id": "synthetic", "object": "chat.completion", "created": 0, "model": "synthetic-model",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": '{"status": "ok"}'}}],
}


def test_sdk_automatic_retries_are_disabled() -> None:
    assert build_transport(CONFIG).client.max_retries == 0


def test_missing_api_key_is_transport_unavailable() -> None:
    with pytest.raises(TransportFailure) as excinfo:
        build_transport({**CONFIG, "llm_api_key": None})
    assert excinfo.value.code == "transport_unavailable"


@pytest.mark.parametrize("base_url", ["http://192.0.2.10/v1", None])
def test_real_adapter_cannot_reach_network(monkeypatch, network_attempts, base_url) -> None:
    monkeypatch.setattr(gateway_module, "transport_factory",
                        gateway_module._default_transport_factory)
    gateway = CloudPromptGateway({**CONFIG, "llm_base_url": base_url})
    with pytest.raises(TransportFailure) as excinfo:
        gateway.test_connection()
    assert excinfo.value.retryable is True
    assert len(network_attempts) == MAX_ATTEMPTS  # one socket attempt per gateway attempt


def _sealed_request() -> tuple[SanitizedRequest, fakes.FakeTransport]:
    transport = fakes.FakeTransport('{"status": "ok"}')
    CloudPromptGateway(CONFIG, transport=transport).test_connection()
    return transport.requests[0], transport


def test_forged_or_tampered_requests_are_refused_before_io(network_attempts) -> None:
    sealed, _ = _sealed_request()
    adapter = build_transport(CONFIG)
    forged = SanitizedRequest(sealed.feature, sealed.job_id, sealed.idempotency_key,
                              json.dumps({"messages": [{"role": "user",
                                                        "content": s.USER_NAME}]}).encode())
    tampered = dataclasses.replace(sealed, body=sealed.body.replace(b"connectivity", b"Vardabrek"))
    for request in (forged, tampered):
        with pytest.raises(GatewayBypassError):
            adapter.send(request)
    assert network_attempts == []


def test_legacy_direct_call_is_a_gateway_bypass(model_transport_calls, network_attempts) -> None:
    with pytest.raises(GatewayBypassError):
        chat_json(f"classify {s.page_text()}", config=CONFIG)
    assert model_transport_calls == [] and network_attempts == []


def test_adapter_retries_send_identical_wire_payload_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500 if len(seen) == 1 else 200, json=COMPLETION)

    adapter = OpenAICompatibleTransport(
        base_url="http://192.0.2.10/v1", api_key="synthetic-test-key", provider="openai",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = CloudPromptGateway(CONFIG, transport=adapter).test_connection()
    assert result.model == "synthetic-model"
    first, second = seen
    assert first.content == second.content
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    wire = json.loads(first.content)
    assert wire["messages"] == json.loads(_sealed_request()[0].body)["messages"]
    assert wire["response_format"] == {"type": "json_object"}
    s.assert_no_sentinels(first.content)


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (401, False), (429, True)])
def test_adapter_maps_provider_errors(status: int, retryable: bool) -> None:
    adapter = OpenAICompatibleTransport(
        base_url="http://192.0.2.10/v1", api_key="synthetic-test-key", provider="openai",
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": {"message": s.USER_NAME}}))))
    sealed, _ = _sealed_request()
    with pytest.raises(TransportFailure) as excinfo:
        adapter.send(sealed)
    assert excinfo.value.retryable is retryable
    assert s.USER_NAME not in str(excinfo.value)
