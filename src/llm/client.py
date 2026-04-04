"""LLM client: OpenRouter integration via OpenAI-compatible API."""
from __future__ import annotations

import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

# Default model — good balance of cost, speed, and structured output quality
DEFAULT_MODEL = "google/gemini-2.0-flash-001"


def _get_client() -> OpenAI:
    """Build an OpenAI client pointed at OpenRouter."""
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. "
            "Add it to .env or export it as an environment variable."
        )

    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


def chat_json(
    prompt: str,
    *,
    system: str = "You are a document analysis assistant. Always respond with valid JSON.",
    model: str | None = None,
    config: dict | None = None,
) -> dict | list:
    """Send a prompt to OpenRouter and parse the JSON response.

    Args:
        prompt: The user message.
        system: System message.
        model: Override model (defaults to config or DEFAULT_MODEL).
        config: Pipeline config dict — reads ``openrouter_model`` if present.

    Returns:
        Parsed JSON (dict or list).

    Raises:
        ValueError: If the response is not valid JSON.
        RuntimeError: If the API call fails.
    """
    if model is None:
        model = (config or {}).get("openrouter_model", DEFAULT_MODEL)

    client = _get_client()

    logger.info("LLM call: model=%s, prompt_length=%d", model, len(prompt))

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    logger.debug("LLM raw response: %s", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Try to extract JSON from markdown code blocks
        import re
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m:
            return json.loads(m.group(1))
        raise ValueError(f"LLM returned invalid JSON: {raw[:500]}") from exc
