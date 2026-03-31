from __future__ import annotations

import asyncio
import html
import json
import re
from pathlib import Path
from typing import Any

from autohhkek.domain.models import Anamnesis, UserPreferences, utc_now_iso
from autohhkek.services.hh_login import run_hh_login
from autohhkek.services.playwright_browser import launch_chromium_resilient


KNOWN_SKILLS = [
    "Python",
    "SQL",
    "NLP",
    "LLM",
    "RAG",
    "Transformers",
    "PyTorch",
    "TensorFlow",
    "Pandas",
    "NumPy",
    "Scikit-learn",
    "LangChain",
    "FastAPI",
    "Docker",
    "Kubernetes",
    "Airflow",
    "Spark",
    "Linux",
    "MLOps",
    "Computer Vision",
    "Deep Learning",
]

SECTION_STOP_WORDS = (
    "Опыт работы",
    "Ключевые навыки",
    "Навыки",
    "Образование",
    "Повышение квалификации",
    "Сертификаты",
    "Знание языков",
    "Гражданство",
    "Разрешение на работу",
)

RESUME_NOISE_PATTERNS = (
    r"^Мы используем файлы cookie",
    r"^Правила использования файлов cookie",
    r"^Понятно$",
    r"^Чаты\b",
    r"^Резюме и профиль\b",
    r"^Отклики\b",
    r"^Сервисы\b",
    r"^Карьера\b",
    r"^Помощь\b",
    r"^Поиск\b",
    r"^Создать резюме$",
    r"^Мои резюме\s*/?$",
    r"^Редактировать$",
)

RESUME_END_MARKERS = (
    "Подобрали для вас",
    "Скачайте приложение",
    "HeadHunter",
    "О компании",
    "Наши вакансии",
    "Реклама на сайте",
    "Соискателям",
    "Мобильное приложение",
    "Этика и комплаенс",
    "Пользовательское соглашение",
)

LANGUAGE_MARKERS = (
    "Русский",
    "Английский",
    "Немецкий",
    "Французский",
    "Испанский",
    "Китайский",
    "Russian",
    "English",
)


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = _normalize_space(item).casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(_normalize_space(item))
    return result


def _strip_tags(value: str) -> str:
    return _normalize_space(re.sub(r"<[^>]+>", " ", html.unescape(value or "")))


def _clean_resume_text(page_text: str, *, title: str = "") -> str:
    title_normalized = _normalize_space(title)
    seen_title = not bool(title_normalized)
    cleaned_lines: list[str] = []
    for raw_line in str(page_text or "").splitlines():
        line = _normalize_space(raw_line)
        if not line:
            continue
        if not seen_title:
            if title_normalized and title_normalized in line:
                seen_title = True
            else:
                continue
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in RESUME_NOISE_PATTERNS):
            continue
        if any(marker.casefold() in line.casefold() for marker in RESUME_END_MARKERS):
            break
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _extract_title(page_text: str, page_html: str) -> str:
    patterns = (
        r'<h1[^>]*data-qa=["\']resume-block-title-position["\'][^>]*>(.*?)</h1>',
        r"<h1[^>]*>(.*?)</h1>",
    )
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = _strip_tags(match.group(1))
            if title:
                return title
    for line in page_text.splitlines():
        candidate = _normalize_space(line)
        if candidate and len(candidate) <= 120:
            return candidate
    return ""


def _extract_summary(page_text: str) -> str:
    stop_pattern = "|".join(map(re.escape, SECTION_STOP_WORDS))
    for marker in ("Обо мне", "О себе", "Summary"):
        match = re.search(rf"{re.escape(marker)}\s*(.+?)(?:\n\s*\n|{stop_pattern})", page_text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            summary = _normalize_space(match.group(1))
            if summary:
                return summary[:1000]
    paragraphs = [_normalize_space(part) for part in page_text.split("\n\n")]
    for paragraph in paragraphs:
        if not paragraph or len(paragraph) <= 80:
            continue
        if re.search(r"(Контакты|Мобильный телефон|Электронная почта|Опыт работы|Ключевые навыки)", paragraph, flags=re.IGNORECASE):
            continue
        return paragraph[:1000]
    return ""


def _summary_has_resume_noise(value: str) -> bool:
    normalized = _normalize_space(value)
    if not normalized:
        return False
    lines = [line for line in normalized.splitlines() if line]
    for line in lines[:12]:
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in RESUME_NOISE_PATTERNS):
            return True
    lowered = normalized.casefold()
    noisy_fragments = (
        "мы используем файлы cookie",
        "правила использования файлов cookie",
        "резюме и профиль",
        "отклики",
        "создать резюме",
        "мобильный телефон",
        "электронная почта",
        "предпочитаемый способ связи",
    )
    return any(fragment in lowered for fragment in noisy_fragments)


