import subprocess
import sys
import random
import asyncio
import concurrent.futures
import os
import json
import re
from tqdm import tqdm
import argparse  # Добавлено для парсинга аргументов

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
from logic.vacancy_handler import process_vacancy  # Новый импорт (теперь принимает готовый letter и relevance)
from logic.llm_handler import robust_llm_query  # Для LLM (используется только в main)
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import Error as PlaywrightError  # Для обработки closed errors
from playwright_stealth import Stealth  # Используем Stealth класс для async
from typing import Dict, Any, Tuple

# Конфигурация (данные для тестов из конфига)
RESUME_ID = "[Ваш ID резюме]"  # Ваш ID резюме
USER_WISHES = "[Ваши пожелания]"  # Пожелания пользователя
USER_PROFILE = ""  # Будет обновлено после парсинга

# Конфигурация для тестов (тестовые данные для независимого тестирования модулей)
TEST_CONFIG = {
    "test_profile": "Тестовый профиль специалиста по LLM: опыт 2+ года в NLP, разработка бенчмарков, промптинг",
    "test_vacancy_title": "[Название тестовой вакансии]",
    "test_vacancy_url": "[Ссылка на тестовую вакансию]",
    "test_vacancy_description": "Тестовое описание вакансии по ML. Требуется опыт в LLM и промптинге. Включите в письмо фразу 'Я готов к вызовам'.",
    "test_llm_response": {
        "relevance": "Оценка релевантности (0-10): 8",
        "apply": "Да",
        "letter": "Уважаемые коллеги!"
    },
    "test_letter_text": """Тест""",
    "test_relevance_score": 8,
    "test_apply_decision": "да",
    "test_model_name": "command-a-03-2025",
    "test_provider_name": "CohereForAI_C4AI_Command",
    "test_disclaimer": '\n\nСопроводительное письмо составлено при помощи Большой Языковой Модели "{model_name}" провайдера "{provider_name}" используя библиотеку g4f. Эта же модель использовалась для определения соответствия вакансии резюме и пожеланиям соискателя. Исходный код программы для автоматизации откликов на hh.ru доступен в репозитории https://github.com/TryDotAtwo/AutoHHKek'
}

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
    "CONCURRENCY_LIMIT": 1,  # Вернули 5 для параллели
    "PROCESS_INTERVAL": 5,  # Интервал между запуском новых задач в секундах (опционально, если нужно)
    "DEBUG_MODE": True,  # Включено для отладки
    "SCREENSHOT_DIR": "./screenshots",  # Директория для скриншотов
    "PAGE_TIMEOUT": 150  # Таймаут для закрытия страницы в секундах (5 минут)
}

CONFIG = {
    "resume_id": RESUME_ID,
    "user_wishes": USER_WISHES,
    "prompts": PROMPTS,
    "magic_numbers": MAGIC_NUMBERS,
    "test_config": TEST_CONFIG,
    "system_prompt": "Ты - ассистент по сопроводительным письмам. Отвечай только JSON.",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "model_name": "command-a-03-2025",
    "provider_name": "CohereForAI_C4AI_Command",
    "disclaimer_template": '\n\nСопроводительное письмо составлено при помощи Большой Языковой Модели "{model_name}" провайдера "{provider_name}" используя библиотеку g4f. Эта же модель использовалась для определения соответствия вакансии резюме и пожеланиям соискателя. Исходный код программы для автоматизации откликов на hh.ru доступен в репозитории https://github.com/TryDotAtwo/AutoHHKek',
    "progress_desc": "Обработка вакансий",
    "progress_unit": "vac",
    "launch_msg": "🚀 Запуск {index}/{total}: {title}...",
    "complete_msg": "✅ Завершено для {title}: {status}",
    "test_login_msg": "🧪 Тестируем только модуль login...",
    "test_resume_msg": "🧪 Тестируем только модуль resume_parser (ID: {resume_id})...",
    "test_vacancy_search_msg": "🧪 Тестируем только модуль vacancy_parser (ID: {resume_id})...",
    "test_llm_msg": "🧪 Тестируем только модуль llm_handler...",
    "test_vacancy_handler_msg": "🧪 Тестируем только модуль vacancy_handler для '{title}' (ID резюме: {resume_id})...",
    "login_success": "✅ Логин успешен! Context создан.",
    "resume_parsed": "✅ Резюме спарсено: {profile}...",
    "vacancies_found": "✅ Найдено {count} вакансий (всего {total}). Пример: {example}",
    "llm_success": "✅ LLM ответ: {response}",
    "llm_result": "   - Результат: {result}",
    "llm_model": "   - Модель: {model}, Провайдер: {provider}",
    "vacancy_handler_result": "✅ Результат: {result}",
    "no_vacancies": "Вакансии не найдены.",
    "stats_msg": "📊 Найдено {count} вакансий (из {total}). Concurrency: {concurrency}, интервал: {interval} сек",
    "progress_update": "Обработано {processed}/{total} (осталось: {remaining})",
    "task_error": "💥 Ошибка в задаче: {error}",
    "total_success": "🎯 Итого успешных откликов с письмом: {success}/{total}",
    "profile_preview_len": 200,
    "title_preview_len": 50,
    "random_delay_prob": 0.5,
    "random_delay_min": 1,
    "random_delay_add": 1
}

