import asyncio
import json
import os
import random
import re
from typing import Dict, List, Tuple
from urllib.parse import urlencode

from playwright.async_api import Page

CACHE_FILE = 'vacancies_cache.json'
TRANSIENT_GOTO_ERRORS = ("ERR_NETWORK_CHANGED", "ERR_CONNECTION_RESET", "ERR_ABORTED", "ERR_HTTP2_PROTOCOL_ERROR")


def build_resume_search_url(resume_id: str, query_params: Dict[str, object] | None = None, *, page: int | None = None) -> str:
    query_params = dict(query_params or {})
    params: list[tuple[str, str]] = [
        ("resume", str(resume_id or "").strip()),
        ("from", "resumelist"),
        ("search_field", "name"),
        ("search_field", "company_name"),
        ("search_field", "description"),
        ("enable_snippets", "true"),
        ("forceFiltersSaving", "true"),
        ("items_on_page", "100"),
    ]
    text = str(query_params.get("text") or "").strip()
    if text:
        params.append(("text", text))
    salary_from = query_params.get("salary_from")
    if salary_from not in ("", None):
        params.append(("salary_from", str(salary_from)))
    area = str(query_params.get("area") or "").strip()
    if area:
        params.append(("area", area))
    if str(query_params.get("remote_work") or "") == "1":
        params.append(("work_format", "REMOTE"))
    if page is not None:
        params.append(("page", str(page)))
    return f"https://hh.ru/search/vacancy?{urlencode(params, doseq=True)}"

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


