const tabs = [
  { id: "agent", label: "Агент" },
  { id: "vacancies", label: "Вакансии" },
  { id: "vacancy", label: "Карточка" },
  { id: "activity", label: "Ход работы" },
];

const categoryMeta = {
  fit: { label: "Подходит", hint: "Можно рассматривать к отклику", className: "lane--fit" },
  doubt: { label: "Сомневаюсь", hint: "Нужен ручной разбор", className: "lane--doubt" },
  no_fit: { label: "Не подходит", hint: "Лучше не тратить время", className: "lane--no-fit" },
};

const decisionMeta = {
  fit: { label: "Подходит", className: "button--fit" },
  doubt: { label: "Сомневаюсь", className: "button--doubt" },
  no_fit: { label: "Не подходит", className: "button--no-fit" },
};

const reasonMeta = {
  positive: { label: "За", className: "reason-lane--positive" },
  neutral: { label: "Нужно уточнить", className: "reason-lane--neutral" },
  negative: { label: "Против", className: "reason-lane--negative" },
};

const state = {
  snapshot: null,
  activeTab: "agent",
  userSelectedTab: false,
  selectedVacancyId: "",
  openDetails: {},
  isBusy: false,
  pendingActionMessage: "",
  workspaceScrollTopByTab: { agent: 0, vacancies: 0, vacancy: 0, activity: 0 },
  chatScrollTop: 0,
  chatWasNearBottom: true,
  autoRefreshPauseUntil: 0,
  refreshInFlight: false,
  announcements: new Set(),
  chatHistory: [
    {
      role: "assistant",
      text: "Опиши задачу в чате. Я веду логин в hh.ru, выбор резюме, правила поиска, фильтры и разбор вакансий.",
    },
  ],
};

const LAYOUT_STORAGE_KEY = "dashboard.sidebar.width";

function clampSidebarWidth(width) {
  const safeWidth = Number.isFinite(width) ? width : 384;
  const minWidth = 320;
  const viewportCap = Math.max(minWidth, window.innerWidth - 520);
  return Math.max(minWidth, Math.min(viewportCap, safeWidth));
}

function parseStoredSidebarWidth(rawValue) {
  const numeric = Number.parseFloat(String(rawValue || "").replace("px", "").trim());
  return clampSidebarWidth(numeric);
}

function applySidebarWidth(shell, rawValue) {
  if (!shell) return;
  const width = parseStoredSidebarWidth(rawValue);
  const cssValue = `${width}px`;
  shell.style.setProperty("--sidebar-width", cssValue);
  window.localStorage.setItem(LAYOUT_STORAGE_KEY, cssValue);
}

function pauseAutoRefresh(ms = 6000) {
  state.autoRefreshPauseUntil = Math.max(state.autoRefreshPauseUntil, Date.now() + ms);
}

function currentWorkspace() {
  return document.querySelector(".workspace");
}

function rememberScrollState() {
  const workspace = currentWorkspace();
  const chatLog = document.getElementById("chat-log");
  if (workspace) {
    state.workspaceScrollTopByTab[state.activeTab] = workspace.scrollTop;
  }
  if (chatLog) {
    state.chatScrollTop = chatLog.scrollTop;
    state.chatWasNearBottom = chatLog.scrollTop + chatLog.clientHeight >= chatLog.scrollHeight - 24;
  }
}

function restoreScrollState() {
  const workspace = currentWorkspace();
  const chatLog = document.getElementById("chat-log");
  if (workspace) {
    const nextTop = state.workspaceScrollTopByTab[state.activeTab] || 0;
    workspace.scrollTop = nextTop;
    window.requestAnimationFrame(() => {
      workspace.scrollTop = nextTop;
    });
  }
  if (chatLog) {
    const nextTop = state.chatWasNearBottom ? chatLog.scrollHeight : state.chatScrollTop;
    chatLog.scrollTop = nextTop;
    window.requestAnimationFrame(() => {
      chatLog.scrollTop = nextTop;
    });
  }
}

function setActiveTab(nextTab, { userInitiated = false, pauseMs = 2500 } = {}) {
  if (!nextTab) return;
  rememberScrollState();
  state.activeTab = nextTab;
  if (userInitiated) state.userSelectedTab = true;
  if (pauseMs > 0) pauseAutoRefresh(pauseMs);
  renderTabbar();
  updateVisibleTab();
  restoreScrollState();
}

