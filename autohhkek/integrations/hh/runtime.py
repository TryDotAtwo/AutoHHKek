from __future__ import annotations

from pathlib import Path
from typing import Any

from autohhkek.domain.enums import BrowserBackend
from autohhkek.domain.models import HHRuntimeConfig, ScreeningPlan, Vacancy
from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPBridge, PlaywrightMCPConfig
from autohhkek.integrations.hh.repair_worker import PlaywrightRepairWorker
from autohhkek.integrations.hh.script_engine import build_default_script_registry
from autohhkek.services.llm_runtime import LLMRuntime
from autohhkek.services.openai_runtime import OpenAIAppConfig
from autohhkek.services.g4f_runtime import G4FAppConfig
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig
from autohhkek.services.storage import WorkspaceStore

from .forms import build_screening_plan


class HHAutomationRuntime:
    def __init__(self, config: HHRuntimeConfig | None = None, project_root: Path | None = None) -> None:
        self.config = config or HHRuntimeConfig()
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
        self.store = WorkspaceStore(self.project_root)
        self.runtime_settings = self.store.load_runtime_settings()
        self.openai_config = OpenAIAppConfig.from_env()
        self.openai_config.model = self.runtime_settings.openai_model
        self.openrouter_config = OpenRouterAppConfig.from_env()
        self.openrouter_config.model = self.runtime_settings.openrouter_model
        self.g4f_config = G4FAppConfig.from_env()
        self.g4f_config.model = self.runtime_settings.g4f_model
        self.g4f_config.provider = self.runtime_settings.g4f_provider
        self.g4f_config.resolve_target()
        self.playwright_mcp = PlaywrightMCPBridge(PlaywrightMCPConfig.from_env())
        self.repair_worker = PlaywrightRepairWorker(
            project_root=self.project_root,
            runtime_settings=self.runtime_settings,
            openai_config=self.openai_config,
            openrouter_config=self.openrouter_config,
            g4f_config=self.g4f_config,
            bridge=self.playwright_mcp,
        )
        self.llm_runtime = LLMRuntime(self.runtime_settings)
        self.registry = build_default_script_registry()

    def describe_capabilities(self) -> dict[str, Any]:
        openai_ready = self.openai_config.is_available()
        openrouter_ready = self.openrouter_config.is_available()
        mcp_ready = self.playwright_mcp.is_available()
        llm_capabilities = self.llm_runtime.capabilities()
        g4f_capabilities = llm_capabilities["backends"]["g4f"]
        supports = [
            "vacancy-analysis",
            "resume-draft",
            "screening-plan",
            "cover-letter-plan",
            "dashboard-observability",
            "script-first-ui-automation",
            "repair-worker",
        ]
        if openai_ready:
            supports.extend(["openai-vacancy-review", "openai-filter-planning"])
        if openrouter_ready:
            supports.extend(["openrouter-vacancy-review", "openrouter-filter-planning"])
        if g4f_capabilities.get("ready"):
            supports.extend(["g4f-vacancy-review", "g4f-filter-planning"])
        if mcp_ready:
            supports.append("playwright-mcp-repair")
        return {
            "backend": self.config.backend.value,
            "headless": self.config.headless,
            "supports": supports,
            "script_actions": self.registry.available_actions(),
            "openai_available": openai_ready,
            "openai_ready": openai_ready,
            "openai_model": self.openai_config.model,
            "openrouter_ready": openrouter_ready,
            "openrouter_model": self.openrouter_config.model,
            "selected_llm_backend": llm_capabilities["selected_backend"],
            "selected_llm_backend_ready": llm_capabilities["selected_backend_ready"],
            "effective_backend": llm_capabilities["effective_backend"],
            "effective_backend_ready": llm_capabilities["effective_backend_ready"],
            "fallback_applied": llm_capabilities["fallback_applied"],
            "llm_backends": llm_capabilities["backends"],
            "playwright_mcp_ready": mcp_ready,
            "playwright_mcp_command": self.playwright_mcp.config.command,
            "playwright_mcp_source": "auto" if self.playwright_mcp.config.command and not self.store.project_root.joinpath(".env").exists() else "configured",
            "playwright_mcp": self.playwright_mcp.to_stdio_params() if mcp_ready else {},
        }

    def backend_status(self) -> str:
        openai_ready = self.openai_config.is_available()
        openrouter_ready = self.openrouter_config.is_available()
        mcp_ready = self.playwright_mcp.is_available()
        g4f_ready = self.llm_runtime.capabilities()["backends"]["g4f"]["ready"]

        if self.config.backend == BrowserBackend.PLAYWRIGHT_MCP:
            if mcp_ready:
                return "Playwright MCP backend selected and configured for browser repair tasks."
            return (
                "Playwright MCP backend selected, but локальный MCP-сервер не найден. "
                "Установите Node.js/npx или задайте AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND."
            )

        if self.llm_runtime.selected_backend == "g4f":
            if g4f_ready and mcp_ready:
                return "Script-first DOM automation with g4f review/planning and Playwright MCP repair tasks."
            if g4f_ready:
                return "Script-first DOM automation with g4f review/planning and без доступного Playwright MCP."
            return "Script-first DOM automation with g4f selected, but g4f is unavailable."
        if self.llm_runtime.selected_backend == "openrouter":
            if openrouter_ready and mcp_ready:
                return "Script-first DOM automation with OpenRouter review/planning and Playwright MCP repair fallback."
            if openrouter_ready:
                return "Script-first DOM automation with OpenRouter review/planning и без доступного Playwright MCP."
            return "Script-first DOM automation with OpenRouter selected, but OpenRouter is unavailable."
        if openai_ready and mcp_ready:
            return "Script-first DOM automation with OpenAI review/planning and Playwright MCP repair fallback."
        if openai_ready:
            return "Script-first DOM automation with OpenAI review/planning и без доступного Playwright MCP."
        if mcp_ready:
            return "Script-first DOM automation with deterministic rules and Playwright MCP repair fallback."
        return "Script-first DOM automation with deterministic rules and без доступного Playwright MCP."

    def build_apply_state_machine(self, vacancy: Vacancy, cover_letter_enabled: bool) -> list[dict[str, Any]]:
        screening_plan = build_screening_plan(vacancy)
        screening_needed = bool(screening_plan.questions or screening_plan.notes)
        return [
            {"stage": "analysis", "title": "Vacancy analysis", "status": "ready"},
            {"stage": "resume", "title": "Resume selection", "status": "ready"},
            {
                "stage": "screening",
                "title": "Questionnaire or test",
                "status": "ready" if screening_needed else "skip",
            },
            {
                "stage": "cover_letter",
                "title": "Cover letter",
                "status": "ready" if cover_letter_enabled else "skip",
            },
            {"stage": "submit", "title": "Submit application", "status": "pending"},
            {"stage": "post_apply", "title": "Post-apply hh chat or survey", "status": "pending"},
        ]

    def build_screening_plan(self, vacancy: Vacancy) -> ScreeningPlan:
        return build_screening_plan(vacancy)

    def plan_script_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.registry.execute(action, payload).to_dict()
        if result["strategy"] == "agent_fallback":
            if self.runtime_settings.auto_run_repair_worker:
                repair_task = self.run_repair(action, payload, result.get("error", "missing_script"))
            else:
                repair_task = self.build_repair_plan(action, payload, result.get("error", "missing_script"))
            self.store.save_repair_task(repair_task)
            result["repair_task"] = repair_task
        return result

    def build_repair_plan(self, action: str, payload: dict[str, Any], error: str = "") -> dict[str, Any]:
        return self.repair_worker.prepare_task(action, payload, error)

    def run_repair(self, action: str, payload: dict[str, Any], error: str = "") -> dict[str, Any]:
        return self.repair_worker.run(action, payload, error)
