import asyncio

from autohhkek.domain.models import Vacancy
from autohhkek.services.hh_refresh import HHVacancyRefresher
from autohhkek.services.storage import WorkspaceStore
from logic.vacancy_parser import extract_page_vacancies, get_search_session_id, get_total_vacancies, goto_with_retry, search_vacancies


def test_hh_refresh_skips_without_resume_id(tmp_path):
    store = WorkspaceStore(tmp_path)

    result = HHVacancyRefresher(store, search_runner=lambda resume_id, limit: []).refresh(limit=10)

    assert result["status"] == "skipped"
    assert result["reason"] == "resume_id_missing"


def test_hh_refresh_saves_vacancies_from_runner(tmp_path):
    store = WorkspaceStore(tmp_path)
    state_path = tmp_path / "hh_state.json"
    state_path.write_text('{"cookies": []}', encoding="utf-8")

    def runner(resume_id: str, limit: int):
        assert resume_id == "resume-123"
        assert limit == 5
        return (
            [
                Vacancy(vacancy_id="vac-1", title="LLM Engineer", url="https://hh.ru/vacancy/1"),
                Vacancy(vacancy_id="vac-2", title="ML Engineer", url="https://hh.ru/vacancy/2"),
            ],
            {
                "total_available": 1254,
                "pages_parsed": 13,
                "search_url": "https://hh.ru/search/vacancy?resume=resume-123",
            },
        )

    result = HHVacancyRefresher(
        store,
        resume_id="resume-123",
        state_path=state_path,
        search_runner=runner,
    ).refresh(limit=5)

    assert result["status"] == "updated"
    assert result["count"] == 2
    assert result["total_available"] == 1254
    assert result["pages_parsed"] == 13
    assert len(store.load_vacancies()) == 2


def test_hh_refresh_clears_stale_vacancies_when_live_search_returns_empty(tmp_path):
    store = WorkspaceStore(tmp_path)
    state_path = tmp_path / "hh_state.json"
    state_path.write_text('{"cookies": []}', encoding="utf-8")
    store.save_vacancies([Vacancy(vacancy_id="stale-1", title="Old vacancy", url="https://hh.ru/vacancy/old")])

    result = HHVacancyRefresher(
        store,
        resume_id="resume-123",
        state_path=state_path,
        search_runner=lambda resume_id, limit: [],
    ).refresh(limit=5)

    assert result["status"] == "empty"
    assert store.load_vacancies() == []


def test_goto_with_retry_retries_on_transient_network_errors():
    class FakePage:
        def __init__(self):
            self.calls = 0

        async def goto(self, url, wait_until="domcontentloaded", timeout=60000):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("Page.goto: net::ERR_NETWORK_CHANGED")
            return None

    page = FakePage()
    asyncio.run(goto_with_retry(page, "https://hh.ru/search/vacancy"))

    assert page.calls == 3


def test_extract_page_vacancies_uses_locator_cards_instead_of_element_handle_locator():
    class FakePage:
        async def evaluate(self, script):
            return [
                {
                    "title": "LLM Engineer",
                    "href": "https://hh.ru/vacancy/123",
                    "lines": [
                        "LLM Engineer",
                        "от 300 000 ₽",
                        "Spice IT",
                        "Москва",
                        "Можно удалённо",
                        "Python, Transformers",
                    ],
                }
            ]

    vacancies = asyncio.run(extract_page_vacancies(FakePage()))

    assert len(vacancies) == 1
    assert vacancies[0]["url"] == "https://hh.ru/vacancy/123"
    assert vacancies[0]["company"] == "Spice IT"
    assert vacancies[0]["is_remote"] == "true"


