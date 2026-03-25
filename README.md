# AutoHHKek

# БОТ СПАМАНУЛ, ТУТ ВСЁ СЛОМАНО НАХУЙ ЖПТШКОЙ 5.4 ПОФИКШУ ЗАВТРА (26.03)



Script-first hh.ru automation with agent review, long-term memory, vacancy analysis, intake, resume drafting, apply planning, and a local dashboard.

The project entrypoint is the root file:

```powershell
python main.py
```

## Architecture

- The agent reads vacancies, user rules, anamnesis, and imported markdown files.
- Deterministic scripts perform hh.ru UI actions such as search filters, resume selection, and apply clicks.
- OpenAI and OpenRouter are used only where reasoning is valuable: vacancy review and filter intent planning.
- If a scripted DOM action fails, the runtime prepares a Playwright MCP repair task instead of replacing the whole flow with free-form browsing.
- Workspace memory, logs, plans, and artifacts are stored in `.autohhkek/`.

## Commands

```powershell
python main.py overview
python main.py intake
python main.py import-rules path\to\rules.md
python main.py analyze --limit 120
python main.py plan-filters
python main.py resume --print
python main.py plan-apply
python main.py dashboard --open-browser
```

## Environment

The project loads `.env` from the repository root at startup. A ready-to-copy template lives in [.env.example](/C:/Users/Иван%20Литвак/source/repos/AutoHHKek/AutoHHKek/.env.example).

Set the environment before enabling the agent layer:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:AUTOHHKEK_OPENAI_MODEL="gpt-5.4"
$env:OPENROUTER_API_KEY="or-..."
$env:AUTOHHKEK_OPENROUTER_MODEL="openai/gpt-4o-mini"
$env:AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND="npx"
$env:AUTOHHKEK_PLAYWRIGHT_MCP_ARGS="-y @playwright/mcp@latest"
```

Behavior:

- Without `OPENAI_API_KEY`, vacancy review and filter planning fall back to deterministic rules.
- Without `OPENROUTER_API_KEY`, the OpenRouter backend is visible in the UI but falls back to deterministic rules.
- `g4f` is available as an alternative LLM backend and can be selected from the dashboard.
- Without Playwright MCP configuration, script fallback is still planned and logged, but the repair bridge is reported as not configured.
- Dashboard and `python main.py overview` show which runtime path is active.

OpenRouter is used through the OpenAI-compatible API at `https://openrouter.ai/api/v1`. The official docs describe the OpenAI-compatible setup and the optional `HTTP-Referer` / `X-OpenRouter-Title` headers used for attribution: [OpenRouter quickstart](https://openrouter.ai/docs/quickstart) and [OpenRouter OpenAI SDK guide](https://openrouter.ai/docs/guides/community/openai-sdk).

## Dashboard Control Plane

- Start the dashboard with `python main.py dashboard`.
- The dashboard persists runtime controls in `.autohhkek/memory/runtime_settings.json`.
- You can switch between `openai`, `openrouter`, and `g4f`, choose the dashboard work mode, and launch actions directly from the UI.
- OpenAI mode supports MCP-based repair execution.
- OpenRouter mode uses the same OpenAI-compatible agent flow and supports MCP-based repair execution.
- g4f mode supports vacancy/filter agents and repair-plan generation, while repair execution remains plan-only.

## Runtime Layout

After the first run, the project stores working state in `.autohhkek/`:

- `memory/` for user preferences and anamnesis
- `rules/` for generated and imported vacancy selection rules
- `snapshots/` for cached vacancies and assessments
- `artifacts/` for resume drafts and apply plans
- `runs/` for run summaries
- `events/` for JSONL event logs

## Testing

Run the test suite from the repository root:

```powershell
py -3 -m pytest -q
```
