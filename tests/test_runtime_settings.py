from autohhkek.domain.models import RuntimeSettings
from autohhkek.services.storage import WorkspaceStore


def test_storage_roundtrip_runtime_settings(tmp_path):
    store = WorkspaceStore(tmp_path)
    settings = RuntimeSettings(
        llm_backend="openrouter",
        dashboard_mode="analyze",
        openrouter_model="openai/gpt-5-nano",
        g4f_model="gpt-4o-mini",
        mode_selected=True,
    )

    store.save_runtime_settings(settings)

    loaded = store.load_runtime_settings()
    assert loaded is not None
    assert loaded.llm_backend == "openrouter"
    assert loaded.dashboard_mode == "analyze"
    assert loaded.openrouter_model == "openai/gpt-5-nano"
    assert loaded.g4f_model == "gpt-4o-mini"
    assert loaded.mode_selected is True
