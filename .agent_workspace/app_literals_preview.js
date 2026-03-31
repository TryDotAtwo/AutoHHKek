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
  fit: { label: "РџРѕРґС…РѕРґРёС‚", className: "button--fit" },
  doubt: { label: "РЎРѕРјРЅРµРІР°СЋСЃСЊ", className: "button--doubt" },
  no_fit: { label: "РќРµ РїРѕРґС…РѕРґРёС‚", className: "button--no-fit" },
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
  return repairText(String(value ?? ""))
    .replaceAll("&nbsp;", " ")
    .replace(/\u00a0/g, " ")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function tryDecodeMojibake(value) {
  if (!value || !/[Р РЎРЃГ‘Гђ]/.test(value)) return value;
  try {
    const bytes = Uint8Array.from(Array.from(value, (char) => char.charCodeAt(0) & 0xff));
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  } catch {
    return value;
  }
}

function looksBetter(original, candidate) {
  if (!candidate || candidate === original) return false;
  const brokenBefore = (original.match(/[Р РЎРЃГ‘Гђ]/g) || []).length + (original.match(/\?{3,}/g) || []).length * 4;
  const brokenAfter = (candidate.match(/[Р РЎРЃГ‘Гђ]/g) || []).length + (candidate.match(/\?{3,}/g) || []).length * 4;
  return brokenAfter < brokenBefore;
}

function repairText(value) {
  let current = String(value ?? "");
  for (let step = 0; step < 2; step += 1) {
    const decoded = tryDecodeMojibake(current);
    if (!looksBetter(current, decoded)) break;
    current = decoded;
  }
  return current;
}

function repairRenderedText(root) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  textNodes.forEach((node) => {
    const repaired = repairText(node.nodeValue || "");
    if (repaired !== node.nodeValue) node.nodeValue = repaired;
  });
  root.querySelectorAll("[placeholder],[title],[aria-label]").forEach((node) => {
    ["placeholder", "title", "aria-label"].forEach((name) => {
      const value = node.getAttribute(name);
      if (!value) return;
      const repaired = repairText(value);
      if (repaired !== value) node.setAttribute(name, repaired);
    });
  });
}

function formatDate(value) {
  if (!value) return "РЅРµС‚ РґР°РЅРЅС‹С…";
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
  if (importance === "critical") return "РЎРµР№С‡Р°СЃ С„РёРєСЃРёСЂСѓРµРј РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Рµ РєСЂРёС‚РµСЂРёРё";
  if (importance === "important") return "РўРµРїРµСЂСЊ СѓС‚РѕС‡РЅСЏРµРј РІР°Р¶РЅС‹Рµ РїСЂРµРґРїРѕС‡С‚РµРЅРёСЏ";
  return "Р’ РєРѕРЅС†Рµ РґРѕР±РёСЂР°РµРј С‚РѕРЅРєРёРµ РїРѕР¶РµР»Р°РЅРёСЏ";
}

function categoryLabel(category) {
  return categoryMeta[category]?.label || category || "РЅРµРёР·РІРµСЃС‚РЅРѕ";
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
      return { label: "Р“РѕС‚РѕРІРѕ", className: "pipeline-pill--completed" };
    case "active":
      return { label: "РЎРµР№С‡Р°СЃ", className: "pipeline-pill--active" };
    case "blocked":
      return { label: "Р‘Р»РѕРєРµСЂ", className: "pipeline-pill--blocked" };
    default:
      return { label: "Р”Р°Р»РµРµ", className: "pipeline-pill--pending" };
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
      summary: loginRunning ? "??????? ???? hh.ru ??? ????? ? ??????????? ?????." : hasLogin ? "?????? hh.ru ??? ?????????." : "????? ??????? hh.ru ? ?????? ???? ???????.",
      detail: snapshot.hh_login?.message || "???????????? ??? ???????? ???? ? ?????, ????? ????? ???????? ???????????.",
      action: hasLogin && !loginRunning ? null : { id: "hh-login", label: "??????? hh.ru" },
    },
    {
      id: "resume",
      title: "2. ????? ??????",
      status: !hasLogin ? "blocked" : selectedResume ? "completed" : "active",
      summary: !hasLogin
        ? "???? ??? ????? ? hh.ru, ?????? ??????????."
        : selectedResume
          ? `??? ?????? ??????? ??????: ${snapshot.selected_resume_title || snapshot.selected_resume_id}.`
          : multipleResumes
            ? "?? hh.ru ??????? ????????? ??????. ????? ??????? ???? ??? ??????."
            : hhResumes.length
              ? "?????? ???????, ??? ????? ??????? ??? live search."
              : "????? ???????? ?????? ?????? ? hh.ru.",
      detail: snapshot.setup_summary?.live_refresh_message || "????? ?????? ?????? ??? ?????? ?????????? ??? ?????? ? ????????????? ???????.",
      action: !hasLogin ? null : selectedResume ? null : { id: "hh-resumes", label: "???????? ?????? ??????" },
    },
    {
      id: "intake",
      title: "3. ???????????? intake-??????",
      status: !selectedResume && multipleResumes ? "blocked" : intakeReady ? "completed" : "active",
      summary: intakeReady
        ? "???????????? ?????????, ??????????? ? ???????????????? ??????? ??? ????????????."
        : intakeDialogCompleted && !intakeConfirmed
          ? "????? ????????, ?? ??????? ??? ????? ???? ??????????? ????? ???????? ??????."
          : intakeDialogCompleted
            ? "?????? ????????, ?? ????????????? ?????? ??? ?? ?????????."
            : "??????? ????? ???????? ?????? ? ?????? ?????? ??????????? ??????? ? ?????? ??????.",
      detail: intakeReady
        ? "?????? ??? ??????? ????? ?????????????? ??? ??????, ?????? ????????, ????? ? ????????????????."
        : intakeDialogCompleted && !intakeConfirmed
          ? "????????? ???????? ?????? ?????? ????????? ? ??????????? ??. ?? ????????????? ????? ???????? ?? ???????????."
          : "??????? ??????????? ???????????? ????????, ????? ?????? ????????????, ? ? ????? ?????????????? ??????.",
      action: intakeDialogCompleted && !intakeConfirmed ? { id: "confirm-intake", label: "??????????? ???????" } : { id: "start-intake", label: intakeDialogCompleted ? "????????????? ?????" : "?????? ?????" },
    },
    {
      id: "profile",
      title: "4. ????????????? ??????? ? ??????",
      status: !intakeReady ? "blocked" : resumeDraftReady && profileSyncReady && rulesReady ? "completed" : "active",
      summary: resumeDraftReady && profileSyncReady && rulesReady
        ? "???????, ???????? ?????? ? ??????? ??? ????????????????."
        : "????? ???????? ???????, ???????? ? ??????????? ??????? ??????.",
      detail: intakeStructuredReady
        ? snapshot.profile_sync?.message || "????? ????????????? ??????? ? ??????? ????? ????????? ? ??????????? ?????????."
        : "???? ??? ????????? ????? ???????????? intake-???????.",
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
        ? "????? ?? ????????, ???? ?? ???????? ???????????? intake."
        : !selectedResume
          ? "????? ??????? ?????? ??? live search."
          : filterPlanReady && vacanciesLoaded
            ? hhTotal
              ? `?? hh.ru ??????? ${hhTotal} ????????, ? ????????? ??????? ${snapshot.counts?.total_vacancies || 0}.`
              : `??????? ???????, ? ????????? ??????? ${snapshot.counts?.total_vacancies || 0} ????????.`
            : filterPlanReady
              ? "??????? ??? ?????????, ???????? ???????? ??????? ????????."
              : "????? ????????? ??????? ? ?????? hh-?????? ??? ??????.",
      detail: snapshot.filter_plan?.search_text
        ? snapshot.setup_summary?.live_refresh_stats?.search_url
          ? `????????? ?????: ${snapshot.filter_plan.search_text}. ?????????? ?????? hh-?????? ? ???????? ??? ???????? ?? ?????.`
          : `????????? ?????: ${snapshot.filter_plan.search_text}.`
        : "??????? ????? ????????? ?? ?????? ?????? ????????? ? ?????????? ??????.",
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
        ? "?????? ????????? ????? ????????????? intake ? ????? ????????."
        : analyzing
          ? snapshot.analysis_job?.message || "???? ?????? ????????."
          : assessedCount > 0
            ? `????????? ${assessedCount} ????????: ${snapshot.counts?.fit || 0} / ${snapshot.counts?.doubt || 0} / ${snapshot.counts?.no_fit || 0}.`
            : "????? ???????? ???????? ????? ???????? ?? ?? ???? ????????.",
      detail: assessedCount > 0
        ? "????? ?????????? ?? ?????????, ?????????? ???????? ????? ????????? ? ????????? ???????."
        : "??????? = ????????, ?????? = ????? ????????, ??????? = ?? ????????.",
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
    return snapshot.apply_batch_job.message || "РРґРµС‚ РїР°РєРµС‚РЅР°СЏ РѕС‚РїСЂР°РІРєР° РѕС‚РєР»РёРєРѕРІ.";
  }
  if (snapshot.analysis_job?.running) {
    return snapshot.analysis_job.message || "РРґРµС‚ Р°РЅР°Р»РёР· Рё СЂР°Р·Р±РѕСЂ РІР°РєР°РЅСЃРёР№.";
  }
  if (snapshot.hh_login?.running) {
    return snapshot.hh_login.message || "РћС‚РєСЂС‹С‚ РІС…РѕРґ РІ hh.ru, РѕР¶РёРґР°СЋ Р°РІС‚РѕСЂРёР·Р°С†РёСЋ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.";
  }
  return snapshot.profile_sync?.message || snapshot.analysis_state?.stale_reason || "";
}

