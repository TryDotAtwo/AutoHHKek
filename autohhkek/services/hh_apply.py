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
    "[data-qa='vacancy-response-letter-informer'] textarea[name='text']",
    "[data-qa='vacancy-response-letter-informer'] textarea",
    "[data-qa='textarea-native-wrapper'] textarea[name='text']",
    "[data-qa='textarea-native-wrapper'] textarea",
    "[data-qa='vacancy-response-popup'] textarea",
    "[data-qa='vacancy-response-popup'] [contenteditable='true']",
    "[role='dialog'] textarea",
    "[role='dialog'] [contenteditable='true']",
    "textarea[name='text']",
    "textarea",
]

SUBMIT_SELECTORS = [
    "button[data-qa='vacancy-response-letter-submit']",
    "button[data-qa='vacancy-response-submit-popup']",
    "button[data-qa='submit-vacancy-response']",
    "button[type='submit'][form^='cover-letter-']",
    "[data-qa='vacancy-response-letter-informer'] button[type='submit']",
    "button:has-text('Отправить')",
    "button:has-text('Откликнуться')",
    "button:has-text('Продолжить')",
    "button:has-text('Далее')",
]

QUESTIONNAIRE_SELECTORS = [
    "[data-qa='task-body']",
    "[data-qa='vacancy-response-popup'] [data-qa*='question']",
    "[data-qa='vacancy-response-popup'] [data-qa*='task']",
    "[data-qa='vacancy-response-popup'] button:has-text('Продолжить')",
    "[data-qa='vacancy-response-popup'] button:has-text('Далее')",
]

POPUP_SELECTORS = [
    "[data-qa='vacancy-response-popup']",
    "[data-qa='vacancy-response-letter-informer']",
    "[role='dialog']",
]

ALREADY_APPLIED_TOKENS = (
    "вы уже откликнулись",
    "отклик отправлен",
    "вы откликались",
)

SUCCESS_TOKENS = (
    "отклик успешно",
    "отклик отправлен",
    "вы уже откликнулись",
    "вы откликались",
    "резюме доставлено",
)

QUESTIONNAIRE_TOKENS = (
    "ответьте на вопросы",
    "необходимо пройти тест",
    "пройдите тест",
    "заполните анкету",
    "опрос",
)

