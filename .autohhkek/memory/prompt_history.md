# Prompt History

## 2026-03-28

- Пользователь: "Почему резюме 2, если их 3 судя по всему".
- Выполнено: проверены правила проекта, найден источник счётчика, сопоставлены UI, snapshot, `hh_resumes.json` и debug HTML, после чего усилен сборщик каталога резюме hh.ru.
- Пользователь: "Резюме вибрал, но агент там не спарсил ничего, по сути".
- Выполнено: найден guard, который блокировал `profile_sync` без уже созданных `preferences/anamnesis`; поток исправлен на bootstrap-парсинг выбранного резюме.
- Пользователь: "В интейке нужно дать возможность акк поменять тоже, а не просто войти кнопка. Плюс может несколько разних акков же бить".
- Выполнено: в intake-экран встроен существующий switcher hh-аккаунтов с действиями `войти в другой аккаунт`, `переключить`, `удалить`.
- Пользователь: "Также вот чат неактивен, резюме не перепарсило".
- Выполнено: переключение аккаунта теперь триггерит автосинк выбранного резюме нового аккаунта, а intake-форма перестаёт зависать в `wait`, если резюме уже выбрано.
- Пользователь: "Я уже вошел, а просит войти. Это тупо. Над UX интейка поработай, сделай шрифтики меньше и прочее."
- Выполнено: переписан state-aware copy для intake overlay и уплотнена типографика/геометрия intake-экрана.
- User: compact the intake screen, rework the cards, remove false login prompts, and restart the dashboard. Done: pipeline-style intake layout, tighter cards/typography, state-aware copy, automatic pre-question resume read trigger, dashboard restarted.
- User: show which facts the agent already extracted and fix the false backend error shown when starting intake from a selected resume. Done: explicit facts block added in intake UI; run_resume bootstraps empty profile instead of requiring completed intake.
- User: LLM via OpenRouter should extract the full resume, ground examples in the resume, and show the extracted facts explicitly. Done: resume sync now feeds cleaned full text to OpenRouter; interview examples derive from extracted facts; UI facts block expanded with summary/strengths/constraints.
- User: remove extra noise from cleaned resume text, verify full cleaned resume and vacancies are passed to LLM, explain 29 vs 20 vacancies, and fix chat buttons overlapping messages. Done: resume cleaner gets footer end-markers; vacancy parser now scroll-loads more results; vacancy OpenRouter prompt now includes vacancy_searchable_text; chat header/actions wrap properly.
- User: 29 vacancies is obviously too low; broaden hh search and verify full cleaned resume/vacancy text reaches LLM. Done: planner switched to broader resume-first search, parser scroll-loads result cards, vacancy OpenRouter prompt includes vacancy_searchable_text, chat quick-actions wrapping fixed.
