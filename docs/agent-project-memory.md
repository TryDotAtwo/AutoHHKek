# Agent project memory

Краткие правила и журнал правок для ассистентов.

## Конвенции

- Дашборд: статика в `autohhkek/dashboard/assets/` (`app.js`, `app.css`, `index.html`).
- Карточки резюме: разметка `renderResumeChooser` в `app.js`; стили `.resume-card`, `.resume-card-actions` в `app.css`.

## История

| Дата | Изменение |
|------|-----------|
| 2026-04-01 | `.resume-card-actions`: вместо flex со `space-between` — CSS grid `repeat(2, minmax(0, 1fr))` + `min-width: 0` на кнопках, чтобы «Открыть»/«Выбрать» не вылезали из карточки в узкой колонке. |
| 2026-04-01 | Intake: больше отступов (`.intake-stage`, `.intake-dialog-shell`, чат `.chat-composer`/`.chat-log`); карточки аккаунтов `.account-card` с `gap`. |
| 2026-04-01 | Снимок: `intake.resume_sync_extracted` из `last_resume_sync_extracted` (компактно). `hhLoginReady` в `app.js`: также true при успешном `profile_sync` или при выбранном резюме + списке hh. `intakeResumeFacts` / `intakeResumeIntel`: фолбэки из extracted и anamnesis. Сервер: `run_resume` после `select-resume` только если sync `updated`/`no_changes`; то же после `select-account` при успешном sync. |
| 2026-04-01 | Чат: `.sidebar` + `.chat-panel` flex-колонка, `.chat-log` единственный скролл (`flex`+`min-height:0`), композер `flex-shrink:0`, `scrollbar-gutter:stable`. Пайплайн: крупнее `gap`/padding. Шаг 2 пайплайна: кнопка «Изменить резюме» (`hh-resumes`). Заголовок резюме: `_cleanup_resume_title` + `stripResumeTitleNoise` убирают склейку `…LLMsОбновлено`. |