function collectQuickActions(snapshot) {
  const actions = [];
  const currentStep = currentPipelineStep(snapshot);
  if (currentStep?.action) actions.push(currentStep.action);
  if (!isIntakeReady(snapshot)) {
    actions.push({ id: snapshot.setup_summary?.intake_dialog_completed ? "confirm-intake" : "start-intake", label: snapshot.setup_summary?.intake_dialog_completed ? "??????????? ???????" : "?????? ?????" });
  }
  if (snapshot.hh_accounts?.length) {
    actions.push({ id: "hh-login", label: "?????? ???????" });
  }
  if (isIntakeReady(snapshot) && snapshot.selected_resume_id && snapshot.setup_summary?.rules_loaded && !snapshot.filter_plan?.search_text) {
    actions.push({ id: "plan-filters", label: "??????? ???????" });
  }
  if (isIntakeReady(snapshot) && snapshot.selected_resume_id && snapshot.filter_plan?.search_text && !snapshot.analysis_job?.running) {
    actions.push({ id: "analyze", label: "????????? ?????" });
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
    appendAssistantMessage(step?.summary || "Р“РѕС‚РѕРІ Рє СЃР»РµРґСѓСЋС‰РµРјСѓ С€Р°РіСѓ.", `initial:${step?.id || "step"}`);
    return;
  }

  if (!previousSnapshot.hh_login?.running && snapshot.hh_login?.running) {
    appendAssistantMessage("РћС‚РєСЂС‹Р» hh.ru. РџСЂРѕР№РґРёС‚Рµ РІС…РѕРґ Рё РєР°РїС‡Сѓ РІ Р±СЂР°СѓР·РµСЂРµ, СЏ РїРѕРґРѕР¶РґСѓ.", `login-running:${snapshot.hh_login?.started_at || "now"}`);
  }
  if (previousSnapshot.hh_login?.status !== snapshot.hh_login?.status && snapshot.hh_login?.status === "completed") {
    appendAssistantMessage("Р’С…РѕРґ РІ hh.ru Р·Р°РІРµСЂС€РµРЅ, РјРѕР¶РЅРѕ РїРµСЂРµС…РѕРґРёС‚СЊ Рє РІС‹Р±РѕСЂСѓ СЂРµР·СЋРјРµ.", `login-completed:${snapshot.hh_login?.finished_at || "done"}`);
  }
  if (previousSnapshot.selected_resume_id !== snapshot.selected_resume_id && snapshot.selected_resume_id) {
    appendAssistantMessage(`Р—Р°С„РёРєСЃРёСЂРѕРІР°Р» СЂРµР·СЋРјРµ РґР»СЏ РїРѕРёСЃРєР°: ${snapshot.selected_resume_title || snapshot.selected_resume_id}.`, `resume:${snapshot.selected_resume_id}`);
  }
  if (!previousSnapshot.analysis_job?.running && snapshot.analysis_job?.running) {
    appendAssistantMessage(snapshot.analysis_job?.message || "Р—Р°РїСѓСЃС‚РёР» Р°РЅР°Р»РёР· РІР°РєР°РЅСЃРёР№.", `analysis-running:${snapshot.analysis_job?.started_at || "run"}`);
  }
  if (previousSnapshot.analysis_job?.status !== snapshot.analysis_job?.status && snapshot.analysis_job?.status === "completed") {
    appendAssistantMessage(snapshot.analysis_job?.message || "РђРЅР°Р»РёР· РІР°РєР°РЅСЃРёР№ Р·Р°РІРµСЂС€РµРЅ.", `analysis-completed:${snapshot.analysis_job?.finished_at || "done"}`);
  }
  if (!previousSnapshot.pending_rule_edit?.markdown && snapshot.pending_rule_edit?.markdown) {
    appendAssistantMessage("РџРѕРґРіРѕС‚РѕРІРёР» С‡РµСЂРЅРѕРІРёРє РїСЂР°РІРєРё РїСЂР°РІРёР». РџСЂРѕРІРµСЂСЊС‚Рµ diff Рё РїРѕРґС‚РІРµСЂРґРёС‚Рµ РёР·РјРµРЅРµРЅРёРµ РІ С‡Р°С‚Рµ.", `rules-draft:${snapshot.pending_rule_edit.filename || "draft"}`);
  }
}

function renderHero(snapshot) {
  const mode = snapshot.runtime_settings?.dashboard_mode || "analyze";
  const backend = snapshot.runtime_settings?.llm_backend || "openai";
  const step = currentPipelineStep(snapshot);
  const selectedResume = snapshot.selected_resume_title || snapshot.selected_resume_id || "РЅРµ РІС‹Р±СЂР°РЅРѕ";
  document.getElementById("hero-summary").textContent =
    step?.summary || snapshot.next_recommended_action?.reason || "Р”Р°С€Р±РѕСЂРґ РїРѕРєР°Р·С‹РІР°РµС‚ С‚РµРєСѓС‰РµРµ СЃРѕСЃС‚РѕСЏРЅРёРµ РїРѕРёСЃРєР° Рё РѕС‡РµСЂРµРґСЊ РІР°РєР°РЅСЃРёР№.";
  document.getElementById("hero-next-action").textContent = step?.title || snapshot.next_recommended_action?.label || "РћР¶РёРґР°СЋ РґРµР№СЃС‚РІРёРµ";
  document.getElementById("hero-next-reason").textContent = `Р РµР¶РёРј: ${mode}. РњРѕРґРµР»СЊРЅС‹Р№ backend: ${backend}. Р РµР·СЋРјРµ: ${selectedResume}.`;
  document.getElementById("hero-runtime").textContent = `${backend} В· ${mode}`;
  document.getElementById("generated-at").textContent = `РћР±РЅРѕРІР»РµРЅРѕ: ${formatDate(snapshot.generated_at)}`;
}

