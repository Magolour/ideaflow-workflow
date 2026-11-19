/* IdeaFlow 工作台前端：提交想法 → 轮询阶段 → 渲染结果 → 下载。 */

const state = {
  constraints: [],
  jobId: null,
  result: null,
  pollTimer: null,
  clockTimer: null,
  startedAt: 0,
  busy: false,
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
  [els.empty, els.runPanel, els.errorPanel, els.resultPanel].forEach((node) => {
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
  els.generateLabel.textContent = busy ? "正在生成…" : "生成设计文档";
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
  els.timeline.innerHTML = "";
  els.runTitle.textContent = "正在生成设计文档";
  showOnly(els.runPanel);
  startClock();

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({
        idea,
        constraints: state.constraints,
        max_rounds: Number(els.rounds.value),
        threshold: Number(els.threshold.value) / 100,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error?.message || `服务返回 ${response.status}`);
    }
    state.jobId = payload.job_id;
    pollJob();
  } catch (error) {
    showError({ code: "NETWORK", message: `${error.message}。请确认本地服务已启动。` });
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
        renderResult(job.result);
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

/* ------------------------------ 事件绑定 ------------------------------ */

function bind() {
  els.idea.addEventListener("input", () => {
    els.ideaCount.textContent = `${els.idea.value.length} / 1200`;
    if (els.idea.value.trim()) {
      els.ideaError.hidden = true;
      els.idea.classList.remove("invalid");
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

bind();
renderChips();
checkServer();

/* 支持用链接预填想法：/?idea=…&constraint=…&auto=1 直接出结果，便于演示与分享。 */
const params = new URLSearchParams(location.search);
const presetIdea = params.get("idea");
if (presetIdea) {
  els.idea.value = presetIdea.slice(0, 1200);
  els.ideaCount.textContent = `${els.idea.value.length} / 1200`;
  (params.getAll("constraint") || []).forEach(addConstraint);
  if (params.get("auto") === "1") {
    generate();
  }
}
