from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import traceback
from pathlib import Path

from autohhkek.services.account_profiles import derive_account_profile
from autohhkek.services.playwright_browser import ensure_async_subprocess_available, launch_chromium_resilient


def _normalize_resume_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"https://hh.ru{url}"
    return f"https://hh.ru/{url}"


def _cleanup_resume_title(value: str) -> str:
    title = re.sub(r"<[^>]+>", " ", str(value or ""))
    title = html_lib.unescape(title).replace("\xa0", " ")
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"\s+Обновлено.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*,?\s*Подключено автоподнятие.*$", "", title, flags=re.IGNORECASE)
    return title.strip(" ,") or ""


def _title_from_card_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip(" ,") for line in str(value or "").splitlines()]
    cleaned = [line for line in lines if line]
    if not cleaned:
        return ""
    bad_prefixes = (
        "обновлено",
        "подключено автоподнятие",
        "постоянная работа",
        "подработка",
        "стажировка",
        "начинающий специалист",
        "удалённо",
        "удаленно",
        "на месте работодателя",
    )
    for line in cleaned[:8]:
        normalized = line.casefold()
        if any(normalized.startswith(prefix) for prefix in bad_prefixes):
            continue
        if re.fullmatch(r"[\d\s₽$€.,·+\-]+", line):
            continue
        return _cleanup_resume_title(line)
    return _cleanup_resume_title(cleaned[0])


