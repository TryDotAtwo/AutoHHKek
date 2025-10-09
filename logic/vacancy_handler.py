import asyncio
import random
import re
from typing import Dict, Any
from playwright.async_api import Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import Error as PlaywrightError

# Импорты из других модулей (будут доступны через main)
from logic.llm_handler import robust_llm_query

def debug_print(message: str, debug_mode: bool):
    """Выводит сообщение только в debug режиме."""
    if debug_mode:
        print(message)

async def handle_captcha(page: Page, debug_mode: bool, title: str = "") -> bool:
    """Обрабатывает CAPTCHA: просит пользователя пройти её и подтверждает продолжение."""
    print(f"🔒 Обнаружена CAPTCHA для вакансии '{title}'! Пожалуйста, перейдите в открытый браузер, пройдите CAPTCHA вручную и нажмите Enter здесь, чтобы продолжить...")
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
        debug_print(f"❌ Page error in operation: {e}", debug_mode=True)
        return None

async def parse_vacancy_description(page: Page, title: str, debug_mode: bool) -> str:
    """Парсит описание вакансии аналогично парсингу резюме: полный текст, очистка."""
    debug_print("📄 Начинаем парсинг описания вакансии...", debug_mode)
    
    # Проверка на CAPTCHA (без safe_page_operation, прямой try-except, увеличенный таймаут)
    debug_print("🔍 Проверяем CAPTCHA...", debug_mode)
    try:
        await page.wait_for_selector("text=Подтвердите, что вы не робот", timeout=2000)
        await handle_captcha(page, title, debug_mode)
    except PlaywrightTimeoutError:
        pass  # Нет CAPTCHA, ок
    debug_print("✅ CAPTCHA check done.", debug_mode)
    
    # Проверяем на ошибку страницы (прямой try-except, без safe, чтобы избежать hidden spam)
    debug_print("🔍 Проверяем ошибку страницы...", debug_mode)
    try:
        await page.wait_for_selector("text=Произошла ошибка", timeout=10000)
        debug_print("⚠️ Обнаружена ошибка страницы: 'Произошла ошибка. Попробуйте перезагрузить страницу.'", debug_mode)
        # Если ошибка, релоадим сразу
        await page.reload(wait_until="domcontentloaded", timeout=45000)
    except PlaywrightTimeoutError:
        pass  # Нет ошибки, ок
    debug_print("✅ Error check done.", debug_mode)
    
    # Перезагрузка для очистки кэша (с safe)
    debug_print("🔄 Релоадим страницу для очистки кэша...", debug_mode)
    reload_result = await safe_page_operation(page, page.reload, wait_until="domcontentloaded", timeout=45000)
    if reload_result is None:
        debug_print("⚠️ Таймаут релоада, продолжаем с частичной загрузкой.", debug_mode)
        await page.wait_for_timeout(5000)
    debug_print("✅ Reload done.", debug_mode)
    
    # Получаем весь видимый текст (без разворачивания секций)
    debug_print("📖 Извлекаем текст страницы...", debug_mode)
    full_raw_text = await safe_page_operation(page, page.inner_text, "body")
    if full_raw_text is None:
        debug_print("❌ Не удалось извлечь текст, fallback.", debug_mode)
        return ""  # Fallback если page closed
    
    debug_print(f"📄 Сырой текст получен ({len(full_raw_text)} символов). Очищаем...", debug_mode)
    
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
    
    debug_print(f"✅ Описание готово ({len(full_raw_text)} символов).", debug_mode)
    return full_raw_text

