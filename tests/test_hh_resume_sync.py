from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.hh_resume_sync import HHResumeProfileSync
from autohhkek.services.storage import WorkspaceStore


def test_hh_resume_sync_retries_after_relogin(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    store.save_selected_resume_id("resume-1")
    store.save_preferences(UserPreferences(target_titles=["LLM Engineer"]))
    store.save_anamnesis(Anamnesis(headline="LLM Engineer"))

    calls = {"count": 0}

    async def fake_fetch(self, resume_id):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("login_required")
        return {
            "page_url": "https://hh.ru/resume/resume-1",
            "text": "LLM Engineer\nPython\nNLP",
            "html": "<h1>LLM Engineer</h1><p>Python NLP</p>",
        }

    monkeypatch.setattr(HHResumeProfileSync, "_fetch_selected_resume", fake_fetch)
    monkeypatch.setattr(
        "autohhkek.services.hh_resume_sync.run_hh_login",
        lambda project_root: {"status": "completed", "message": "relogged"},
    )

    result = HHResumeProfileSync(store).sync_selected_resume()

    assert result["status"] in {"updated", "no_changes"}
    assert result["relogin_attempted"] is True
    assert calls["count"] == 2


def test_hh_resume_sync_bootstraps_missing_profile_files(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    store.save_selected_resume_id("resume-1")
    store.save_hh_resumes([{"resume_id": "resume-1", "title": "Trainee ML Engineer", "url": "https://hh.ru/resume/resume-1"}])

    async def fake_fetch(self, resume_id):
        return {
            "page_url": "https://hh.ru/resume/resume-1",
            "text": "Trainee ML Engineer\nО себе\nPython NLP SQL English",
            "html": '<h1 data-qa="resume-block-title-position">Trainee ML Engineer</h1><p>Python NLP SQL English</p>',
        }

    monkeypatch.setattr(HHResumeProfileSync, "_fetch_selected_resume", fake_fetch)

    result = HHResumeProfileSync(store).sync_selected_resume()

    assert result["status"] == "updated"
    assert result["bootstrap_profile"] is True
    assert store.load_preferences() is not None
    assert store.load_anamnesis() is not None
    assert store.load_anamnesis().headline == "Trainee ML Engineer"
