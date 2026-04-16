from __future__ import annotations

import argparse
import ast
import asyncio
import json
import textwrap
import webbrowser
from pathlib import Path

from autohhkek.agents.application_agent import ApplicationAgent
from autohhkek.agents.intake_agent import IntakeAgent
from autohhkek.agents.resume_agent import ResumeAgent
from autohhkek.agents.vacancy_analysis_agent import VacancyAnalysisAgent
from autohhkek.dashboard.server import start_dashboard_server
from autohhkek.domain.enums import FitCategory
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.rule_loader import apply_rule_bundles, load_rule_bundle
from autohhkek.services.rules import build_selection_rules_markdown
from autohhkek.services.storage import WorkspaceStore


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="AutoHHKek",
        description="Agentic runtime for hh.ru analysis, resume prep, script-first UI automation, and observability.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("overview", help="Show runtime overview.")

    intake = subparsers.add_parser("intake", help="Collect anamnesis and search rules.")
    intake.add_argument("--no-interactive", action="store_true", help="Do not ask questions, bootstrap from legacy data only.")
    intake.add_argument("--rules-md", nargs="*", default=[], help="Extra markdown rule files to import.")

    import_rules = subparsers.add_parser("import-rules", help="Import extra markdown rule files.")
    import_rules.add_argument("paths", nargs="+", help="Paths to markdown files.")

    analyze = subparsers.add_parser("analyze", help="Review vacancies without applying.")
    analyze.add_argument("--limit", type=int, default=120)
    analyze.add_argument("--no-interactive", action="store_true")
    analyze.add_argument("--rules-md", nargs="*", default=[], help="Extra markdown rule files to import before analysis.")

    filter_plan = subparsers.add_parser("plan-filters", help="Build hh.ru filter plan from current rules.")
    filter_plan.add_argument("--as-json", action="store_true")

    resume = subparsers.add_parser("resume", help="Build resume draft.")
    resume.add_argument("--print", action="store_true", dest="print_result")

    dashboard = subparsers.add_parser("dashboard", help="Start local dashboard.")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8766)
    dashboard.add_argument("--open-browser", action="store_true")
    dashboard.add_argument("--limit", type=int, default=120)

    plan_apply = subparsers.add_parser("plan-apply", help="Build apply plan for the best candidate vacancy.")
    plan_apply.add_argument("--vacancy-id", default="")

    repair = subparsers.add_parser("plan-repair", help="Build or run a Playwright MCP repair task for a broken script action.")
    repair.add_argument("--action", required=True, help="Script action name, for example click_apply_button.")
    repair.add_argument("--payload-json", default="{}", help="JSON payload for the failed action.")
    repair.add_argument("--error", default="missing_script", help="Failure reason or exception text.")
    repair.add_argument("--run-agent", action="store_true", help="Run the OpenAI + MCP repair worker instead of only preparing the task.")

    return parser


def _import_rule_paths(store: WorkspaceStore, paths: list[str]) -> int:
    if not paths:
        return 0
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    if not preferences or not anamnesis:
        raise RuntimeError("Import rules requires intake/anamnesis first.")

    bundles = [load_rule_bundle(Path(path)) for path in paths]
    updated_preferences, updated_anamnesis, imported_section = apply_rule_bundles(
        preferences,
        anamnesis,
        bundles,
        current_rules_markdown="",
    )
    final_rules = build_selection_rules_markdown(updated_preferences, updated_anamnesis)
    if imported_section.strip():
        final_rules = f"{final_rules.rstrip()}\n\n{imported_section.strip()}\n"

    store.save_preferences(updated_preferences)
    store.save_anamnesis(updated_anamnesis)
    store.save_selection_rules(final_rules)
    for bundle in bundles:
        store.save_imported_rule(bundle.source_path, bundle.raw_markdown)
    store.record_event("rules", f"Imported {len(bundles)} markdown rule file(s).", details={"paths": paths})
    return len(bundles)


