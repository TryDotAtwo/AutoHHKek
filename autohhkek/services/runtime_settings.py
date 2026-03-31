from __future__ import annotations

import os
from typing import Any

from autohhkek.services.openrouter_runtime import normalize_openrouter_model


DEFAULT_RUNTIME_SETTINGS: dict[str, Any] = {
    "llm_backend": "openrouter",
    "dashboard_mode": "analyze",
    "mode_selected": False,
    "auto_run_repair_worker": False,
    "openai_model": "gpt-5.4",
    "openrouter_model": "openai/gpt-5-nano",
    "g4f_model": "gpt-4o-mini",
    "g4f_provider": "",
    "selected_resume_id": "",
}

AVAILABLE_LLM_BACKENDS = ["openai", "openrouter", "g4f"]
AVAILABLE_DASHBOARD_MODES = ["analyze", "apply_plan", "repair", "full_pipeline"]
LEGACY_DASHBOARD_MODE_ALIASES = {
    "plan_apply": "apply_plan",
}


def _runtime_defaults_from_env() -> dict[str, Any]:
    return {
        **DEFAULT_RUNTIME_SETTINGS,
        "openai_model": os.getenv("AUTOHHKEK_OPENAI_MODEL", DEFAULT_RUNTIME_SETTINGS["openai_model"]).strip() or DEFAULT_RUNTIME_SETTINGS["openai_model"],
        "openrouter_model": normalize_openrouter_model(os.getenv("AUTOHHKEK_OPENROUTER_MODEL", DEFAULT_RUNTIME_SETTINGS["openrouter_model"])),
        "g4f_model": os.getenv("AUTOHHKEK_G4F_MODEL", DEFAULT_RUNTIME_SETTINGS["g4f_model"]).strip() or DEFAULT_RUNTIME_SETTINGS["g4f_model"],
        "g4f_provider": os.getenv("AUTOHHKEK_G4F_PROVIDER", DEFAULT_RUNTIME_SETTINGS["g4f_provider"]).strip(),
    }


def normalize_runtime_settings(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    env_defaults = _runtime_defaults_from_env()
    data = dict(env_defaults)
    incoming = dict(payload or {})
    legacy_openrouter_default = "openai/gpt-4o-mini"
    if payload:
        data.update(payload)
    if "agent_mode" in incoming and "dashboard_mode" not in incoming:
        data["dashboard_mode"] = incoming["agent_mode"]
    data["dashboard_mode"] = LEGACY_DASHBOARD_MODE_ALIASES.get(data["dashboard_mode"], data["dashboard_mode"])
    if data["llm_backend"] not in AVAILABLE_LLM_BACKENDS:
        data["llm_backend"] = DEFAULT_RUNTIME_SETTINGS["llm_backend"]
    if data["dashboard_mode"] not in AVAILABLE_DASHBOARD_MODES:
        data["dashboard_mode"] = DEFAULT_RUNTIME_SETTINGS["dashboard_mode"]
    data["mode_selected"] = bool(data.get("mode_selected", False))
    data["auto_run_repair_worker"] = bool(data.get("auto_run_repair_worker", False))
    for field in ("openai_model", "openrouter_model", "g4f_model"):
        value = str(data.get(field) or "").strip()
        if not value or value == DEFAULT_RUNTIME_SETTINGS[field] or (field == "openrouter_model" and value == legacy_openrouter_default):
            data[field] = str(env_defaults[field])
        else:
            data[field] = value
    data["openrouter_model"] = normalize_openrouter_model(str(data.get("openrouter_model") or env_defaults["openrouter_model"]))
    data["g4f_provider"] = str(data.get("g4f_provider") or env_defaults["g4f_provider"] or "")
    return data
