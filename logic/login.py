import asyncio
from playwright.async_api import async_playwright, BrowserContext
import json
import os
from typing import Optional

async def setup_browser_and_login(playwright) -> Optional[BrowserContext]:
    """Настройка браузера и логин (с сохранением state)."""
    browser = await playwright.chromium.launch(headless=False)  # headless=True для без GUI
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
    
    # Всегда загружаем cookies, если файл существует
    if os.path.exists("hh_state.json"):
        storage_state = json.loads(open("hh_state.json").read())
        await context.add_cookies(storage_state.get("cookies", []))
        print("Загружен сохраненный state.")
    
    page = await context.new_page()
    await page.goto("https://hh.ru/", wait_until="domcontentloaded", timeout=60000)
    print("Страница загружена.")
    
    # Проверяем наличие кнопки логина
    login_link = await page.query_selector("a[data-qa='login']")
    if login_link:
        print("Кнопка логина найдена. Кликаем на неё...")
        await login_link.click()
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        print("Откройте форму логина в браузере и завершите вход вручную.")
        input("После успешного логина нажмите Enter в этой консоли...")
        print("Логин подтверждён.")
    else:
        print("Кнопка логина не найдена - уже залогинены.")
    
    # Сохраняем/обновляем state всегда
    cookies = await context.cookies()
    storage_state = {"cookies": cookies}
    with open("hh_state.json", "w") as f:
        json.dump(storage_state, f)
    print("State сохранен в hh_state.json")
    
    return context
