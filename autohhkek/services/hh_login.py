from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic

from autohhkek.services.account_profiles import derive_account_profile
from autohhkek.services.hh_resume_catalog import HHResumeCatalog
from autohhkek.services.storage import WorkspaceStore


def _load_storage_state(state_path: Path) -> dict[str, object]:
    if not state_path.exists():
        return {}
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


async def _is_logged_in(page) -> bool:
    selectors = (
        "[data-qa='mainmenu_myResumes']",
        "[data-qa='mainmenu_vacancyResponses']",
        "a[href*='/applicant/resumes']",
        "a[href*='/applicant/settings']",
    )
    if "login" in page.url:
        return False
    for selector in selectors:
        try:
            if await page.query_selector(selector):
                return True
        except Exception:  # noqa: BLE001
            continue
    try:
        return await page.query_selector("a[data-qa='login']") is None and "/applicant/" in page.url
    except Exception:  # noqa: BLE001
        return False


async def _launch_login_browser(playwright):
    try:
        return await playwright.chromium.launch(headless=False)
    except Exception:
        try:
            return await playwright.chromium.launch(channel="chrome", headless=False)
        except Exception:
            return await playwright.chromium.launch(channel="msedge", headless=False)


async def _run_login_async(project_root: Path, *, timeout_sec: int = 600) -> dict[str, object]:
    from playwright.async_api import async_playwright

    current_store = WorkspaceStore(project_root.resolve())
    state_path = current_store.paths.incoming_hh_state_path
    seed_state = current_store.hh_state_path
    async with async_playwright() as playwright:
        browser = await _launch_login_browser(playwright)
        storage_state = _load_storage_state(seed_state)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            storage_state=storage_state or None,
        )

        page = await context.new_page()
        await page.goto("https://hh.ru/account/login?backurl=%2Fapplicant%2Fresumes", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        deadline = monotonic() + timeout_sec

        while monotonic() < deadline:
            if page.is_closed():
                await browser.close()
                return {
                    "status": "failed",
                    "message": "Browser was closed before login completed.",
                    "state_path": str(state_path),
                }
            if await _is_logged_in(page):
                state_payload = await context.storage_state()
                state_path.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                await browser.close()
                return {
                    "status": "completed",
                    "message": "hh.ru login state saved.",
                    "state_path": str(state_path),
                    "final_url": page.url,
                }
            await asyncio.sleep(2)

        await browser.close()
        return {
            "status": "timeout",
            "message": "Login was not completed before timeout.",
            "state_path": str(state_path),
        }


def run_hh_login(project_root: Path, *, timeout_sec: int = 600) -> dict[str, object]:
    project_root = project_root.resolve()
    result = asyncio.run(_run_login_async(project_root, timeout_sec=timeout_sec))
    store = WorkspaceStore(project_root)
    if result.get("status") != "completed":
        store.touch_dashboard_timestamp(
            "last_hh_login_attempt_at",
            extra={
                "last_hh_login_status": str(result.get("status") or "failed"),
                "last_hh_login_message": str(result.get("message") or ""),
            },
        )
        return result

    incoming_state_path = store.paths.incoming_hh_state_path
    state_payload = _load_storage_state(incoming_state_path)
    catalog_store = WorkspaceStore(project_root)
    resumes_result = HHResumeCatalog(catalog_store, state_path=incoming_state_path).refresh()
    resumes = list(resumes_result.get("items") or [])
    profile = derive_account_profile(storage_state=state_payload, resumes=resumes)
    account_store = WorkspaceStore(project_root, account_key=str(profile["account_key"]))
    account_store.hh_state_path.parent.mkdir(parents=True, exist_ok=True)
    account_store.hh_state_path.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    account_store.save_hh_resumes(resumes)
    if len(resumes) == 1 and not account_store.load_selected_resume_id():
        account_store.save_selected_resume_id(str(resumes[0].get("resume_id") or ""))
    registry_item = account_store.save_account_profile(
        {
            **profile,
            "last_login_at": account_store.touch_dashboard_timestamp("last_hh_login_at")["last_hh_login_at"],
            "selected_resume_id": account_store.load_selected_resume_id(),
        }
    )
    account_store.set_active_account(str(profile["account_key"]))
    result["resumes"] = resumes_result
    result["active_account"] = registry_item
    result["account_key"] = str(profile["account_key"])
    result["display_name"] = str(profile["display_name"])
    result["state_path"] = str(account_store.hh_state_path)
    return result