def _extract_experience_years(page_text: str) -> float | None:
    match = re.search(r"Опыт работы[:\s]+(\d+)\s*(?:год|года|лет)?(?:\s+(\d+)\s*(?:месяц|месяца|месяцев))?", page_text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"(\d+)\s*(?:год|года|лет)(?:\s+(\d+)\s*(?:месяц|месяца|месяцев))?\s+опыта", page_text, flags=re.IGNORECASE)
    if not match:
        return None
    years = int(match.group(1) or 0)
    months = int(match.group(2) or 0)
    return round(years + (months / 12.0), 1)


def _extract_links(page_text: str) -> list[str]:
    links = re.findall(r"https?://[^\s<>()]+", page_text)
    return _unique([link.rstrip(".,)") for link in links])


def _extract_languages(page_text: str) -> list[str]:
    languages: list[str] = []
    for language in LANGUAGE_MARKERS:
        if re.search(rf"\b{re.escape(language)}\b", page_text, flags=re.IGNORECASE):
            languages.append(language)
    return _unique(languages)


def _extract_skills(page_text: str, preferences: UserPreferences, anamnesis: Anamnesis) -> list[str]:
    pool = list(KNOWN_SKILLS)
    pool.extend(preferences.required_skills)
    pool.extend(preferences.preferred_skills)
    pool.extend(anamnesis.primary_skills)
    pool.extend(anamnesis.secondary_skills)
    hits: list[str] = []
    lowered = page_text.casefold()
    for skill in _unique(pool):
        if skill and skill.casefold() in lowered:
            hits.append(skill)
    return _unique(hits)


def extract_resume_profile(page_text: str, page_html: str, preferences: UserPreferences, anamnesis: Anamnesis) -> dict[str, Any]:
    title = _extract_title(page_text, page_html)
    cleaned_text = _clean_resume_text(page_text, title=title)
    summary = _extract_summary(cleaned_text)
    experience_years = _extract_experience_years(cleaned_text)
    skills = _extract_skills(cleaned_text, preferences, anamnesis)
    languages = _extract_languages(cleaned_text)
    links = _extract_links(cleaned_text)
    target_titles = _unique([title, *preferences.target_titles]) if title else list(preferences.target_titles)
    return {
        "headline": title,
        "summary": summary,
        "cleaned_text": cleaned_text[:20000],
        "experience_years": experience_years,
        "skills": skills,
        "languages": languages,
        "links": links,
        "target_titles": target_titles,
    }


