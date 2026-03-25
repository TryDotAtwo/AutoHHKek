from __future__ import annotations

import json
from typing import Any, Callable

from autohhkek.agents.openai_filter_agent import FilterPlanningOutput
from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.g4f_runtime import G4FAppConfig


RunnerFn = Callable[[list[dict[str, str]], G4FAppConfig], Any]


class G4FHHFilterAgent:
    def __init__(self, config: G4FAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        self.config = config or G4FAppConfig.from_env()
        self.runner = runner or self._run_completion
        self.last_status = "idle"
        self.last_error = ""

    def plan(self, preferences: UserPreferences, anamnesis: Anamnesis) -> FilterPlanningOutput | None:
        if not self.config.is_available():
            self.last_status = "unavailable"
            self.last_error = ""
            return None
        messages = self._build_messages(preferences, anamnesis)
        try:
            output = self.runner(messages, self.config)
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = str(exc)
            return None
        if not isinstance(output, FilterPlanningOutput):
            output = FilterPlanningOutput.model_validate(output)
        self.last_status = "ok"
        self.last_error = ""
        return output

    def _build_messages(self, preferences: UserPreferences, anamnesis: Anamnesis) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Convert user search intent into hh.ru filter JSON. "
                    "Return search_text, area_code, remote_only, salary_min, residual_rules, rationale."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "preferences": preferences.to_dict(),
                        "anamnesis": anamnesis.to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]

    def _run_completion(self, messages: list[dict[str, str]], config: G4FAppConfig) -> FilterPlanningOutput:
        from g4f.client import Client

        client = Client()
        completion = client.chat.completions.create(
            model=config.model,
            provider=config.provider or None,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        return FilterPlanningOutput.model_validate_json(content)