async def goto_with_retry(page: Page, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 60000, attempts: int = 3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if not any(token in message for token in TRANSIENT_GOTO_ERRORS) or attempt >= attempts:
                raise
            print(f"Повтор загрузки страницы после transient-сбоя ({attempt}/{attempts}): {message}")
            await asyncio.sleep(1.5 * attempt)
    if last_error is not None:
        raise last_error



async def get_total_vacancies(page: Page) -> int:
    try:
        all_text = await page.inner_text("body")
        patterns = (
            r'Найдено\s*([\d\s\u00a0\u202f]+)\s*подходящих.*?ваканси[яй].*?для.*?резюме',
            r'Найдено\s*([\d\s\u00a0\u202f]+)\s*подходящих.*?ваканси[йя]',
            r'Найдено\s*([\d\s\u00a0\u202f]+)\s*ваканси[йя]',
        )
        for pattern in patterns:
            match = re.search(pattern, all_text, re.IGNORECASE | re.UNICODE | re.DOTALL)
            if match:
                digits = re.sub(r"[^\d]", "", match.group(1))
                if digits:
                    return int(digits)
        return 0
    except Exception as e:
        print(f"Ошибка: {e}")
        return 0


async def extract_page_vacancies(page: Page) -> List[Dict[str, str]]:
    """Извлекает вакансии со страницы выдачи hh.ru."""
    vacancies: List[Dict[str, str]] = []
    seen: set[str] = set()
    raw_cards = await page.evaluate(
        """
        () => {
          const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
          const result = [];
          const cards = Array.from(document.querySelectorAll("[data-qa='serp-item'], article, li, [class*='vacancy-serp'], [data-qa*='vacancy-serp']"));
          for (const card of cards) {
            const link =
              card.querySelector("a[data-qa='serp-item__title']") ||
              card.querySelector("a[data-qa='vacancy-serp__vacancy-title']") ||
              card.querySelector("a[href*='/vacancy/']");
            if (!link) continue;
            const href = link.getAttribute("href") || "";
            if (!/\\/vacancy\\/\\d+/.test(href)) continue;
            const title = normalize(link.textContent);
            if (!title || title.length < 3) continue;
            const absoluteHref = new URL(href, location.origin).href;
            const salary =
              card.querySelector("[data-qa='vacancy-serp__vacancy-compensation']") ||
              card.querySelector("[data-qa='serp-item__compensation']") ||
              card.querySelector("[data-qa*='compensation']");
            const company =
              card.querySelector("[data-qa='vacancy-serp__vacancy-employer']") ||
              card.querySelector("[data-qa='serp-item__company-name']") ||
              card.querySelector("[data-qa='vacancy-serp__vacancy-employer-text']") ||
              card.querySelector("[data-qa*='vacancy-employer']") ||
              card.querySelector("[data-qa*='company']") ||
              card.querySelector("a[href*='/employer/']") ||
              card.querySelector("span[data-qa*='employer']");
            const location =
              card.querySelector("[data-qa='vacancy-serp__vacancy-address']") ||
              card.querySelector("[data-qa='vacancy-serp__vacancy-address-text']") ||
              card.querySelector("[data-qa='serp-item__location']") ||
              card.querySelector("[data-qa*='vacancy-address']") ||
              card.querySelector("[data-qa*='location']");
            const snippet =
              card.querySelector("[data-qa='vacancy-serp__vacancy_snippet_responsibility']") ||
              card.querySelector("[data-qa='vacancy-serp__vacancy_snippet_requirement']") ||
              card.querySelector("[data-qa='serp-item__description']");
            result.push({
              title,
              href: absoluteHref,
              salary_text: normalize(salary?.textContent || ""),
              company: normalize(company?.textContent || ""),
              location: normalize(location?.textContent || ""),
              summary: normalize(snippet?.textContent || ""),
              all_text: normalize(card.innerText || ""),
            });
          }
          if (result.length === 0) {
            const anchors = Array.from(document.querySelectorAll("a[data-qa='serp-item__title'], a[href*='/vacancy/']"));
            for (const link of anchors) {
              const href = link.getAttribute("href") || "";
              if (!/\\/vacancy\\/\\d+/.test(href)) continue;
              const title = normalize(link.textContent);
              if (!title || title.length < 3) continue;
              const absoluteHref = new URL(href, location.origin).href;
              let card = link.closest("article, li, div");
              let cursor = link.parentElement;
              for (let depth = 0; !card && cursor && depth < 8; depth += 1) {
                const text = normalize(cursor.innerText);
                if (text.length > title.length + 40) {
                  card = cursor;
                  break;
                }
                cursor = cursor.parentElement;
              }
              const searchRoot = card || link.parentElement || link;
              const company =
                searchRoot.querySelector?.("[data-qa*='vacancy-employer'], [data-qa*='company'], a[href*='/employer/'], span[data-qa*='employer']")?.textContent || "";
              const location =
                searchRoot.querySelector?.("[data-qa*='vacancy-address'], [data-qa*='location']")?.textContent || "";
              const salary =
                searchRoot.querySelector?.("[data-qa*='compensation']")?.textContent || "";
              result.push({
                title,
                href: absoluteHref,
                salary_text: normalize(salary),
                company: normalize(company),
                location: normalize(location),
                summary: "",
                all_text: normalize((card || link).innerText || ""),
              });
            }
          }
          return result;
        }
        """
    )
    for item in raw_cards or []:
        title = " ".join(str(item.get("title") or "").split())
        href = str(item.get("href") or "").strip()
        if not title or not href:
            continue
        if href in seen:
            continue
        seen.add(href)
        all_visible = " ".join(str(item.get("all_text") or "").split())
        salary_text = " ".join(str(item.get("salary_text") or "").split())
        company = " ".join(str(item.get("company") or "").split())
        location = " ".join(str(item.get("location") or "").split())
        summary = " ".join(str(item.get("summary") or "").split())
        lines = [" ".join(str(line).split()) for line in list(item.get("lines") or []) if str(line).strip()]
        if not all_visible and lines:
            all_visible = " ".join(lines)
        if not salary_text:
            salary_match = re.search(r"(от\s*\d[\d\s]*|до\s*\d[\d\s]*|\d[\d\s]*\s*[–-]\s*\d[\d\s]*\s*(₽|руб|€|\$)?)", all_visible, re.IGNORECASE)
            salary_text = salary_match.group(0).strip() if salary_match else ""
        if lines and (not company or not location):
            title_index = next((idx for idx, line in enumerate(lines) if title in line or line in title), 0)
            tail_lines = lines[title_index + 1 :]
            if not salary_text:
                salary_text = next(
                    (
                        line
                        for line in tail_lines[:4]
                        if re.search(r"(₽|руб|€|\$|от\s*\d|до\s*\d|\d\s*[–-]\s*\d)", line, re.IGNORECASE)
                    ),
                    "",
                )
            for line in tail_lines:
                if line == salary_text:
                    continue
                if not company and not re.search(r"(удал|remote|опыт|график|занятост|оформлен|час|смотрят|₽|руб|€|\$)", line, re.IGNORECASE):
                    company = line
                    continue
                if company and not location and not re.search(r"(удал|remote|опыт|график|занятост|оформлен|час|смотрят|₽|руб|€|\$)", line, re.IGNORECASE):
                    location = line
                    break
        if company and company.lower() == title.lower():
            company = ""
        if not company:
            company_match = re.search(r"(ООО\s+[^\n·]+|АО\s+[^\n·]+|ЗАО\s+[^\n·]+|ИП\s+[^\n·]+|[A-Z][A-Za-z0-9& ._-]{2,})", all_visible)
            if company_match:
                company = company_match.group(1).strip()
        if not location:
            location_match = re.search(r"(Москва|Санкт-Петербург|Новосибирск|Екатеринбург|Казань|Нижний Новгород|удал[её]нно|remote|гибрид|офис)", all_visible, re.IGNORECASE)
            if location_match:
                location = location_match.group(1).strip()
        if not summary:
            summary = all_visible.replace(title, "", 1).replace(company, "", 1).replace(location, "", 1).replace(salary_text, "", 1).strip()
        vacancies.append(
            {
                "title": title,
                "url": href,
                "company": company,
                "salary_text": salary_text,
                "location": location,
                "employment": "",
                "summary": summary,
                "is_remote": "true" if re.search(r"(удал[её]н|remote)", all_visible, re.IGNORECASE) else "false",
            }
        )
    return vacancies



async def get_search_session_id(page: Page) -> str:
    """Извлечение searchSessionId из URL."""
    try:
        current_url = page.url
        # print(f"DEBUG: current_url: {current_url}")
        match = re.search(r'[?&]searchSessionId=([^&]+)', current_url)
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

async def search_vacancies(
    page: Page,
    resume_id: str,
    initial_max_pages: int = 100,
    query_params: Dict[str, object] | None = None,
) -> Tuple[List[Dict[str, str]], int, Dict[str, object]]:
    """Поиск вакансий по резюме с динамическим определением max_pages из пагинации и кэшированием."""
    print("Поиск вакансий по резюме...")
    base_url = build_resume_search_url(resume_id, query_params)
    
    # Загрузка первой страницы
    await goto_with_retry(page, base_url, wait_until="domcontentloaded", timeout=60000)
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

    # Первая страница уже открыта по base_url; повторный goto на page=0 может ломать контекст поиска hh.ru.
    current_page_num = 0
    try:
        await page.wait_for_selector("a[href*='/vacancy/']", timeout=30000)
    except:
        print("Контейнер вакансий не найден на первой странице.")
        return [], total_count, {"search_url": base_url, "pages_parsed": 0, "search_session_id": search_session_id}
    
    first_page_vacancies = await extract_page_vacancies(page)
    
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
    
    pages_parsed = 1 if first_page_vacancies else 0

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
            
            page_url = build_resume_search_url(resume_id, query_params, page=current_page_num)
            if search_session_id:
                page_url = f"{page_url}&searchSessionId={search_session_id}"
            await goto_with_retry(page, page_url, wait_until="domcontentloaded", timeout=60000)
            print(f"Загружена страница {current_page_num + 1} (URL: page={current_page_num})")
            
            try:
                await page.wait_for_selector("a[href*='/vacancy/']", timeout=30000)
            except:
                print(f"Контейнер вакансий не найден на странице {current_page_num + 1}. Возможно, конец пагинации.")
                break
            
            page_vacancies = await extract_page_vacancies(page)
            
            if len(page_vacancies) == 0:
                print(f"Нет вакансий на странице {current_page_num + 1}. Конец пагинации.")
                break
            
            all_vacancies.extend(page_vacancies)
            pages_parsed += 1
            print(f"Страница {current_page_num + 1}: найдено {len(page_vacancies)} вакансий. Всего собрано: {len(all_vacancies)} (общее на hh.ru: {total_count})")
            
            # Проверка: если число отпаршенных совпало с общим - прекращаем парсинг
            if len(all_vacancies) >= total_count:
                print(f"Число отпаршенных вакансий ({len(all_vacancies)}) достигло общего ({total_count}) - прекращаем парсинг.")
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
            
            page_url = build_resume_search_url(resume_id, query_params, page=current_page_num)
            if search_session_id:
                page_url = f"{page_url}&searchSessionId={search_session_id}"
            await goto_with_retry(page, page_url, wait_until="domcontentloaded", timeout=60000)
            print(f"Загружена страница {current_page_num + 1} (URL: page={current_page_num})")
            
            try:
                await page.wait_for_selector("a[href*='/vacancy/']", timeout=30000)
            except:
                print(f"Контейнер вакансий не найден на странице {current_page_num + 1}. Возможно, конец пагинации.")
                break
            
            page_vacancies = await extract_page_vacancies(page)
            
            if len(page_vacancies) == 0:
                print(f"Нет вакансий на странице {current_page_num + 1}. Конец пагинации.")
                break
            
            all_vacancies.extend(page_vacancies)
            pages_parsed += 1
            print(f"Страница {current_page_num + 1}: найдено {len(page_vacancies)} вакансий. Всего собрано: {len(all_vacancies)} (общее на hh.ru: {total_count})")
            
            # Проверка: если число отпаршенных совпало с общим - прекращаем парсинг
            if len(all_vacancies) >= total_count:
                print(f"Число отпаршенных вакансий ({len(all_vacancies)}) достигло общего ({total_count}) - прекращаем парсинг.")
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
    return all_vacancies, total_count, {"search_url": base_url, "pages_parsed": pages_parsed, "search_session_id": search_session_id}