function shouldSkipRefresh() {
  if (state.refreshInFlight || state.isBusy || Date.now() < state.autoRefreshPauseUntil) return true;
  const active = document.activeElement;
  if (!active) return false;
  const tag = (active.tagName || "").toUpperCase();
  return tag === "TEXTAREA" || tag === "INPUT" || active.isContentEditable;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&nbsp;", " ")
    .replace(/\u00a0/g, " ")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDate(value) {
  if (!value) return "нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function renderList(items, renderItem, emptyText) {
  return items && items.length ? items.map(renderItem).join("") : `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    throw new Error(errorPayload.error || `HTTP ${response.status}`);
  }
  return response.json();
}

async function sendClientLog(kind, payload) {
  try {
    await fetch("/api/client-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, payload }),
      keepalive: true,
    });
  } catch {}
}

function pickDefaultCard(snapshot) {
  return snapshot.columns?.fit?.[0] || snapshot.columns?.doubt?.[0] || snapshot.columns?.no_fit?.[0] || null;
}

function isIntakeReady(snapshot) {
  return Boolean(snapshot?.setup_summary?.intake_ready);
}

function isIntakeConfirmed(snapshot) {
  return Boolean(snapshot?.setup_summary?.intake_confirmed);
}

function intakeDialogState(snapshot) {
  return snapshot?.intake_dialog || {};
}

function activeIntakeQuestion(snapshot) {
  const dialog = intakeDialogState(snapshot);
  const questions = Array.isArray(dialog.questions) ? dialog.questions : [];
  const stepIndex = Number(dialog.step_index || 0);
  return questions[stepIndex] || null;
}

function intakePriorityLabel(question) {
  const importance = String(question?.importance || "").toLowerCase();
  if (importance === "critical") return "Сейчас фиксируем обязательные критерии";
  if (importance === "important") return "Теперь уточняем важные предпочтения";
  return "В конце добираем тонкие пожелания";
}

function categoryLabel(category) {
  return categoryMeta[category]?.label || category || "неизвестно";
}

function findCardById(snapshot, vacancyId) {
  if (!snapshot || !vacancyId) return null;
  for (const key of Object.keys(snapshot.columns || {})) {
    const found = (snapshot.columns[key] || []).find((item) => item.id === vacancyId);
    if (found) return found;
  }
  return null;
}

function currentCard(snapshot) {
  return findCardById(snapshot, state.selectedVacancyId) || pickDefaultCard(snapshot);
}

function listBoardVacancyIds(snapshot) {
  return ["fit", "doubt", "no_fit"].flatMap((key) => (snapshot.columns?.[key] || []).map((item) => item.id));
}

function nextVacancyId(snapshot, currentId) {
  const ids = listBoardVacancyIds(snapshot);
  if (!ids.length) return "";
  const index = ids.indexOf(currentId);
  if (index < 0) return ids[0];
  return ids[index + 1] || ids[index - 1] || ids[0];
}

function ensureSelectedVacancy(snapshot) {
  const selected = currentCard(snapshot);
  if (selected) state.selectedVacancyId = selected.id;
}

function preferredTab(snapshot) {
  if (!isIntakeReady(snapshot) || !snapshot?.setup_summary?.ready_to_run || snapshot?.analysis_job?.running || snapshot?.hh_login?.running) {
    return "agent";
  }
  const mode = snapshot?.runtime_settings?.dashboard_mode || "analyze";
  if (mode === "apply_plan") return "vacancy";
  return "vacancies";
}

function updateVisibleTab() {
  tabs.forEach((tab) => {
    document.getElementById(`${tab.id}-view`)?.classList.toggle("is-active", state.activeTab === tab.id);
  });
}

function renderTabbar() {
  const root = document.getElementById("tabbar");
  root.innerHTML = tabs
    .map((tab) => `<button class="tab-button ${state.activeTab === tab.id ? "is-active" : ""}" data-tab="${escapeHtml(tab.id)}">${escapeHtml(tab.label)}</button>`)
    .join("");
  root.querySelectorAll("[data-tab]").forEach((node) =>
    node.addEventListener("click", () => {
      setActiveTab(node.getAttribute("data-tab") || "agent", { userInitiated: true });
    }),
  );
}

function pipelineStatusMeta(status) {
  switch (status) {
    case "completed":
      return { label: "Готово", className: "pipeline-pill--completed" };
    case "active":
      return { label: "Сейчас", className: "pipeline-pill--active" };
    case "blocked":
      return { label: "Блокер", className: "pipeline-pill--blocked" };
    default:
      return { label: "Далее", className: "pipeline-pill--pending" };
  }
}

function buildPipeline(snapshot) {
  const hhResumes = snapshot.hh_resumes || [];
  const hasLogin = Boolean(snapshot.hh_login?.state_file_exists);
  const loginRunning = Boolean(snapshot.hh_login?.running);
  const selectedResume = Boolean(snapshot.selected_resume_id);
  const intakeReady = Boolean(snapshot.setup_summary?.intake_ready);
  const intakeStructuredReady = Boolean(snapshot.setup_summary?.intake_structured_ready);
  const intakeDialogCompleted = Boolean(snapshot.setup_summary?.intake_dialog_completed);
  const intakeConfirmed = Boolean(snapshot.setup_summary?.intake_confirmed);
  const resumeDraftReady = Boolean(snapshot.setup_summary?.resume_draft_ready);
  const profileSyncReady = ["updated", "no_changes"].includes(snapshot.profile_sync?.status || "");
  const rulesReady = Boolean(snapshot.setup_summary?.rules_loaded);
  const filterPlanReady = Boolean(snapshot.filter_plan && Object.keys(snapshot.filter_plan).length);
  const vacanciesLoaded = (snapshot.counts?.total_vacancies || 0) > 0;
  const liveStats = snapshot.setup_summary?.live_refresh_stats || {};
  const hhTotal = liveStats.total_available || 0;
  const assessedCount = snapshot.counts?.assessed || 0;
  const analyzing = Boolean(snapshot.analysis_job?.running);
  const multipleResumes = hhResumes.length > 1;

  return [
    {
      id: "login",
      title: "1. ????? ? hh.ru",
      status: loginRunning ? "active" : hasLogin ? "completed" : "active",
      summary: loginRunning ? "???????????? ???????? ???? ? ????? ? ????????." : hasLogin ? "?????? hh.ru ??? ?????????." : "????? ??????? hh.ru ? ????????? ??????? ?????.",
      detail: snapshot.hh_login?.message || "???????????? ??? ?????????? ??????? ? ???????? ?????.",
      action: hasLogin && !loginRunning ? null : { id: "hh-login", label: "??????? hh.ru" },
    },
    {
      id: "resume",
      title: "2. ????? ??????",
      status: !hasLogin ? "blocked" : selectedResume ? "completed" : "active",
      summary: !hasLogin
        ? "??? ?????? ?????? ?????? ?? ????????."
        : selectedResume
          ? `??? ?????? ??????? ??????: ${snapshot.selected_resume_title || snapshot.selected_resume_id}.`
          : multipleResumes
            ? "?? hh.ru ??????? ????????? ??????. ???????????? ?????? ??????? ??????."
            : hhResumes.length
              ? "??????? ???? ??????, ????? ??????????."
              : "????? ????????? ?????? ?????? ?? hh.ru.",
      detail: snapshot.setup_summary?.live_refresh_message || "?????? ??????????, ?? ???? ????? ???? live search.",
      action: !hasLogin ? null : selectedResume ? null : { id: "hh-resumes", label: "???????? ?????? ??????" },
    },
    {
      id: "intake",
      title: "3. ???????????? intake-??????",
      status: !selectedResume && multipleResumes ? "blocked" : intakeReady ? "completed" : "active",
      summary: intakeReady
        ? "????????? ??????????, ??????????? ? ???????????? ??? ??????? ? ???????."
        : intakeDialogCompleted && !intakeConfirmed
          ? "????????? ????????, ??? ?????? ??? ?????????? ??? ?????????????? ??????????????."
        : intakeDialogCompleted
          ? "?????? ????????, ?? ??????????? ?????? ??? ???????????? ??? ?????????? ??????."
          : "??????? ????? ?????? ???????? ?????? ? ???????, ? ??? ????? ????????? ????? ????????.",
      detail: intakeReady
        ? "?????? ? ?????? ???????????? ??????? ? ???????? ???????????????? ???????."
        : intakeDialogCompleted && !intakeConfirmed
          ? "Проверьте итоговую сводку правил кандидата и подтвердите её. До подтверждения поиск вакансий не запускается."
        : "?????? ???? ??? ???????? ? ????????? ?????????????: ??????? ??????? ????????, ????? ??????????? ??????. ??? ????? ????? ? ?????? ?? ???????????.",
      action: intakeDialogCompleted && !intakeConfirmed ? { id: "confirm-intake", label: "????????????? ???????" } : { id: "start-intake", label: intakeDialogCompleted ? "?????????? ?????????" : "?????? ?????" },
    },
    {
      id: "profile",
      title: "4. ????????????? ??????? ? ??????",
      status: !intakeReady ? "blocked" : resumeDraftReady && profileSyncReady && rulesReady ? "completed" : "active",
      summary: resumeDraftReady && profileSyncReady && rulesReady
        ? "???????, ???????? ?????? ? ??????? ????????????????."
        : "????? ??????? ????? ??????????? ???????, ??????? ? ???????? ??????.",
      detail: intakeStructuredReady
        ? snapshot.profile_sync?.message || "????? ?????????? ??????? ????? ???????? ??????? ? ??????? ?? ?????????? ??????."
        : "??????????? ?????? ???? ????: ????? ??????? ????????? intake-??????.",
      action: !intakeReady ? null : { id: "resume-sync", label: "???????? ???????" },
    },
    {
      id: "filters",
      title: "5. ??????? ? ??????? ????????",
      status: !intakeReady
        ? "blocked"
        : !selectedResume
          ? "blocked"
          : analyzing
            ? "active"
            : filterPlanReady && vacanciesLoaded
              ? "completed"
              : "active",
      summary: !intakeReady
        ? "????? ????? ?? ?????????? ????????????? intake."
        : !selectedResume
          ? "??????? ????? ??????? ?????? ??? live search."
          : filterPlanReady && vacanciesLoaded
            ? hhTotal
              ? `?? hh.ru ??????? ${hhTotal} ????????, ? ????????? ??????? ${snapshot.counts?.total_vacancies || 0}.`
              : `???? ???????? ????????, ? ??????? ${snapshot.counts?.total_vacancies || 0} ????????.`
            : filterPlanReady
              ? "??????? ??????, ???????? ???????? ??????? ????????."
              : "????? ??????? ??????? ?? ?????? ? ????????.",
      detail: snapshot.filter_plan?.search_text
        ? snapshot.setup_summary?.live_refresh_stats?.search_url
          ? `????????? ?????: ${snapshot.filter_plan.search_text}. ?????????? ?????? hh-?????? ? ???????? ??? ???????? ?? ?????.`
          : `????????? ?????: ${snapshot.filter_plan.search_text}.`
        : "??????? ?????? ????????? ?? ??????? ????, ???? ? ??????????? ?????????.",
      action: !intakeReady
        ? null
        : !selectedResume
          ? null
          : filterPlanReady
            ? { id: "analyze", label: "????????? ?????" }
            : { id: "plan-filters", label: "??????? ???????" },
    },
    {
      id: "assessment",
      title: "6. ?????? ?? 3 ????????",
      status: !intakeReady ? "pending" : analyzing ? "active" : assessedCount > 0 ? "completed" : vacanciesLoaded ? "active" : "pending",
      summary: !intakeReady
        ? "???? ???? ????????? ????? ????????????? intake ? ??????? ??????."
        : analyzing
          ? snapshot.analysis_job?.message || "???? ???????? ????????."
          : assessedCount > 0
            ? `??????? ${assessedCount} ????????: ${snapshot.counts?.fit || 0} / ${snapshot.counts?.doubt || 0} / ${snapshot.counts?.no_fit || 0}.`
            : "????? ???????? ???????? ????? ???????? ?? ?? ???? ????????.",
      detail: assessedCount > 0
        ? "?????? ???????? ?????? ??????? ???????, ???? ? ?????? ???????? ????? ????????????."
        : "???????? ??????????: ??????? = ????????, ?????? = ??????????, ??????? = ?? ????????.",
      action: assessedCount > 0 ? { id: "open-vacancies", label: "??????? ?????" } : vacanciesLoaded ? { id: "analyze", label: "??????? ????????" } : null,
    },
  ];
}


function currentPipelineStep(snapshot) {
  const pipeline = buildPipeline(snapshot);
  return pipeline.find((step) => step.status === "active") || pipeline.find((step) => step.status === "blocked") || pipeline[pipeline.length - 1];
}

function currentActivityMessage(snapshot) {
  if (state.pendingActionMessage) return state.pendingActionMessage;
  if (snapshot.apply_batch_job?.running) {
    return snapshot.apply_batch_job.message || "Идет пакетная отправка откликов.";
  }
  if (snapshot.analysis_job?.running) {
    return snapshot.analysis_job.message || "Идет анализ и разбор вакансий.";
  }
  if (snapshot.hh_login?.running) {
    return snapshot.hh_login.message || "Открыт вход в hh.ru, ожидаю авторизацию пользователя.";
  }
  return snapshot.profile_sync?.message || snapshot.analysis_state?.stale_reason || "";
}

function collectQuickActions(snapshot) {
  const actions = [];
  const currentStep = currentPipelineStep(snapshot);
  if (currentStep?.action) actions.push(currentStep.action);
  if (!isIntakeReady(snapshot)) {
    actions.push({ id: snapshot.setup_summary?.intake_dialog_completed ? "confirm-intake" : "start-intake", label: snapshot.setup_summary?.intake_dialog_completed ? "Подтвердить правила" : "Начать опрос" });
  }
  if (snapshot.hh_accounts?.length) {
    actions.push({ id: "hh-login", label: "?????? ???????" });
  }
  if (isIntakeReady(snapshot) && snapshot.selected_resume_id && snapshot.setup_summary?.rules_loaded && !snapshot.filter_plan?.search_text) {
    actions.push({ id: "plan-filters", label: "??????? ???????" });
  }
  if (isIntakeReady(snapshot) && snapshot.selected_resume_id && snapshot.filter_plan?.search_text && !snapshot.analysis_job?.running) {
    actions.push({ id: "analyze", label: "????????? ??????" });
  }
  if (isIntakeReady(snapshot) && snapshot.counts?.assessed) {
    actions.push({ id: "open-vacancies", label: "??????? ?????" });
  }
  const unique = [];
  const seen = new Set();
  for (const action of actions) {
    if (!action?.id || seen.has(action.id)) continue;
    seen.add(action.id);
    unique.push(action);
  }
  return unique.slice(0, 4);
}


function appendAssistantMessage(text, key = "") {
  if (!text) return;
  if (key && state.announcements.has(key)) return;
  if (key) state.announcements.add(key);
  const last = state.chatHistory[state.chatHistory.length - 1];
  if (last?.role === "assistant" && last?.text === text) return;
  state.chatHistory.push({ role: "assistant", text });
}

function announceSnapshotChanges(snapshot, previousSnapshot) {
  if (!previousSnapshot) {
    const step = currentPipelineStep(snapshot);
    appendAssistantMessage(step?.summary || "Готов к следующему шагу.", `initial:${step?.id || "step"}`);
    return;
  }

  if (!previousSnapshot.hh_login?.running && snapshot.hh_login?.running) {
    appendAssistantMessage("Открыл hh.ru. Пройдите вход и капчу в браузере, я подожду.", `login-running:${snapshot.hh_login?.started_at || "now"}`);
  }
  if (previousSnapshot.hh_login?.status !== snapshot.hh_login?.status && snapshot.hh_login?.status === "completed") {
    appendAssistantMessage("Вход в hh.ru завершен, можно переходить к выбору резюме.", `login-completed:${snapshot.hh_login?.finished_at || "done"}`);
  }
  if (previousSnapshot.selected_resume_id !== snapshot.selected_resume_id && snapshot.selected_resume_id) {
    appendAssistantMessage(`Зафиксировал резюме для поиска: ${snapshot.selected_resume_title || snapshot.selected_resume_id}.`, `resume:${snapshot.selected_resume_id}`);
  }
  if (!previousSnapshot.analysis_job?.running && snapshot.analysis_job?.running) {
    appendAssistantMessage(snapshot.analysis_job?.message || "Запустил анализ вакансий.", `analysis-running:${snapshot.analysis_job?.started_at || "run"}`);
  }
  if (previousSnapshot.analysis_job?.status !== snapshot.analysis_job?.status && snapshot.analysis_job?.status === "completed") {
    appendAssistantMessage(snapshot.analysis_job?.message || "Анализ вакансий завершен.", `analysis-completed:${snapshot.analysis_job?.finished_at || "done"}`);
  }
  if (!previousSnapshot.pending_rule_edit?.markdown && snapshot.pending_rule_edit?.markdown) {
    appendAssistantMessage("Подготовил черновик правки правил. Проверьте diff и подтвердите изменение в чате.", `rules-draft:${snapshot.pending_rule_edit.filename || "draft"}`);
  }
}

function renderHero(snapshot) {
  const mode = snapshot.runtime_settings?.dashboard_mode || "analyze";
  const backend = snapshot.runtime_settings?.llm_backend || "openai";
  const step = currentPipelineStep(snapshot);
  const selectedResume = snapshot.selected_resume_title || snapshot.selected_resume_id || "не выбрано";
  document.getElementById("hero-summary").textContent =
    step?.summary || snapshot.next_recommended_action?.reason || "Дашборд показывает текущее состояние поиска и очередь вакансий.";
  document.getElementById("hero-next-action").textContent = step?.title || snapshot.next_recommended_action?.label || "Ожидаю действие";
  document.getElementById("hero-next-reason").textContent = `Режим: ${mode}. Модельный backend: ${backend}. Резюме: ${selectedResume}.`;
  document.getElementById("hero-runtime").textContent = `${backend} · ${mode}`;
  document.getElementById("generated-at").textContent = `Обновлено: ${formatDate(snapshot.generated_at)}`;
}

function renderStatusStrip(snapshot) {
  const step = currentPipelineStep(snapshot);
  const cards = [
    ["Следующий шаг", step?.title || "Ожидание", step?.summary || "Пайплайн готов.", step?.status === "completed" ? "good" : step?.status === "blocked" ? "warn" : "neutral"],
    ["Логин", snapshot.hh_login?.status || "idle", snapshot.hh_login?.message || "Сессия hh.ru ожидает проверки.", snapshot.hh_login?.state_file_exists ? "good" : snapshot.hh_login?.running ? "neutral" : "warn"],
    ["Резюме", snapshot.selected_resume_title || snapshot.selected_resume_id || "не выбрано", snapshot.hh_resumes?.length ? `На hh.ru найдено ${snapshot.hh_resumes.length}.` : "Список резюме еще не подтянут.", snapshot.selected_resume_id ? "good" : "warn"],
    ["Фильтры", snapshot.filter_plan?.search_text || "не собраны", snapshot.filter_plan?.planner_backend ? `Планировщик: ${snapshot.filter_plan.planner_backend}.` : "Фильтры еще не построены.", snapshot.filter_plan?.search_text ? "good" : "warn"],
    ["Оценка", String(snapshot.counts?.assessed || 0), `${snapshot.counts?.fit || 0} подходит · ${snapshot.counts?.doubt || 0} сомнение · ${snapshot.counts?.no_fit || 0} не подходит`, snapshot.counts?.assessed ? "good" : snapshot.analysis_job?.running ? "neutral" : "warn"],
  ];
  document.getElementById("status-strip").innerHTML = cards
    .map(([label, value, note, klass]) => `<article class="status-card status-card--${escapeHtml(klass)}"><span class="status-label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><p>${escapeHtml(note)}</p></article>`)
    .join("");
}

function renderActionButtons(actions, extraClass = "") {
  return actions
    .map(
      (action) => `
        <button
          class="button ${extraClass} ${escapeHtml(action.className || "")}"
          type="button"
          data-dashboard-action="${escapeHtml(action.id || "")}"
          ${action.chatPrompt ? `data-chat-prompt="${escapeHtml(action.chatPrompt)}"` : ""}
        >
          ${escapeHtml(action.label)}
        </button>
      `,
    )
    .join("");
}

function renderResumeChooser(snapshot) {
  const resumes = snapshot.hh_resumes || [];
  if (!resumes.length) {
    return `<div class="empty-state">Список резюме пока пуст. После логина нажмите «Обновить список резюме».</div>`;
  }
  return `
    <div class="resume-grid">
      ${resumes
        .map((resume) => {
          const isActive = snapshot.selected_resume_id === resume.resume_id;
          return `
            <article class="resume-card ${isActive ? "is-active" : ""}">
              <div>
                <strong>${escapeHtml(resume.title || resume.resume_id)}</strong>
                <p class="muted">${escapeHtml(resume.resume_id)}</p>
              </div>
              <div class="resume-card-actions">
                <a class="button button--ghost" href="${escapeHtml(resume.url || "#")}" target="_blank" rel="noreferrer">Открыть</a>
                <button class="button ${isActive ? "button--primary" : ""}" type="button" data-resume-id="${escapeHtml(resume.resume_id)}">
                  ${isActive ? "Выбрано" : "Выбрать"}
                </button>
              </div>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderAccountSwitcher(snapshot) {
  const accounts = snapshot.hh_accounts || [];
  const activeKey = snapshot.active_account?.account_key || snapshot.workspace?.account_key || "";
  return `
    <div class="stack compact">
      <div class="note">
        <strong>Активный hh-аккаунт</strong>
        <p>${escapeHtml(snapshot.active_account?.display_name || activeKey || "еще не определен")}</p>
      </div>
      <div class="resume-grid">
        ${renderList(
          accounts,
          (account) => `
            <article class="resume-card ${activeKey === account.account_key ? "is-active" : ""}">
              <div>
                <strong>${escapeHtml(account.display_name || account.account_key)}</strong>
                <p class="muted">${escapeHtml(account.resume_count ? `${account.resume_count} резюме` : "резюме не определены")}</p>
              </div>
              <div class="resume-card-actions">
                <button class="button ${activeKey === account.account_key ? "button--primary" : ""}" type="button" ${activeKey === account.account_key ? "disabled" : `data-account-key="${escapeHtml(account.account_key || "")}"`}>
                  ${activeKey === account.account_key ? "Активен" : "Переключить"}
                </button>
              </div>
            </article>
          `,
          "После логина здесь появятся сохраненные hh-аккаунты.",
        )}
      </div>
    </div>
  `;
}

function renderPipeline(snapshot) {
  return buildPipeline(snapshot)
    .map((step) => {
      const status = pipelineStatusMeta(step.status);
      return `
        <article class="pipeline-step pipeline-step--${escapeHtml(step.status)}">
          <div class="pipeline-step-head">
            <div>
              <strong>${escapeHtml(step.title)}</strong>
              <p>${escapeHtml(step.summary)}</p>
            </div>
            <span class="pipeline-pill ${escapeHtml(status.className)}">${escapeHtml(status.label)}</span>
          </div>
          <p class="muted">${escapeHtml(step.detail || "")}</p>
          ${
            step.action
              ? `<div class="pipeline-step-actions">${renderActionButtons([{ ...step.action, chatPrompt: step.chatPrompt || "" }], "button--compact")}</div>`
              : ""
          }
        </article>
      `;
    })
    .join("");
}

function renderFilterPlan(snapshot) {
  const filterPlan = snapshot.filter_plan || {};
  const liveStats = snapshot.setup_summary?.live_refresh_stats || {};
  const systemRules = snapshot.intake?.system_rules_preview || "";
  const userRules = snapshot.intake?.user_rules_preview || "";
  if (!Object.keys(filterPlan).length) {
    return `<div class="empty-state">План фильтров еще не собран. Этот шаг должен происходить до анализа вакансий.</div>`;
  }
  return `
    <div class="detail-meta-grid">
      <div class="meta-row"><span>Поисковый текст</span><strong>${escapeHtml(filterPlan.search_text || "не задан")}</strong></div>
      <div class="meta-row"><span>Планировщик</span><strong>${escapeHtml(filterPlan.planner_backend || "rules")}</strong></div>
      <div class="meta-row"><span>Стратегия</span><strong>${escapeHtml(filterPlan.strategy || "script_first")}</strong></div>
      <div class="meta-row"><span>На hh.ru</span><strong>${escapeHtml(liveStats.total_available ? `${liveStats.total_available} найдено` : "еще не считали")}</strong></div>
      <div class="meta-row"><span>В локальной очереди</span><strong>${escapeHtml(String(snapshot.counts?.total_vacancies || 0))}</strong></div>
      <div class="meta-row"><span>Страниц пройдено</span><strong>${escapeHtml(liveStats.pages_parsed ? String(liveStats.pages_parsed) : "0")}</strong></div>
      <div class="meta-row"><span>Параметры запроса</span><strong>${escapeHtml(JSON.stringify(filterPlan.query_params || {}))}</strong></div>
    </div>
    ${
      filterPlan.search_url
        ? `<div class="note"><strong>HH-поиск</strong><p><a href="${escapeHtml(filterPlan.search_url)}" target="_blank" rel="noreferrer">${escapeHtml(filterPlan.search_url)}</a></p></div>`
        : ""
    }
    <div class="detail-block">
      <strong>Остаточные правила</strong>
      ${renderList(filterPlan.residual_rules || [], (item) => `<div class="reason-card"><p>${escapeHtml(item)}</p></div>`, "Дополнительных ограничений нет.")}
    </div>
    <div class="detail-meta-grid detail-meta-grid--rules">
      <div class="note"><strong>Общие правила</strong><p>${escapeHtml(systemRules || "Пока не собраны.")}</p></div>
      <div class="note"><strong>Правила пользователя</strong><p>${escapeHtml(userRules || "Пока не собраны.")}</p></div>
    </div>
  `;
}

function renderAgentView(snapshot) {
  const root = document.getElementById("agent-view");
  if (!isIntakeReady(snapshot)) {
    const dialog = intakeDialogState(snapshot);
    const questions = dialog.questions || [];
    const stepIndex = Number(dialog.step_index || 0);
    const currentQuestion = questions[stepIndex] || null;
    const context = dialog.context || {};
    const recentMessages = state.chatHistory.slice(-6);
    root.innerHTML = `
      <section class="panel intake-stage">
        <div class="intake-stage-head">
          <div>
            <span class="panel-kicker">Обязательный Intake</span>
            <h2>Сначала разберем ваш профиль, потом перейдем к поиску вакансий</h2>
            <p class="panel-lead">Это обязательный этап. Агент сначала вытягивает максимум из резюме, потом коротким диалогом собирает ваши жесткие критерии и только после этого запускает поиск и оценку вакансий.</p>
          </div>
          <div class="inline-actions">
            ${renderActionButtons([{ id: "start-intake", label: dialog.active ? "Перезапустить опрос" : "Начать опрос" }], "button--compact")}
          </div>
        </div>
        <div class="intake-stage-grid">
          <div class="note">
            <strong>Что уже взяли из резюме</strong>
            <p>${escapeHtml(context.resume_title || snapshot.selected_resume_title || "Резюме пока не выбрано.")}</p>
            <p>${escapeHtml((context.inferred_roles || []).length ? `Роли: ${context.inferred_roles.join(", ")}` : "Роли из резюме еще не уточнены.")}</p>
            <p>${escapeHtml((context.inferred_skills || []).length ? `Навыки: ${context.inferred_skills.join(", ")}` : "Навыки из резюме еще не уточнены.")}</p>
          </div>
          <div class="note">
            <strong>Что обязательно нужно узнать</strong>
            <p>${escapeHtml((snapshot.setup_summary?.intake_missing || []).length ? `Пока не закрыты: ${(snapshot.setup_summary.intake_missing || []).join(", ")}.` : "Структурные пробелы закрыты, можно переходить дальше.")}</p>
            <p>${escapeHtml(currentQuestion ? `Сейчас вопрос ${stepIndex + 1} из ${questions.length}.` : "Диалог еще не начат.")}</p>
          </div>
        </div>
        <div class="intake-dialog-shell">
          <div class="intake-transcript">
            ${renderList(
              recentMessages,
              (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "агент" : "вы")}</span><p>${escapeHtml(item.text)}</p></article>`,
              "Диалог еще не начат.",
            )}
          </div>
          <div class="intake-question-card">
            <strong>${escapeHtml(currentQuestion?.title || "Нажмите «Начать опрос», чтобы агент начал диалог.")}</strong>
            <p>${escapeHtml(currentQuestion?.why || "Сначала фиксируем жесткие критерии, потом доуточняем желательные детали.")}</p>
            <p class="muted">${escapeHtml(currentQuestion?.example || "Можно отвечать свободным текстом. Если пункт неважен, напишите «пропустить».")}</p>
          </div>
          <form id="intake-form" class="intake-form">
            <textarea id="intake-input" rows="7" placeholder="Ответьте свободным текстом. Например: только remote, не хочу госуху, роли LLM Engineer/NLP Engineer, зарплата от 350k."></textarea>
            <div class="inline-actions">
              <button class="button" type="button" data-dashboard-action="start-intake">${escapeHtml(dialog.active ? "Начать заново" : "Начать опрос")}</button>
              <button class="button button--primary" type="submit">${escapeHtml(dialog.active ? "Отправить ответ" : "Начать и перейти к вопросам")}</button>
            </div>
          </form>
        </div>
      </section>
    `;
    wireActionButtons(root);
    root.querySelector("#intake-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = root.querySelector("#intake-input");
      const value = (input?.value || "").trim();
      if (!dialog.active && !value) {
        await sendChatCommand("начать опрос");
        return;
      }
      if (!value || state.isBusy) return;
      await sendChatCommand(value);
      if (input) input.value = "";
    });
    return;
  }
  const step = currentPipelineStep(snapshot);
  const actions = collectQuickActions(snapshot);
  const selectedCard = currentCard(snapshot);
  const activityMessage = currentActivityMessage(snapshot);
  root.innerHTML = `
    <div class="agent-grid">
      <section class="panel panel--wide">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Пайплайн</span>
            <h2>${escapeHtml(step?.title || "Рабочий маршрут")}</h2>
          </div>
          <div class="inline-actions">
            ${renderActionButtons(actions, "button--compact")}
          </div>
        </div>
        <p class="panel-lead">${escapeHtml(step?.detail || "Агент ждет следующего действия.")}</p>
        <div class="pipeline-grid">${renderPipeline(snapshot)}</div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Аккаунты</span>
            <h2>Профили hh.ru</h2>
          </div>
        </div>
        <p class="panel-lead">Можно хранить несколько hh-аккаунтов в одной программе и быстро переключаться между ними.</p>
        ${renderAccountSwitcher(snapshot)}
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Резюме</span>
            <h2>Выбор для поиска</h2>
          </div>
        </div>
        <p class="panel-lead">Пользователь должен явно видеть, по какому резюме идет live search и оценка.</p>
        ${renderResumeChooser(snapshot)}
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Фильтры</span>
            <h2>Поиск перед парсингом</h2>
          </div>
        </div>
        ${renderFilterPlan(snapshot)}
      </section>

      <section class="panel panel--wide">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Фокус</span>
            <h2>Что пользователь видит сейчас</h2>
          </div>
        </div>
        <div class="focus-grid">
          <div class="note">
            <strong>Ожидание ручного действия</strong>
            <p>${escapeHtml(snapshot.hh_login?.running ? "Браузер открыт. Ждем, пока пользователь завершит логин и капчу." : snapshot.setup_summary?.live_refresh_message || "Контекст hh.ru готовится.")}</p>
          </div>
          <div class="note">
            <strong>Поиск на hh.ru</strong>
            <p>${escapeHtml(
              snapshot.setup_summary?.live_refresh_stats?.total_available
                ? `Найдено ${snapshot.setup_summary.live_refresh_stats.total_available} вакансий, локально собрано ${snapshot.setup_summary.live_refresh_stats.count || snapshot.counts?.total_vacancies || 0}.`
                : "После запуска анализа здесь появится общее число вакансий с hh.ru и прогресс парсинга.",
            )}</p>
          </div>
          <div class="note">
            <strong>Текущая рекомендация агента</strong>
            <p>${escapeHtml(snapshot.next_recommended_action?.reason || step?.summary || "Откройте чат и уточните следующий шаг.")}</p>
          </div>
          <div class="note">
            <strong>Состояние оценки</strong>
            <p>${escapeHtml(snapshot.analysis_job?.message || snapshot.analysis_state?.stale_reason || "Анализ еще не запускался.")}</p>
          </div>
          <div class="note">
            <strong>Синхронизация резюме</strong>
            <p>${escapeHtml(snapshot.profile_sync?.message || "Синхронизация еще не запускалась.")}</p>
          </div>
          <div class="note">
            <strong>Текущий кандидат на просмотр</strong>
            <p>${escapeHtml(selectedCard ? `${selectedCard.title} · ${selectedCard.category_label}` : "Вакансия еще не выбрана.")}</p>
          </div>
        </div>
      </section>
    </div>
  `;

  root.querySelectorAll("[data-resume-id]").forEach((node) =>
    node.addEventListener("click", async () => {
      const resumeId = node.getAttribute("data-resume-id") || "";
      if (!resumeId) return;
      await handleServerAction("/api/actions/select-resume", { resume_id: resumeId });
    }),
  );
  root.querySelectorAll("[data-account-key]").forEach((node) =>
    node.addEventListener("click", async () => {
      const accountKey = node.getAttribute("data-account-key") || "";
      if (!accountKey) return;
      await handleServerAction("/api/actions/select-account", { account_key: accountKey });
    }),
  );
  wireActionButtons(root);
}

function renderChatShell() {
  const root = document.getElementById("chat-sidebar");
  if (!root) return;
  root.innerHTML = `
    <section class="chat-panel">
      <div class="chat-panel-head">
        <div>
          <span class="panel-kicker">Чат агента</span>
          <h2>Чат</h2>
        </div>
      </div>
      <div id="chat-quick-actions" class="chip-row"></div>
      <div id="chat-log" class="chat-log"></div>
      <form id="chat-form" class="chat-form">
        <textarea id="chat-input" rows="4" placeholder="Напиши задачу, правку правил или уточнение по резюме"></textarea>
        <button id="chat-submit" class="button button--primary" type="submit">Отправить</button>
      </form>
    </section>
  `;
  document.getElementById("chat-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("chat-input");
    const message = (input?.value || "").trim();
    if (!message || state.isBusy) return;
    await sendChatCommand(message);
  });
  document.getElementById("chat-input")?.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    const input = document.getElementById("chat-input");
    const message = (input?.value || "").trim();
    if (!message || state.isBusy) return;
    await sendChatCommand(message);
  });
}

function renderChatLog() {
  const log = document.getElementById("chat-log");
  if (!log) return;
  log.innerHTML = renderList(
    state.chatHistory,
    (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "агент" : "вы")}</span><p>${escapeHtml(item.text)}</p></article>`,
    "Чат пуст.",
  );
  if (state.chatWasNearBottom) log.scrollTop = log.scrollHeight;
  else log.scrollTop = state.chatScrollTop;
  const button = document.getElementById("chat-submit");
  if (button) button.disabled = state.isBusy;
}