def _print_overview(store: WorkspaceStore) -> None:
    runtime = HHAutomationRuntime(project_root=store.project_root)
    capabilities = runtime.describe_capabilities()
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    vacancies = store.load_vacancies()
    assessments = store.load_assessments()
    runs = store.list_runs(limit=5)
    imported_rules = store.load_imported_rules()
    filter_plan = store.load_filter_plan()
    runtime_settings = store.load_runtime_settings()
    print("AutoHHKek runtime overview")
    print(f"runtime_dir: {store.paths.runtime_root}")
    print(f"intake_ready: {bool(preferences and anamnesis)}")
    print(f"vacancies_cached: {len(vacancies)}")
    print(f"assessments_cached: {len(assessments)}")
    print(f"runs_recorded: {len(runs)}")
    print(f"imported_rule_files: {len(imported_rules)}")
    print(f"filter_plan_ready: {bool(filter_plan)}")
    print(f"openai_ready: {capabilities['openai_ready']}")
    print(f"openai_model: {capabilities['openai_model'] if capabilities['openai_ready'] else 'disabled'}")
    print(f"openrouter_ready: {capabilities.get('openrouter_ready', False)}")
    print(f"openrouter_model: {capabilities['openrouter_model'] if capabilities.get('openrouter_ready') else 'disabled'}")
    print(f"playwright_mcp_ready: {capabilities['playwright_mcp_ready']}")
    print(f"selected_llm_backend: {runtime_settings.llm_backend}")
    print(f"dashboard_mode: {runtime_settings.dashboard_mode}")
    if preferences:
        print(f"target_titles: {', '.join(preferences.target_titles) or 'not set'}")
        print(f"cover_letter_mode: {preferences.cover_letter_mode}")
    if filter_plan:
        print(f"filter_planner_backend: {filter_plan.get('planner_backend', 'rules')}")
    if runs:
        print(f"latest_run: {runs[0].run_id} ({runs[0].mode}, {runs[0].status})")


def _print_analysis_summary(store: WorkspaceStore) -> None:
    vacancies = {item.vacancy_id: item for item in store.load_vacancies()}
    assessments = sorted(store.load_assessments(), key=lambda item: item.score, reverse=True)
    print("Analysis complete.")
    for category in (FitCategory.FIT, FitCategory.DOUBT, FitCategory.NO_FIT):
        current = [item for item in assessments if item.category == category][:5]
        print(f"\n[{category.value}] {len([item for item in assessments if item.category == category])}")
        for item in current:
            vacancy = vacancies.get(item.vacancy_id)
            title = vacancy.title if vacancy else item.vacancy_id
            print(f" - {title} | score={item.score:.1f} | {item.subcategory}")
            print(f"   {item.explanation}")
            print(f"   review_strategy={item.review_strategy}")


