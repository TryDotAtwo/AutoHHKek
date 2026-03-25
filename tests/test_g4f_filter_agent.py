from autohhkek.agents.g4f_filter_agent import G4FHHFilterAgent
from autohhkek.agents.openai_filter_agent import FilterPlanningOutput
from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.g4f_runtime import G4FAppConfig


def test_g4f_filter_agent_returns_structured_filter_intent():
    agent = G4FHHFilterAgent(
        config=G4FAppConfig(model="gpt-4o-mini"),
        runner=lambda messages, config: FilterPlanningOutput(
            search_text="LLM Engineer OR Applied Scientist",
            area_code="1",
            remote_only=True,
            salary_min=360000,
            residual_rules=["Avoid outsource agencies"],
            rationale="Prefer strong product companies",
        ),
    )

    result = agent.plan(UserPreferences(target_titles=["LLM Engineer"]), Anamnesis(primary_skills=["Python", "LLM"]))

    assert result is not None
    assert result.area_code == "1"
    assert result.remote_only is True
    assert result.salary_min == 360000
