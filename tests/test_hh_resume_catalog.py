from pathlib import Path

from autohhkek.services.hh_resume_catalog import HHResumeCatalog, _extract_resume_items
from autohhkek.services.storage import WorkspaceStore


async def _raise_fetch_error(self):
    raise RuntimeError("playwright crashed")


async def _return_fetch_payload(self):
    return {
        "status": "empty",
        "message": "No resumes were extracted.",
        "items": [],
        "page_url": "https://hh.ru/applicant/resumes",
        "page_title": "My resumes",
        "debug_artifact": "C:/debug/meta.json",
    }


def test_resume_catalog_refresh_saves_debug_artifact_on_exception(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    monkeypatch.setattr(HHResumeCatalog, "_fetch", _raise_fetch_error)

    result = HHResumeCatalog(store).refresh()

    assert result["status"] == "failed"
    assert "debug_artifact" in result
    assert Path(result["debug_artifact"]).exists()


def test_resume_catalog_refresh_returns_debug_artifact_from_fetch_result(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    monkeypatch.setattr(HHResumeCatalog, "_fetch", _return_fetch_payload)

    result = HHResumeCatalog(store).refresh()

    assert result["status"] == "empty"
    assert result["debug_artifact"] == "C:/debug/meta.json"


def test_extract_resume_items_unescapes_title_and_does_not_shadow_html_module():
    items = _extract_resume_items(
        '<a href="/resume/07687234ff0dd3ea5f0039ed1f47594655564f" title="ignored">Theoretical&nbsp;physicist &amp; LLM Engineer</a>'
    )

    assert items == [
        {
            "resume_id": "07687234ff0dd3ea5f0039ed1f47594655564f",
            "title": "Theoretical physicist & LLM Engineer",
            "url": "https://hh.ru/resume/07687234ff0dd3ea5f0039ed1f47594655564f",
        }
    ]


async def _return_resume_payload(self):
    return {
        "status": "completed",
        "message": "Список резюме обновлен: найдено 1.",
        "items": [
            {
                "resume_id": "07687234ff0dd3ea5f0039ed1f47594655564f",
                "title": "Theoretical physicist",
                "url": "https://hh.ru/resume/07687234ff0dd3ea5f0039ed1f47594655564f",
            }
        ],
        "page_url": "https://hh.ru/applicant/resumes",
        "page_title": "Мои резюме",
    }


def test_resume_catalog_refresh_updates_account_profile_resume_count(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path, account_key="hh-demo")
    store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    store.hh_state_path.write_text('{"cookies": []}', encoding="utf-8")
    monkeypatch.setattr(HHResumeCatalog, "_fetch", _return_resume_payload)

    HHResumeCatalog(store).refresh()

    accounts = {item["account_key"]: item for item in store.load_accounts()}
    assert accounts["hh-demo"]["resume_count"] == 1
    assert accounts["hh-demo"]["resume_ids"] == ["07687234ff0dd3ea5f0039ed1f47594655564f"]