function renderStatusStrip(snapshot) {
  const step = currentPipelineStep(snapshot);
  const cards = [
    ["РЎР»РµРґСѓСЋС‰РёР№ С€Р°Рі", step?.title || "РћР¶РёРґР°РЅРёРµ", step?.summary || "РџР°Р№РїР»Р°Р№РЅ РіРѕС‚РѕРІ.", step?.status === "completed" ? "good" : step?.status === "blocked" ? "warn" : "neutral"],
    ["Р›РѕРіРёРЅ", snapshot.hh_login?.status || "idle", snapshot.hh_login?.message || "РЎРµСЃСЃРёСЏ hh.ru РѕР¶РёРґР°РµС‚ РїСЂРѕРІРµСЂРєРё.", snapshot.hh_login?.state_file_exists ? "good" : snapshot.hh_login?.running ? "neutral" : "warn"],
    ["Р РµР·СЋРјРµ", snapshot.selected_resume_title || snapshot.selected_resume_id || "РЅРµ РІС‹Р±СЂР°РЅРѕ", snapshot.hh_resumes?.length ? `РќР° hh.ru РЅР°Р№РґРµРЅРѕ ${snapshot.hh_resumes.length}.` : "РЎРїРёСЃРѕРє СЂРµР·СЋРјРµ РµС‰Рµ РЅРµ РїРѕРґС‚СЏРЅСѓС‚.", snapshot.selected_resume_id ? "good" : "warn"],
    ["Р¤РёР»СЊС‚СЂС‹", snapshot.filter_plan?.search_text || "РЅРµ СЃРѕР±СЂР°РЅС‹", snapshot.filter_plan?.planner_backend ? `РџР»Р°РЅРёСЂРѕРІС‰РёРє: ${snapshot.filter_plan.planner_backend}.` : "Р¤РёР»СЊС‚СЂС‹ РµС‰Рµ РЅРµ РїРѕСЃС‚СЂРѕРµРЅС‹.", snapshot.filter_plan?.search_text ? "good" : "warn"],
    ["РћС†РµРЅРєР°", String(snapshot.counts?.assessed || 0), `${snapshot.counts?.fit || 0} РїРѕРґС…РѕРґРёС‚ В· ${snapshot.counts?.doubt || 0} СЃРѕРјРЅРµРЅРёРµ В· ${snapshot.counts?.no_fit || 0} РЅРµ РїРѕРґС…РѕРґРёС‚`, snapshot.counts?.assessed ? "good" : snapshot.analysis_job?.running ? "neutral" : "warn"],
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
    return `<div class="empty-state">РЎРїРёСЃРѕРє СЂРµР·СЋРјРµ РїРѕРєР° РїСѓСЃС‚. РџРѕСЃР»Рµ Р»РѕРіРёРЅР° РЅР°Р¶РјРёС‚Рµ В«РћР±РЅРѕРІРёС‚СЊ СЃРїРёСЃРѕРє СЂРµР·СЋРјРµВ».</div>`;
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
                <a class="button button--ghost" href="${escapeHtml(resume.url || "#")}" target="_blank" rel="noreferrer">РћС‚РєСЂС‹С‚СЊ</a>
                <button class="button ${isActive ? "button--primary" : ""}" type="button" data-resume-id="${escapeHtml(resume.resume_id)}">
                  ${isActive ? "Р’С‹Р±СЂР°РЅРѕ" : "Р’С‹Р±СЂР°С‚СЊ"}
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
        <strong>РђРєС‚РёРІРЅС‹Р№ hh-Р°РєРєР°СѓРЅС‚</strong>
        <p>${escapeHtml(snapshot.active_account?.display_name || activeKey || "РµС‰Рµ РЅРµ РѕРїСЂРµРґРµР»РµРЅ")}</p>
      </div>
      <div class="resume-grid">
        ${renderList(
          accounts,
          (account) => `
            <article class="resume-card ${activeKey === account.account_key ? "is-active" : ""}">
              <div>
                <strong>${escapeHtml(account.display_name || account.account_key)}</strong>
                <p class="muted">${escapeHtml(account.resume_count ? `${account.resume_count} СЂРµР·СЋРјРµ` : "СЂРµР·СЋРјРµ РЅРµ РѕРїСЂРµРґРµР»РµРЅС‹")}</p>
              </div>
              <div class="resume-card-actions">
                <button class="button ${activeKey === account.account_key ? "button--primary" : ""}" type="button" ${activeKey === account.account_key ? "disabled" : `data-account-key="${escapeHtml(account.account_key || "")}"`}>
                  ${activeKey === account.account_key ? "РђРєС‚РёРІРµРЅ" : "РџРµСЂРµРєР»СЋС‡РёС‚СЊ"}
                </button>
              </div>
            </article>
          `,
          "РџРѕСЃР»Рµ Р»РѕРіРёРЅР° Р·РґРµСЃСЊ РїРѕСЏРІСЏС‚СЃСЏ СЃРѕС…СЂР°РЅРµРЅРЅС‹Рµ hh-Р°РєРєР°СѓРЅС‚С‹.",
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
    return `<div class="empty-state">РџР»Р°РЅ С„РёР»СЊС‚СЂРѕРІ РµС‰Рµ РЅРµ СЃРѕР±СЂР°РЅ. Р­С‚РѕС‚ С€Р°Рі РґРѕР»Р¶РµРЅ РїСЂРѕРёСЃС…РѕРґРёС‚СЊ РґРѕ Р°РЅР°Р»РёР·Р° РІР°РєР°РЅСЃРёР№.</div>`;
  }
  return `
    <div class="detail-meta-grid">
      <div class="meta-row"><span>РџРѕРёСЃРєРѕРІС‹Р№ С‚РµРєСЃС‚</span><strong>${escapeHtml(filterPlan.search_text || "РЅРµ Р·Р°РґР°РЅ")}</strong></div>
      <div class="meta-row"><span>РџР»Р°РЅРёСЂРѕРІС‰РёРє</span><strong>${escapeHtml(filterPlan.planner_backend || "rules")}</strong></div>
      <div class="meta-row"><span>РЎС‚СЂР°С‚РµРіРёСЏ</span><strong>${escapeHtml(filterPlan.strategy || "script_first")}</strong></div>
      <div class="meta-row"><span>РќР° hh.ru</span><strong>${escapeHtml(liveStats.total_available ? `${liveStats.total_available} РЅР°Р№РґРµРЅРѕ` : "РµС‰Рµ РЅРµ СЃС‡РёС‚Р°Р»Рё")}</strong></div>
      <div class="meta-row"><span>Р’ Р»РѕРєР°Р»СЊРЅРѕР№ РѕС‡РµСЂРµРґРё</span><strong>${escapeHtml(String(snapshot.counts?.total_vacancies || 0))}</strong></div>
      <div class="meta-row"><span>РЎС‚СЂР°РЅРёС† РїСЂРѕР№РґРµРЅРѕ</span><strong>${escapeHtml(liveStats.pages_parsed ? String(liveStats.pages_parsed) : "0")}</strong></div>
      <div class="meta-row"><span>РџР°СЂР°РјРµС‚СЂС‹ Р·Р°РїСЂРѕСЃР°</span><strong>${escapeHtml(JSON.stringify(filterPlan.query_params || {}))}</strong></div>
    </div>
    ${
      filterPlan.search_url
        ? `<div class="note"><strong>HH-РїРѕРёСЃРє</strong><p><a href="${escapeHtml(filterPlan.search_url)}" target="_blank" rel="noreferrer">${escapeHtml(filterPlan.search_url)}</a></p></div>`
        : ""
    }
    <div class="detail-block">
      <strong>РћСЃС‚Р°С‚РѕС‡РЅС‹Рµ РїСЂР°РІРёР»Р°</strong>
      ${renderList(filterPlan.residual_rules || [], (item) => `<div class="reason-card"><p>${escapeHtml(item)}</p></div>`, "Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹С… РѕРіСЂР°РЅРёС‡РµРЅРёР№ РЅРµС‚.")}
    </div>
    <div class="detail-meta-grid detail-meta-grid--rules">
      <div class="note"><strong>РћР±С‰РёРµ РїСЂР°РІРёР»Р°</strong><p>${escapeHtml(systemRules || "РџРѕРєР° РЅРµ СЃРѕР±СЂР°РЅС‹.")}</p></div>
      <div class="note"><strong>РџСЂР°РІРёР»Р° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ</strong><p>${escapeHtml(userRules || "РџРѕРєР° РЅРµ СЃРѕР±СЂР°РЅС‹.")}</p></div>
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
            <span class="panel-kicker">РћР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ Intake</span>
            <h2>РЎРЅР°С‡Р°Р»Р° СЂР°Р·Р±РµСЂРµРј РІР°С€ РїСЂРѕС„РёР»СЊ, РїРѕС‚РѕРј РїРµСЂРµР№РґРµРј Рє РїРѕРёСЃРєСѓ РІР°РєР°РЅСЃРёР№</h2>
            <p class="panel-lead">Р­С‚Рѕ РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ СЌС‚Р°Рї. РђРіРµРЅС‚ СЃРЅР°С‡Р°Р»Р° РІС‹С‚СЏРіРёРІР°РµС‚ РјР°РєСЃРёРјСѓРј РёР· СЂРµР·СЋРјРµ, РїРѕС‚РѕРј РєРѕСЂРѕС‚РєРёРј РґРёР°Р»РѕРіРѕРј СЃРѕР±РёСЂР°РµС‚ РІР°С€Рё Р¶РµСЃС‚РєРёРµ РєСЂРёС‚РµСЂРёРё Рё С‚РѕР»СЊРєРѕ РїРѕСЃР»Рµ СЌС‚РѕРіРѕ Р·Р°РїСѓСЃРєР°РµС‚ РїРѕРёСЃРє Рё РѕС†РµРЅРєСѓ РІР°РєР°РЅСЃРёР№.</p>
          </div>
          <div class="inline-actions">
            ${renderActionButtons([{ id: "start-intake", label: dialog.active ? "РџРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ РѕРїСЂРѕСЃ" : "РќР°С‡Р°С‚СЊ РѕРїСЂРѕСЃ" }], "button--compact")}
          </div>
        </div>
        <div class="intake-stage-grid">
          <div class="note">
            <strong>Р§С‚Рѕ СѓР¶Рµ РІР·СЏР»Рё РёР· СЂРµР·СЋРјРµ</strong>
            <p>${escapeHtml(context.resume_title || snapshot.selected_resume_title || "Р РµР·СЋРјРµ РїРѕРєР° РЅРµ РІС‹Р±СЂР°РЅРѕ.")}</p>
            <p>${escapeHtml((context.inferred_roles || []).length ? `Р РѕР»Рё: ${context.inferred_roles.join(", ")}` : "Р РѕР»Рё РёР· СЂРµР·СЋРјРµ РµС‰Рµ РЅРµ СѓС‚РѕС‡РЅРµРЅС‹.")}</p>
            <p>${escapeHtml((context.inferred_skills || []).length ? `РќР°РІС‹РєРё: ${context.inferred_skills.join(", ")}` : "РќР°РІС‹РєРё РёР· СЂРµР·СЋРјРµ РµС‰Рµ РЅРµ СѓС‚РѕС‡РЅРµРЅС‹.")}</p>
          </div>
          <div class="note">
            <strong>Р§С‚Рѕ РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ РЅСѓР¶РЅРѕ СѓР·РЅР°С‚СЊ</strong>
            <p>${escapeHtml((snapshot.setup_summary?.intake_missing || []).length ? `РџРѕРєР° РЅРµ Р·Р°РєСЂС‹С‚С‹: ${(snapshot.setup_summary.intake_missing || []).join(", ")}.` : "РЎС‚СЂСѓРєС‚СѓСЂРЅС‹Рµ РїСЂРѕР±РµР»С‹ Р·Р°РєСЂС‹С‚С‹, РјРѕР¶РЅРѕ РїРµСЂРµС…РѕРґРёС‚СЊ РґР°Р»СЊС€Рµ.")}</p>
            <p>${escapeHtml(currentQuestion ? `РЎРµР№С‡Р°СЃ РІРѕРїСЂРѕСЃ ${stepIndex + 1} РёР· ${questions.length}.` : "Р”РёР°Р»РѕРі РµС‰Рµ РЅРµ РЅР°С‡Р°С‚.")}</p>
          </div>
        </div>
        <div class="intake-dialog-shell">
          <div class="intake-transcript">
            ${renderList(
              recentMessages,
              (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "Р°РіРµРЅС‚" : "РІС‹")}</span><p>${escapeHtml(item.text)}</p></article>`,
              "Р”РёР°Р»РѕРі РµС‰Рµ РЅРµ РЅР°С‡Р°С‚.",
            )}
          </div>
          <div class="intake-question-card">
            <strong>${escapeHtml(currentQuestion?.title || "РќР°Р¶РјРёС‚Рµ В«РќР°С‡Р°С‚СЊ РѕРїСЂРѕСЃВ», С‡С‚РѕР±С‹ Р°РіРµРЅС‚ РЅР°С‡Р°Р» РґРёР°Р»РѕРі.")}</strong>
            <p>${escapeHtml(currentQuestion?.why || "РЎРЅР°С‡Р°Р»Р° С„РёРєСЃРёСЂСѓРµРј Р¶РµСЃС‚РєРёРµ РєСЂРёС‚РµСЂРёРё, РїРѕС‚РѕРј РґРѕСѓС‚РѕС‡РЅСЏРµРј Р¶РµР»Р°С‚РµР»СЊРЅС‹Рµ РґРµС‚Р°Р»Рё.")}</p>
            <p class="muted">${escapeHtml(currentQuestion?.example || "РњРѕР¶РЅРѕ РѕС‚РІРµС‡Р°С‚СЊ СЃРІРѕР±РѕРґРЅС‹Рј С‚РµРєСЃС‚РѕРј. Р•СЃР»Рё РїСѓРЅРєС‚ РЅРµРІР°Р¶РµРЅ, РЅР°РїРёС€РёС‚Рµ В«РїСЂРѕРїСѓСЃС‚РёС‚СЊВ».")}</p>
          </div>
          <form id="intake-form" class="intake-form">
            <textarea id="intake-input" rows="7" placeholder="РћС‚РІРµС‚СЊС‚Рµ СЃРІРѕР±РѕРґРЅС‹Рј С‚РµРєСЃС‚РѕРј. РќР°РїСЂРёРјРµСЂ: С‚РѕР»СЊРєРѕ remote, РЅРµ С…РѕС‡Сѓ РіРѕСЃСѓС…Сѓ, СЂРѕР»Рё LLM Engineer/NLP Engineer, Р·Р°СЂРїР»Р°С‚Р° РѕС‚ 350k."></textarea>
            <div class="inline-actions">
              <button class="button" type="button" data-dashboard-action="start-intake">${escapeHtml(dialog.active ? "РќР°С‡Р°С‚СЊ Р·Р°РЅРѕРІРѕ" : "РќР°С‡Р°С‚СЊ РѕРїСЂРѕСЃ")}</button>
              <button class="button button--primary" type="submit">${escapeHtml(dialog.active ? "РћС‚РїСЂР°РІРёС‚СЊ РѕС‚РІРµС‚" : "РќР°С‡Р°С‚СЊ Рё РїРµСЂРµР№С‚Рё Рє РІРѕРїСЂРѕСЃР°Рј")}</button>
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
        await sendChatCommand("РЅР°С‡Р°С‚СЊ РѕРїСЂРѕСЃ");
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
            <span class="panel-kicker">РџР°Р№РїР»Р°Р№РЅ</span>
            <h2>${escapeHtml(step?.title || "Р Р°Р±РѕС‡РёР№ РјР°СЂС€СЂСѓС‚")}</h2>
          </div>
          <div class="inline-actions">
            ${renderActionButtons(actions, "button--compact")}
          </div>
        </div>
        <p class="panel-lead">${escapeHtml(step?.detail || "РђРіРµРЅС‚ Р¶РґРµС‚ СЃР»РµРґСѓСЋС‰РµРіРѕ РґРµР№СЃС‚РІРёСЏ.")}</p>
        <div class="pipeline-grid">${renderPipeline(snapshot)}</div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">РђРєРєР°СѓРЅС‚С‹</span>
            <h2>РџСЂРѕС„РёР»Рё hh.ru</h2>
          </div>
        </div>
        <p class="panel-lead">РњРѕР¶РЅРѕ С…СЂР°РЅРёС‚СЊ РЅРµСЃРєРѕР»СЊРєРѕ hh-Р°РєРєР°СѓРЅС‚РѕРІ РІ РѕРґРЅРѕР№ РїСЂРѕРіСЂР°РјРјРµ Рё Р±С‹СЃС‚СЂРѕ РїРµСЂРµРєР»СЋС‡Р°С‚СЊСЃСЏ РјРµР¶РґСѓ РЅРёРјРё.</p>
        ${renderAccountSwitcher(snapshot)}
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Р РµР·СЋРјРµ</span>
            <h2>Р’С‹Р±РѕСЂ РґР»СЏ РїРѕРёСЃРєР°</h2>
          </div>
        </div>
        <p class="panel-lead">РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РґРѕР»Р¶РµРЅ СЏРІРЅРѕ РІРёРґРµС‚СЊ, РїРѕ РєР°РєРѕРјСѓ СЂРµР·СЋРјРµ РёРґРµС‚ live search Рё РѕС†РµРЅРєР°.</p>
        ${renderResumeChooser(snapshot)}
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Р¤РёР»СЊС‚СЂС‹</span>
            <h2>РџРѕРёСЃРє РїРµСЂРµРґ РїР°СЂСЃРёРЅРіРѕРј</h2>
          </div>
        </div>
        ${renderFilterPlan(snapshot)}
      </section>

      <section class="panel panel--wide">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">Р¤РѕРєСѓСЃ</span>
            <h2>Р§С‚Рѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІРёРґРёС‚ СЃРµР№С‡Р°СЃ</h2>
          </div>
        </div>
        <div class="focus-grid">
          <div class="note">
            <strong>РћР¶РёРґР°РЅРёРµ СЂСѓС‡РЅРѕРіРѕ РґРµР№СЃС‚РІРёСЏ</strong>
            <p>${escapeHtml(snapshot.hh_login?.running ? "Р‘СЂР°СѓР·РµСЂ РѕС‚РєСЂС‹С‚. Р–РґРµРј, РїРѕРєР° РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р·Р°РІРµСЂС€РёС‚ Р»РѕРіРёРЅ Рё РєР°РїС‡Сѓ." : snapshot.setup_summary?.live_refresh_message || "РљРѕРЅС‚РµРєСЃС‚ hh.ru РіРѕС‚РѕРІРёС‚СЃСЏ.")}</p>
          </div>
          <div class="note">
            <strong>РџРѕРёСЃРє РЅР° hh.ru</strong>
            <p>${escapeHtml(
              snapshot.setup_summary?.live_refresh_stats?.total_available
                ? `РќР°Р№РґРµРЅРѕ ${snapshot.setup_summary.live_refresh_stats.total_available} РІР°РєР°РЅСЃРёР№, Р»РѕРєР°Р»СЊРЅРѕ СЃРѕР±СЂР°РЅРѕ ${snapshot.setup_summary.live_refresh_stats.count || snapshot.counts?.total_vacancies || 0}.`
                : "РџРѕСЃР»Рµ Р·Р°РїСѓСЃРєР° Р°РЅР°Р»РёР·Р° Р·РґРµСЃСЊ РїРѕСЏРІРёС‚СЃСЏ РѕР±С‰РµРµ С‡РёСЃР»Рѕ РІР°РєР°РЅСЃРёР№ СЃ hh.ru Рё РїСЂРѕРіСЂРµСЃСЃ РїР°СЂСЃРёРЅРіР°.",
            )}</p>
          </div>
          <div class="note">
            <strong>РўРµРєСѓС‰Р°СЏ СЂРµРєРѕРјРµРЅРґР°С†РёСЏ Р°РіРµРЅС‚Р°</strong>
            <p>${escapeHtml(snapshot.next_recommended_action?.reason || step?.summary || "РћС‚РєСЂРѕР№С‚Рµ С‡Р°С‚ Рё СѓС‚РѕС‡РЅРёС‚Рµ СЃР»РµРґСѓСЋС‰РёР№ С€Р°Рі.")}</p>
          </div>
          <div class="note">
            <strong>РЎРѕСЃС‚РѕСЏРЅРёРµ РѕС†РµРЅРєРё</strong>
            <p>${escapeHtml(snapshot.analysis_job?.message || snapshot.analysis_state?.stale_reason || "РђРЅР°Р»РёР· РµС‰Рµ РЅРµ Р·Р°РїСѓСЃРєР°Р»СЃСЏ.")}</p>
          </div>
          <div class="note">
            <strong>РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ СЂРµР·СЋРјРµ</strong>
            <p>${escapeHtml(snapshot.profile_sync?.message || "РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ РµС‰Рµ РЅРµ Р·Р°РїСѓСЃРєР°Р»Р°СЃСЊ.")}</p>
          </div>
          <div class="note">
            <strong>РўРµРєСѓС‰РёР№ РєР°РЅРґРёРґР°С‚ РЅР° РїСЂРѕСЃРјРѕС‚СЂ</strong>
            <p>${escapeHtml(selectedCard ? `${selectedCard.title} В· ${selectedCard.category_label}` : "Р’Р°РєР°РЅСЃРёСЏ РµС‰Рµ РЅРµ РІС‹Р±СЂР°РЅР°.")}</p>
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
          <span class="panel-kicker">Р§Р°С‚ Р°РіРµРЅС‚Р°</span>
          <h2>Р§Р°С‚</h2>
        </div>
      </div>
      <div id="chat-quick-actions" class="chip-row"></div>
      <div id="chat-log" class="chat-log"></div>
      <form id="chat-form" class="chat-form">
        <textarea id="chat-input" rows="4" placeholder="РќР°РїРёС€Рё Р·Р°РґР°С‡Сѓ, РїСЂР°РІРєСѓ РїСЂР°РІРёР» РёР»Рё СѓС‚РѕС‡РЅРµРЅРёРµ РїРѕ СЂРµР·СЋРјРµ"></textarea>
        <button id="chat-submit" class="button button--primary" type="submit">РћС‚РїСЂР°РІРёС‚СЊ</button>
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
    (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "Р°РіРµРЅС‚" : "РІС‹")}</span><p>${escapeHtml(item.text)}</p></article>`,
    "Р§Р°С‚ РїСѓСЃС‚.",
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
  const resumeAnalysis = snapshot.intake?.resume_intake_analysis || {};
  const confirmationMode = Boolean(dialog.completed || (snapshot.setup_summary?.intake_dialog_completed && !snapshot.setup_summary?.intake_confirmed));
  const overlay = document.createElement("section");
  overlay.id = "intake-overlay";
  overlay.className = "intake-overlay";
  overlay.innerHTML = `
    <div class="intake-overlay__backdrop"></div>
    <div class="intake-overlay__panel">
      <div class="intake-overlay__head">
        <div>
          <span class="panel-kicker">РћР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ Intake</span>
          <h2>${escapeHtml(confirmationMode ? "РџРѕРґС‚РІРµСЂРґРёС‚Рµ РёС‚РѕРіРѕРІС‹Рµ РїСЂР°РІРёР»Р° РїРµСЂРµРґ Р·Р°РїСѓСЃРєРѕРј РїРѕРёСЃРєР°" : "РЎРЅР°С‡Р°Р»Р° РєРѕСЂРѕС‚РєРёР№ РґРёР°Р»РѕРі Рѕ РІР°С€РёС… С‚СЂРµР±РѕРІР°РЅРёСЏС…, РїРѕС‚РѕРј РїРѕРёСЃРє РІР°РєР°РЅСЃРёР№")}</h2>
          <p class="panel-lead">${escapeHtml(confirmationMode ? "РђРіРµРЅС‚ СЃРѕР±СЂР°Р» СЃС‚СЂСѓРєС‚СѓСЂРЅС‹Рµ РїСЂР°РІРёР»Р° РєР°РЅРґРёРґР°С‚Р°. РџСЂРѕРІРµСЂСЊС‚Рµ РєСЂР°С‚РєСѓСЋ СЃРІРѕРґРєСѓ Рё РїРѕРґС‚РІРµСЂРґРёС‚Рµ РµС‘. Р”Рѕ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ РїРѕРёСЃРє Рё РѕС†РµРЅРєР° РІР°РєР°РЅСЃРёР№ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅС‹." : "РђРіРµРЅС‚ СѓР¶Рµ РІС‹С‚Р°С‰РёР» Р±Р°Р·Сѓ РёР· hh-СЂРµР·СЋРјРµ Рё С‚РµРїРµСЂСЊ Р·Р°РґР°РµС‚ С‚РѕР»СЊРєРѕ РЅРµРґРѕСЃС‚Р°СЋС‰РёРµ РІРѕРїСЂРѕСЃС‹. РџРѕРєР° СЌС‚РѕС‚ РґРёР°Р»РѕРі РЅРµ Р·Р°РІРµСЂС€РµРЅ, РїРѕРёСЃРє Рё РѕС†РµРЅРєР° РІР°РєР°РЅСЃРёР№ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅС‹.")}</p>
        </div>
        <div class="intake-overlay__progress">
          <strong>${escapeHtml(confirmationMode ? "РћСЃС‚Р°Р»СЃСЏ С€Р°Рі РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ" : intakePriorityLabel(question))}</strong>
          <span class="muted">${escapeHtml(confirmationMode ? "РџРѕСЃР»Рµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ РѕС‚РєСЂРѕСЋС‚СЃСЏ РїРѕРёСЃРє Рё Р°РЅР°Р»РёР·" : question ? `Р’РѕРїСЂРѕСЃ ${Number(dialog.step_index || 0) + 1} РёР· ${(dialog.questions || []).length}` : "РќР°Р¶РјРёС‚Рµ В«РќР°С‡Р°С‚СЊ РѕРїСЂРѕСЃВ»")}</span>
        </div>
      </div>
      <div class="intake-overlay__meta">
        <article class="note">
          <strong>Р§С‚Рѕ СѓР¶Рµ РїРѕРЅСЏР» РёР· СЂРµР·СЋРјРµ</strong>
          <p>${escapeHtml(context.resume_title || snapshot.selected_resume_title || "Р РµР·СЋРјРµ РїРѕРєР° РЅРµ РІС‹Р±СЂР°РЅРѕ.")}</p>
          <p>${escapeHtml((context.inferred_roles || []).length ? `Р РѕР»Рё: ${context.inferred_roles.join(", ")}` : "Р РѕР»Рё РёР· СЂРµР·СЋРјРµ РїРѕРєР° РЅРµ РІС‹РґРµР»РµРЅС‹.")}</p>
          <p>${escapeHtml((context.detected_skills || []).length ? `РќР°РІС‹РєРё: ${context.detected_skills.slice(0, 8).join(", ")}` : "РќР°РІС‹РєРё РёР· СЂРµР·СЋРјРµ РїРѕРєР° РЅРµ РІС‹РґРµР»РµРЅС‹.")}</p>
        </article>
        <article class="note">
          <strong>Р§С‚Рѕ РµС‰Рµ РЅСѓР¶РЅРѕ СѓС‚РѕС‡РЅРёС‚СЊ</strong>
          <p>${escapeHtml((snapshot.setup_summary?.intake_missing || []).length ? (snapshot.setup_summary.intake_missing || []).join(", ") : "РљСЂРёС‚РёС‡РЅС‹Рµ РїСЂРѕР±РµР»С‹ Р·Р°РєСЂС‹С‚С‹. РћСЃС‚Р°Р»РѕСЃСЊ РґРѕР±СЂР°С‚СЊ СѓС‚РѕС‡РЅРµРЅРёСЏ Рё Р·Р°РІРµСЂС€РёС‚СЊ РґРёР°Р»РѕРі.")}</p>
        </article>
      </div>
      <div class="intake-overlay__body">
        <div id="intake-overlay-log" class="intake-overlay__log">
          ${
            confirmationMode
              ? `
                <article class="note"><strong>Р¦РµР»РµРІС‹Рµ СЂРѕР»Рё</strong><p>${escapeHtml((contract.search_targets?.primary_roles || []).join(", ") || "РќРµ Р·Р°РїРѕР»РЅРµРЅРѕ")}</p></article>
                <article class="note"><strong>Р–РµСЃС‚РєРёРµ РѕРіСЂР°РЅРёС‡РµРЅРёСЏ</strong><p>${escapeHtml([
                  contract.hard_constraints?.work_format === "remote_only" ? "С‚РѕР»СЊРєРѕ remote" : "",
                  ...(contract.hard_constraints?.exclude_company_types || []),
                  ...(contract.hard_constraints?.exclude_vacancy_signals || []),
                ].filter(Boolean).join(", ") || "РќРµ Р·Р°РїРѕР»РЅРµРЅРѕ")}</p></article>
                <article class="note"><strong>Must-have СЃС‚РµРє</strong><p>${escapeHtml((contract.search_targets?.must_have_keywords || []).join(", ") || "РќРµ Р·Р°РїРѕР»РЅРµРЅРѕ")}</p></article>
                <article class="note"><strong>РЎРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ</strong><p>${escapeHtml(`РЇР·С‹Рє: ${contract.cover_letter_policy?.language || "ru"}, С‚РѕРЅ: ${contract.cover_letter_policy?.tone || "РґРµР»РѕРІРѕР№"}`)}</p></article>
              `
              : renderList(
                  transcript,
                  (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "Р°РіРµРЅС‚" : "РІС‹")}</span><p>${escapeHtml(item.text)}</p></article>`,
                  "Р”РёР°Р»РѕРі РµС‰Рµ РЅРµ РЅР°С‡Р°С‚.",
                )
          }
        </div>
        <aside class="intake-overlay__question">
          <strong>${escapeHtml(confirmationMode ? "РЎРІРѕРґРєР° РїСЂР°РІРёР» СЃРѕР±СЂР°РЅР°" : question?.title || "РќР°Р¶РјРёС‚Рµ В«РќР°С‡Р°С‚СЊ РѕРїСЂРѕСЃВ», С‡С‚РѕР±С‹ РїРµСЂРµР№С‚Рё Рє РїРµСЂРІРѕРјСѓ РІРѕРїСЂРѕСЃСѓ.")}</strong>
          <p>${escapeHtml(confirmationMode ? "Р•СЃР»Рё СЃРІРѕРґРєР° РІ С†РµР»РѕРј РІРµСЂРЅР°, РїРѕРґС‚РІРµСЂРґРёС‚Рµ РїСЂР°РІРёР»Р°. Р•СЃР»Рё С‡С‚Рѕ-С‚Рѕ РЅРµ С‚Р°Рє, РїРµСЂРµР·Р°РїСѓСЃС‚РёС‚Рµ РґРёР°Р»РѕРі Рё РїРѕРїСЂР°РІСЊС‚Рµ РѕС‚РІРµС‚С‹." : question?.hint || "РЎРЅР°С‡Р°Р»Р° С„РёРєСЃРёСЂСѓРµРј РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Рµ РєСЂРёС‚РµСЂРёРё, РїРѕС‚РѕРј РІР°Р¶РЅС‹Рµ РїСЂРµРґРїРѕС‡С‚РµРЅРёСЏ Рё РІ РєРѕРЅС†Рµ РЅРµРѕР±СЏР·Р°С‚РµР»СЊРЅС‹Рµ РЅСЋР°РЅСЃС‹.")}</p>
          <p class="muted">${escapeHtml(confirmationMode ? "РџРѕСЃР»Рµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ СЌС‚Рё РїСЂР°РІРёР»Р° СЃС‚Р°РЅСѓС‚ РёСЃС‚РѕС‡РЅРёРєРѕРј РґР»СЏ РїРѕРёСЃРєР°, РѕС†РµРЅРєРё РІР°РєР°РЅСЃРёР№, СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹С… Рё Р°РЅРєРµС‚." : question?.example || "РњРѕР¶РЅРѕ РѕС‚РІРµС‡Р°С‚СЊ СЃРІРѕР±РѕРґРЅС‹Рј С‚РµРєСЃС‚РѕРј. Р•СЃР»Рё С‚РµРєСѓС‰РµРµ РїРѕРЅРёРјР°РЅРёРµ РїРѕРґС…РѕРґРёС‚, РЅР°РїРёС€РёС‚Рµ В«РѕСЃС‚Р°РІРёС‚СЊ РєР°Рє РµСЃС‚СЊВ».")}</p>
        </aside>
      </div>
      <form id="intake-overlay-form" class="intake-overlay__form">
        ${confirmationMode ? "" : '<textarea id="intake-overlay-input" rows="6" placeholder="РћС‚РІРµС‚СЊС‚Рµ СЃРІРѕР±РѕРґРЅС‹Рј С‚РµРєСЃС‚РѕРј. РќР°РїСЂРёРјРµСЂ: С‚РѕР»СЊРєРѕ remote, РЅРµ С…РѕС‡Сѓ РіРѕСЃСѓС…Сѓ Рё СѓРЅРёРІРµСЂСЃРёС‚РµС‚С‹, СЂРѕР»Рё LLM Engineer/NLP Engineer, Р·Р°СЂРїР»Р°С‚Р° РѕС‚ 350k."></textarea>'}
        <div class="inline-actions">
          <button class="button" type="button" data-dashboard-action="start-intake">${escapeHtml(dialog.active ? "РќР°С‡Р°С‚СЊ Р·Р°РЅРѕРІРѕ" : "РќР°С‡Р°С‚СЊ РѕРїСЂРѕСЃ")}</button>
          ${
            confirmationMode
              ? '<button class="button button--primary" type="button" data-dashboard-action="confirm-intake">РџРѕРґС‚РІРµСЂРґРёС‚СЊ Рё РѕС‚РєСЂС‹С‚СЊ РїРѕРёСЃРє</button>'
              : `<button class="button button--primary" type="submit">${escapeHtml(dialog.active ? "РћС‚РїСЂР°РІРёС‚СЊ РѕС‚РІРµС‚" : "РќР°С‡Р°С‚СЊ РґРёР°Р»РѕРі")}</button>`
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
      await sendChatCommand("РЅР°С‡Р°С‚СЊ РѕРїСЂРѕСЃ");
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
      <span class="panel-kicker">LLM РЅРµРґРѕСЃС‚СѓРїРЅР°</span>
      <h2>${escapeHtml(gate.title || "РќРµ СѓРґР°Р»РѕСЃСЊ РѕР±СЂР°С‚РёС‚СЊСЃСЏ Рє РјРѕРґРµР»Рё")}</h2>
      <p class="panel-lead">${escapeHtml(gate.message || "РќР° СЌС‚РѕРј СЌС‚Р°РїРµ РЅСѓР¶РµРЅ Р°РіРµРЅС‚РЅС‹Р№ СЂР°Р·Р±РѕСЂ С‡РµСЂРµР· OpenRouter. РЎРµР№С‡Р°СЃ РјРѕРґРµР»СЊ РЅРµРґРѕСЃС‚СѓРїРЅР°.")}</p>
      <div class="note">
        <strong>Р§С‚Рѕ РґРµР»Р°С‚СЊ РґР°Р»СЊС€Рµ</strong>
        <p>РњРѕР¶РЅРѕ Р»РёР±Рѕ РїСЂРѕРґРѕР»Р¶РёС‚СЊ РЅР° СЌРІСЂРёСЃС‚РёРєР°С… РєР°Рє РІСЂРµРјРµРЅРЅС‹Р№ СЂРµР¶РёРј, Р»РёР±Рѕ РЅРёС‡РµРіРѕ РЅРµ Р·Р°РїСѓСЃРєР°С‚СЊ Рё РґРѕР¶РґР°С‚СЊСЃСЏ, РїРѕРєР° LLM СЃРЅРѕРІР° СЃС‚Р°РЅРµС‚ РґРѕСЃС‚СѓРїРЅР°.</p>
      </div>
      <div class="inline-actions">
        <button class="button button--primary" type="button" data-dashboard-action="llm-fallback-heuristics">РџСЂРѕРґРѕР»Р¶РёС‚СЊ РЅР° СЌРІСЂРёСЃС‚РёРєР°С…</button>
        <button class="button" type="button" data-dashboard-action="llm-wait">Р–РґР°С‚СЊ LLM</button>
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
  state.pendingActionMessage = "РћС‚РїСЂР°РІР»СЏСЋ СЃРѕРѕР±С‰РµРЅРёРµ Р°РіРµРЅС‚Сѓ.";
  renderChatLog();
  if (state.snapshot) renderAgentView(state.snapshot);
  try {
    const result = await postJson("/api/chat", { message, selected_vacancy_id: state.selectedVacancyId || "" });
    if (input) input.value = "";
    if (result.result?.message) state.chatHistory.push({ role: "assistant", text: result.result.message });
    if (result.snapshot) renderSnapshot(result.snapshot);
  } catch (error) {
    state.chatHistory.push({ role: "assistant", text: `РћС€РёР±РєР°: ${error.message}` });
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
    ? "РћР±РЅРѕРІР»СЏСЋ РґРµР№СЃС‚РІРёСЏ РїРѕ РІР°РєР°РЅСЃРёРё."
    : (url.includes("/resume")
      ? "РћР±РЅРѕРІР»СЏСЋ РїСЂРѕС„РёР»СЊ Рё С‡РµСЂРЅРѕРІРёРє СЂРµР·СЋРјРµ."
      : (url.includes("/analyze")
        ? "Р—Р°РїСѓСЃРєР°СЋ Р°РЅР°Р»РёР· Рё СЂР°Р·Р±РѕСЂ РІР°РєР°РЅСЃРёР№."
        : (url.includes("/apply-batch")
          ? `Р—Р°РїСѓСЃРєР°СЋ РїР°РєРµС‚РЅСѓСЋ РѕС‚РїСЂР°РІРєСѓ РѕС‚РєР»РёРєРѕРІ РїРѕ РєРѕР»РѕРЅРєРµ ${categoryLabel(payload?.category || "")}.`
          : "Р’С‹РїРѕР»РЅСЏСЋ РґРµР№СЃС‚РІРёРµ.")));
  if (state.snapshot) renderAgentView(state.snapshot);
  try {
    const result = await postJson(url, payload);
    const message = pickResultMessage(result);
    if (message) state.chatHistory.push({ role: "assistant", text: message });
    renderChatLog();
    if (result.snapshot) renderSnapshot(result.snapshot);
    if (onSuccess) onSuccess(result);
  } catch (error) {
    state.chatHistory.push({ role: "assistant", text: `РћС€РёР±РєР°: ${error.message}` });
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
      "РЎРЅР°С‡Р°Р»Р° РЅСѓР¶РЅРѕ Р·Р°РІРµСЂС€РёС‚СЊ РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ РґРёР°Р»РѕРі Рѕ РїСЂРµРґРїРѕС‡С‚РµРЅРёСЏС… РєР°РЅРґРёРґР°С‚Р°. РџРѕСЃР»Рµ СЌС‚РѕРіРѕ СЏ РѕС‚РєСЂРѕСЋ РїРѕРёСЃРє, Р°РЅР°Р»РёР· Рё РѕС‚РєР»РёРєРё.",
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
    root.innerHTML = `<section class="panel"><div class="note"><strong>Сначала завершите intake</strong><p>${escapeHtml("Пока intake не подтверждён, поиск и оценка вакансий заблокированы. Сначала завершите и подтвердите onboarding в правой панели.")}</p></div></section>`;
    return;
  }
  if ((snapshot.counts?.assessed || 0) <= 0) {
    root.innerHTML = `<section class="panel"><div class="note"><strong>Оценённых вакансий пока нет</strong><p>${escapeHtml(snapshot.analysis_job?.message || "Сначала запустите анализ, чтобы заполнить и отсортировать очередь.")}</p></div><div class="note"><strong>Источник вакансий</strong><p>${escapeHtml(snapshot.setup_summary?.live_refresh_message || "Поиск hh.ru ещё не запускался.")}</p></div></section>`;
    return;
  }
  root.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div>
          <span class="panel-kicker">Очередь откликов</span>
          <h2>Вакансии, разбитые по трём колонкам</h2>
        </div>
      </div>
      <div class="stack compact board-summary">
        <div class="note"><strong>Статус анализа</strong><p>${escapeHtml(snapshot.analysis_job?.message || "Оценка очереди не запускалась.")}</p></div>
        <div class="note"><strong>Источник</strong><p>${escapeHtml(snapshot.setup_summary?.live_refresh_message || "Источник вакансий ещё не обновлялся.")}</p></div>
        <div class="note"><strong>Лимит откликов</strong><p>${escapeHtml(`Сегодня использовано ${applyLimits.used_today || 0} из ${applyLimits.daily_limit || 200}, осталось ${applyLimits.remaining_today || 0}.`)}</p></div>
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
                      applyBatchJob.running && applyBatchJob.category === key ? "Идёт отклик" : "Откликнуться по всем"
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
                            <div class="vacancy-meta">${escapeHtml(card.company || "компания не указана")} • ${escapeHtml(card.location || "локация не указана")}</div>
                          </div>
                          <span class="score">${escapeHtml(card.score)}</span>
                        </div>
                        <p>${escapeHtml(card.reason_summary || "Краткое пояснение по вакансии ещё не сохранено.")}</p>
                      </article>
                    `,
                    "Пока нет карточек в этой колонке.",
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
          `РЎРµР№С‡Р°СЃ СѓР¶Рµ РёРґРµС‚ РїР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ ${categoryLabel(applyBatchJob.category || "")}. Р”РѕР¶РґРёС‚РµСЃСЊ Р·Р°РІРµСЂС€РµРЅРёСЏ С‚РµРєСѓС‰РµРіРѕ Р·Р°РїСѓСЃРєР°.`,
          `apply-batch-running:${applyBatchJob.category || "unknown"}`,
        );
        renderChatLog();
        return;
      }
      pauseAutoRefresh(15000);
      appendAssistantMessage(`Р—Р°РїСѓСЃРєР°СЋ РїР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ ${categoryLabel(category)}.`, `apply-batch-start:${category}`);
      renderChatLog();
      await handleServerAction("/api/actions/apply-batch", { category });
    }),
  );
}