function renderChatSidebar(snapshot) {
  if (!document.getElementById("chat-log")) renderChatShell();
  const quickActions = document.getElementById("chat-quick-actions");
  quickActions.innerHTML = renderActionButtons(collectQuickActions(snapshot), "button--chip");
  wireActionButtons(quickActions);
  renderChatLog();
}

function renderBlockingIntakeOverlay(snapshot) {
  document.getElementById("intake-overlay")?.remove();
  if (isIntakeReady(snapshot)) return;

  const shell = document.querySelector(".app-shell");
  if (!shell) return;

  const dialog = intakeDialogState(snapshot);
  const question = activeIntakeQuestion(snapshot);
  const context = dialog.context || {};
  const transcript = state.chatHistory.slice(-10);
  const contract = snapshot.intake?.user_rules_contract || {};
  const confirmationMode = Boolean(dialog.completed || (snapshot.setup_summary?.intake_dialog_completed && !snapshot.setup_summary?.intake_confirmed));
  const overlay = document.createElement("section");
  overlay.id = "intake-overlay";
  overlay.className = "intake-overlay";
  overlay.innerHTML = `
    <div class="intake-overlay__backdrop"></div>
    <div class="intake-overlay__panel">
      <div class="intake-overlay__head">
        <div>
          <span class="panel-kicker">Обязательный Intake</span>
          <h2>${escapeHtml(confirmationMode ? "Подтвердите итоговые правила перед запуском поиска" : "Сначала короткий диалог о ваших требованиях, потом поиск вакансий")}</h2>
          <p class="panel-lead">${escapeHtml(confirmationMode ? "Агент собрал структурные правила кандидата. Проверьте краткую сводку и подтвердите её. До подтверждения поиск и оценка вакансий заблокированы." : "Агент уже вытащил базу из hh-резюме и теперь задает только недостающие вопросы. Пока этот диалог не завершен, поиск и оценка вакансий заблокированы.")}</p>
        </div>
        <div class="intake-overlay__progress">
          <strong>${escapeHtml(confirmationMode ? "Остался шаг подтверждения" : intakePriorityLabel(question))}</strong>
          <span class="muted">${escapeHtml(confirmationMode ? "После подтверждения откроются поиск и анализ" : question ? `Вопрос ${Number(dialog.step_index || 0) + 1} из ${(dialog.questions || []).length}` : "Нажмите «Начать опрос»")}</span>
        </div>
      </div>
      <div class="intake-overlay__meta">
        <article class="note">
          <strong>Что уже понял из резюме</strong>
          <p>${escapeHtml(context.resume_title || snapshot.selected_resume_title || "Резюме пока не выбрано.")}</p>
          <p>${escapeHtml((context.inferred_roles || []).length ? `Роли: ${context.inferred_roles.join(", ")}` : "Роли из резюме пока не выделены.")}</p>
          <p>${escapeHtml((context.detected_skills || []).length ? `Навыки: ${context.detected_skills.slice(0, 8).join(", ")}` : "Навыки из резюме пока не выделены.")}</p>
        </article>
        <article class="note">
          <strong>Что еще нужно уточнить</strong>
          <p>${escapeHtml((snapshot.setup_summary?.intake_missing || []).length ? (snapshot.setup_summary.intake_missing || []).join(", ") : "Критичные пробелы закрыты. Осталось добрать уточнения и завершить диалог.")}</p>
        </article>
      </div>
      <div class="intake-overlay__body">
        <div id="intake-overlay-log" class="intake-overlay__log">
          ${
            confirmationMode
              ? `
                <article class="note"><strong>Целевые роли</strong><p>${escapeHtml((contract.search_targets?.primary_roles || []).join(", ") || "Не заполнено")}</p></article>
                <article class="note"><strong>Жесткие ограничения</strong><p>${escapeHtml([
                  contract.hard_constraints?.work_format === "remote_only" ? "только remote" : "",
                  ...(contract.hard_constraints?.exclude_company_types || []),
                  ...(contract.hard_constraints?.exclude_vacancy_signals || []),
                ].filter(Boolean).join(", ") || "Не заполнено")}</p></article>
                <article class="note"><strong>Must-have стек</strong><p>${escapeHtml((contract.search_targets?.must_have_keywords || []).join(", ") || "Не заполнено")}</p></article>
                <article class="note"><strong>Сопроводительное</strong><p>${escapeHtml(`Язык: ${contract.cover_letter_policy?.language || "ru"}, тон: ${contract.cover_letter_policy?.tone || "деловой"}`)}</p></article>
              `
              : renderList(
                  transcript,
                  (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "агент" : "вы")}</span><p>${escapeHtml(item.text)}</p></article>`,
                  "Диалог еще не начат.",
                )
          }
        </div>
        <aside class="intake-overlay__question">
          <strong>${escapeHtml(confirmationMode ? "Сводка правил собрана" : question?.title || "Нажмите «Начать опрос», чтобы перейти к первому вопросу.")}</strong>
          <p>${escapeHtml(confirmationMode ? "Если сводка в целом верна, подтвердите правила. Если что-то не так, перезапустите диалог и поправьте ответы." : question?.hint || "Сначала фиксируем обязательные критерии, потом важные предпочтения и в конце необязательные нюансы.")}</p>
          <p class="muted">${escapeHtml(confirmationMode ? "После подтверждения эти правила станут источником для поиска, оценки вакансий, сопроводительных и анкет." : question?.example || "Можно отвечать свободным текстом. Если текущее понимание подходит, напишите «оставить как есть».")}</p>
        </aside>
      </div>
      <form id="intake-overlay-form" class="intake-overlay__form">
        ${confirmationMode ? "" : '<textarea id="intake-overlay-input" rows="6" placeholder="Ответьте свободным текстом. Например: только remote, не хочу госуху и университеты, роли LLM Engineer/NLP Engineer, зарплата от 350k."></textarea>'}
        <div class="inline-actions">
          <button class="button" type="button" data-dashboard-action="start-intake">${escapeHtml(dialog.active ? "Начать заново" : "Начать опрос")}</button>
          ${
            confirmationMode
              ? '<button class="button button--primary" type="button" data-dashboard-action="confirm-intake">Подтвердить и открыть поиск</button>'
              : `<button class="button button--primary" type="submit">${escapeHtml(dialog.active ? "Отправить ответ" : "Начать диалог")}</button>`
          }
        </div>
      </form>
    </div>
  `;
  shell.appendChild(overlay);
  wireActionButtons(overlay);
  overlay.querySelector("#intake-overlay-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (confirmationMode) return;
    const input = overlay.querySelector("#intake-overlay-input");
    const value = (input?.value || "").trim();
    if (!dialog.active && !value) {
      await sendChatCommand("начать опрос");
      return;
    }
    if (!value || state.isBusy) return;
    await sendChatCommand(value);
    if (input) input.value = "";
  });
}

function renderLlmGateOverlay(snapshot) {
  document.getElementById("llm-gate-overlay")?.remove();
  const gate = snapshot?.llm_gate || {};
  if (!gate.active) return;
  const shell = document.querySelector(".app-shell");
  if (!shell) return;
  const overlay = document.createElement("section");
  overlay.id = "llm-gate-overlay";
  overlay.className = "llm-gate-overlay";
  overlay.innerHTML = `
    <div class="intake-overlay__backdrop"></div>
    <div class="llm-gate-overlay__panel">
      <span class="panel-kicker">LLM недоступна</span>
      <h2>${escapeHtml(gate.title || "Не удалось обратиться к модели")}</h2>
      <p class="panel-lead">${escapeHtml(gate.message || "На этом этапе нужен агентный разбор через OpenRouter. Сейчас модель недоступна.")}</p>
      <div class="note">
        <strong>Что делать дальше</strong>
        <p>Можно либо продолжить на эвристиках как временный режим, либо ничего не запускать и дождаться, пока LLM снова станет доступна.</p>
      </div>
      <div class="inline-actions">
        <button class="button button--primary" type="button" data-dashboard-action="llm-fallback-heuristics">Продолжить на эвристиках</button>
        <button class="button" type="button" data-dashboard-action="llm-wait">Ждать LLM</button>
      </div>
    </div>
  `;
  shell.appendChild(overlay);
  wireActionButtons(overlay);
}

function focusChatInput(promptText = "") {
  setActiveTab("agent", { userInitiated: true, pauseMs: 8000 });
  const input = document.getElementById("chat-input");
  if (!input) return;
  if (promptText) input.value = promptText;
  input.focus();
}

async function sendChatCommand(message) {
  if (!message || state.isBusy) return;
  const input = document.getElementById("chat-input");
  pauseAutoRefresh(12000);
  state.chatHistory.push({ role: "user", text: message });
  state.isBusy = true;
  state.pendingActionMessage = "Отправляю сообщение агенту.";
  renderChatLog();
  if (state.snapshot) renderAgentView(state.snapshot);
  try {
    const result = await postJson("/api/chat", { message, selected_vacancy_id: state.selectedVacancyId || "" });
    if (input) input.value = "";
    if (result.result?.message) state.chatHistory.push({ role: "assistant", text: result.result.message });
    if (result.snapshot) renderSnapshot(result.snapshot);
  } catch (error) {
    state.chatHistory.push({ role: "assistant", text: `Ошибка: ${error.message}` });
  } finally {
    state.isBusy = false;
    state.pendingActionMessage = "";
    renderChatLog();
    if (state.snapshot) renderAgentView(state.snapshot);
  }
}

function pickResultMessage(result) {
  return (
    result?.result?.payload?.result?.message ||
    result?.result?.payload?.message ||
    result?.result?.message ||
    result?.message ||
    ""
  );
}

async function handleServerAction(url, payload = {}, onSuccess) {
  state.pendingActionMessage = payload?.vacancy_id
    ? "Обновляю действия по вакансии."
    : (url.includes("/resume")
      ? "Обновляю профиль и черновик резюме."
      : (url.includes("/analyze")
        ? "Запускаю анализ и разбор вакансий."
        : (url.includes("/apply-batch")
          ? `Запускаю пакетную отправку откликов по колонке ${categoryLabel(payload?.category || "")}.`
          : "Выполняю действие.")));
  if (state.snapshot) renderAgentView(state.snapshot);
  try {
    const result = await postJson(url, payload);
    const message = pickResultMessage(result);
    if (message) state.chatHistory.push({ role: "assistant", text: message });
    renderChatLog();
    if (result.snapshot) renderSnapshot(result.snapshot);
    if (onSuccess) onSuccess(result);
  } catch (error) {
    state.chatHistory.push({ role: "assistant", text: `Ошибка: ${error.message}` });
    renderChatLog();
  }
}

async function runDashboardAction(actionId, chatPrompt = "") {
  if (!actionId) return;
  const intakeBlockedActions = new Set(["resume-sync", "build-rules", "plan-filters", "analyze", "apply-plan", "open-vacancies", "open-vacancy"]);
  if (actionId === "focus-chat") {
    focusChatInput(chatPrompt);
    return;
  }
  if (actionId === "start-intake") {
    await sendChatCommand("?????? ?????");
    return;
  }
  if (actionId === "open-vacancies") {
    setActiveTab("vacancies", { userInitiated: true });
    return;
  }
  if (actionId === "open-vacancy") {
    setActiveTab("vacancy", { userInitiated: true });
    return;
  }
  if (state.snapshot && !isIntakeReady(state.snapshot) && intakeBlockedActions.has(actionId)) {
    appendAssistantMessage(
      "Сначала нужно завершить обязательный диалог о предпочтениях кандидата. После этого я открою поиск, анализ и отклики.",
      `intake-block:${actionId}`,
    );
    renderChatLog();
    setActiveTab("agent", { userInitiated: true });
    return;
  }

  const routes = {
    "hh-login": ["/api/actions/hh-login", {}],
    "hh-resumes": ["/api/actions/hh-resumes", {}],
    "confirm-intake": ["/api/actions/confirm-intake", {}],
    "llm-fallback-heuristics": ["/api/actions/llm-fallback-heuristics", { stage: state.snapshot?.llm_gate?.stage || "resume_intake" }],
    "llm-wait": ["/api/actions/llm-wait", { stage: state.snapshot?.llm_gate?.stage || "resume_intake" }],
    "resume-sync": ["/api/actions/resume", {}],
    "build-rules": ["/api/actions/build-rules", {}],
    "plan-filters": ["/api/actions/plan-filters", {}],
    analyze: ["/api/actions/analyze", { limit: 120 }],
    "apply-plan": ["/api/actions/apply-plan", { vacancy_id: state.selectedVacancyId || "" }],
  };
  const route = routes[actionId];
  if (!route) return;
  await handleServerAction(route[0], route[1]);
}


function wireActionButtons(root) {
  root.querySelectorAll("[data-dashboard-action]").forEach((node) =>
    node.addEventListener("click", async () => {
      const actionId = node.getAttribute("data-dashboard-action") || "";
      const chatPrompt = node.getAttribute("data-chat-prompt") || "";
      await runDashboardAction(actionId, chatPrompt);
    }),
  );
}

function renderVacancies(snapshot) {
  const root = document.getElementById("vacancies-view");
  const applyLimits = snapshot.apply_limits || { daily_limit: 200, used_today: 0, remaining_today: 200 };
  const applyBatchJob = snapshot.apply_batch_job || {};
  if (!isIntakeReady(snapshot)) {
    root.innerHTML = `<section class="panel"><div class="note"><strong>??????? ???????????? intake</strong><p>${escapeHtml("????? ????????, ????? ? ???????? ??????? ??????????? ?????? ????? ?????????? ??????? ? ???????. ??????? ????????? ???????????? intake ? ?????? ??????.")}</p></div></section>`;
    return;
  }
  if ((snapshot.counts?.assessed || 0) <= 0) {
    root.innerHTML = `<section class="panel"><div class="note"><strong>?????? ??? ?? ?????????</strong><p>${escapeHtml(snapshot.analysis_job?.message || "??????? ???????? ????? ????????? ? ????????? ??????.")}</p></div><div class="note"><strong>???????? ????????</strong><p>${escapeHtml(snapshot.setup_summary?.live_refresh_message || "???????? hh.ru ??? ?? ?????.")}</p></div></section>`;
    return;
  }
  root.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div>
          <span class="panel-kicker">????? ????????????</span>
          <h2>?????? ???????? ?? ???? ????????</h2>
        </div>
      </div>
      <div class="stack compact board-summary">
        <div class="note"><strong>?????? ???????</strong><p>${escapeHtml(snapshot.analysis_job?.message || "?????????? ?????? ? ?????????.")}</p></div>
        <div class="note"><strong>????????</strong><p>${escapeHtml(snapshot.setup_summary?.live_refresh_message || "???????? ?? ?????????.")}</p></div>
        <div class="note"><strong>????? ????????</strong><p>${escapeHtml(`??????? ???????????? ${applyLimits.used_today || 0} ?? ${applyLimits.daily_limit || 200}, ???????? ${applyLimits.remaining_today || 0}.`)}</p></div>
      </div>
      <div class="board">
        ${Object.entries(categoryMeta)
          .map(
            ([key, meta]) => `
              <section class="lane ${meta.className}">
                <div class="lane-head">
                  <div>
                    <h3>${escapeHtml(meta.label)}</h3>
                    <p class="muted">${escapeHtml(meta.hint)}</p>
                  </div>
                  <div class="lane-head-actions">
                    <span class="lane-count">${escapeHtml((snapshot.columns?.[key] || []).length)}</span>
                    <button class="button button--ghost button--compact" type="button" ${
                      applyBatchJob.running ? "disabled" : `data-apply-batch="${escapeHtml(key)}"`
                    }>${
                      applyBatchJob.running && applyBatchJob.category === key ? "???? ????????" : "???????????? ?? ????"
                    }</button>
                  </div>
                </div>
                <div class="lane-stack">
                  ${renderList(
                    snapshot.columns?.[key] || [],
                    (card) => `
                      <article class="vacancy-card vacancy-card--${escapeHtml(card.category)} ${state.selectedVacancyId === card.id ? "is-active" : ""}" data-open-vacancy="${escapeHtml(card.id)}">
                        <div class="vacancy-card-top">
                          <div>
                            <strong>${escapeHtml(card.title)}</strong>
                            <div class="vacancy-meta">${escapeHtml(card.company || "???????? ?? ???????")} ? ${escapeHtml(card.location || "??????? ?? ???????")}</div>
                          </div>
                          <span class="score">${escapeHtml(card.score)}</span>
                        </div>
                        <p>${escapeHtml(card.reason_summary || "??????? ?????? ?? ?????????.")}</p>
                      </article>
                    `,
                    "???? ??? ????????.",
                  )}
                </div>
              </section>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
  root.querySelectorAll("[data-open-vacancy]").forEach((node) =>
    node.addEventListener("click", () => {
      pauseAutoRefresh(8000);
      state.selectedVacancyId = node.getAttribute("data-open-vacancy") || "";
      state.userSelectedTab = true;
      rememberScrollState();
      state.activeTab = "vacancy";
      renderTabbar();
      renderVacancyDetail(state.snapshot);
      updateVisibleTab();
      restoreScrollState();
    }),
  );
  root.querySelectorAll("[data-apply-batch]").forEach((node) =>
    node.addEventListener("click", async (event) => {
      event.stopPropagation();
      const category = event.currentTarget?.getAttribute("data-apply-batch") || "";
      if (applyBatchJob.running) {
        appendAssistantMessage(
          `Сейчас уже идет пакетный отклик по колонке ${categoryLabel(applyBatchJob.category || "")}. Дождитесь завершения текущего запуска.`,
          `apply-batch-running:${applyBatchJob.category || "unknown"}`,
        );
        renderChatLog();
        return;
      }
      pauseAutoRefresh(15000);
      appendAssistantMessage(`Запускаю пакетный отклик по колонке ${categoryLabel(category)}.`, `apply-batch-start:${category}`);
      renderChatLog();
      await handleServerAction("/api/actions/apply-batch", { category });
    }),
  );
}