async def create_page_with_auto_close(context: BrowserContext, user_agent: str) -> Tuple[Page, asyncio.Task]:
    """Создает страницу с фоновой задачей для автоматического закрытия через PAGE_TIMEOUT секунд."""
    page = await context.new_page()
    await page.set_extra_http_headers({"User-Agent": user_agent})
    
    async def auto_closer():
        await asyncio.sleep(CONFIG["magic_numbers"]["PAGE_TIMEOUT"])
        try:
            await page.close()
            print(f"⏰ Автоматическое закрытие страницы через {CONFIG['magic_numbers']['PAGE_TIMEOUT']} сек.")
        except Exception as e:
            print(f"⚠️ Ошибка при автоматическом закрытии страницы: {e}")
    
    closer_task = asyncio.create_task(auto_closer())
    return page, closer_task

async def test_login():
    """Тест только модуля login: настройка браузера и логин (независимый)."""
    print(CONFIG["test_login_msg"])
    async with async_playwright() as p:
        context = await setup_browser_and_login(p)
        print(CONFIG["login_success"])
        await context.close()

async def test_resume_parser(resume_id: str = CONFIG["resume_id"]):
    """Тест только модуля resume_parser: парсинг резюме (логин + парсинг, без других)."""
    print(CONFIG["test_resume_msg"].format(resume_id=resume_id))
    async with async_playwright() as p:
        context = await setup_browser_and_login(p)
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        page, closer_task = await create_page_with_auto_close(context, CONFIG["user_agent"])
        try:
            profile = await parse_resume(page, resume_id, CONFIG["user_wishes"])
            preview = profile[:CONFIG["profile_preview_len"]] + "..." if len(profile) > CONFIG["profile_preview_len"] else profile
            print(CONFIG["resume_parsed"].format(profile=preview))
        finally:
            closer_task.cancel()
            try:
                await page.close()
            except:
                pass
            await context.close()

async def test_vacancy_search(resume_id: str = CONFIG["resume_id"]):
    """Тест только модуля vacancy_parser: поиск вакансий (логин + поиск, без других)."""
    print(CONFIG["test_vacancy_search_msg"].format(resume_id=resume_id))
    async with async_playwright() as p:
        context = await setup_browser_and_login(p)
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        page, closer_task = await create_page_with_auto_close(context, CONFIG["user_agent"])
        try:
            vacancies, total = await search_vacancies(page, resume_id)
            example = vacancies[0]['title'] if vacancies else 'Нет вакансий'
            print(CONFIG["vacancies_found"].format(count=len(vacancies), total=total, example=example))
        finally:
            closer_task.cancel()
            try:
                await page.close()
            except:
                pass
            await context.close()

