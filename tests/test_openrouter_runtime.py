from autohhkek.services.openrouter_runtime import OpenRouterAppConfig, normalize_openrouter_model


def test_openrouter_config_reads_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("AUTOHHKEK_OPENROUTER_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("AUTOHHKEK_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("AUTOHHKEK_OPENROUTER_REFERER", "https://example.com")
    monkeypatch.setenv("AUTOHHKEK_OPENROUTER_TITLE", "AutoHHKek")

    config = OpenRouterAppConfig.from_env()

    assert config.api_key == "or-test"
    assert config.model == "openai/gpt-4o-mini"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.site_url == "https://example.com"
    assert config.app_name == "AutoHHKek"


def test_openrouter_config_reports_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    config = OpenRouterAppConfig.from_env()

    assert config.is_available() is False


def test_openrouter_config_normalizes_model_aliases(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("AUTOHHKEK_OPENROUTER_MODEL", "gpt-5.4-nano")

    config = OpenRouterAppConfig.from_env()

    assert config.model == "openai/gpt-5-nano"
    assert normalize_openrouter_model("gpt-5.4") == "openai/gpt-5.4"
