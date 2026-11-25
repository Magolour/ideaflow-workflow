/* IdeaFlow 工作台前端：提交想法 → 轮询阶段 → 渲染结果 → 下载。 */

const state = {
  constraints: [],
  jobId: null,
  result: null,
  pollTimer: null,
  clockTimer: null,
  startedAt: 0,
  busy: false,
  mode: "proposal",
  case: null,
  loadedCaseText: "",
  spec: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const query = new URLSearchParams(location.search);
const initialTab = ["doc", "options", "objections", "questions"].includes(query.get("tab"))
  ? query.get("tab")
  : "doc";

const els = {
  form: $("#ideaForm"),
  idea: $("#idea"),
  ideaCount: $("#ideaCount"),
  ideaError: $("#ideaError"),
  constraintInput: $("#constraintInput"),
  addConstraint: $("#btnAddConstraint"),
  chips: $("#chips"),
  rounds: $("#rounds"),
  roundsOut: $("#roundsOut"),
  threshold: $("#threshold"),
  thresholdOut: $("#thresholdOut"),
  generate: $("#btnGenerate"),
  generateLabel: $(".btn-generate .btn-label"),
  status: $("#serverStatus"),
  empty: $("#emptyState"),
  runPanel: $("#runPanel"),
  runTitle: $("#runTitle"),
  runTime: $("#runTime"),
  timeline: $("#timeline"),
  errorPanel: $("#errorPanel"),
  errorMsg: $("#errorMsg"),
  errorHint: $("#errorHint"),
  retry: $("#btnRetry"),
  resultPanel: $("#resultPanel"),
  resultTitle: $("#resultTitle"),
  resultSub: $("#resultSub"),
  metrics: $("#metrics"),
  tabs: $("#tabs"),
  paper: $("#paper"),
  panelOptions: $("#panelOptions"),
  panelObjections: $("#panelObjections"),
  panelQuestions: $("#panelQuestions"),
  countOptions: $("#countOptions"),
  countObjections: $("#countObjections"),
  countQuestions: $("#countQuestions"),
  btnCopy: $("#btnCopy"),
  dlMd: $("#dlMd"),
  dlHtml: $("#dlHtml"),
  btnPrint: $("#btnPrint"),
  btnHelp: $("#btnHelp"),
  help: $("#helpDialog"),
  toast: $("#toast"),
  modeSwitch: $("#modeSwitch"),
  railEyebrow: $("#railEyebrow"),
  railTitle: $("#railTitle"),
  railSub: $("#railSub"),
  advancedOptions: $("#advancedOptions"),
  caseField: $("#caseField"),
  emptyTitle: $("#emptyState h2"),
  emptySub: $("#emptyState .empty-sub"),
  specResult: $("#specResult"),
  specTitle: $("#specTitle"),
  specSub: $("#specSub"),
  specBanner: $("#specBanner"),
  specMetrics: $("#specMetrics"),
  specTabs: $("#specTabs"),
  specPaper: $("#specPaper"),
  panelSnapshot: $("#panelSnapshot"),
  panelDrafts: $("#panelDrafts"),
  panelAudit: $("#panelAudit"),
  countDrafts: $("#countDrafts"),
  countIssues: $("#countIssues"),
  specDlMd: $("#specDlMd"),
  specDlJson: $("#specDlJson"),
  specPrint: $("#specPrint"),
  liveModel: $("#liveModel"),
  runWarnings: $("#runWarnings"),
};

/* ------------------------------ 工具 ------------------------------ */

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => els.toast.classList.remove("show"), 2000);
}

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function showOnly(panel) {
  [els.empty, els.runPanel, els.errorPanel, els.resultPanel, els.specResult].forEach((node) => {
    node.hidden = node !== panel;
  });
}

/* ------------------------------ 约束输入 ------------------------------ */

function renderChips() {
  els.chips.innerHTML = state.constraints
    .map((text, index) => `<span class="chip"><span>${escapeHtml(text)}</span>`
      + `<button type="button" data-index="${index}" aria-label="移除约束">×</button></span>`)
    .join("");
}

function addConstraint(raw) {
  const text = raw.trim();
  if (!text) return;
  if (text.length > 120) {
    toast("单条约束最多 120 字");
    return;
  }
  if (state.constraints.includes(text)) {
    els.constraintInput.value = "";
    return;
  }
  if (state.constraints.length >= 12) {
    toast("约束最多 12 条");
    return;
  }
  state.constraints.push(text);
  els.constraintInput.value = "";
  renderChips();
}

/* ------------------------------ 渲染 ------------------------------ */

function renderTimeline(logs) {
  if (!logs.length) {
    els.timeline.innerHTML = '<li>准备输入与角色上下文…</li>';
    return;
  }
  els.timeline.innerHTML = logs.map((log, index) => {
    const sub = log.text.trim().startsWith("·");
    const done = index < logs.length - 1;
    return `<li class="${sub ? "sub" : ""} ${done ? "done" : ""}">${escapeHtml(log.text.trim())}</li>`;
  }).join("");
  els.timeline.scrollTop = els.timeline.scrollHeight;
}

