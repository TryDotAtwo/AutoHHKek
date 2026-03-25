from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import traceback
from pathlib import Path

from autohhkek.services.account_profiles import derive_account_profile
from autohhkek.services.playwright_browser import launch_chromium_resilient


def _normalize_resume_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"https://hh.ru{url}"
    return f"https://hh.ru/{url}"


def _extract_resume_items(page_html: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r'href="(?P<href>[^"]*/resume/(?P<id>[a-zA-Z0-9-]+)[^"]*)"[^>]*>(?P<title>.*?)</a>', re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(page_html):
        resume_id = match.group("id").strip()
        if not resume_id or resume_id in seen:
            continue
        seen.add(resume_id)
        title = re.sub(r"<[^>]+>", " ", match.group("title"))
        title = html_lib.unescape(title).replace("\xa0", " ")
        title = re.sub(r"\s+", " ", title).strip() or f"Резюме {resume_id}"
        items.append({"resume_id": resume_id, "title": title, "url": _normalize_resume_url(match.group("href").strip())})
    return items


class HHResumeCatalog:
    def __init__(self, store, *, state_path: Path | None = None) -> None:
        self.store = store
        self.state_path = Path(state_path) if state_path else self.store.hh_state_path

    def refresh(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {"status": "skipped", "message": "Файл hh_state.json не найден. Сначала выполните вход в hh.ru.", "items": []}
        try:
            fetch_result = asyncio.run(self._fetch())
        except Exception as exc:  # noqa: BLE001
            debug_path = self.store.save_debug_artifact(
                "hh-resumes-exception",
                {
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "state_path": str(self.state_path),
                },
                extension="json",
                subdir="hh",
            )
            self.store.record_event(
                "hh-resumes",
                f"Resume refresh failed: {exc}",
                details={"debug_path": debug_path},
            )
            return {"status": "failed", "message": f"Не удалось обновить список резюме: {exc}", "items": [], "debug_artifact": debug_path}
        items = list(fetch_result.get("items") or [])
        self.store.save_hh_resumes(items)
        selected = self.store.load_selected_resume_id()
        if len(items) == 1 and not selected:
            self.store.save_selected_resume_id(items[0]["resume_id"])
            selected = items[0]["resume_id"]
        account_profile = derive_account_profile(resumes=items)
        self.store.save_account_profile(
            {
                **account_profile,
                "account_key": self.store.account_key,
                "selected_resume_id": selected or "",
            }
        )
        status = str(fetch_result.get("status") or "completed")
        message = str(fetch_result.get("message") or f"Список резюме обновлен: найдено {len(items)}.")
        self.store.touch_dashboard_timestamp(
            "last_resume_catalog_at",
            extra={
                "last_resume_catalog_status": status,
                "last_resume_catalog_count": len(items),
            },
        )
        self.store.record_event(
            "hh-resumes",
            message,
            details={
                "selected_resume_id": selected or "",
                "status": status,
                "page_url": fetch_result.get("page_url", ""),
                "page_title": fetch_result.get("page_title", ""),
                "debug_artifact": fetch_result.get("debug_artifact", ""),
            },
        )
        return {
            "status": status,
            "message": message,
            "items": items,
            "selected_resume_id": selected or "",
            "page_url": fetch_result.get("page_url", ""),
            "page_title": fetch_result.get("page_title", ""),
            "debug_artifact": fetch_result.get("debug_artifact", ""),
        }

    def _write_debug_artifact(self, *, page_url: str, page_title: str, html: str) -> str:
        html_path = self.store.save_debug_artifact("hh-resumes-page", html, extension="html", subdir="hh")
        return self.store.save_debug_artifact(
            "hh-resumes-page-meta",
            {
                "page_url": page_url,
                "page_title": page_title,
                "html_path": html_path,
            },
            extension="json",
            subdir="hh",
        )

    async def _fetch(self) -> dict[str, object]:
        from playwright.async_api import async_playwright

        state_payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        last_page_url = ""
        last_page_title = ""
        last_html = ""
        async with async_playwright() as playwright:
            browser = await launch_chromium_resilient(playwright, headless=True)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                storage_state=state_payload or None,
            )
            page = await context.new_page()
            for url in (
                "https://hh.ru/applicant/resumes",
                "https://hh.ru/applicant/resumes?hhtmFrom=main",
                "https://hh.ru/resumes",
            ):
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1500)
                page_html = await page.content()
                page_url = page.url
                page_title = await page.title()
                last_page_url = page_url
                last_page_title = page_title
                last_html = page_html
                items = _extract_resume_items(page_html)
                if not items:
                    try:
                        links = await page.locator("a[href*='/resume/']").evaluate_all(
                            """nodes => nodes.map(node => ({href: node.getAttribute('href') || '', title: (node.textContent || '').trim()}))"""
                        )
                    except Exception:  # noqa: BLE001
                        links = []
                    seen: set[str] = set()
                    for link in links:
                        href = str(link.get("href") or "")
                        match = re.search(r"/resume/([a-zA-Z0-9-]+)", href)
                        if not match:
                            continue
                        resume_id = match.group(1)
                        if resume_id in seen:
                            continue
                        seen.add(resume_id)
                        items.append(
                            {
                                "resume_id": resume_id,
                                "title": html_lib.unescape(str(link.get("title") or f"Резюме {resume_id}")).replace("\xa0", " ").strip() or f"Резюме {resume_id}",
                                "url": _normalize_resume_url(href),
                            }
                        )
                if items:
                    return {
                        "status": "completed",
                        "message": f"Список резюме обновлен: найдено {len(items)}.",
                        "items": items,
                        "page_url": page_url,
                        "page_title": page_title,
                    }
                if "login" in page.url or await page.query_selector("a[data-qa='login']"):
                    return {
                        "status": "login_required",
                        "message": "Сессия hh.ru не авторизована. Сначала завершите вход в браузере.",
                        "items": [],
                        "page_url": page_url,
                        "page_title": page_title,
                    }
        debug_path = self._write_debug_artifact(page_url=last_page_url, page_title=last_page_title, html=last_html)
        return {
            "status": "empty",
            "message": "Страница резюме hh.ru открылась, но карточки резюме не удалось извлечь. Возможно, в аккаунте нет резюме или изменилась разметка страницы.",
            "items": [],
            "page_url": last_page_url,
            "page_title": last_page_title,
            "debug_artifact": debug_path,
        }
