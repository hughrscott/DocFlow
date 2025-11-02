"""
Settings API for DocFlow.

Allows reading and updating LLM configuration at runtime by modifying
config/llm_config.yaml. Minimal validation to keep it safe.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any
import yaml
import os

from config.settings import settings
from utils.security import require_basic_auth

router = APIRouter(prefix="/api/v1/settings", tags=["settings"]) 


class ProvidersConfig(BaseModel):
    claude_enabled: Optional[bool] = None
    ollama_enabled: Optional[bool] = None
    ollama_base_url: Optional[str] = None
    claude_api_key: Optional[str] = None


class UpdateSettingsRequest(BaseModel):
    vision_provider: Optional[str] = None
    text_provider: Optional[str] = None
    providers: Optional[ProvidersConfig] = None


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


@router.get("")
async def get_settings():
    """Return current LLM settings and providers config."""
    cfg = _load_yaml(settings.llm_config_path)
    return cfg


@router.post("")
async def update_settings(payload: UpdateSettingsRequest, _: bool = Depends(require_basic_auth)):
    """Update a subset of LLM settings safely and persist to file."""
    cfg = _load_yaml(settings.llm_config_path)

    # Basic validation / allowlist
    valid_providers = {"claude", "ollama", "openai"}

    if payload.vision_provider and payload.vision_provider not in valid_providers:
        raise HTTPException(status_code=400, detail="Invalid vision_provider")
    if payload.text_provider and payload.text_provider not in valid_providers:
        raise HTTPException(status_code=400, detail="Invalid text_provider")

    if payload.vision_provider:
        cfg.setdefault("llm", {})["vision_provider"] = payload.vision_provider
    if payload.text_provider:
        cfg.setdefault("llm", {})["text_provider"] = payload.text_provider

    prov_cfg = cfg.setdefault("providers", {})
    if payload.providers:
        if payload.providers.claude_enabled is not None:
            prov_cfg.setdefault("claude", {})["enabled"] = bool(payload.providers.claude_enabled)
        if payload.providers.ollama_enabled is not None:
            prov_cfg.setdefault("ollama", {})["enabled"] = bool(payload.providers.ollama_enabled)
        if payload.providers.ollama_base_url:
            prov_cfg.setdefault("ollama", {})["base_url"] = payload.providers.ollama_base_url
        if payload.providers.claude_api_key:
            # write-through env replacement pattern
            if len(payload.providers.claude_api_key) < 10:
                raise HTTPException(status_code=400, detail="claude_api_key seems invalid")
            prov_cfg.setdefault("claude", {})["api_key"] = payload.providers.claude_api_key

    try:
        _save_yaml(settings.llm_config_path, cfg)
        return {"success": True, "message": "Settings updated", "settings": cfg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write settings: {e}")
