from __future__ import annotations

import json
import re
import threading
from difflib import unified_diff
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from autohhkek.domain.models import utc_now_iso
from autohhkek.services.hh_resume_sync import HHResumeProfileSync
from autohhkek.app.commands import (
    begin_intake_dialog,
    restart_intake_dialog,
    build_detailed_intake_prompt,
    build_rules_from_profile,
    choose_heuristic_fallback,
    confirm_intake_rules,
    continue_intake_dialog,
    import_rules_text,
    postpone_until_llm_available,
    run_analyze,
    run_refresh_vacancies,
    run_apply_batch,
    run_apply_submit,
    run_intake,
    run_intake_from_file,
    run_intake_from_text,
    run_plan_filters,
    run_plan_apply,
    run_plan_repair,
    run_resume,
    run_selected_mode,
    save_cover_letter_override,
    delete_hh_account,
    select_hh_account,
    select_resume_for_search,
    update_vacancy_feedback,
    update_runtime_settings,
)
from autohhkek.services.hh_login import run_hh_login
from autohhkek.services.hh_resume_catalog import HHResumeCatalog
from autohhkek.services.chat_rule_parser import parse_rule_request, patch_to_markdown
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig
from autohhkek.services.storage import WorkspaceStore

from .snapshot import build_dashboard_snapshot


ASSETS_DIR = Path(__file__).resolve().parent / "assets"


@dataclass
class DashboardHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    url: str

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _asset_response(path: Path) -> tuple[bytes, str]:
    suffix_map = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
    }
    return path.read_bytes(), suffix_map.get(path.suffix, "application/octet-stream")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", _repair_mojibake_text((value or "").strip())).lower()


def _repair_mojibake_text(value: str) -> str:
    current = str(value or "")
    def _marker_count(text: str) -> int:
        markers = 0
        for first, second in zip(text, text[1:]):
            if first in {"Р", "С"} and second and second not in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя \t\r\n":
                if ord(second) > 127:
                    markers += 1
            if first in {"Ð", "Ñ"} and second and ord(second) > 127:
                markers += 1
        markers += text.count("�") * 3
        markers += text.count("пїЅ") * 3
        markers += text.count("\\x") * 2
        markers += text.count("07@") * 2
        return markers

    if _marker_count(current) < 2:
        return current
    for codec in ("cp1251", "latin1"):
        for _ in range(2):
            try:
                candidate = current.encode(codec, errors="ignore").decode("utf-8", errors="ignore")
            except UnicodeError:
                break
            if not candidate or candidate == current:
                break
            if _marker_count(candidate) >= _marker_count(current):
                break
            current = candidate
    return current


def _repair_payload_strings(value):
    if isinstance(value, str):
        return _repair_mojibake_text(value)
    if isinstance(value, list):
        return [_repair_payload_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_repair_payload_strings(item) for item in value)
    if isinstance(value, dict):
        return {key: _repair_payload_strings(item) for key, item in value.items()}
    return value


def _normalize_patterns(patterns: tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    return tuple(_normalize_text(item) for item in patterns)


def _contains_any(normalized: str, patterns) -> bool:
    return any(pattern and pattern in normalized for pattern in _normalize_patterns(patterns))


def _equals_any(normalized: str, patterns) -> bool:
    return normalized in set(_normalize_patterns(patterns))


def _chat_response(message: str, *, action: str = "", details: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "message": message,
        "action": action,
        "details": details or {},
    }


def _openrouter_chat_reply(store: WorkspaceStore, *, text: str, selected_vacancy_id: str = "") -> dict[str, object] | None:
    config = OpenRouterAppConfig.from_env()
    if not config.is_available():
        return None
    settings = store.load_runtime_settings()
    config.model = str(getattr(settings, "openrouter_model", "") or config.model)
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            default_headers={
                **({"HTTP-Referer": config.site_url} if config.site_url else {}),
                **({"X-OpenRouter-Title": config.app_name} if config.app_name else {}),
            } or None,
            timeout=config.timeout_sec,
        )
        selected_resume_id = store.load_selected_resume_id()
        resume_items = list(store.load_hh_resumes())
        selected_resume = next((item for item in resume_items if str(item.get("resume_id") or "") == selected_resume_id), None)
        prompt = (
            "Ты ассистент в дашборде AutoHHKek. "
            "Отвечай по-русски, коротко и по делу. "
            "Если пользователь пишет короткую реплику вроде приветствия или проверки связи, ответь естественно и попроси сформулировать задачу. "
            "Не придумывай, что уже запустил repair, анализ или отклик, если этого не было. "
            "Если из сообщения не следует конкретная команда, не запускай фоновые действия сам. "
            f"Текущий selected_resume_id: {selected_resume_id or 'не выбран'}. "
            f"Текущий selected_resume_title: {str((selected_resume or {}).get('title') or '')[:240] or 'не выбрано'}. "
            f"Текущий selected_vacancy_id: {selected_vacancy_id or 'не выбрана'}.\n\n"
            f"Сообщение пользователя: {text.strip()}"
        )
        response = client.responses.create(model=config.model, input=prompt)
        message = str(getattr(response, "output_text", "") or "").strip()
        if not message:
            return None
        return {"message": message, "backend": "openrouter", "model": config.model}
    except Exception as exc:  # noqa: BLE001
        return {"message": "", "backend": "openrouter", "model": config.model, "error": str(exc)}


