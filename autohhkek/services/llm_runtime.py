from __future__ import annotations

from typing import Any

from autohhkek.services.g4f_runtime import G4FAppConfig
from autohhkek.services.openai_runtime import OpenAIAppConfig
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig
from autohhkek.services.runtime_settings import normalize_runtime_settings


class LLMRuntime:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        source = settings.to_dict() if hasattr(settings, "to_dict") else settings
        self.settings = normalize_runtime_settings(source)
        self.openai = OpenAIAppConfig.from_env()
        self.openrouter = OpenRouterAppConfig.from_env()
        self.g4f = G4FAppConfig.from_env()
        self.openai.model = self.settings["openai_model"]
        self.openrouter.model = self.settings["openrouter_model"]
        self.g4f.model = self.settings["g4f_model"]
        self.g4f.provider = self.settings["g4f_provider"]

    @property
    def selected_backend(self) -> str:
        return self.settings["llm_backend"]

    def effective_backend(self) -> str:
        requested = self.selected_backend
        fallback_orders = {
            "openai": ["openai", "openrouter", "g4f"],
            "openrouter": ["openrouter", "openai", "g4f"],
            "g4f": ["g4f", "openrouter", "openai"],
        }
        for backend in fallback_orders.get(requested, [requested, "openrouter", "g4f", "openai"]):
            if self.backend_ready(backend):
                return backend
        return requested

    def backend_ready(self, backend: str) -> bool:
        if backend == "g4f":
            return self.g4f.is_available()
        if backend == "openrouter":
            return self.openrouter.is_available()
        return self.openai.is_available()

    def selected_backend_ready(self) -> bool:
        return self.backend_ready(self.selected_backend)

    def capabilities(self) -> dict[str, Any]:
        g4f_runtime = self.g4f.to_runtime_dict(
            requested_model=self.settings["g4f_model"],
            requested_provider=self.settings["g4f_provider"],
        )
        effective_backend = self.effective_backend()
        return {
            "selected_backend": self.selected_backend,
            "selected_backend_ready": self.selected_backend_ready(),
            "effective_backend": effective_backend,
            "effective_backend_ready": self.backend_ready(effective_backend),
            "fallback_applied": effective_backend != self.selected_backend,
            "backends": {
                "openai": {
                    "ready": self.openai.is_available(),
                    "model": self.openai.model,
                    "supports_mcp_repair": True,
                },
                "openrouter": {
                    "ready": self.openrouter.is_available(),
                    "model": self.openrouter.model,
                    "base_url": self.openrouter.base_url,
                    "supports_mcp_repair": True,
                },
                "g4f": g4f_runtime,
            },
        }
