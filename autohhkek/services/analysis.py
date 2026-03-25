from __future__ import annotations

import re
from collections import Counter

from autohhkek.domain.enums import FitCategory, ReasonGroup
from autohhkek.domain.models import Anamnesis, AssessmentReason, UserPreferences, Vacancy, VacancyAssessment


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item.strip())
    return result


def infer_salary_from_text(text: str) -> tuple[int | None, int | None]:
    numbers = [int(item.replace(" ", "")) for item in re.findall(r"(\d[\d ]{3,})", text)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers), max(numbers)


class VacancyRuleEngine:
    def __init__(self, preferences: UserPreferences, anamnesis: Anamnesis) -> None:
        self.preferences = preferences
        self.anamnesis = anamnesis
        skill_pool = preferences.required_skills + preferences.preferred_skills + anamnesis.primary_skills + anamnesis.secondary_skills
        self.skill_pool = [normalize_text(item) for item in unique_preserve_order(skill_pool)]

    def assess(self, vacancy: Vacancy) -> VacancyAssessment:
        text = normalize_text(vacancy.searchable_text())
        reasons: list[AssessmentReason] = []
        score = 50.0
        hard_block: AssessmentReason | None = None

        excluded_terms = self.preferences.excluded_companies + self.preferences.excluded_keywords + self.preferences.forbidden_keywords
        for term in excluded_terms:
            normalized = normalize_text(term)
            if normalized and normalized in text:
                hard_block = AssessmentReason(
                    code="hard_block",
                    label="Жёсткое исключение",
                    group=ReasonGroup.NEGATIVE,
                    detail=f"Найдён запрещённый маркер: {term}",
                    weight=-100,
                    subcategory="blacklisted_employer",
                )
                break

        target_hits = [title for title in self.preferences.target_titles if normalize_text(title) in text]
        if target_hits:
            score += 18
            reasons.append(
                AssessmentReason(
                    code="title_match",
                    label="Совпадение по роли",
                    group=ReasonGroup.POSITIVE,
                    detail=f"Вакансия пересекается с целевой ролью: {', '.join(target_hits[:2])}",
                    weight=18,
                    subcategory="role_fit",
                )
            )
        elif self.preferences.target_titles:
            score -= 8
            reasons.append(
                AssessmentReason(
                    code="title_gap",
                    label="Слабое совпадение по роли",
                    group=ReasonGroup.NEUTRAL,
                    detail="В названии вакансии нет явного совпадения с целевыми ролями.",
                    weight=-8,
                    subcategory="title_gap",
                )
            )

        required_hits = [skill for skill in self.preferences.required_skills if normalize_text(skill) in text]
        required_missing = [skill for skill in self.preferences.required_skills if normalize_text(skill) not in text]
        if required_hits:
            bonus = min(20, len(required_hits) * 6)
            score += bonus
            reasons.append(
                AssessmentReason(
                    code="required_skills_present",
                    label="Обязательные навыки совпадают",
                    group=ReasonGroup.POSITIVE,
                    detail=f"Найдены обязательные навыки: {', '.join(required_hits[:4])}",
                    weight=bonus,
                    subcategory="must_have_hit",
                )
            )
        if required_missing:
            penalty = min(24, len(required_missing) * 8)
            score -= penalty
            reasons.append(
                AssessmentReason(
                    code="required_skills_missing",
                    label="Часть must-have не найдена",
                    group=ReasonGroup.NEUTRAL if len(required_missing) == 1 else ReasonGroup.NEGATIVE,
                    detail=f"Не найдены навыки: {', '.join(required_missing[:4])}",
                    weight=-penalty,
                    subcategory="skill_gap",
                )
            )

        preferred_hits = [skill for skill in self.skill_pool if skill and skill in text]
        if preferred_hits:
            bonus = min(16, len(set(preferred_hits)) * 2)
            score += bonus
            reasons.append(
                AssessmentReason(
                    code="skill_overlap",
                    label="Есть стековое пересечение",
                    group=ReasonGroup.POSITIVE,
                    detail=f"Совпали ключевые слова стека: {', '.join(sorted(set(preferred_hits))[:6])}",
                    weight=bonus,
                    subcategory="skill_overlap",
                )
            )

        remote_terms = ("удален", "remote", "гибрид", "hybrid")
        is_remote = vacancy.is_remote or any(term in text for term in remote_terms)
        if self.preferences.remote_only:
            if is_remote:
                score += 8
                reasons.append(
                    AssessmentReason(
                        code="remote_match",
                        label="Подходит по формату работы",
                        group=ReasonGroup.POSITIVE,
                        detail="Вакансия выглядит удалённой или гибридной.",
                        weight=8,
                        subcategory="remote_fit",
                    )
                )
            else:
                score -= 20
                reasons.append(
                    AssessmentReason(
                        code="remote_required",
                        label="Нет удалённого формата",
                        group=ReasonGroup.NEGATIVE,
                        detail="Пользователь ищет только remote-вакансии.",
                        weight=-20,
                        subcategory="format_mismatch",
                    )
                )
        elif self.preferences.preferred_locations:
            location_hit = any(normalize_text(location) in text for location in self.preferences.preferred_locations)
            if location_hit or is_remote:
                score += 6
                reasons.append(
                    AssessmentReason(
                        code="location_fit",
                        label="Подходит по географии",
                        group=ReasonGroup.POSITIVE,
                        detail="Локация или remote-формат совпадают с ожиданиями.",
                        weight=6,
                        subcategory="location_fit",
                    )
                )
            elif not self.preferences.allow_relocation:
                score -= 12
                reasons.append(
                    AssessmentReason(
                        code="location_gap",
                        label="Сомнение по локации",
                        group=ReasonGroup.NEGATIVE,
                        detail="Локация не совпадает, а релокация отключена.",
                        weight=-12,
                        subcategory="location_mismatch",
                    )
                )

        salary_from, salary_to = vacancy.salary_from, vacancy.salary_to
        if salary_from is None and salary_to is None:
            salary_from, salary_to = infer_salary_from_text(f"{vacancy.salary_text}\n{vacancy.description}\n{vacancy.summary}")
        if self.preferences.salary_min:
            visible_salary = salary_to or salary_from
            if visible_salary is None:
                score -= 4
                reasons.append(
                    AssessmentReason(
                        code="salary_unknown",
                        label="Зарплата не указана",
                        group=ReasonGroup.NEUTRAL,
                        detail="Без диапазона зарплаты вакансия требует ручной проверки.",
                        weight=-4,
                        subcategory="missing_salary",
                    )
                )
            elif visible_salary >= self.preferences.salary_min:
                score += 8
                reasons.append(
                    AssessmentReason(
                        code="salary_fit",
                        label="Зарплата в диапазоне",
                        group=ReasonGroup.POSITIVE,
                        detail=f"Видимая зарплата не ниже {self.preferences.salary_min:,} RUB".replace(",", " "),
                        weight=8,
                        subcategory="salary_fit",
                    )
                )
            else:
                score -= 18
                reasons.append(
                    AssessmentReason(
                        code="salary_low",
                        label="Зарплата ниже порога",
                        group=ReasonGroup.NEGATIVE,
                        detail=f"Видимая зарплата ниже желаемого минимума {self.preferences.salary_min:,} RUB".replace(",", " "),
                        weight=-18,
                        subcategory="salary_low",
                    )
                )

        if any(marker in text for marker in ("тест", "опрос", "анкета", "скрининг")):
            score -= 3
            reasons.append(
                AssessmentReason(
                    code="screening_required",
                    label="Потребуется опрос или тест",
                    group=ReasonGroup.NEUTRAL,
                    detail="В тексте вакансии упоминаются анкеты, тесты или скрининг.",
                    weight=-3,
                    subcategory="screening_or_test_required",
                )
            )

        if "сопровод" in text:
            reasons.append(
                AssessmentReason(
                    code="cover_letter_requested",
                    label="Может потребоваться сопроводительное",
                    group=ReasonGroup.NEUTRAL,
                    detail="Есть признаки запроса на сопроводительное письмо.",
                    weight=0,
                    subcategory="cover_letter_requested",
                )
            )

        if hard_block:
            reasons.append(hard_block)
            score = min(score, 15)
            category = FitCategory.NO_FIT
            subcategory = hard_block.subcategory or "blacklisted_employer"
        else:
            if score >= 72:
                category = FitCategory.FIT
            elif score >= 45:
                category = FitCategory.DOUBT
            else:
                category = FitCategory.NO_FIT
            subcategory = self._pick_subcategory(category, reasons)

        explanation = self._build_explanation(category, reasons, score)
        action = {
            FitCategory.FIT: "Можно переводить в apply-flow после проверки анкеты/резюме.",
            FitCategory.DOUBT: "Нужен ручной разбор причин и, возможно, уточнение правил.",
            FitCategory.NO_FIT: "Отклик не нужен, вакансию стоит оставить только в архиве анализа.",
        }[category]

        return VacancyAssessment(
            vacancy_id=vacancy.vacancy_id,
            category=category,
            subcategory=subcategory,
            score=round(score, 1),
            explanation=explanation,
            reasons=reasons,
            recommended_action=action,
            ready_for_apply=category == FitCategory.FIT,
        )

    def _pick_subcategory(self, category: FitCategory, reasons: list[AssessmentReason]) -> str:
        if not reasons:
            return "manual_review"
        matching = [reason.subcategory for reason in reasons if reason.subcategory]
        if not matching:
            return "manual_review"
        counts = Counter(matching)
        if category == FitCategory.FIT and "screening_or_test_required" in counts:
            return "fit_but_screening"
        return counts.most_common(1)[0][0]

    def _build_explanation(self, category: FitCategory, reasons: list[AssessmentReason], score: float) -> str:
        if not reasons:
            return f"Категория {category.value}, итоговый score {score:.1f}, но причин не накопилось."
        lead = {
            FitCategory.FIT: "Вакансия выглядит подходящей.",
            FitCategory.DOUBT: "Вакансия требует ручного разбора.",
            FitCategory.NO_FIT: "Вакансия сейчас не подходит.",
        }[category]
        top = sorted(reasons, key=lambda item: abs(item.weight), reverse=True)[:3]
        details = "; ".join(reason.detail for reason in top)
        return f"{lead} Score {score:.1f}. Ключевые причины: {details}"
