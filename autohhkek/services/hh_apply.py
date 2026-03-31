from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from autohhkek.domain.models import utc_now_iso
from autohhkek.services.playwright_browser import launch_chromium_resilient
from autohhkek.services.storage import WorkspaceStore

APPLY_SELECTORS = [
    "a[data-qa='vacancy-response-link-top']",
    "button[data-qa='vacancy-response-button']",
    "button:has-text('Откликнуться')",
]

RESUME_SELECTORS = [
    "[data-qa='resume-selector']",
    "select[name='resume']",
]

COVER_LETTER_SELECTORS = [
    "textarea[data-qa='vacancy-response-popup-form-letter-input']",
    "textarea[name='cover_letter']",
    "textarea",
]

SUBMIT_SELECTORS = [
    "button[data-qa='vacancy-response-submit-popup']",
    "button[data-qa='submit-vacancy-response']",
    "button:has-text('Отправить')",
    "button:has-text('Откликнуться')",
    "button:has-text('Продолжить')",
    "button:has-text('Далее')",
]

QUESTIONNAIRE_SELECTORS = [
    "form[data-qa='vacancy-response-popup-form']",
    "[data-qa='task-body']",
    "[data-qa='vacancy-response-popup'] input",
    "[data-qa='vacancy-response-popup'] textarea",
    "button:has-text('Продолжить')",
    "button:has-text('Далее')",
]

ALREADY_APPLIED_TOKENS = (
    "вы уже откликнулись",
    "отклик отправлен",
    "вы откликались",
)

QUESTIONNAIRE_TOKENS = (
    "анкета",
    "тест",
    "опрос",
    "ответьте на вопросы",
    "пройдите опрос",
)


def _load_storage_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


async def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if await locator.count() and await locator.first.is_visible():
                return locator.first, selector
        except Exception:  # noqa: BLE001
            continue
    return None, ""


async def _page_text(page) -> str:
    try:
        return " ".join((await page.locator("body").inner_text()).split())
    except Exception:  # noqa: BLE001
        return ""


async def _has_questionnaire(page) -> bool:
    for selector in QUESTIONNAIRE_SELECTORS:
        try:
            locator = page.locator(selector)
            if await locator.count():
                return True
        except Exception:  # noqa: BLE001
            continue
    text = (await _page_text(page)).lower()
    return any(token in text for token in QUESTIONNAIRE_TOKENS)


def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    return any(token in normalized for token in tokens)


async def _launch_apply_browser(playwright):
    return await launch_chromium_resilient(playwright, headless=False)