def apply_resume_profile_sync(
    preferences: UserPreferences,
    anamnesis: Anamnesis,
    extracted: dict[str, Any],
) -> tuple[UserPreferences, Anamnesis, list[dict[str, Any]]]:
    updated_preferences = UserPreferences.from_dict(preferences.to_dict())
    updated_anamnesis = Anamnesis.from_dict(anamnesis.to_dict())
    changes: list[dict[str, Any]] = []

    def remember_change(scope: str, field: str, before: Any, after: Any) -> None:
        if before == after:
            return
        changes.append({"scope": scope, "field": field, "before": before, "after": after})

    headline = str(extracted.get("headline") or "").strip()
    if headline and headline != updated_anamnesis.headline:
        remember_change("anamnesis", "headline", updated_anamnesis.headline, headline)
        updated_anamnesis.headline = headline

    summary = str(extracted.get("summary") or "").strip()
    current_summary = str(updated_anamnesis.summary or "").strip()
    should_replace_summary = bool(summary) and summary != current_summary and (
        not current_summary
        or len(summary) >= len(current_summary)
        or _summary_has_resume_noise(current_summary)
    )
    if should_replace_summary:
        remember_change("anamnesis", "summary", updated_anamnesis.summary, summary)
        updated_anamnesis.summary = summary

    experience_years = extracted.get("experience_years")
    if isinstance(experience_years, (int, float)) and experience_years > 0 and abs(float(updated_anamnesis.experience_years or 0.0) - float(experience_years)) >= 0.5:
        remember_change("anamnesis", "experience_years", updated_anamnesis.experience_years, experience_years)
        updated_anamnesis.experience_years = float(experience_years)

    skills = _unique(list(updated_anamnesis.primary_skills) + list(extracted.get("skills") or []))
    if skills != updated_anamnesis.primary_skills:
        remember_change("anamnesis", "primary_skills", updated_anamnesis.primary_skills, skills)
        updated_anamnesis.primary_skills = skills

    languages = _unique(list(updated_anamnesis.languages) + list(extracted.get("languages") or []))
    if languages != updated_anamnesis.languages:
        remember_change("anamnesis", "languages", updated_anamnesis.languages, languages)
        updated_anamnesis.languages = languages

    links = _unique(list(updated_anamnesis.links) + list(extracted.get("links") or []))
    if links != updated_anamnesis.links:
        remember_change("anamnesis", "links", updated_anamnesis.links, links)
        updated_anamnesis.links = links

    target_titles = _unique(list(extracted.get("target_titles") or updated_preferences.target_titles))
    if target_titles != updated_preferences.target_titles:
        remember_change("preferences", "target_titles", updated_preferences.target_titles, target_titles)
        updated_preferences.target_titles = target_titles

    preferred_skills = _unique(list(updated_preferences.preferred_skills) + list(extracted.get("skills") or []))
    if preferred_skills != updated_preferences.preferred_skills:
        remember_change("preferences", "preferred_skills", updated_preferences.preferred_skills, preferred_skills)
        updated_preferences.preferred_skills = preferred_skills

    return updated_preferences, updated_anamnesis, changes


