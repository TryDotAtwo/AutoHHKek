from __future__ import annotations

from autohhkek.domain.enums import FitCategory
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.storage import WorkspaceStore

from .resume_agent import ResumeAgent


class ApplicationAgent:
    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store
        self.resume_agent = ResumeAgent(store)
        self.runtime = HHAutomationRuntime(project_root=store.project_root)

    def build_plan(self, vacancy_id: str | None = None) -> dict:
        vacancies = {item.vacancy_id: item for item in self.store.load_vacancies()}
        assessments = self.store.load_assessments()
        assessment_map = {item.vacancy_id: item for item in assessments}

        target = None
        if vacancy_id:
            target = vacancies.get(vacancy_id)
        if target is None:
            ranked = [item for item in assessments if item.category in {FitCategory.FIT, FitCategory.DOUBT}]
            ranked.sort(key=lambda item: item.score, reverse=True)
            if ranked:
                target = vacancies.get(ranked[0].vacancy_id)
        if target is None and assessments:
            fallback = sorted(assessments, key=lambda item: item.score, reverse=True)[0]
            target = vacancies.get(fallback.vacancy_id)
        if target is None:
            raise RuntimeError("No vacancy available to build apply plan.")

        assessment = assessment_map[target.vacancy_id]
        preferences = self.store.load_preferences()
        cover_letter = ""
        if preferences and preferences.cover_letter_mode != "never":
            cover_letter = self.resume_agent.build_cover_letter(target, assessment)
        stored_override = self.store.load_cover_letter_draft(target.vacancy_id)
        if stored_override.strip():
            cover_letter = stored_override

        payload = {
            "vacancy": target.to_dict(),
            "assessment": assessment.to_dict(),
            "runtime": self.runtime.describe_capabilities(),
            "backend_status": self.runtime.backend_status(),
            "filter_plan": self.store.load_filter_plan() or {},
            "screening_plan": self.runtime.build_screening_plan(target).to_dict(),
            "cover_letter_enabled": bool(cover_letter.strip()),
            "cover_letter_preview": cover_letter,
            "stages": self.runtime.build_apply_state_machine(target, bool(cover_letter.strip())),
            "script_actions": [
                self.runtime.plan_script_action("click_apply_button", {"vacancy_id": target.vacancy_id}),
            ],
        }
        self.store.save_apply_plan(payload)
        self.store.record_event("apply-plan", f"Built apply plan for {target.title}.")
        return payload
