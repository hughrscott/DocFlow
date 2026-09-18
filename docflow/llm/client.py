"""Provider adapter for CloudPromptGateway (OpenAI-compatible APIs).

This is the only module allowed to import or call a provider SDK. It sends
nothing but requests sealed by the gateway, with SDK automatic retries disabled.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping

import openai
from openai import OpenAI

from docflow.llm.gateway import (
    DEFAULT_MODEL,
    SanitizedRequest,
    TransportFailure,
    verify_sealed,
)
from docflow.privacy.types import GatewayBypassError

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MODEL", "PROVIDERS", "OpenAICompatibleTransport", "build_transport"]

# Provider presets — base_url and env var for API key
PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "env_key": None,  # Ollama doesn't need a key
    },
}


class OpenAICompatibleTransport:
    """Send sealed gateway requests to an OpenAI-compatible chat completions API."""

    def __init__(self, *, base_url: str, api_key: str, provider: str, http_client=None) -> None:
        self.provider = provider
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                             http_client=http_client)

    def send(self, request: SanitizedRequest) -> str:
        verify_sealed(request)
        payload = json.loads(request.body)
        try:
            response = self.client.chat.completions.create(
                **payload,
                timeout=request.timeout,
                extra_headers={"Idempotency-Key": request.idempotency_key},
            )
        except openai.APITimeoutError:
            raise TransportFailure("provider timed out", code="timeout", retryable=True) from None
        except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError):
            raise TransportFailure("provider unavailable", retryable=True) from None
        except openai.OpenAIError:
            raise TransportFailure("provider rejected the request") from None
        choices = response.choices or []
        return (choices[0].message.content or "") if choices else ""


def build_transport(config: Mapping) -> OpenAICompatibleTransport:
    """Build the configured provider transport (default factory for the gateway)."""
    from dotenv import load_dotenv
    load_dotenv()

    provider = config.get("llm_provider", "openrouter")
    preset = PROVIDERS.get(provider)
    if preset is None:  # never redirect an unrecognised provider to a cloud preset
        raise TransportFailure("unknown model provider", code="transport_unavailable")
    base_url = config.get("llm_base_url") or preset["base_url"]

    # API key: config > provider-specific env var > generic fallback
    api_key = config.get("llm_api_key")
    if api_key in ("", "••••••••", None):
        api_key = None
    if not api_key and preset["env_key"]:
        api_key = os.environ.get(preset["env_key"])
    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and provider != "ollama":
        raise TransportFailure("no API key configured for the model provider",
                               code="transport_unavailable")

    # Ollama doesn't need a real key but the SDK requires one
    return OpenAICompatibleTransport(base_url=base_url, api_key=api_key or "ollama",
                                     provider=provider)


def chat_json(prompt: str, **kwargs) -> dict | list:
    """Removed direct model call. All model use must go through CloudPromptGateway."""
    raise GatewayBypassError("direct model calls are disabled; use CloudPromptGateway")
