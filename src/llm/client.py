"""LLM client: multi-provider support via OpenAI-compatible API."""
from __future__ import annotations

import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "google/gemini-2.0-flash-001"

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


def _get_client(config: dict | None = None) -> OpenAI:
    """Build an OpenAI client for the configured provider."""
    from dotenv import load_dotenv
    load_dotenv()

    cfg = config or {}

    # Determine provider and settings
    provider = cfg.get("llm_provider", "openrouter")
    preset = PROVIDERS.get(provider, PROVIDERS["openrouter"])

    # Config can override base_url directly
    base_url = cfg.get("llm_base_url", preset["base_url"])

    # API key: config > env var > provider-specific env var
    api_key = cfg.get("llm_api_key")
    if not api_key and preset["env_key"]:
        api_key = os.environ.get(preset["env_key"])
    if not api_key:
        # Try generic fallback
        api_key = os.environ.get("OPENROUTER_API_KEY")

    if not api_key and provider != "ollama":
        raise RuntimeError(
            f"No API key found for provider '{provider}'. "
            f"Set it in config (llm_api_key), in .env ({preset.get('env_key', '?')}), "
            f"or in the Settings UI."
        )

    # Ollama doesn't need a real key but the SDK requires one
    if not api_key:
        api_key = "ollama"

    return OpenAI(base_url=base_url, api_key=api_key)


def chat_json(
    prompt: str,
    *,
    system: str = "You are a document analysis assistant. Always respond with valid JSON.",
    model: str | None = None,
    config: dict | None = None,
) -> dict | list:
    """Send a prompt and parse the JSON response.

    Supports any OpenAI-compatible API (OpenRouter, OpenAI, Groq, Ollama).
    """
    cfg = config or {}
    if model is None:
        model = cfg.get("llm_model") or cfg.get("openrouter_model") or DEFAULT_MODEL

    client = _get_client(cfg)

    logger.info("LLM call: model=%s, prompt_length=%d", model, len(prompt))

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    # Not all providers support response_format
    provider = cfg.get("llm_provider", "openrouter")
    if provider != "ollama":
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)

    raw = response.choices[0].message.content
    logger.debug("LLM raw response: %s", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        import re
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m:
            return json.loads(m.group(1))
        raise ValueError(f"LLM returned invalid JSON: {raw[:500]}") from exc
