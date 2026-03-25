from __future__ import annotations

from autohhkek.agents.openai_filter_agent import OpenAIHHFilterAgent, RunnerFn
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


class OpenRouterHHFilterAgent(OpenAIHHFilterAgent):
    def __init__(self, config: OpenRouterAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        super().__init__(config=config or OpenRouterAppConfig.from_env(), runner=runner)

    def _build_agent(self):
        agent = super()._build_agent()
        agent.name = "AutoHHKek OpenRouter HH Filter Planner"
        return agent
