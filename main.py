import subprocess
import sys
import random
import asyncio
import concurrent.futures
import os
import json
import re
from tqdm import tqdm

def install_and_import(package):
    """Устанавливает пакет, если не установлен, и импортирует его."""
    try:
        __import__(package.replace('-', '_'))  # Для импорта без дефиса
    except ImportError:
        pip_package = package  # Для pip с дефисом, если package имеет дефис
        print(f"Устанавливаю {pip_package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_package])
        __import__(package.replace('-', '_'))

# Проверяем и устанавливаем основные внешние библиотеки
install_and_import("playwright")
subprocess.check_call(["playwright", "install"])  # Устанавливаем браузеры для Playwright

install_and_import("requests")
install_and_import("g4f")
install_and_import("playwright-stealth")  # Исправлено: с дефисом для pip
install_and_import("tqdm")

# Теперь безопасные импорты
import time
from logic.login import setup_browser_and_login
from logic.resume_parser import parse_resume
from logic.vacancy_parser import search_vacancies
from logic.llm_handler import robust_llm_query
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import Error as PlaywrightError  # Для обработки closed errors
from playwright_stealth import Stealth  # Используем Stealth класс для async
from typing import Dict, Any, Tuple

# Конфигурация
RESUME_ID = "[Ваш ID резюме]"  # Ваш ID резюме
USER_WISHES = "[Ваиш пожелания]"  # Пожелания пользователя
USER_PROFILE = ""  # Будет обновлено после парсинга

PROMPTS = {
    "process": """
    На основе моего профиля: {profile}

    Моё имя: [Ваше имя]

    Дополнительные пожелания (используй только для оценки релевантности и решения, стоит ли откликаться, но НЕ включай их в сопроводительное письмо): {user_wishes}

    Вакансия: {title}
    Описание: {description}

    Инструкции:
    1. Проанализируй мой профиль и текст вакансии.
    2. Определи релевантность вакансии по шкале от 0 до 10, учитывая мой опыт, навыки и дополнительные пожелания.
    3. Прими решение, стоит ли откликаться («Да» или «Нет»). Дополнительные пожелания учитываются только для оценки релевантности и решения, но не должны упоминаться в письме.
    4. Если какая-либо информация из дополнительных пожеланий уже содержится в моём профиле, не дублируй её в письме.
    5. Если в тексте вакансии есть прямое указание включить в сопроводительное письмо определённое слово, фразу или текст — обязательно включи это дословно.
    6. Сделай акцент на информации из моего профиля, включая ссылки (например, на публикации, бенчмарки, препринты), чтобы подчеркнуть мой опыт и достижения.
    7. Сгенерируй персонализированное, убедительное сопроводительное письмо (1–2 абзаца, на русском языке), которое отражает мою квалификацию, опыт и мотивацию. Обращение должно быть вежливым, без излишнего официоза.
    8. Обязательно придерживайся формата JSON. Не добавляй комментариев, Markdown или лишних полей.

    Ответь ТОЛЬКО в JSON формате:
    {{
        "relevance": "Оценка релевантности (0-10): ",
        "apply": "Да/Нет - стоит ли откликаться?",
        "letter": "Полный текст письма (1–2 абзаца, персонализированный, убедительный) на русском языке"
    }}
    """
}

MAGIC_NUMBERS = {
    "RELEVANCE_THRESHOLD": 7,
    "DESCRIPTION_MAX_LEN": 1000,  # Оставляем для совместимости, но не используем срез
    "CONCURRENCY_LIMIT": 5,  # Уменьшено для стабильности
    "PROCESS_INTERVAL": 3,  # Интервал между запуском новых задач в секундах
    "DEBUG_MODE": False,  # Включено для отладки
    "SCREENSHOT_DIR": "./screenshots"  # Директория для скриншотов
}

def debug_print(message: str):
    """Выводит сообщение только в debug режиме."""
    if MAGIC_NUMBERS["DEBUG_MODE"]:
        print(message)

async def handle_captcha(page: Page, title: str = "") -> bool:
    """Обрабатывает CAPTCHA: просит пользователя пройти её и подтверждает продолжение."""
    print(f"Обнаружена CAPTCHA{' для вакансии: ' + title if title else ''}! Пожалуйста, перейдите в открытый браузер, пройдите CAPTCHA вручную и нажмите Enter здесь, чтобы продолжить...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input)
    print("Продолжаем работу...")
    # Дополнительная проверка, что CAPTCHA пройдена (опционально)
    try:
        await page.wait_for_selector("text=Подтвердите, что вы не робот", timeout=2000)
        print("CAPTCHA всё ещё видна! Пожалуйста, пройдите её заново и нажмите Enter.")
        await loop.run_in_executor(None, input)
    except PlaywrightTimeoutError:
        pass  # CAPTCHA пройдена, ок
    return True

async def safe_page_operation(page: Page, operation: callable, *args, **kwargs) -> Any:
    """Безопасная обёртка для операций с page: check closed и catch errors."""
    try:
        if page.is_closed():
            raise PlaywrightError("Page is closed")
        return await operation(*args, **kwargs)
    except (PlaywrightError, PlaywrightTimeoutError) as e:
        debug_print(f"Page error in operation: {e}")
        return None

async def parse_vacancy_description(page: Page, title: str) -> str:
    """Парсит описание вакансии аналогично парсингу резюме: полный текст, очистка."""
    debug_print("Парсинг описания вакансии...")
    
    # Проверка на CAPTCHA
    captcha_result = await safe_page_operation(page, page.wait_for_selector, "text=Подтвердите, что вы не робот", timeout=5000)
    if captcha_result is not None:
        await handle_captcha(page, title)
    
    # Проверяем на ошибку страницы
    error_result = await safe_page_operation(page, page.wait_for_selector, "text=Произошла ошибка", timeout=5000)
    if error_result is not None:
        debug_print("Обнаружена ошибка страницы: 'Произошла ошибка. Попробуйте перезагрузить страницу.'")
    
    # Перезагрузка для очистки кэша
    reload_result = await safe_page_operation(page, page.reload, wait_until="domcontentloaded", timeout=30000)
    if reload_result is None:
        debug_print("Таймаут загрузки страницы вакансии, продолжаем с частичной загрузкой.")
        await page.wait_for_timeout(5000)
    
    # Получаем весь видимый текст (без разворачивания секций)
    full_raw_text = await safe_page_operation(page, page.inner_text, "body")
    if full_raw_text is None:
        return ""  # Fallback если page closed
    
    # debug_print(f"RAW FULL TEXT (первые 500 символов): {full_raw_text[:500]}...")
    
    # Очистка: удаляем текст ниже "Задайте вопрос работодателю" и выше названия вакансии
    # Находим позицию заголовка вакансии (предполагаем, что title известен)
    title_start = full_raw_text.find(title)
    if title_start == -1:
        # Fallback: ищем по паттерну заголовка вакансии
        title_match = re.search(r'class="vacancy-title".*?>([^<]+)<', full_raw_text, re.DOTALL | re.IGNORECASE)
        if title_match:
            title_start = title_match.start()
        else:
            title_start = 0  # Если не нашли, берём с начала
    
    # Находим позицию "Задайте вопрос работодателю"
    question_start = full_raw_text.find("Задайте вопрос работодателю")
    if question_start != -1:
        full_raw_text = full_raw_text[:question_start]
    
    # Обрезаем до/от заголовка
    if title_start > 0:
        full_raw_text = full_raw_text[title_start:]
    
    # Дополнительная очистка: удаляем cookie баннеры, чаты и т.д. (простой regex)
    unwanted_patterns = [
        r'Мы\s+используем файлы cookie.*?Понятно',
        r'Чаты.*?Поиск',
        r'^\s*$\n'  # Лишние пустые строки
    ]
    for pattern in unwanted_patterns:
        full_raw_text = re.sub(pattern, '', full_raw_text, flags=re.DOTALL | re.MULTILINE)
    
    # Удаляем лишние пробелы/переносы
    full_raw_text = re.sub(r'\n\s*\n', '\n', full_raw_text.strip())
    
    # Выводим очищенный текст после всех удалений
    # debug_print(f"CLEANED FULL TEXT (первые 500 символов): {full_raw_text[:500]}...")
    # debug_print(f"CLEANED FULL TEXT (полный): {full_raw_text}")
    
    return full_raw_text

async def process_vacancy(vacancy: Dict[str, str], context: BrowserContext, progress_bar: tqdm, progress_lock: asyncio.Lock, semaphore: asyncio.Semaphore) -> Dict[str, Any]:
    """Обрабатывает одну вакансию: LLM-оценка + генерация, отклик если подходит."""
    title = vacancy["title"]
    url = vacancy["url"]
    debug_print(f"🚀 Обрабатываю вакансию: {title} (URL: {url})")  # Debug print
    
    # Имитация задержки
    await asyncio.sleep(random.uniform(1, 3))
    
    page = await context.new_page()
    await page.set_extra_http_headers({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    # Retry для загрузки (увеличен таймаут)
    max_retries = 3
    loaded = False
    for attempt in range(max_retries):
        try:
            debug_print(f"📥 Попытка загрузки {title}, попытка {attempt + 1}/{max_retries}")
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_load_state("networkidle", timeout=45000)  # Увеличено
            debug_print(f"✅ Страница {title} загружена.")
            loaded = True
            break
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            debug_print(f"❌ Таймаут/ошибка загрузки {title} (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(5, 8))
            else:
                debug_print(f"❌ Не удалось загрузить {title}, скип")
                await page.close()
                async with progress_lock:
                    progress_bar.update(1)
                return {"title": title, "status": "load_failed"}
    
    # CAPTCHA
    try:
        await page.wait_for_selector("text=Подтвердите, что вы не робот", timeout=5000)
        print(f"🔒 CAPTCHA для {title}! Пройдите вручную...")  # Print для пользователя
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)
        debug_print("✅ CAPTCHA пройдена.")
    except PlaywrightTimeoutError:
        pass
    
    # Имитация чтения
    await asyncio.sleep(random.uniform(3, 5))
    
    # Проверка уже откликнутого
    try:
        success_elem = await page.query_selector("div[data-qa='success-response']")
        if success_elem:
            debug_print(f"ℹ️ Уже откликнуто на {title}, скип")
            await page.close()
            async with progress_lock:
                progress_bar.update(1)
            return {"title": title, "status": "already_applied"}
    except Exception as e:
        debug_print(f"⚠️ Ошибка проверки уже откликнутого: {e}")
    
    # Кнопка отклика (15 сек таймаут)
    apply_selector = "a[data-qa='vacancy-response-link-top'], button[data-qa='vacancy-response-button']"
    try:
        apply_elem = await page.wait_for_selector(apply_selector, timeout=15000)
        debug_print(f"✅ Кнопка отклика найдена для {title}")
    except PlaywrightTimeoutError:
        debug_print(f"❌ Кнопка отклика не найдена за 15 сек для {title}")
        await page.close()
        async with progress_lock:
            progress_bar.update(1)
        return {"title": title, "status": "no_apply_button"}
    
    # Парсинг описания
    description = await parse_vacancy_description(page, title)
    if not description:
        debug_print(f"❌ Парсинг описания failed для {title}")
        await page.close()
        async with progress_lock:
            progress_bar.update(1)
        return {"title": title, "status": "parse_failed"}
    debug_print(f"📄 Описание готово ({len(description)} символов)")
    
    # LLM
    system_prompt = "Ты - ассистент по сопроводительным письмам. Отвечай только JSON."
    user_prompt = PROMPTS["process"].format(profile=USER_PROFILE, user_wishes=USER_WISHES, title=title, description=description)
    debug_print(f"🤖 Отправляю в LLM для {title}...")
    llm_response = await robust_llm_query(system_prompt, user_prompt)
    debug_print(f"📥 LLM ответ: {llm_response}")  # Debug print
    
    if isinstance(llm_response, tuple) and len(llm_response) == 3:
        llm_result, model_name, provider_name = llm_response
    else:
        llm_result = llm_response
        model_name = "command-a-03-2025"
        provider_name = "CohereForAI_C4AI_Command"
    
    if llm_result and isinstance(llm_result, dict):
        relevance_str = llm_result.get("relevance", "0")
        relevance_score = int(relevance_str.split(':')[-1].strip()) if isinstance(relevance_str, str) else int(relevance_str or 0)
        apply_decision = llm_result.get("apply", "").lower().strip()
        letter = llm_result.get("letter", "")
        
        debug_print(f"📊 LLM: relevance={relevance_score}, apply='{apply_decision}', letter_len={len(letter)}")
        
        # Disclaimer
        disclaimer = f'\n\nСопроводительное письмо составлено при помощи Большой Языковой Модели "{model_name}" провайдера "{provider_name}" используя библиотеку g4f. Эта же модель использовалась для определения соответствия вакансии резюме и пожеланиям соискателя. Исходный код программы для автоматизации откликов на hh.ru доступен в репозитории https://github.com/TryDotAtwo/AutoHHKek'
        letter += disclaimer
        
        if relevance_score >= MAGIC_NUMBERS["RELEVANCE_THRESHOLD"] and apply_decision in ["да", "yes"] and letter.strip():
            debug_print(f"🎯 {title} релевантна (score: {relevance_score}), откликаемся...")

            try:
                # Клик отклика
                await page.click(apply_selector, force=True, timeout=15000)
                await page.wait_for_load_state("networkidle", timeout=20000)
                print(f"🔘 Клик по отклику успешен для {title}")
                
                # Шаг 1: Ждём базовый успех ("Резюме доставлено" + чат)
                base_selectors = [
                    ".magritte-text_style-primary.magritte-text_typography-title-4-semibold:has-text('Резюме доставлено')",
                    "text=Резюме доставлено",
                    "div[data-qa='success-response']"
                ]
                chat_selectors = [
                    "text=Связаться с работодателем можно в чате",
                    ".magritte-text_style-secondary.magritte-text_typography-paragraph-2-regular:has-text('Связаться с&nbsp;работодателем можно в&nbsp;чате')"
                ]
                
                base_success = False
                for sel in base_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=20000)
                        base_success = True
                        break
                    except PlaywrightTimeoutError:
                        continue
                
                chat_success = False
                for sel in chat_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=10000)
                        chat_success = True
                        break
                    except PlaywrightTimeoutError:
                        continue
                
                if not (base_success and chat_success):
                    print(f"❌ Базовый успех не подтверждён для {title}")
                    await page.close()
                    async with progress_lock:
                        progress_bar.update(1)
                    return {"title": title, "status": "no_base_success"}
                
                print(f"📤 Резюме доставлено для {title}! Отправляем письмо...")
                
                # Шаг 2: Ждём форму письма
                form_selector = "textarea[name='text'], .magritte-native-element"
                await page.wait_for_selector(form_selector, timeout=25000)
                print(f"📝 Форма открылась для {title}")
                
                # Очищаем возможную ошибку (из HTML: aria-invalid)
                await page.evaluate("""
                    const textarea = document.querySelector('textarea[name="text"]');
                    if (textarea) {
                        textarea.value = '';
                        textarea.setAttribute('aria-invalid', 'false');
                    }
                    const error = document.querySelector('.magritte-form-helper-error');
                    if (error) error.remove();
                """)
                await asyncio.sleep(1)
                
                # Заполняем
                message_field = page.locator(form_selector)
                await message_field.focus()
                await message_field.fill(letter)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                print(f"✏️ Письмо заполнено для {title}")
                
                # Шаг 3: Submit
                submit_selector = "button[data-qa='vacancy-response-letter-submit'], button[type='submit']"
                submit_btn = await page.wait_for_selector(submit_selector, timeout=15000)
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                print(f"📤 Submit отправлен для {title}")
                
                # Шаг 4: Ждём успех письма ("Сопроводительное письмо отправлено")
                letter_success_selectors = [
                    "text=Сопроводительное письмо отправлено",
                    ".magritte-text:has-text('Сопроводительное письмо отправлено')",
                    ".magritte-form-helper:not(.magritte-form-helper-error):has-text('отправлено')",
                    "form#cover-letter:not(:has(textarea[aria-invalid='true']))"  # Fallback: форма чистая
                ]
                letter_success = False
                for sel in letter_success_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=20000)
                        letter_success = True
                        break
                    except PlaywrightTimeoutError:
                        continue
                
                if letter_success:
                    print(f"🎉 ✅ Успешный отклик с письмом на {title}!")
                    return {"title": title, "status": "letter_sent"}
                else:
                    print(f"❌ Письмо не подтверждено для {title}. Проверьте вручную.")
                    return {"title": title, "status": "letter_failed"}

            except Exception as e:
                print(f"💥 Ошибка отклика на {title}: {e}")
                return {"title": title, "status": "error"}

            finally:
                await page.close()
                async with progress_lock:
                    progress_bar.update(1)
                    remaining = progress_bar.total - progress_bar.n
                    progress_bar.set_description(f"Обработано {progress_bar.n}/{progress_bar.total} (осталось: {remaining})")
        else:
            debug_print(f"⏭️ {title} не релевантна (score: {relevance_score}, apply: {apply_decision}), скип")
    else:
        debug_print(f"🤖 Ошибка LLM для {title}: {llm_result}, скип")
    
    await page.close()
    async with progress_lock:
        progress_bar.update(1)
        remaining = progress_bar.total - progress_bar.n
        progress_bar.set_description(f"Обработано {progress_bar.n}/{progress_bar.total} (осталось: {remaining})")
    return {"title": title, "llm_result": llm_result, "status": "processed"}

async def main():
    """Основная асинхронная функция."""
    async with async_playwright() as p:
        context = await setup_browser_and_login(p)
        
        # Применяем stealth к context (для всех pages)
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        
        # Парсим резюме для обновления USER_PROFILE
        page = await context.new_page()
        # Stealth уже применен
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        global USER_PROFILE
        USER_PROFILE = await parse_resume(page, RESUME_ID, USER_WISHES)  # Добавлен аргумент USER_WISHES
        await page.close()
        
        # Поиск вакансий
        page = await context.new_page()
        # Stealth уже применен
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        vacancies, total_count = await search_vacancies(page, RESUME_ID)  # Увеличил лимит сбора, если нужно
        # НЕ закрываем страницу поиска
        # await page.close()  # Закомментировано, чтобы держать открытой

        if not vacancies:
            print("Вакансии не найдены.")
            await context.close()
            return

        print(f"📊 Найдено {len(vacancies)} вакансий (из {total_count}). Concurrency: {MAGIC_NUMBERS['CONCURRENCY_LIMIT']}, интервал: {MAGIC_NUMBERS['PROCESS_INTERVAL']} сек")

        # Семфор для ограничения concurrency
        semaphore = asyncio.Semaphore(MAGIC_NUMBERS["CONCURRENCY_LIMIT"])
        progress_lock = asyncio.Lock()

        # Прогресс-бар
        progress_bar = tqdm(total=len(vacancies), desc="Обработка вакансий", unit="vac")

        async def bounded_process(vac):
            async with semaphore:
                # Задержка перед запуском (1-2 сек, кроме первой) - но основная задержка в main
                if random.random() > 0.5:  # Имитация случайной задержки
                    await asyncio.sleep(1 + random.random())
                return await process_vacancy(vac, context, progress_bar, progress_lock, semaphore)

        # Создаем задачи с интервалом 2 секунды между запуском
        tasks = []
        for i, vac in enumerate(vacancies):
            if i > 0:
                await asyncio.sleep(MAGIC_NUMBERS["PROCESS_INTERVAL"])  # Интервал между открытием вакансий
            debug_print(f"🚀 Запуск {i+1}/{len(vacancies)}: {vac['title'][:50]}...")
            task = asyncio.create_task(bounded_process(vac))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        progress_bar.close()
        for result in results:
            if isinstance(result, Exception):
                print(f"💥 Ошибка в задаче: {result}")
            else:
                debug_print(f"✅ Завершено для {result['title']}: {result}")

        await page.close()
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