function detailMetaRows(card, snapshot) {
  const rows = [
    ["Компания", card.company || "не указана"],
    ["Локация", card.location || "не указана"],
    ["Категория", card.category_label || card.category || "не указана"],
    ["Счёт", String(card.score)],
  ];
  return rows.map(([label, value]) => `<div class="meta-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
}

function renderReasonColumns(card) {
  const groups = { positive: [], neutral: [], negative: [] };
  (card.reasons || []).forEach((reason) => {
    const key = groups[reason.group] ? reason.group : "neutral";
    groups[key].push(reason);
  });
  return `
    <div class="reason-matrix">
      ${Object.entries(reasonMeta)
        .map(
          ([key, meta]) => `
            <section class="reason-lane ${meta.className}">
              <div class="lane-head">
                <h3>${escapeHtml(meta.label)}</h3>
                <span class="lane-count">${escapeHtml(groups[key].length)}</span>
              </div>
              <div class="lane-stack">
                ${renderList(
                  groups[key],
                  (reason) => `<article class="reason-card"><strong>${escapeHtml(reason.label || reason.code || "Причина")}</strong><p>${escapeHtml(reason.detail || "")}</p></article>`,
                  "Пусто.",
                )}
              </div>
            </section>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderVacancyDetail(snapshot) {
  const root = document.getElementById("vacancy-view");
  if (!isIntakeReady(snapshot)) {
    root.innerHTML = `<section class="panel"><div class="empty-state">Сначала завершите обязательный intake-диалог, затем откроются карточки вакансий и детальный разбор.</div></section>`;
    return;
  }
  const card = currentCard(snapshot);
  if (!card) {
    root.innerHTML = `<section class="panel"><div class="empty-state">Выбери вакансию из списка, чтобы увидеть детальный разбор и действия.</div></section>`;
    return;
  }
  const applyPlan = snapshot.apply_plan?.vacancy?.vacancy_id === card.id ? snapshot.apply_plan : null;
  const coverLetter = card.cover_letter_draft || applyPlan?.cover_letter_preview || "";
  const feedback = card.user_feedback || {};
  const descriptionOpen = Boolean(state.openDetails[card.id]);
  const allIds = listBoardVacancyIds(snapshot);
  const currentIndex = Math.max(0, allIds.indexOf(card.id));
  const prevId = currentIndex > 0 ? allIds[currentIndex - 1] : "";
  const nextId = nextVacancyId(snapshot, card.id);
  root.innerHTML = `
    <div class="vacancy-detail-grid">
      <section class="panel panel--detail-main">
        <div class="panel-head panel-head--vacancy">
          <div>
            <span class="panel-kicker">Детальный разбор</span>
            <h2>${escapeHtml(card.title)}</h2>
            <p class="panel-lead panel-lead--compact">${escapeHtml((snapshot.selected_resume_title || snapshot.selected_resume_id || "Резюме не выбрано") + " · " + formatDate(snapshot.generated_at))}</p>
          </div>
          <div class="detail-top-actions">
            <div class="inline-actions">
              <button class="button button--ghost" type="button" id="open-prev-vacancy" ${prevId ? "" : "disabled"}>${escapeHtml(prevId ? "Предыдущая вакансия" : "Нет предыдущей")}</button>
              <button class="button button--ghost" type="button" id="open-next-vacancy-top" ${nextId && nextId !== card.id ? "" : "disabled"}>${escapeHtml(nextId && nextId !== card.id ? "Следующая вакансия" : "Нет следующей")}</button>
            </div>
            <div class="cta-grid cta-grid--decision-top">
              ${Object.entries(decisionMeta)
                .map(
                  ([key, meta]) => `
                    <button class="button ${escapeHtml(meta.className)} ${feedback.decision === key ? "is-active" : ""}" type="button" data-feedback="${escapeHtml(key)}">
                      ${escapeHtml(meta.label)}
                    </button>
                  `,
                )
                .join("")}
            </div>
            <a class="button button--ghost" href="${escapeHtml(card.url || "#")}" target="_blank" rel="noreferrer">Открыть на hh.ru</a>
          </div>
        </div>
        <div class="detail-meta-grid">${detailMetaRows(card, snapshot)}</div>
        <div class="note note--decision"><strong>Почему принято такое решение</strong><p>${escapeHtml(card.explanation || "Пояснение не сохранено.")}</p></div>
        <div class="detail-block">
          <strong>Трехколоночный разбор соответствия</strong>
          ${renderReasonColumns(card)}
        </div>
        <details class="details-box vacancy-description" ${descriptionOpen ? "open" : ""}>
          <summary>Описание вакансии</summary>
          <div class="description-text">${escapeHtml(card.description || card.summary || "Описание вакансии не сохранено.")}</div>
        </details>
      </section>
      <aside class="panel panel--detail-side">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Отклик</span>
            <h2>Действия по вакансии</h2>
          </div>
        </div>
        <div class="note"><strong>Текущее решение</strong><p>${escapeHtml(card.category_label || "ещё не выбрано")}</p><p class="muted">${escapeHtml(feedback.decided_at ? formatDate(feedback.decided_at) : "автоматическая оценка")}</p></div>
        <div class="field">
          <label for="cover-letter-input">Сопроводительное письмо</label>
          <textarea id="cover-letter-input" rows="12" placeholder="Здесь можно отредактировать письмо перед откликом">${escapeHtml(coverLetter)}</textarea>
        </div>
        <div class="cta-stack">
          <button class="button button--ghost" id="save-cover-letter">Сохранить письмо</button>
          <button class="button button--ghost" id="build-apply-plan">Собрать план отклика</button>
          <button class="button button--primary" id="apply-submit">Откликнуться</button>
        </div>
        <div class="note"><strong>Статус отклика</strong><p>${escapeHtml(feedback.last_apply_message || "Отклик еще не запускался.")}</p><p class="muted">${escapeHtml(feedback.last_apply_at ? formatDate(feedback.last_apply_at) : "нет запуска")}</p></div>
        <button class="button button--ghost" type="button" id="open-next-vacancy">${escapeHtml(nextId && nextId !== card.id ? "Следующая карточка" : "Остаться на этой карточке")}</button>
      </aside>
    </div>
  `;
  root.querySelectorAll("[data-feedback]").forEach((node) =>
    node.addEventListener("click", async () => {
      pauseAutoRefresh(12000);
      const decision = node.getAttribute("data-feedback") || "";
      const nextAfterMove = nextVacancyId(snapshot, card.id);
      await handleServerAction("/api/actions/vacancy-feedback", { vacancy_id: card.id, decision }, () => {
        state.selectedVacancyId = nextAfterMove || state.selectedVacancyId;
        setActiveTab("vacancy", { userInitiated: true, pauseMs: 4000 });
        renderVacancyDetail(state.snapshot);
      });
    }),
  );
  document.getElementById("save-cover-letter")?.addEventListener("click", async () => {
    pauseAutoRefresh(12000);
    const text = document.getElementById("cover-letter-input")?.value || "";
    await handleServerAction("/api/actions/save-cover-letter", { vacancy_id: card.id, cover_letter: text });
  });
  document.getElementById("build-apply-plan")?.addEventListener("click", async () => {
    pauseAutoRefresh(12000);
    const text = document.getElementById("cover-letter-input")?.value || "";
    await handleServerAction("/api/actions/save-cover-letter", { vacancy_id: card.id, cover_letter: text });
    await handleServerAction("/api/actions/apply-plan", { vacancy_id: card.id });
  });
  document.getElementById("apply-submit")?.addEventListener("click", async () => {
    pauseAutoRefresh(15000);
    const text = document.getElementById("cover-letter-input")?.value || "";
    await handleServerAction("/api/actions/apply-submit", { vacancy_id: card.id, cover_letter: text });
  });
  root.querySelector(".vacancy-description")?.addEventListener("toggle", (event) => {
    pauseAutoRefresh(5000);
    state.openDetails[card.id] = Boolean(event.currentTarget?.open);
  });
  document.getElementById("open-prev-vacancy")?.addEventListener("click", () => {
    if (!prevId) return;
    pauseAutoRefresh(5000);
    state.selectedVacancyId = prevId;
    renderVacancyDetail(state.snapshot);
  });
  document.getElementById("open-next-vacancy-top")?.addEventListener("click", () => {
    if (!nextId || nextId === card.id) return;
    pauseAutoRefresh(5000);
    state.selectedVacancyId = nextId;
    renderVacancyDetail(state.snapshot);
  });
  document.getElementById("open-next-vacancy")?.addEventListener("click", () => {
    if (!nextId || nextId === card.id) return;
    pauseAutoRefresh(5000);
    state.selectedVacancyId = nextId;
    renderVacancyDetail(state.snapshot);
  });
}

function freshnessCards(snapshot) {
  const rows = [
    ["Вход в hh.ru", snapshot.freshness?.timestamps?.hh_login_at, snapshot.freshness?.stale?.hh_login_at],
    ["Каталог резюме", snapshot.freshness?.timestamps?.resume_catalog_at, snapshot.freshness?.stale?.resume_catalog_at],
    ["Анкета", snapshot.freshness?.timestamps?.intake_at, snapshot.freshness?.stale?.intake_at],
    ["Правила", snapshot.freshness?.timestamps?.rules_at, snapshot.freshness?.stale?.rules_at],
    ["Анализ", snapshot.freshness?.timestamps?.analysis_at, snapshot.freshness?.stale?.analysis_at],
  ];
  return rows
    .map(([label, value, stale]) => `<div class="note"><strong>${escapeHtml(label)}</strong><p>${escapeHtml(value ? formatDate(value) : "нет данных")}</p><p class="muted">${escapeHtml(stale ? "данные устарели" : "данные актуальны")}</p></div>`)
    .join("");
}

function renderActivity(snapshot) {
  const root = document.getElementById("activity-view");
  root.innerHTML = `
    <div class="activity-grid">
      <section class="panel">
        <div class="panel-head"><div><span class="panel-kicker">Свежесть данных</span><h2>Когда что обновлялось</h2></div></div>
        <div class="stack">${freshnessCards(snapshot)}</div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><span class="panel-kicker">События</span><h2>Последние действия</h2></div></div>
        <div class="stack">
          ${renderList(snapshot.recent_events || [], (event) => `<article class="history-card"><strong>${escapeHtml(event.kind || "событие")}</strong><p>${escapeHtml(event.message || "")}</p><p class="muted">${escapeHtml(formatDate(event.timestamp))}</p></article>`, "Событий пока нет.")}
        </div>
      </section>
      <section class="panel panel--wide">
        <div class="panel-head"><div><span class="panel-kicker">Запуски</span><h2>История прогонов</h2></div></div>
        <div class="stack">
          ${renderList(snapshot.recent_runs || [], (run) => `<article class="history-card"><strong>${escapeHtml(run.run_id || "run")}</strong><p>${escapeHtml(run.mode || "")} · ${escapeHtml(run.status || "")}</p><p class="muted">${escapeHtml(formatDate(run.started_at))}</p></article>`, "Запусков пока нет.")}
        </div>
      </section>
    </div>
  `;
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  rememberScrollState();
  const previousSnapshot = state.snapshot;
  state.snapshot = snapshot;
  document.body.classList.toggle("intake-blocking", !isIntakeReady(snapshot));
  document.querySelector(".dashboard-shell")?.classList.toggle("dashboard-shell--intake", !isIntakeReady(snapshot));
  ensureSelectedVacancy(snapshot);
  if (!state.userSelectedTab) {
    if (snapshot.analysis_job?.running) state.activeTab = "agent";
    else if (!previousSnapshot) state.activeTab = preferredTab(snapshot);
  }
  announceSnapshotChanges(snapshot, previousSnapshot);
  renderHero(snapshot);
  renderStatusStrip(snapshot);
  renderTabbar();
  renderChatSidebar(snapshot);
  renderAgentView(snapshot);
  renderVacancies(snapshot);
  renderVacancyDetail(snapshot);
  renderActivity(snapshot);
  renderBlockingIntakeOverlay(snapshot);
  renderLlmGateOverlay(snapshot);
  updateVisibleTab();
  restoreScrollState();
}

async function refresh() {
  if (shouldSkipRefresh()) return;
  try {
    state.refreshInFlight = true;
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    renderSnapshot(await response.json());
  } catch (error) {
    const root = document.getElementById("agent-view");
    if (root) root.innerHTML = `<section class="panel"><div class="empty-state">Не удалось загрузить дашборд: ${escapeHtml(error.message)}</div></section>`;
  } finally {
    state.refreshInFlight = false;
  }
}

window.addEventListener("error", (event) => {
  void sendClientLog("window-error", {
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
  });
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason && typeof event.reason === "object" ? JSON.stringify(event.reason) : String(event.reason || "");
  void sendClientLog("window-unhandledrejection", { reason });
});

function initLayoutResizer() {
  const shell = document.querySelector(".dashboard-shell");
  const resizer = document.getElementById("layout-resizer");
  if (!shell || !resizer) return;

  const storedWidth = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
  if (storedWidth) applySidebarWidth(shell, storedWidth);

  let startX = 0;
  let startWidth = 0;

  const onMove = (event) => {
    pauseAutoRefresh(3000);
    const delta = startX - event.clientX;
    applySidebarWidth(shell, startWidth + delta);
  };

  const onUp = () => {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    document.body.style.userSelect = "";
  };

  resizer.addEventListener("mousedown", (event) => {
    pauseAutoRefresh(5000);
    startX = event.clientX;
    startWidth = document.getElementById("chat-sidebar")?.getBoundingClientRect().width || 384;
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  window.addEventListener("resize", () => {
    const currentWidth = document.getElementById("chat-sidebar")?.getBoundingClientRect().width || storedWidth || 384;
    applySidebarWidth(shell, currentWidth);
  });
}

function initInteractionGuards() {
  document.addEventListener(
    "mousedown",
    (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("button, a, summary, [data-open-vacancy], [data-dashboard-action], .tab-button")) {
        pauseAutoRefresh(8000);
      }
    },
    true,
  );

  document.addEventListener(
    "click",
    (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("button, a, summary, [data-open-vacancy], [data-dashboard-action], .tab-button")) {
        pauseAutoRefresh(5000);
      }
    },
    true,
  );

  document.addEventListener(
    "focusin",
    (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.matches("textarea, input")) pauseAutoRefresh(15000);
    },
    true,
  );

  document.querySelector(".workspace")?.addEventListener(
    "scroll",
    () => {
      state.workspaceScrollTopByTab[state.activeTab] = currentWorkspace()?.scrollTop || 0;
    },
    { passive: true },
  );

  document.getElementById("chat-sidebar")?.addEventListener(
    "scroll",
    () => {
      pauseAutoRefresh(1500);
    },
    { passive: true },
  );
}

renderChatShell();
initLayoutResizer();
initInteractionGuards();
void refresh();
setInterval(() => {
  void refresh();
}, 5000);