function metricBlock(data) {
  const counts = data.counts;
  const support = Math.round((Number(data.support) || 0) * 100);
  const blockingQuestions = data.questions.filter((row) => row.blocking).length;
  const consensus = data.consensus || {};
  const state = consensus.state || "no_consensus";
  return `
    <div class="metric wide">
      <span class="k">共识判定</span>
      <p class="consensus-line"><span class="badge ${escapeHtml(state)}">${escapeHtml(consensus.label)}</span></p>
      <p class="note">${escapeHtml(consensus.reason)}</p>
    </div>
    <div class="metric">
      <span class="k">主张</span>
      <span class="v">${counts.claims}</span>
      <p class="note">${counts.assumptions} 条假设待验证</p>
    </div>
    <div class="metric">
      <span class="k">异议</span>
      <span class="v">${counts.objections}<small>条</small></span>
      <p class="note">${counts.blocking ? `${counts.blocking} 条阻断未解决` : "无未解决阻断项"}</p>
    </div>
    <div class="metric">
      <span class="k">待澄清</span>
      <span class="v">${counts.open_questions}</span>
      <p class="note">${blockingQuestions ? `${blockingQuestions} 个阻断问题待答复` : "无阻断问题"}</p>
    </div>
    <div class="metric">
      <span class="k">支持度</span>
      <span class="v">${support}<small>%</small></span>
      <div class="bar"><i style="width:${support}%"></i></div>
    </div>`;
}

function renderOptions(data) {
  if (!data.options.length) {
    els.panelOptions.innerHTML = '<p class="empty-inline">本次没有生成候选方案。</p>';
    return;
  }
  const supportById = Object.fromEntries(
    (data.option_support || []).map((row) => [row.option, row.ratio]));
  const cards = data.options.map((option) => {
    const ratio = Math.round((supportById[option.id] || 0) * 100);
    const stances = Object.entries(option.stances || {})
      .map(([role, stance]) => `<span class="stance ${escapeHtml(stance)}">${escapeHtml(role)} · ${escapeHtml(stanceLabel(stance))}</span>`)
      .join("");
    return `
      <article class="opt">
        <header>
          <span class="opt-key">${escapeHtml(option.key)}</span>
          <div>
            <h3>${escapeHtml(option.name)}</h3>
            <p class="opt-sub">${escapeHtml(option.cost_days)} 天 · 风险 ${escapeHtml(option.risk)} · 组内支持 ${ratio}%</p>
          </div>
        </header>
        <ul class="opt-scope">${option.scope.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        <p class="opt-trade"><b>取舍：</b>${escapeHtml(option.tradeoffs)}</p>
        <div class="opt-stances">${stances || '<span class="stance unreviewed">暂无立场</span>'}</div>
      </article>`;
  }).join("");

  const decisions = data.decisions.length
    ? `<table class="plain"><thead><tr><th>编号</th><th>议题</th><th>结论</th><th>理由</th><th>裁决方</th></tr></thead><tbody>`
      + data.decisions.map((row) => `<tr>
          <td class="tag-mono">${escapeHtml(row.id)}</td>
          <td>${escapeHtml(row.scope)}</td>
          <td>${escapeHtml(row.choice)}</td>
          <td>${escapeHtml(row.reason)}</td>
          <td class="tag-mono">${escapeHtml(row.decider)}</td>
        </tr>`).join("")
      + "</tbody></table>"
    : '<p class="empty-inline">暂无已定结论。</p>';

  const nonGoals = data.non_goals.length
    ? `<div class="section-title"><h3>明确不做</h3><span>${data.non_goals.length} 项</span></div>`
      + `<table class="plain"><thead><tr><th>内容</th><th>不做的理由</th></tr></thead><tbody>`
      + data.non_goals.map((row) => `<tr><td>${escapeHtml(row.item)}</td><td>${escapeHtml(row.reason)}</td></tr>`).join("")
      + "</tbody></table>"
    : "";

  els.panelOptions.innerHTML = `
    <div class="section-title"><h3>候选方案</h3><span>${data.options.length} 个 · 支持度 ${Math.round((data.support || 0) * 100)}%</span></div>
    <div class="opt-grid">${cards}</div>
    <div class="section-title"><h3>已定结论</h3><span>${data.decisions.length} 条</span></div>
    ${decisions}
    ${nonGoals}`;
}

function stanceLabel(stance) {
  return { agree: "赞同", conditional: "有条件", oppose: "反对", unreviewed: "未评审" }[stance] || stance;
}

