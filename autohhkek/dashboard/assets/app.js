const tabs = [
  { id: "agent", label: "Агент" },
  { id: "vacancies", label: "Вакансии" },
  { id: "vacancy", label: "Карточка" },
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
  /** @type {null | "chat_llm" | "server_action"} */
  busySource: null,
  pendingActionMessage: "",
  pendingActionFrames: [],
  pendingActionStartedAt: 0,
  pendingTickerId: 0,
  workspaceScrollTopByTab: { agent: 0, vacancies: 0, vacancy: 0 },
  chatScrollTop: 0,
  chatWasNearBottom: true,
  intakeOverlayScrollTop: 0,
  intakeOverlayLogScrollTop: 0,
  autoRefreshPauseUntil: 0,
  refreshInFlight: false,
  announcements: new Set(),
  bgJobLog: {
    analysisSession: "",
    lastAnalysisMsg: "",
    refreshSession: "",
    refreshLineIdx: 0,
    sawRefreshRunning: false,
  },
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
  if (window.innerWidth <= 1240) return Math.max(280, Math.min(window.innerWidth - 32, safeWidth));
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
  const intakeOverlay = document.getElementById("intake-overlay");
  const intakeOverlayPanel = intakeOverlay?.querySelector(".intake-overlay__panel");
  const intakeOverlayLog = document.getElementById("intake-overlay-log");
  if (workspace) {
    state.workspaceScrollTopByTab[state.activeTab] = workspace.scrollTop;
  }
  if (chatLog) {
    state.chatScrollTop = chatLog.scrollTop;
    state.chatWasNearBottom = chatLog.scrollTop + chatLog.clientHeight >= chatLog.scrollHeight - 24;
  }
  if (intakeOverlayPanel) {
    state.intakeOverlayScrollTop = intakeOverlayPanel.scrollTop;
  }
  if (intakeOverlayLog) {
    state.intakeOverlayLogScrollTop = intakeOverlayLog.scrollTop;
  }
}

