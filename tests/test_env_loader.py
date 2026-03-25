from autohhkek.services.env_loader import load_project_dotenv


def test_load_project_dotenv_reads_repository_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=or-test\nAUTOHHKEK_OPENROUTER_MODEL=openai/gpt-4o-mini\n", encoding="utf-8")

    loaded = load_project_dotenv(tmp_path)

    assert loaded["OPENROUTER_API_KEY"] == "or-test"
    assert loaded["AUTOHHKEK_OPENROUTER_MODEL"] == "openai/gpt-4o-mini"
