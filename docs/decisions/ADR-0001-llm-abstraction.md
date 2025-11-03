# ADR-0001: LLM Abstraction and Fallback

Status: accepted
Date: 2025-11-03

Context
- Support multiple LLM providers (Claude, Ollama) with health checks and fallbacks.

Decision
- Implement `llm/base.py` interfaces and `LLMManager` to orchestrate provider selection and fallback.

Consequences
- Clear separation of provider-specific code; easier to add providers.
- Centralized error handling and health checks.

