from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import BaseModel, Field

from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.openai_runtime import OpenAIAppConfig


class FilterPlanningOutput(BaseModel):
    search_text: str = ""
    area_code: str = ""
    remote_only: bool = False
    salary_min: int | None = None
    residual_rules: list[str] = Field(default_factory=list)
    rationale: str = ""


RunnerFn = Callable[[Any, str, Any], Any]


class OpenAIHHFilterAgent:
    def __init__(self, config: OpenAIAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        self.config = config or OpenAIAppConfig.from_env()
        self.runner = runner or self._run_sync
        self.last_status = "idle"
        self.last_error = ""

    def plan(self, preferences: UserPreferences, anamnesis: Anamnesis) -> FilterPlanningOutput | None:
        if not self.config.is_available():
            self.last_status = "unavailable"
            self.last_error = ""
            return None

        try:
            result = self.runner(
                self._build_agent(),
                self._build_prompt(preferences, anamnesis),
                run_config=self.config.build_run_config(workflow_name="AutoHHKek filter planning"),
            )
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = str(exc)
            return None

        output = getattr(result, "final_output", None)
        if output is None:
            self.last_status = "empty"
            self.last_error = "missing final_output"
            return None
        if not isinstance(output, FilterPlanningOutput):
            try:
                output = FilterPlanningOutput.model_validate(output)
            except Exception as exc:  # noqa: BLE001
                self.last_status = "error"
                self.last_error = str(exc)
                return None
        self.last_status = "ok"
        self.last_error = ""
        return output

    def _build_agent(self):
        from agents import Agent

        return Agent(
            name="AutoHHKek HH Filter Planner",
            model=self.config.model,
            instructions=(
                "You convert the user's hiring preferences into hh.ru search intent. "
                "Prefer deterministic, reusable filters. Only propose area_code values when you are confident "
                "they map to hh.ru regions, and keep unsupported nuance in residual_rules."
            ),
            output_type=FilterPlanningOutput,
        )

    def _build_prompt(self, preferences: UserPreferences, anamnesis: Anamnesis) -> str:
        payload = {
            "preferences": preferences.to_dict(),
            "anamnesis": anamnesis.to_dict(),
        }
        return (
            "Plan hh.ru filters from this user profile.\n"
            "Return search_text, area_code, remote_only, salary_min, residual_rules, and rationale.\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _run_sync(self, agent, prompt: str, run_config=None):
        from agents import Runner

        return Runner.run_sync(agent, prompt, run_config=run_config)
