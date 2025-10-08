import asyncio
from playwright.async_api import Page
from typing import List, Dict, Tuple
import re
import random
import json
import os

CACHE_FILE = 'vacancies_cache.json'

def load_cache() -> List[Dict[str, str]]:
    """Загрузка кэша из JSON."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                print(f"Загружен кэш с {len(cache)} вакансиями.")
                return cache
        except Exception as e:
            print(f"Ошибка загрузки кэша: {e}")
    return []

def save_cache(vacancies: List[Dict[str, str]]):
    """Сохранение кэша в JSON."""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(vacancies, f, ensure_ascii=False, indent=2)
        print(f"Кэш сохранён: {len(vacancies)} вакансий.")
    except Exception as e:
        print(f"Ошибка сохранения кэша: {e}")



async def get_total_vacancies(page: Page) -> int:
    try:
        all_text = await page.inner_text("body")
        match = re.search(r'Найдено.*?(\d+).*?подходящих.*?ваканси[яй].*?для.*?резюме', all_text, re.IGNORECASE | re.UNICODE | re.DOTALL)
        return int(match.group(1)) if match else 0
    except Exception as e:
        print(f"Ошибка: {e}")
        return 0



async def get_search_session_id(page: Page) -> str:
    """Извлечение searchSessionId из URL."""
    try:
        current_url = page.url
        # print(f"DEBUG: current_url: {current_url}")
        match = re.search(r'searchSessionId=([a-f0-9-]+)', current_url)
        if match:
            return match.group(1)
        return ""
    except Exception as e:
        print(f"Ошибка при извлечении searchSessionId: {e}")
        return ""

async def get_max_pages_from_pagination(page: Page, total_count: int = 0) -> int:
    """Определение максимального номера страницы из контейнера пагинации."""
    try:
        # Ищем внутри nav[data-qa="pager-block"]
        pager_container = await page.query_selector("nav[data-qa='pager-block']")
        if not pager_container:
            return 0
        
        page_links = await pager_container.query_selector_all("a[data-qa='pager-page']")
        max_page = 0
        for link in page_links:
            href = await link.get_attribute("href")
            match = re.search(r'page=(\d+)', href)
            if match:
                page_num = int(match.group(1))
                max_page = max(max_page, page_num)
        
        # Если есть кнопка "Далее", добавляем +1
        next_btn = await pager_container.query_selector("a[data-qa='pager-next']")
        if next_btn:
            max_page += 1
        
        # Если total_count известно, вычисляем приблизительное количество страниц (100 на страницу)
        if total_count > 0:
            estimated_pages = (total_count + 99) // 100
            max_page = max(max_page, estimated_pages)
        
        return max_page + 1  # +1, так как page=0 — первая страница
    except Exception as e:
        print(f"Ошибка при извлечении максимальной страницы: {e}")
        return 0

def find_matching_sequence(cache: List[Dict[str, str]], current_page_vacancies: List[Dict[str, str]]) -> int:
    """Находит индекс в кэше, где current_page_vacancies совпадает с последовательностью из 100 вакансий."""
    if len(current_page_vacancies) != 100:
        return -1  # Только если ровно 100
    
    # Улучшенная нормализация: только title (игнорируем URL, т.к. они динамические)
    # Убираем пунктуацию, тире, слэши; лишние пробелы; lower
    def norm_title(title):
        # Удаляем пунктуацию и нормализуем
        title_clean = re.sub(r'[.,!?;:\-/()]+', ' ', title.strip())  # Убираем ., !? ; : - / ( )
        title_norm = re.sub(r'\s+', ' ', title_clean).lower().strip()
        return title_norm
    
    current_norm = [norm_title(vac['title']) for vac in current_page_vacancies]
    cache_norm = [norm_title(vac['title']) for vac in cache]
    
    # Debug: print первых 3 для сравнения
    print("🔍 Debug: Первые 3 нормализованных title из current страницы:")
    for i in range(min(3, len(current_norm))):
        print(f"  {i+1}: {current_norm[i]}")
    print("🔍 Первые 3 нормализованных title из кэша:")
    for i in range(min(3, len(cache_norm))):
        print(f"  {i+1}: {cache_norm[i]}")
    
    for i in range(len(cache_norm) - 99):
        if cache_norm[i:i+100] == current_norm:
            print(f"Найдено совпадение с кэшем начиная с индекса {i} (100 вакансий идентичны по порядку).")
            return i
    
    # Опциональный fuzzy fallback: если >90% совпадают (раскомментируйте если нужно)
    # from difflib import SequenceMatcher
    # max_similarity = 0
    # best_i = -1
    # for i in range(len(cache_norm) - 99):
    #     sim = SequenceMatcher(None, cache_norm[i:i+100], current_norm).ratio()
    #     if sim > 0.9 and sim > max_similarity:
    #         max_similarity = sim
    #         best_i = i
    # if best_i != -1:
    #     print(f"Fuzzy совпадение (90%+) с индекса {best_i} (similarity: {max_similarity:.2f}).")
    #     return best_i
    
    print("Совпадение не найдено даже после улучшенной нормализации.")
    return -1

async def search_vacancies(page: Page, resume_id: str, initial_max_pages: int = 100) -> Tuple[List[Dict[str, str]], int]:
    """Поиск вакансий по резюме с динамическим определением max_pages из пагинации и кэшированием."""
    print("Поиск вакансий по резюме...")
    # Убираем area=1 для поиска по всей России, если в Москве 0
    base_url = f"https://hh.ru/search/vacancy?resume={resume_id}&hhtmFromLabel=rec_vacancy_show_all&hhtmFrom=main&search_field=name&search_field=company_name&search_field=description&enable_snippets=true&forceFiltersSaving=true&items_on_page=100"
    
    # Загрузка первой страницы
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    print("Страница поиска по резюме загружена.")
    
    # Сделать скриншот для дебага
    await page.screenshot(path='debug_search_page.png')
    # print("DEBUG: Скриншот сохранён как debug_search_page.png")
    
    # Извлечение session_id и total_count
    search_session_id = await get_search_session_id(page)
    if search_session_id:
        base_url += f"&searchSessionId={search_session_id}"
    total_count = await get_total_vacancies(page)
    print(f"Всего найдено вакансий на hh.ru: {total_count}")
    
    # Парсинг первой страницы (current_page_num=0)
    current_page_num = 0
    page_url = f"{base_url}&page={current_page_num}"
    await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
    print(f"Загружена страница 1 (URL: page={current_page_num})")
    
    try:
        await page.wait_for_selector("div.vacancy-info--ieHKDTkezpEj0Gsx", timeout=30000)
    except:
        print("Контейнер вакансий не найден на первой странице.")
        return [], total_count
    
    vacancy_blocks = await page.query_selector_all("div.vacancy-info--ieHKDTkezpEj0Gsx")
    first_page_vacancies = []
    for block in vacancy_blocks:
        title_elem = await block.query_selector("a[data-qa='serp-item__title']")
        if title_elem:
            title = await title_elem.inner_text()
            href = await title_elem.get_attribute("href")
            if href and not href.startswith('https://'):
                href = f"https://hh.ru{href}" if href.startswith('/') else f"https://hh.ru/{href}"
            first_page_vacancies.append({"title": title, "url": href})
    
    if len(first_page_vacancies) != 100:
        print(f"На первой странице найдено {len(first_page_vacancies)} вакансий, не 100 — обновляем кэш заново.")
        use_cache = False
        all_vacancies = first_page_vacancies
        start_page_num = 1
    else:
        # Проверка кэша: скип если total_count сильно отличается
        cache = load_cache()
        if abs(total_count - len(cache)) / max(1, len(cache)) > 0.05:  # >5% разница — скип
            print(f"Total_count ({total_count}) сильно отличается от кэша ({len(cache)}), обновляем заново.")
            use_cache = False
            all_vacancies = first_page_vacancies
            start_page_num = 1
        else:
            matching_index = find_matching_sequence(cache, first_page_vacancies)
            if matching_index != -1:
                print(f"Кэш корректный. Начинаем обработку с индекса {matching_index} из кэша.")
                all_vacancies = cache[matching_index:]
                use_cache = True
                # Продолжаем парсинг с следующей страницы после совпадения
                start_page_num = current_page_num + 1
            else:
                print("Первая страница не совпадает с кэшем — обновляем кэш заново.")
                use_cache = False
                all_vacancies = first_page_vacancies
                start_page_num = 1
    print(f"Страница 1: найдено {len(first_page_vacancies)} вакансий.")
    
    if not use_cache:
        # Если не используем кэш, парсим все заново
        current_page_num = start_page_num
        max_pages = initial_max_pages
        while current_page_num < max_pages:
            # Динамическое обновление max_pages из пагинации
            dynamic_max = await get_max_pages_from_pagination(page, total_count)
            if dynamic_max > 0:
                max_pages = max(max_pages, dynamic_max)
                print(f"Обновлён max_pages из пагинации: {max_pages}")
            
            page_url = f"{base_url}&page={current_page_num}"
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            print(f"Загружена страница {current_page_num + 1} (URL: page={current_page_num})")
            
            try:
                await page.wait_for_selector("div.vacancy-info--ieHKDTkezpEj0Gsx", timeout=30000)
            except:
                print(f"Контейнер вакансий не найден на странице {current_page_num + 1}. Возможно, конец пагинации.")
                break
            
            vacancy_blocks = await page.query_selector_all("div.vacancy-info--ieHKDTkezpEj0Gsx")
            page_vacancies = []
            for block in vacancy_blocks:
                title_elem = await block.query_selector("a[data-qa='serp-item__title']")
                if title_elem:
                    title = await title_elem.inner_text()
                    href = await title_elem.get_attribute("href")
                    if href and not href.startswith('https://'):
                        href = f"https://hh.ru{href}" if href.startswith('/') else f"https://hh.ru/{href}"
                    page_vacancies.append({"title": title, "url": href})
            
            if len(page_vacancies) == 0:
                print(f"Нет вакансий на странице {current_page_num + 1}. Конец пагинации.")
                break
            
            all_vacancies.extend(page_vacancies)
            print(f"Страница {current_page_num + 1}: найдено {len(page_vacancies)} вакансий. Всего собрано: {len(all_vacancies)} (общее на hh.ru: {total_count})")
            
            # Проверка: если число отпаршенных совпало с общим - прекращаем парсинг
            if len(all_vacancies) >= total_count:
                print(f"Число отпаршенных вакансий ({len(all_vacancies)}) достигло общего ({total_count}) - прекращаем парсинг.")
                break
            
            # Проверка на следующую страницу
            next_link = await page.query_selector(f"a[href*='page={current_page_num + 1}']")
            if not next_link:
                print("Ссылка на следующую страницу не найдена, конец пагинации.")
                break
            
            current_page_num += 1
            await asyncio.sleep(random.uniform(2, 4))  # Случайная пауза 2-4 сек
        
        save_cache(all_vacancies)
    else:
        # Если используем кэш, продолжаем парсинг только новых страниц после совпадения
        current_page_num = start_page_num
        max_pages = initial_max_pages
        while current_page_num < max_pages:
            # Динамическое обновление max_pages из пагинации
            dynamic_max = await get_max_pages_from_pagination(page, total_count)
            if dynamic_max > 0:
                max_pages = max(max_pages, dynamic_max)
                print(f"Обновлён max_pages из пагинации: {max_pages}")
            
            page_url = f"{base_url}&page={current_page_num}"
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            print(f"Загружена страница {current_page_num + 1} (URL: page={current_page_num})")
            
            try:
                await page.wait_for_selector("div.vacancy-info--ieHKDTkezpEj0Gsx", timeout=30000)
            except:
                print(f"Контейнер вакансий не найден на странице {current_page_num + 1}. Возможно, конец пагинации.")
                break
            
            vacancy_blocks = await page.query_selector_all("div.vacancy-info--ieHKDTkezpEj0Gsx")
            page_vacancies = []
            for block in vacancy_blocks:
                title_elem = await block.query_selector("a[data-qa='serp-item__title']")
                if title_elem:
                    title = await title_elem.inner_text()
                    href = await title_elem.get_attribute("href")
                    if href and not href.startswith('https://'):
                        href = f"https://hh.ru{href}" if href.startswith('/') else f"https://hh.ru/{href}"
                    page_vacancies.append({"title": title, "url": href})
            
            if len(page_vacancies) == 0:
                print(f"Нет вакансий на странице {current_page_num + 1}. Конец пагинации.")
                break
            
            all_vacancies.extend(page_vacancies)
            print(f"Страница {current_page_num + 1}: найдено {len(page_vacancies)} вакансий. Всего собрано: {len(all_vacancies)} (общее на hh.ru: {total_count})")
            
            # Проверка: если число отпаршенных совпало с общим - прекращаем парсинг
            if len(all_vacancies) >= total_count:
                print(f"Число отпаршенных вакансий ({len(all_vacancies)}) достигло общего ({total_count}) - прекращаем парсинг.")
                break
            
            # Проверка на следующую страницу
            next_link = await page.query_selector(f"a[href*='page={current_page_num + 1}']")
            if not next_link:
                print("Ссылка на следующую страницу не найдена, конец пагинации.")
                break
            
            current_page_num += 1
            await asyncio.sleep(random.uniform(2, 4))  # Случайная пауза 2-4 сек
        
        # Обновляем кэш новыми вакансиями
        cache = load_cache()  # Перезагружаем, чтобы добавить в конец
        cache.extend(all_vacancies[len(first_page_vacancies):])  # Добавляем только новые (после первой страницы)
        save_cache(cache)
        all_vacancies = cache[matching_index:]  # Возвращаем с позиции совпадения
    
    print(f"Всего собрано {len(all_vacancies)} вакансий для обработки (из {total_count} на hh.ru).")
    if len(all_vacancies) == 0:
        print("Возможно, селекторы изменились или требуется логин.")
    return all_vacancies, total_count
