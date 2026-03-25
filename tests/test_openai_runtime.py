from autohhkek.services.openai_runtime import OpenAIAppConfig


def test_openai_config_reads_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTOHHKEK_OPENAI_MODEL", "gpt-5.4")
    monkeypatch.setenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", "npx")
    monkeypatch.setenv("AUTOHHKEK_PLAYWRIGHT_MCP_ARGS", "-y @playwright/mcp@latest")

    config = OpenAIAppConfig.from_env()

    assert config.api_key == "sk-test"
    assert config.model == "gpt-5.4"
    assert config.playwright_mcp_command == "npx"
    assert config.playwright_mcp_args == ["-y", "@playwright/mcp@latest"]


def test_openai_config_reports_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config = OpenAIAppConfig.from_env()

    assert config.is_available() is False
