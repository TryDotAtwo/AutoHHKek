from __future__ import annotations

import re
from urllib.parse import urlencode
from autohhkek.agents.g4f_filter_agent import G4FHHFilterAgent
from autohhkek.agents.openai_filter_agent import OpenAIHHFilterAgent
from autohhkek.agents.openrouter_filter_agent import OpenRouterHHFilterAgent
from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.integrations.hh.script_engine import build_default_script_registry
HH_AREA_CODES = {
    "\u043c\u043e\u0441\u043a\u0432\u0430": "1",
    "\u0441\u0430\u043d\u043a\u0442-\u043f\u0435\u0442\u0435\u0440\u0431\u0443\u0440\u0433": "2",
}

REMOTE_MARKERS = ("удален", "удалён", "remote", "home office", "work from home")


def _normalize_phrase(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip(" ,;/")
    return cleaned


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = _normalize_phrase(item)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


class HHFilterPlanner:
    def __init__(
        self,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
        selected_resume_id: str = "",
        llm_backend: str = "openrouter",
        llm_planner: OpenAIHHFilterAgent | None = None,
    ) -> None:
        self.preferences = preferences
        self.anamnesis = anamnesis
        self.selected_resume_id = str(selected_resume_id or "").strip()
        self.registry = build_default_script_registry()
        self.llm_backend = llm_backend
        self.llm_planner = llm_planner or self._build_planner(llm_backend)

    def build(self) -> dict:
        llm_plan = self.llm_planner.plan(self.preferences, self.anamnesis)
        search_text = self._build_search_text()
        salary_min = self.preferences.salary_min
        remote_only = self._prefer_hh_remote_filter()
        area_code = self._pick_area_code()
        residual_rules = self._build_residual_rules()
        planning_notes: list[str] = []
        planner_backend = "rules"
        strategy = "script_first_with_agent_fallback"
        resume_first_search = self._prefer_resume_only_search()

        if llm_plan is not None:
            planner_backend = {
                "g4f": "g4f_agent",
                "openrouter": "openrouter_agent",
            }.get(self.llm_backend, "openai_agent")
            strategy = {
                "g4f": "script_first_with_g4f_planning",
                "openrouter": "script_first_with_openrouter_planning",
            }.get(self.llm_backend, "script_first_with_openai_planning")
            if not resume_first_search and llm_plan.search_text.strip():
                search_text = llm_plan.search_text.strip()
            if salary_min is None and llm_plan.salary_min is not None:
                salary_min = llm_plan.salary_min
            if not remote_only and llm_plan.remote_only:
                residual_rules.append("Planner prefers remote vacancies, but hh search stays broad and applies this during scoring.")
            if not area_code and llm_plan.area_code:
                residual_rules.append(f"Planner suggested area code {llm_plan.area_code}, but hh search stays broad unless location is a hard constraint.")
            for rule in llm_plan.residual_rules:
                if rule and rule not in residual_rules:
                    residual_rules.append(rule)
            if llm_plan.rationale:
                planning_notes.append(llm_plan.rationale)
            if resume_first_search and llm_plan.search_text.strip():
                residual_rules.append(f"Search hint from planner: {llm_plan.search_text.strip()}")

        if resume_first_search:
            search_text = ""
            planning_notes.append("Resume-first search enabled: hh resume drives the broad candidate pool; text narrowing stays in residual rules and local scoring.")

        query_params: dict[str, object] = {}
        ui_actions = [self.registry.execute("open_search_page", {}).to_dict()]
        if search_text:
            query_params["text"] = search_text
            ui_actions.append(self.registry.execute("set_search_text", {"query": search_text}).to_dict())

        if salary_min:
            residual_rules.append(f"Salary preference: от {salary_min}. Не режем hh-поиск этим фильтром, применяем как мягкий критерий.")

        if remote_only:
            query_params["remote_work"] = "1"
            ui_actions.append(self.registry.execute("set_remote_filter", {"enabled": True}).to_dict())

        if area_code:
            query_params["area"] = area_code
            ui_actions.append(self.registry.execute("set_area_filter", {"area_code": area_code}).to_dict())

        search_rounds = self._build_search_rounds(query_params, resume_first_search, llm_plan)
        planning_notes.append(
            f"Search rounds: {len(search_rounds)} (broad primary, then keyword follow-ups merged and deduped)."
        )

        return {
            "search_text": search_text,
            "query_params": query_params,
            "search_url": self._build_search_url(query_params),
            "resume_id": self.selected_resume_id,
            "ui_actions": ui_actions,
            "residual_rules": residual_rules,
            "strategy": strategy,
            "planner_backend": planner_backend,
            "planning_notes": planning_notes,
            "llm_planner_status": getattr(self.llm_planner, "last_status", "unknown"),
            "llm_planner_error": getattr(self.llm_planner, "last_error", ""),
            "llm_filter_intent": llm_plan.model_dump() if llm_plan is not None else None,
            "search_rounds": search_rounds,
        }

    def _build_search_text(self) -> str:
        titles = self._target_titles_for_search()
        skills = self._skill_terms_for_search()
        parts: list[str] = []
        if titles:
            parts.append(" OR ".join([f'"{item}"' if " " in item else item for item in titles[:3]]))
        if skills:
            parts.extend(skills[:3])
        return " ".join(parts).strip()

    def _prefer_resume_only_search(self) -> bool:
        # If a resume is selected, hh already has strong semantic context.
        # Keep the query broad and apply strict constraints in residual scoring.
        return bool(self.selected_resume_id)

    def _target_titles_for_search(self) -> list[str]:
        source = [item for item in self.preferences.target_titles if item.strip()]
        if not source and self.anamnesis.headline:
            source = [self.anamnesis.headline]
        candidates: list[str] = []
        for item in source:
            parts = re.split(r"[/|;,]+", item)
            for part in parts:
                candidate = _normalize_phrase(part)
                if not candidate or len(candidate) < 3:
                    continue
                candidates.append(candidate)
        return _dedupe_preserve_order(candidates)

    def _skill_terms_for_search(self) -> list[str]:
        source = list(self.preferences.required_skills) + list(self.preferences.preferred_skills) + list(self.anamnesis.primary_skills)
        return _dedupe_preserve_order([item for item in source if _normalize_phrase(item)])

    def _prefer_hh_remote_filter(self) -> bool:
        return bool(self.preferences.remote_only)

    def _build_residual_rules(self) -> list[str]:
        residual_rules: list[str] = []
        for term in self.preferences.excluded_companies:
            residual_rules.append(f"Exclude employers matching: {term}")
        for term in self.preferences.forbidden_keywords + self.preferences.excluded_keywords:
            residual_rules.append(f"Reject vacancies containing: {term}")
        if self.preferences.notes:
            residual_rules.append(f"User note: {self.preferences.notes}")
        if self._infer_remote_only() and not self.preferences.remote_only:
            residual_rules.append("Remote/удалёнка указаны в предпочтениях пользователя, даже если флаг remote_only ещё не был сохранён.")
        return residual_rules

    def _pick_area_code(self) -> str:
        if self.selected_resume_id:
            return ""
        for location in self.preferences.preferred_locations:
            code = HH_AREA_CODES.get(location.strip().lower())
            if code:
                return code
        return ""

    def _infer_remote_only(self) -> bool:
        if self.preferences.remote_only:
            return True
        haystack = " ".join([*self.preferences.preferred_locations, self.preferences.notes]).casefold()
        return any(marker in haystack for marker in REMOTE_MARKERS)

    def _build_search_url(self, query_params: dict[str, object]) -> str:
        params: list[tuple[str, str]] = [
            ("from", "resumelist"),
            ("search_field", "name"),
            ("search_field", "company_name"),
            ("search_field", "description"),
            ("enable_snippets", "true"),
            ("forceFiltersSaving", "true"),
        ]
        if self.selected_resume_id:
            params.append(("resume", self.selected_resume_id))
        text = str(query_params.get("text") or "").strip()
        if text:
            params.append(("text", text))
        salary_from = query_params.get("salary_from")
        if salary_from not in ("", None):
            params.append(("salary_from", str(salary_from)))
        area = str(query_params.get("area") or "").strip()
        if area:
            params.append(("area", area))
        if str(query_params.get("remote_work") or "") == "1":
            params.append(("work_format", "REMOTE"))
        return f"https://hh.ru/search/vacancy?{urlencode(params, doseq=True)}"

    def _build_planner(self, llm_backend: str):
        if llm_backend == "g4f":
            return G4FHHFilterAgent()
        if llm_backend == "openrouter":
            return OpenRouterHHFilterAgent()
        return OpenAIHHFilterAgent()

    def _heuristic_follow_up_texts(self) -> list[str]:
        skills = self._skill_terms_for_search()[:4]
        titles = self._target_titles_for_search()[:2]
        merged: list[str] = []
        for item in skills + titles:
            normalized = _normalize_phrase(item)
            if len(normalized) < 2:
                continue
            if normalized not in merged:
                merged.append(normalized)
        combo = " ".join(skills[:2]) if len(skills) >= 2 else ""
        if combo and combo not in merged:
            merged.insert(0, combo)
        return merged[:5]

    def _build_search_rounds(
        self,
        base_query_params: dict[str, object],
        resume_first_search: bool,
        llm_plan,
    ) -> list[dict[str, object]]:
        primary = dict(base_query_params)
        rounds: list[dict[str, object]] = [
            {
                "id": "primary_broad",
                "query_params": primary,
                "initial_max_pages": 100,
                "persist_serp_cache": True,
                "max_pages_cap": None,
            }
        ]
        follow: list[str] = []
        if llm_plan is not None:
            follow = [str(t).strip() for t in (llm_plan.follow_up_search_texts or []) if str(t).strip()]
        if not follow and resume_first_search:
            follow = self._heuristic_follow_up_texts()

        seen: set[str] = {str(primary.get("text") or "").strip().casefold()}
        for i, raw in enumerate(follow[:6]):
            text = _normalize_phrase(raw)
            if len(text) < 2:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            qp = dict(base_query_params)
            qp["text"] = text
            rounds.append(
                {
                    "id": f"followup_kw_{i + 1}",
                    "query_params": qp,
                    "initial_max_pages": 6,
                    "persist_serp_cache": False,
                    "max_pages_cap": 6,
                }
            )
        return rounds
