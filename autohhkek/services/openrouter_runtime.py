from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5-nano"
DEFAULT_OPENROUTER_TIMEOUT_SEC = 25.0
OPENROUTER_MODEL_ALIASES = {
    "gpt-5.4": "openai/gpt-5.4",
    "gpt-5.4-nano": "openai/gpt-5-nano",
    "gpt-5-nano": "openai/gpt-5-nano",
    "gpt-4o-mini": "openai/gpt-4o-mini",
}


def normalize_openrouter_model(model: str) -> str:
    value = (model or "").strip()
    if not value:
        return DEFAULT_OPENROUTER_MODEL
    return OPENROUTER_MODEL_ALIASES.get(value, value)


@dataclass(slots=True)
class OpenRouterAppConfig:
    api_key: str = ""
    model: str = DEFAULT_OPENROUTER_MODEL
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    site_url: str = ""
    app_name: str = "AutoHHKek"
    timeout_sec: float = DEFAULT_OPENROUTER_TIMEOUT_SEC

    @classmethod
    def from_env(cls) -> "OpenRouterAppConfig":
        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            model=normalize_openrouter_model(os.getenv("AUTOHHKEK_OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)),
            base_url=os.getenv("AUTOHHKEK_OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).strip() or DEFAULT_OPENROUTER_BASE_URL,
            site_url=(
                os.getenv("AUTOHHKEK_OPENROUTER_REFERER", "").strip()
                or os.getenv("AUTOHHKEK_OPENROUTER_SITE_URL", "").strip()
            ),
            app_name=(
                os.getenv("AUTOHHKEK_OPENROUTER_TITLE", "").strip()
                or os.getenv("AUTOHHKEK_OPENROUTER_APP_NAME", "AutoHHKek").strip()
                or "AutoHHKek"
            ),
            timeout_sec=float(os.getenv("AUTOHHKEK_OPENROUTER_TIMEOUT_SEC", str(DEFAULT_OPENROUTER_TIMEOUT_SEC)) or DEFAULT_OPENROUTER_TIMEOUT_SEC),
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def build_provider(self):
        from agents import OpenAIProvider
        from openai import AsyncOpenAI

        headers = {}
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-OpenRouter-Title"] = self.app_name
        client = AsyncOpenAI(
            api_key=self.api_key or None,
            base_url=self.base_url or DEFAULT_OPENROUTER_BASE_URL,
            default_headers=headers or None,
            timeout=self.timeout_sec,
        )
        return OpenAIProvider(openai_client=client, use_responses=True)

    def build_model_settings(self):
        from agents import ModelSettings

        return ModelSettings(verbosity="low")

    def build_run_config(self, *, workflow_name: str = "AutoHHKek workflow", model: str | None = None):
        from agents import RunConfig

        return RunConfig(
            model=normalize_openrouter_model(model or self.model),
            model_provider=self.build_provider(),
            model_settings=self.build_model_settings(),
            tracing_disabled=True,
            workflow_name=workflow_name,
        )

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "available": self.is_available(),
            "model": self.model,
            "base_url": self.base_url,
            "site_url": self.site_url,
            "app_name": self.app_name,
            "timeout_sec": self.timeout_sec,
        }