def _build_rules_proposal(*, current_rules: str, markdown: str, filename: str = "chat_rules.md") -> dict[str, object]:
    current_lines = (current_rules or "").splitlines()
    proposal_block = f"\n# Proposed chat rule edit\n\nSource: {filename}\n\n{markdown.strip()}\n"
    proposed_rules = (current_rules.rstrip() + proposal_block) if current_rules.strip() else proposal_block.lstrip()
    diff_lines = list(
        unified_diff(
            current_lines,
            proposed_rules.splitlines(),
            fromfile="current_rules",
            tofile="proposed_rules",
            lineterm="",
        )
    )
    return {
        "filename": filename,
        "markdown": markdown.strip(),
        "current_rules_preview": current_rules[:3000],
        "proposed_rules_preview": proposed_rules[:3000],
        "diff": "\n".join(diff_lines[:200]),
    }


def _extract_rule_request_payload(text: str) -> tuple[str, str]:
    normalized = _normalize_text(text)
    canonical_prefixes = (
        "добавь правило:",
        "обнови правила:",
        "правила:",
        "предложи правило:",
    )
    for prefix in canonical_prefixes:
        if normalized.startswith(prefix):
            return prefix, text.split(":", 1)[1].strip() if ":" in text else ""
    return "", ""


def _find_resume_reference(store: WorkspaceStore, text: str) -> tuple[str, str]:
    items = store.load_hh_resumes()
    if not items:
        return "", ""
    match = re.search(r"\b(\d+)\b", text)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(items):
            item = items[index]
            return str(item.get("resume_id") or ""), str(item.get("title") or item.get("resume_id") or "")
    for item in items:
        title = str(item.get("title") or "")
        resume_id = str(item.get("resume_id") or "")
        if title and title.lower() in text:
            return resume_id, title
        if resume_id and resume_id.lower() in text:
            return resume_id, title or resume_id
    return "", ""


def _should_auto_bootstrap(snapshot: dict[str, object], hh_login_status: dict[str, object], bootstrap_status: dict[str, object]) -> bool:
    if bool(hh_login_status.get("running")) or bool(bootstrap_status.get("running")):
        return False
    if not bool((snapshot.get("intake") or {}).get("ready")):
        return False
    if not bool((snapshot.get("hh_login") or {}).get("state_file_exists")):
        return False
    profile_sync = dict(snapshot.get("profile_sync") or {})
    if str(profile_sync.get("status") or "") not in {"updated", "no_changes"}:
        return True
    hh_resumes = list(snapshot.get("hh_resumes") or [])
    if len(hh_resumes) == 1 and not str(snapshot.get("selected_resume_id") or "").strip():
        return True
    return False


def _run_first_bootstrap(
    *,
    project_root: Path,
    hh_login_status: dict[str, object],
    bootstrap_status: dict[str, object],
) -> None:
    store = WorkspaceStore(project_root)
    store.touch_dashboard_timestamp("last_auto_bootstrap_at", extra={"last_auto_bootstrap_status": "running"})
    bootstrap_status.update(
        {
            "running": True,
            "status": "running",
            "message": "Проверяю логин, резюме и правила перед первым запуском.",
            "started_at": utc_now_iso(),
            "finished_at": "",
        }
    )
    try:
        if store.hh_state_path.exists():
            login_result = {"status": "completed", "message": "Сессия hh.ru уже активна, повторный логин не нужен."}
            hh_login_status.update(
                {
                    "running": False,
                    "status": "completed",
                    "message": str(login_result.get("message") or ""),
                    "finished_at": utc_now_iso(),
                }
            )
        else:
            hh_login_status.update(
                {
                    "running": True,
                    "status": "running",
                    "message": "Открываю hh.ru для входа.",
                    "started_at": utc_now_iso(),
                    "finished_at": "",
                }
            )
            login_result = run_hh_login(project_root)
            hh_login_status.update(
                {
                    "running": False,
                    "status": str(login_result.get("status") or "failed"),
                    "message": str(login_result.get("message") or ""),
                    "finished_at": utc_now_iso(),
                }
            )

        resumes_payload = dict((login_result.get("resumes") or {}))
        resume_items = list(resumes_payload.get("items") or [])
        if not resume_items:
            resumes_payload = HHResumeCatalog(store).refresh()
            resume_items = list(resumes_payload.get("items") or [])

        selected_resume_id = store.load_selected_resume_id()
        if len(resume_items) == 1 and not selected_resume_id:
            select_resume_for_search(store, resume_id=str(resume_items[0].get("resume_id") or ""))
            selected_resume_id = store.load_selected_resume_id()

        if store.load_preferences() and store.load_anamnesis():
            if selected_resume_id:
                resume_result = run_resume(store)
                bootstrap_status["message"] = str(resume_result.get("message") or "Резюме и правила обновлены.")
            else:
                rules_result = build_rules_from_profile(store)
                bootstrap_status["message"] = "Правила обновлены. Для откликов нужно выбрать резюме."
                bootstrap_status["details"] = rules_result

        bootstrap_status.update(
            {
                "running": False,
                "status": "completed",
                "finished_at": utc_now_iso(),
                "resumes_found": len(resume_items),
                "selected_resume_id": selected_resume_id,
            }
        )
        store.update_dashboard_state(
            {
                "last_auto_bootstrap_status": "completed",
                "last_auto_bootstrap_message": str(bootstrap_status.get("message") or ""),
                "last_auto_bootstrap_finished_at": str(bootstrap_status.get("finished_at") or ""),
            }
        )
    except Exception as exc:  # noqa: BLE001
        hh_login_status.update(
            {
                "running": False,
                "status": "failed",
                "message": str(exc),
                "finished_at": utc_now_iso(),
            }
        )
        bootstrap_status.update(
            {
                "running": False,
                "status": "failed",
                "message": str(exc),
                "finished_at": utc_now_iso(),
            }
        )
        store.update_dashboard_state(
            {
                "last_auto_bootstrap_status": "failed",
                "last_auto_bootstrap_message": str(exc),
                "last_auto_bootstrap_finished_at": utc_now_iso(),
            }
        )


