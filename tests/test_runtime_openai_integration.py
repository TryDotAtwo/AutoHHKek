from autohhkek.agents.openai_filter_agent import FilterPlanningOutput
from autohhkek.agents.openai_review_agent import OpenAIVacancyReviewer, VacancyReviewOutput
from autohhkek.agents.vacancy_review_agent import VacancyReviewAgent
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.openai_runtime import OpenAIAppConfig


class _FilterPlannerStub:
    def plan(self, preferences, anamnesis):
        return FilterPlanningOutput(
            search_text="LLM Engineer OR Applied Scientist",
            area_code="1",
            remote_only=True,
            salary_min=350000,
            residual_rules=["Avoid consulting agencies"],
            rationale="Prefer Moscow senior roles",
        )


def test_filter_planner_uses_openai_overlay_when_available():
    plan = HHFilterPlanner(
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(primary_skills=["Python", "LLM"]),
        llm_planner=_FilterPlannerStub(),
    ).build()

    assert plan["planner_backend"] == "openai_agent"
    assert plan["strategy"] == "script_first_with_openai_planning"
    assert plan["search_text"] == "LLM Engineer OR Applied Scientist"
    assert plan["query_params"]["salary_from"] == 350000
    assert plan["query_params"]["remote_work"] == "1"


def test_runtime_reports_mcp_ready_with_local_npx_autodetect(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", raising=False)
    monkeypatch.delenv("AUTOHHKEK_PLAYWRIGHT_MCP_ARGS", raising=False)
    monkeypatch.setattr("autohhkek.integrations.hh.playwright_mcp.shutil.which", lambda command: "npx")

    capabilities = HHAutomationRuntime().describe_capabilities()

    assert capabilities["openai_ready"] is False
    assert capabilities["playwright_mcp_ready"] is True


class _OpenAIReviewerStub:
    def review(self, vacancy, preferences, anamnesis):
        return OpenAIVacancyReviewer(
            config=OpenAIAppConfig(api_key="sk-test", model="gpt-5.4"),
            runner=lambda agent, prompt, run_config=None: type(
                "Result",
                (),
                {
                    "final_output": VacancyReviewOutput(
                        category="fit",
                        subcategory="llm_match",
                        score=93,
                        explanation="Strong match",
                        review_notes="LLM review succeeded",
                    )
                },
            )(),
        ).review(vacancy, preferences, anamnesis)


def test_vacancy_review_agent_prefers_openai_when_available():
    assessment = VacancyReviewAgent(
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
        openai_reviewer=_OpenAIReviewerStub(),
    ).review(Vacancy(vacancy_id="vac-1", title="LLM Engineer"))

    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "openai_agent"
