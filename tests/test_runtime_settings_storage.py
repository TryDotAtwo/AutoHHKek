from autohhkek.services.storage import WorkspaceStore
from autohhkek.services.runtime_settings import normalize_runtime_settings


def test_workspace_store_persists_runtime_settings_and_repair_tasks(tmp_path):
    store = WorkspaceStore(tmp_path)

    store.save_runtime_settings(
        {
            "llm_backend": "openrouter",
            "agent_mode": "plan_apply",
            "auto_run_repair_worker": True,
            "openrouter_model": "openai/gpt-5-nano",
        }
    )
    store.append_repair_task(
        {
            "action": "click_apply_button",
            "status": "prepared",
            "repair_patch_path": "patch.diff",
        }
    )

    settings = store.load_runtime_settings()
    tasks = store.load_repair_tasks()

    assert settings["llm_backend"] == "openrouter"
    assert settings["agent_mode"] == "plan_apply"
    assert settings["openrouter_model"] == "openai/gpt-5-nano"
    assert tasks[0]["action"] == "click_apply_button"


def test_runtime_settings_prefer_env_model_when_only_default_is_stored(monkeypatch):
    monkeypatch.setenv("AUTOHHKEK_OPENROUTER_MODEL", "gpt-5.4-nano")

    settings = normalize_runtime_settings({"llm_backend": "openrouter", "openrouter_model": "openai/gpt-4o-mini"})

    assert settings["openrouter_model"] == "openai/gpt-5-nano"


def test_workspace_store_persists_selected_resume_id(tmp_path):
    store = WorkspaceStore(tmp_path)

    store.save_selected_resume_id("resume-123")

    assert store.load_selected_resume_id() == "resume-123"