function renderObjections(data) {
  if (!data.objections.length) {
    els.panelObjections.innerHTML = '<p class="empty-inline">本次讨论没有提出异议。</p>';
    return;
  }
  const rank = { blocking: 0, major: 1, minor: 2 };
  const rows = [...data.objections].sort((a, b) => {
    const openDelta = (a.status === "open" ? 0 : 1) - (b.status === "open" ? 0 : 1);
    return openDelta || (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9);
  });
  els.panelObjections.innerHTML = `<ul class="obj-list">${rows.map((row) => {
    const response = row.response && row.response.argument
      ? `<p class="obj-resp"><b>${escapeHtml(row.response.by)} 回应：</b>${escapeHtml(row.response.argument)}</p>`
      : "";
    return `
      <li class="obj">
        <div class="obj-head">
          <span class="sev ${escapeHtml(row.severity)}">${escapeHtml(row.severity_label)}</span>
          <span class="obj-id">${escapeHtml(row.id)}</span>
          <span class="obj-role">${escapeHtml(row.role_name)} · 针对 ${escapeHtml(row.target)}</span>
          <span class="obj-state ${escapeHtml(row.status)}">${escapeHtml(row.status_label)}</span>
        </div>
        <p class="obj-text">${escapeHtml(row.text)}</p>
        <p class="obj-meta">分歧类型：${escapeHtml(row.divergence || "未分类")} ｜ 后果：${escapeHtml(row.consequence)}</p>
        ${response}
      </li>`;
  }).join("")}</ul>`;
}

function renderQuestions(data) {
  if (!data.questions.length) {
    els.panelQuestions.innerHTML = '<p class="empty-inline">没有登记待澄清问题。</p>';
    return;
  }
  els.panelQuestions.innerHTML = `<ul class="q-list">${data.questions.map((row) => `
    <li class="q-item">
      <div class="q-head">
        <span class="obj-id">${escapeHtml(row.id)}</span>
        ${row.blocking ? '<span class="sev blocking">阻断</span>' : '<span class="sev minor">一般</span>'}
      </div>
      <p class="q-text">${escapeHtml(row.text)}</p>
      <p class="q-answer"><b>当前处理：</b>${escapeHtml(row.resolution || "尚未答复")}</p>
    </li>`).join("")}</ul>`;
}

