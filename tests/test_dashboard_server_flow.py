import json
import time
import urllib.request
from pathlib import Path

from autohhkek.dashboard.server import start_dashboard_server
from autohhkek.services.storage import WorkspaceStore


def _request_json(method: str, url: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_dashboard_server_supports_mode_first_onboarding_flow(tmp_path):
    (tmp_path / "vacancies_cache.json").write_text(
        json.dumps(
            [
                {"title": "LLM Engineer Python Remote", "url": "https://example.com/v1"},
                {"title": "Data Scientist", "url": "https://example.com/v2"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import autohhkek.app.commands as dashboard_commands

    original_ensure_hh_context = dashboard_commands.ensure_hh_context
    dashboard_commands.ensure_hh_context = lambda store, auto_login=True: {
        "status": "ready",
        "message": "hh context ready",
        "selected_resume_id": "resume-1",
    }

    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        assert urllib.request.urlopen(handle.url + "/", timeout=30).status == 200
        assert _request_json("GET", handle.url + "/api/dashboard")[0] == 200
        assert _request_json("POST", handle.url + "/api/runtime/settings", {"dashboard_mode": "analyze", "llm_backend": "openai"})[0] == 200
        assert _request_json(
            "POST",
            handle.url + "/api/actions/intake",
            {
                "full_name": "Ivan Litvak",
                "headline": "LLM Engineer",
                "summary": "Python, NLP, agents",
                "experience_years": 5,
                "target_titles": ["LLM Engineer", "ML Engineer"],
                "primary_skills": ["Python", "LLM"],
                "required_skills": ["Python"],
                "preferred_locations": ["Moscow"],
                "salary_min": 350000,
                "remote_only": True,
                "cover_letter_mode": "adaptive",
            },
        )[0] == 200
        assert _request_json("POST", handle.url + "/api/actions/build-rules", {})[0] == 200
        run_status, run_payload = _request_json(
            "POST",
            handle.url + "/api/actions/run-selected",
            {"dashboard_mode": "analyze", "llm_backend": "openai"},
        )

        assert run_status == 200
        assert run_payload["result"]["status"] in {"started", "running"}

        deadline = time.time() + 5
        snapshot = run_payload["snapshot"]
        while time.time() < deadline:
            _, snapshot = _request_json("GET", handle.url + "/api/dashboard")
            if snapshot["analysis_job"]["status"] == "completed":
                break
            time.sleep(0.05)

        assert snapshot["analysis_job"]["status"] == "completed"
        assert snapshot["counts"]["assessed"] == 2
    finally:
        handle.close()
        dashboard_commands.ensure_hh_context = original_ensure_hh_context


def test_dashboard_server_accepts_openrouter_runtime_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json(
            "POST",
            handle.url + "/api/runtime/settings",
            {
                "dashboard_mode": "analyze",
                "llm_backend": "openrouter",
                "openrouter_model": "openai/gpt-5-nano",
            },
        )

        assert status == 200
        assert payload["snapshot"]["runtime_settings"]["llm_backend"] == "openrouter"
        assert payload["snapshot"]["runtime_settings"]["openrouter_model"] == "openai/gpt-5-nano"
    finally:
        handle.close()


def test_dashboard_server_starts_hh_login_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "autohhkek.dashboard.server.run_hh_login",
        lambda project_root: {"status": "completed", "message": "hh.ru login state saved.", "resumes": {"items": [{"resume_id": "resume-1", "title": "LLM Engineer"}]}},
    )
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json("POST", handle.url + "/api/actions/hh-login", {})

        assert status == 200
        assert payload["result"]["action"] == "hh-login"

        time.sleep(0.1)
        status, snapshot = _request_json("GET", handle.url + "/api/dashboard")
        assert status == 200
        assert snapshot["hh_login"]["status"] == "completed"
    finally:
        handle.close()


def test_dashboard_server_saves_selected_resume(tmp_path):
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json(
            "POST",
            handle.url + "/api/actions/select-resume",
            {"resume_id": "resume-42"},
        )

        assert status == 200
        assert payload["snapshot"]["selected_resume_id"] == "resume-42"
        assert payload["snapshot"]["analysis_state"]["stale"] is True
    finally:
        handle.close()


def test_dashboard_server_switches_active_hh_account(tmp_path):
    primary = WorkspaceStore(tmp_path, account_key="hh-primary")
    primary.save_account_profile({"account_key": "hh-primary", "display_name": "Primary"})
    primary.save_hh_resumes([{"resume_id": "resume-a", "title": "Primary"}])
    primary.save_selected_resume_id("resume-a")

    secondary = WorkspaceStore(tmp_path, account_key="hh-secondary")
    secondary.save_account_profile({"account_key": "hh-secondary", "display_name": "Secondary"})
    secondary.save_hh_resumes([{"resume_id": "resume-b", "title": "Secondary"}])
    secondary.save_selected_resume_id("resume-b")

    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json(
            "POST",
            handle.url + "/api/actions/select-account",
            {"account_key": "hh-primary"},
        )

        assert status == 200
        assert payload["result"]["action"] == "select-account"
        assert payload["snapshot"]["active_account"]["account_key"] == "hh-primary"
        assert payload["snapshot"]["selected_resume_id"] == "resume-a"
    finally:
        handle.close()


def test_dashboard_server_supports_chat_runtime_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json("POST", handle.url + "/api/chat", {"message": "backend openrouter"})

        assert status == 200
        assert payload["result"]["action"] == "runtime-settings"
        assert payload["snapshot"]["runtime_settings"]["llm_backend"] == "openrouter"
    finally:
        handle.close()


def test_dashboard_server_supports_plan_filters_action(tmp_path):
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        _request_json(
            "POST",
            handle.url + "/api/actions/intake",
            {
                "full_name": "Ivan Litvak",
                "headline": "LLM Engineer",
                "summary": "Python, NLP, agents",
                "experience_years": 5,
                "target_titles": ["LLM Engineer", "ML Engineer"],
                "primary_skills": ["Python", "LLM"],
                "required_skills": ["Python"],
                "preferred_locations": ["Remote"],
            },
        )

        status, payload = _request_json("POST", handle.url + "/api/actions/plan-filters", {})

        assert status == 200
        assert payload["result"]["action"] == "plan_filters"
        assert payload["snapshot"]["filter_plan"]["search_text"] == "LLM Engineer OR ML Engineer"
    finally:
        handle.close()


def test_dashboard_server_chat_rules_editor_requires_confirmation(tmp_path):
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        _request_json(
            "POST",
            handle.url + "/api/actions/intake",
            {
                "full_name": "Ivan Litvak",
                "headline": "LLM Engineer",
                "summary": "Python, NLP, agents",
                "experience_years": 5,
                "target_titles": ["LLM Engineer"],
                "primary_skills": ["Python", "LLM"],
                "required_skills": ["Python"],
                "preferred_locations": ["Remote"],
            },
        )
        _request_json("POST", handle.url + "/api/actions/build-rules", {})

        status, payload = _request_json("POST", handle.url + "/api/chat", {"message": "добавь правило: excluded_companies: Evil Corp"})

        assert status == 200
        assert payload["result"]["action"] == "propose-rules"
        assert payload["snapshot"]["pending_rule_edit"]["markdown"] == "excluded_companies: Evil Corp"
        assert "Evil Corp" not in payload["snapshot"]["intake"]["rules_preview"]

        status, payload = _request_json("POST", handle.url + "/api/chat", {"message": "подтверди правила"})

        assert status == 200
        assert payload["result"]["action"] == "import-rules"
        assert payload["snapshot"]["pending_rule_edit"] == {}
        assert "Evil Corp" in payload["snapshot"]["intake"]["rules_preview"]
    finally:
        handle.close()


def test_dashboard_server_chat_accepts_natural_language_rule_request(tmp_path):
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        _request_json(
            "POST",
            handle.url + "/api/actions/intake",
            {
                "full_name": "Ivan Litvak",
                "headline": "LLM Engineer",
                "summary": "Python, NLP, agents",
                "experience_years": 5,
                "target_titles": ["LLM Engineer"],
                "primary_skills": ["Python", "LLM"],
                "required_skills": ["Python"],
                "preferred_locations": ["Remote"],
            },
        )
        _request_json("POST", handle.url + "/api/actions/build-rules", {})

        status, payload = _request_json("POST", handle.url + "/api/chat", {"message": "Не хочу финтех и офис в Москве, только remote, зарплата от 350000"})

        assert status == 200
        assert payload["result"]["action"] == "propose-rules"
        assert "salary_min: 350000" in payload["snapshot"]["pending_rule_edit"]["markdown"]
        assert "remote_only: true" in payload["snapshot"]["pending_rule_edit"]["markdown"]
    finally:
        handle.close()


def test_dashboard_server_supports_vacancy_feedback_action(tmp_path):
    from autohhkek.domain.enums import FitCategory, ReasonGroup
    from autohhkek.domain.models import AssessmentReason, Vacancy, VacancyAssessment
    from autohhkek.services.storage import WorkspaceStore

    store = WorkspaceStore(tmp_path)
    store.save_vacancies(
        [
            Vacancy(
                vacancy_id="vac-1",
                title="LLM Engineer",
                company="Acme",
                location="Remote",
                url="https://example.com/vac-1",
            )
        ]
    )
    store.save_assessments(
        [
            VacancyAssessment(
                vacancy_id="vac-1",
                category=FitCategory.FIT,
                subcategory="good_match",
                score=92,
                explanation="Strong match",
                reasons=[AssessmentReason(code="skills", label="Навыки", group=ReasonGroup.POSITIVE, detail="Python")],
            )
        ]
    )

    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json(
            "POST",
            handle.url + "/api/actions/vacancy-feedback",
            {"vacancy_id": "vac-1", "decision": "doubt"},
        )

        assert status == 200
        assert payload["result"]["action"] == "vacancy_feedback"
        assert payload["snapshot"]["columns"]["doubt"][0]["id"] == "vac-1"
    finally:
        handle.close()


def test_dashboard_server_supports_apply_submit_action(tmp_path, monkeypatch):
    from autohhkek.domain.enums import FitCategory, ReasonGroup
    from autohhkek.domain.models import AssessmentReason, Vacancy, VacancyAssessment
    from autohhkek.services.storage import WorkspaceStore

    monkeypatch.setattr(
        "autohhkek.services.hh_apply.run_hh_apply",
        lambda **kwargs: {"status": "completed", "message": "apply completed", "vacancy_url": kwargs["vacancy_url"]},
    )

    store = WorkspaceStore(tmp_path)
    store.save_selected_resume_id("resume-1")
    store.save_vacancies(
        [
            Vacancy(
                vacancy_id="vac-1",
                title="LLM Engineer",
                company="Acme",
                location="Remote",
                url="https://example.com/vac-1",
            )
        ]
    )
    store.save_assessments(
        [
            VacancyAssessment(
                vacancy_id="vac-1",
                category=FitCategory.FIT,
                subcategory="good_match",
                score=92,
                explanation="Strong match",
                reasons=[AssessmentReason(code="skills", label="Навыки", group=ReasonGroup.POSITIVE, detail="Python")],
            )
        ]
    )

    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json(
            "POST",
            handle.url + "/api/actions/apply-submit",
            {"vacancy_id": "vac-1", "cover_letter": "custom text"},
        )

        assert status == 200
        assert payload["result"]["action"] == "apply_submit"
        assert payload["result"]["payload"]["result"]["status"] == "completed"
        assert payload["snapshot"]["vacancy_feedback"]["vac-1"]["last_apply_status"] == "completed"
    finally:
        handle.close()


def test_dashboard_server_starts_apply_batch_action(tmp_path, monkeypatch):
    from autohhkek.domain.enums import FitCategory, ReasonGroup
    from autohhkek.domain.models import AssessmentReason, Vacancy, VacancyAssessment
    from autohhkek.services.storage import WorkspaceStore

    monkeypatch.setattr(
        "autohhkek.app.commands.run_apply_batch",
        lambda store, category, **kwargs: {
            "action": "apply_batch",
            "category": category,
            "attempted": 1,
            "applied": 1,
            "failed": 0,
            "message": "batch completed",
        },
    )

    store = WorkspaceStore(tmp_path)
    store.save_selected_resume_id("resume-1")
    store.save_vacancies(
        [
            Vacancy(
                vacancy_id="vac-1",
                title="LLM Engineer",
                company="Acme",
                location="Remote",
                url="https://example.com/vac-1",
            )
        ]
    )
    store.save_assessments(
        [
            VacancyAssessment(
                vacancy_id="vac-1",
                category=FitCategory.FIT,
                subcategory="good_match",
                score=92,
                explanation="Strong match",
                reasons=[AssessmentReason(code="skills", label="Навыки", group=ReasonGroup.POSITIVE, detail="Python")],
            )
        ]
    )

    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json("POST", handle.url + "/api/actions/apply-batch", {"category": "fit"})
        assert status == 200
        assert payload["result"]["action"] == "apply_batch"
        assert payload["result"]["status"] == "running"
    finally:
        handle.close()


def test_dashboard_server_captures_client_log(tmp_path):
    from autohhkek.services.storage import WorkspaceStore

    store = WorkspaceStore(tmp_path)
    handle = start_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    try:
        status, payload = _request_json(
            "POST",
            handle.url + "/api/client-log",
            {"kind": "window-error", "payload": {"message": "boom", "lineno": 10}},
        )

        assert status == 200
        assert payload["result"]["action"] == "client-log"
        assert payload["result"]["status"] == "captured"
        assert Path(payload["result"]["debug_artifact"]).exists()
        assert any(event["kind"] == "dashboard-client" for event in store.load_events())
    finally:
        handle.close()
