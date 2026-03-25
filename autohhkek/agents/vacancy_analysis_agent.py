from __future__ import annotations

from collections import Counter
import hashlib

from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import RunSummary, Vacancy, VacancyAssessment
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.hh_refresh import HHVacancyRefresher
from autohhkek.services.llm_runtime import LLMRuntime
from autohhkek.services.profile_rules import compose_rules_markdown
from autohhkek.services.seed import import_legacy_vacancies
from autohhkek.services.storage import WorkspaceStore, _vacancy_signature, build_vacancy_snapshot_hash

from .vacancy_review_agent import VacancyReviewAgent


class VacancyAnalysisAgent:
    def __init__(self, store: WorkspaceStore, vacancy_refresher: HHVacancyRefresher | None = None) -> None:
        self.store = store
        self.vacancy_refresher = vacancy_refresher or HHVacancyRefresher(store)

    def ensure_vacancies(self, limit: int = 150, *, refresh: bool = True) -> tuple[list[Vacancy], dict[str, object]]:
        refresh_result = {"status": "skipped", "reason": "refresh_disabled", "message": "Live refresh disabled."}
        if refresh:
            refresh_result = self.vacancy_refresher.refresh(limit=limit)
        vacancies = self.store.load_vacancies()
        if vacancies or refresh_result.get("status") in {"updated", "empty"}:
            return vacancies, refresh_result
        legacy_path = self.store.project_root / "vacancies_cache.json"
        imported = import_legacy_vacancies(self.store, legacy_path, limit=limit)
        if refresh_result.get("status") == "skipped":
            refresh_result = {
                "status": "seeded_cache",
                "reason": "legacy_cache",
                "message": f"Loaded {len(imported)} vacancies from legacy cache.",
                "count": len(imported),
            }
        return imported, refresh_result

    def analyze(self, limit: int = 150, *, progress_callback=None) -> tuple[RunSummary, list[VacancyAssessment]]:
        preferences = self.store.load_preferences()
        anamnesis = self.store.load_anamnesis()
        runtime_settings = self.store.load_runtime_settings()
        if not preferences or not anamnesis:
            raise RuntimeError("Нельзя анализировать вакансии без intake.")

        rules_markdown = compose_rules_markdown(self.store, preferences, anamnesis)
        self.store.save_selection_rules(rules_markdown)

        llm_runtime = LLMRuntime(runtime_settings)
        effective_backend = llm_runtime.effective_backend()
        previous_vacancies = {item.vacancy_id: item for item in self.store.load_vacancies()}
        previous_assessments = {item.vacancy_id: item for item in self.store.load_assessments()}
        vacancies, refresh_result = self.ensure_vacancies(limit=0, refresh=True)
        vacancies = vacancies[:limit]

        reviewer = VacancyReviewAgent(preferences, anamnesis, llm_backend=effective_backend)
        assessments: list[VacancyAssessment] = []
        reused_assessments = 0
        total_to_review = len(vacancies)
        if progress_callback:
            progress_callback(done=0, total=total_to_review, title="", strategy="starting")
        for index, vacancy in enumerate(vacancies, start=1):
            previous_vacancy = previous_vacancies.get(vacancy.vacancy_id)
            previous_assessment = previous_assessments.get(vacancy.vacancy_id)
            if previous_vacancy and previous_assessment and _vacancy_signature(previous_vacancy) == _vacancy_signature(vacancy):
                assessments.append(previous_assessment)
                reused_assessments += 1
            else:
                assessments.append(reviewer.review(vacancy))
            if index == total_to_review or index % 5 == 0:
                self.store.save_assessments(assessments)
            if progress_callback:
                progress_callback(
                    done=index,
                    total=total_to_review,
                    title=vacancy.title,
                    strategy=getattr(assessments[-1], "review_strategy", ""),
                )
        self.store.save_assessments(assessments)

        filter_plan = HHFilterPlanner(
            preferences,
            anamnesis,
            selected_resume_id=self.store.load_selected_resume_id(),
            llm_backend=effective_backend,
        ).build()
        self.store.save_filter_plan(filter_plan)
        vacancy_hash = build_vacancy_snapshot_hash(vacancies)
        review_strategy_counts = Counter(item.review_strategy for item in assessments)
        llm_reviewed_count = sum(
            count for strategy, count in review_strategy_counts.items() if strategy and strategy != "rule_based_fallback"
        )
        analysis_state = {
            "run_id": "",
            "assessed_at": "",
            "rules_rebuilt_at": "",
            "rules_hash": hashlib.sha1(rules_markdown.encode("utf-8")).hexdigest(),
            "rules_preview": rules_markdown[:1500],
            "vacancy_snapshot_hash": vacancy_hash,
            "vacancy_count": len(vacancies),
            "assessment_count": len(assessments),
            "requested_backend": runtime_settings.llm_backend,
            "effective_backend": effective_backend,
            "requested_model": getattr(runtime_settings, f"{runtime_settings.llm_backend}_model", ""),
            "effective_model": filter_plan.get("llm_model") or filter_plan.get("model") or "",
            "refresh_result": refresh_result,
            "filter_search_text": filter_plan.get("search_text") or "",
            "filter_query_params": filter_plan.get("query_params") or {},
            "review_strategy_counts": dict(review_strategy_counts),
            "llm_reviewed_count": llm_reviewed_count,
            "rule_fallback_count": review_strategy_counts.get("rule_based_fallback", 0),
            "reused_assessment_count": reused_assessments,
            "stale": False,
            "stale_reason": "",
        }

        counts = {
            FitCategory.FIT.value: sum(1 for item in assessments if item.category == FitCategory.FIT),
            FitCategory.DOUBT.value: sum(1 for item in assessments if item.category == FitCategory.DOUBT),
            FitCategory.NO_FIT.value: sum(1 for item in assessments if item.category == FitCategory.NO_FIT),
        }
        run = RunSummary(
            run_id=self.store.build_run_id("analyze"),
            mode="analyze",
            status="completed",
            processed=len(assessments),
            counts=counts,
            notes=[
                "Режим анализа не делает отклики.",
                "Перед анализом правила и hh.ru фильтры пересобраны из текущего профиля.",
                f"Источник вакансий: {refresh_result.get('message') or refresh_result.get('reason') or 'unknown'}",
                f"LLM backend: requested {runtime_settings.llm_backend}, effective {effective_backend}.",
                "Для каждой вакансии сохранены категория и причины.",
            ],
        )
        run.finished_at = run.started_at
        self.store.save_run(run)
        analysis_state["run_id"] = run.run_id
        analysis_state["assessed_at"] = run.finished_at
        analysis_state["rules_rebuilt_at"] = run.finished_at
        self.store.save_analysis_state(analysis_state)
        self.store.record_event("rules", "Rebuilt search rules from current profile before analysis.", run_id=run.run_id)
        self.store.record_event(
            "analysis",
            f"Проанализировано {len(assessments)} вакансий.",
            details={**counts, "refresh": refresh_result, "effective_backend": effective_backend},
            run_id=run.run_id,
        )
        self.store.record_event("filters", "Построен script-first план фильтров hh.ru.", details=filter_plan, run_id=run.run_id)
        return run, assessments