def _parse_payload_json(raw: str) -> dict:
    text = raw.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = ast.literal_eval(text)
    if not isinstance(payload, dict):
        raise ValueError("payload-json must decode to an object/dict")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["dashboard", "--open-browser"])
    command = args.command

    store = WorkspaceStore(project_root())
    intake_agent = IntakeAgent(store)
    analysis_agent = VacancyAnalysisAgent(store)
    resume_agent = ResumeAgent(store)
    application_agent = ApplicationAgent(store)
    runtime = HHAutomationRuntime(project_root=store.project_root)

    if command == "overview":
        _print_overview(store)
        return 0

    if command == "intake":
        preferences, anamnesis = intake_agent.ensure(interactive=not args.no_interactive)
        imported_count = _import_rule_paths(store, args.rules_md)
        print("Intake saved.")
        print(f"role: {anamnesis.headline}")
        print(f"titles: {', '.join(preferences.target_titles)}")
        print(f"rules_path: {store.paths.rules_markdown_path}")
        if imported_count:
            print(f"imported_rule_files: {imported_count}")
        return 0

    if command == "import-rules":
        intake_agent.ensure(interactive=False)
        imported_count = _import_rule_paths(store, args.paths)
        print(f"imported_rule_files: {imported_count}")
        print(f"rules_path: {store.paths.rules_markdown_path}")
        return 0

    if command == "analyze":
        intake_agent.ensure(interactive=not args.no_interactive)
        if args.rules_md:
            _import_rule_paths(store, args.rules_md)
        analysis_agent.analyze(limit=args.limit)
        _print_analysis_summary(store)
        print(f"\nDashboard data: {store.paths.runtime_root}")
        return 0

    if command == "plan-filters":
        intake_agent.ensure(interactive=False)
        preferences = store.load_preferences()
        anamnesis = store.load_anamnesis()
        runtime_settings = store.load_runtime_settings()
        if not preferences or not anamnesis:
            raise RuntimeError("Filter plan requires intake first.")
        plan = HHFilterPlanner(
            preferences,
            anamnesis,
            selected_resume_id=store.load_selected_resume_id(),
            llm_backend=runtime_settings.llm_backend,
        ).build()
        store.save_filter_plan(plan)
        store.record_event("filters", "Updated hh.ru filter plan.")
        if args.as_json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(f"search_text: {plan['search_text']}")
            print(f"strategy: {plan['strategy']}")
            print(f"planner_backend: {plan['planner_backend']}")
            print(f"query_params: {plan['query_params']}")
            print("ui_actions:")
            for action in plan["ui_actions"]:
                print(f" - {action['action']} [{action['strategy']}]")
            if plan["residual_rules"]:
                print("residual_rules:")
                for rule in plan["residual_rules"]:
                    print(f" - {rule}")
            if plan.get("planning_notes"):
                print("planning_notes:")
                for note in plan["planning_notes"]:
                    print(f" - {note}")
        return 0

    if command == "resume":
        intake_agent.ensure(interactive=True)
        _, markdown = resume_agent.build_resume_draft()
        print(f"resume_draft: {store.paths.resume_draft_path}")
        if args.print_result:
            print()
            print(markdown)
        return 0

    if command == "dashboard":
        handle = start_dashboard_server(store.project_root, host=args.host, port=args.port)
        print(f"dashboard_url: {handle.url}")
        print("Press Ctrl+C to stop the dashboard.")
        if args.open_browser:
            webbrowser.open(handle.url)
        try:
            handle.thread.join()
        except KeyboardInterrupt:
            handle.close()
        return 0

    if command == "plan-apply":
        intake_agent.ensure(interactive=False)
        if not store.load_assessments():
            asyncio.run(analysis_agent.analyze_async(limit=0, max_concurrency=60))
        payload = application_agent.build_plan(vacancy_id=args.vacancy_id or None)
        print(f"vacancy: {payload['vacancy']['title']}")
        print(f"backend: {payload['runtime']['backend']}")
        print(f"selected_llm_backend: {payload['runtime'].get('selected_llm_backend', runtime.runtime_settings.llm_backend)}")
        print(f"openai_ready: {payload['runtime']['openai_ready']}")
        print(f"openrouter_ready: {payload['runtime'].get('openrouter_ready', False)}")
        print(f"playwright_mcp_ready: {payload['runtime']['playwright_mcp_ready']}")
        print(f"screening_platform: {payload['screening_plan']['platform']}")
        print(f"cover_letter_enabled: {payload['cover_letter_enabled']}")
        print("\nscript_actions:")
        for action in payload["script_actions"]:
            print(f" - {action['action']} [{action['strategy']}]")
        print("\nstages:")
        for stage in payload["stages"]:
            print(f" - {stage['title']}: {stage['status']}")
        if payload["cover_letter_preview"]:
            print("\ncover_letter_preview:")
            print(textwrap.shorten(payload["cover_letter_preview"], width=300, placeholder="..."))
        return 0

    if command == "plan-repair":
        payload_json = _parse_payload_json(args.payload_json)
        payload = runtime.run_repair(args.action, payload_json, args.error) if args.run_agent else runtime.build_repair_plan(args.action, payload_json, args.error)
        print(f"action: {payload['action']}")
        print(f"selected_llm_backend: {payload.get('selected_llm_backend', runtime.runtime_settings.llm_backend)}")
        print(f"status: {payload.get('status', 'prepared')}")
        print(f"openai_ready: {payload['openai_ready']}")
        print(f"openrouter_ready: {payload.get('openrouter_ready', False)}")
        print(f"mcp_ready: {payload['mcp_ready']}")
        print(f"repair_patch_path: {payload['repair_patch_path']}")
        print(f"repair_test_path: {payload['repair_test_path']}")
        print("\nprompt:")
        print(payload["prompt"])
        if payload.get("output"):
            print("\npatch_summary:")
            print(payload["output"].get("patch_summary", ""))
        if payload.get("worker_error"):
            print(f"\nworker_error: {payload['worker_error']}")
        return 0

    parser.error(f"Unknown command: {command}")
    return 2