function detailMetaRows(card, snapshot) {
  const rows = [
    ["РљРѕРјРїР°РЅРёСЏ", card.company || "РЅРµ СѓРєР°Р·Р°РЅР°"],
    ["Р›РѕРєР°С†РёСЏ", card.location || "РЅРµ СѓРєР°Р·Р°РЅР°"],
    ["РљР°С‚РµРіРѕСЂРёСЏ", card.category_label || card.category || "РЅРµ СѓРєР°Р·Р°РЅР°"],
    ["РЎС‡С‘С‚", String(card.score)],
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
                  (reason) => `<article class="reason-card"><strong>${escapeHtml(reason.label || reason.code || "РџСЂРёС‡РёРЅР°")}</strong><p>${escapeHtml(reason.detail || "")}</p></article>`,
                  "РџСѓСЃС‚Рѕ.",
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
    root.innerHTML = `<section class="panel"><div class="empty-state">РЎРЅР°С‡Р°Р»Р° Р·Р°РІРµСЂС€РёС‚Рµ РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ intake-РґРёР°Р»РѕРі, Р·Р°С‚РµРј РѕС‚РєСЂРѕСЋС‚СЃСЏ РєР°СЂС‚РѕС‡РєРё РІР°РєР°РЅСЃРёР№ Рё РґРµС‚Р°Р»СЊРЅС‹Р№ СЂР°Р·Р±РѕСЂ.</div></section>`;
    return;
  }
  const card = currentCard(snapshot);
  if (!card) {
    root.innerHTML = `<section class="panel"><div class="empty-state">Р’С‹Р±РµСЂРё РІР°РєР°РЅСЃРёСЋ РёР· СЃРїРёСЃРєР°, С‡С‚РѕР±С‹ СѓРІРёРґРµС‚СЊ РґРµС‚Р°Р»СЊРЅС‹Р№ СЂР°Р·Р±РѕСЂ Рё РґРµР№СЃС‚РІРёСЏ.</div></section>`;
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
            <span class="panel-kicker">Р”РµС‚Р°Р»СЊРЅС‹Р№ СЂР°Р·Р±РѕСЂ</span>
            <h2>${escapeHtml(card.title)}</h2>
            <p class="panel-lead panel-lead--compact">${escapeHtml((snapshot.selected_resume_title || snapshot.selected_resume_id || "Р РµР·СЋРјРµ РЅРµ РІС‹Р±СЂР°РЅРѕ") + " В· " + formatDate(snapshot.generated_at))}</p>
          </div>
          <div class="detail-top-actions">
            <div class="inline-actions">
              <button class="button button--ghost" type="button" id="open-prev-vacancy" ${prevId ? "" : "disabled"}>${escapeHtml(prevId ? "РџСЂРµРґС‹РґСѓС‰Р°СЏ РІР°РєР°РЅСЃРёСЏ" : "РќРµС‚ РїСЂРµРґС‹РґСѓС‰РµР№")}</button>
              <button class="button button--ghost" type="button" id="open-next-vacancy-top" ${nextId && nextId !== card.id ? "" : "disabled"}>${escapeHtml(nextId && nextId !== card.id ? "РЎР»РµРґСѓСЋС‰Р°СЏ РІР°РєР°РЅСЃРёСЏ" : "РќРµС‚ СЃР»РµРґСѓСЋС‰РµР№")}</button>
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
            <a class="button button--ghost" href="${escapeHtml(card.url || "#")}" target="_blank" rel="noreferrer">РћС‚РєСЂС‹С‚СЊ РЅР° hh.ru</a>
          </div>
        </div>
        <div class="detail-meta-grid">${detailMetaRows(card, snapshot)}</div>
        <div class="note note--decision"><strong>РџРѕС‡РµРјСѓ РїСЂРёРЅСЏС‚Рѕ С‚Р°РєРѕРµ СЂРµС€РµРЅРёРµ</strong><p>${escapeHtml(card.explanation || "РџРѕСЏСЃРЅРµРЅРёРµ РЅРµ СЃРѕС…СЂР°РЅРµРЅРѕ.")}</p></div>
        <div class="detail-block">
          <strong>РўСЂРµС…РєРѕР»РѕРЅРѕС‡РЅС‹Р№ СЂР°Р·Р±РѕСЂ СЃРѕРѕС‚РІРµС‚СЃС‚РІРёСЏ</strong>
          ${renderReasonColumns(card)}
        </div>
        <details class="details-box vacancy-description" ${descriptionOpen ? "open" : ""}>
          <summary>РћРїРёСЃР°РЅРёРµ РІР°РєР°РЅСЃРёРё</summary>
          <div class="description-text">${escapeHtml(card.description || card.summary || "РћРїРёСЃР°РЅРёРµ РІР°РєР°РЅСЃРёРё РЅРµ СЃРѕС…СЂР°РЅРµРЅРѕ.")}</div>
        </details>
      </section>
      <aside class="panel panel--detail-side">
        <div class="panel-head">
          <div>
            <span class="panel-kicker">РћС‚РєР»РёРє</span>
            <h2>Р”РµР№СЃС‚РІРёСЏ РїРѕ РІР°РєР°РЅСЃРёРё</h2>
          </div>
        </div>
        <div class="note"><strong>РўРµРєСѓС‰РµРµ СЂРµС€РµРЅРёРµ</strong><p>${escapeHtml(card.category_label || "РµС‰С‘ РЅРµ РІС‹Р±СЂР°РЅРѕ")}</p><p class="muted">${escapeHtml(feedback.decided_at ? formatDate(feedback.decided_at) : "Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєР°СЏ РѕС†РµРЅРєР°")}</p></div>
        <div class="field">
          <label for="cover-letter-input">РЎРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ РїРёСЃСЊРјРѕ</label>
          <textarea id="cover-letter-input" rows="12" placeholder="Р—РґРµСЃСЊ РјРѕР¶РЅРѕ РѕС‚СЂРµРґР°РєС‚РёСЂРѕРІР°С‚СЊ РїРёСЃСЊРјРѕ РїРµСЂРµРґ РѕС‚РєР»РёРєРѕРј">${escapeHtml(coverLetter)}</textarea>
        </div>
        <div class="cta-stack">
          <button class="button button--ghost" id="save-cover-letter">РЎРѕС…СЂР°РЅРёС‚СЊ РїРёСЃСЊРјРѕ</button>
          <button class="button button--ghost" id="build-apply-plan">РЎРѕР±СЂР°С‚СЊ РїР»Р°РЅ РѕС‚РєР»РёРєР°</button>
          <button class="button button--primary" id="apply-submit">РћС‚РєР»РёРєРЅСѓС‚СЊСЃСЏ</button>
        </div>
        <div class="note"><strong>РЎС‚Р°С‚СѓСЃ РѕС‚РєР»РёРєР°</strong><p>${escapeHtml(feedback.last_apply_message || "РћС‚РєР»РёРє РµС‰Рµ РЅРµ Р·Р°РїСѓСЃРєР°Р»СЃСЏ.")}</p><p class="muted">${escapeHtml(feedback.last_apply_at ? formatDate(feedback.last_apply_at) : "РЅРµС‚ Р·Р°РїСѓСЃРєР°")}</p></div>
        <button class="button button--ghost" type="button" id="open-next-vacancy">${escapeHtml(nextId && nextId !== card.id ? "РЎР»РµРґСѓСЋС‰Р°СЏ РєР°СЂС‚РѕС‡РєР°" : "РћСЃС‚Р°С‚СЊСЃСЏ РЅР° СЌС‚РѕР№ РєР°СЂС‚РѕС‡РєРµ")}</button>
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
    ["Р’С…РѕРґ РІ hh.ru", snapshot.freshness?.timestamps?.hh_login_at, snapshot.freshness?.stale?.hh_login_at],
    ["РљР°С‚Р°Р»РѕРі СЂРµР·СЋРјРµ", snapshot.freshness?.timestamps?.resume_catalog_at, snapshot.freshness?.stale?.resume_catalog_at],
    ["РђРЅРєРµС‚Р°", snapshot.freshness?.timestamps?.intake_at, snapshot.freshness?.stale?.intake_at],
    ["РџСЂР°РІРёР»Р°", snapshot.freshness?.timestamps?.rules_at, snapshot.freshness?.stale?.rules_at],
    ["РђРЅР°Р»РёР·", snapshot.freshness?.timestamps?.analysis_at, snapshot.freshness?.stale?.analysis_at],
  ];
  return rows
    .map(([label, value, stale]) => `<div class="note"><strong>${escapeHtml(label)}</strong><p>${escapeHtml(value ? formatDate(value) : "РЅРµС‚ РґР°РЅРЅС‹С…")}</p><p class="muted">${escapeHtml(stale ? "РґР°РЅРЅС‹Рµ СѓСЃС‚Р°СЂРµР»Рё" : "РґР°РЅРЅС‹Рµ Р°РєС‚СѓР°Р»СЊРЅС‹")}</p></div>`)
    .join("");
}

