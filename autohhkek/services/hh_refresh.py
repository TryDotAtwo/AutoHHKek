from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable

from autohhkek.domain.models import Vacancy
from autohhkek.services.playwright_browser import launch_chromium_resilient


class HHVacancyRefresher:
    def __init__(
        self,
        store,
        *,
        resume_id: str = "",
        state_path: Path | None = None,
        search_runner: Callable[[str, int], object] | None = None,
    ) -> None:
        self.store = store
        selected_resume_id = self.store.load_selected_resume_id() if hasattr(self.store, "load_selected_resume_id") else ""
        self.resume_id = (resume_id or selected_resume_id or os.getenv("AUTOHHKEK_HH_RESUME_ID", "")).strip()
        self.state_path = Path(state_path) if state_path else self.store.hh_state_path
        self.search_runner = search_runner or self._run_live_refresh

    def refresh(self, *, limit: int = 0) -> dict[str, object]:
        if not self.resume_id:
            return {
                "status": "skipped",
                "reason": "resume_id_missing",
                "message": "Не выбрано резюме для поиска на hh.ru.",
            }
        if not self.state_path.exists():
            return {
                "status": "skipped",
                "reason": "login_required",
                "message": "Нет сохраненной сессии hh.ru. Сначала выполните вход.",
            }
        try:
            runner_result = self.search_runner(self.resume_id, limit)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "reason": "refresh_error",
                "message": f"Не удалось обновить вакансии с hh.ru: {exc}",
            }

        metadata: dict[str, object] = {}
        if isinstance(runner_result, tuple) and len(runner_result) == 2:
            vacancies, metadata = runner_result
        else:
            vacancies = runner_result
        vacancies = list(vacancies or [])
        metadata = dict(metadata or {})

        known_ids = {item.vacancy_id for item in self.store.load_vacancies()}
        unique_vacancies: list[Vacancy] = []
        seen_ids: set[str] = set()
        for vacancy in vacancies:
            if vacancy.vacancy_id in seen_ids:
                continue
            seen_ids.add(vacancy.vacancy_id)
            unique_vacancies.append(vacancy)
        new_ids = [item.vacancy_id for item in unique_vacancies if item.vacancy_id not in known_ids]

        if vacancies:
            total_available = int(metadata.get("total_available") or 0)
            pages_parsed = int(metadata.get("pages_parsed") or 0)
            search_url = str(metadata.get("search_url") or "")
            self.store.save_vacancies(unique_vacancies)
            self.store.update_dashboard_state(
                {
                    "last_live_refresh_total_available": total_available,
                    "last_live_refresh_count": len(unique_vacancies),
                    "last_live_refresh_new_count": len(new_ids),
                    "last_live_refresh_pages_parsed": pages_parsed,
                    "last_live_refresh_search_url": search_url,
                    "last_live_refresh_message": f"Поиск hh.ru завершен: в очереди {len(unique_vacancies)} вакансий, новых {len(new_ids)}.",
                }
            )
            self.store.record_event(
                "vacancy-refresh",
                f"Обновлено {len(unique_vacancies)} вакансий из поиска hh.ru. Новых: {len(new_ids)}.",
                details={
                    "resume_id": self.resume_id,
                    "count": len(unique_vacancies),
                    "new_count": len(new_ids),
                    "total_available": total_available,
                    "pages_parsed": pages_parsed,
                    "search_url": search_url,
                },
            )
            total_suffix = f" На hh.ru найдено {total_available}." if total_available else ""
            pages_suffix = f" Пройдено страниц: {pages_parsed}." if pages_parsed else ""
            return {
                "status": "updated",
                "reason": "live_refresh",
                "message": (
                    f"Поиск hh.ru завершен: в очереди {len(unique_vacancies)} вакансий, новых {len(new_ids)}."
                    f"{total_suffix}{pages_suffix}"
                ),
                "count": len(unique_vacancies),
                "new_count": len(new_ids),
                "total_available": total_available,
                "pages_parsed": pages_parsed,
                "search_url": search_url,
            }

        self.store.save_vacancies([])
        self.store.update_dashboard_state(
            {
                "last_live_refresh_total_available": int(metadata.get("total_available") or 0),
                "last_live_refresh_count": 0,
                "last_live_refresh_new_count": 0,
                "last_live_refresh_pages_parsed": int(metadata.get("pages_parsed") or 0),
                "last_live_refresh_search_url": str(metadata.get("search_url") or ""),
                "last_live_refresh_message": "Поиск hh.ru завершен без вакансий.",
            }
        )
        self.store.record_event(
            "vacancy-refresh",
            "Поиск hh.ru завершен без вакансий.",
            details={"resume_id": self.resume_id, "count": 0, **metadata},
        )
        return {
            "status": "empty",
            "reason": "no_results",
            "message": "Поиск hh.ru завершен, но вакансий не найдено.",
            "count": 0,
            **metadata,
        }

    def _run_live_refresh(self, resume_id: str, limit: int) -> tuple[list[Vacancy], dict[str, object]]:
        async def _inner() -> tuple[list[Vacancy], dict[str, object]]:
            from playwright.async_api import async_playwright

            from logic.vacancy_parser import extract_vacancy_detail, search_vacancies

            state_payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            cookies = list(state_payload.get("cookies") or [])
            filter_plan = self.store.load_filter_plan() or {}
            query_params = dict(filter_plan.get("query_params") or {})
            async with async_playwright() as playwright:
                browser = await launch_chromium_resilient(playwright, headless=True)
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 1100},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                if cookies:
                    await context.add_cookies(cookies)
                page = await context.new_page()
                raw_vacancies, total_count, parser_meta = await search_vacancies(page, resume_id, query_params=query_params)
                detail_limit = min(len(raw_vacancies), limit if limit and limit > 0 else 120)
                for item in raw_vacancies[:detail_limit]:
                    vacancy_url = str(item.get("url") or "").strip()
                    if not vacancy_url:
                        continue
                    try:
                        await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)
                        detail = await extract_vacancy_detail(page)
                    except Exception:
                        continue
                    description = str(detail.get("description") or "").strip()
                    if description:
                        item["description"] = description
                    skills = [str(skill).strip() for skill in list(detail.get("skills") or []) if str(skill).strip()]
                    if skills:
                        item["skills"] = skills
                await context.close()
                await browser.close()
            result = [self._to_vacancy(item, resume_id) for item in raw_vacancies]
            return (
                result[:limit] if limit and limit > 0 else result,
                {
                    "total_available": total_count,
                    "pages_parsed": int(parser_meta.get("pages_parsed") or 0),
                    "search_url": str(parser_meta.get("search_url") or filter_plan.get("search_url") or ""),
                },
            )

        return asyncio.run(_inner())

    def _to_vacancy(self, payload: dict[str, str], resume_id: str) -> Vacancy:
        url = str(payload.get("url") or "").strip()
        title = str(payload.get("title") or "Без названия").strip() or "Без названия"
        match = re.search(r"/vacancy/(\d+)", url)
        vacancy_id = match.group(1) if match else hashlib.sha1(f"{title}:{url}".encode("utf-8")).hexdigest()[:16]
        salary_text = str(payload.get("salary_text") or "").strip()
        salary_numbers = [int(item.replace(" ", "")) for item in re.findall(r"(\d[\d ]{3,})", salary_text)]
        salary_from = salary_numbers[0] if len(salary_numbers) >= 1 else None
        salary_to = salary_numbers[1] if len(salary_numbers) >= 2 else salary_from
        return Vacancy(
            vacancy_id=vacancy_id,
            title=title,
            company=str(payload.get("company") or "").strip(),
            location=str(payload.get("location") or "").strip(),
            employment=str(payload.get("employment") or "").strip(),
            salary_text=salary_text,
            salary_from=salary_from,
            salary_to=salary_to,
            is_remote=str(payload.get("is_remote") or "").strip().lower() == "true",
            url=url,
            summary=str(payload.get("summary") or title).strip(),
            description=str(payload.get("description") or payload.get("all_text") or payload.get("summary") or title).strip(),
            meta={"source": "hh_live_search", "resume_id": resume_id},
        )
