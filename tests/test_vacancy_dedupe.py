from autohhkek.services.vacancy_dedupe import dedupe_remote_same_posting_different_region, merge_serp_by_url


def test_merge_serp_by_url_keeps_first_order_and_merges_richer_text():
    items = [
        {"url": "https://hh.ru/vacancy/1", "title": "A", "description": "short"},
        {"url": "https://hh.ru/vacancy/1", "title": "A", "description": "much longer body text " * 5},
        {"url": "https://hh.ru/vacancy/2", "title": "B", "description": "x"},
    ]
    merged = merge_serp_by_url(items)
    assert len(merged) == 2
    by_url = {m["url"]: m for m in merged}
    assert "much longer" in by_url["https://hh.ru/vacancy/1"]["description"]


def test_dedupe_remote_same_posting_different_region_collapses_twin_cards():
    body = "одинаковое описание вакансии " * 8
    items = [
        {
            "url": "https://hh.ru/vacancy/10",
            "title": "Python разработчик",
            "company": "ООО Рога",
            "location": "Москва",
            "description": body,
            "is_remote": "true",
        },
        {
            "url": "https://hh.ru/vacancy/11",
            "title": "Python разработчик",
            "company": "ООО Рога",
            "location": "Казань",
            "description": body,
            "is_remote": "true",
        },
        {
            "url": "https://hh.ru/vacancy/12",
            "title": "Другая роль",
            "company": "ИП Копыта",
            "location": "Офис Москва",
            "description": "совсем другой текст " * 4,
            "is_remote": "false",
        },
    ]
    out, removed = dedupe_remote_same_posting_different_region(items)
    assert removed == 1
    assert len(out) == 2