async def test_llm_handler():
    """Тест только модуля llm_handler: запрос к LLM (без браузера, тестовые данные из конфига)."""
    print(CONFIG["test_llm_msg"])
    user_prompt = CONFIG["prompts"]["process"].format(
        profile=CONFIG["test_config"]["test_profile"],
        user_wishes=CONFIG["user_wishes"],
        title=CONFIG["test_config"]["test_vacancy_title"],
        description=CONFIG["test_config"]["test_vacancy_description"]
    )
    response = await robust_llm_query(CONFIG["system_prompt"], user_prompt)
    print(CONFIG["llm_success"].format(response=response))
    if isinstance(response, tuple) and len(response) == 3:
        print(CONFIG["llm_result"].format(result=response[0]))
        print(CONFIG["llm_model"].format(model=response[1], provider=response[2]))

async def test_vacancy_handler(vacancy_url: str = CONFIG["test_config"]["test_vacancy_url"], 
                               vacancy_title: str = CONFIG["test_config"]["test_vacancy_title"], 
                               resume_id: str = CONFIG["resume_id"]):
    """Тест только модуля vacancy_handler: обработка вакансии (логин + process_vacancy, текст письма из конфига)."""
    print(CONFIG["test_vacancy_handler_msg"].format(title=vacancy_title, resume_id=resume_id))
    # Фиксированный текст для сопроводительного письма (для чистого теста кликов, без LLM)
    FIXED_LETTER_TEXT = CONFIG["test_config"]["test_letter_text"]
    # Фиксированные relevance и decision для теста (Да, score 8)
    FIXED_RELEVANCE_SCORE = CONFIG["test_config"]["test_relevance_score"]
    FIXED_APPLY_DECISION = CONFIG["test_config"]["test_apply_decision"]
    # Фиксированные данные для LLM внутри process_vacancy (не передаем letter напрямую, т.к. функция делает LLM сама)
    async with async_playwright() as p:
        context = await setup_browser_and_login(p)
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        vacancy = {"title": vacancy_title, "url": vacancy_url}
        # Вызов process_vacancy с аргументами для старой сигнатуры (LLM сработает на тестовых profile/wishes/prompts)
        result = await process_vacancy(
            vacancy, context, 
            CONFIG["test_config"]["test_profile"],  # user_profile
            CONFIG["user_wishes"],  # user_wishes
            CONFIG["prompts"],  # prompts (dict, чтобы избежать ошибки string indices)
            CONFIG["magic_numbers"]["RELEVANCE_THRESHOLD"],  # relevance_threshold
            CONFIG["magic_numbers"]["DEBUG_MODE"]  # debug_mode
        )
        print(CONFIG["vacancy_handler_result"].format(result=result))
        await context.close()

