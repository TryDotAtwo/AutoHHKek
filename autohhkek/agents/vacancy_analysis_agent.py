# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
import asyncio
import copy
import hashlib
import json
from typing import Any

from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import RunSummary, Vacancy, VacancyAssessment
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.hh_refresh import HHVacancyRefresher
from autohhkek.services.llm_runtime import LLMRuntime
from autohhkek.services.profile_rules import compose_rules_markdown
from autohhkek.services.seed import import_legacy_vacancies
from autohhkek.services.storage import WorkspaceStore, _vacancy_signature, build_vacancy_snapshot_hash

from .vacancy_review_agent import VacancyReviewAgent


TARGET_ROUNDS = 10


class VacancyAnalysisAgent:
    def __init__(self, store: WorkspaceStore, vacancy_refresher: HHVacancyRefresher | None = None) -> None:
        self.store = store
        self.vacancy_refresher = vacancy_refresher or HHVacancyRefresher(store)

    # ---------------------------
    # helper: round expansion
    # ---------------------------
    def _expand_search_rounds(self, filter_plan: dict[str, Any]) -> dict[str, Any]:
        rounds = list(filter_plan.get("search_rounds") or [])
        if len(rounds) >= TARGET_ROUNDS:
            return filter_plan

        base_params = dict(filter_plan.get("query_params") or {})
        expanded = list(rounds)

        for i in range(TARGET_ROUNDS - len(rounds)):
            new_params = copy.deepcopy(base_params)

            # Детерминированные вариации.
            if i % 5 == 0:
                new_params.pop("experience", None)
            elif i % 5 == 1:
                new_params["order_by"] = "publication_time"
            elif i % 5 == 2:
                new_params["search_field"] = "name"
            elif i % 5 == 3:
                new_params["search_field"] = "description"
            elif i % 5 == 4:
                new_params["order_by"] = "relevance"

            expanded.append(
                {
                    "id": f"auto_{i + 1}",
                    "query_params": new_params,
                    "initial_max_pages": 100,
                    "persist_serp_cache": False,
                    "max_pages_cap": None,
                }
            )

        patched = dict(filter_plan)
        patched["search_rounds"] = expanded
        return patched

    # ---------------------------
    # helper: human-readable round preview
    # ---------------------------
    def _compact_query_params(self, params: dict[str, Any]) -> str:
        if not params:
            return "без доп. параметров"

        keys_priority = [
            "text",
            "search_field",
            "area",
            "experience",
            "schedule",
            "employment",
            "order_by",
            "salary",
            "only_with_salary",
        ]

        parts: list[str] = []
        for key in keys_priority:
            if key not in params:
                continue
            value = params.get(key)
            if value in ("", None, [], {}):
                continue
            if isinstance(value, (list, tuple)):
                value_text = ",".join(str(item) for item in value if str(item).strip())
            else:
                value_text = str(value)
            if value_text.strip():
                parts.append(f"{key}={value_text}")

        if not parts:
            for key, value in list(params.items())[:6]:
                if value in ("", None, [], {}):
                    continue
                parts.append(f"{key}={value}")

        return "; ".join(parts[:6]) if parts else "без доп. параметров"

    def _rounds_preview(self, filter_plan: dict[str, Any]) -> list[dict[str, str]]:
        rounds = list(filter_plan.get("search_rounds") or [])
        preview: list[dict[str, str]] = []
        for index, item in enumerate(rounds, start=1):
            query_params = dict(item.get("query_params") or {})
            preview.append(
                {
                    "index": str(index),
                    "id": str(item.get("id") or f"round_{index}"),
                    "params_text": self._compact_query_params(query_params),
                }
            )
        return preview

    # ---------------------------
    # helper: unified progress emitter
    # ---------------------------
    def _emit_progress(
        self,
        progress_callback,
        *,
        stage: str,
        message: str,
        done: int = 0,
        total: int = 0,
        title: str = "",
        strategy: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not progress_callback:
            return
        progress_callback(
            stage=stage,
            message=message,
            done=done,
            total=total,
            title=title,
            strategy=strategy,
            details=details or {},
        )

    # ---------------------------
    # helper: refresh logger -> progress callback
    # ---------------------------
    def _make_refresh_logger(self, progress_callback):
        def _log(line: str) -> None:
            text = str(line or "").strip()
            if not text:
                return
            self._emit_progress(
                progress_callback,
                stage="refresh",
                message=text,
                strategy="refresh",
            )
        return _log

    # ---------------------------
    def ensure_vacancies(self, limit: int = 150, *, refresh: bool = True) -> tuple[list[Vacancy], dict[str, object]]:
        refresh_result: dict[str, object] = {
            "status": "skipped",
            "reason": "refresh_disabled",
            "message": "Live refresh disabled.",
        }

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

    # ---------------------------
    # shared finalize
    # ---------------------------
    def _finalize_analysis(
        self,
        *,
        vacancies: list[Vacancy],
        assessments: list[VacancyAssessment],
        refresh_result: dict[str, Any],
        rules_markdown: str,
        runtime_settings,
        effective_backend: str,
        filter_plan: dict[str, Any],
        reused_assessments: int,
        failed_reviews: int,
        max_concurrency: int | None,
    ) -> tuple[RunSummary, list[VacancyAssessment]]:
        vacancy_hash = build_vacancy_snapshot_hash(vacancies)
        review_strategy_counts = Counter(item.review_strategy for item in assessments)
        llm_reviewed_count = sum(count for strategy, count in review_strategy_counts.items() if strategy)

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
            "search_rounds_count": len(filter_plan.get("search_rounds") or []),
            "review_strategy_counts": dict(review_strategy_counts),
            "llm_reviewed_count": llm_reviewed_count,
            "failed_review_count": failed_reviews,
            "reused_assessment_count": reused_assessments,
            "stale": False,
            "stale_reason": "",
        }

        counts = {
            FitCategory.FIT.value: sum(1 for item in assessments if item.category == FitCategory.FIT),
            FitCategory.DOUBT.value: sum(1 for item in assessments if item.category == FitCategory.DOUBT),
            FitCategory.NO_FIT.value: sum(1 for item in assessments if item.category == FitCategory.NO_FIT),
        }

        notes = [
            "Режим анализа не делает отклики.",
            "Перед анализом правила и hh.ru фильтры пересобраны из текущего профиля.",
            f"Источник вакансий: {refresh_result.get('message') or refresh_result.get('reason') or 'unknown'}",
            f"LLM backend: requested {runtime_settings.llm_backend}, effective {effective_backend}.",
            f"Search rounds: {len(filter_plan.get('search_rounds') or [])}.",
            "Для каждой успешно обработанной вакансии сохранены категория и причины.",
            f"LLM review failures: {failed_reviews}.",
            f"Reused cached assessments: {reused_assessments}.",
        ]
        if max_concurrency is not None:
            notes.append(f"Concurrency: {max(1, int(max_concurrency or 1))}.")

        run = RunSummary(
            run_id=self.store.build_run_id("analyze"),
            mode="analyze",
            status="completed",
            processed=len(assessments),
            counts=counts,
            notes=notes,
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
            details={
                **counts,
                "refresh": refresh_result,
                "effective_backend": effective_backend,
                "failed_review_count": failed_reviews,
                "reused_assessment_count": reused_assessments,
                "requested_total": len(vacancies),
                "saved_assessments": len(assessments),
                "search_rounds_count": len(filter_plan.get("search_rounds") or []),
                "search_text": filter_plan.get("search_text") or "",
                "search_url": filter_plan.get("search_url") or "",
            },
            run_id=run.run_id,
        )
        self.store.record_event("filters", "Построен script-first план фильтров hh.ru.", details=filter_plan, run_id=run.run_id)

        return run, assessments

    # ---------------------------
    def analyze(self, limit: int = 150, *, progress_callback=None) -> tuple[RunSummary, list[VacancyAssessment]]:
        preferences = self.store.load_preferences()
        anamnesis = self.store.load_anamnesis()
        runtime_settings = self.store.load_runtime_settings()

        if not preferences or not anamnesis:
            raise RuntimeError("Нельзя анализировать вакансии без intake.")

        rules_markdown = compose_rules_markdown(self.store, preferences, anamnesis)
        self.store.save_selection_rules(rules_markdown)

        self._emit_progress(
            progress_callback,
            stage="rules_build",
            message="Пересобираю правила поиска из текущего профиля.",
            strategy="rules_build",
        )

        llm_runtime = LLMRuntime(runtime_settings)
        effective_backend = llm_runtime.effective_backend()

        previous_vacancies = {item.vacancy_id: item for item in self.store.load_vacancies()}
        previous_assessments = {item.vacancy_id: item for item in self.store.load_assessments()}

        filter_plan = HHFilterPlanner(
            preferences,
            anamnesis,
            selected_resume_id=self.store.load_selected_resume_id(),
            llm_backend=effective_backend,
        ).build()
        filter_plan = self._expand_search_rounds(filter_plan)
        self.store.save_filter_plan(filter_plan)

        rounds_preview = self._rounds_preview(filter_plan)
        rounds_preview_text = " | ".join(
            f"{item['index']}/{len(rounds_preview)} [{item['id']}] {item['params_text']}" for item in rounds_preview[:10]
        )
        self._emit_progress(
            progress_callback,
            stage="filter_plan",
            message=(
                f"Построил hh-фильтры. Search rounds: {len(rounds_preview)}. "
                f"Поисковый текст: {filter_plan.get('search_text') or 'не задан'}. "
                f"URL: {filter_plan.get('search_url') or 'ещё не построен'}. "
                f"Раунды: {rounds_preview_text or 'не заданы'}."
            ),
            strategy="filter_plan",
            details={
                "search_text": filter_plan.get("search_text") or "",
                "search_url": filter_plan.get("search_url") or "",
                "search_rounds": rounds_preview,
            },
        )

        refresh_logger = self._make_refresh_logger(progress_callback)
        refresh_result = self.vacancy_refresher.refresh(limit=0, log_line=refresh_logger)
        vacancies = self.store.load_vacancies()

        if not vacancies and refresh_result.get("status") not in {"updated", "empty"}:
            legacy_path = self.store.project_root / "vacancies_cache.json"
            imported = import_legacy_vacancies(self.store, legacy_path, limit=limit)
            if refresh_result.get("status") == "skipped":
                refresh_result = {
                    "status": "seeded_cache",
                    "reason": "legacy_cache",
                    "message": f"Loaded {len(imported)} vacancies from legacy cache.",
                    "count": len(imported),
                }
            vacancies = imported

        if limit and limit > 0:
            vacancies = vacancies[:limit]

        reviewer = VacancyReviewAgent(preferences, anamnesis, llm_backend=effective_backend)
        assessments: list[VacancyAssessment] = []
        reused_assessments = 0
        failed_reviews = 0
        total_to_review = len(vacancies)

        self._emit_progress(
            progress_callback,
            stage="review_start",
            message=(
                f"Начинаю LLM-анализ вакансий. Всего: {total_to_review}. "
                f"После refresh: {refresh_result.get('count') or len(vacancies)}. "
                f"Источник: {refresh_result.get('message') or refresh_result.get('reason') or 'unknown'}."
            ),
            done=0,
            total=total_to_review,
            strategy="review_start",
        )

        for index, vacancy in enumerate(vacancies, start=1):
            previous_vacancy = previous_vacancies.get(vacancy.vacancy_id)
            previous_assessment = previous_assessments.get(vacancy.vacancy_id)

            if previous_vacancy and previous_assessment and _vacancy_signature(previous_vacancy) == _vacancy_signature(vacancy):
                assessment = previous_assessment
                reused_assessments += 1
            else:
                assessment = reviewer.review(vacancy)
                if assessment is None:
                    failed_reviews += 1
                    continue

            assessments.append(assessment)

            if index % 5 == 0 or index == total_to_review:
                self.store.save_assessments(assessments)

            self._emit_progress(
                progress_callback,
                stage="review_progress",
                message=(
                    f"Анализирую вакансии: {index}/{total_to_review}. "
                    f"Последняя: {vacancy.title}. "
                    f"Reuse: {reused_assessments}. Ошибок LLM: {failed_reviews}."
                ),
                done=index,
                total=total_to_review,
                title=vacancy.title,
                strategy=getattr(assessment, "review_strategy", ""),
                details={
                    "reused": reused_assessments,
                    "failed": failed_reviews,
                    "saved": len(assessments),
                },
            )

        self.store.save_assessments(assessments)

        self._emit_progress(
            progress_callback,
            stage="finalize",
            message="Сохраняю результаты анализа, summary и колонки.",
            done=total_to_review,
            total=total_to_review,
            strategy="finalize",
        )

        return self._finalize_analysis(
            vacancies=vacancies,
            assessments=assessments,
            refresh_result=refresh_result,
            rules_markdown=rules_markdown,
            runtime_settings=runtime_settings,
            effective_backend=effective_backend,
            filter_plan=filter_plan,
            reused_assessments=reused_assessments,
            failed_reviews=failed_reviews,
            max_concurrency=None,
        )

    # ---------------------------
    async def analyze_async(
        self,
        limit: int = 150,
        *,
        progress_callback=None,
        max_concurrency: int = 6,
    ) -> tuple[RunSummary, list[VacancyAssessment]]:
        preferences = self.store.load_preferences()
        anamnesis = self.store.load_anamnesis()
        runtime_settings = self.store.load_runtime_settings()

        if not preferences or not anamnesis:
            raise RuntimeError("Нельзя анализировать вакансии без intake.")

        rules_markdown = compose_rules_markdown(self.store, preferences, anamnesis)
        self.store.save_selection_rules(rules_markdown)

        self._emit_progress(
            progress_callback,
            stage="rules_build",
            message="Пересобираю правила поиска из текущего профиля.",
            strategy="rules_build",
        )

        llm_runtime = LLMRuntime(runtime_settings)
        effective_backend = llm_runtime.effective_backend()

        previous_vacancies = {item.vacancy_id: item for item in self.store.load_vacancies()}
        previous_assessments = {item.vacancy_id: item for item in self.store.load_assessments()}

        filter_plan = HHFilterPlanner(
            preferences,
            anamnesis,
            selected_resume_id=self.store.load_selected_resume_id(),
            llm_backend=effective_backend,
        ).build()
        filter_plan = self._expand_search_rounds(filter_plan)
        self.store.save_filter_plan(filter_plan)

        rounds_preview = self._rounds_preview(filter_plan)
        rounds_preview_text = " | ".join(
            f"{item['index']}/{len(rounds_preview)} [{item['id']}] {item['params_text']}" for item in rounds_preview[:10]
        )
        self._emit_progress(
            progress_callback,
            stage="filter_plan",
            message=(
                f"Построил hh-фильтры. Search rounds: {len(rounds_preview)}. "
                f"Поисковый текст: {filter_plan.get('search_text') or 'не задан'}. "
                f"URL: {filter_plan.get('search_url') or 'ещё не построен'}. "
                f"Раунды: {rounds_preview_text or 'не заданы'}."
            ),
            strategy="filter_plan",
            details={
                "search_text": filter_plan.get("search_text") or "",
                "search_url": filter_plan.get("search_url") or "",
                "search_rounds": rounds_preview,
            },
        )

        refresh_logger = self._make_refresh_logger(progress_callback)
        refresh_result = await self.vacancy_refresher.refresh_async(limit=0, log_line=refresh_logger)
        vacancies = self.store.load_vacancies()

        if not vacancies and refresh_result.get("status") not in {"updated", "empty"}:
            legacy_path = self.store.project_root / "vacancies_cache.json"
            imported = import_legacy_vacancies(self.store, legacy_path, limit=limit)
            if refresh_result.get("status") == "skipped":
                refresh_result = {
                    "status": "seeded_cache",
                    "reason": "legacy_cache",
                    "message": f"Loaded {len(imported)} vacancies from legacy cache.",
                    "count": len(imported),
                }
            vacancies = imported

        if limit and limit > 0:
            vacancies = vacancies[:limit]

        reviewer = VacancyReviewAgent(preferences, anamnesis, llm_backend=effective_backend)

        assessments: list[VacancyAssessment | None] = [None] * len(vacancies)
        reused_assessments = 0
        failed_reviews = 0
        total_to_review = len(vacancies)

        self._emit_progress(
            progress_callback,
            stage="review_start",
            message=(
                f"Начинаю LLM-анализ вакансий. Всего: {total_to_review}. "
                f"После refresh: {refresh_result.get('count') or len(vacancies)}. "
                f"Concurrency: {max(1, int(max_concurrency or 1))}."
            ),
            done=0,
            total=total_to_review,
            strategy="review_start",
            details={
                "concurrency": max(1, int(max_concurrency or 1)),
                "refresh_result": refresh_result,
            },
        )

        semaphore = asyncio.Semaphore(max(1, int(max_concurrency or 1)))
        progress_lock = asyncio.Lock()
        done_counter = 0

        async def review_one(index: int, vacancy: Vacancy) -> None:
            nonlocal reused_assessments, failed_reviews, done_counter

            previous_vacancy = previous_vacancies.get(vacancy.vacancy_id)
            previous_assessment = previous_assessments.get(vacancy.vacancy_id)

            if previous_vacancy and previous_assessment and _vacancy_signature(previous_vacancy) == _vacancy_signature(vacancy):
                assessment = previous_assessment
                async with progress_lock:
                    reused_assessments += 1
            else:
                async with semaphore:
                    assessment = await reviewer.review_async(vacancy)

            assessments[index] = assessment

            async with progress_lock:
                if assessment is None:
                    failed_reviews += 1

                done_counter += 1

                self._emit_progress(
                    progress_callback,
                    stage="review_progress",
                    message=(
                        f"Анализирую вакансии: {done_counter}/{total_to_review}. "
                        f"Последняя: {vacancy.title}. "
                        f"Reuse: {reused_assessments}. Ошибок LLM: {failed_reviews}."
                    ),
                    done=done_counter,
                    total=total_to_review,
                    title=vacancy.title,
                    strategy=getattr(assessment, "review_strategy", "") if assessment else "failed",
                    details={
                        "reused": reused_assessments,
                        "failed": failed_reviews,
                        "saved": len([item for item in assessments if item is not None]),
                    },
                )

                if done_counter == total_to_review or done_counter % 5 == 0:
                    self.store.save_assessments([item for item in assessments if item is not None])

        await asyncio.gather(*(review_one(index, vacancy) for index, vacancy in enumerate(vacancies)))

        final_assessments = [item for item in assessments if item is not None]
        self.store.save_assessments(final_assessments)

        self._emit_progress(
            progress_callback,
            stage="finalize",
            message="Сохраняю результаты анализа, summary и колонки.",
            done=total_to_review,
            total=total_to_review,
            strategy="finalize",
        )

        return self._finalize_analysis(
            vacancies=vacancies,
            assessments=final_assessments,
            refresh_result=refresh_result,
            rules_markdown=rules_markdown,
            runtime_settings=runtime_settings,
            effective_backend=effective_backend,
            filter_plan=filter_plan,
            reused_assessments=reused_assessments,
            failed_reviews=failed_reviews,
            max_concurrency=max_concurrency,
        )