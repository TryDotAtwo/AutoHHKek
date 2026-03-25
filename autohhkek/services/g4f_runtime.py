from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


_DEFAULT_MODEL = "gpt-4o-mini"
_CURATED_MODELS = {
    "gpt-4o-mini": "gpt_4o_mini",
    "gpt-4o": "gpt_4o",
    "gemini-2.5-flash": "gemini_2_5_flash",
}
_PROVIDER_PRIORITY = {
    "OpenaiChat": 0,
    "GeminiPro": 1,
    "Chatai": 2,
    "Copilot": 3,
    "PollinationsAI": 4,
}


def _provider_name(provider: Any) -> str:
    return getattr(provider, "__name__", None) or getattr(provider, "__qualname__", None) or str(provider)


def _flatten_provider_chain(provider: Any) -> list[Any]:
    if provider is None:
        return []
    providers = getattr(provider, "providers", None)
    if providers:
        flattened: list[Any] = []
        for item in providers:
            flattened.extend(_flatten_provider_chain(item))
        return flattened
    return [provider]


@lru_cache(maxsize=1)
def _discover_verified_targets() -> list[dict[str, Any]]:
    try:
        import g4f.models as g4f_models
    except Exception:  # noqa: BLE001
        return []

    discovered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for model_name, attr_name in _CURATED_MODELS.items():
        model_obj = getattr(g4f_models, attr_name, None)
        if model_obj is None:
            continue
        providers = _flatten_provider_chain(getattr(model_obj, "best_provider", None))
        for provider in providers:
            provider_name = _provider_name(provider)
            key = (model_name, provider_name)
            if key in seen:
                continue
            seen.add(key)
            item = {
                "model": model_name,
                "provider": provider_name,
                "base_provider": str(getattr(model_obj, "base_provider", "")),
                "working": bool(getattr(provider, "working", False)),
                "needs_auth": bool(getattr(provider, "needs_auth", False)),
            }
            if item["working"] and not item["needs_auth"]:
                item["label"] = f"{item['model']} via {item['provider']}"
                discovered.append(item)
    return sorted(
        discovered,
        key=lambda item: (
            list(_CURATED_MODELS).index(item["model"]) if item["model"] in _CURATED_MODELS else len(_CURATED_MODELS),
            _PROVIDER_PRIORITY.get(item["provider"], 99),
            item["provider"],
        ),
    )


@dataclass(slots=True)
class G4FAppConfig:
    model: str = _DEFAULT_MODEL
    provider: str = ""
    _resolved_target: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_env(cls) -> "G4FAppConfig":
        return cls(
            model=os.getenv("AUTOHHKEK_G4F_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL,
            provider=os.getenv("AUTOHHKEK_G4F_PROVIDER", "").strip(),
        )

    def available_targets(self) -> list[dict[str, Any]]:
        return [dict(item) for item in _discover_verified_targets()]

    def resolve_target(self) -> dict[str, Any] | None:
        targets = self.available_targets()
        if not targets:
            self._resolved_target = None
            return None

        requested_model = (self.model or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
        requested_provider = (self.provider or "").strip()
        exact_match = next(
            (
                target
                for target in targets
                if target["model"] == requested_model and target["provider"] == requested_provider
            ),
            None,
        )
        if exact_match is not None:
            chosen = dict(exact_match)
            resolution = "requested_pair"
        else:
            same_model_targets = [target for target in targets if target["model"] == requested_model]
            if same_model_targets:
                chosen = dict(same_model_targets[0])
                resolution = "fallback_provider" if requested_provider else "requested_model"
            else:
                chosen = dict(targets[0])
                resolution = "fallback_model"

        chosen["requested_model"] = requested_model
        chosen["requested_provider"] = requested_provider
        chosen["resolution"] = resolution
        self.model = chosen["model"]
        self.provider = chosen["provider"]
        self._resolved_target = chosen
        return dict(chosen)

    def is_available(self) -> bool:
        return self.resolve_target() is not None

    def to_runtime_dict(self, *, requested_model: str = "", requested_provider: str = "") -> dict[str, Any]:
        resolved = self.resolve_target()
        return {
            "ready": resolved is not None,
            "requested_model": requested_model or self.model,
            "requested_provider": requested_provider or self.provider,
            "model": resolved["model"] if resolved else self.model,
            "provider": resolved["provider"] if resolved else self.provider,
            "resolved_model": resolved["model"] if resolved else "",
            "resolved_provider": resolved["provider"] if resolved else "",
            "resolution": resolved["resolution"] if resolved else "unavailable",
            "targets": self.available_targets(),
            "supports_mcp_repair": False,
        }