async def process_vacancy(
    vacancy: Dict[str, str], 
    context: BrowserContext, 
    user_profile: str, 
    user_wishes: str, 
    prompts: Dict[str, str], 
    relevance_threshold: int, 
    debug_mode: bool
) -> Dict[str, Any]:
    """Обрабатывает одну вакансию: LLM-оценка + генерация, отклик если подходит."""
    title = vacancy["title"]
    url = vacancy["url"]
    debug_print(f"🚀 Начинаем обработку вакансии: '{title}' (URL: {url})", debug_mode)
    
    # Имитация задержки
    debug_print("⏳ Имитируем задержку перед загрузкой (1-3s)...", debug_mode)
    await asyncio.sleep(random.uniform(1, 3))
    debug_print("✅ Задержка done.", debug_mode)
    
    page = await context.new_page()
    await page.set_extra_http_headers({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    debug_print("🆕 Новая страница создана.", debug_mode)
    
    # Retry для загрузки (уменьшен таймаут для теста)
    max_retries = 3
    loaded = False
    for attempt in range(max_retries):
        debug_print(f"📥 Попытка загрузки '{title}', попытка {attempt + 1}/{max_retries}", debug_mode)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)  # Reduced to 30s
            debug_print("✅ Goto completed.", debug_mode)
            # Убрали wait_for_load_state("networkidle")
            loaded = True
            break
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            debug_print(f"❌ Таймаут/ошибка загрузки '{title}' (попытка {attempt + 1}): {e}", debug_mode)
            if attempt < max_retries - 1:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(5, 8))
            else:
                debug_print(f"❌ Не удалось загрузить '{title}', скип", debug_mode)
                await page.close()
                return {"title": title, "status": "load_failed"}
    
    if not loaded:
        await page.close()
        return {"title": title, "status": "load_failed"}
    
    # CAPTCHA (прямой try-except, увеличенный таймаут)
    debug_print("🔍 Проверяем CAPTCHA...", debug_mode)
    try:
        await page.wait_for_selector("text=Подтвердите, что вы не робот", timeout=2000)
        print(f"🔒 CAPTCHA для '{title}'! Пройдите вручную...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)
        debug_print("✅ CAPTCHA пройдена.", debug_mode)
    except PlaywrightTimeoutError:
        debug_print("✅ Нет CAPTCHA.", debug_mode)
    debug_print("✅ CAPTCHA check done.", debug_mode)
    
    # Имитация чтения
    debug_print("⏳ Имитируем чтение (3-5s)...", debug_mode)
    await asyncio.sleep(random.uniform(3, 5))
    debug_print("✅ Чтение done.", debug_mode)
    
    # Проверка уже откликнутого
    debug_print("🔍 Проверяем, уже ли откликнуто...", debug_mode)
    try:
        success_elem = await page.query_selector("div[data-qa='success-response']")
        if success_elem:
            debug_print(f"ℹ️ Уже откликнуто на '{title}', скип", debug_mode)
            await page.close()
            return {"title": title, "status": "already_applied"}
        debug_print("✅ Не откликнуто ранее.", debug_mode)
    except Exception as e:
        debug_print(f"⚠️ Ошибка проверки уже откликнутого: {e}", debug_mode)
    debug_print("✅ Already applied check done.", debug_mode)
    
    # Кнопка отклика (уменьшен таймаут для теста, добавлен print)
    apply_selector = "a[data-qa='vacancy-response-link-top']"
    debug_print(f"🔍 Ждём кнопку отклика: '{apply_selector}' (timeout 10s)...", debug_mode)
    try:
        apply_elem = await page.wait_for_selector(apply_selector, timeout=10000)  # Reduced for test
        debug_print(f"✅ Кнопка отклика найдена для '{title}'.", debug_mode)
    except PlaywrightTimeoutError:
        # Try alternative selector if main fails
        alt_selectors = [
            "button[data-qa='vacancy-response-button']",
            "a[data-qa='vacancy-response-link']",
            "button:has-text('Откликнуться')"
        ]
        found = False
        for alt_sel in alt_selectors:
            debug_print(f"🔍 Пробуем альтернативный селектор: '{alt_sel}'", debug_mode)
            try:
                apply_elem = await page.wait_for_selector(alt_sel, timeout=5000)
                apply_selector = alt_sel  # Update for later click
                debug_print(f"✅ Альтернативная кнопка найдена: '{alt_sel}'", debug_mode)
                found = True
                break
            except PlaywrightTimeoutError:
                continue
        if not found:
            debug_print(f"❌ Кнопка отклика не найдена для '{title}' ни по основному, ни по альтернативам.", debug_mode)
            await page.close()
            return {"title": title, "status": "no_apply_button"}
    
    # Парсинг описания
    debug_print("📄 Парсим описание...", debug_mode)
    description = await parse_vacancy_description(page, title, debug_mode)
    if not description:
        debug_print(f"❌ Парсинг описания failed для '{title}'", debug_mode)
        await page.close()
        return {"title": title, "status": "parse_failed"}
    debug_print(f"📄 Описание готово ({len(description)} символов)", debug_mode)
    
    # LLM с retry (до 3 попыток)
    debug_print("🤖 Готовим промпт для LLM...", debug_mode)
    system_prompt = "Ты - ассистент по сопроводительным письмам. Отвечай только JSON."
    user_prompt = prompts["process"].format(profile=user_profile, user_wishes=user_wishes, title=title, description=description)
    llm_result = None
    max_llm_retries = 3
    for llm_attempt in range(max_llm_retries):
        debug_print(f"🤖 Отправляю в LLM для '{title}' (попытка {llm_attempt + 1}/{max_llm_retries})...", debug_mode)
        llm_response = await robust_llm_query(system_prompt, user_prompt)
        debug_print(f"📥 LLM ответ: {llm_response}", debug_mode)
        
        if isinstance(llm_response, tuple) and len(llm_response) == 3:
            llm_result, model_name, provider_name = llm_response
        else:
            llm_result = llm_response
            model_name = "command-a-03-2025"
            provider_name = "CohereForAI_C4AI_Command"
        
        if llm_result and isinstance(llm_result, dict):
            debug_print("✅ LLM успех.", debug_mode)
            break
        else:
            debug_print(f"⚠️ LLM failed (попытка {llm_attempt + 1}), retry...", debug_mode)
            await asyncio.sleep(10)  # Sleep перед retry
    
    if not (llm_result and isinstance(llm_result, dict)):
        debug_print(f"🤖 Ошибка LLM для '{title}': {llm_result}, скип", debug_mode)
        await page.close()
        return {"title": title, "llm_result": llm_result, "status": "llm_failed"}
    
    relevance_str = llm_result.get("relevance", "0")
    # Fixed parsing: handle float scores like '8.5'
    try:
        if isinstance(relevance_str, str):
            score_part = relevance_str.split(':')[-1].strip()
            relevance_score = int(round(float(score_part)))
        else:
            relevance_score = int(relevance_str or 0)
    except (ValueError, IndexError):
        relevance_score = 0  # Fallback if parsing fails
    apply_decision = llm_result.get("apply", "").lower().strip()
    letter = llm_result.get("letter", "")
    
    debug_print(f"📊 LLM: relevance={relevance_score}, apply='{apply_decision}', letter_len={len(letter)}", debug_mode)
    
    # Disclaimer
    disclaimer = f'\n\nСопроводительное письмо составлено при помощи Большой Языковой Модели "{model_name}" провайдера "{provider_name}" используя библиотеку g4f. Эта же модель использовалась для определения соответствия вакансии резюме и пожеланиям соискателя. Исходный код программы для автоматизации откликов на hh.ru доступен в репозитории https://github.com/TryDotAtwo/AutoHHKek'
    letter += disclaimer
    
    if relevance_score >= relevance_threshold and apply_decision in ["да", "yes"] and letter.strip():
        debug_print(f"🎯 '{title}' релевантна (score: {relevance_score}), откликаемся...", debug_mode)

        try:
            # Клик отклика (уменьшен таймаут)
            debug_print(f"🖱️ Кликаем по кнопке отклика: '{apply_selector}'", debug_mode)
            await page.click(apply_selector, force=True, timeout=10000)
            debug_print("✅ Click done.", debug_mode)
            # Убрали wait_for_load_state("networkidle")
            await asyncio.sleep(2)  # Короткий sleep вместо networkidle
            debug_print("✅ Sleep after click done.", debug_mode)
            await asyncio.sleep(5)  # Доп. sleep для полной загрузки
            if debug_mode:
                print(f"🔘 Клик по отклику успешен для '{title}'")
            
            # Шаг 1: Ждём базовый успех ("Резюме доставлено" + чат) — уменьшенные таймауты
            debug_print("🔍 Ждём подтверждения 'Резюме доставлено'...", debug_mode)
            base_selectors = [
                "text=Резюме доставлено",
                ".magritte-text_style-primary.magritte-text_typography-title-4-semibold:has-text('Резюме доставлено')",
                "div[data-qa='success-response']"
            ]
            base_success = False
            for sel in base_selectors:
                debug_print(f"  - Пробуем селектор: '{sel}'", debug_mode)
                try:
                    await page.wait_for_selector(sel, timeout=10000)  # Reduced
                    base_success = True
                    if debug_mode:
                        print(f"✅ 'Резюме доставлено' подтверждено для '{title}'")
                    break
                except PlaywrightTimeoutError:
                    continue
            
            if not base_success:
                print(f"❌ 'Резюме доставлено' не подтверждено для '{title}'")
                await page.close()
                return {"title": title, "status": "no_base_success"}
            
            debug_print("🔍 Ждём подтверждения чата...", debug_mode)
            chat_selectors = [
                "text=Связаться с работодателем можно в чате",
                ".magritte-text_style-secondary.magritte-text_typography-paragraph-2-regular:has-text('Связаться с&nbsp;работодателем можно в&nbsp;чате')"
            ]
            chat_success = False
            for sel in chat_selectors:
                debug_print(f"  - Пробуем селектор: '{sel}'", debug_mode)
                try:
                    await page.wait_for_selector(sel, timeout=10000)
                    chat_success = True
                    if debug_mode:
                        print(f"✅ Чат подтверждён для '{title}'")
                    break
                except PlaywrightTimeoutError:
                    continue
            
            if not chat_success:
                print(f"❌ Чат не подтверждён для '{title}'")
                await page.close()
                return {"title": title, "status": "no_chat_success"}
            
            if debug_mode:
                print(f"📤 Резюме доставлено для '{title}'! Отправляем письмо...")
            await asyncio.sleep(3)  # Sleep перед формой
            
            # Шаг 2: Ждём форму письма (уменьшен таймаут)
            debug_print("📝 Ждём форму письма...", debug_mode)
            form_selector = "textarea[name='text']"
            await page.wait_for_selector(form_selector, timeout=10000)
            if debug_mode:
                print(f"📝 Форма открылась для '{title}'")
            await asyncio.sleep(2)
            
            # Очищаем возможную ошибку (из HTML: aria-invalid)
            debug_print("🧹 Очищаем поле...", debug_mode)
            await page.evaluate("""
                const textarea = document.querySelector('textarea[name="text"]');
                if (textarea) {
                    textarea.value = '';
                    textarea.setAttribute('aria-invalid', 'false');
                }
                const error = document.querySelector('.magritte-form-helper-error');
                if (error) error.remove();
            """)
            await asyncio.sleep(2)
            debug_print("✅ Очистка done.", debug_mode)
            
            # Заполняем
            debug_print("✏️ Заполняем письмо...", debug_mode)
            message_field = page.locator(form_selector)
            await message_field.focus()
            await message_field.fill(letter)
            await asyncio.sleep(random.uniform(3, 5))  # Увеличено для имитации
            if debug_mode:
                print(f"✏️ Письмо заполнено для '{title}'")
            debug_print("✅ Fill done.", debug_mode)
            
            # Шаг 3: Submit (уменьшен таймаут)
            debug_print("📤 Ждём и кликаем submit...", debug_mode)
            submit_selector = "button[data-qa='vacancy-response-letter-submit']"
            submit_btn = await page.wait_for_selector(submit_selector, timeout=10000)
            await submit_btn.click()
            debug_print("✅ Submit click done.", debug_mode)
            # Убрали wait_for_load_state("networkidle")
            await asyncio.sleep(2)  # Короткий sleep вместо networkidle
            await asyncio.sleep(5)  # Доп. sleep после submit
            if debug_mode:
                print(f"📤 Submit отправлен для '{title}'")
            
            # Шаг 4: Ждём успех письма ("Сопроводительное письмо отправлено") — уменьшен таймаут
            debug_print("🔍 Ждём подтверждения отправки письма...", debug_mode)
            letter_success_selectors = [
                "text=Сопроводительное письмо отправлено",
                ".magritte-text_style-primary.magritte-text_typography-label-3-regular:has-text('Сопроводительное письмо отправлено')"
            ]
            letter_success = False
            for sel in letter_success_selectors:
                debug_print(f"  - Пробуем селектор: '{sel}'", debug_mode)
                try:
                    await page.wait_for_selector(sel, timeout=10000)
                    letter_success = True
                    if debug_mode:
                        print(f"✅ 'Сопроводительное письмо отправлено' подтверждено для '{title}'")
                    break
                except PlaywrightTimeoutError:
                    continue
            
            if letter_success:
                if debug_mode:
                    print(f"🎉 ✅ Успешный отклик с письмом на '{title}'!")
                await page.close()
                result = {"title": title, "status": "letter_sent"}
                if not debug_mode:
                    print(f"🎯 Отклик с письмом отправлен на '{title}'")
                return result
            else:
                print(f"❌ Письмо не подтверждено для '{title}'. Проверьте вручную.")
                await page.close()
                result = {"title": title, "status": "letter_failed"}
                if not debug_mode:
                    print(f"⚠️ Не удалось отправить письмо для '{title}'")
                return result

        except Exception as e:
            print(f"💥 Ошибка отклика на '{title}': {e}")
            debug_print(f"💥 Полная ошибка: {e}", debug_mode)
            await page.close()
            result = {"title": title, "status": "error"}
            if not debug_mode:
                print(f"⚠️ Ошибка обработки вакансии '{title}'")
            return result

    else:
        debug_print(f"⏭️ '{title}' не релевантна (score: {relevance_score}, apply: {apply_decision}), скип", debug_mode)
        result = {"title": title, "llm_result": llm_result, "status": "processed"}
        if not debug_mode:
            print(f"⏭️ Вакансия '{title}' пропущена (релевантность низкая)")
        return result
    
    await page.close()
    debug_print(f"🏁 Обработка '{title}' завершена.", debug_mode)
    return result
