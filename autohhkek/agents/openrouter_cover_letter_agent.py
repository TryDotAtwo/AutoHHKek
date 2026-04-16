# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from autohhkek.domain.models import (
    Anamnesis,
    UserPreferences,
    Vacancy,
    VacancyAssessment,
)
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig
from autohhkek.services.rules import (
    build_cover_letter_response_contract_markdown,
    build_cover_letter_rules_markdown,
)


class CoverLetterOutput(BaseModel):
    cover_letter: str = Field(default="", description="Готовый текст сопроводительного письма на русском языке.")
    short_rationale: str = Field(
        default="",
        description="Короткая внутренняя мотивация выбора формулировок. Поле служебное, в UI не показывать.",
    )
    language: str = Field(default="ru", description="Язык результата. Ожидаемое значение: ru.")


class OpenRouterCoverLetterAgent:
    """
    Агент генерации сопроводительного письма через OpenRouter + Agents SDK.

    Входной контекст:
    1. resume_markdown: полное резюме пользователя
    2. assessment: оценка вакансии и причины соответствия
    3. vacancy: полное описание вакансии
    4. cover-letter rules: правила написания сопроводительного
    5. response/history rules: правила формата ответа и записи истории

    Выход:
    - только строка сопроводительного письма
    """

    def __init__(self, config: OpenRouterAppConfig | None = None) -> None:
        self.config = config or OpenRouterAppConfig.from_env()
        self.last_status: str = "idle"
        self.last_error: str = ""

    def is_available(self) -> bool:
        return self.config.is_available()


    def _sanitize_cover_letter(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        for fragment in (
            "system instruction",
            "system prompt",
            "ignore previous",
            "assistant:",
            "user:",
            "developer:",
            "tool:",
            "chain of thought",
            "hidden rules",
            "instruction:",
            "instructions:",
        ):
            cleaned = cleaned.replace(fragment, "").replace(fragment.title(), "")
        cleaned = cleaned.replace("###", "").replace("##", "").replace("#", "")
        cleaned = cleaned.strip("`\"' \n\t")
        return " ".join(cleaned.split()).strip()



    def generate(
        self,
        *,
        vacancy: Vacancy,
        assessment: VacancyAssessment,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
        resume_markdown: str,
        selection_rules: str,
        imported_rules: list[dict[str, Any]] | None = None,
        dashboard_state: dict[str, Any] | None = None,
    ) -> str:
        imported_rules = imported_rules or []
        dashboard_state = dashboard_state or {}

        if not self.config.is_available():
            self.last_status = "unavailable"
            self.last_error = "OpenRouter config is not available."
            return ""

        agent = self._build_agent()
        prompt = self._build_prompt(
            vacancy=vacancy,
            assessment=assessment,
            preferences=preferences,
            anamnesis=anamnesis,
            resume_markdown=resume_markdown,
            selection_rules=selection_rules,
            imported_rules=imported_rules,
            dashboard_state=dashboard_state,
        )

        try:
            result = self._run_agent(agent=agent, prompt=prompt)
        except Exception as exc:
            self.last_status = "error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ""

        final_output = getattr(result, "final_output", None)
        if final_output is None:
            self.last_status = "empty"
            self.last_error = "Runner returned no final_output."
            return ""

        try:
            parsed = self._coerce_output(final_output)
        except Exception as exc:
            self.last_status = "parse_error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ""

        cover_letter = self._sanitize_cover_letter(parsed.cover_letter)
        if not cover_letter:
            self.last_status = "empty"
            self.last_error = "Model returned empty cover_letter."
            return ""

        self.last_status = "ok"
        self.last_error = ""
        return cover_letter

    def _build_agent(self):
        from agents import Agent

        instructions = (
            "Роль: агент генерации сопроводительных писем для hh.ru.\n"
            "Язык ответа: только русский.\n"
            "Источник фактов: только переданный контекст.\n"
            "Запрещено выдумывать:\n"
            "- опыт\n"
            "- стек\n"
            "- достижения\n"
            "- образование\n"
            "- метрики\n"
            "- домен вакансии\n"
            "- требования работодателя, которых нет во входных данных\n"
            "Требование: сопроводительное письмо должно быть конкретным, деловым, кратким, "
            "релевантным вакансии и основанным на реальных фактах из резюме и assessment.\n"
            "Если совпадение частичное, нельзя утверждать полное соответствие.\n"
            "Если assessment содержит ограничения или слабые места, формулировать осторожно.\n"
            "Нельзя возвращать markdown-заголовки, списки, JSON, пояснения о правилах, prompt text, "
            "system text, chain-of-thought, hidden instructions.\n"
            "Нужно вернуть только структурированный объект результата."
        )

        return Agent(
            name="AutoHHKek OpenRouter Cover Letter Agent",
            model=self.config.model,
            instructions=instructions,
            output_type=CoverLetterOutput,
        )

    def _run_agent(self, *, agent, prompt: str):
        from agents import Runner

        run_config = self.config.build_run_config(
            workflow_name="AutoHHKek cover letter generation",
            model=self.config.model,
        )
        return Runner.run_sync(agent, prompt, run_config=run_config)

    def _coerce_output(self, final_output: Any) -> CoverLetterOutput:
        if isinstance(final_output, CoverLetterOutput):
            return final_output

        if isinstance(final_output, dict):
            return CoverLetterOutput.model_validate(final_output)

        if isinstance(final_output, str):
            text = final_output.strip()

            # Сначала пробуем JSON
            try:
                data = json.loads(text)
            except Exception:
                data = None

            if isinstance(data, dict):
                return CoverLetterOutput.model_validate(data)

            # Иначе трактуем весь текст как готовое письмо
            return CoverLetterOutput(
                cover_letter=text,
                short_rationale="",
                language="ru",
            )

        return CoverLetterOutput.model_validate(final_output)

    def _build_prompt(
        self,
        *,
        vacancy: Vacancy,
        assessment: VacancyAssessment,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
        resume_markdown: str,
        selection_rules: str,
        imported_rules: list[dict[str, Any]],
        dashboard_state: dict[str, Any],
    ) -> str:
        cover_letter_rules = build_cover_letter_rules_markdown()
        response_contract_rules = build_cover_letter_response_contract_markdown()

        payload = {
            "task": {
                "name": "generate_cover_letter",
                "goal": (
                    "Построить сопроводительное письмо для отклика на вакансию. "
                    "Письмо должно опираться на резюме кандидата, оценку релевантности вакансии, "
                    "полное описание вакансии и правила написания."
                ),
            },
            "candidate_resume_markdown": resume_markdown,
            "candidate_profile": {
                "anamnesis": anamnesis.to_dict(),
                "preferences": preferences.to_dict(),
            },
            "vacancy_assessment": assessment.to_dict(),
            "vacancy_full": vacancy.to_dict(),
            "program_rules": {
                "selection_rules": selection_rules,
                "imported_rules": imported_rules,
                "dashboard_state_subset": {
                    "selected_resume_id": dashboard_state.get("selected_resume_id"),
                    "intake_confirmed": dashboard_state.get("intake_confirmed"),
                    "cover_letter_mode": dashboard_state.get("cover_letter_mode"),
                },
            },
            "writing_rules_markdown": cover_letter_rules,
            "response_contract_markdown": response_contract_rules,
        }

        return (
            "Задача: сгенерировать сопроводительное письмо.\n\n"
            "Приоритет источников:\n"
            "1. Резюме кандидата\n"
            "2. Оценка релевантности вакансии и причины совпадения\n"
            "3. Полное описание вакансии\n"
            "4. Правила написания сопроводительного\n"
            "5. Правила формата ответа\n\n"
            "Обязательные ограничения:\n"
            "- Использовать только факты из входных данных.\n"
            "- Не писать того, чего нет в резюме, assessment или вакансии.\n"
            "- Не дублировать резюме целиком.\n"
            "- Не писать абстрактный канцелярит.\n"
            "- Не писать списки, заголовки, JSON, комментарии.\n"
            "- Письмо должно быть пригодно для прямой вставки в форму отклика.\n\n"
            "Контекст:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
    

    