from __future__ import annotations

from autohhkek.services.hh_login import run_hh_login
from autohhkek.services.hh_resume_catalog import HHResumeCatalog
from autohhkek.services.storage import WorkspaceStore


def _refresh_catalog(store, state_path):
    return HHResumeCatalog(store, state_path=state_path).refresh()


def ensure_hh_context(store, *, auto_login: bool = True) -> dict[str, object]:
    state_path = store.hh_state_path
    login_result: dict[str, object] | None = None
    active_store = store

    if hasattr(store, "record_event"):
        store.record_event("hh-preflight", "Checking hh.ru login state before analysis.")

    if not state_path.exists():
        if not auto_login:
            return {
                "status": "needs_login",
                "message": "Login to hh.ru is required before live vacancy refresh.",
            }
        if hasattr(store, "record_event"):
            store.record_event("hh-preflight", "hh_state.json is missing. Opening browser login flow for hh.ru.")
        login_result = run_hh_login(store.project_root)
        if login_result.get("status") != "completed":
            if hasattr(store, "record_event"):
                store.record_event(
                    "hh-preflight",
                    str(login_result.get("message") or "hh.ru login was not completed."),
                    details=login_result,
                )
            return {
                "status": "needs_login",
                "message": str(login_result.get("message") or "hh.ru login was not completed."),
                "login_result": login_result,
            }

    if hasattr(store, "record_event"):
        store.record_event("hh-preflight", "Refreshing hh.ru resume catalog.")
    catalog = _refresh_catalog(active_store, state_path)
    if catalog.get("status") == "login_required" and auto_login:
        if hasattr(store, "record_event"):
            store.record_event("hh-preflight", "hh.ru session expired. Reopening login flow.", details=catalog)
        login_result = run_hh_login(store.project_root)
        if login_result.get("status") == "completed":
            active_store = WorkspaceStore(store.project_root)
            state_path = active_store.hh_state_path
            catalog = _refresh_catalog(active_store, state_path)
    if catalog.get("status") == "login_required":
        if hasattr(active_store, "record_event"):
            active_store.record_event("hh-preflight", str(catalog.get("message") or "hh.ru session is not authenticated."), details=catalog)
        return {
            "status": "needs_login",
            "message": str(catalog.get("message") or "hh.ru session is not authenticated."),
            "login_result": login_result or {},
            "catalog": catalog,
        }
    items = list(catalog.get("items") or [])
    selected_resume_id = active_store.load_selected_resume_id()
    cached_items = list(active_store.load_hh_resumes()) if hasattr(active_store, "load_hh_resumes") else []
    if len(items) == 1 and not selected_resume_id:
        selected_resume_id = str(items[0].get("resume_id") or "")
        active_store.save_selected_resume_id(selected_resume_id)
        if hasattr(active_store, "record_event"):
            active_store.record_event("hh-preflight", f"Auto-selected the only hh.ru resume: {selected_resume_id}.")
    if not items and selected_resume_id and any(str(item.get("resume_id") or "") == selected_resume_id for item in cached_items):
        if hasattr(active_store, "record_event"):
            active_store.record_event(
                "hh-preflight",
                "Resume catalog refresh returned empty, falling back to locally cached selected resume.",
                details={"selected_resume_id": selected_resume_id, "cached_count": len(cached_items)},
            )
        catalog = {
            **catalog,
            "status": str(catalog.get("status") or "completed"),
            "items": cached_items,
            "message": str(catalog.get("message") or "Using cached hh.ru resume list."),
        }
        items = cached_items
    if not items:
        if hasattr(active_store, "record_event"):
            active_store.record_event("hh-preflight", "No hh.ru resumes were found for the logged in account.", details=catalog)
        return {
            "status": "no_resumes",
            "message": "No hh.ru resumes were found for the logged in account.",
            "login_result": login_result or {},
            "catalog": catalog,
        }
    if len(items) > 1 and not selected_resume_id:
        if hasattr(active_store, "record_event"):
            active_store.record_event("hh-preflight", "Several hh.ru resumes were found. Waiting for explicit resume selection.", details=catalog)
        return {
            "status": "needs_resume_selection",
            "message": "Several hh.ru resumes were found. Select one resume before analysis.",
            "login_result": login_result or {},
            "catalog": catalog,
        }
    if hasattr(active_store, "record_event"):
        active_store.record_event("hh-preflight", f"hh.ru preflight is ready. Selected resume: {selected_resume_id}.")
    return {
        "status": "ready",
        "message": "hh.ru context is ready for live vacancy refresh.",
        "selected_resume_id": selected_resume_id,
        "login_result": login_result or {},
        "catalog": catalog,
        "account_key": active_store.account_key,
    }