async def main():
    """Основная асинхронная функция — оркестратор (использует модули независимо)."""
    async with async_playwright() as p:
        # Шаг 1: Логин (независимый модуль)
        context = await setup_browser_and_login(p)
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        
        # Шаг 2: Парсинг резюме (независимый модуль)
        page, closer_task_resume = await create_page_with_auto_close(context, CONFIG["user_agent"])
        try:
            global USER_PROFILE
            USER_PROFILE = await parse_resume(page, CONFIG["resume_id"], CONFIG["user_wishes"])
        finally:
            closer_task_resume.cancel()
            try:
                await page.close()
            except:
                pass
        
        # Шаг 3: Поиск вакансий (независимый модуль)
        page, closer_task_search = await create_page_with_auto_close(context, CONFIG["user_agent"])
        try:
            vacancies, total_count = await search_vacancies(page, CONFIG["resume_id"])
        finally:
            closer_task_search.cancel()
            try:
                await page.close()
            except:
                pass
        
        if not vacancies:
            print(CONFIG["no_vacancies"])
            await context.close()
            return

        concurrency = CONFIG["magic_numbers"]["CONCURRENCY_LIMIT"]
        interval = CONFIG["magic_numbers"]["PROCESS_INTERVAL"]
        debug_mode = CONFIG["magic_numbers"]["DEBUG_MODE"]
        print(CONFIG["stats_msg"].format(
            count=len(vacancies), total=total_count, 
            concurrency=concurrency, interval=interval
        ))

        # Семфор для ограничения concurrency
        semaphore = asyncio.Semaphore(concurrency)
        progress_lock = asyncio.Lock()

        # Прогресс-бар
        progress_bar = tqdm(total=len(vacancies), desc=CONFIG["progress_desc"], unit=CONFIG["progress_unit"])

        async def bounded_process(vac, index: int):
            """Обёртка: print запуска перед семафором (чтобы принты шли постепенно)."""
            title_preview = vac['title'][:CONFIG["title_preview_len"]] + "..." if len(vac['title']) > CONFIG["title_preview_len"] else vac['title']
            print(CONFIG["launch_msg"].format(index=index+1, total=len(vacancies), title=title_preview))
            async with semaphore:
                # Имитация случайной задержки перед обработкой
                if random.random() > CONFIG["random_delay_prob"]:
                    await asyncio.sleep(CONFIG["random_delay_min"] + random.random() * CONFIG["random_delay_add"])
                
                # Шаг 4: Обработка вакансии (LLM, парсинг и отклик внутри vacancy_handler)
                result = await process_vacancy(
                    vac, context, 
                    USER_PROFILE,  # user_profile
                    CONFIG["user_wishes"],  # user_wishes
                    CONFIG["prompts"],  # prompts (dict)
                    CONFIG["magic_numbers"]["RELEVANCE_THRESHOLD"],  # relevance_threshold
                    debug_mode  # debug_mode
                )
                
                # Обновляем прогресс только после полного завершения вакансии
                async with progress_lock:
                    progress_bar.update(1)
                    remaining = progress_bar.total - progress_bar.n
                    progress_bar.set_description(CONFIG["progress_update"].format(
                        processed=progress_bar.n, total=progress_bar.total, remaining=remaining
                    ))
                title_preview = result['title'][:CONFIG["title_preview_len"]] + "..." if len(result['title']) > CONFIG["title_preview_len"] else result['title']
                print(CONFIG["complete_msg"].format(title=title_preview, status=result['status']))
                return result

        # Запускаем задачи последовательно с интервалом, ограничивая parallelism семафором
        tasks = []
        for i, vac in enumerate(vacancies):
            task = asyncio.create_task(bounded_process(vac, i))
            tasks.append(task)
            if i < len(vacancies) - 1:  # Не ждем после последней
                await asyncio.sleep(interval)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        progress_bar.close()

        # Фильтруем исключения
        for result in results:
            if isinstance(result, Exception):
                print(CONFIG["task_error"].format(error=result))

        # Итоговый вывод (опционально)
        successful = sum(1 for r in results if not isinstance(r, Exception) and r.get('status') == 'letter_sent')
        print(CONFIG["total_success"].format(success=successful, total=len(vacancies)))

        await context.close()

async def get_vacancy_description(context: BrowserContext, vac: Dict[str, str]) -> str:
    """Вспомогательная функция для получения описания вакансии (из vacancy_handler, но вызывается в main для изоляции)."""
    # Здесь можно реализовать парсинг описания, но для простоты — пустая строка (замените на реальный вызов)
    return "Описание вакансии (получено из парсера)."

if __name__ == "__main__":
    # === ТЕСТЫ МОДУЛЕЙ (раскомментируйте нужный и запустите файл) ===
    
    # Тест login (просто логин)
    # asyncio.run(test_login())
    
    # Тест resume_parser (парсинг резюме по RESUME_ID из конфига)
    # asyncio.run(test_resume_parser())
    
    # Тест vacancy_search (поиск вакансий по RESUME_ID из конфига)
    # asyncio.run(test_vacancy_search())
    
    # Тест llm_handler (запрос к LLM с тестовыми данными из конфига)
    # asyncio.run(test_llm_handler())
    
    # Тест vacancy_handler (обработка конкретной вакансии: URL и title из примера, с фиксированным письмом)
    # asyncio.run(test_vacancy_handler())

    # Полный main (раскомментируйте для нормальной работы)
    asyncio.run(main())