class HHResumeProfileSync:
    def __init__(self, store, *, state_path: Path | None = None) -> None:
        self.store = store
        self.state_path = Path(state_path) if state_path else self.store.hh_state_path

    def sync_selected_resume(self) -> dict[str, Any]:
        preferences = self.store.load_preferences()
        anamnesis = self.store.load_anamnesis()
        selected_resume_id = self.store.load_selected_resume_id()
        if not selected_resume_id:
            return {"status": "skipped", "reason": "resume_not_selected", "message": "Select a hh.ru resume before syncing the profile."}
        if not self.state_path.exists():
            return {"status": "skipped", "reason": "login_required", "message": "hh.ru login is required before syncing the selected resume."}
        bootstrap_profile = False
        if preferences is None:
            preferences = UserPreferences()
            bootstrap_profile = True
        if anamnesis is None:
            anamnesis = Anamnesis()
            bootstrap_profile = True

        payload: dict[str, Any]
        relogin_attempted = False
        try:
            payload = asyncio.run(self._fetch_selected_resume(selected_resume_id))
        except RuntimeError as exc:
            if "login_required" not in str(exc):
                raise
            relogin_attempted = True
            login_result = run_hh_login(self.store.project_root)
            if login_result.get("status") != "completed":
                return {
                    "status": "skipped",
                    "reason": "login_required",
                    "message": str(login_result.get("message") or "hh.ru login is required before syncing the selected resume."),
                    "login_result": login_result,
                }
            self.state_path = self.store.hh_state_path
            try:
                payload = asyncio.run(self._fetch_selected_resume(selected_resume_id))
            except Exception as exc:  # noqa: BLE001
                debug_artifact = self.store.save_debug_artifact(
                    "hh-resume-sync-error",
                    {"resume_id": selected_resume_id, "error": str(exc), "relogin_attempted": True},
                    extension="json",
                    subdir="hh",
                )
                return {
                    "status": "failed",
                    "reason": "fetch_error",
                    "message": f"Failed to refresh the selected hh.ru resume after relogin: {exc}",
                    "debug_artifact": debug_artifact,
                }
        except Exception as exc:  # noqa: BLE001
            debug_artifact = self.store.save_debug_artifact(
                "hh-resume-sync-error",
                {"resume_id": selected_resume_id, "error": str(exc), "relogin_attempted": relogin_attempted},
                extension="json",
                subdir="hh",
            )
            return {
                "status": "failed",
                "reason": "fetch_error",
                "message": f"Failed to refresh the selected hh.ru resume: {exc}",
                "debug_artifact": debug_artifact,
            }

        extracted = extract_resume_profile(str(payload.get("text") or ""), str(payload.get("html") or ""), preferences, anamnesis)
        updated_preferences, updated_anamnesis, changes = apply_resume_profile_sync(preferences, anamnesis, extracted)
        if changes or bootstrap_profile:
            self.store.save_preferences(updated_preferences)
            self.store.save_anamnesis(updated_anamnesis)

        state_patch = {
            "last_resume_sync_at": utc_now_iso(),
            "last_resume_sync_status": "updated" if changes else "no_changes",
            "last_resume_sync_resume_id": selected_resume_id,
            "last_resume_sync_title": str(extracted.get("headline") or ""),
            "last_resume_sync_extracted": extracted,
            "last_resume_sync_page_url": str(payload.get("page_url") or ""),
            "last_resume_sync_page_title": str(payload.get("title") or ""),
            "last_resume_sync_text_length": int(len(str(payload.get("text") or ""))),
            "last_resume_sync_html_length": int(len(str(payload.get("html") or ""))),
            "last_resume_sync_change_count": len(changes),
            "last_resume_sync_bootstrap_profile": bootstrap_profile,
            "last_resume_sync_message": (
                f"Синхронизация профиля обновила поля: {len(changes)}."
                if changes
                else "Синхронизация профиля завершена, новых изменений не найдено."
            ),
        }
        self.store.update_dashboard_state(state_patch)
        self.store.record_event(
            "hh-resume-sync",
            state_patch["last_resume_sync_message"],
            details={"resume_id": selected_resume_id, "changes": changes, "extracted": extracted},
        )
        return {
            "status": "updated" if changes else "no_changes",
            "reason": "profile_synced",
            "message": state_patch["last_resume_sync_message"],
            "resume_id": selected_resume_id,
            "resume_title": str(extracted.get("headline") or ""),
            "changes": changes,
            "extracted": extracted,
            "page_url": str(payload.get("page_url") or ""),
            "relogin_attempted": relogin_attempted,
            "bootstrap_profile": bootstrap_profile,
        }

    async def _fetch_selected_resume(self, resume_id: str) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        state_payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        resume_url = self._resume_url_for(resume_id)
        async with async_playwright() as playwright:
            browser = await launch_chromium_resilient(playwright, headless=True)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1100},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                storage_state=state_payload or None,
            )
            page = await context.new_page()
            await page.goto(resume_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            if "login" in page.url:
                raise RuntimeError("login_required")
            await self._expand_resume_sections(page)
            await page.wait_for_timeout(700)
            payload = {
                "page_url": page.url,
                "title": await page.title(),
                "text": await page.inner_text("body"),
                "html": await page.content(),
            }
            await context.close()
            await browser.close()
            return payload

    async def _expand_resume_sections(self, page) -> None:
        selectors = (
            "button:has-text('Показать полностью')",
            "button:has-text('Развернуть')",
            "button:has-text('Показать больше')",
            "button:has-text('Подробнее')",
            "button:has-text('Еще')",
        )
        for selector in selectors:
            try:
                buttons = await page.query_selector_all(selector)
            except Exception:  # noqa: BLE001
                continue
            for button in buttons[:10]:
                try:
                    await button.click(timeout=1500)
                    await page.wait_for_timeout(150)
                except Exception:  # noqa: BLE001
                    continue

    def _resume_url_for(self, resume_id: str) -> str:
        selected = next((item for item in self.store.load_hh_resumes() if str(item.get("resume_id") or "") == resume_id), None)
        if selected and str(selected.get("url") or "").strip():
            return str(selected.get("url") or "").strip()
        return f"https://hh.ru/resume/{resume_id}"
