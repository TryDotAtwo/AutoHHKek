from autohhkek.services.hh_preflight import ensure_hh_context
from autohhkek.services.storage import WorkspaceStore


def test_hh_preflight_requires_login_when_state_missing(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    monkeypatch.setattr(
        "autohhkek.services.hh_preflight.run_hh_login",
        lambda project_root: {"status": "timeout", "message": "login not completed"},
    )

    result = ensure_hh_context(store, auto_login=True)

    assert result["status"] == "needs_login"


def test_hh_preflight_requires_resume_selection_for_multiple_resumes(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    monkeypatch.setattr(
        "autohhkek.services.hh_preflight.HHResumeCatalog.refresh",
        lambda self: {
            "status": "completed",
            "items": [
                {"resume_id": "r1", "title": "LLM Engineer"},
                {"resume_id": "r2", "title": "Data Scientist"},
            ],
        },
    )

    result = ensure_hh_context(store, auto_login=False)

    assert result["status"] == "needs_resume_selection"


def test_hh_preflight_falls_back_to_cached_selected_resume_when_catalog_is_empty(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    store.save_hh_resumes([{"resume_id": "r1", "title": "LLM Engineer", "url": "https://hh.ru/resume/r1"}])
    store.save_selected_resume_id("r1")
    monkeypatch.setattr(
        "autohhkek.services.hh_preflight.HHResumeCatalog.refresh",
        lambda self: {"status": "empty", "items": [], "message": "empty catalog"},
    )

    result = ensure_hh_context(store, auto_login=False)

    assert result["status"] == "ready"
    assert result["selected_resume_id"] == "r1"
    assert result["catalog"]["items"][0]["resume_id"] == "r1"
