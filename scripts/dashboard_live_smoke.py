from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autohhkek.services.playwright_browser import launch_chromium_resilient


BASE_URL = "http://127.0.0.1:8768"
ARTIFACTS_DIR = Path("artifacts") / "live_smoke"


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
    return cleaned.strip("_") or "item"


async def _scroll_container(locator) -> dict[str, float]:
    before = await locator.evaluate("(node) => node.scrollTop")
    await locator.evaluate("(node) => { node.scrollTop = node.scrollHeight; }")
    await locator.page.wait_for_timeout(250)
    bottom = await locator.evaluate("(node) => node.scrollTop")
    await locator.evaluate("(node) => { node.scrollTop = 0; }")
    await locator.page.wait_for_timeout(250)
    after = await locator.evaluate("(node) => node.scrollTop")
    return {"before": before, "bottom": bottom, "after_reset": after}


async def _click_if_visible(page, selector: str, label: str, results: list[dict[str, object]]) -> bool:
    locator = page.locator(selector)
    if not await locator.count():
        return False
    target = locator.first
    if not await target.is_visible():
        return False
    await target.click()
    await page.wait_for_timeout(500)
    results.append({"action": "click", "target": label, "selector": selector, "status": "ok"})
    return True


async def _click_with_retry(locator, label: str, results: list[dict[str, object]], attempts: int = 4) -> None:
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            await locator.scroll_into_view_if_needed()
            await locator.click(timeout=8000)
            results.append({"action": "click", "target": label, "status": "ok", "attempt": attempt})
            return
        except Exception as exc:  # pragma: no cover - live UI diagnostics
            last_error = str(exc)
            await locator.page.wait_for_timeout(1200)
    results.append({"action": "click", "target": label, "status": "failed", "error": last_error})
    raise RuntimeError(f"{label}: {last_error}")


async def main() -> int:
    from playwright.async_api import async_playwright

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ARTIFACTS_DIR / "report.json"
    console_events: list[dict[str, str]] = []
    page_errors: list[str] = []
    response_errors: list[dict[str, object]] = []
    action_results: list[dict[str, object]] = []
    screenshots: list[str] = []

    async with async_playwright() as playwright:
        browser = await launch_chromium_resilient(playwright, headless=False)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1100},
            locale="ru-RU",
        )
        page = await context.new_page()

        page.on("console", lambda msg: console_events.append({"type": msg.type, "text": msg.text}))
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "response",
            lambda response: response_errors.append({"status": response.status, "url": response.url})
            if response.status >= 400 and "/api/" in response.url
            else None,
        )

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        await page.locator("#tabbar").wait_for(timeout=20000)

        workspace = page.locator(".workspace")
        chat_log = page.locator("#chat-log")

        resize_handle = page.locator("#layout-resizer")
        if await resize_handle.count():
            box = await resize_handle.bounding_box()
            if box:
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await page.mouse.down()
                await page.mouse.move(box["x"] - 120, box["y"] + box["height"] / 2, steps=8)
                await page.mouse.up()
                await page.wait_for_timeout(400)
                action_results.append({"action": "drag", "target": "layout-resizer", "status": "ok"})

        if await workspace.count():
            await workspace.evaluate("(node) => { node.scrollTop = 900; }")
            await page.wait_for_timeout(6500)
            scroll_after_refresh = await workspace.evaluate("(node) => node.scrollTop")
            action_results.append(
                {
                    "action": "assert_scroll_preserved",
                    "target": "workspace",
                    "status": "ok" if scroll_after_refresh > 600 else "failed",
                    "value": scroll_after_refresh,
                }
            )

        tab_specs = [
            ("agent", "agent"),
            ("vacancies", "vacancies"),
            ("vacancy", "vacancy"),
            ("activity", "activity"),
        ]
        for index, (tab_id, label) in enumerate(tab_specs, start=1):
            button = page.locator(f'.tab-button[data-tab="{tab_id}"]')
            await _click_with_retry(button, f"tab:{tab_id}", action_results)
            await page.wait_for_timeout(700)
            screenshot_path = ARTIFACTS_DIR / f"tab_{index}_{_safe_name(label)}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            screenshots.append(str(screenshot_path.resolve()))
            action_results.append({"action": "open_tab", "target": tab_id, "status": "ok"})

            if tab_id == "agent":
                chips = page.locator("#chat-quick-actions .button")
                for chip_index in range(min(await chips.count(), 4)):
                    chip = chips.nth(chip_index)
                    chip_label = (await chip.inner_text()).strip()
                    await _click_with_retry(chip, f"quick-action:{chip_label}", action_results, attempts=3)
                    await page.wait_for_timeout(1200)
                    action_results.append({"action": "click_quick_action", "target": chip_label, "status": "ok"})
            elif tab_id == "vacancies":
                await _scroll_container(workspace)
                cards = page.locator("[data-open-vacancy]")
                if await cards.count():
                    await _click_with_retry(cards.first, "open_first_vacancy_card", action_results)
                    await page.wait_for_timeout(800)
                    action_results.append({"action": "open_first_vacancy_card", "target": "vacancy-card", "status": "ok"})
            elif tab_id == "vacancy":
                await _click_if_visible(page, "summary", "vacancy-description-summary", action_results)
                textarea = page.locator("#cover-letter-input")
                if await textarea.count():
                    await textarea.fill("Здравствуйте! Это live smoke test отклика на русском языке.")
                    action_results.append({"action": "fill", "target": "cover-letter-input", "status": "ok"})
                await _click_if_visible(page, "#save-cover-letter", "save-cover-letter", action_results)
                await _click_if_visible(page, "#build-apply-plan", "build-apply-plan", action_results)
                await _click_if_visible(page, "#apply-submit", "apply-submit", action_results)
            elif tab_id == "activity":
                await _scroll_container(workspace)

        if await chat_log.count():
            scroll_info = await _scroll_container(chat_log)
            action_results.append({"action": "scroll", "target": "chat-log", "status": "ok", "details": scroll_info})

        final_screenshot = ARTIFACTS_DIR / "final_full.png"
        await page.screenshot(path=str(final_screenshot), full_page=True)
        screenshots.append(str(final_screenshot.resolve()))

        await context.close()
        await browser.close()

    severe_console = [item for item in console_events if item["type"] in {"error", "warning"}]
    api_failures = [item for item in response_errors if item["status"] >= 400]
    failed_actions = [item for item in action_results if item.get("status") != "ok"]
    report = {
        "base_url": BASE_URL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": {
            "console_events": len(console_events),
            "severe_console": len(severe_console),
            "page_errors": len(page_errors),
            "api_failures": len(api_failures),
            "actions": len(action_results),
            "failed_actions": len(failed_actions),
        },
        "console_events": console_events,
        "page_errors": page_errors,
        "api_failures": api_failures,
        "action_results": action_results,
        "screenshots": screenshots,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False))
    print(str(report_path.resolve()))
    return 1 if severe_console or page_errors or api_failures or failed_actions else 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
