from autohhkek.domain.models import RuntimeSettings
from autohhkek.services.llm_runtime import LLMRuntime


def test_llm_runtime_prefers_openrouter_when_openai_unavailable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    runtime = LLMRuntime(RuntimeSettings(llm_backend="openai", openrouter_model="openai/gpt-4o-mini"))

    assert runtime.selected_backend == "openai"
    assert runtime.effective_backend() == "openrouter"
    assert runtime.capabilities()["fallback_applied"] is True
