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
from autohhkek.app.commands import (
    begin_intake_dialog,
    build_detailed_intake_prompt,
    build_rules_from_profile,
    confirm_intake_rules,
    continue_intake_dialog,
    import_rules_text,
    run_analyze,
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
    select_hh_account,
    select_resume_for_search,
    update_vacancy_feedback,
    update_runtime_settings,
)
from autohhkek.services.hh_login import run_hh_login
from autohhkek.services.hh_resume_catalog import HHResumeCatalog
from autohhkek.services.chat_rule_parser import parse_rule_request, patch_to_markdown
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
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _chat_response(message: str, *, action: str = "", details: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "message": message,
        "action": action,
        "details": details or {},
    }


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
    prefixes = (
        "РґРѕР±Р°РІСЊ РїСЂР°РІРёР»Рѕ:",
        "РѕР±РЅРѕРІРё РїСЂР°РІРёР»Р°:",
        "РїСЂР°РІРёР»Р°:",
        "РїСЂРµРґР»РѕР¶Рё РїСЂР°РІРёР»Рѕ:",
    )
    for prefix in prefixes:
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
            "message": "РџСЂРѕРІРµСЂСЏСЋ Р»РѕРіРёРЅ, СЂРµР·СЋРјРµ Рё РїСЂР°РІРёР»Р° РїРµСЂРµРґ РїРµСЂРІС‹Рј Р·Р°РїСѓСЃРєРѕРј.",
            "started_at": utc_now_iso(),
            "finished_at": "",
        }
    )
    try:
        hh_login_status.update(
            {
                "running": True,
                "status": "running",
                "message": "РћС‚РєСЂС‹РІР°СЋ hh.ru РґР»СЏ РІС…РѕРґР°.",
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
                bootstrap_status["message"] = str(resume_result.get("message") or "Р РµР·СЋРјРµ Рё РїСЂР°РІРёР»Р° РѕР±РЅРѕРІР»РµРЅС‹.")
            else:
                rules_result = build_rules_from_profile(store)
                bootstrap_status["message"] = "РџСЂР°РІРёР»Р° РѕР±РЅРѕРІР»РµРЅС‹. Р”Р»СЏ РѕС‚РєР»РёРєРѕРІ РЅСѓР¶РЅРѕ РІС‹Р±СЂР°С‚СЊ СЂРµР·СЋРјРµ."
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
        return _chat_response("РџСѓСЃС‚РѕРµ СЃРѕРѕР±С‰РµРЅРёРµ. РќР°РїРёС€Рё Р·Р°РґР°С‡Сѓ РёР»Рё РёР·РјРµРЅРµРЅРёРµ РїСЂР°РІРёР».")

    if "РїРѕРјРѕС‰" in normalized or normalized in {"help", "?"}:
        return _chat_response(
            "Р§РµСЂРµР· С‡Р°С‚ РјРѕР¶РЅРѕ СѓРїСЂР°РІР»СЏС‚СЊ РїРѕРёСЃРєРѕРј, Р»РѕРіРёРЅРѕРј РІ hh.ru, СЂРµР·СЋРјРµ, РїСЂР°РІРёР»Р°РјРё, Р°РЅР°Р»РёР·РѕРј РІР°РєР°РЅСЃРёР№ Рё РѕС‚РєР»РёРєРѕРј.",
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

    if lowered_text in {"подтвердить правила", "подтверждаю правила", "подтвердить intake", "ok, запускай"}:
        result = confirm_intake_rules(store)
        return _chat_response(str(result.get("message") or "Правила подтверждены."), action="confirm-intake", details=result)

    if lowered_text in {"РѕС‚РјРµРЅР° РѕРїСЂРѕСЃР°", "СЃР±СЂРѕСЃРёС‚СЊ РѕРїСЂРѕСЃ", "РїСЂРµСЂРІР°С‚СЊ РѕРїСЂРѕСЃ"}:
        store.update_dashboard_state({"intake_dialog": {}, "intake_dialog_completed": False, "intake_confirmed": False, "intake_confirmed_at": ""})
        return _chat_response("РћРїСЂРѕСЃ СЃР±СЂРѕС€РµРЅ. РњРѕР¶РЅРѕ РЅР°С‡Р°С‚СЊ Р·Р°РЅРѕРІРѕ РєРѕРјР°РЅРґРѕР№ В«РЅР°С‡Р°С‚СЊ РѕРїСЂРѕСЃВ».", action="intake-dialog")

    if (store.load_dashboard_state().get("intake_dialog") or {}).get("active"):
        result = continue_intake_dialog(store, message=text)
        return _chat_response(str(result.get("message") or "РџСЂРѕРґРѕР»Р¶Р°СЋ РѕРїСЂРѕСЃ."), action="intake-dialog", details=result)

    if ("Р»РѕРіРёРЅ" in normalized and "hh" in normalized) or normalized.startswith("РІРѕР№С‚Рё hh") or normalized.startswith("РІРѕР№С‚Рё РІ hh") or normalized in {"РІРѕР№С‚Рё", "РІРѕР№С‚Рё РІ hh.ru", "РІРѕР№С‚Рё РІ С…С…", "РІРѕР№С‚Рё С…С…", "РІ С…С…"}:
        if not hh_login_status.get("running"):
            hh_login_status.update(
                {
                    "running": True,
                    "status": "running",
                    "message": "РћС‚РєСЂС‹РІР°СЋ hh.ru РґР»СЏ РІС…РѕРґР°.",
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
        return _chat_response("РћС‚РєСЂС‹Р» РѕРєРЅРѕ РІС…РѕРґР° РІ hh.ru. РџРѕСЃР»Рµ РІС…РѕРґР° РїСЂРѕРґРѕР»Р¶Сѓ СЂР°Р±РѕС‚Сѓ СЃ СЂРµР·СЋРјРµ Рё Р°РЅР°Р»РёР·РѕРј.", action="hh-login")

    if "РїРѕРґС‚СЏРЅРё СЂРµР·СЋРјРµ" in normalized or "РѕР±РЅРѕРІРё СЂРµР·СЋРјРµ" in normalized or "СЃРїРёСЃРѕРє СЂРµР·СЋРјРµ" in normalized:
        payload = HHResumeCatalog(store).refresh()
        items = list(payload.get("items") or [])
        if not items:
            return _chat_response("Р РµР·СЋРјРµ РЅР° hh.ru РїРѕРєР° РЅРµ РЅР°Р№РґРµРЅС‹. РџСЂРѕРІРµСЂСЊ РІС…РѕРґ РІ hh.ru Рё СЃРїРёСЃРѕРє СЂРµР·СЋРјРµ.", action="hh-resumes", details=payload)
        return _chat_response(
            f"РџРѕРґС‚СЏРЅСѓР» {len(items)} СЂРµР·СЋРјРµ СЃ hh.ru. Р•СЃР»Рё РёС… РЅРµСЃРєРѕР»СЊРєРѕ, РІС‹Р±РµСЂРё РЅСѓР¶РЅРѕРµ СЂРµР·СЋРјРµ РІ РёРЅС‚РµСЂС„РµР№СЃРµ РёР»Рё С‡РµСЂРµР· С‡Р°С‚.",
            action="hh-resumes",
            details=payload,
        )

    if normalized.startswith("РІС‹Р±РµСЂРё СЂРµР·СЋРјРµ") or normalized.startswith("СЂРµР·СЋРјРµ "):
        resume_id, title = _find_resume_reference(store, normalized)
        if not resume_id:
            return _chat_response("РќРµ СЃРјРѕРі РѕРїСЂРµРґРµР»РёС‚СЊ СЂРµР·СЋРјРµ. РЎРЅР°С‡Р°Р»Р° РѕР±РЅРѕРІРё СЃРїРёСЃРѕРє СЂРµР·СЋРјРµ, Р·Р°С‚РµРј РІС‹Р±РµСЂРё РЅСѓР¶РЅРѕРµ.", action="select-resume")
        result = select_resume_for_search(store, resume_id=resume_id)
        return _chat_response(f"Р’С‹Р±СЂР°Р» СЂРµР·СЋРјРµ РґР»СЏ РїРѕРёСЃРєР°: {title or resume_id}.", action="select-resume", details=result)

    if "backend openrouter" in normalized or "РІС‹Р±РµСЂРё openrouter" in normalized:
        result = update_runtime_settings(store, {"llm_backend": "openrouter", "mode_selected": True})
        return _chat_response(f"Backend РїРµСЂРµРєР»СЋС‡С‘РЅ РЅР° OpenRouter. РњРѕРґРµР»СЊ: {result.get('openrouter_model')}.", action="runtime-settings", details=result)
    if "backend openai" in normalized or "РІС‹Р±РµСЂРё openai" in normalized:
        result = update_runtime_settings(store, {"llm_backend": "openai", "mode_selected": True})
        return _chat_response(f"Backend РїРµСЂРµРєР»СЋС‡С‘РЅ РЅР° OpenAI. РњРѕРґРµР»СЊ: {result.get('openai_model')}.", action="runtime-settings", details=result)
    if "backend g4f" in normalized or "РІС‹Р±РµСЂРё g4f" in normalized:
        result = update_runtime_settings(store, {"llm_backend": "g4f", "mode_selected": True})
        return _chat_response(
            f"Backend РїРµСЂРµРєР»СЋС‡С‘РЅ РЅР° g4f. Р¦РµР»СЊ: {result.get('g4f_model')} / {result.get('g4f_provider') or 'auto'}.",
            action="runtime-settings",
            details=result,
        )

    mode_match = re.search(r"\bСЂРµР¶РёРј\s+(analyze|apply_plan|repair|full_pipeline)\b", normalized)
    if mode_match:
        mode = mode_match.group(1)
        result = update_runtime_settings(store, {"dashboard_mode": mode, "mode_selected": True})
        return _chat_response(f"Р РµР¶РёРј РїРµСЂРµРєР»СЋС‡С‘РЅ РЅР° {mode}.", action="runtime-settings", details=result)

    if normalized.startswith("РјРѕРґРµР»СЊ openrouter ") or normalized.startswith("openrouter model "):
        model = text.split(" ", 2)[-1].strip()
        result = update_runtime_settings(store, {"openrouter_model": model})
        return _chat_response(f"РњРѕРґРµР»СЊ OpenRouter РѕР±РЅРѕРІР»РµРЅР°: {result.get('openrouter_model')}.", action="runtime-settings", details=result)

    if "РїРµСЂРµСЃРѕР±РµСЂРё РїСЂР°РІРёР»Р°" in normalized or "СЃРіРµРЅРµСЂРёСЂСѓР№ РїСЂР°РІРёР»Р°" in normalized:
        result = build_rules_from_profile(store)
        pending_rule_edit.clear()
        return _chat_response("Р‘Р°Р·РѕРІС‹Рµ РїСЂР°РІРёР»Р° РїРµСЂРµСЃРѕР±СЂР°РЅС‹ РёР· С‚РµРєСѓС‰РµРіРѕ РїСЂРѕС„РёР»СЏ Рё Р°РЅР°РјРЅРµР·Р°.", action="build-rules", details=result)

    if normalized in {"РїРѕРґС‚РІРµСЂРґРё РїСЂР°РІРёР»Р°", "РїСЂРёРјРµРЅРё РїСЂР°РІРёР»Р°", "СЃРѕС…СЂР°РЅРё РїСЂР°РІРёР»Р°"}:
        if not pending_rule_edit:
            return _chat_response("РќРµС‚ РЅРµРїРѕРґС‚РІРµСЂР¶РґС‘РЅРЅРѕРіРѕ РёР·РјРµРЅРµРЅРёСЏ РїСЂР°РІРёР».", action="import-rules")
        result = import_rules_text(
            store,
            filename=str(pending_rule_edit.get("filename") or "chat_rules.md"),
            markdown=str(pending_rule_edit.get("markdown") or ""),
        )
        applied_preview = str(pending_rule_edit.get("diff") or "")
        pending_rule_edit.clear()
        return _chat_response(
            "РР·РјРµРЅРµРЅРёРµ РїСЂР°РІРёР» РїСЂРёРјРµРЅРµРЅРѕ. Р”Р»СЏ РїРµСЂРµСЃС‡С‘С‚Р° РІР°РєР°РЅСЃРёР№ С‚РµРїРµСЂСЊ Р·Р°РїСѓСЃС‚Рё Analyze.",
            action="import-rules",
            details={**result, "diff": applied_preview},
        )

    if normalized in {"РѕС‚РјРµРЅРё РїСЂР°РІРёР»Р°", "РѕС‚РєР°С‚Рё РїСЂР°РІРёР»Р°", "cancel rules"}:
        if not pending_rule_edit:
            return _chat_response("РќРµС‚ РЅРµРїРѕРґС‚РІРµСЂР¶РґС‘РЅРЅРѕРіРѕ РёР·РјРµРЅРµРЅРёСЏ РїСЂР°РІРёР».", action="import-rules")
        pending_rule_edit.clear()
        return _chat_response("Р§РµСЂРЅРѕРІРёРє РёР·РјРµРЅРµРЅРёСЏ РїСЂР°РІРёР» РѕС‚РјРµРЅС‘РЅ.", action="import-rules")

    if "С‡РµСЂРЅРѕРІРёРє РїСЂР°РІРёР»" in normalized or "РїРѕРєР°Р¶Рё diff РїСЂР°РІРёР»" in normalized or "РїРѕРєР°Р¶Рё С‡РµСЂРЅРѕРІРёРє РїСЂР°РІРёР»" in normalized:
        if not pending_rule_edit:
            return _chat_response("Р§РµСЂРЅРѕРІРёРєР° РёР·РјРµРЅРµРЅРёСЏ РїСЂР°РІРёР» РЅРµС‚.", action="show-rules")
        return _chat_response(
            "РќРёР¶Рµ С‡РµСЂРЅРѕРІРёРє РёР·РјРµРЅРµРЅРёСЏ РїСЂР°РІРёР». РќР°РїРёС€Рё В«РїРѕРґС‚РІРµСЂРґРё РїСЂР°РІРёР»Р°В» РёР»Рё В«РѕС‚РјРµРЅРё РїСЂР°РІРёР»Р°В».",
            action="show-rules",
            details=dict(pending_rule_edit),
        )

    prefix, raw_request = _extract_rule_request_payload(text)
    natural_rule_request = (
        any(token in normalized for token in ("РЅРµ С…РѕС‡Сѓ", "РїСЂРµРґРїРѕС‡РёС‚Р°СЋ", "С‚РѕР»СЊРєРѕ remote", "С‚РѕР»СЊРєРѕ СѓРґР°Р»", "Р·Р°СЂРїР»Р°С‚Р° РѕС‚", "РёСЃРєР»СЋС‡Рё РєРѕРјРїР°РЅРёСЋ", "Р±РµР· "))
        and not prefix
    )
    if prefix or natural_rule_request:
        if not raw_request:
            raw_request = text.strip()
        if not raw_request:
            return _chat_response("РџРѕСЃР»Рµ РєРѕРјР°РЅРґС‹ РЅСѓР¶РµРЅ С‚РµРєСЃС‚ РїСЂР°РІРёР»Р°.", action="import-rules")
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
            "РџРѕРґРіРѕС‚РѕРІРёР» РёР·РјРµРЅРµРЅРёРµ РїСЂР°РІРёР». РџРѕСЃРјРѕС‚СЂРё diff Рё РЅР°РїРёС€Рё В«РїРѕРґС‚РІРµСЂРґРё РїСЂР°РІРёР»Р°В» РёР»Рё В«РѕС‚РјРµРЅРё РїСЂР°РІРёР»Р°В».",
            action="propose-rules",
            details=proposal,
        )

    if "РїРѕРєР°Р¶Рё РїСЂР°РІРёР»Р°" in normalized:
        preview = store.load_selection_rules()[:2000]
        return _chat_response(preview or "РџСЂР°РІРёР»Р° РїРѕРєР° РїСѓСЃС‚С‹Рµ.", action="show-rules", details={"preview": preview})

    if "СЃРѕР±РµСЂРё СЂРµР·СЋРјРµ" in normalized or "РѕР±РЅРѕРІРё СЂРµР·СЋРјРµ РєР°РЅРґРёРґР°С‚Р°" in normalized:
        result = run_resume(store)
        return _chat_response("Р§РµСЂРЅРѕРІРёРє СЂРµР·СЋРјРµ РѕР±РЅРѕРІР»С‘РЅ.", action="resume", details={"has_markdown": bool(result.get("markdown"))})

    if "СЃРѕР±РµСЂРё С„РёР»СЊС‚СЂС‹" in normalized or "РѕР±РЅРѕРІРё С„РёР»СЊС‚СЂС‹" in normalized or "СЃРїР»Р°РЅРёСЂСѓР№ С„РёР»СЊС‚СЂС‹" in normalized:
        result = run_plan_filters(store)
        return _chat_response(str(result.get("message") or "Filter plan updated."), action="plan-filters", details=result)

    if "repair" in normalized and ("Р·Р°РїСѓСЃС‚Рё" in normalized or "РїРѕС‡РёРЅРё" in normalized):
        latest = next(iter(store.load_repair_tasks(limit=1)), None)
        if not latest:
            return _chat_response("Repair queue РїСѓСЃС‚. РЎРЅР°С‡Р°Р»Р° РґРѕР»Р¶РЅР° РїРѕСЏРІРёС‚СЊСЃСЏ repair-Р·Р°РґР°С‡Р°.", action="repair")
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
            return _chat_response(f"Repair worker РЅРµ РІС‹РїРѕР»РЅРёР»СЃСЏ: {worker_error or status}.", action="repair", details=result)
        return _chat_response("Repair worker Р·Р°РїСѓС‰РµРЅ РґР»СЏ РїРѕСЃР»РµРґРЅРµР№ Р·Р°РґР°С‡Рё.", action="repair", details=result)

    if "apply plan" in normalized or "РїР»Р°РЅ РѕС‚РєР»РёРєР°" in normalized:
        vacancy_id = selected_vacancy_id.strip()
        result = run_plan_apply(store, vacancy_id=vacancy_id or None)
        vacancy = ((result.get("payload") or {}).get("vacancy") or {}).get("title") or vacancy_id or "РІС‹Р±СЂР°РЅРЅР°СЏ РІР°РєР°РЅСЃРёСЏ"
        return _chat_response(f"РџР»Р°РЅ РѕС‚РєР»РёРєР° СЃРѕР±СЂР°РЅ: {vacancy}.", action="apply-plan", details=result)

    if "Р·Р°РїСѓСЃС‚Рё Р°РЅР°Р»РёР·" in normalized or normalized == "Р°РЅР°Р»РёР·" or "РїРѕРёСЃРє РІР°РєР°РЅСЃРёР№" in normalized or normalized in {"РіРѕ", "go", "РїРѕРµС…Р°Р»Рё", "Р·Р°РїСѓСЃРєР°Р№"}:
        result = start_analyze_job(limit=120)
        return _chat_response(str(result.get("message") or "Р—Р°РїСѓСЃС‚РёР» Р°РЅР°Р»РёР·."), action="analyze", details=result)
    threading.Thread(
        target=lambda: run_plan_repair(
            store,
            action="chat_command_router",
            payload={"message": text, "normalized": normalized},
            error="unknown_chat_command",
            run_agent=True,
        ),
        daemon=True,
    ).start()
    return _chat_response(
        "РќРµ РїРѕРЅСЏР» РєРѕРјР°РЅРґСѓ РѕРґРЅРѕР·РЅР°С‡РЅРѕ. Р—Р°РїСѓСЃС‚РёР» repair-РјР°СЂС€СЂСѓС‚ Рё РїР°СЂР°Р»Р»РµР»СЊРЅРѕ РїРѕРґРіРѕС‚РѕРІР»СЋ Р»СѓС‡С€РёР№ СЃС†РµРЅР°СЂРёР№ РґР»СЏ С‡Р°С‚Р°.",
        action="unknown",
        details={"background_repair": True},
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
            self._send_bytes(
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
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
                    bootstrap_status["message"] = "Р—Р°РїСѓСЃРєР°СЋ РїРµСЂРІС‹Р№ РІС…РѕРґ Рё РѕР±РЅРѕРІР»РµРЅРёРµ РїСЂР°РІРёР»."
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
                    elif parsed.path == "/api/actions/resume":
                        result = run_resume(store)
                    elif parsed.path == "/api/actions/confirm-intake":
                        result = confirm_intake_rules(store)
                    elif parsed.path == "/api/actions/plan-filters":
                        result = run_plan_filters(store)
                    elif parsed.path == "/api/actions/run-selected":
                        if store.load_runtime_settings().dashboard_mode == "analyze":
                            result = self._start_analyze_job(limit=int(body.get("limit", 120)))
                        else:
                            result = run_selected_mode(store)
                    elif parsed.path == "/api/actions/analyze":
                        result = self._start_analyze_job(limit=int(body.get("limit", 120)))
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
                                else f"Сейчас уже идет пакетный отклик по колонке {active_category or 'unknown'}. Запуск для {category or 'unknown'} не начат."
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
                                    "message": f"Р—Р°РїСѓСЃРєР°СЋ РїР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ {category}.",
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
                                            "apply_batch_message": f"РРґС‘С‚ РїР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ {category}.",
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
                                                "message": str(batch_result.get("message") or "РџР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє Р·Р°РІРµСЂС€С‘РЅ."),
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
                                "message": f"РџР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ {category} Р·Р°РїСѓС‰РµРЅ РІ С„РѕРЅРµ.",
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
                        if not hh_login_status.get("running"):
                            hh_login_status.update(
                                {
                                    "running": True,
                                    "status": "running",
                                    "message": "РћС‚РєСЂС‹РІР°СЋ hh.ru РґР»СЏ РІС…РѕРґР°.",
                                    "started_at": utc_now_iso(),
                                    "finished_at": "",
                                }
                            )

                            def _worker() -> None:
                                try:
                                    result = run_hh_login(project_root)
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
                    elif parsed.path == "/api/actions/select-account":
                        result = select_hh_account(store, account_key=str(body.get("account_key") or ""))
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
                    "message": "РџСЂРѕРІРµСЂСЏСЋ РІС…РѕРґ РІ hh.ru, СЂРµР·СЋРјРµ Рё РіРѕС‚РѕРІРЅРѕСЃС‚СЊ live-РїРѕРёСЃРєР°.",
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
                            analyze_status["message"] = "РќРµ РЅР°Р№РґРµРЅ hh_state.json. РћС‚РєСЂС‹РІР°СЋ Р±СЂР°СѓР·РµСЂ РґР»СЏ РІС…РѕРґР° РІ hh.ru."
                    else:
                        with mutation_lock:
                            analyze_status["phase"] = "resumes"
                            analyze_status["message"] = "РћР±РЅРѕРІР»СЏСЋ СЂРµР·СЋРјРµ hh.ru Рё РїРѕРґРіРѕС‚Р°РІР»РёРІР°СЋ live-РїРѕРёСЃРє РІР°РєР°РЅСЃРёР№."

                    with mutation_lock:
                        analyze_status["phase"] = "analysis"
                        analyze_status["message"] = "РћР±РЅРѕРІР»СЏСЋ РІР°РєР°РЅСЃРёРё СЃ hh.ru Рё РїРµСЂРµСЃС‡РёС‚С‹РІР°СЋ РёС… РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ С‚РµРєСѓС‰РµРіРѕ РїСЂРѕС„РёР»СЏ."
                    def _progress(*, done: int, total: int, title: str, strategy: str) -> None:
                        worker_store.update_dashboard_state(
                            {
                                "analysis_progress_done": done,
                                "analysis_progress_total": total,
                                "analysis_progress_title": title,
                                "analysis_progress_strategy": strategy,
                                "analysis_progress_updated_at": utc_now_iso(),
                            }
                        )
                        with mutation_lock:
                            analyze_status["message"] = (
                                f"РћС†РµРЅРёРІР°СЋ РІР°РєР°РЅСЃРёРё: {done}/{total}."
                                + (f" РџРѕСЃР»РµРґРЅСЏСЏ: {title}." if title else "")
                            )

                    result = run_analyze(worker_store, limit=limit, interactive=False, progress_callback=_progress)
                    with mutation_lock:
                        analyze_status.update(
                            {
                                "running": False,
                                "status": str(result.get("status") or "completed"),
                                "phase": "completed" if result.get("status") == "completed" else "blocked",
                                "message": str(result.get("message") or "РђРЅР°Р»РёР· Р·Р°РІРµСЂС€РµРЅ."),
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

    return DashboardHandler


def start_dashboard_server(project_root: Path, host: str = "127.0.0.1", port: int = 8766) -> DashboardHandle:
    handler = _handler_factory(project_root.resolve())
    server = ThreadingHTTPServer((host, port), handler)
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host == "0.0.0.0" else str(actual_host)
    url = f"http://{browser_host}:{actual_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return DashboardHandle(server=server, thread=thread, url=url)


