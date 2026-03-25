from autohhkek.agents.openai_filter_agent import FilterPlanningOutput, OpenAIHHFilterAgent
from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.openai_runtime import OpenAIAppConfig


class _FakeResult:
    def __init__(self, output):
        self.final_output = output


def test_openai_filter_agent_returns_structured_filter_intent():
    agent = OpenAIHHFilterAgent(
        config=OpenAIAppConfig(api_key="sk-test", model="gpt-5.4"),
        runner=lambda agent, prompt, run_config=None: _FakeResult(
            FilterPlanningOutput(
                search_text="LLM Engineer OR Applied Scientist",
                area_code="1",
                remote_only=True,
                salary_min=350000,
                residual_rules=["Avoid academic employers"],
                rationale="Optimize for senior Moscow-based LLM roles",
            )
        ),
    )

    result = agent.plan(UserPreferences(target_titles=["LLM Engineer"]), Anamnesis(primary_skills=["Python", "LLM"]))

    assert result is not None
    assert result.search_text.startswith("LLM Engineer")
    assert result.area_code == "1"
    assert result.remote_only is True
