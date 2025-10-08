import asyncio
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from typing import Optional, Dict
import hashlib
import json
import os
from .llm_handler import robust_llm_query

CACHE_FILE = "resume_cache.json"


def load_cache(hash_key: str) -> Optional[str]:
    """Загружает кэш из JSON файла."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        return cache.get(hash_key)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Ошибка загрузки кэша: {e}")
        return None


def save_to_cache(hash_key: str, value: str):
    """Сохраняет в кэш в JSON файл."""
    try:
        if not os.path.exists(CACHE_FILE):
            cache = {}
        else:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        cache[hash_key] = value
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print("Кэш сохранён.")
    except Exception as e:
        print(f"Ошибка сохранения кэша: {e}")


async def parse_resume(page: Page, resume_id: str, user_wishes: str) -> str:
    """Парсит резюме с HH для формирования USER_PROFILE."""
    print("Парсинг резюме...")
    resume_url = f"https://hh.ru/resume/{resume_id}"
    try:
        await page.goto(resume_url, wait_until="domcontentloaded", timeout=30000)
        print("Страница резюме загружена.")
        
        # Проверяем на ошибку страницы
        error_elem = await page.query_selector("text=Произошла ошибка")
        if error_elem:
            print("Обнаружена ошибка страницы: 'Произошла ошибка. Попробуйте перезагрузить страницу.'")
            print("Возможно, неверный ID резюме или требуется перелогин. Переходим к полному тексту для LLM.")
        
        # Перезагрузка для очистки кэша
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        print("Страница перезагружена для очистки кэша.")
        
    except PlaywrightTimeoutError:
        print("Таймаут загрузки страницы резюме, продолжаем с частичной загрузкой.")
        await page.wait_for_timeout(5000)
    
    # Разворачиваем все секции
    await expand_all_sections(page)
    
    # Получаем весь видимый текст
    full_raw_text = await page.inner_text("body")
    print(f"RAW FULL TEXT (первые 500 символов): {full_raw_text[:500]}...")

    # Удаляем контактные данные
    contacts_removed = False
    try:
        # Находим блок контактов по заголовку "Контакты"
        contacts_header = await page.query_selector("h4[data-qa='title']:has-text('Контакты')")
        if contacts_header:
            # Используем data-qa атрибуты для стабильного поиска блока (классы хэшированы в CSS модулях)
            contacts_block = await contacts_header.evaluate_handle("el => el.closest('[data-qa=\"title-container\"]').parentElement")
            if contacts_block:
                contacts_text = await contacts_block.evaluate("el => el.innerText")
                if contacts_text and contacts_text.strip():
                    # Заменяем на плейсхолдер, чтобы сохранить структуру
                    full_raw_text = full_raw_text.replace(contacts_text.strip(), "[Контакты удалены]")
                    print("Контактные данные удалены из raw текста.")
                    contacts_removed = True
                else:
                    raise Exception("Контактный блок найден, но текст пустой")
            else:
                raise Exception("Контактный блок не найден (parentElement вернул null)")
        else:
            raise Exception("Заголовок 'Контакты' не найден")
    except Exception as e:
        error_msg = "Ваши контактные данные не удалось удалить. В случае их подстановки в запрос к Большой Языковой Модели эти данные утекут в сеть и могут быть использованы для дообучения Большой Языковой Модели. Чтобы избежать этого код был прерван. Обратитесь к разработчику для решения проблемы или решите её сами, спасибо."
        print(f"Ошибка удаления контактов: {e}")
        raise Exception(error_msg)

    # Вычисляем хеш от обработанного raw текста
    text_hash = hashlib.sha256(full_raw_text.encode('utf-8')).hexdigest()
    print(f"Хеш текста: {text_hash[:16]}...")

    # Проверяем кэш
    cached_profile = load_cache(text_hash)
    if cached_profile:
        print("Кэш найден, возвращаем обработанное резюме.")
        return cached_profile

    print("Кэш не найден, обрабатываем через LLM.")

    # ========================== ПРОМПТ ДЛЯ LLM ==========================
    system_prompt = """Ты — эксперт по анализу и структурированию резюме на русском языке.
Тебе даётся полный неструктурированный текст резюме с сайта hh.ru.
Твоя задача — извлечь максимум информации и выдать цельный текст резюме (не краткое summary!) на русском языке, в удобном структурированном виде для сопроводительных писем и подбора вакансий.

⚠️ Не пропускай важные данные. Не придумывай ничего от себя.
⚠️ Удали элементы интерфейса, cookie баннеры, чаты, кнопки и другие лишние куски.
⚠️ Строго соблюдай формат JSON с одним полем "resume".
"""

    user_prompt = f"""
