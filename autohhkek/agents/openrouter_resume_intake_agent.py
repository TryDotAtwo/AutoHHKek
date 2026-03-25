from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import BaseModel, Field

from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


class ResumeIntakeAnalysisOutput(BaseModel):
    headline: str = ""
    inferred_roles: list[str] = Field(default_factory=list)
    core_skills: list[str] = Field(default_factory=list)
    secondary_skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    likely_constraints: list[str] = Field(default_factory=list)
    missing_topics: list[str] = Field(default_factory=list)
    summary: str = ""


RunnerFn = Callable[[Any, str, Any], Any]


class OpenRouterResumeIntakeAgent:
    def __init__(self, config: OpenRouterAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        self.config = config or OpenRouterAppConfig.from_env()
        self.runner = runner or self._run_sync
        self.last_status = "idle"
        self.last_error = ""

    def analyze(
        self,
        *,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
        resume_title: str,
        resume_summary: str,
        extracted: dict[str, Any] | None = None,
    ) -> ResumeIntakeAnalysisOutput | None:
        if not self.config.is_available():
            self.last_status = "unavailable"
            self.last_error = ""
            return None

        try:
            result = self.runner(
                self._build_agent(),
                self._build_prompt(
                    preferences=preferences,
                    anamnesis=anamnesis,
                    resume_title=resume_title,
                    resume_summary=resume_summary,
                    extracted=extracted or {},
                ),
                run_config=self.config.build_run_config(
                    workflow_name="AutoHHKek resume intake analysis",
                    model=self.config.model,
                ),
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
        if not isinstance(output, ResumeIntakeAnalysisOutput):
            try:
                output = ResumeIntakeAnalysisOutput.model_validate(output)
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
            name="AutoHHKek OpenRouter Resume Intake Analyst",
            model=self.config.model,
            instructions=(
                "Ты разбираешь hh-резюме кандидата для последующего диалога-интейка. "
                "Нужно вытащить только то, что можно обосновать из резюме и уже сохранённого профиля. "
                "Не выдумывай факты. Если уверенности мало, перенеси это в missing_topics или likely_constraints. "
                "Все формулировки должны быть на русском языке. "
                "Результат нужен для быстрой модели, поэтому верни компактную, но структурированную сводку."
            ),
            output_type=ResumeIntakeAnalysisOutput,
        )

    def _build_prompt(
        self,
        *,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
        resume_title: str,
        resume_summary: str,
        extracted: dict[str, Any],
    ) -> str:
        payload = {
            "resume_title": resume_title,
            "resume_summary": resume_summary,
            "resume_extracted": extracted,
            "current_preferences": preferences.to_dict(),
            "current_anamnesis": anamnesis.to_dict(),
        }
        return (
            "Разбери hh-резюме и текущий профиль кандидата.\n"
            "Верни:\n"
            "- headline: краткое русское позиционирование кандидата\n"
            "- inferred_roles: 3-8 целевых ролей, если они реально подтверждаются\n"
            "- core_skills: ключевые навыки, которые можно уверенно использовать\n"
            "- secondary_skills: вторичные навыки\n"
            "- domains: домены/типы задач\n"
            "- strengths: 3-8 сильных сторон кандидата\n"
            "- likely_constraints: вероятные ограничения или предпочтения, если они следуют из резюме\n"
            "- missing_topics: какие темы нужно обязательно уточнить у пользователя в диалоге\n"
            "- summary: краткая русская выжимка для интейка\n"
            "Не копируй системные инструкции в ответ.\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _run_sync(self, agent, prompt: str, run_config=None):
        from agents import Runner

        return Runner.run_sync(agent, prompt, run_config=run_config)