LETTER_ERROR_TOKENS = (
    "произошла ошибка, попробуйте ещё раз",
    "не удалось отправить",
    "ошибка",
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


def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    return any(token in normalized for token in tokens)


async def _has_questionnaire(page) -> bool:
    for selector in QUESTIONNAIRE_SELECTORS:
        try:
            locator = page.locator(selector)
            if await locator.count() and await locator.first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue

    text = (await _page_text(page)).lower()
    return any(token in text for token in QUESTIONNAIRE_TOKENS)


async def _launch_apply_browser(playwright):
    return await launch_chromium_resilient(playwright, headless=False)


async def _wait_for_any_visible(page, selectors: list[str], timeout_ms: int = 5000) -> tuple[bool, str]:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
    while asyncio.get_event_loop().time() < deadline:
        locator, selector = await _first_visible(page, selectors)
        if locator:
            return True, selector
        await page.wait_for_timeout(150)
    return False, ""


async def _fill_cover_letter(control, text: str) -> tuple[bool, str, int]:
    stripped = text.strip()
    if not stripped:
        return True, "not_requested", 0

    try:
        tag_name = await control.evaluate("(node) => node.tagName.toLowerCase()")
    except Exception:  # noqa: BLE001
        tag_name = ""

    try:
        is_contenteditable = await control.evaluate(
            "(node) => node.getAttribute('contenteditable') === 'true'"
        )
    except Exception:  # noqa: BLE001
        is_contenteditable = False

    if tag_name == "textarea":
        try:
            await control.click()
            await control.fill(stripped)
            current_value = await control.input_value()
            inserted_len = len(current_value.strip())
            if inserted_len > 0:
                return True, "used", inserted_len
            return False, "fill_failed", 0
        except Exception:  # noqa: BLE001
            return False, "fill_failed", 0

    if is_contenteditable:
        try:
            await control.click()
            await control.fill(stripped)
            current_text = (await control.text_content()) or ""
            inserted_len = len(current_text.strip())
            if inserted_len > 0:
                return True, "used", inserted_len
            return False, "fill_failed", 0
        except Exception:  # noqa: BLE001
            return False, "fill_failed", 0

    try:
        await control.click()
        await control.fill(stripped)
        current_text = (await control.text_content()) or ""
        inserted_len = len(current_text.strip())
        if inserted_len > 0:
            return True, "used", inserted_len
    except Exception:  # noqa: BLE001
        pass

    return False, "unsupported_editor", 0


async def _detect_letter_error(page) -> bool:
    text = (await _page_text(page)).lower()
    return any(token in text for token in LETTER_ERROR_TOKENS)


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
                    "phase": "before_apply_click",
                }

            apply_button, apply_selector = await _first_visible(page, APPLY_SELECTORS)
            if not apply_button:
                if await _has_questionnaire(page):
                    return {
                        "status": "questionnaire_required",
                        "message": "После открытия вакансии обнаружена анкета или тест. Нужен специальный сценарий прохождения.",
                        "vacancy_url": page.url,
                        "phase": "before_apply_click",
                    }
                return {
                    "status": "needs_repair",
                    "message": "Не найдена кнопка отклика на странице вакансии.",
                    "vacancy_url": page.url,
                    "phase": "before_apply_click",
                }

            await apply_button.click()
            await page.wait_for_timeout(1200)

            popup_visible, popup_selector = await _wait_for_any_visible(page, POPUP_SELECTORS, timeout_ms=5000)

            if not popup_visible:
                text_after_click = await _page_text(page)

                if _has_any_token(text_after_click, SUCCESS_TOKENS):
                    return {
                        "status": "completed",
                        "message": "После нажатия кнопки отклика hh.ru сразу подтвердил отправку отклика.",
                        "vacancy_url": page.url,
                        "apply_selector": apply_selector,
                        "phase": "after_apply_click_direct_success",
                    }

                if await _has_questionnaire(page):
                    return {
                        "status": "questionnaire_required",
                        "message": "После нажатия кнопки отклика обнаружена анкета или тест.",
                        "vacancy_url": page.url,
                        "apply_selector": apply_selector,
                        "phase": "after_apply_click",
                    }

            if resume_id:
                resume_control, resume_selector = await _first_visible(page, RESUME_SELECTORS)
                if resume_control:
                    try:
                        tag_name = await resume_control.evaluate("(node) => node.tagName.toLowerCase()")
                        if tag_name == "select":
                            await resume_control.select_option(value=resume_id)
                        else:
                            try:
                                await resume_control.click()
                                option = page.locator(
                                    f"[data-resume-id='{resume_id}'], [value='{resume_id}']"
                                ).first
                                if await option.count() and await option.is_visible():
                                    await option.click()
                            except Exception:  # noqa: BLE001
                                pass
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    resume_selector = ""
            else:
                resume_selector = ""

            cover_selector = ""
            cover_letter_status = "not_requested"
            inserted_cover_letter_len = 0
            cover_letter_present = bool(cover_letter.strip())

            if cover_letter_present:
                cover_input, cover_selector = await _first_visible(page, COVER_LETTER_SELECTORS)
                if not cover_input:
                    return {
                        "status": "needs_follow_up",
                        "message": "Форма отклика открылась, но поле сопроводительного письма не найдено. Автоотправка остановлена.",
                        "vacancy_url": page.url,
                        "apply_selector": apply_selector,
                        "resume_selector": resume_selector,
                        "cover_letter_selector": cover_selector,
                        "cover_letter_status": "not_available",
                        "popup_selector": popup_selector,
                        "phase": "cover_letter_detection",
                    }

                fill_ok, cover_letter_status, inserted_cover_letter_len = await _fill_cover_letter(
                    cover_input,
                    cover_letter,
                )
                if not fill_ok:
                    return {
                        "status": "needs_follow_up",
                        "message": "Поле сопроводительного письма найдено, но текст не был подтверждён в форме. Автоотправка остановлена.",
                        "vacancy_url": page.url,
                        "apply_selector": apply_selector,
                        "resume_selector": resume_selector,
                        "cover_letter_selector": cover_selector,
                        "cover_letter_status": cover_letter_status,
                        "popup_selector": popup_selector,
                        "phase": "cover_letter_fill",
                    }

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
                        "popup_selector": popup_selector,
                        "phase": "before_submit",
                    }
                return {
                    "status": "needs_follow_up",
                    "message": "Форма отклика открылась, но финальная кнопка отправки не найдена.",
                    "vacancy_url": page.url,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "apply_selector": apply_selector,
                    "cover_letter_status": cover_letter_status,
                    "popup_selector": popup_selector,
                    "phase": "before_submit",
                }

            if cover_letter_present and cover_letter_status != "used":
                return {
                    "status": "needs_follow_up",
                    "message": "Сопроводительное письмо было задано, но не подтверждено в форме. Отправка остановлена.",
                    "vacancy_url": page.url,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "apply_selector": apply_selector,
                    "submit_selector": submit_selector,
                    "cover_letter_status": cover_letter_status,
                    "popup_selector": popup_selector,
                    "phase": "submit_guard",
                }

            await submit_button.click()
            await page.wait_for_timeout(1800)

            final_text = await _page_text(page)
            final_text_lc = final_text.lower()

            if await _detect_letter_error(page):
                return {
                    "status": "needs_follow_up",
                    "message": "hh.ru показал ошибку при отправке сопроводительного письма. Нужен повтор или отдельная правка сценария.",
                    "vacancy_url": page.url,
                    "apply_selector": apply_selector,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "submit_selector": submit_selector,
                    "cover_letter_status": "submit_error",
                    "inserted_cover_letter_len": inserted_cover_letter_len,
                    "popup_selector": popup_selector,
                    "phase": "after_submit",
                }

            if _has_any_token(final_text_lc, SUCCESS_TOKENS):
                return {
                    "status": "completed",
                    "message": "Отклик отправлен на hh.ru.",
                    "vacancy_url": page.url,
                    "apply_selector": apply_selector,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "submit_selector": submit_selector,
                    "cover_letter_status": cover_letter_status,
                    "inserted_cover_letter_len": inserted_cover_letter_len,
                    "popup_selector": popup_selector,
                    "phase": "after_submit",
                }

            if await _has_questionnaire(page):
                return {
                    "status": "questionnaire_required",
                    "message": "После нажатия кнопки отправки hh.ru перевёл отклик в анкету или тест.",
                    "vacancy_url": page.url,
                    "apply_selector": apply_selector,
                    "resume_selector": resume_selector,
                    "cover_letter_selector": cover_selector,
                    "submit_selector": submit_selector,
                    "cover_letter_status": cover_letter_status,
                    "inserted_cover_letter_len": inserted_cover_letter_len,
                    "popup_selector": popup_selector,
                    "phase": "after_submit",
                }

            return {
                "status": "completed_without_confirmation",
                "message": "Кнопка отправки нажата, но hh.ru не показал явное подтверждение результата.",
                "vacancy_url": page.url,
                "apply_selector": apply_selector,
                "resume_selector": resume_selector,
                "cover_letter_selector": cover_selector,
                "submit_selector": submit_selector,
                "cover_letter_status": cover_letter_status,
                "inserted_cover_letter_len": inserted_cover_letter_len,
                "popup_selector": popup_selector,
                "phase": "after_submit",
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