function renderResult(data) {
  state.result = data;
  const lines = data.markdown.split("\n");
  const heading = (lines.find((line) => line.startsWith("# ")) || "").replace(/^#\s*/, "");
  els.resultTitle.textContent = heading || data.idea;
  els.resultSub.textContent = `运行 ${data.run_id} · ${formatTime(data.created_at)} · 角色实现 ${data.agent || "mock"}`;

  const warnings = data.warnings || [];
  els.runWarnings.hidden = warnings.length === 0;
  els.runWarnings.innerHTML = warnings.map((row) => `<div class="warn-row">
      <b>${escapeHtml(row.role)}</b> 在 ${escapeHtml(row.stage)} 阶段被跳过：
      ${escapeHtml(row.code)} ${escapeHtml(row.message)}
    </div>`).join("");

  els.metrics.innerHTML = metricBlock(data);
  els.paper.innerHTML = data.html;
  els.countOptions.textContent = data.options.length;
  els.countObjections.textContent = data.objections.length;
  els.countQuestions.textContent = data.questions.length;

  renderOptions(data);
  renderObjections(data);
  renderQuestions(data);

  const runId = encodeURIComponent(data.run_id);
  els.dlMd.href = `/api/download?run_id=${runId}&format=md`;
  els.dlHtml.href = `/api/download?run_id=${runId}&format=html`;

  switchTab(initialTab);
  showOnly(els.resultPanel);
  els.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function switchTab(name) {
  $$(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
  $$(".panel").forEach((panel) => { panel.hidden = panel.dataset.panel !== name; });
}

/* ------------------------------ 生成流程 ------------------------------ */

function setBusy(busy) {
  state.busy = busy;
  els.generate.disabled = busy;
  els.generateLabel.textContent = busy ? "正在生成…" : MODE_TEXT[state.mode].label;
}

function startClock() {
  state.startedAt = Date.now();
  els.runTime.textContent = "0.0s";
  clearInterval(state.clockTimer);
  state.clockTimer = setInterval(() => {
    els.runTime.textContent = `${((Date.now() - state.startedAt) / 1000).toFixed(1)}s`;
  }, 100);
}

function stopClock() {
  clearInterval(state.clockTimer);
  state.clockTimer = null;
}

function showError(error) {
  stopClock();
  setBusy(false);
  const code = error.code || "ERROR";
  const path = error.path ? `（位置：${error.path}）` : "";
  els.errorMsg.textContent = `${code} · ${error.message}${path}`;
  const hints = {
    LLM_NOT_CONFIGURED: "在服务端设置 AI_API_KEY 与 AI_MODEL 后重试；或取消勾选“使用真实模型”。",
    LLM_UNAVAILABLE: "真实模型调用失败，可查看 run.json 与 ai_calls.jsonl，或改用离线模式。",
    LLM_BAD_OUTPUT: "模型输出未通过契约校验，可降低温度、换模型后重试；其余角色会继续。",
  };
  els.errorHint.textContent = hints[code]
    || (code === "NETWORK"
      ? "请确认本地服务已启动，且端口未被占用。"
      : "可以调整输入后重试；如果是配置问题，检查运行目录权限。");
  showOnly(els.errorPanel);
}

async function generate() {
  if (state.busy) return;
  const idea = els.idea.value.trim();
  if (!idea) {
    els.ideaError.hidden = false;
    els.idea.classList.add("invalid");
    els.idea.focus();
    return;
  }
  els.ideaError.hidden = true;
  els.idea.classList.remove("invalid");

  setBusy(true);
  state.result = null;
  state.spec = null;
  els.timeline.innerHTML = "";
  els.runTitle.textContent = state.mode === "spec" ? "正在生成设计规格" : "正在生成设计文档";
  showOnly(els.runPanel);
  startClock();

  const live = Boolean(els.liveModel && els.liveModel.checked);
  const body = state.mode === "spec"
    ? { flow: "spec", idea, constraints: state.constraints, case: state.case, live }
    : {
      flow: "proposal",
      idea,
      constraints: state.constraints,
      max_rounds: Number(els.rounds.value),
      threshold: Number(els.threshold.value) / 100,
      live,
    };

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const failure = new Error(payload?.error?.message || `服务返回 ${response.status}`);
      failure.code = payload?.error?.code || `HTTP_${response.status}`;
      failure.path = payload?.error?.path || "";
      throw failure;
    }
    state.jobId = payload.job_id;
    pollJob();
  } catch (error) {
    showError({
      code: error.code || "NETWORK",
      message: error.code ? error.message : `${error.message}。请确认本地服务已启动。`,
      path: error.path || "",
    });
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const response = await fetch(`/api/job?id=${encodeURIComponent(state.jobId)}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error?.message || `服务返回 ${response.status}`);
    }
    const job = payload.job;
    renderTimeline(job.logs || []);
    if (job.state === "done") {
      // 生成通常不到 1 秒；留一点时间让阶段日志可读，避免界面闪一下就跳走
      const wait = Math.max(0, 520 - (Date.now() - state.startedAt));
      setTimeout(() => {
        stopClock();
        setBusy(false);
        try {
          if (job.result && job.result.flow === "spec") {
            renderSpecResult(job.result);
          } else {
            renderResult(job.result);
          }
        } catch (error) {
          showError({ code: "RENDER_ERROR",
                      message: `${error.name}: ${error.message}` });
        }
      }, wait);
      return;
    }
    if (job.state === "error") {
      showError(job.error || { code: "ERROR", message: "生成失败" });
      return;
    }
    state.pollTimer = setTimeout(pollJob, 240);
  } catch (error) {
    showError({ code: "NETWORK", message: error.message });
  }
}

/* ------------------------------ 规格视图 ------------------------------ */

const MODE_TEXT = {
  proposal: {
    eyebrow: "Input",
    title: "从一个想法开始",
    sub: "六个角色独立出稿、互相批判，再整合成一份带分歧记录的设计文档。",
    label: "生成设计文档",
    emptyTitle: "一句想法，一份可确认的设计文档",
    emptySub: "输入想法后，系统会走完澄清、发散、批判、整合、签署、收敛六个阶段，输出 11 章文档，并保留未解决的问题与少数意见。",
  },
  spec: {
    eyebrow: "Specification",
    title: "从想法到设计规格",
    sub: "需求快照 → 两组独立方案 → 逐份评审 → 设计规格 → 自审查 → 修订 → 人工问题。",
    label: "生成设计规格",
    emptyTitle: "一句想法，一份可追溯的设计规格",
    emptySub: "先把输入固化成带编号与来源的需求快照，再让两组独立出方案、逐份评审、整合规格，并做覆盖、冲突与验收自审查。",
  },
};

const SPEC_TYPE_LABEL = { requirement: "明确要求", constraint: "约束", open: "待澄清" };
const SPEC_PRIORITY_LABEL = { must: "必须", should: "应当", could: "可选" };
const SPEC_SATISFIES = { full: "完全满足", partial: "部分满足", declined: "不满足" };

const specTabFromUrl = ["snapshot", "drafts", "doc", "audit"].includes(query.get("stab"))
  ? query.get("stab")
  : "snapshot";

function setMode(mode) {
  state.mode = mode === "spec" ? "spec" : "proposal";
  const text = MODE_TEXT[state.mode];
  $$(".mode").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.mode === state.mode));
  });
  els.railEyebrow.textContent = text.eyebrow;
  els.railTitle.textContent = text.title;
  els.railSub.textContent = text.sub;
  els.generateLabel.textContent = text.label;
  els.advancedOptions.hidden = state.mode !== "proposal";
  els.caseField.hidden = state.mode !== "spec";
  els.emptyTitle.textContent = text.emptyTitle;
  els.emptySub.textContent = text.emptySub;
  if (state.mode === "spec") {
    els.idea.placeholder = "例如：做一个校园二手书交易小程序，学生可以发布、搜索教材并线下交易";
  }
}

function clearCaseSelection() {
  state.case = null;
  state.loadedCaseText = "";
  $$(".case-btn").forEach((button) => button.setAttribute("aria-pressed", "false"));
}

async function pickCase(name) {
  if (!name) {
    clearCaseSelection();
    els.idea.focus();
    return;
  }
  try {
    const response = await fetch(`/api/case?name=${encodeURIComponent(name)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "案例加载失败");
    state.case = name;
    state.loadedCaseText = data.text;
    els.idea.value = data.text;
    els.ideaCount.textContent = `${data.text.length} / 1200`;
    els.ideaError.hidden = true;
    els.idea.classList.remove("invalid");
    $$(".case-btn").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.case === name));
    });
    toast(`已载入案例：${data.title}`);
  } catch (error) {
    toast(error.message);
  }
}

