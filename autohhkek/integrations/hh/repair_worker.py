from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from autohhkek.domain.models import RuntimeSettings
from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPBridge, PlaywrightMCPConfig
from autohhkek.services.g4f_runtime import G4FAppConfig
from autohhkek.services.openai_runtime import OpenAIAppConfig
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig
from autohhkek.services.paths import WorkspacePaths


RunnerFn = Callable[[Any, str, Any], Any]
G4FRunnerFn = Callable[[list[dict[str, str]], str, Any], Any]


class PlaywrightRepairOutput(BaseModel):
    diagnosis: str
    patch_summary: str
    patch_text: str
    test_text: str
    selector_notes: list[str] = Field(default_factory=list)


class PlaywrightRepairWorker:
    def __init__(
        self,
        project_root: Path,
        runtime_settings: RuntimeSettings | None = None,
        openai_config: OpenAIAppConfig | None = None,
        openrouter_config: OpenRouterAppConfig | None = None,
        g4f_config: G4FAppConfig | None = None,
        bridge: PlaywrightMCPBridge | None = None,
        runner: RunnerFn | None = None,
        g4f_runner: G4FRunnerFn | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.paths = WorkspacePaths(self.project_root)
        self.paths.ensure()
        self.runtime_settings = runtime_settings or RuntimeSettings()
        self.openai_config = openai_config or OpenAIAppConfig.from_env()
        self.openrouter_config = openrouter_config or OpenRouterAppConfig.from_env()
        self.g4f_config = g4f_config or G4FAppConfig.from_env()
        self.bridge = bridge or PlaywrightMCPBridge(PlaywrightMCPConfig.from_env())
        self.runner = runner or self._run_sync
        self.g4f_runner = g4f_runner or self._run_g4f

    def prepare_task(self, action: str, payload: dict[str, Any], error: str = "") -> dict[str, Any]:
        reason = error or "missing_script"
        repair_dir = self.paths.artifacts_dir / "repairs"
        repair_dir.mkdir(parents=True, exist_ok=True)
        patch_path = repair_dir / f"{action}_patch.diff"
        test_path = repair_dir / f"test_{action}_repair.py"
        prompt = self.bridge.build_repair_prompt(action, payload, error=reason)
        return {
            "action": action,
            "payload": payload,
            "error": reason,
            "prompt": prompt,
            "repair_patch_path": str(patch_path),
            "repair_test_path": str(test_path),
            "mcp_ready": self.bridge.is_available(),
            "mcp_stdio": self.bridge.to_stdio_params() if self.bridge.is_available() else {},
            "openai_ready": self.openai_config.is_available(),
            "openrouter_ready": self.openrouter_config.is_available(),
            "g4f_ready": self.g4f_config.is_available(),
            "selected_llm_backend": self.runtime_settings.llm_backend,
        }

    def run(self, action: str, payload: dict[str, Any], error: str = "") -> dict[str, Any]:
        task = self.prepare_task(action, payload, error)
        requested_backend = self.runtime_settings.llm_backend
        backend = self._resolve_backend(task)
        task["requested_llm_backend"] = requested_backend
        task["selected_llm_backend"] = backend
        llm_ready = {
            "g4f": task["g4f_ready"],
            "openrouter": task["openrouter_ready"],
        }.get(backend, task["openai_ready"])
        if backend != "g4f" and (not llm_ready or not task["mcp_ready"]):
            task["status"] = "unavailable"
            task["worker_error"] = "No available LLM backend with MCP repair support."
            return task
        if backend == "g4f" and not llm_ready:
            task["status"] = "unavailable"
            task["worker_error"] = "No available g4f backend for repair."
            return task

        try:
            if backend == "g4f":
                result = self.g4f_runner(self._build_g4f_messages(task), self.g4f_config.model, PlaywrightRepairOutput)
            else:
                config = self.openrouter_config if backend == "openrouter" else self.openai_config
                result = self.runner(
                    self._build_agent(config),
                    self._build_prompt(task),
                    run_config=config.build_run_config(workflow_name="AutoHHKek MCP repair worker"),
                )
        except Exception as exc:  # noqa: BLE001
            task["status"] = "error"
            task["worker_error"] = str(exc)
            return task

        if backend == "g4f":
            output = result if isinstance(result, PlaywrightRepairOutput) else PlaywrightRepairOutput.model_validate(result)
        else:
            output = getattr(result, "final_output", None)
            if output is None:
                task["status"] = "error"
                task["worker_error"] = "missing final_output"
                return task
            if not isinstance(output, PlaywrightRepairOutput):
                output = PlaywrightRepairOutput.model_validate(output)

        task["status"] = "ready"
        if backend == "g4f":
            task["repair_mode"] = "plan_only"
        else:
            task["repair_mode"] = "live_mcp"
        task["output"] = output.model_dump()
        return task

    def _resolve_backend(self, task: dict[str, Any]) -> str:
        requested = self.runtime_settings.llm_backend
        if requested != "g4f" and not bool(task.get("mcp_ready")):
            return requested
        priorities = {
            "openai": ["openai", "openrouter", "g4f"],
            "openrouter": ["openrouter", "openai", "g4f"],
            "g4f": ["g4f", "openrouter", "openai"],
        }
        ready = {
            "openai": bool(task.get("openai_ready")),
            "openrouter": bool(task.get("openrouter_ready")),
            "g4f": bool(task.get("g4f_ready")),
        }
        for backend in priorities.get(requested, [requested, "openrouter", "openai", "g4f"]):
            if ready.get(backend):
                return backend
        return requested

    def _build_agent(self, config):
        from agents import Agent

        return Agent(
            name="AutoHHKek Playwright MCP Repair Worker",
            model=config.model,
            instructions=(
                "You repair a broken hh.ru DOM automation step. "
                "Use Playwright MCP to inspect the page and return a reusable patch diff and a regression test. "
                "Do not suggest manual clicking when a deterministic script can be restored."
            ),
            output_type=PlaywrightRepairOutput,
            mcp_servers=[self.bridge.to_mcp_server()],
        )

    def _build_prompt(self, task: dict[str, Any]) -> str:
        return (
            "Repair this hh.ru automation step and produce a patch diff plus a regression test.\n"
            "The patch must preserve the script-first runtime.\n"
            f"{json.dumps(task, ensure_ascii=False, indent=2)}"
        )

    def _build_g4f_messages(self, task: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Return JSON only with diagnosis, patch_summary, patch_text, test_text, and selector_notes. "
                    "Repair an hh.ru script-first automation step."
                ),
            },
            {"role": "user", "content": self._build_prompt(task)},
        ]

    def _run_sync(self, agent, prompt: str, run_config=None):
        from agents import Runner

        async def _run():
            async with AsyncExitStack() as stack:
                for server in getattr(agent, "mcp_servers", []) or []:
                    await stack.enter_async_context(server)
                return await Runner.run(agent, prompt, run_config=run_config)

        return asyncio.run(_run())

    def _run_g4f(self, messages: list[dict[str, str]], model: str, response_schema=None) -> PlaywrightRepairOutput:
        from g4f.client import Client

        client = Client()
        completion = client.chat.completions.create(
            model=model,
            provider=self.g4f_config.provider or None,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        return PlaywrightRepairOutput.model_validate_json(content)