Ниже приведён полный текст страницы резюме (возможно, с ошибками или мусором):

================ RAW RESUME TEXT (in Russian) ================
{full_raw_text}
================ END =================

Пожелания пользователя:
{user_wishes}

Если в тексте есть сообщения об ошибке вроде «Произошла ошибка. Попробуйте перезагрузить страницу.» или резюме не загрузилось, то верни:
{{
    "resume": "Резюме не найдено"
}}

Иначе:
— Извлеки и структурируй ВСЮ полезную информацию, включая:
• Желаемую должность  
• Опыт работы (компании, должности, периоды, достижения, обязанности)  
• Навыки  
• Образование (включая вузы, годы, факультеты, степень)  
• Курсы, сертификаты  
• Проекты и публикации  
• Владение языками  
• Гражданство, разрешение на работу, формат занятости  
• Ссылки (GitHub, LinkedIn, портфолио), если явно указаны  
• Блок "О себе", пожелания, ключевые компетенции и т.п.

— Составь цельный связный текст **на русском**, максимально информативный и пригодный для использования как основа сопроводительного письма.
— Не используй маркированные списки. Пиши как HR/рекрутер: связный текст, 1–3 абзаца, с чёткой структурой и максимумом содержания.

Формат ответа:
Верни ТОЛЬКО корректный JSON:
{{
    "resume": "твой текст резюме на русском"
}}

⚠️ Никакого Markdown, комментариев или дополнительных полей.
⚠️ JSON должен быть синтаксически валиден.
"""

    # Отправляем в LLM
    result = await robust_llm_query(system_prompt, user_prompt)
    
    if result and isinstance(result, dict) and 'resume' in result:
        user_profile = result['resume']
    else:
        # fallback на сырой текст
        user_profile = full_raw_text[:2000]
    
    print(f"PROCESSED USER_PROFILE (от LLM): {user_profile}")
    
    # Сохраняем в кэш
    save_to_cache(text_hash, user_profile)
    
    return user_profile


async def expand_all_sections(page: Page):
    """Разворачивает все collapsible секции с таймаутами."""
    expand_selectors = [
        "button:has-text('Развернуть')",
        "button:has-text('Показать больше')",
        "button:has-text('Подробнее')",
        "button[aria-label*='развернуть']",
        "button[title*='развернуть']",
        "[role='button']:has-text('Развернуть')",
        "button[data-qa*='expand']",
    ]
    expanded_count = 0
    for selector in expand_selectors:
        try:
            expands = await page.query_selector_all(selector)
            print(f"Найдено {len(expands)} элементов для {selector}")
            for i, expand in enumerate(expands):
                try:
                    await expand.scroll_into_view_if_needed()
                    await expand.click(timeout=5000, force=True)
                    await asyncio.wait_for(page.wait_for_load_state("domcontentloaded"), timeout=5.0)
                    await page.wait_for_timeout(1500)
                    expanded_count += 1
                    print(f"Развернута секция {expanded_count} по {selector} [{i}]")
                except (PlaywrightTimeoutError, asyncio.TimeoutError):
                    print(f"Таймаут клика/загрузки для {selector} [{i}], пропускаем")
                except Exception as e:
                    print(f"Ошибка клика для {selector} [{i}]: {e}")
        except Exception as e:
            print(f"Ошибка по {selector}: {e}")
    
    specific = {
        "О себе": "div:has-text('О себе') button:has-text('Развернуть')",
        "Опыт работы": "div:has-text('Опыт работы') button:has-text('Развернуть')",
        "Образование": "div:has-text('Образование') button:has-text('Развернуть')",
        "Навыки": "div:has-text('Навыки') button:has-text('Развернуть')",
        "Повышение квалификации": "div:has-text('Повышение') button:has-text('Развернуть')",
    }
    for name, selector in specific.items():
        try:
            expand = await page.query_selector(selector)
            if expand:
                try:
                    await expand.scroll_into_view_if_needed()
                    await expand.click(timeout=5000, force=True)
                    await asyncio.wait_for(page.wait_for_load_state("domcontentloaded"), timeout=5.0)
                    await page.wait_for_timeout(1500)
                    print(f"Развернут '{name}'")
                except (PlaywrightTimeoutError, asyncio.TimeoutError):
                    print(f"Таймаут для '{name}', пропускаем")
                except Exception as e:
                    print(f"Ошибка клика '{name}': {e}")
            else:
                print(f"Блок '{name}' не найден")
        except Exception as e:
            print(f"Ошибка поиска '{name}': {e}")
    
    print(f"Всего развернуто: {expanded_count}")
    await page.wait_for_timeout(3000)