def test_search_vacancies_does_not_reload_first_page_with_page_zero():
    class FakeLocator:
        def __init__(self, text="", href=""):
            self._text = text
            self._href = href

        @property
        def first(self):
            return self

        async def count(self):
            return 1 if self._text or self._href else 0

        async def inner_text(self):
            return self._text

        async def get_attribute(self, name):
            return self._href if name == "href" else None

    class FakeCard:
        def locator(self, selector):
            mapping = {
                "a[data-qa='serp-item__title']": FakeLocator("LLM Engineer", "/vacancy/123"),
                "[data-qa='vacancy-serp__vacancy-employer'], [data-qa='vacancy-serp__vacancy-employer-text']": FakeLocator("Spice IT"),
                "[data-qa='vacancy-serp__vacancy-compensation']": FakeLocator("от 300 000 ₽"),
                "[data-qa='vacancy-serp__vacancy-address'], [data-qa='vacancy-serp__vacancy-address-text']": FakeLocator("Москва"),
                "[data-qa='vacancy-serp__vacancy-work-experience']": FakeLocator("3–6 лет"),
                "[data-qa='vacancy-serp__vacancy-busy'], [data-qa='vacancy-serp__vacancy-employment']": FakeLocator("Полная занятость"),
                "[data-qa='vacancy-serp__vacancy_snippet_responsibility']": FakeLocator("LLM и NLP"),
                "[data-qa='vacancy-serp__vacancy_snippet_requirement']": FakeLocator("Python, Transformers"),
                "[data-qa='labels-wrapper']": FakeLocator("Можно удалённо"),
            }
            return mapping.get(selector, FakeLocator())

    class FakeCards:
        async def count(self):
            return 1

        def nth(self, index):
            return FakeCard()

    class FakePage:
        def __init__(self):
            self.goto_calls = []
            self.url = "https://hh.ru/search/vacancy?resume=resume-123&searchSessionId=session-1"

        async def goto(self, url, wait_until="domcontentloaded", timeout=60000):
            self.goto_calls.append(url)

        async def screenshot(self, path):
            return None

        async def wait_for_timeout(self, ms: int):
            return None

        async def inner_text(self, selector):
            assert selector == "body"
            return "Найдено 1 296 подходящих вакансий"

        async def wait_for_selector(self, selector, timeout=30000):
            return True

        async def evaluate(self, script):
            return [
                {
                    "title": "LLM Engineer",
                    "href": "https://hh.ru/vacancy/123",
                    "lines": [
                        "LLM Engineer",
                        "от 300 000 ₽",
                        "Spice IT",
                        "Москва",
                        "Можно удалённо",
                        "Python, Transformers",
                    ],
                }
            ]

        def locator(self, selector):
            if selector == "[data-qa='serp-item']":
                return FakeCards()
            if selector == "a[href*='/vacancy/']":

                class VacancyAnchors:
                    async def count(self):
                        return 1

                return VacancyAnchors()
            raise AssertionError(f"Unexpected locator selector: {selector}")

        async def query_selector(self, selector):
            if selector == "nav[data-qa='pager-block']":
                return None
            if selector == "a[data-qa='pager-next']":
                return None
            raise AssertionError(f"Unexpected query_selector selector: {selector}")

    page = FakePage()
    vacancies, total_count, meta = asyncio.run(
        search_vacancies(
            page,
            "resume-123",
            query_params={"remote_work": "1"},
            max_pages_cap=2,
            persist_serp_cache=False,
        )
    )

    assert page.goto_calls
    assert "page=0" not in page.goto_calls[0]
    assert all("page=0" not in url for url in page.goto_calls)
    assert len(vacancies) >= 1
    assert meta["pages_parsed"] >= 1


def test_get_total_vacancies_accepts_nbsp_and_narrow_nbsp():
    class FakePage:
        async def inner_text(self, selector):
            assert selector == "body"
            return "Найдено 1\u202f296 подходящих вакансий"

    assert asyncio.run(get_total_vacancies(FakePage())) == 1296


def test_get_search_session_id_accepts_non_hex_values():
    class FakePage:
        url = "https://hh.ru/search/vacancy?resume=1&searchSessionId=abcDEF_123-xyz&hhtmFrom=resumelist"

    assert asyncio.run(get_search_session_id(FakePage())) == "abcDEF_123-xyz"