async def _run_apply_flow(
    *,
    state_path: Path,
    vacancy_url: str,
    resume_id: str,
    cover_letter: str,
) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        return {
            "status": "failed",
            "message": "Playwright is not installed.",
            "error": str(exc),
        }

    storage_state = _load_storage_state(state_path)
    if not storage_state:
        return {
            "status": "needs_login",
            "message": "hh_state.json is missing. Login to hh.ru first.",
        }

    async with async_playwright() as playwright:
        browser = await _launch_apply_browser(playwright)
        context = await browser.new_context(storage_state=storage_state, locale="ru-RU")
        page = await context.new_page()
        try:
            await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)
            if "login" in page.url:
                return {
                    "status": "needs_login",
                    "message": "hh.ru session expired. Re-login is required before applying.",
                    "vacancy_url": page.url,
                }
            initial_text = await _page_text(page)
            if _has_any_token(initial_text, ALREADY_APPLIED_TOKENS):
                return {
                    "status": "already_applied",
                    "message": "По этой вакансии отклик уже был отправлен ранее.",
                    "vacancy_url": page.url,
                }

            apply_button, apply_selector = await _first_visible(page, APPLY_SELECTORS)
            if not apply_button:
                if await _has_questionnaire(page):
                    return {
                        "status": "questionnaire_required",
                        "message": "После открытия вакансии обнаружена анкета или тест. Нужен специальный сценарий прохождения.",
                        "vacancy_url": page.url,
                    }
                return {
                    "status": "needs_repair",
                    "message": "Не нашёл кнопку отклика на странице вакансии и не смог подтвердить, что отклик уже отправлен.",
                    "vacancy_url": page.url,
                }

            await apply_button.click()
            await page.wait_for_timeout(1200)
            after_click_text = await _page_text(page)
            if _has_any_token(after_click_text, ALREADY_APPLIED_TOKENS):
                return {
                    "status": "already_applied",
                    "message": "После открытия формы выяснилось, что по вакансии уже есть отклик.",
                    "vacancy_url": page.url,
                    "apply_selector": apply_selector,
                }

            if resume_id:
                resume_control, resume_selector = await _first_visible(page, RESUME_SELECTORS)
                if resume_control:
                    tag_name = await resume_control.evaluate("(node) => node.tagName.toLowerCase()")
                    if tag_name == "select":
                        await resume_control.select_option(value=resume_id)
                    else:
                        try:
                            await resume_control.click()
                            option = page.locator(f"[data-resume-id='{resume_id}'], [value='{resume_id}']").first
                            if await option.count():
                                await option.click()
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    resume_selector = ""
            else:
                resume_selector = ""

            cover_selector = ""
            cover_letter_status = "used" if cover_letter.strip() else "not_requested"
            if cover_letter.strip():
                cover_input, cover_selector = await _first_visible(page, COVER_LETTER_SELECTORS)
                if cover_input:
                    await cover_input.fill(cover_letter.strip())
                else:
                    cover_letter_status = "not_available"

            submit_button, submit_selector = await _first_visible(page, SUBMIT_SELECTORS)
            if not submit_button:
                if await _has_questionnaire(page):
                    return {
                        "status": "questionnaire_required",
                        "message": "Отклик упёрся в анкету или тест на hh.ru. Нужен сценарий автопрохождения.",
                        "vacancy_url": page.url,
                        "resume_selector": resume_selector,
                        "cover_letter_selector": cover_selector,
                        "apply_selector": apply_selector,
                        "cover_letter_status": cover_letter_status,
                    }
                return {
                    "status": "needs_follow_up",
                    "message": "Форма отклика открылась, но финальную отправку подтвердить не удалось.",
                    "vacancy_url": page.url,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "apply_selector": apply_selector,
                    "cover_letter_status": cover_letter_status,
                }

            await submit_button.click()
            await page.wait_for_timeout(1500)
            final_text = await _page_text(page)
            if _has_any_token(final_text, ALREADY_APPLIED_TOKENS) or "отклик успешно" in final_text.lower():
                status = "completed_without_cover_letter" if cover_letter_status == "not_available" else "completed"
                message = (
                    "Отклик отправлен, но hh.ru не дал приложить сопроводительное письмо."
                    if status == "completed_without_cover_letter"
                    else "Отклик отправлен или дошёл до завершающего шага на hh.ru."
                )
                return {
                    "status": status,
                    "message": message,
                    "vacancy_url": page.url,
                    "apply_selector": apply_selector,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "submit_selector": submit_selector,
                    "cover_letter_status": cover_letter_status,
                }

            return {
                "status": "completed_without_confirmation" if cover_letter_status == "not_available" else "completed",
                "message": (
                    "Кнопка отправки нажата, но hh.ru не дал приложить сопроводительное письмо и не показал явное подтверждение."
                    if cover_letter_status == "not_available"
                    else "Кнопка отправки нажата, ждём подтверждение от hh.ru."
                ),
                "vacancy_url": page.url,
                "apply_selector": apply_selector,
                "resume_selector": resume_selector,
                "cover_letter_selector": cover_selector,
                "submit_selector": submit_selector,
                "cover_letter_status": cover_letter_status,
            }
        finally:
            state_payload = await context.storage_state()
            state_path.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            await context.close()
            await browser.close()


def run_hh_apply(
    *,
    project_root: Path,
    vacancy_url: str,
    resume_id: str = "",
    cover_letter: str = "",
) -> dict[str, Any]:
    if not vacancy_url.strip():
        return {
            "status": "failed",
            "message": "Не передан URL вакансии.",
        }
    state_path = WorkspaceStore(Path(project_root).resolve()).hh_state_path
    try:
        return asyncio.run(
            _run_apply_flow(
                state_path=state_path,
                vacancy_url=vacancy_url.strip(),
                resume_id=resume_id.strip(),
                cover_letter=cover_letter,
            )
        )
    except PermissionError as exc:
        return {
            "status": "needs_repair",
            "message": f"Failed to start Playwright browser for hh.ru apply flow: {exc}",
            "error": str(exc),
            "reason": "playwright_launch_denied",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "message": f"Unexpected hh.ru apply flow error: {exc}",
            "error": str(exc),
            "reason": "apply_flow_exception",
        }


def apply_to_vacancy(store, *, vacancy_id: str, cover_letter_override: str = "") -> dict[str, Any]:
    vacancy_key = str(vacancy_id or "").strip()
    vacancies = {item.vacancy_id: item for item in store.load_vacancies()}
    target = vacancies.get(vacancy_key)
    if not target:
        raise RuntimeError("vacancy_id was not found in the current vacancy cache.")
    selected_resume_id = store.load_selected_resume_id()
    cover_letter = cover_letter_override.strip() or store.load_cover_letter_draft(vacancy_key)
    result = run_hh_apply(
        project_root=store.project_root,
        vacancy_url=target.url,
        resume_id=selected_resume_id,
        cover_letter=cover_letter,
    )
    if hasattr(store, "save_vacancy_feedback_item"):
        store.save_vacancy_feedback_item(
            vacancy_key,
            {
                "last_apply_status": str(result.get("status") or ""),
                "last_apply_message": str(result.get("message") or ""),
                "last_apply_at": utc_now_iso(),
            },
        )
    return {
        "vacancy_id": vacancy_key,
        "vacancy_url": target.url,
        "selected_resume_id": selected_resume_id,
        "cover_letter_used": bool(cover_letter.strip()),
        "result": result,
    }
