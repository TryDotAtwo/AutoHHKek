from __future__ import annotations

import sys

from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.rules import build_selection_rules_markdown, needs_intake
from autohhkek.services.seed import bootstrap_from_legacy_resume
from autohhkek.services.storage import WorkspaceStore


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


class IntakeAgent:
    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def ensure(self, interactive: bool = True) -> tuple[UserPreferences, Anamnesis]:
        preferences = self.store.load_preferences()
        anamnesis = self.store.load_anamnesis()
        if not needs_intake(preferences, anamnesis):
            return preferences, anamnesis

        legacy_resume_path = self.store.project_root / "resume_cache.json"
        bootstrapped = bootstrap_from_legacy_resume(self.store, legacy_resume_path)
        if bootstrapped:
            preferences, anamnesis = bootstrapped
            if not interactive:
                self.store.save_selection_rules(build_selection_rules_markdown(preferences, anamnesis))
                return preferences, anamnesis

        preferences = self.store.load_preferences() or UserPreferences()
        anamnesis = self.store.load_anamnesis() or Anamnesis()

        if interactive and sys.stdin.isatty():
            preferences, anamnesis = self._collect_interactively(preferences, anamnesis)

        rules = build_selection_rules_markdown(preferences, anamnesis)
        self.store.save_preferences(preferences)
        self.store.save_anamnesis(anamnesis)
        self.store.save_selection_rules(rules)
        self.store.record_event("intake", "Интейк пользователя сохранён.")
        return preferences, anamnesis

    def _ask(self, label: str, default: str = "") -> str:
        prompt = f"{label}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        answer = input(prompt).strip()
        return answer or default

    def _collect_interactively(self, preferences: UserPreferences, anamnesis: Anamnesis) -> tuple[UserPreferences, Anamnesis]:
        print("Запуск intake. Ответы сохранятся в долговременную память проекта.")
        preferences.full_name = self._ask("Имя и фамилия", preferences.full_name)
        anamnesis.headline = self._ask("Целевая роль / headline", anamnesis.headline or "ML / LLM Engineer")
        anamnesis.summary = self._ask("Короткое описание опыта", anamnesis.summary[:240] if anamnesis.summary else "")
        experience_raw = self._ask("Сколько лет коммерческого опыта", str(anamnesis.experience_years or 0)).replace(",", ".")
        try:
            anamnesis.experience_years = float(experience_raw)
        except ValueError:
            anamnesis.experience_years = anamnesis.experience_years or 0.0
        preferences.target_titles = _split_csv(self._ask("Целевые названия вакансий через запятую", ", ".join(preferences.target_titles or [anamnesis.headline])))
        anamnesis.primary_skills = _split_csv(self._ask("Ключевые навыки через запятую", ", ".join(anamnesis.primary_skills)))
        preferences.required_skills = _split_csv(self._ask("Жёсткие must-have навыки", ", ".join(preferences.required_skills)))
        preferences.preferred_skills = _split_csv(self._ask("Желательные навыки", ", ".join(preferences.preferred_skills or anamnesis.primary_skills)))
        preferences.preferred_locations = _split_csv(self._ask("Предпочтительные локации", ", ".join(preferences.preferred_locations or ["Москва"])))
        preferences.excluded_companies = _split_csv(self._ask("Какие компании или типы организаций исключать", ", ".join(preferences.excluded_companies)))
        preferences.forbidden_keywords = _split_csv(self._ask("Запрещённые ключевые слова", ", ".join(preferences.forbidden_keywords)))
        salary_raw = self._ask("Минимальная зарплата в RUB", str(preferences.salary_min or ""))
        preferences.salary_min = int(salary_raw) if salary_raw.isdigit() else preferences.salary_min
        preferences.remote_only = self._ask("Только удалёнка? yes/no", "yes" if preferences.remote_only else "no").lower() in {"yes", "y", "да"}
        preferences.allow_relocation = self._ask("Разрешить релокацию? yes/no", "yes" if preferences.allow_relocation else "no").lower() in {"yes", "y", "да"}
        preferences.cover_letter_mode = self._ask("Сопроводительные письма: adaptive / never / always", preferences.cover_letter_mode)
        preferences.notes = self._ask("Дополнительные правила отбора", preferences.notes)
        anamnesis.achievements = _split_csv(self._ask("Главные достижения через запятую", ", ".join(anamnesis.achievements)))
        anamnesis.languages = _split_csv(self._ask("Языки через запятую", ", ".join(anamnesis.languages)))
        anamnesis.links = _split_csv(self._ask("Полезные ссылки через запятую", ", ".join(anamnesis.links)))
        return preferences, anamnesis
