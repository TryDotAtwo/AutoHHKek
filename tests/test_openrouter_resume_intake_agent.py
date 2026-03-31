from types import SimpleNamespace

from autohhkek.agents.openrouter_resume_intake_agent import OpenRouterResumeIntakeAgent
from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


def test_openrouter_resume_intake_agent_returns_structured_output():
    expected = {
        "headline": "LLM/NLP инженер с исследовательским бэкграундом",
        "inferred_roles": ["LLM Engineer", "NLP Engineer"],
        "core_skills": ["Python", "NLP", "LLM"],
        "secondary_skills": ["PyTorch"],
        "domains": ["AI infra", "Applied research"],
        "strengths": ["сильный исследовательский бэкграунд", "практика LLM/NLP"],
        "likely_constraints": ["предпочтение remote-формату"],
        "missing_topics": ["минимальная зарплата", "стоп-слова по работодателям"],
        "summary": "Подходит для LLM/NLP ролей и applied research.",
    }

    def runner(agent, prompt, run_config=None):
        return SimpleNamespace(final_output=expected)

    agent = OpenRouterResumeIntakeAgent(
        config=OpenRouterAppConfig(api_key="test-key", model="openai/gpt-5-nano"),
        runner=runner,
    )

    result = agent.analyze(
        preferences=UserPreferences(),
        anamnesis=Anamnesis(),
        resume_title="Theoretical physicist / LLM Engineer",
        resume_summary="Python, NLP, LLM, research",
        extracted={"skills": ["Python", "NLP", "LLM"]},
    )

    assert result is not None
    assert result.inferred_roles == ["LLM Engineer", "NLP Engineer"]
    assert result.core_skills == ["Python", "NLP", "LLM"]
    assert result.summary.startswith("Подходит")