function renderActivity(snapshot) {
  const root = document.getElementById("activity-view");
  root.innerHTML = `
    <div class="activity-grid">
      <section class="panel">
        <div class="panel-head"><div><span class="panel-kicker">РЎРІРµР¶РµСЃС‚СЊ РґР°РЅРЅС‹С…</span><h2>РљРѕРіРґР° С‡С‚Рѕ РѕР±РЅРѕРІР»СЏР»РѕСЃСЊ</h2></div></div>
        <div class="stack">${freshnessCards(snapshot)}</div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><span class="panel-kicker">РЎРѕР±С‹С‚РёСЏ</span><h2>РџРѕСЃР»РµРґРЅРёРµ РґРµР№СЃС‚РІРёСЏ</h2></div></div>
        <div class="stack">
          ${renderList(snapshot.recent_events || [], (event) => `<article class="history-card"><strong>${escapeHtml(event.kind || "СЃРѕР±С‹С‚РёРµ")}</strong><p>${escapeHtml(event.message || "")}</p><p class="muted">${escapeHtml(formatDate(event.timestamp))}</p></article>`, "РЎРѕР±С‹С‚РёР№ РїРѕРєР° РЅРµС‚.")}
        </div>
      </section>
      <section class="panel panel--wide">
        <div class="panel-head"><div><span class="panel-kicker">Р—Р°РїСѓСЃРєРё</span><h2>РСЃС‚РѕСЂРёСЏ РїСЂРѕРіРѕРЅРѕРІ</h2></div></div>
        <div class="stack">
          ${renderList(snapshot.recent_runs || [], (run) => `<article class="history-card"><strong>${escapeHtml(run.run_id || "run")}</strong><p>${escapeHtml(run.mode || "")} В· ${escapeHtml(run.status || "")}</p><p class="muted">${escapeHtml(formatDate(run.started_at))}</p></article>`, "Р—Р°РїСѓСЃРєРѕРІ РїРѕРєР° РЅРµС‚.")}
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
  repairRenderedText(document.body);
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
    if (root) root.innerHTML = `<section class="panel"><div class="empty-state">РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РіСЂСѓР·РёС‚СЊ РґР°С€Р±РѕСЂРґ: ${escapeHtml(error.message)}</div></section>`;
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