def _extract_resume_items(page_html: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r'href="(?P<href>[^"]*/resume/(?P<id>[a-zA-Z0-9-]+)[^"]*)"[^>]*>(?P<title>.*?)</a>', re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(page_html):
        resume_id = match.group("id").strip()
        if not resume_id or resume_id in seen:
            continue
        seen.add(resume_id)
        title = _cleanup_resume_title(match.group("title")) or resume_id
        items.append({"resume_id": resume_id, "title": title, "url": _normalize_resume_url(match.group("href").strip())})
    return items


def _extract_resume_id(value: str) -> str:
    match = re.search(r"/resume/([a-zA-Z0-9-]+)", str(value or ""))
    return match.group(1).strip() if match else ""


def _extract_resume_items_from_dom_payload(raw_items: list[dict[str, object]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_items:
        href = str(raw.get("href") or "").strip()
        resume_id = _extract_resume_id(href)
        if not resume_id:
            for attr_value in raw.get("attrs") or []:
                resume_id = _extract_resume_id(str(attr_value or ""))
                if resume_id:
                    break
        if not resume_id or resume_id in seen:
            continue
        seen.add(resume_id)
        title = _cleanup_resume_title(str(raw.get("title") or ""))
        if not title or title == resume_id:
            title = _title_from_card_text(str(raw.get("card_text") or "")) or resume_id
        items.append(
            {
                "resume_id": resume_id,
                "title": title or resume_id,
                "url": _normalize_resume_url(href or f"/resume/{resume_id}"),
            }
        )
    return items


def _merge_resume_candidates(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    by_id: dict[str, int] = {}
    for group in groups:
        for item in group:
            resume_id = str(item.get("resume_id") or "").strip()
            if not resume_id:
                continue
            title = _cleanup_resume_title(str(item.get("title") or "")) or resume_id
            current = {
                "resume_id": resume_id,
                "title": title,
                "url": _normalize_resume_url(str(item.get("url") or f"https://hh.ru/resume/{resume_id}").strip()),
            }
            index = by_id.get(resume_id)
            if index is None:
                by_id[resume_id] = len(merged)
                merged.append(current)
                continue
            existing = merged[index]
            if len(title) > len(str(existing.get("title") or "")) and title != resume_id:
                existing["title"] = title
            if not str(existing.get("url") or "").strip():
                existing["url"] = current["url"]
    return merged


class HHResumeCatalog:
    def __init__(self, store, *, state_path: Path | None = None) -> None:
        self.store = store
        self.state_path = Path(state_path) if state_path else self.store.hh_state_path

    def refresh(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {"status": "skipped", "message": " hh_state.json  .     hh.ru.", "items": []}
        try:
            ensure_async_subprocess_available()
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "message": f"Playwright runtime is unavailable for hh.ru resume refresh: {exc}", "items": []}
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
            return {"status": "failed", "message": f"    : {exc}", "items": [], "debug_artifact": debug_path}
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
        message = str(fetch_result.get("message") or f"Найдено резюме hh.ru: {len(items)}.")
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
                page_url = page.url
                page_title = await page.title()
                items = await self._collect_resume_candidates(page)
                page_html = await page.content()
                last_page_url = page_url
                last_page_title = page_title
                last_html = page_html
                regex_items = _extract_resume_items(page_html)
                items = _merge_resume_candidates(items, regex_items)
                if items:
                    debug_path = self._write_debug_artifact(page_url=page_url, page_title=page_title, html=page_html)
                    return {
                        "status": "completed",
                        "message": f"Найдено резюме hh.ru: {len(items)}.",
                        "items": items,
                        "page_url": page_url,
                        "page_title": page_title,
                        "debug_artifact": debug_path,
                    }
                if "login" in page.url or await page.query_selector("a[data-qa='login']"):
                    return {
                        "status": "login_required",
                        "message": " hh.ru  .     .",
                        "items": [],
                        "page_url": page_url,
                        "page_title": page_title,
                    }
        debug_path = self._write_debug_artifact(page_url=last_page_url, page_title=last_page_title, html=last_html)
        return {
            "status": "empty",
            "message": "  hh.ru ,      . ,        .",
            "items": [],
            "page_url": last_page_url,
            "page_title": last_page_title,
            "debug_artifact": debug_path,
        }

    async def _scroll_resume_list(self, page) -> None:
        for _ in range(8):
            try:
                moved = await page.evaluate(
                    """() => {
                        const before = window.scrollY || document.documentElement.scrollTop || 0;
                        window.scrollTo(0, document.body.scrollHeight);
                        const containers = Array.from(document.querySelectorAll('*'))
                          .filter(node => {
                            const style = window.getComputedStyle(node);
                            return ['auto', 'scroll'].includes(style.overflowY) && node.scrollHeight > node.clientHeight + 80;
                          });
                        let movedContainer = false;
                        for (const node of containers.slice(0, 12)) {
                          const topBefore = node.scrollTop || 0;
                          node.scrollTop = node.scrollHeight;
                          if ((node.scrollTop || 0) > topBefore) movedContainer = true;
                        }
                        const after = window.scrollY || document.documentElement.scrollTop || 0;
                        return after > before || movedContainer;
                    }"""
                )
            except Exception:  # noqa: BLE001
                return
            await page.wait_for_timeout(350)
            if not moved:
                break
        try:
            await page.evaluate(
                """() => {
                    window.scrollTo(0, 0);
                    const containers = Array.from(document.querySelectorAll('*'))
                      .filter(node => {
                        const style = window.getComputedStyle(node);
                        return ['auto', 'scroll'].includes(style.overflowY) && node.scrollHeight > node.clientHeight + 80;
                      });
                    for (const node of containers.slice(0, 12)) node.scrollTop = 0;
                }"""
            )
        except Exception:  # noqa: BLE001
            return
        await page.wait_for_timeout(150)

    async def _collect_resume_candidates(self, page) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        previous_count = -1
        for _ in range(3):
            await self._scroll_resume_list(page)
            await page.wait_for_timeout(700)
            dom_items = await self._extract_resume_items_dom(page)
            page_html = await page.content()
            regex_items = _extract_resume_items(page_html)
            merged = _merge_resume_candidates(merged, dom_items, regex_items)
            if len(merged) == previous_count:
                break
            previous_count = len(merged)
        return merged

    async def _extract_resume_items_dom(self, page) -> list[dict[str, str]]:
        try:
            raw_items = await page.locator("a[href*='/resume/'], [data-qa*='resume'], [class*='resume'], [class*='Resume']").evaluate_all(
                """nodes => nodes.map(node => {
                    const href = node.getAttribute('href') || '';
                    const selfText = (node.textContent || '').trim();
                    const aria = node.getAttribute('aria-label') || '';
                    const card = node.closest('article, li, [data-qa*="resume"], [class*="resume"], [class*="Resume"]');
                    const cardText = (card && (card.innerText || card.textContent) || '').trim();
                    const attrs = Array.from(node.attributes || []).map(attr => attr.value || '');
                    return { href, title: selfText || aria, card_text: cardText, attrs };
                })"""
            )
        except Exception:  # noqa: BLE001
            return []
        return _extract_resume_items_from_dom_payload(raw_items)
