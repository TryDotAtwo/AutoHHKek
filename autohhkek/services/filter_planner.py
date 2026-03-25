from __future__ import annotations

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


class HHFilterPlanner:
    def __init__(
        self,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
        selected_resume_id: str = "",
        llm_backend: str = "openai",
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
        remote_only = self.preferences.remote_only
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
                remote_only = True
            if not area_code and llm_plan.area_code:
                area_code = llm_plan.area_code
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
            query_params["salary_from"] = salary_min
            ui_actions.append(self.registry.execute("set_salary_min", {"salary_min": salary_min}).to_dict())

        if remote_only:
            query_params["remote_work"] = "1"
            ui_actions.append(self.registry.execute("set_remote_filter", {"enabled": True}).to_dict())

        if area_code:
            query_params["area"] = area_code
            ui_actions.append(self.registry.execute("set_area_filter", {"area_code": area_code}).to_dict())

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
        }

    def _build_search_text(self) -> str:
        titles = [item.strip() for item in self.preferences.target_titles if item.strip()]
        if titles:
            return " OR ".join(titles)
        skills = [item.strip() for item in self.anamnesis.primary_skills if item.strip()]
        return " ".join(skills)

    def _prefer_resume_only_search(self) -> bool:
        if not self.selected_resume_id:
            return False
        explicit_titles = [item.strip() for item in self.preferences.target_titles if item.strip()]
        explicit_skills = [item.strip() for item in self.anamnesis.primary_skills if item.strip()]
        explicit_required = [item.strip() for item in self.preferences.required_skills if item.strip()]
        return not (explicit_titles or explicit_skills or explicit_required)

    def _build_residual_rules(self) -> list[str]:
        residual_rules: list[str] = []
        for term in self.preferences.excluded_companies:
            residual_rules.append(f"Exclude employers matching: {term}")
        for term in self.preferences.forbidden_keywords + self.preferences.excluded_keywords:
            residual_rules.append(f"Reject vacancies containing: {term}")
        if self.preferences.notes:
            residual_rules.append(f"User note: {self.preferences.notes}")
        return residual_rules

    def _pick_area_code(self) -> str:
        for location in self.preferences.preferred_locations:
            code = HH_AREA_CODES.get(location.strip().lower())
            if code:
                return code
        return ""

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