function restoreScrollState() {
  const workspace = currentWorkspace();
  const chatLog = document.getElementById("chat-log");
  const intakeOverlay = document.getElementById("intake-overlay");
  const intakeOverlayPanel = intakeOverlay?.querySelector(".intake-overlay__panel");
  const intakeOverlayLog = document.getElementById("intake-overlay-log");
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
  if (intakeOverlayPanel) {
    const nextTop = state.intakeOverlayScrollTop || 0;
    intakeOverlayPanel.scrollTop = nextTop;
    window.requestAnimationFrame(() => {
      intakeOverlayPanel.scrollTop = nextTop;
    });
  }
  if (intakeOverlayLog) {
    const nextTop = state.intakeOverlayLogScrollTop || 0;
    intakeOverlayLog.scrollTop = nextTop;
    window.requestAnimationFrame(() => {
      intakeOverlayLog.scrollTop = nextTop;
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

function injectVacancySectionBreaks(text) {
  let t = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const needles = [
    "Обязанности",
    "Требования",
    "Условия",
    "Задачи",
    "О компании",
    "О вакансии",
    "Чем предстоит",
    "Чем предстоит заниматься",
    "Мы предлагаем",
    "Мы ожидаем",
    "Ключевые требования",
    "Что нужно от вас",
    "Что предстоит делать",
    "У нас для вас",
    "График работы",
    "Оформление",
    "Выплаты",
    "Опыт работы",
    "Тип занятости",
  ];
  for (const w of needles) {
    const re = new RegExp(`([\\.\\!\\?\\n])\\s*(${w})`, "gi");
    t = t.replace(re, "$1\n\n$2");
  }
  t = t.replace(/\s+(Обязанности|Требования|Условия)\s*:/gi, "\n\n$1:");
  return t.trim();
}

function formatVacancyDescriptionHtml(raw) {
  const repaired = repairText(String(raw ?? ""));
  if (!repaired.trim()) {
    return `<p class="vacancy-desc-lead">${escapeHtml("Полный текст вакансии пока не сохранён. Нажмите «Ещё раз спарсить вакансии».")}</p>`;
  }
  const expanded = injectVacancySectionBreaks(repaired);
  const chunks = expanded
    .split(/\n{2,}/)
    .map((c) => c.trim())
    .filter(Boolean);
  if (chunks.length <= 1 && !repaired.includes("\n")) {
    const sentences = repaired.split(/(?<=[\\.\\!\\?])\s+(?=[А-ЯЁA-Z0-9«"„])/);
    return `<div class="vacancy-desc-prose">${sentences
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => `<p>${escapeHtml(s)}</p>`)
      .join("")}</div>`;
  }
  return chunks
    .map((chunk) => {
      const m = chunk.match(/^([^:\n]{1,120}):\s*([\s\S]*)$/);
      if (m && m[1].trim().length <= 120 && !m[1].includes(".") && m[2].trim()) {
        const title = m[1].trim();
        const body = m[2].trim();
        const paras = body
          .split(/\n+/)
          .map((p) => p.trim())
          .filter(Boolean)
          .map((p) => `<p>${escapeHtml(p)}</p>`)
          .join("");
        return `<section class="vacancy-desc-section"><h4 class="vacancy-desc-heading">${escapeHtml(title)}</h4><div class="vacancy-desc-body">${paras}</div></section>`;
      }
      const paras = chunk
        .split(/\n+/)
        .map((p) => p.trim())
        .filter(Boolean)
        .map((p) => `<p>${escapeHtml(p)}</p>`)
        .join("");
      return `<section class="vacancy-desc-section"><div class="vacancy-desc-body">${paras}</div></section>`;
    })
    .join("");
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

function mojibakeMarkerCount(value) {
  const text = String(value ?? "");
  let markers = 0;
  for (let index = 0; index < text.length - 1; index += 1) {
    const first = text[index];
    const second = text[index + 1];
    if (!second) continue;
    const secondCode = second.charCodeAt(0);
    if ((first === "Р" || first === "С") && !/[А-Яа-яЁё\s]/.test(second) && secondCode > 127) markers += 1;
    if ((first === "Ð" || first === "Ñ") && secondCode > 127) markers += 1;
  }
  if (text.includes("�")) markers += 3;
  if (text.includes("пїЅ")) markers += 3;
  if (text.includes("\\x")) markers += 2;
  if (text.includes("07@")) markers += 2;
  return markers;
}

function tryDecodeMojibake(value) {
  if (!value || mojibakeMarkerCount(value) < 2) return value;
  try {
    const bytes = Uint8Array.from(Array.from(value, (char) => char.charCodeAt(0) & 0xff));
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  } catch {
    return value;
  }
}

function looksBetter(original, candidate) {
  if (!candidate || candidate === original) return false;
  const brokenBefore = mojibakeMarkerCount(original) + (original.match(/\?{3,}/g) || []).length * 4;
  const brokenAfter = mojibakeMarkerCount(candidate) + (candidate.match(/\?{3,}/g) || []).length * 4;
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

function hhLoginReady(snapshot) {
  return Boolean(snapshot?.hh_login?.state_file_exists);
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

function optimisticIntakeRestartSnapshot(snapshot) {
  if (!snapshot) return null;
  const dialog = intakeDialogState(snapshot);
  const questions = Array.isArray(dialog.questions) ? dialog.questions : [];
  if (!questions.length) return snapshot;
  return {
    ...snapshot,
    intake_dialog: {
      ...dialog,
      active: true,
      completed: false,
      step_index: 0,
      answers: {},
    },
    setup_summary: {
      ...(snapshot.setup_summary || {}),
      intake_ready: false,
      intake_dialog_completed: false,
      intake_confirmed: false,
      intake_missing: ["dialog"],
    },
  };
}

function intakePriorityLabel(question) {
  if (!question) return "Сначала подключаем hh.ru и читаем резюме";
  const importance = String(question?.importance || "").toLowerCase();
  if (importance === "critical") return "Сейчас фиксируем обязательные критерии";
  if (importance === "important") return "Теперь уточняем важные предпочтения";
  return "В конце добираем тонкие пожелания";
}

function intakeResumeFacts(snapshot, context = {}) {
  const analysis = snapshot.intake?.resume_intake_analysis || {};
  const profileSync = snapshot.profile_sync || {};
  const profileSyncReady = ["updated", "no_changes"].includes(profileSync.status || "");
  const resumeTitle = context.resume_title || (profileSyncReady ? profileSync.resume_title || "" : "");
  const roles = Array.isArray(context.inferred_roles) && context.inferred_roles.length
    ? context.inferred_roles
    : Array.isArray(analysis.inferred_roles) ? analysis.inferred_roles : [];
  const skills = Array.isArray(context.detected_skills) && context.detected_skills.length
    ? context.detected_skills
    : Array.isArray(analysis.core_skills) ? analysis.core_skills : [];
  const missingTopics = Array.isArray(context.missing_topics) && context.missing_topics.length
    ? context.missing_topics
    : Array.isArray(analysis.missing_topics) ? analysis.missing_topics : [];
  const domains = Array.isArray(context.detected_domains) && context.detected_domains.length
    ? context.detected_domains
    : Array.isArray(analysis.domains) ? analysis.domains : [];
  const experienceYears = Number(context.detected_experience || snapshot.intake?.anamnesis?.experience_years || 0);
  return { resumeTitle, roles, skills, missingTopics, domains, experienceYears };
}

function intakeResumeSummary(snapshot, context = {}) {
  const facts = intakeResumeFacts(snapshot, context);
  const { resumeTitle, roles, skills, domains, experienceYears } = facts;
  const hasResumeFacts = Boolean(resumeTitle || roles.length || skills.length);
  if (hasResumeFacts) {
    const extraLine = domains.length
      ? `Домены: ${domains.slice(0, 4).join(", ")}`
      : experienceYears > 0
        ? `Опыт: ${experienceYears} лет`
        : "";
    return {
      title: "Что уже понял из резюме",
      lines: [
        resumeTitle || "Резюме подключено, но заголовок пока не распознан.",
        roles.length ? `Роли: ${roles.join(", ")}` : "Роли из резюме пока не выделены.",
        skills.length ? `Навыки: ${skills.slice(0, 8).join(", ")}` : "Навыки из резюме пока не выделены.",
        extraLine,
      ].filter(Boolean),
    };
  }
  if (hhLoginReady(snapshot) && snapshot?.selected_resume_id) {
    return {
      title: "Что уже происходит",
      lines: [
        "Резюме уже выбрано.",
        "Агент дочитывает страницу целиком и вытаскивает структуру.",
        "После этого стартует короткий опросник только по пробелам.",
      ],
    };
  }
  if (hhLoginReady(snapshot)) {
    return {
      title: "Что уже происходит",
      lines: [
        "Логин уже есть.",
        "Осталось выбрать одно резюме для этого аккаунта.",
        "Дальше разбор и опрос пойдут автоматически.",
      ],
    };
  }
  return {
    title: "С чего начинаем",
    lines: [
      "1. Войдите в hh.ru под нужным аккаунтом.",
      "2. Дайте агенту прочитать выбранное резюме и вытащить факты автоматически.",
      "3. Только потом доберём короткими вопросами то, чего реально не хватает.",
    ],
  };
}

function intakeQuestionFallback(snapshot, context = {}, options = {}) {
  const short = Boolean(options.short);
  const facts = intakeResumeFacts(snapshot, context);
  const hasResumeFacts = Boolean((facts.resumeTitle || "").trim() || facts.roles.length || facts.skills.length);
  if (!hasResumeFacts) {
    if (hhLoginReady(snapshot) && snapshot?.selected_resume_id) {
      return short
        ? "Резюме уже выбрано. Можно запускать первичное уточнение."
        : "Резюме уже выбрано. Если фактов ещё мало, агент доберёт недостающее на первом шаге и затем задаст уточняющие вопросы.";
    }
    return short
      ? "Сначала войдите в hh.ru и выберите резюме"
      : "Сначала войдите в hh.ru и выберите резюме. Агент сам вытащит максимум фактов, а потом задаст только недостающие вопросы.";
  }
  return short
    ? "Резюме уже прочитано. Можно перейти к первому уточняющему вопросу."
    : "Агент уже прочитал резюме. Дальше пойдут только уточняющие вопросы по реально недостающим пунктам.";
}

function intakeQuestionHint(snapshot) {
  if (hhLoginReady(snapshot) && snapshot?.selected_resume_id) {
    return "Резюме уже выбрано. Агент должен дочитать его, собрать факты и только потом задавать короткие уточняющие вопросы.";
  }
  return "Без резюме агент не будет задавать осмысленные вопросы. Сначала нужен вход в hh.ru и выбор резюме, потом он сам соберёт базу и задаст только то, чего не хватает.";
}

function intakeQuestionExample(snapshot) {
  if (hhLoginReady(snapshot) && snapshot?.selected_resume_id) {
    return "Можно запускать первичное доуточнение. Если фактов из резюме ещё мало, агент сначала доберёт их автоматически.";
  }
  if (hhLoginReady(snapshot)) {
    return "Выберите одно резюме. После выбора агент сам дочитает его и соберёт факты.";
  }
  return "Сначала подключите hh.ru, потом выберите резюме. Остальное агент сделает сам.";
}

function intakeWorkflowSummary(snapshot, context = {}) {
  const facts = intakeResumeFacts(snapshot, context);
  const profileSyncReady = ["updated", "no_changes"].includes(snapshot?.profile_sync?.status || "");
  const selectedResume = Boolean(snapshot?.selected_resume_id);
  const loginReady = hhLoginReady(snapshot);
  const hasFacts = Boolean((facts.resumeTitle || "").trim() || facts.roles.length || facts.skills.length);
  const dialog = intakeDialogState(snapshot);
  if (!loginReady) {
    return {
      title: "Что делаем сейчас",
      lines: [
        "Подключаем hh.ru под нужным аккаунтом.",
        "После логина подтягиваем список резюме.",
        "Потом выбираем одно резюме и разбираем его автоматически.",
      ],
    };
  }
  if (!selectedResume) {
    return {
      title: "Что делаем сейчас",
      lines: [
        "Логин уже есть.",
        "Теперь нужно выбрать одно резюме для этого аккаунта.",
        "После выбора агент читает полный текст резюме и собирает факты без ручного ввода.",
      ],
    };
  }
  if (!profileSyncReady && !hasFacts) {
    return {
      title: "Что делаем сейчас",
      lines: [
        "Резюме уже выбрано.",
        "Следующий шаг: дочитать полную страницу резюме, вытащить блоки, опыт, навыки, ссылки и ограничения.",
        "После этого строим примерный опросник и идём по нему короткими уточнениями.",
      ],
    };
  }
  if (dialog.active) {
    return {
      title: "Что делаем сейчас",
      lines: [
        "База фактов из резюме уже собрана.",
        "Сейчас идём по короткому опроснику только по реально недостающим пунктам.",
        `Шаг ${Number(dialog.step_index || 0) + 1} из ${Number(dialog.total_steps || (dialog.questions || []).length || 1)}.`,
      ],
    };
  }
  return {
    title: "Что делаем сейчас",
    lines: [
      "Резюме уже разобрано.",
      "Опросник уже собран на основе фактов из резюме.",
      "Можно запускать уточнение и двигаться по вопросам.",
    ],
  };
}

function intakePipelineCards(snapshot, context = {}) {
  const facts = intakeResumeFacts(snapshot, context);
  const loginReady = hhLoginReady(snapshot);
  const selectedResume = Boolean(snapshot?.selected_resume_id);
  const profileSyncReady = ["updated", "no_changes"].includes(snapshot?.profile_sync?.status || "");
  const hasFacts = Boolean((facts.resumeTitle || "").trim() || facts.roles.length || facts.skills.length);
  const dialog = intakeDialogState(snapshot);
  return [
    {
      label: "Логин",
      value: loginReady ? "Ок" : "Нужен",
      note: loginReady ? "hh.ru уже подключен." : "Подключаем нужный hh-аккаунт.",
      tone: loginReady ? "good" : "warn",
    },
    {
      label: "Резюме",
      value: selectedResume ? "Выбрано" : "Не выбрано",
      note: selectedResume ? (snapshot.selected_resume_title || snapshot.selected_resume_id || "Резюме зафиксировано.") : "Выберите одно резюме для текущего аккаунта.",
      tone: selectedResume ? "good" : "warn",
    },
    {
      label: "Разбор",
      value: profileSyncReady || hasFacts ? "Готов" : selectedResume ? "Читаем" : "Ждёт",
      note: profileSyncReady || hasFacts ? "Факты из резюме уже собраны." : selectedResume ? "Читаем все блоки и извлекаем структуру." : "Сначала нужен выбор резюме.",
      tone: profileSyncReady || hasFacts ? "good" : selectedResume ? "neutral" : "warn",
    },
    {
      label: "Опросник",
      value: dialog.active ? `Шаг ${Number(dialog.step_index || 0) + 1}` : profileSyncReady || hasFacts ? "Готов" : "Ждёт",
      note: dialog.active ? `Из ${(dialog.questions || []).length || 1} вопросов.` : profileSyncReady || hasFacts ? "Идём только по пробелам." : "Сначала нужен полный разбор.",
      tone: dialog.active || profileSyncReady || hasFacts ? "good" : "warn",
    },
  ];
}

function intakeResumeIntel(snapshot, context = {}) {
  const facts = intakeResumeFacts(snapshot, context);
  const analysis = snapshot.intake?.resume_intake_analysis || {};
  const missing = formatIntakeMissing(snapshot.setup_summary?.intake_missing || []);
  return [
    ["Заголовок", facts.resumeTitle || "ещё не извлекли"],
    ["Сводка", String(analysis.summary || "").trim() || "ещё не собрали"],
    ["Роли", facts.roles.length ? facts.roles.join(", ") : "ещё не выделили"],
    ["Навыки", facts.skills.length ? facts.skills.slice(0, 8).join(", ") : "ещё не выделили"],
    ["Домены", facts.domains.length ? facts.domains.slice(0, 4).join(", ") : "не определены"],
    ["Сильные стороны", Array.isArray(analysis.strengths) && analysis.strengths.length ? analysis.strengths.slice(0, 4).join(", ") : "ещё не выделили"],
    ["Ограничения", Array.isArray(analysis.likely_constraints) && analysis.likely_constraints.length ? analysis.likely_constraints.slice(0, 4).join(", ") : "ещё не выделили"],
    ["Опыт", facts.experienceYears > 0 ? `${facts.experienceYears} лет` : "не определён"],
    ["Пробелы", missing.length ? missing.slice(0, 4).join(", ") : "критичных пробелов нет"],
    ["Синк", snapshot?.profile_sync?.updated_at ? `обновлён ${formatDate(snapshot.profile_sync.updated_at)}` : "ещё не запускался"],
  ];
}

function intakeNeedsResumeSelection(snapshot) {
  return hhLoginReady(snapshot) && !snapshot?.selected_resume_id;
}

function intakeFormMode(snapshot, dialog, context = {}) {
  const confirmationMode = Boolean(dialog.completed || (snapshot.setup_summary?.intake_dialog_completed && !snapshot.setup_summary?.intake_confirmed));
  if (confirmationMode) return "confirm";
  if (!hhLoginReady(snapshot)) return "login";
  if (intakeNeedsResumeSelection(snapshot)) return "resume";
  if (dialog.active) return "answer";
  const facts = intakeResumeFacts(snapshot, context);
  if (snapshot?.selected_resume_id) return "start";
  if (activeIntakeQuestion(snapshot) || Boolean((facts.resumeTitle || "").trim() || facts.roles.length || facts.skills.length)) return "start";
  return "wait";
}

function categoryLabel(category) {
  return categoryMeta[category]?.label || category || "неизвестно";
}

function chatLockedReason(snapshot) {
  if (!snapshot) return "";
  if (!isIntakeReady(snapshot) && !hhLoginReady(snapshot)) {
    return "Сначала войдите в hh.ru. Пока логин не завершён, чат в onboarding заблокирован: агенту нужно сначала прочитать резюме.";
  }
  return "";
}

function currentPendingStatus() {
  if (!state.pendingActionMessage) return "";
  const frames = Array.isArray(state.pendingActionFrames) ? state.pendingActionFrames.filter(Boolean) : [];
  if (!frames.length || !state.pendingActionStartedAt) return state.pendingActionMessage;
  const elapsedMs = Math.max(0, Date.now() - state.pendingActionStartedAt);
  const frameIndex = Math.floor(elapsedMs / 4200) % frames.length;
  return frames[frameIndex] || state.pendingActionMessage;
}

function startPendingTicker() {
  if (state.pendingTickerId) return;
  state.pendingTickerId = window.setInterval(() => {
    if (!state.pendingActionMessage) {
      stopPendingTicker();
      return;
    }
    renderChatLog();
    if (state.snapshot) renderHero(state.snapshot);
  }, 1600);
}

function stopPendingTicker() {
  if (!state.pendingTickerId) return;
  window.clearInterval(state.pendingTickerId);
  state.pendingTickerId = 0;
}

function setPendingStatus(message, frames = []) {
  state.pendingActionMessage = String(message || "").trim();
  state.pendingActionFrames = Array.isArray(frames) ? frames.filter(Boolean) : [];
  state.pendingActionStartedAt = state.pendingActionMessage ? Date.now() : 0;
  if (state.pendingActionMessage && state.pendingActionFrames.length) startPendingTicker();
  else stopPendingTicker();
}

function pendingFramesForAction(kind) {
  const variants = {
    chat: [
      "Получил сообщение. Разбираю, что именно вы хотите.",
      "Сверяю текущий шаг пайплайна и сохранённое состояние.",
      "Проверяю, не конфликтует ли команда с текущим режимом.",
      "Собираю контекст по резюме, фильтрам и текущей очереди.",
      "Смотрю, нужно ли обновить hh-данные перед ответом.",
      "Сверяю активный аккаунт, выбранное резюме и состояние пайплайна.",
      "Готовлю короткий ответ без лишнего шума.",
      "Проверяю, нужно ли запускать действие или достаточно правки состояния.",
      "Подтягиваю последние изменения из snapshot и журнала событий.",
      "Готовлю следующий осмысленный шаг по пайплайну.",
    ],
    "hh-login": [
      "Проверяю текущую hh-сессию и готовлю окно входа.",
      "Поднимаю браузер для авторизации на hh.ru.",
      "Жду, пока пользователь завершит вход и подтверждение.",
    ],
    resume: [
      "Читаю выбранное резюме и вытаскиваю факты в профиль.",
      "Сверяю найденные данные с текущими правилами.",
      "Обновляю сводку профиля и контекст для поиска.",
    ],
    analyze: [
      "Собираю входные данные для анализа вакансий.",
      "Проверяю hh-сессию, резюме и текущий search plan.",
      "Освежаю очередь вакансий с hh.ru перед оценкой.",
      "Проверяю, не сузили ли фильтры выдачу слишком рано.",
      "Подтягиваю полный текст вакансий для локального разбора.",
      "Прогоняю вакансии через оценку и сортировку.",
      "Сверяю причины fit/doubt/no-fit по текущим правилам.",
      "Собираю обновлённые карточки и объяснения.",
      "Обновляю counters, source stats и board state.",
      "Готовлю финальный статус анализа для UI.",
    ],
    "refresh-vacancies": [
      "Открываю hh-поиск по активному резюме.",
      "Добираю новые карточки вакансий из выдачи.",
      "Проверяю, не изменилась ли пагинация и total count.",
      "Сохраняю обновлённую очередь без повторов.",
      "Готовлю свежий source state для следующего анализа.",
      "Сверяю новые вакансии с уже сохранёнными.",
      "Фиксирую search URL и статистику обновления.",
      "Проверяю, появились ли новые страницы и новые id.",
      "Подтягиваю видимый текст карточек для описаний.",
      "Готовлю обновлённый статус refresh.",
    ],
    apply: [
      "Проверяю карточку и текущий статус отклика.",
      "Готовлю шаги отклика и сопроводительное письмо.",
      "Сохраняю результат и обновляю состояние вакансии.",
    ],
    generic: [
      "Принял задачу — кручу шестерёнки на сервере.",
      "Сверяю состояние с тем, что вы уже сделали в интерфейсе.",
      "Перечитываю snapshot, чтобы ничего не потерять по дороге.",
      "На секунду задерживаю автообновление — не дёргаем UI зря.",
      "Проверяю лимиты и блокировки, чтобы не сломать пайплайн.",
      "Собираю ответ так, чтобы в чате не было сюрпризов.",
      "Если сеть тянет — это не зависание, я жду бэкенд.",
      "Почти готово: осталось применить изменения к состоянию.",
      "Параллельно думаю, что показать вам следующим шагом.",
      "Готовлю обновление карточек — сейчас всё подтянется.",
    ],
  };
  return variants[kind] || variants.generic;
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
      title: "1. Вход в hh.ru",
      status: loginRunning ? "active" : hasLogin ? "completed" : "active",
      summary: loginRunning ? "Открыто окно hh.ru для входа и подтверждения сессии." : hasLogin ? "Сессия hh.ru уже активна." : "Нужно открыть hh.ru и пройти вход заново.",
      detail: snapshot.hh_login?.message || "Используйте этот шаг для проверки входа в hh.ru, прежде чем запускать автоматические действия.",
      action: hasLogin && !loginRunning ? null : { id: "hh-login", label: "Открыть hh.ru" },
    },
    {
      id: "resume",
      title: "2. Выбор резюме",
      status: !hasLogin ? "blocked" : selectedResume ? "completed" : "active",
      summary: !hasLogin
        ? "Пока нет входа в hh.ru, резюме недоступны."
        : selectedResume
          ? `Для поиска выбрано резюме: ${snapshot.selected_resume_title || snapshot.selected_resume_id}.`
          : multipleResumes
            ? "На hh.ru найдено несколько резюме. Нужно выбрать одно для поиска."
            : hhResumes.length
              ? "Резюме найдено, его можно использовать для live search."
              : "Нужно обновить список резюме с hh.ru.",
      detail: snapshot.setup_summary?.live_refresh_message || "Этот шаг нужен, чтобы агент опирался на правильное резюме и актуальный профиль кандидата.",
      action: !hasLogin ? null : selectedResume ? null : { id: "hh-resumes", label: "Обновить список резюме" },
    },
    {
      id: "intake",
      title: "3. Обязательный intake-диалог",
      status: !selectedResume && multipleResumes ? "blocked" : intakeReady ? "completed" : "active",
      summary: intakeReady
        ? "Критичные требования, ограничения и предпочтения уже подтверждены."
        : intakeDialogCompleted && !intakeConfirmed
          ? "Диалог завершён, но правила ещё нужно подтвердить перед запуском поиска."
          : intakeDialogCompleted
            ? "Основные ответы собраны, но подтверждение ещё не завершено."
            : "Сначала нужно пройти короткий обязательный диалог о целях поиска и фильтрах.",
      detail: intakeReady
        ? "После этого шага можно собирать профиль, фильтры, анализ и переходить к откликам."
        : intakeDialogCompleted && !intakeConfirmed
          ? "Проверьте итоговые правила поиска и подтвердите их. До подтверждения дальнейшие шаги заблокированы."
          : "Агент соберёт недостающие ограничения, формат работы, ожидания по роли и ключевые стоп-факторы.",
      action: !hasLogin || !selectedResume
        ? null
        : intakeDialogCompleted && !intakeConfirmed
          ? { id: "confirm-intake", label: "Подтвердить правила" }
          : { id: "start-intake", label: intakeDialogCompleted ? "Перезапустить диалог" : "Начать уточнение" },
    },
    {
      id: "profile",
      title: "4. Синхронизация профиля и правил",
      status: !intakeReady ? "blocked" : resumeDraftReady && profileSyncReady && rulesReady ? "completed" : "active",
      summary: resumeDraftReady && profileSyncReady && rulesReady
        ? "Профиль, черновик резюме и правила уже синхронизированы."
        : "Нужно обновить профиль, резюме и производные правила поиска.",
      detail: intakeStructuredReady
        ? snapshot.profile_sync?.message || "Этот шаг синхронизирует профиль кандидата, черновик резюме и правила отбора."
        : "Пока шаг недоступен: сначала завершите обязательный intake-диалог.",
      action: !intakeReady ? null : { id: "resume-sync", label: "Обновить профиль" },
    },
    {
      id: "filters",
      title: "5. Фильтры и источник вакансий",
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
        ? "Нельзя идти дальше, пока не завершён обязательный intake."
        : !selectedResume
          ? "Нужно выбрать резюме для live search."
          : filterPlanReady && vacanciesLoaded
            ? hhTotal
              ? `На hh.ru найдено ${hhTotal} вакансий, в локальной очереди ${snapshot.counts?.total_vacancies || 0}.`
              : `Фильтр собран, в локальной очереди ${snapshot.counts?.total_vacancies || 0} вакансий.`
            : filterPlanReady
              ? "Фильтры готовы, теперь можно запускать обновление и анализ вакансий."
              : "Нужно построить фильтры и запрос hh-поиска для резюме.",
      detail: snapshot.filter_plan?.search_text
        ? snapshot.setup_summary?.live_refresh_stats?.search_url
          ? `Поисковый текст: ${snapshot.filter_plan.search_text}. Подготовлен URL hh-поиска и параметры запроса.`
          : `Поисковый текст: ${snapshot.filter_plan.search_text}.`
        : "Фильтры ещё не собраны на основе текущего резюме и подтверждённых правил.",
      action: !intakeReady
        ? null
        : !selectedResume
          ? null
          : filterPlanReady
            ? { id: "analyze", label: "Запустить анализ" }
            : { id: "plan-filters", label: "Собрать фильтры" },
    },
    {
      id: "assessment",
      title: "6. Оценка по 3 колонкам",
      status: !intakeReady ? "pending" : analyzing ? "active" : assessedCount > 0 ? "completed" : vacanciesLoaded ? "active" : "pending",
      summary: !intakeReady
        ? "Сначала завершите intake и только потом переходите к оценке."
        : analyzing
          ? snapshot.analysis_job?.message || "Идёт анализ вакансий."
          : assessedCount > 0
            ? `Оценено ${assessedCount} вакансий: ${snapshot.counts?.fit || 0} / ${snapshot.counts?.doubt || 0} / ${snapshot.counts?.no_fit || 0}.`
            : "После анализа вакансии появятся в трёх колонках.",
      detail: assessedCount > 0
        ? "Можно перейти к карточкам, ручной корректировке решений и запуску отклика по приоритетным вакансиям."
        : "Подходит = можно откликаться, Сомневаюсь = нужен ручной разбор, Не подходит = отбрасываем.",
      action: assessedCount > 0 ? { id: "open-vacancies", label: "Открыть вакансии" } : vacanciesLoaded ? { id: "analyze", label: "Запустить оценку" } : null,
    },
  ];
}

function localBrowserCapability(snapshot = state.snapshot) {
  return snapshot?.capability_summary || {};
}

function localBrowserReady(snapshot = state.snapshot) {
  return Boolean(localBrowserCapability(snapshot).local_playwright_ready);
}

function localBrowserErrorMessage(snapshot = state.snapshot) {
  const capabilities = localBrowserCapability(snapshot);
  return (
    capabilities.local_playwright_launch_error ||
    capabilities.local_playwright_subprocess_error ||
    "Локальный Playwright-браузер недоступен в текущем окружении."
  );
}

function actionAvailability(action, snapshot = state.snapshot) {
  const actionId = String(action?.id || "");
  const localBrowserActions = new Set(["hh-resumes", "resume-sync"]);
  if (localBrowserActions.has(actionId) && !localBrowserReady(snapshot)) {
    return { disabled: true, reason: localBrowserErrorMessage(snapshot) };
  }
  return { disabled: false, reason: "" };
}


function currentPipelineStep(snapshot) {
  const pipeline = buildPipeline(snapshot);
  return pipeline.find((step) => step.status === "active") || pipeline.find((step) => step.status === "blocked") || pipeline[pipeline.length - 1];
}

function collectQuickActions(snapshot) {
  if (!isIntakeReady(snapshot)) return [];
  if (snapshot.analysis_job?.running) return [];
  return [];
}


function appendAssistantMessage(text, key = "") {
  if (!text) return;
  if (key && state.announcements.has(key)) return;
  if (key) state.announcements.add(key);
  const last = state.chatHistory[state.chatHistory.length - 1];
  if (last?.role === "assistant" && last?.text === text) return;
  state.chatHistory.push({ role: "assistant", text });
}

let progressPollTimer = 0;
function scheduleProgressPoll() {
  if (progressPollTimer) return;
  progressPollTimer = window.setTimeout(() => {
    progressPollTimer = 0;
    void refresh();
  }, 1300);
}

/** Last known server line for background work (snapshot polling), not client-side placeholder copy. */
function liveDashboardActivityLine(snapshot) {
  if (!snapshot) return "";
  const rj = snapshot.refresh_job;
  if (rj?.running) {
    const lines = Array.isArray(rj.log_lines) ? rj.log_lines.map((x) => String(x || "").trim()).filter(Boolean) : [];
    if (lines.length) return lines[lines.length - 1];
    const rm = String(rj.message || "").trim();
    if (rm) return rm;
    return "Парсинг hh.ru…";
  }
  const aj = snapshot.analysis_job;
  if (aj?.running) {
    const am = String(aj.message || "").trim();
    if (am) return am;
    return "Анализ вакансий…";
  }
  const ab = snapshot.apply_batch_job;
  if (ab?.running) {
    const bm = String(ab.message || "").trim();
    if (bm) return bm;
    return "Пакетный отклик…";
  }
  const hl = snapshot.hh_login;
  if (hl?.running) {
    const hm = String(hl.message || "").trim();
    if (hm) return hm;
    return "Вход в hh.ru…";
  }
  const bs = snapshot.bootstrap;
  if (bs?.running) {
    const bsm = String(bs.message || "").trim();
    if (bsm) return bsm;
    return "Первичная настройка…";
  }
  return "";
}

function ingestBackgroundJobLogs(snapshot) {
  if (!snapshot) return;
  const aj = snapshot.analysis_job;
  const analysisSession = String(aj?.started_at || "");
  if (analysisSession && state.bgJobLog.analysisSession !== analysisSession) {
    state.bgJobLog.analysisSession = analysisSession;
    state.bgJobLog.lastAnalysisMsg = "";
  }
  if (aj?.running && aj.message && aj.message !== state.bgJobLog.lastAnalysisMsg) {
    state.bgJobLog.lastAnalysisMsg = aj.message;
    appendAssistantMessage(`[анализ вакансий] ${aj.message}`, `analysis-live:${analysisSession}:${String(aj.message)}`);
  }

  const rj = snapshot.refresh_job;
  const refreshSession = String(rj?.started_at || "");
  if (refreshSession && state.bgJobLog.refreshSession !== refreshSession) {
    state.bgJobLog.refreshSession = refreshSession;
    state.bgJobLog.refreshLineIdx = 0;
    state.bgJobLog.sawRefreshRunning = false;
  }
  if (rj?.running) {
    state.bgJobLog.sawRefreshRunning = true;
  }
  const lines = Array.isArray(rj?.log_lines) ? rj.log_lines : [];
  for (let i = state.bgJobLog.refreshLineIdx; i < lines.length; i += 1) {
    const line = String(lines[i] || "").trim();
    if (!line) continue;
    appendAssistantMessage(`[парсинг hh.ru] ${line}`, `refresh-log:${refreshSession}:${i}`);
  }
  state.bgJobLog.refreshLineIdx = lines.length;

  if (state.bgJobLog.sawRefreshRunning && rj && !rj.running && refreshSession) {
    state.bgJobLog.sawRefreshRunning = false;
    const finalMsg = String(rj.message || "").trim() || "Парсинг вакансий завершён.";
    appendAssistantMessage(`[парсинг hh.ru] ${finalMsg}`, `refresh-final:${refreshSession}`);
  }
}

function announceSnapshotChanges(snapshot, previousSnapshot) {
  if (!previousSnapshot) {
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
  if (!previousSnapshot.pending_rule_edit?.markdown && snapshot.pending_rule_edit?.markdown) {
    appendAssistantMessage("Подготовил черновик правки правил. Проверьте diff и подтвердите изменение в чате.", `rules-draft:${snapshot.pending_rule_edit.filename || "draft"}`);
  }
}

function renderHero(snapshot) {
  const mode = snapshot.runtime_settings?.dashboard_mode || "analyze";
  const backend = snapshot.runtime_settings?.llm_backend || "openrouter";
  const step = currentPipelineStep(snapshot);
  const selectedResume = snapshot.selected_resume_title || snapshot.selected_resume_id || "не выбрано";
  const pendingLine = currentPendingStatus();
  const liveLine = liveDashboardActivityLine(snapshot);
  const summaryEl = document.getElementById("hero-summary");
  if (summaryEl) {
    if (liveLine) {
      summaryEl.textContent = liveLine;
    } else if (state.isBusy && pendingLine) {
      summaryEl.textContent = pendingLine;
    } else {
      summaryEl.textContent =
        step?.summary || snapshot.next_recommended_action?.reason || "Дашборд показывает текущее состояние поиска и очередь вакансий.";
    }
  }
  document.getElementById("hero-next-action").textContent = step?.title || snapshot.next_recommended_action?.label || "Ожидаю действие";
  document.getElementById("hero-next-reason").textContent = `Режим: ${mode}. Модельный backend: ${backend}. Резюме: ${selectedResume}.`;
  document.getElementById("hero-runtime").textContent = `${backend} В· ${mode}`;
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
    .map((action) => {
      const availability = actionAvailability(action);
      return `
          <button
            class="button ${extraClass} ${escapeHtml(action.className || "")}"
            type="button"
            data-dashboard-action="${escapeHtml(action.id || "")}"
            ${action.chatPrompt ? `data-chat-prompt="${escapeHtml(action.chatPrompt)}"` : ""}
            ${availability.reason ? `title="${escapeHtml(availability.reason)}"` : ""}
            ${availability.disabled ? "disabled" : ""}
          >
            ${escapeHtml(action.label)}
          </button>
        `;
    })
      .join("");
}

function bindIntakeFormHandlers({ form, input, onSubmit }) {
  if (!form || typeof onSubmit !== "function") return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await onSubmit();
  });
  if (!input) return;
  input.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    await onSubmit();
  });
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
                <p class="muted resume-id-line"><code class="resume-id" title="${escapeHtml(resume.resume_id)}">${escapeHtml(resume.resume_id)}</code></p>
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
    <div class="account-switcher">
      <div class="note account-switcher__active">
        <strong>Активный hh-аккаунт</strong>
        <p>${escapeHtml(snapshot.active_account?.display_name || activeKey || "еще не определен")}</p>
      </div>
      <div class="inline-actions account-switcher__actions">
        <button class="button button--ghost" type="button" data-dashboard-action="hh-login-fresh">Войти в другой аккаунт</button>
      </div>
      <div class="account-switcher__grid">
        ${renderList(
          accounts,
          (account) => `
            <article class="resume-card account-card ${activeKey === account.account_key ? "is-active" : ""}">
              <div class="account-card__body">
                <strong>${escapeHtml(account.display_name || account.account_key)}</strong>
                <p class="muted">${escapeHtml(account.resume_count ? `${account.resume_count} резюме` : "резюме не определены")}</p>
              </div>
              <div class="resume-card-actions account-card__actions">
                <button class="button ${activeKey === account.account_key ? "button--primary" : ""}" type="button" ${activeKey === account.account_key ? "disabled" : `data-account-key="${escapeHtml(account.account_key || "")}"`}>
                  ${activeKey === account.account_key ? "Активен" : "Переключить"}
                </button>
                <button class="button button--ghost" type="button" data-delete-account-key="${escapeHtml(account.account_key || "")}" data-delete-account-label="${escapeHtml(account.display_name || account.account_key || "профиль")}">
                  Удалить
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
    const resumeSummary = intakeResumeSummary(snapshot, context);
    const workflowSummary = intakeWorkflowSummary(snapshot, context);
    const pipelineCards = intakePipelineCards(snapshot, context);
    const resumeIntel = intakeResumeIntel(snapshot, context);
    const formMode = intakeFormMode(snapshot, dialog, context);
    const needsResumeSelection = intakeNeedsResumeSelection(snapshot);
    root.innerHTML = `
      <section class="panel intake-stage">
        <div class="intake-stage-head">
          <div>
            <span class="panel-kicker">Обязательный Intake</span>
            <h2>Сначала полный разбор резюме, потом точечный опросник</h2>
            <p class="panel-lead">Сначала читаем весь hh-профиль по блокам и собираем факты. Потом идём по короткому опроснику только там, где резюме реально молчит.</p>
          </div>
          <div class="inline-actions">
            ${renderActionButtons(
              [{ id: "hh-login-fresh", label: "Войти в другой аккаунт" }],
              "button--compact",
            )}
          </div>
        </div>
        <div class="status-strip intake-status-strip">
          ${pipelineCards.map((card) => `<article class="status-card status-card--${escapeHtml(card.tone)}"><span class="status-label">${escapeHtml(card.label)}</span><strong>${escapeHtml(card.value)}</strong><p>${escapeHtml(card.note)}</p></article>`).join("")}
        </div>
        <section class="panel panel--subtle">
          <div class="panel-head">
            <div>
              <span class="panel-kicker">HH-аккаунты</span>
              <h2>Аккаунты и сессии</h2>
            </div>
          </div>
          ${renderAccountSwitcher(snapshot)}
        </section>
        <div class="intake-stage-grid">
          <div class="note">
            <strong>Что агент уже понял</strong>
            <div class="detail-block">
              ${resumeIntel.map(([label, value]) => `<div class="meta-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
            </div>
          </div>
          <div class="note">
            <strong>${escapeHtml(workflowSummary.title)}</strong>
            ${workflowSummary.lines.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
          </div>
        </div>
        <div class="intake-stage-grid intake-stage-grid--compact">
          <div class="note">
            <strong>Что ещё нужно уточнить</strong>
            <p>${escapeHtml((snapshot.setup_summary?.intake_missing || []).length ? `Пока не закрыты: ${formatIntakeMissing(snapshot.setup_summary.intake_missing || []).join(", ")}.` : "Структурные пробелы закрыты, можно переходить дальше.")}</p>
            <p>${escapeHtml(
              dialog.active && currentQuestion
                ? `Сейчас вопрос ${stepIndex + 1} из ${questions.length}.`
                : needsResumeSelection
                  ? "Сначала выберите нужное резюме ниже на странице или обновите список."
                  : intakeQuestionFallback(snapshot, context, { short: true }),
            )}</p>
          </div>
          <div class="note">
            <strong>Как пойдёт intake</strong>
            <p>1. Читаем полный текст резюме по блокам.</p>
            <p>2. Собираем факты и ограничения.</p>
            <p>3. Строим примерный опросник по пробелам.</p>
            <p>4. По ходу уточняем детали, если они реально нужны.</p>
          </div>
        </div>
        ${
          needsResumeSelection
            ? `
              <section class="panel panel--subtle">
                <div class="panel-head">
                  <div>
                    <span class="panel-kicker">Выбор резюме</span>
                    <h2>Найдены несколько резюме</h2>
                  </div>
                  <div class="inline-actions">
                    ${renderActionButtons([{ id: "hh-resumes", label: "Обновить список" }], "button--compact")}
                  </div>
                </div>
                <p class="panel-lead">Выберите одно резюме для поиска, синхронизации профиля и дальнейшего интейка.</p>
                ${renderResumeChooser(snapshot)}
              </section>
            `
            : ""
        }
        <div class="intake-dialog-shell">
          <div class="intake-transcript">
            ${renderList(
              recentMessages,
              (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "агент" : "вы")}</span><p>${escapeHtml(item.text)}</p></article>`,
              "Диалог еще не начат.",
            )}
          </div>
          <div class="intake-question-card">
            <strong>${escapeHtml(currentQuestion?.title || intakeQuestionFallback(snapshot, context))}</strong>
            <p>${escapeHtml(currentQuestion?.hint || "Сейчас двигаемся только по недостающим пунктам. Всё, что уже удалось вытащить из резюме, повторно спрашивать не нужно.")}</p>
            <p class="muted">${escapeHtml(
              currentQuestion?.example ||
                (!hhLoginReady(snapshot)
                  ? "Сначала завершите вход в hh.ru, иначе агенту не с чем работать."
                  : needsResumeSelection
                    ? "Выберите одно резюме для текущего аккаунта. Дальше агент сам доберёт факты."
                    : "Нажмите кнопку ниже. Если разбор резюме ещё неполный, агент сначала дочитает страницу и только потом задаст первый вопрос."),
            )}</p>
          </div>
          <form id="intake-form" class="intake-form">
            <textarea id="intake-input" rows="7" ${formMode === "answer" ? "" : "disabled"} placeholder="${escapeHtml(
              formMode === "login"
                ? "Сначала войдите в hh.ru."
                : formMode === "resume"
                  ? "Сначала выберите резюме для поиска."
                  : formMode === "start"
                    ? "Нажмите кнопку ниже. Агент сначала дочитает резюме, если разбор ещё не закончен."
                    : "Ответьте свободным текстом. Например: только remote, не хочу госуху, роли LLM Engineer/NLP Engineer, зарплата от 350k.",
            )}"></textarea>
            <div class="inline-actions">
              ${
                formMode === "answer"
                  ? `<button class="button" type="button" data-dashboard-action="start-intake" ${state.isBusy ? "disabled" : ""}>Начать заново</button>
                     <button class="button button--primary" type="submit" ${state.isBusy ? "disabled" : ""}>${escapeHtml(state.isBusy ? "Отправляю..." : "Отправить ответ")}</button>`
                  : formMode === "start"
                    ? `<button class="button button--primary" type="button" data-dashboard-action="start-intake" ${state.isBusy ? "disabled" : ""}>Начать уточнение</button>`
                    : ""
              }
            </div>
          </form>
        </div>
      </section>
    `;
    wireActionButtons(root);
    const intakeForm = root.querySelector("#intake-form");
    const intakeInput = root.querySelector("#intake-input");
    bindIntakeFormHandlers({
      form: intakeForm,
      input: intakeInput,
      onSubmit: async () => {
        const input = intakeInput;
        const value = (input?.value || "").trim();
        if (formMode !== "answer") {
          await runDashboardAction("start-intake");
          return;
        }
        if (!value || state.isBusy) return;
        await sendChatCommand(value);
        if (input) input.value = "";
      },
    });
    root.querySelectorAll("[data-resume-id]").forEach((node) =>
      node.addEventListener("click", async () => {
        const resumeId = node.getAttribute("data-resume-id") || "";
        if (!resumeId) return;
        await handleServerAction("/api/actions/select-resume", { resume_id: resumeId });
      }),
    );
    return;
  }
  const step = currentPipelineStep(snapshot);
  const actions = collectQuickActions(snapshot);
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
  root.querySelectorAll("[data-delete-account-key]").forEach((node) =>
    node.addEventListener("click", async () => {
      const accountKey = node.getAttribute("data-delete-account-key") || "";
      const label = node.getAttribute("data-delete-account-label") || accountKey || "профиль";
      if (!accountKey) return;
      if (!window.confirm(`Удалить профиль hh.ru: ${label}?`)) return;
      await handleServerAction("/api/actions/delete-account", { account_key: accountKey });
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
      <div id="chat-agent-activity" class="chat-agent-activity" role="status" aria-live="polite" hidden></div>
      <div id="chat-lock-note"></div>
      <div id="chat-quick-actions" class="chip-row"></div>
      <div id="chat-log" class="chat-log"></div>
      <div class="chat-composer">
        <div id="chat-status" class="chat-status" role="status" aria-live="polite" hidden></div>
        <form id="chat-form" class="chat-form">
          <label class="chat-input-label" for="chat-input">Ваше сообщение</label>
          <textarea id="chat-input" class="chat-input" rows="4" placeholder="Напиши задачу, правку правил или уточнение по резюме" autocomplete="off"></textarea>
          <button id="chat-submit" class="button button--primary" type="submit">Отправить</button>
        </form>
      </div>
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
  const lockedReason = chatLockedReason(state.snapshot);
  const lockNote = document.getElementById("chat-lock-note");
  const visibleHistory = state.chatHistory.filter((item) => String(item?.text || "").trim());
  log.innerHTML = renderList(
    visibleHistory,
    (item) => `<article class="chat-message chat-message--${escapeHtml(item.role)}"><span>${escapeHtml(item.role === "assistant" ? "агент" : "вы")}</span><p>${escapeHtml(item.text)}</p></article>`,
    "Чат пуст.",
  );
  if (state.chatWasNearBottom) log.scrollTop = log.scrollHeight;
  else log.scrollTop = state.chatScrollTop;
  const input = document.getElementById("chat-input");
  const button = document.getElementById("chat-submit");
  if (lockNote) {
    lockNote.innerHTML = lockedReason
      ? `<article class="note note--warning"><strong>Чат пока закрыт</strong><p>${escapeHtml(lockedReason)}</p></article>`
      : "";
  }
  if (input) {
    input.disabled = Boolean(state.isBusy || lockedReason);
    input.placeholder = lockedReason || "Напиши задачу, правку правил или уточнение по резюме";
    if (lockedReason) input.title = lockedReason;
    else input.removeAttribute("title");
  }
  if (button) {
    button.disabled = Boolean(state.isBusy || lockedReason);
    if (state.isBusy && state.busySource === "chat_llm") {
      button.textContent = "Модель (LLM) отвечает…";
    } else if (state.isBusy) {
      button.textContent = "Действие выполняется…";
    } else {
      button.textContent = "Отправить";
    }
    if (lockedReason) button.title = lockedReason;
    else button.removeAttribute("title");
  }
  const liveActivityLine = liveDashboardActivityLine(state.snapshot);
  const statusEl = document.getElementById("chat-status");
  if (statusEl) {
    if (state.isBusy && state.busySource === "chat_llm") {
      statusEl.hidden = false;
      statusEl.className = "chat-status chat-status--llm";
      statusEl.textContent =
        "Запрос обрабатывает языковая модель (LLM). Ответ агента появится в ленте выше.";
    } else if (state.isBusy && state.busySource === "server_action" && !liveActivityLine) {
      statusEl.hidden = false;
      statusEl.className = "chat-status chat-status--action";
      statusEl.textContent = "Сервер выполняет действие (не чат LLM). Дождитесь завершения.";
    } else {
      statusEl.hidden = true;
      statusEl.textContent = "";
      statusEl.className = "chat-status";
    }
  }
  const activityEl = document.getElementById("chat-agent-activity");
  if (activityEl) {
    const pendingLine = currentPendingStatus();
    if (liveActivityLine) {
      activityEl.hidden = false;
      activityEl.textContent = liveActivityLine;
      activityEl.classList.add("chat-agent-activity--live");
    } else if (state.isBusy && pendingLine) {
      activityEl.hidden = false;
      activityEl.textContent = pendingLine;
      activityEl.classList.remove("chat-agent-activity--live");
    } else {
      activityEl.hidden = true;
      activityEl.textContent = "";
      activityEl.classList.remove("chat-agent-activity--live");
    }
  }
}

function renderChatSidebar(snapshot) {
  if (!document.getElementById("chat-log")) renderChatShell();
  const quickActions = document.getElementById("chat-quick-actions");
  quickActions.innerHTML = renderActionButtons(collectQuickActions(snapshot), "button--chip");
  quickActions.style.display = quickActions.innerHTML.trim() ? "flex" : "none";
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
  const resumeFacts = intakeResumeFacts(snapshot, context);
  const resumeSummary = intakeResumeSummary(snapshot, context);
  const formMode = intakeFormMode(snapshot, dialog, context);
  const outstandingTopics = resumeFacts.missingTopics.length
    ? resumeFacts.missingTopics.slice(0, 4)
    : (snapshot.setup_summary?.intake_missing || []).length
      ? formatIntakeMissing(snapshot.setup_summary.intake_missing || [])
      : [];
  const overlay = document.createElement("section");
  overlay.id = "intake-overlay";
  overlay.className = "intake-overlay";
  overlay.innerHTML = `
    <div class="intake-overlay__backdrop"></div>
    <div class="intake-overlay__panel">
      <div class="intake-overlay__head">
        <div>
          <span class="panel-kicker">Обязательный Intake</span>
          <h2>${escapeHtml(confirmationMode ? "Подтвердите итоговые правила перед запуском поиска" : "Сначала полный разбор резюме, потом точечный опросник")}</h2>
          <p class="panel-lead">${escapeHtml(confirmationMode ? "Агент собрал структурные правила кандидата. Проверьте краткую сводку и подтвердите её. До подтверждения поиск и оценка вакансий заблокированы." : "Сначала читаем весь hh-профиль по блокам и собираем факты. Потом идём по короткому опроснику только там, где резюме реально молчит.")}</p>
        </div>
      </div>
      <div class="intake-overlay__meta">
        <article class="note">
          <strong>HH-аккаунты</strong>
          <p>Во время intake можно переключаться между сохранёнными hh-аккаунтами. У каждого аккаунта своя сессия и свои резюме.</p>
          ${renderAccountSwitcher(snapshot)}
        </article>
        <article class="note">
          <strong>Что агент уже понял</strong>
          <div class="detail-block">
            ${intakeResumeIntel(snapshot, context).map(([label, value]) => `<div class="meta-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
          </div>
        </article>
        <article class="note">
          <strong>Что еще нужно уточнить</strong>
          <p>${escapeHtml(outstandingTopics.length ? outstandingTopics.join(", ") : "Критичные пробелы закрыты. Осталось добрать уточнения и завершить диалог.")}</p>
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
          <strong>${escapeHtml(confirmationMode ? "Сводка правил собрана" : question?.title || intakeQuestionFallback(snapshot, context))}</strong>
          <p>${escapeHtml(confirmationMode ? "Если сводка в целом верна, подтвердите правила. Если что-то не так, перезапустите диалог и поправьте ответы." : question?.hint || intakeQuestionHint(snapshot))}</p>
          <p class="muted">${escapeHtml(confirmationMode ? "После подтверждения эти правила станут источником для поиска, оценки вакансий, сопроводительных и анкет." : question?.example || intakeQuestionExample(snapshot))}</p>
        </aside>
      </div>
      <form id="intake-overlay-form" class="intake-overlay__form">
        ${
          formMode === "resume"
            ? `
              <div class="intake-overlay__resume-picker">
                <div class="panel-head">
                  <div>
                    <span class="panel-kicker">Выбор резюме</span>
                    <h2>Выберите резюме для поиска</h2>
                  </div>
                  <div class="inline-actions">
                    ${renderActionButtons([{ id: "hh-resumes", label: "Обновить список" }], "button--compact")}
                  </div>
                </div>
                <p class="panel-lead">После выбора агент сразу зафиксирует профиль и соберёт базу фактов автоматически.</p>
                ${renderResumeChooser(snapshot)}
              </div>
            `
            : ""
        }
        ${confirmationMode ? "" : `<textarea id="intake-overlay-input" rows="6" ${formMode === "answer" ? "" : "disabled"} placeholder="${escapeHtml(
          formMode === "login"
            ? "Сначала войдите в hh.ru."
            : formMode === "resume"
              ? "Сначала выберите резюме для поиска."
              : formMode === "start"
                ? "Нажмите кнопку ниже. Если разбор резюме ещё не завершён, агент сначала доберёт факты."
                : "Ответьте свободным текстом. Например: только remote, не хочу госуху и университеты, роли LLM Engineer/NLP Engineer, зарплата от 350k.",
        )}"></textarea>`}
        <div class="inline-actions">
          ${
            confirmationMode
              ? '<button class="button button--primary" type="button" data-dashboard-action="confirm-intake">Подтвердить и открыть поиск</button>'
              : formMode === "answer"
                ? `<button class="button" type="button" data-dashboard-action="start-intake" ${state.isBusy ? "disabled" : ""}>Начать заново</button>
                   <button class="button button--primary" type="submit" ${state.isBusy ? "disabled" : ""}>${escapeHtml(state.isBusy ? "Отправляю..." : "Отправить ответ")}</button>`
                : formMode === "start"
                  ? `<button class="button button--primary" type="button" data-dashboard-action="start-intake" ${state.isBusy ? "disabled" : ""}>Начать уточнение</button>`
                  : ""
          }
        </div>
      </form>
    </div>
  `;
  shell.appendChild(overlay);
  wireActionButtons(overlay);
  overlay.querySelectorAll("[data-resume-id]").forEach((node) =>
    node.addEventListener("click", async () => {
      const resumeId = node.getAttribute("data-resume-id") || "";
      if (!resumeId) return;
      await handleServerAction("/api/actions/select-resume", { resume_id: resumeId });
    }),
  );
  const intakeOverlayForm = overlay.querySelector("#intake-overlay-form");
  const intakeOverlayInput = overlay.querySelector("#intake-overlay-input");
  bindIntakeFormHandlers({
    form: intakeOverlayForm,
    input: intakeOverlayInput,
    onSubmit: async () => {
      if (confirmationMode) return;
      const input = intakeOverlayInput;
      const value = (input?.value || "").trim();
      if (formMode !== "answer") {
        await runDashboardAction("start-intake");
        return;
      }
      if (!value || state.isBusy) return;
      await sendChatCommand(value);
      if (input) input.value = "";
    },
  });
}

function renderLlmGateOverlay(snapshot) {
  document.getElementById("llm-gate-overlay")?.remove();
  const gate = snapshot?.llm_gate || {};
  const stage = String(gate.stage || "");
  if (stage === "resume_intake" && snapshot?.setup_summary?.intake_ready) return;
  if (stage === "filter_plan" && snapshot?.setup_summary?.filter_plan_ready) return;
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
  const lockedReason = chatLockedReason(state.snapshot);
  if (lockedReason) {
    appendAssistantMessage(lockedReason, `chat-locked:${lockedReason}`);
    renderChatLog();
    return;
  }
  const input = document.getElementById("chat-input");
  pauseAutoRefresh(12000);
  state.chatHistory.push({ role: "user", text: message });
  state.busySource = "chat_llm";
  state.isBusy = true;
  setPendingStatus("Отправляю сообщение агенту.", pendingFramesForAction("chat"));
  renderChatLog();
  if (state.snapshot) renderHero(state.snapshot);
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
    state.busySource = null;
    setPendingStatus("");
    renderChatLog();
    if (state.snapshot) renderHero(state.snapshot);
    if (state.snapshot) renderAgentView(state.snapshot);
  }
}

function pickResultMessage(result) {
  const payload = result?.result?.payload || {};
  if (result?.result?.action === "plan_apply" && payload?.vacancy?.title) {
    return `План отклика собран для вакансии «${payload.vacancy.title}». Проверьте письмо и шаги отклика в карточке.`;
  }
  return (
    payload?.result?.message ||
    payload?.message ||
    result?.result?.message ||
    result?.message ||
    ""
  );
}

async function handleServerAction(url, payload = {}, onSuccess) {
  const pendingMessage = payload?.vacancy_id
    ? "Обновляю действия по вакансии."
    : (url.includes("/hh-login")
      ? "Открываю hh.ru для входа."
      : (url.includes("/refresh-vacancies")
        ? "Ещё раз подтягиваю вакансии с hh.ru."
      : (url.includes("/resume")
        ? "Обновляю профиль и черновик резюме."
        : (url.includes("/analyze")
          ? "Запускаю анализ и разбор вакансий."
          : (url.includes("/apply")
            ? "Готовлю действие по отклику."
            : (url.includes("/apply-batch")
              ? `Запускаю пакетную отправку откликов по колонке ${categoryLabel(payload?.category || "")}.`
              : "Выполняю действие."))))));
  state.busySource = "server_action";
  state.isBusy = true;
  setPendingStatus(pendingMessage, []);
  renderChatLog();
  if (state.snapshot) renderHero(state.snapshot);
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
  } finally {
    state.isBusy = false;
    state.busySource = null;
    setPendingStatus("");
    renderChatLog();
    if (state.snapshot) renderHero(state.snapshot);
    if (state.snapshot) renderAgentView(state.snapshot);
  }
}

async function runDashboardAction(actionId, chatPrompt = "") {
  if (!actionId) return;
  const intakeBlockedActions = new Set(["resume-sync", "build-rules", "plan-filters", "analyze", "apply-plan", "open-vacancies", "open-vacancy"]);
  const availability = actionAvailability({ id: actionId });
  if (actionId === "focus-chat") {
    focusChatInput(chatPrompt);
    return;
  }
  if (availability.disabled) {
    appendAssistantMessage(availability.reason, `action-disabled:${actionId}:${availability.reason}`);
    renderChatLog();
    return;
  }
  if (actionId === "start-intake") {
    const context = intakeDialogState(state.snapshot).context || {};
    const facts = intakeResumeFacts(state.snapshot, context);
    const hasFacts = Boolean((facts.resumeTitle || "").trim() || facts.roles.length || facts.skills.length);
    if (state.snapshot?.selected_resume_id && !hasFacts) {
      await handleServerAction("/api/actions/resume", {});
    }
    const optimisticSnapshot = optimisticIntakeRestartSnapshot(state.snapshot);
    if (optimisticSnapshot) renderSnapshot(optimisticSnapshot);
    await handleServerAction("/api/actions/start-intake", {
      restart: Boolean(state.snapshot?.setup_summary?.intake_dialog_completed || intakeDialogState(state.snapshot).active),
    });
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
    "hh-login-fresh": ["/api/actions/hh-login", { fresh_start: true }],
    "hh-resumes": ["/api/actions/hh-resumes", {}],
    "confirm-intake": ["/api/actions/confirm-intake", {}],
    "llm-fallback-heuristics": ["/api/actions/llm-fallback-heuristics", { stage: state.snapshot?.llm_gate?.stage || "resume_intake" }],
    "llm-wait": ["/api/actions/llm-wait", { stage: state.snapshot?.llm_gate?.stage || "resume_intake" }],
    "resume-sync": ["/api/actions/resume", {}],
    "build-rules": ["/api/actions/build-rules", {}],
    "plan-filters": ["/api/actions/plan-filters", {}],
    analyze: ["/api/actions/analyze", { limit: 120 }],
    "refresh-vacancies": ["/api/actions/refresh-vacancies", { limit: 0 }],
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
        <div class="inline-actions">
          <button class="button button--ghost" type="button" data-dashboard-action="refresh-vacancies" ${snapshot.analysis_job?.running ? "disabled" : ""}>Ещё раз спарсить вакансии</button>
          <button class="button button--primary" type="button" data-dashboard-action="analyze" ${snapshot.analysis_job?.running ? "disabled" : ""}>Запустить анализ</button>
        </div>
      </div>
      <div class="stack board-summary">
        <div class="note"><strong>Статус анализа</strong><p>${escapeHtml(snapshot.analysis_job?.message || "Оценка очереди не запускалась.")}</p></div>
        <div class="note"><strong>Источник</strong><p>${escapeHtml(snapshot.setup_summary?.live_refresh_message || "Источник вакансий ещё не обновлялся.")}</p><p class="muted">${escapeHtml(`На hh.ru видно ${snapshot.setup_summary?.live_refresh_stats?.total_available || 0}, в локальной очереди ${snapshot.setup_summary?.live_refresh_stats?.count || 0}, новых после refresh ${snapshot.setup_summary?.live_refresh_stats?.new_count || 0}.`)}</p></div>
        <div class="note"><strong>Фильтры поиска</strong><p>${escapeHtml(snapshot.filter_plan?.search_text || "Широкий resume-first поиск без текстового сужения.")}</p><p class="muted">${escapeHtml((snapshot.filter_plan?.residual_rules || []).slice(0, 3).join(" • ") || "Жёстко режем только очевидный no-fit, остальное уходит в локальную оценку.")}</p></div>
        <div class="note"><strong>Лимит откликов</strong><p>${escapeHtml(`Сегодня использовано ${applyLimits.used_today || 0} из ${applyLimits.daily_limit || 200}, осталось ${applyLimits.remaining_today || 0}.`)}</p></div>
      </div>
      <div class="board">
        ${Object.entries(categoryMeta)
          .map(
            ([key, meta]) => `
              <section class="lane ${meta.className}">
                <div class="lane-head">
                  <div class="lane-head-top">
                    <div class="lane-head-text">
                      <h3>${escapeHtml(meta.label)}</h3>
                      <p class="muted">${escapeHtml(meta.hint)}</p>
                    </div>
                    <span class="lane-count lane-count--corner" aria-label="Карточек в колонке">${escapeHtml((snapshot.columns?.[key] || []).length)}</span>
                  </div>
                  <div class="lane-head-actions">
                    <button class="button button--ghost button--batch-lane" type="button" ${
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
                          <span class="score score--corner" aria-label="Оценка">${escapeHtml(card.score)}</span>
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
  wireActionButtons(root);
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
  const applyRuntimeMessage = String(feedback.last_apply_message || "");
  const applyUnavailable =
    !localBrowserReady(snapshot) || applyRuntimeMessage.includes("Failed to start Playwright browser for hh.ru apply flow");
  const applyUnavailableReason = applyUnavailable
    ? (applyRuntimeMessage.includes("Failed to start Playwright browser for hh.ru apply flow")
      ? applyRuntimeMessage
      : localBrowserErrorMessage(snapshot))
    : "";
  root.innerHTML = `
    <div class="vacancy-detail-grid">
      <section class="panel panel--detail-main">
        <div class="panel-head panel-head--vacancy">
          <div>
            <span class="panel-kicker">Детальный разбор</span>
            <h2>${escapeHtml(card.title)}</h2>
            <div class="panel-lead panel-lead--compact vacancy-card-context" aria-label="Контекст карточки">
              <span class="vacancy-card-context__resume">${escapeHtml(snapshot.selected_resume_title || snapshot.selected_resume_id || "Резюме не выбрано")}</span>
              <span class="vacancy-card-context__sep">·</span>
              <time class="vacancy-card-context__time" datetime="${escapeHtml(snapshot.generated_at || "")}">${escapeHtml(formatDate(snapshot.generated_at))}</time>
            </div>
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
          <div class="description-text vacancy-desc">${formatVacancyDescriptionHtml(card.description || card.summary || "")}</div>
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
        <div class="cta-grid cta-grid--decision-bottom">
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
        <div class="field">
          <label for="cover-letter-input">Сопроводительное письмо</label>
          <textarea id="cover-letter-input" rows="12" placeholder="Здесь можно отредактировать письмо перед откликом">${escapeHtml(coverLetter)}</textarea>
        </div>
        <div class="cta-stack">
          <button class="button button--ghost" id="save-cover-letter">Сохранить письмо</button>
          <button class="button button--ghost" id="build-apply-plan">Собрать план отклика</button>
          <button class="button button--primary" id="apply-submit" ${applyUnavailable ? `disabled title="${escapeHtml(applyUnavailableReason)}"` : ""}>Откликнуться</button>
        </div>
        ${applyUnavailable ? `<div class="note note--pending"><strong>Локальный браузер недоступен</strong><p>${escapeHtml(applyUnavailableReason)}</p></div>` : ""}
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
    if (applyUnavailable) {
      appendAssistantMessage(applyUnavailableReason, `apply-disabled:${card.id}:${applyUnavailableReason}`);
      renderChatLog();
      return;
    }
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

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  rememberScrollState();
  const previousSnapshot = state.snapshot;
  state.snapshot = snapshot;
  document.body.classList.toggle("intake-blocking", !isIntakeReady(snapshot));
  document.querySelector(".dashboard-shell")?.classList.toggle("dashboard-shell--intake", !isIntakeReady(snapshot));
  ensureSelectedVacancy(snapshot);
  if (state.activeTab === "activity") state.activeTab = "agent";
  if (!state.userSelectedTab) {
    if (snapshot.analysis_job?.running) state.activeTab = "agent";
    else if (!previousSnapshot) state.activeTab = preferredTab(snapshot);
  }
  announceSnapshotChanges(snapshot, previousSnapshot);
  ingestBackgroundJobLogs(snapshot);
  renderHero(snapshot);
  renderStatusStrip(snapshot);
  renderTabbar();
  renderChatSidebar(snapshot);
  renderAgentView(snapshot);
  renderVacancies(snapshot);
  renderVacancyDetail(snapshot);
  renderBlockingIntakeOverlay(snapshot);
  renderLlmGateOverlay(snapshot);
  repairRenderedText(document.body);
  updateVisibleTab();
  restoreScrollState();
  if (snapshot?.refresh_job?.running || snapshot?.analysis_job?.running) {
    scheduleProgressPoll();
  }
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
  if (storedWidth && window.innerWidth > 1240) applySidebarWidth(shell, storedWidth);
  if (window.innerWidth <= 1240) shell.style.removeProperty("--sidebar-width");

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
    if (window.innerWidth <= 1240) {
      shell.style.removeProperty("--sidebar-width");
      return;
    }
    const currentWidth = document.getElementById("chat-sidebar")?.getBoundingClientRect().width || storedWidth || 384;
    applySidebarWidth(shell, currentWidth);
  });
}

function formatIntakeMissing(items) {
  const snapshot = state.snapshot;
  const profileMessage = !hhLoginReady(snapshot)
    ? "нужно войти в hh.ru"
    : !snapshot?.selected_resume_id
      ? "нужно выбрать резюме для поиска"
      : "нужно дочитать и структурировать выбранное резюме";
  const mapping = {
    profile: profileMessage,
    dialog: "обязательный диалог не завершен",
    confirmation: "правила ещё не подтверждены",
    structured_profile: "структурный профиль ещё не собран",
    exclusions: "не заданы исключения и стоп-слова",
  };
  return (Array.isArray(items) ? items : []).map((item) => mapping[item] || item);
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

