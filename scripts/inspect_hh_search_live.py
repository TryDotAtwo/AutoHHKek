from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from autohhkek.services.playwright_browser import launch_chromium_resilient
from logic.vacancy_parser import build_resume_search_url, get_total_vacancies


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ROOT = PROJECT_ROOT / ".autohhkek" / "accounts" / "hh-2512a1360b39"
STATE_PATH = ACCOUNT_ROOT / "session" / "hh_state.json"
OUT_DIR = PROJECT_ROOT / ".agent_workspace"


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state_payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    cookies = list(state_payload.get("cookies") or [])
    url = build_resume_search_url(
        "07687234ff0dd3ea5f0039ed1f47594655564f",
        {"remote_work": "1"},
    )
    async with async_playwright() as playwright:
        browser = await launch_chromium_resilient(playwright, headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1100},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        stats = await page.evaluate(
            """
            () => {
              const allAnchors = Array.from(document.querySelectorAll("a"));
              const vacancyAnchors = allAnchors
                .filter((item) => /\\/vacancy\\/\\d+/.test(item.getAttribute("href") || ""))
                .map((item) => ({
                  href: item.href,
                  text: (item.textContent || "").replace(/\\s+/g, " ").trim(),
                }))
                .slice(0, 25);
              return {
                url: location.href,
                title: document.title,
                body_excerpt: (document.body?.innerText || "").slice(0, 4000),
                serp_item_count: document.querySelectorAll("[data-qa='serp-item']").length,
                serp_title_count: document.querySelectorAll("a[data-qa='serp-item__title']").length,
                vacancy_href_count: allAnchors.filter((item) => /\\/vacancy\\/\\d+/.test(item.getAttribute("href") || "")).length,
                first_vacancy_anchors: vacancyAnchors,
              };
            }
            """
        )
        stats["total_available"] = await get_total_vacancies(page)
        screenshot_path = OUT_DIR / "inspect_hh_search_live.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        stats["screenshot"] = str(screenshot_path)
        output_path = OUT_DIR / "inspect_hh_search_live.json"
        output_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output_path)
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