def _handle_chat_command(
    *,
    project_root: Path,
    store: WorkspaceStore,
    text: str,
    selected_vacancy_id: str = "",
    hh_login_status: dict[str, object],
    analyze_status: dict[str, object],
    apply_batch_status: dict[str, object],
    pending_rule_edit: dict[str, object],
    start_analyze_job,
) -> dict[str, object]:
    normalized = _normalize_text(text)
    if not normalized:
        return _chat_response("Пустое сообщение. Напиши задачу или изменение правил.")

    if "помощ" in normalized or normalized in {"help", "?"}:
        return _chat_response(
            "Через чат можно управлять поиском, логином в hh.ru, резюме, правилами, анализом вакансий и откликом.",
            action="help",
        )

    if any(token in normalized for token in ("\u0438\u043d\u0442\u0435\u0439\u043a", "\u043e\u043f\u0440\u043e\u0441", "\u0430\u043d\u043a\u0435\u0442\u0430", "\u043e\u043d\u0431\u043e\u0440\u0434\u0438\u043d\u0433")) and any(
        token in normalized for token in ("\u043d\u0430\u0447", "\u0441\u0442\u0430\u0440\u0442", "\u043f\u043e\u043a\u0430\u0436\u0438", "\u0448\u0430\u0431\u043b\u043e\u043d", "\u0432\u043e\u043f\u0440\u043e\u0441")
    ):
        result = begin_intake_dialog(store)
        return _chat_response(str(result.get("message") or build_detailed_intake_prompt(store)), action="intake-dialog", details=result)

    lowered_text = text.strip().lower()
    if lowered_text.startswith("\u0438\u043d\u0442\u0435\u0439\u043a \u0438\u0437 \u0444\u0430\u0439\u043b\u0430 ") or lowered_text.startswith("\u043e\u043f\u0440\u043e\u0441 \u0438\u0437 \u0444\u0430\u0439\u043b\u0430 "):
        path_value = text.strip().split(" ", 3)[-1]
        result = run_intake_from_file(store, path_value=path_value)
        return _chat_response(str(result.get("message") or "\u0418\u043d\u0442\u0435\u0439\u043a \u0438\u0437 \u0444\u0430\u0439\u043b\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d."), action="intake", details=result)

    if lowered_text.startswith(("\u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0438\u043d\u0442\u0435\u0439\u043a", "\u0438\u043d\u0442\u0435\u0439\u043a:", "\u0430\u043d\u043a\u0435\u0442\u0430:", "\u043c\u043e\u0438 \u043e\u0442\u0432\u0435\u0442\u044b:")):
        payload_text = text.split(":", 1)[1] if ":" in text else text
        result = run_intake_from_text(store, raw_text=payload_text.strip(), source_name="chat")
        return _chat_response(str(result.get("message") or "\u0418\u043d\u0442\u0435\u0439\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d."), action="intake", details=result)

    if lowered_text in {"подтверди интейк", "подтверди опрос", "confirm intake", "ok, дальше"}:
        result = confirm_intake_rules(store)
        return _chat_response(str(result.get("message") or "Интейк подтверждён."), action="confirm-intake", details=result)

    if lowered_text in {"отмена опроса", "сбросить опрос", "прервать опрос"}:
        store.update_dashboard_state({"intake_dialog": {}, "intake_dialog_completed": False, "intake_confirmed": False, "intake_confirmed_at": ""})
        return _chat_response("Опрос сброшен. Можно начать заново командой «начать опрос».", action="intake-dialog")

    if (store.load_dashboard_state().get("intake_dialog") or {}).get("active"):
        result = continue_intake_dialog(store, message=text)
        return _chat_response(str(result.get("message") or "Продолжаю опрос."), action="intake-dialog", details=result)

    if ("логин" in normalized and "hh" in normalized) or normalized.startswith("войти hh") or normalized.startswith("войти в hh") or normalized in {"войти", "войти в hh.ru", "войти в хх", "войти хх", "в хх"}:
        if not hh_login_status.get("running"):
            hh_login_status.update(
                {
                    "running": True,
                    "status": "running",
                    "message": "Открываю hh.ru для входа.",
                    "started_at": utc_now_iso(),
                    "finished_at": "",
                }
            )

            def _worker() -> None:
                try:
                    result = run_hh_login(project_root)
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "failed", "message": str(exc)}
                hh_login_status.update(
                    {
                        "running": False,
                        "status": str(result.get("status") or "failed"),
                        "message": str(result.get("message") or ""),
                        "finished_at": utc_now_iso(),
                    }
                )

            threading.Thread(target=_worker, daemon=True).start()
        return _chat_response("Открыл окно входа в hh.ru. После входа продолжу работу с резюме и анализом.", action="hh-login")

    if "подтяни резюме" in normalized or "обнови резюме" in normalized or "список резюме" in normalized:
        payload = HHResumeCatalog(store).refresh()
        items = list(payload.get("items") or [])
        if not items:
            return _chat_response("Резюме на hh.ru пока не найдены. Проверь вход в hh.ru и список резюме.", action="hh-resumes", details=payload)
        return _chat_response(
            f"Подтянул {len(items)} резюме с hh.ru. Если их несколько, выбери нужное резюме в интерфейсе или через чат.",
            action="hh-resumes",
            details=payload,
        )

    if normalized.startswith("выбери резюме") or normalized.startswith("резюме "):
        resume_id, title = _find_resume_reference(store, normalized)
        if not resume_id:
            return _chat_response("Не смог определить резюме. Сначала обнови список резюме, затем выбери нужное.", action="select-resume")
        result = select_resume_for_search(store, resume_id=resume_id)
        return _chat_response(f"Выбрал резюме для поиска: {title or resume_id}.", action="select-resume", details=result)

    if "backend openrouter" in normalized or "выбери openrouter" in normalized:
        result = update_runtime_settings(store, {"llm_backend": "openrouter", "mode_selected": True})
        return _chat_response(f"Backend переключён на OpenRouter. Модель: {result.get('openrouter_model')}.", action="runtime-settings", details=result)
    if "backend openai" in normalized or "выбери openai" in normalized:
        result = update_runtime_settings(store, {"llm_backend": "openai", "mode_selected": True})
        return _chat_response(f"Backend переключён на OpenAI. Модель: {result.get('openai_model')}.", action="runtime-settings", details=result)
    if "backend g4f" in normalized or "выбери g4f" in normalized:
        result = update_runtime_settings(store, {"llm_backend": "g4f", "mode_selected": True})
        return _chat_response(
            f"Backend переключён на g4f. Цель: {result.get('g4f_model')} / {result.get('g4f_provider') or 'auto'}.",
            action="runtime-settings",
            details=result,
        )

    mode_match = re.search(r"\bрежим\s+(analyze|apply_plan|repair|full_pipeline)\b", normalized)
    if mode_match:
        mode = mode_match.group(1)
        result = update_runtime_settings(store, {"dashboard_mode": mode, "mode_selected": True})
        return _chat_response(f"Режим переключён на {mode}.", action="runtime-settings", details=result)

    if normalized.startswith("модель openrouter ") or normalized.startswith("openrouter model "):
        model = text.split(" ", 2)[-1].strip()
        result = update_runtime_settings(store, {"openrouter_model": model})
        return _chat_response(f"Модель OpenRouter обновлена: {result.get('openrouter_model')}.", action="runtime-settings", details=result)

    if "пересобери правила" in normalized or "сгенерируй правила" in normalized:
        result = build_rules_from_profile(store)
        pending_rule_edit.clear()
        return _chat_response("Базовые правила пересобраны из текущего профиля и анамнеза.", action="build-rules", details=result)

    if normalized in {"подтверди правила", "примени правила", "сохрани правила"}:
        if not pending_rule_edit:
            return _chat_response("Нет неподтверждённого изменения правил.", action="import-rules")
        result = import_rules_text(
            store,
            filename=str(pending_rule_edit.get("filename") or "chat_rules.md"),
            markdown=str(pending_rule_edit.get("markdown") or ""),
        )
        applied_preview = str(pending_rule_edit.get("diff") or "")
        pending_rule_edit.clear()
        return _chat_response(
            "Изменение правил применено. Для пересчёта вакансий теперь запусти Analyze.",
            action="import-rules",
            details={**result, "diff": applied_preview},
        )

    if _equals_any(normalized, {"отмени правила", "откати правила", "cancel rules"}):
        if not pending_rule_edit:
            return _chat_response("Нет неподтверждённого изменения правил.", action="import-rules")
        pending_rule_edit.clear()
        return _chat_response("Черновик изменения правил отменён.", action="import-rules")

    if _contains_any(normalized, ("черновик правил", "покажи diff правил", "покажи черновик правил")):
        if not pending_rule_edit:
            return _chat_response("Черновика изменения правил нет.", action="show-rules")
        return _chat_response(
            "Ниже черновик изменения правил. Напиши «подтверди правила» или «отмени правила».",
            action="show-rules",
            details=dict(pending_rule_edit),
        )

    prefix, raw_request = _extract_rule_request_payload(text)
    natural_rule_request = (
        _contains_any(normalized, ("не хочу", "предпочитаю", "только remote", "только удал", "зарплата от", "исключи компанию", "без "))
        and not prefix
    )
    if prefix or natural_rule_request:
        if not raw_request:
            raw_request = text.strip()
        if not raw_request:
            return _chat_response("После команды нужен текст правила.", action="import-rules")
        markdown = raw_request
        if ":" not in raw_request:
            patch = parse_rule_request(raw_request)
            markdown = patch_to_markdown(patch)
            if not markdown:
                markdown = f"notes: {raw_request}"
        proposal = _build_rules_proposal(current_rules=store.load_selection_rules(), markdown=markdown, filename="chat_rules.md")
        pending_rule_edit.clear()
        pending_rule_edit.update(proposal)
        return _chat_response(
            "Подготовил изменение правил. Посмотри diff и напиши «подтверди правила» или «отмени правила».",
            action="propose-rules",
            details=proposal,
        )

    if _contains_any(normalized, ("покажи правила",)):
        preview = store.load_selection_rules()[:2000]
        return _chat_response(preview or "Правила пока пустые.", action="show-rules", details={"preview": preview})

    if _contains_any(normalized, ("собери резюме", "обнови резюме кандидата")):
        result = run_resume(store)
        return _chat_response("Черновик резюме обновлён.", action="resume", details={"has_markdown": bool(result.get("markdown"))})

    if _contains_any(normalized, ("собери фильтры", "обнови фильтры", "спланируй фильтры")):
        result = run_plan_filters(store)
        return _chat_response(str(result.get("message") or "Filter plan updated."), action="plan-filters", details=result)

    if "repair" in normalized and _contains_any(normalized, ("запусти", "почини")):
        latest = next(iter(store.load_repair_tasks(limit=1)), None)
        if not latest:
            return _chat_response("Repair queue пуст. Сначала должна появиться repair-задача.", action="repair")
        result = run_plan_repair(
            store,
            action=str(latest.get("action") or "unknown_action"),
            payload=dict(latest.get("payload") or {}),
            error=str(latest.get("error") or "missing_script"),
            run_agent=True,
        )
        payload = dict(result.get("payload") or {})
        status = str(payload.get("status") or "")
        worker_error = str(payload.get("worker_error") or payload.get("error") or "")
        if status in {"error", "failed", "unavailable"}:
            return _chat_response(f"Repair worker не выполнился: {worker_error or status}.", action="repair", details=result)
        return _chat_response("Repair worker запущен для последней задачи.", action="repair", details=result)

    if _contains_any(normalized, ("apply plan", "план отклика")):
        vacancy_id = selected_vacancy_id.strip()
        result = run_plan_apply(store, vacancy_id=vacancy_id or None)
        vacancy = ((result.get("payload") or {}).get("vacancy") or {}).get("title") or vacancy_id or "выбранная вакансия"
        return _chat_response(f"План отклика собран: {vacancy}.", action="apply-plan", details=result)

    if _contains_any(normalized, ("запусти анализ", "поиск вакансий")) or _equals_any(normalized, {"анализ", "го", "go", "поехали", "запускай"}):
        result = start_analyze_job(limit=0)
        return _chat_response(str(result.get("message") or "Запустил анализ."), action="analyze", details=result)

    llm_reply = _openrouter_chat_reply(store, text=text, selected_vacancy_id=selected_vacancy_id)
    if llm_reply and str(llm_reply.get("message") or "").strip():
        return _chat_response(
            str(llm_reply.get("message") or "").strip(),
            action="chat-llm",
            details={"backend": llm_reply.get("backend"), "model": llm_reply.get("model")},
        )
    return _chat_response(
        "Не распознал это как явную команду. Сформулируйте, что именно нужно сделать: логин в hh.ru, выбрать резюме, пересобрать правила, запустить анализ или поправить отклик.",
        action="unknown",
        details={"llm_error": str((llm_reply or {}).get("error") or "")},
    )


