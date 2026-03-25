from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPBridge, PlaywrightMCPConfig


Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class AutomationResult:
    action: str
    success: bool
    strategy: str
    details: dict[str, Any]
    error: str = ""
    fallback: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "success": self.success,
            "strategy": self.strategy,
            "details": self.details,
            "error": self.error,
            "fallback": self.fallback,
        }


def build_agent_fallback(action: str, payload: dict[str, Any], error: str = "") -> dict[str, Any]:
    bridge = PlaywrightMCPBridge(PlaywrightMCPConfig.from_env())
    reason = error or "missing_script"
    return {
        "backend": "playwright_mcp",
        "reason": reason,
        "payload": payload,
        "mcp_ready": bridge.is_available(),
        "mcp_stdio": bridge.to_stdio_params() if bridge.is_available() else {},
        "prompt": bridge.build_repair_prompt(action, payload, error=reason),
    }


class HHScriptRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, action: str, handler: Handler) -> None:
        self._handlers[action] = handler

    def execute(self, action: str, payload: dict[str, Any]) -> AutomationResult:
        handler = self._handlers.get(action)
        if handler is None:
            return AutomationResult(
                action=action,
                success=False,
                strategy="agent_fallback",
                details={},
                error="missing_script",
                fallback=build_agent_fallback(action, payload),
            )
        try:
            details = handler(payload)
            return AutomationResult(
                action=action,
                success=True,
                strategy="script",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            return AutomationResult(
                action=action,
                success=False,
                strategy="agent_fallback",
                details={},
                error=str(exc),
                fallback=build_agent_fallback(action, payload, error=str(exc)),
            )

    def available_actions(self) -> list[str]:
        return sorted(self._handlers)


def _require(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"missing required field: {key}")
    return value


def build_default_script_registry() -> HHScriptRegistry:
    registry = HHScriptRegistry()

    registry.register(
        "open_search_page",
        lambda payload: {
            "url": "https://hh.ru/search/vacancy",
            "method": "goto",
        },
    )
    registry.register(
        "set_search_text",
        lambda payload: {
            "selector": "input[data-qa='search-input']",
            "value": _require(payload, "query"),
            "method": "fill",
        },
    )
    registry.register(
        "set_salary_min",
        lambda payload: {
            "selector": "input[data-qa='advanced-search-salary']",
            "value": _require(payload, "salary_min"),
            "method": "fill",
        },
    )
    registry.register(
        "set_remote_filter",
        lambda payload: {
            "selector": "[data-qa='advanced-search__remote-work']",
            "value": bool(payload.get("enabled", True)),
            "method": "check",
        },
    )
    registry.register(
        "set_area_filter",
        lambda payload: {
            "selector": "[data-qa='advanced-search-region-switcher']",
            "value": _require(payload, "area_code"),
            "method": "select",
        },
    )
    registry.register(
        "click_apply_button",
        lambda payload: {
            "selector_candidates": [
                "a[data-qa='vacancy-response-link-top']",
                "button[data-qa='vacancy-response-button']",
                "button:has-text('Откликнуться')",
            ],
            "method": "click",
            "vacancy_id": payload.get("vacancy_id", ""),
        },
    )
    registry.register(
        "choose_resume",
        lambda payload: {
            "selector": "[data-qa='resume-selector']",
            "resume_id": _require(payload, "resume_id"),
            "method": "select",
        },
    )
    return registry
