import asyncio
from playwright.async_api import Page
from typing import List, Dict

async def search_vacancies(page: Page, resume_id: str, max_vacancies: int = 10, max_pages: int = 5) -> List[Dict[str, str]]:
    """Поиск вакансий по резюме через UI с пагинацией."""
    print("Поиск вакансий по резюме...")
    search_url = f"https://hh.ru/search/vacancy?resume={resume_id}&hhtmFromLabel=rec_vacancy_show_all&hhtmFrom=main&area=1&search_field=name&search_field=company_name&search_field=description&enable_snippets=true"
    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    print("Страница поиска по резюме загружена.")
    
    all_vacancies = []
    current_page = 0
    while len(all_vacancies) < max_vacancies and current_page < max_pages:
        # Ждем первый контейнер вакансий
        try:
            await page.wait_for_selector("div.vacancy-info--ieHKDTkezpEj0Gsx", timeout=30000)
        except:
            print("Контейнер вакансий не найден на странице", current_page + 1)
            break
        
        # Извлекаем все блоки вакансий на текущей странице
        vacancy_blocks = await page.query_selector_all("div.vacancy-info--ieHKDTkezpEj0Gsx")
        page_vacancies = []
        for block in vacancy_blocks:
            # Заголовок вакансии
            title_elem = await block.query_selector("a[data-qa='serp-item__title']")
            if title_elem:
                title = await title_elem.inner_text()
                href = await title_elem.get_attribute("href")
                if href and not href.startswith('https://'):
                    href = f"https://hh.ru{href}" if href.startswith('/') else f"https://hh.ru/{href}"
                page_vacancies.append({"title": title, "url": href})
        
        all_vacancies.extend(page_vacancies)
        print(f"Страница {current_page + 1}: найдено {len(page_vacancies)} вакансий. Всего: {len(all_vacancies)}")
        
        if len(all_vacancies) >= max_vacancies:
            break
        
        # Клик на следующую страницу
        next_btn = await page.query_selector("a[data-qa='pager-next']")
        if next_btn:
            await next_btn.click()
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            current_page += 1
            # Имитация скролла/паузы
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        else:
            print("Кнопка 'Далее' не найдена, конец пагинации.")
            break
    
    # Обрезаем до лимита
    all_vacancies = all_vacancies[:max_vacancies]
    
    print(f"Всего найдено {len(all_vacancies)} вакансий.")
    if len(all_vacancies) == 0:
        print("Возможно, селекторы изменились или требуется логин для просмотра результатов.")
    return all_vacancies