def _handler_factory(project_root: Path):
    mutation_lock = threading.Lock()
    hh_login_status: dict[str, object] = {
        "running": False,
        "status": "idle",
        "message": "",
        "started_at": "",
        "finished_at": "",
    }
    analyze_status: dict[str, object] = {
        "running": False,
        "status": "idle",
        "message": "",
        "phase": "",
        "started_at": "",
        "finished_at": "",
        "result": {},
    }
    bootstrap_status: dict[str, object] = {
        "running": False,
        "status": "idle",
        "message": "",
        "started_at": "",
        "finished_at": "",
    }
    pending_rule_edit: dict[str, object] = {}
    apply_batch_status: dict[str, object] = {
        "running": False,
        "status": "idle",
        "message": "",
        "category": "",
        "started_at": "",
        "finished_at": "",
    }
    refresh_status: dict[str, object] = {
        "running": False,
        "status": "idle",
        "message": "",
        "log_lines": [],
        "started_at": "",
        "finished_at": "",
        "result": {},
    }
    runtime_patch_fields = (
        "llm_backend",
        "dashboard_mode",
        "mode_selected",
        "auto_run_repair_worker",
        "openai_model",
        "openrouter_model",
        "g4f_model",
        "g4f_provider",
    )

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "AutoHHKekDashboard/1.0"

        def _snapshot_payload(self) -> dict[str, object]:
            payload = build_dashboard_snapshot(project_root)
            current_store = WorkspaceStore(project_root)
            payload["hh_login"] = {
                **hh_login_status,
                "state_file_exists": current_store.hh_state_path.exists(),
            }
            payload["analysis_job"] = dict(analyze_status)
            payload["refresh_job"] = dict(refresh_status)
            payload["pending_rule_edit"] = dict(pending_rule_edit)
            payload["bootstrap"] = dict(bootstrap_status)
            freshness = dict(payload.get("freshness") or {})
            timestamps = dict(freshness.get("timestamps") or {})
            if not timestamps.get("auto_bootstrap_at") and bootstrap_status.get("started_at"):
                timestamps["auto_bootstrap_at"] = str(bootstrap_status.get("started_at") or "")
                freshness["timestamps"] = timestamps
                payload["freshness"] = freshness
            return payload

        def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
            repaired_payload = _repair_payload_strings(payload)
            self._send_bytes(
                json.dumps(repaired_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json; charset=utf-8",
                status=status,
            )

        def _read_json_body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                body, content_type = _asset_response(ASSETS_DIR / "index.html")
                self._send_bytes(body, content_type)
                return
            if parsed.path == "/assets/app.css":
                body, content_type = _asset_response(ASSETS_DIR / "app.css")
                self._send_bytes(body, content_type)
                return
            if parsed.path == "/assets/app.js":
                body, content_type = _asset_response(ASSETS_DIR / "app.js")
                self._send_bytes(body, content_type)
                return
            if parsed.path == "/favicon.ico":
                self._send_bytes(b"", "image/x-icon", status=204)
                return
            if parsed.path == "/api/dashboard":
                payload = self._snapshot_payload()
                if _should_auto_bootstrap(payload, hh_login_status, bootstrap_status):
                    bootstrap_status["message"] = "Запускаю первый вход и обновление правил."
                    threading.Thread(
                        target=_run_first_bootstrap,
                        kwargs={
                            "project_root": project_root,
                            "hh_login_status": hh_login_status,
                            "bootstrap_status": bootstrap_status,
                        },
                        daemon=True,
                    ).start()
                    payload = self._snapshot_payload()
                self._send_json(payload)
                return
            self._send_json({"status": "error", "error": "not_found", "path": parsed.path}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            body = self._read_json_body()
            store = WorkspaceStore(project_root)
            with mutation_lock:
                try:
                    runtime_patch = {key: body[key] for key in runtime_patch_fields if key in body}
                    if runtime_patch and parsed.path in {"/api/actions/run-selected", "/api/actions/analyze", "/api/actions/plan-repair"}:
                        update_runtime_settings(store, runtime_patch)
                    if parsed.path == "/api/runtime/settings":
                        result = update_runtime_settings(store, dict(body))
                    elif parsed.path == "/api/actions/intake":
                        result = run_intake(store, interactive=False, payload=dict(body))
                    elif parsed.path == "/api/actions/build-rules":
                        result = build_rules_from_profile(store)
                    elif parsed.path == "/api/actions/start-intake":
                        result = restart_intake_dialog(store) if bool(body.get("restart", False)) else begin_intake_dialog(store)
                    elif parsed.path == "/api/actions/resume":
                        result = run_resume(store)
                    elif parsed.path == "/api/actions/confirm-intake":
                        result = confirm_intake_rules(store)
                    elif parsed.path == "/api/actions/llm-fallback-heuristics":
                        result = choose_heuristic_fallback(store, stage=str(body.get("stage") or "resume_intake"))
                    elif parsed.path == "/api/actions/llm-wait":
                        result = postpone_until_llm_available(store, stage=str(body.get("stage") or "resume_intake"))
                    elif parsed.path == "/api/actions/plan-filters":
                        result = run_plan_filters(store)
                    elif parsed.path == "/api/actions/refresh-vacancies":
                        result = self._start_refresh_job(limit=int(body.get("limit", 0)))
                    elif parsed.path == "/api/actions/run-selected":
                        if store.load_runtime_settings().dashboard_mode == "analyze":
                            result = self._start_analyze_job(limit=int(body.get("limit", 0)))
                        else:
                            result = run_selected_mode(store)
                    elif parsed.path == "/api/actions/analyze":
                        result = self._start_analyze_job(limit=int(body.get("limit", 0)))
                    elif parsed.path == "/api/actions/apply-plan":
                        result = run_plan_apply(store, vacancy_id=body.get("vacancy_id") or None)
                    elif parsed.path == "/api/actions/save-cover-letter":
                        result = save_cover_letter_override(
                            store,
                            vacancy_id=str(body.get("vacancy_id") or ""),
                            cover_letter=str(body.get("cover_letter") or ""),
                        )
                    elif parsed.path == "/api/actions/apply-submit":
                        result = run_apply_submit(
                            store,
                            vacancy_id=str(body.get("vacancy_id") or ""),
                            cover_letter=str(body.get("cover_letter") or ""),
                        )
                    elif parsed.path == "/api/actions/apply-batch":
                        category = str(body.get("category") or "").strip()
                        if apply_batch_status.get("running"):
                            active_category = str(apply_batch_status.get("category") or "")
                            busy_message = (
                                f"Пакетный отклик по колонке {active_category} уже выполняется."
                                if active_category and active_category == category
                                else f"Сейчас выполняется пакетный отклик по колонке {active_category or 'unknown'}. Новый запуск для {category or 'unknown'} пока отложен."
                            )
                            result = {
                                "action": "apply_batch",
                                "status": "running",
                                "message": busy_message,
                            }
                        else:
                            apply_batch_status.update(
                                {
                                    "running": True,
                                    "status": "running",
                                    "message": f"Запускаю пакетный отклик по колонке {category}.",
                                    "category": category,
                                    "started_at": utc_now_iso(),
                                    "finished_at": "",
                                }
                            )

                            def _apply_batch_worker() -> None:
                                worker_store = WorkspaceStore(project_root)
                                try:
                                    worker_store.update_dashboard_state(
                                        {
                                            "apply_batch_running": True,
                                            "apply_batch_category": category,
                                            "apply_batch_message": f"Идёт пакетный отклик по колонке {category}.",
                                            "apply_batch_started_at": utc_now_iso(),
                                        }
                                    )
                                    batch_result = run_apply_batch(worker_store, category=category)
                                    worker_store.update_dashboard_state(
                                        {
                                            "apply_batch_running": False,
                                            "apply_batch_category": category,
                                            "apply_batch_message": str(batch_result.get("message") or ""),
                                            "apply_batch_finished_at": utc_now_iso(),
                                        }
                                    )
                                    with mutation_lock:
                                        apply_batch_status.update(
                                            {
                                                "running": False,
                                                "status": "completed",
                                                "message": str(batch_result.get("message") or "Пакетный отклик завершён."),
                                                "finished_at": utc_now_iso(),
                                            }
                                        )
                                except Exception as exc:  # noqa: BLE001
                                    worker_store.update_dashboard_state(
                                        {
                                            "apply_batch_running": False,
                                            "apply_batch_category": category,
                                            "apply_batch_message": str(exc),
                                            "apply_batch_finished_at": utc_now_iso(),
                                        }
                                    )
                                    with mutation_lock:
                                        apply_batch_status.update(
                                            {
                                                "running": False,
                                                "status": "failed",
                                                "message": str(exc),
                                                "finished_at": utc_now_iso(),
                                            }
                                        )

                            threading.Thread(target=_apply_batch_worker, daemon=True).start()
                            result = {
                                "action": "apply_batch",
                                "status": "running",
                                "message": f"Пакетный отклик по колонке {category} запущен в фоне.",
                            }
                    elif parsed.path == "/api/actions/vacancy-feedback":
                        result = update_vacancy_feedback(
                            store,
                            vacancy_id=str(body.get("vacancy_id") or ""),
                            decision=str(body.get("decision") or ""),
                        )
                    elif parsed.path == "/api/actions/import-rules":
                        result = import_rules_text(
                            store,
                            filename=str(body.get("filename") or "dashboard_rules.md"),
                            markdown=str(body.get("markdown") or ""),
                        )
                    elif parsed.path == "/api/actions/plan-repair":
                        result = run_plan_repair(
                            store,
                            action=str(body.get("action") or "unknown_action"),
                            payload=dict(body.get("payload") or {}),
                            error=str(body.get("error") or "missing_script"),
                            run_agent=bool(body.get("run_agent", False)),
                        )
                    elif parsed.path == "/api/actions/hh-login":
                        fresh_start = bool(body.get("fresh_start", False))
                        if not hh_login_status.get("running"):
                            hh_login_status.update(
                                {
                                    "running": True,
                                    "status": "running",
                                    "message": "Открываю hh.ru для входа." if not fresh_start else "Открываю hh.ru для входа в другой аккаунт.",
                                    "started_at": utc_now_iso(),
                                    "finished_at": "",
                                }
                            )

                            def _worker() -> None:
                                try:
                                    result = run_hh_login(project_root, fresh_start=fresh_start)
                                    if result.get("status") == "completed":
                                        auto_store = WorkspaceStore(project_root)
                                        if auto_store.load_preferences() and auto_store.load_anamnesis() and auto_store.load_selected_resume_id():
                                            auto_resume = run_resume(auto_store)
                                            result["auto_resume"] = auto_resume
                                            resume_message = str(auto_resume.get("message") or "").strip()
                                            if resume_message:
                                                result["message"] = f"{str(result.get('message') or '').strip()} {resume_message}".strip()
                                except Exception as exc:  # noqa: BLE001
                                    result = {"status": "failed", "message": str(exc)}
                                with mutation_lock:
                                    hh_login_status.update(
                                        {
                                            "running": False,
                                            "status": str(result.get("status") or "failed"),
                                            "message": str(result.get("message") or ""),
                                            "finished_at": utc_now_iso(),
                                        }
                                    )

                            threading.Thread(target=_worker, daemon=True).start()
                        result = {"action": "hh-login", "status": hh_login_status["status"]}
                    elif parsed.path == "/api/actions/hh-resumes":
                        result = {"action": "hh-resumes", "payload": HHResumeCatalog(store).refresh()}
                    elif parsed.path == "/api/actions/select-resume":
                        resume_id = str(body.get("resume_id") or "").strip()
                        result = select_resume_for_search(store, resume_id=resume_id)
                        if resume_id:
                            sync_result = HHResumeProfileSync(store).sync_selected_resume()
                            result["profile_sync"] = sync_result
                            sync_ok = str(sync_result.get("status") or "") in ("updated", "no_changes")
                            if sync_ok and store.load_preferences() and store.load_anamnesis():
                                auto_resume = run_resume(store)
                                result["auto_resume"] = auto_resume
                                resume_message = str(auto_resume.get("message") or "").strip()
                                if resume_message:
                                    result["message"] = f"{str(result.get('message') or '').strip()} {resume_message}".strip()
                    elif parsed.path == "/api/actions/select-account":
                        result = select_hh_account(store, account_key=str(body.get("account_key") or ""))
                    elif parsed.path == "/api/actions/delete-account":
                        result = delete_hh_account(store, account_key=str(body.get("account_key") or ""))
                    elif parsed.path == "/api/chat":
                        result = _handle_chat_command(
                            project_root=project_root,
                            store=store,
                            text=str(body.get("message") or ""),
                            selected_vacancy_id=str(body.get("selected_vacancy_id") or ""),
                            hh_login_status=hh_login_status,
                            analyze_status=analyze_status,
                            apply_batch_status=apply_batch_status,
                            pending_rule_edit=pending_rule_edit,
                            start_analyze_job=self._start_analyze_job,
                        )
                    elif parsed.path == "/api/client-log":
                        debug_path = store.save_debug_artifact(
                            str(body.get("kind") or "client-log"),
                            body.get("payload") or {},
                            extension="json",
                            subdir="dashboard",
                        )
                        store.record_event(
                            "dashboard-client",
                            f"Client log captured: {body.get('kind') or 'client-log'}",
                            details={"debug_artifact": debug_path},
                        )
                        result = {"action": "client-log", "status": "captured", "debug_artifact": debug_path}
                    else:
                        self._send_json({"status": "error", "error": "not_found", "path": parsed.path}, status=404)
                        return
                except Exception as exc:  # noqa: BLE001
                    debug_path = store.save_debug_artifact(
                        "dashboard-api-error",
                        {
                            "path": parsed.path,
                            "error": str(exc),
                            "body": body,
                        },
                        extension="json",
                        subdir="dashboard",
                    )
                    store.record_event(
                        "dashboard-error",
                        f"API failure on {parsed.path}: {exc}",
                        details={"debug_artifact": debug_path},
                    )
                    self._send_json({"status": "error", "error": str(exc), "path": parsed.path}, status=500)
                    return
            self._send_json({"status": "ok", "result": result, "snapshot": self._snapshot_payload()})

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _start_analyze_job(self, *, limit: int) -> dict[str, object]:
            if analyze_status.get("running"):
                return {"action": "analyze", "status": "running", "message": str(analyze_status.get("message") or "Analyze is already running.")}

            analyze_status.update(
                {
                    "running": True,
                    "status": "running",
                    "phase": "preflight",
                    "message": "Проверяю вход в hh.ru, резюме и готовность live-поиска.",
                    "started_at": utc_now_iso(),
                    "finished_at": "",
                    "result": {},
                }
            )

            def _worker() -> None:
                worker_store = WorkspaceStore(project_root)
                state_path = worker_store.hh_state_path
                try:
                    if not state_path.exists():
                        with mutation_lock:
                            analyze_status["phase"] = "login"
                            analyze_status["message"] = "Не найден hh_state.json. Открываю браузер для входа в hh.ru."
                    else:
                        with mutation_lock:
                            analyze_status["phase"] = "resumes"
                            analyze_status["message"] = "Обновляю резюме hh.ru и подготавливаю live-поиск вакансий."

                    with mutation_lock:
                        analyze_status["phase"] = "analysis"
                        analyze_status["message"] = "Обновляю вакансии с hh.ru и пересчитываю их относительно текущего профиля."
                    def _progress(
                        *,
                        stage: str = "",
                        message: str = "",
                        done: int = 0,
                        total: int = 0,
                        title: str = "",
                        strategy: str = "",
                        details: dict | None = None,
                    ) -> None:
                        worker_store.update_dashboard_state(
                            {
                                "analysis_progress_stage": stage,
                                "analysis_progress_message": message,
                                "analysis_progress_done": done,
                                "analysis_progress_total": total,
                                "analysis_progress_title": title,
                                "analysis_progress_strategy": strategy,
                                "analysis_progress_details": details or {},
                                "analysis_progress_updated_at": utc_now_iso(),
                            }
                        )
                        with mutation_lock:
                            analyze_status["message"] = message or (
                                f"Оцениваю вакансии: {done}/{total}." + (f" Последняя: {title}." if title else "")
                            )

                    result = run_analyze(worker_store, limit=limit, interactive=False, progress_callback=_progress)
                    with mutation_lock:
                        analyze_status.update(
                            {
                                "running": False,
                                "status": str(result.get("status") or "completed"),
                                "phase": "completed" if result.get("status") == "completed" else "blocked",
                                "message": str(result.get("message") or "Анализ завершен."),
                                "finished_at": utc_now_iso(),
                                "result": result,
                            }
                        )
                    worker_store.update_dashboard_state(
                        {
                            "analysis_progress_done": 0,
                            "analysis_progress_total": 0,
                            "analysis_progress_title": "",
                            "analysis_progress_strategy": "",
                            "analysis_progress_updated_at": utc_now_iso(),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    debug_path = worker_store.save_debug_artifact(
                        "analyze-job-error",
                        {
                            "error": str(exc),
                            "phase": str(analyze_status.get("phase") or ""),
                        },
                        extension="json",
                        subdir="dashboard",
                    )
                    worker_store.record_event(
                        "dashboard-error",
                        f"Analyze job failed: {exc}",
                        details={"debug_artifact": debug_path},
                    )
                    with mutation_lock:
                        analyze_status.update(
                            {
                                "running": False,
                                "status": "failed",
                                "phase": "failed",
                                "message": str(exc),
                                "finished_at": utc_now_iso(),
                                "result": {"error": str(exc)},
                            }
                        )
                    worker_store.update_dashboard_state(
                        {
                            "analysis_progress_done": 0,
                            "analysis_progress_total": 0,
                            "analysis_progress_title": "",
                            "analysis_progress_strategy": "",
                            "analysis_progress_updated_at": utc_now_iso(),
                        }
                    )

            threading.Thread(target=_worker, daemon=True).start()
            return {"action": "analyze", "status": "started", "message": str(analyze_status["message"])}

        def _start_refresh_job(self, *, limit: int) -> dict[str, object]:
            if refresh_status.get("running"):
                return {
                    "action": "refresh_vacancies",
                    "status": "running",
                    "message": str(refresh_status.get("message") or "Парсинг вакансий уже выполняется."),
                }
            refresh_status.update(
                {
                    "running": True,
                    "status": "running",
                    "message": "Стартую парсинг вакансий с hh.ru…",
                    "log_lines": [],
                    "started_at": utc_now_iso(),
                    "finished_at": "",
                    "result": {},
                }
            )

            def _refresh_worker() -> None:
                worker_store = WorkspaceStore(project_root)
                lines: list[str] = []

                def _log(line: str) -> None:
                    text = str(line or "").strip()
                    if not text:
                        return
                    lines.append(text)
                    with mutation_lock:
                        refresh_status["log_lines"] = list(lines[-120:])
                        refresh_status["message"] = text

                try:
                    result = run_refresh_vacancies(worker_store, limit=limit, log_line=_log)
                    with mutation_lock:
                        refresh_status.update(
                            {
                                "running": False,
                                "status": str(result.get("status") or "completed"),
                                "message": str(result.get("message") or "Парсинг завершён."),
                                "finished_at": utc_now_iso(),
                                "result": result,
                                "log_lines": list(lines[-120:]),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    with mutation_lock:
                        refresh_status.update(
                            {
                                "running": False,
                                "status": "failed",
                                "message": str(exc),
                                "finished_at": utc_now_iso(),
                                "result": {"error": str(exc)},
                                "log_lines": list(lines[-120:]),
                            }
                        )

            threading.Thread(target=_refresh_worker, daemon=True).start()
            return {
                "action": "refresh_vacancies",
                "status": "started",
                "message": str(refresh_status["message"]),
            }

    return DashboardHandler


def start_dashboard_server(project_root: Path, host: str = "127.0.0.1", port: int = 8766) -> DashboardHandle:
    project_root = project_root.resolve()
    store = WorkspaceStore(project_root)
    store.update_dashboard_state({
        "apply_batch_running": False,
        "apply_batch_message": "",
        "analysis_progress_done": 0,
        "analysis_progress_total": 0,
        "analysis_progress_title": "",
        "analysis_progress_strategy": "",
        "analysis_progress_updated_at": utc_now_iso(),
    })
    handler = _handler_factory(project_root)
    server = ThreadingHTTPServer((host, port), handler)
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host == "0.0.0.0" else str(actual_host)
    url = f"http://{browser_host}:{actual_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return DashboardHandle(server=server, thread=thread, url=url)