function switchSpecTab(name) {
  $$("#specTabs .tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.stab === name));
  });
  $$("[data-spanel]").forEach((panel) => {
    panel.hidden = panel.dataset.spanel !== name;
  });
}

function renderSpecResult(data) {
  state.spec = data;
  const spec = data.spec;
  const latest = data.audits[data.audits.length - 1];

  els.specTitle.textContent = `设计规格：${data.title}`;
  els.specSub.textContent = `${spec.id} v${spec.version} ｜ 内容哈希 ${spec.hash.slice(0, 12)}… `
    + `｜ 快照 ${data.snapshot.id} v${data.snapshot.version} ｜ ${data.status_label}`
    + (data.ai_calls ? ` ｜ 模型调用 ${data.ai_calls} 次` : "");

  if (data.question) {
    els.specBanner.className = "spec-banner";
    els.specBanner.hidden = false;
    els.specBanner.innerHTML = `<div><b>待人工决策 ${escapeHtml(data.question.id)}</b>：`
      + `${escapeHtml(data.question.question)}</div><a data-goto="audit">查看问题与选项 →</a>`;
  } else if (spec.status === "confirmed") {
    els.specBanner.className = "spec-banner ok";
    els.specBanner.hidden = false;
    els.specBanner.innerHTML = `<div><b>规格审查通过</b>。可下载规格 JSON 并提交编排。</div>`;
  } else if (spec.status === "blocked") {
    els.specBanner.className = "spec-banner";
    els.specBanner.hidden = false;
    els.specBanner.innerHTML = '<div><b>规格仍有阻断</b>。请在「审查与修订」核对问题清单。</div>';
  } else {
    els.specBanner.hidden = true;
  }

  els.specMetrics.innerHTML = specMetricBlock(data);
  els.specPaper.innerHTML = data.html;
  els.countDrafts.textContent = data.drafts.length;
  els.countIssues.textContent = latest.counts.total;

  renderSnapshotPanel(data);
  renderDraftsPanel(data);
  renderAuditPanel(data);

  const runId = encodeURIComponent(data.run_id);
  els.specDlMd.href = `/api/download?run_id=${runId}&artifact=spec-md`;
  els.specDlJson.href = `/api/download?run_id=${runId}&artifact=spec-input`;

  switchSpecTab(specTabFromUrl);
  showOnly(els.specResult);
  els.specResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

function specMetricBlock(data) {
  const spec = data.spec;
  const latest = data.audits[data.audits.length - 1];
  const counts = data.snapshot.counts;
  const blocking = latest.counts.blocking;
  return `
    <div class="metric">
      <span class="k">需求</span>
      <span class="v">${counts.requirement}</span>
      <p class="note">${counts.constraint} 条约束 · ${counts.open} 个待澄清</p>
    </div>
    <div class="metric">
      <span class="k">决策</span>
      <span class="v">${spec.decisions.length}</span>
      <p class="note">${spec.modules.length} 个模块 · ${spec.acceptance.length} 条验收</p>
    </div>
    <div class="metric">
      <span class="k">承诺</span>
      <span class="v">${spec.effort.commit_days}<small>天</small></span>
      <p class="note">期望 ${spec.effort.expected_total} 天</p>
    </div>
    <div class="metric">
      <span class="k">阻断</span>
      <span class="v">${blocking}</span>
      <p class="note">${blocking ? "需处理或转人工" : "自审查通过"}</p>
    </div>
    <div class="metric">
      <span class="k">取舍</span>
      <span class="v">${data.tradeoffs.counts.total}</span>
      <p class="note">${data.tradeoffs.counts.requirement_vs_schedule} 条涉及需求</p>
    </div>`;
}

function renderSnapshotPanel(data) {
  const counts = data.snapshot.counts;
  const rows = data.snapshot.items.map((item) => {
    const source = item.source || {};
    const where = source.section
      + (source.start_line ? ` · 第 ${source.start_line} 行` : "");
    return `<tr>
      <td class="tag-mono">${escapeHtml(item.id)}</td>
      <td><span class="tagtype ${escapeHtml(item.type)}">${escapeHtml(item.label)}</span></td>
      <td>${escapeHtml(SPEC_PRIORITY_LABEL[item.priority] || "")}</td>
      <td>${escapeHtml(item.text)}</td>
      <td class="src">${escapeHtml(where)}</td>
    </tr>`;
  }).join("");

  els.panelSnapshot.innerHTML = `
    <div class="subsection"><h3>需求快照 ${escapeHtml(data.snapshot.id)} v${data.snapshot.version}</h3>
      <span>明确要求 ${counts.requirement} ｜ 约束 ${counts.constraint} ｜ 待澄清 ${counts.open}</span></div>
    <table class="spec-table snapshot-table">
      <thead><tr><th>编号</th><th>类型</th><th>优先级</th><th>内容</th><th>来源</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderDraftsPanel(data) {
  const reviews = data.reviews.drafts || {};
  const cards = data.drafts.map((draft) => {
    const review = reviews[draft.draft_id] || {};
    const scores = review.scores || {};
    const decisions = draft.decisions.map((decision) => {
      const flag = decision.satisfies === "full"
        ? ""
        : `<span class="mini-flag">${SPEC_SATISFIES[decision.satisfies]}</span>`;
      return `<li><span class="dec-id">${escapeHtml(decision.requirement_ids[0])}</span>
        <span>${escapeHtml(decision.choice)}${flag}</span></li>`;
    }).join("");
    return `<article class="draft-card">
      <header>
        <span class="opt-key">${escapeHtml(draft.group)}</span>
        <div><h3>${escapeHtml(draft.group_name)}</h3>
          <p class="draft-meta">承诺 ${draft.commit_days} 天 ｜ 加权 ${review.weighted ?? "—"}</p></div>
      </header>
      <p class="draft-obj">${escapeHtml(draft.objective)}</p>
      <ul class="dec-list">${decisions}</ul>
      <div class="score-row">
        <span>价值 <b>${scores.value ?? "—"}</b></span>
        <span>可行 <b>${scores.feasibility ?? "—"}</b></span>
        <span>成本 <b>${scores.cost ?? "—"}</b></span>
        <span>尝试 <b>${draft.attempts}</b> 次</span>
      </div>
    </article>`;
  }).join("");

  const trades = data.tradeoffs.items.length ? `<table class="spec-table">
      <thead><tr><th>需求</th><th>A 交付优先</th><th>B 体验优先</th><th>差额</th></tr></thead>
      <tbody>${data.tradeoffs.items.map((row) => `<tr>
        <td><b>${escapeHtml(row.requirement_id)}</b><br><span class="muted">${escapeHtml(row.requirement)}</span></td>
        <td>${escapeHtml(row.a.choice)}<br><span class="muted">${row.a.cost_days} 天</span></td>
        <td>${escapeHtml(row.b.choice)}<br><span class="muted">${row.b.cost_days} 天</span></td>
        <td>${row.delta_days > 0 ? "+" : ""}${row.delta_days} 天<br>
          <span class="tension ${escapeHtml(row.tension)}">${escapeHtml(row.tension_label)}</span></td>
      </tr>`).join("")}</tbody></table>`
    : '<p class="empty-inline">两组方案在这个输入上没有选择差异。</p>';

  const disagreements = Object.values(reviews).flatMap((review) => review.disagreements || []);
  const disRows = disagreements.length
    ? disagreements.map((row) => `<div class="dis-row">
        <span class="sev ${escapeHtml(row.severity)}">${row.severity === "blocking" ? "阻断" : "重要"}</span>
        <span class="obj-id">${escapeHtml(row.id)}</span>
        <span>${escapeHtml(row.issue)}</span>
      </div>`).join("")
    : '<p class="empty-inline">评审没有提出分歧。</p>';

  const notAdopted = (data.spec.not_adopted || []).map((row) => `
    <li><b>${escapeHtml(row.scope || row.requirement_ids.join("、"))}</b>：${escapeHtml(row.reason)}</li>`).join("");
  const notAdoptedBlock = notAdopted
    ? `<ul class="rev-changes">${notAdopted}</ul>`
    : '<p class="empty-inline">没有未采纳的差异。</p>';

  els.panelDrafts.innerHTML = `
    <div class="subsection"><h3>两组草案</h3>
      <span>${data.drafts.map((row) => `${row.group} ${row.commit_days} 天`).join(" ｜ ")}</span></div>
    <div class="ab-grid">${cards}</div>

    <div class="subsection"><h3>取舍对照</h3>
      <span>期限 ${data.tradeoffs.project.deadline_days ?? "未给出"} 天</span></div>
    ${trades}

    <div class="subsection"><h3>评审分歧</h3><span>${disagreements.length} 条</span></div>
    ${disRows}

    <div class="subsection"><h3>未采纳的差异</h3><span>${(data.spec.not_adopted || []).length} 项</span></div>
    ${notAdoptedBlock}`;
}

function renderAuditPanel(data) {
  const latest = data.audits[data.audits.length - 1];
  const checks = latest.checks;
  const chips = `
    <div class="audit-chip"><b>${checks.coverage.with_decision}/${checks.coverage.requirements}</b>需求有决策</div>
    <div class="audit-chip"><b>${checks.coverage.with_module}/${checks.coverage.requirements}</b>需求有模块</div>
    <div class="audit-chip"><b>${checks.constraints.verified}/${checks.constraints.total}</b>约束已核对</div>
    <div class="audit-chip"><b>${checks.acceptance.measurable}/${checks.acceptance.total}</b>验收可度量</div>`;

  const audits = data.audits.map((audit) => {
    const issueRows = audit.issues.length
      ? audit.issues.map((issue) => `<div class="issue">
          <span class="sev ${escapeHtml(issue.severity)}">${issue.severity === "blocking" ? "阻断" : "重要"}</span>
          <div><p><span class="obj-id">${escapeHtml(issue.id)}</span> ${escapeHtml(issue.label)}：${escapeHtml(issue.message)}</p>
            ${issue.fix_hint ? `<p class="fix">建议：${escapeHtml(issue.fix_hint)}</p>` : ""}</div>
        </div>`).join("")
      : '<p class="empty-inline">没有发现问题。</p>';
    return `<div class="audit-version">v${audit.spec_version} · ${audit.passed ? "通过" : `阻断 ${audit.counts.blocking} 条`}</div>
      ${issueRows}`;
  }).join("");

  const revisions = data.revisions.length
    ? data.revisions.map((rev) => `
      <article class="rev-card">
        <div class="rev-head">
          <span class="rev-tag">${escapeHtml(rev.id)}</span>
          <b>${escapeHtml(rev.reason)}</b>
        </div>
        <div class="rev-flow">
          <span>发现问题</span><i>→</i><span>修订 v${rev.after.version}</span><i>→</i>
          <span>复核 ${rev.recheck.passed ? "通过" : `剩余 ${rev.recheck.remaining.length} 条`}</span>
        </div>
        <ul class="rev-changes">${rev.changes.map((change) => `<li>
          <b>${escapeHtml(change.requirement_id)}</b>：
          ${change.before.cost_days} → ${change.after.cost_days} 天 ｜ ${escapeHtml(change.after.choice)}
        </li>`).join("")}</ul>
        <p class="rev-resolved">已解决 ${rev.recheck.resolved.length} 条
          <span class="rest">｜ 剩余 ${rev.recheck.remaining.length} 条</span>
          ｜ 承诺 ${rev.before.commit_days} → ${rev.after.commit_days} 天</p>
      </article>`).join("")
    : '<p class="empty-inline">本次没有触发自动修订。</p>';

  const question = data.question ? `
    <article class="q-card">
      <p class="eyebrow">Human decision</p>
      <h3>${escapeHtml(data.question.id)} · ${escapeHtml(data.question.question)}</h3>
      <ul class="q-bg">${data.question.background.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      <table class="q-options">
        <thead><tr><th>选项</th><th>方案</th><th>代价（天）</th><th>说明</th></tr></thead>
        <tbody>${data.question.options.map((option) => `<tr class="${option.key === data.question.recommended ? "rec" : ""}">
          <td class="key">${escapeHtml(option.key)}</td>
          <td>${escapeHtml(option.text)}</td>
          <td>${option.cost_days}</td>
          <td>${escapeHtml(option.consequence)}</td>
        </tr>`).join("")}</tbody>
      </table>
      <p class="q-meta">
        影响：需求 ${escapeHtml(data.question.affected.requirements.join("、") || "—")}
        ｜ 约束 ${escapeHtml(data.question.affected.constraints.join("、") || "—")}<br>
        不决策：${escapeHtml(data.question.if_undecided)}<br>
        <b>建议 ${escapeHtml(data.question.recommended)}（仅建议，不代替决定）</b>
      </p>
      ${data.question.options.some(option => option.automatic) ? `
        <form id="specAnswerForm">
          <label>决定
            <select name="option" required>
              <option value="">请选择</option>
              ${data.question.options.filter(option => option.automatic).map(option =>
                `<option value="${escapeHtml(option.key)}">${escapeHtml(option.text)}</option>`).join("")}
            </select>
          </label>
          <label>处理人 <input name="actor" maxlength="80" required></label>
          <label>理由 <input name="answer" maxlength="1000" required></label>
          <button type="submit">提交回答并复审</button>
        </form>` : '<p class="q-meta">请人工修订规格后重新审查。</p>'}
    </article>`
    : '<p class="empty-inline">没有需要人工决策的问题。</p>';

  els.panelAudit.innerHTML = `
    <div class="subsection"><h3>自审查</h3>
      <span>${latest.passed ? "通过" : `阻断 ${latest.counts.blocking} 条`} ｜ spec ${escapeHtml(data.spec.id)} v${data.spec.version}</span></div>
    <div class="audit-summary">${chips}</div>
    <div class="subsection"><h3>问题清单</h3><span>${data.audits.length} 个版本</span></div>
    ${audits}
    <div class="subsection"><h3>修订记录</h3><span>${data.revisions.length} 条</span></div>
    ${revisions}
    <div class="subsection"><h3>需人工决策</h3><span>${data.question ? data.question.id : "无"}</span></div>
    ${question}`;
}

async function submitSpecAnswer(event) {
  if (event.target.id !== "specAnswerForm") return;
  event.preventDefault();
  const data = state.spec;
  if (!data?.question) return;
  const form = event.target;
  const fields = Object.fromEntries(new FormData(form));
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const response = await fetch("/api/spec/answer", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        ...fields, run_id: data.run_id, spec_ref: data.question.spec_ref,
        question_id: data.question.id
      })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || "提交失败");
    if (state.spec !== data) return;
    const {spec_id: id, ...spec} = result.spec;
    const labels = {requirement: "明确要求", constraint: "约束", open: "待澄清"};
    renderSpecResult({
      ...data, status: result.status,
      status_label: {confirmed: "审查通过", awaiting_human: "待人工决策", blocked: "有阻断"}[result.status],
      spec: {...spec, id},
      snapshot: {id: result.snapshot.snapshot_id, version: result.snapshot.version,
        hash: result.snapshot.hash, counts: result.counts,
        items: result.snapshot.items.map(item => ({...item, label: labels[item.type]}))},
      audits: [...data.audits, result.audit], revisions: spec.revisions, question: result.question,
      markdown: result.markdown, html: result.html
    });
    switchSpecTab("audit");
    toast(result.status === "confirmed" ? "复审通过，可以下载新版规格" : "复审已完成，仍有阻断");
  } catch (error) {
    toast(error.message);
  } finally {
    submit.disabled = false;
  }
}

/* ------------------------------ 事件绑定 ------------------------------ */

function bind() {
  els.panelAudit.addEventListener("submit", submitSpecAnswer);
  els.idea.addEventListener("input", () => {
    els.ideaCount.textContent = `${els.idea.value.length} / 1200`;
    if (els.idea.value.trim()) {
      els.ideaError.hidden = true;
      els.idea.classList.remove("invalid");
    }
    if (state.case && els.idea.value !== state.loadedCaseText) {
      clearCaseSelection();
    }
  });

  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    generate();
  });

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      generate();
    }
  });

  els.addConstraint.addEventListener("click", () => {
    addConstraint(els.constraintInput.value);
    els.constraintInput.focus();
  });

  els.constraintInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addConstraint(els.constraintInput.value);
    } else if (event.key === "Backspace" && !els.constraintInput.value && state.constraints.length) {
      state.constraints.pop();
      renderChips();
    }
  });

  els.chips.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-index]");
    if (!button) return;
    state.constraints.splice(Number(button.dataset.index), 1);
    renderChips();
  });

  els.modeSwitch.addEventListener("click", (event) => {
    const button = event.target.closest(".mode");
    if (button) setMode(button.dataset.mode);
  });

  els.caseField.addEventListener("click", (event) => {
    const button = event.target.closest(".case-btn");
    if (button) pickCase(button.dataset.case || "");
  });

  els.specTabs.addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) switchSpecTab(tab.dataset.stab);
  });

  els.specBanner.addEventListener("click", (event) => {
    const link = event.target.closest("[data-goto]");
    if (link) switchSpecTab(link.dataset.goto);
  });

  els.specPrint.addEventListener("click", () => {
    switchSpecTab("doc");
    setTimeout(() => window.print(), 60);
  });

  els.rounds.addEventListener("input", () => { els.roundsOut.textContent = els.rounds.value; });
  els.threshold.addEventListener("input", () => { els.thresholdOut.textContent = `${els.threshold.value}%`; });

  $$(".example").forEach((button) => {
    button.addEventListener("click", () => {
      els.idea.value = button.dataset.idea;
      els.ideaCount.textContent = `${els.idea.value.length} / 1200`;
      els.idea.focus();
    });
  });

  els.tabs.addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) switchTab(tab.dataset.tab);
  });

  els.btnCopy.addEventListener("click", async () => {
    if (!state.result) return;
    try {
      await navigator.clipboard.writeText(state.result.markdown);
      toast("已复制 Markdown 全文");
    } catch {
      const area = document.createElement("textarea");
      area.value = state.result.markdown;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast("已复制 Markdown 全文");
    }
  });

  els.btnPrint.addEventListener("click", () => {
    switchTab("doc");
    setTimeout(() => window.print(), 60);
  });
  els.retry.addEventListener("click", () => generate());
  els.btnHelp.addEventListener("click", () => els.help.showModal());
}

async function checkServer() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("bad status");
    els.status.className = "status ok";
    els.status.innerHTML = "<i></i>本地服务正常 · 离线 Mock";
  } catch {
    els.status.className = "status bad";
    els.status.innerHTML = "<i></i>未连接到服务";
    if (location.protocol === "file:") {
      showError({
        code: "NEED_SERVER",
        message: "当前是直接打开文件。请在项目目录运行 python -m ideaflow.server，再访问 http://127.0.0.1:8000/",
      });
    }
  }
}

const presetIdea = query.get("idea");
const presetCase = query.get("case");
const autoRun = query.get("auto") === "1";

setMode(query.get("mode") === "spec" ? "spec" : "proposal");
if (query.get("live") === "1") els.liveModel.checked = true;
bind();
renderChips();
checkServer();

if (presetIdea) {
  els.idea.value = presetIdea.slice(0, 1200);
  els.ideaCount.textContent = `${els.idea.value.length} / 1200`;
  query.getAll("constraint").forEach(addConstraint);
}

/* 演示链接：/?mode=spec&case=campus-books-tight&auto=1 直接出规格 */
if (state.mode === "spec" && presetCase) {
  pickCase(presetCase).then(() => {
    if (autoRun) generate();
  });
} else if (presetIdea && autoRun) {
  generate();
}
