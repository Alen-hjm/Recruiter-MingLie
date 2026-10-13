/* Minglie desktop companion. Model calls and assistant runs belong to the workspace. */
"use strict";
const $ = (id) => document.getElementById(id);
const STAGES = {
  recommended: "待沟通",
  contacted: "已联系",
  submitted: "已推荐",
  interview: "面试中",
  offer: "Offer",
  hired: "已录用",
  rejected: "已结束",
};
const state = {
  base: "http://127.0.0.1:5000",
  token: "",
  legacyKey: "",
  jobId: null,
  jobs: [],
  resumes: [],
  pipeline: {},
  panel: "chat",
  stage: "",
  connected: false,
  collecting: false,
  submitting: false,
  sending: false,
  run: null,
  batch: [],
  batchJob: null,
  epoch: 0,
  scroll: {},
  listVersion: 0,
};
let pollTimer, toastTimer;
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const formatReply = (value) =>
  escapeHtml(value)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.*?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
function siteUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "https:" &&
      (u.hostname === "liepin.com" || u.hostname.endsWith(".liepin.com"))
      ? u.href
      : "";
  } catch {
    return "";
  }
}
function baseUrl(value) {
  const u = new URL(value);
  if (
    !["http:", "https:"].includes(u.protocol) ||
    u.username ||
    u.password ||
    u.search ||
    u.hash ||
    u.pathname !== "/"
  )
    throw new Error("请填写工作台的完整地址，例如 http://127.0.0.1:5000");
  if (
    u.protocol !== "https:" &&
    !["localhost", "127.0.0.1", "[::1]"].includes(u.hostname)
  )
    throw new Error("远程工作台请使用 HTTPS 地址");
  return u.origin;
}
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 4200);
}
function activity(message) {
  $("activity").textContent = message;
  $("activity").hidden = !message;
}
async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.token
      ? { "X-API-Token": state.token }
      : state.legacyKey
        ? { "X-API-Key": state.legacyKey }
        : {}),
  };
  let response;
  try {
    response = await fetch(state.base + "/api/extension" + path, {
      ...options,
      headers,
      credentials: "omit",
      redirect: "error",
      signal: AbortSignal.timeout(20000),
    });
  } catch {
    throw new Error("连接暂时中断，请确认工作台已启动，然后刷新连接。");
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("服务地址没有返回有效结果，请检查工作台地址。");
  }
  if (!response.ok || data.success === false) {
    const error = new Error(
      response.status === 401 || response.status === 403
        ? "令牌已失效或无权访问，请在设置中重新连接。"
        : data.error || "处理失败，请稍后重试。",
    );
    error.status = response.status;
    throw error;
  }
  return data;
}
function connection(online, message) {
  state.connected = online;
  $("connectionState").textContent = message;
  $("connectionDot").classList.toggle("online", online);
  $("settingsStatus").textContent = message;
}
function requireConnection() {
  if (state.connected) return true;
  switchPanel("settings");
  toast("先连接工作台，即可使用招聘助手。", true);
  return false;
}
function openWorkspace(path = "/agent", useInput = false) {
  try {
    const base = useInput ? baseUrl($("inputServer").value.trim()) : state.base;
    chrome.tabs.create({ url: base + path });
  } catch (e) {
    toast(e.message, true);
  }
}
function switchPanel(name) {
  if (!["chat", "resumes", "pipeline", "settings"].includes(name)) return;
  state.scroll[state.panel] = $("content").scrollTop;
  state.panel = name;
  document
    .querySelectorAll(".panel")
    .forEach((el) => el.classList.toggle("active", el.id === "panel-" + name));
  document.querySelectorAll(".tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.panel === name);
    if (el.dataset.panel === name) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("composer").hidden = name !== "chat";
  $("composerHint").hidden = name !== "chat";
  $("content").scrollTop = state.scroll[name] || 0;
  if (name === "resumes") loadResumes();
  if (name === "pipeline") loadPipeline();
}
function confirmAction(title, message) {
  return new Promise((resolve) => {
    const dialog = $("confirmDialog");
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = message;
    const finish = (value) => {
      dialog.close();
      dialog.oncancel = null;
      resolve(value);
    };
    $("cancelConfirm").onclick = () => finish(false);
    $("acceptConfirm").onclick = () => finish(true);
    dialog.oncancel = (e) => {
      e.preventDefault();
      finish(false);
    };
    dialog.showModal();
    $("cancelConfirm").focus();
  });
}
function empty(
  el,
  message,
  action = "向助手描述需求",
  callback = () => {
    switchPanel("chat");
    $("chatInput").focus();
  },
) {
  el.innerHTML = `<div class="empty"><span class="empty-icon" aria-hidden="true">⌕</span><span>${escapeHtml(message)}</span><button class="btn secondary">${escapeHtml(action)}</button></div>`;
  el.querySelector("button").onclick = callback;
}
async function connect() {
  const epoch = ++state.epoch;
  clearTimeout(pollTimer);
  state.run = null;
  state.resumes = [];
  state.pipeline = {};
  state.listVersion++;
  $("resumeList").replaceChildren();
  $("pipelineContent").replaceChildren();
  $("pipelineBar").replaceChildren();
  $("resumeCount").textContent = "0 位";
  $("chatMessages").replaceChildren();
  $("runCard").hidden = true;
  $("welcome").hidden = false;
  renderRun();
  if (!state.token && !state.legacyKey) {
    connection(false, "尚未连接工作台");
    $("selJobGlobal").innerHTML = '<option value="">连接后加载岗位</option>';
    return;
  }
  connection(false, "正在连接…");
  try {
    const data = await api("/jobs");
    if (epoch !== state.epoch) return;
    applyJobs(data.data || []);
    connection(true, "工作台已连接");
    await restoreChat(epoch);
  } catch (e) {
    if (epoch === state.epoch) {
      connection(false, "连接需要检查");
      $("settingsStatus").textContent = e.message;
    }
  }
}
function applyJobs(jobs) {
  state.jobs = jobs;
  const selected = jobs.find((j) => Number(j.id) === Number(state.jobId));
  state.jobId = selected ? Number(selected.id) : null;
  $("selJobGlobal").innerHTML =
    '<option value="">新需求，无需预先选择</option>' +
    jobs
      .map(
        (j) => `<option value="${Number(j.id)}">${escapeHtml(j.name)}</option>`,
      )
      .join("");
  $("selJobGlobal").value = state.jobId || "";
}
async function refreshJobs() {
  const epoch = state.epoch;
  const data = await api("/jobs");
  if (epoch === state.epoch) applyJobs(data.data || []);
}
function guide() {
  switchPanel("chat");
  $("welcome").hidden = false;
  $("onboarding").hidden = false;
  $("onboarding").scrollIntoView({ block: "center" });
}
async function checkPage() {
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (!siteUrl(tab?.url)) {
      $("pageState").textContent = "可直接描述招聘需求";
      $("analyzePage").title = "打开猎聘简历详情页后可分析";
      return;
    }
    let info;
    try {
      info = await chrome.tabs.sendMessage(tab.id, { action: "get_page_info" });
    } catch {}
    $("pageState").textContent = info?.isDetailPage
      ? "当前：简历详情"
      : info
        ? "当前：猎聘页面"
        : "猎聘页面需刷新";
    $("analyzePage").title = info?.isDetailPage
      ? "读取并保存当前简历，提交岗位匹配分析"
      : "请在猎聘打开简历详情";
  } catch {
    $("pageState").textContent = "未读取页面";
  }
}
function appendMessage(role, content) {
  $("welcome").hidden = true;
  const el = document.createElement("article");
  el.className = "msg msg-" + (role === "user" ? "user" : "ai");
  el.innerHTML = `<div class="msg-label">${role === "user" ? "你" : "明猎"}</div><div class="msg-bubble">${formatReply(content)}</div>`;
  $("chatMessages").append(el);
  return el;
}
async function restoreChat(epoch = state.epoch) {
  const [history, run] = await Promise.all([
    api("/chat/history"),
    api("/chat/run"),
  ]);
  if (epoch !== state.epoch) return;
  $("chatMessages").replaceChildren();
  $("welcome").hidden = false;
  for (const row of history.data || []) appendMessage(row.role, row.content);
  state.run = run.data;
  if (
    !state.jobId &&
    state.run?.job_id &&
    state.jobs.some((j) => Number(j.id) === Number(state.run.job_id))
  ) {
    state.jobId = Number(state.run.job_id);
    $("selJobGlobal").value = state.jobId;
  }
  renderRun();
  if (state.run?.status === "running") schedulePoll();
}
function renderRun() {
  const run = state.run;
  $("runCard").hidden = !run;
  const running = run?.status === "running";
  $("btnSend").disabled = running || state.sending;
  $("clearChat").disabled = running || state.sending;
  $("composerHint").textContent = running
    ? "助手正在处理，可先写下下一条补充需求"
    : "Enter 发送 · Shift + Enter 换行";
  if (!run) return;
  $("runLabel").textContent =
    run.label || (running ? "正在处理需求" : "本轮处理结束");
  $("runSpinner").hidden = !running;
  document.querySelectorAll("[data-phase]").forEach((el) => {
    el.classList.toggle(
      "done",
      (run.completed_phases || []).includes(el.dataset.phase),
    );
    el.classList.toggle(
      "current",
      run.phase === el.dataset.phase ||
        (run.phase === "planning" && el.dataset.phase === "understanding"),
    );
  });
  const counts = [];
  if (run.scrape) counts.push(`已收集 ${run.scrape.result_count || 0} 份`);
  if (run.score) counts.push(`分析任务 ${run.score.total_count || 0} 份`);
  $("runDetail").textContent =
    counts.join(" · ") ||
    (running ? "切换面板不会中断任务。" : "结果已同步工作台。");
}
function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollRun, 1600);
}
async function pollRun() {
  const epoch = state.epoch;
  try {
    const data = await api("/chat/run");
    if (epoch !== state.epoch) return;
    const previous = state.run;
    state.run = data.data;
    renderRun();
    if (state.run?.status === "running") schedulePoll();
    else if (previous?.status === "running") {
      await restoreChat(epoch);
      if (epoch !== state.epoch) return;
      if (state.run?.job_id) {
        state.jobId = Number(state.run.job_id);
        await chrome.storage.local.set({ selectedJobId: state.jobId });
      }
      await refreshJobs();
      toast(
        state.run?.status === "done"
          ? "本轮需求已处理，查看助手结果。"
          : "执行已结束，请查看结果和待处理事项。",
      );
      if (state.panel === "resumes") loadResumes();
      if (state.panel === "pipeline") loadPipeline();
    }
  } catch (e) {
    if (epoch !== state.epoch) return;
    $("runLabel").textContent = "连接中断，正在重新获取进度";
    $("runDetail").textContent = "任务可能仍在工作台运行，请勿重复发送。";
    pollTimer = setTimeout(pollRun, 5000);
  }
}
async function sendChat() {
  const text = $("chatInput").value.trim();
  if (!text || state.sending || state.run?.status === "running") return;
  if (!requireConnection()) return;
  const epoch = state.epoch;
  state.sending = true;
  renderRun();
  const job = state.jobs.find((j) => Number(j.id) === state.jobId);
  const message = job
    ? `当前关联岗位：${job.name}（岗位 ID：${job.id}）。\n用户需求：${text}`
    : text;
  try {
    const data = await api("/chat/run", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    if (epoch !== state.epoch) return;
    $("chatInput").value = "";
    chrome.storage.local.remove("chatDraft");
    resizeInput();
    appendMessage("user", text);
    state.run = data.data;
    renderRun();
    schedulePoll();
    $("content").scrollTop = $("content").scrollHeight;
  } catch (e) {
    if (epoch !== state.epoch) return;
    toast(e.message, true);
    try {
      await restoreChat(epoch);
    } catch {}
  } finally {
    state.sending = false;
    renderRun();
  }
}
async function clearChat() {
  if (!requireConnection()) return;
  if (
    !(await confirmAction(
      "清空会话？",
      "扩展与工作台共享这份对话。岗位、候选人和跟进记录会保留。",
    ))
  )
    return;
  try {
    await api("/chat/clear", { method: "POST" });
    clearTimeout(pollTimer);
    state.run = null;
    $("chatMessages").replaceChildren();
    $("welcome").hidden = false;
    renderRun();
    toast("会话已清空。");
  } catch (e) {
    toast(e.message, true);
  }
}
function resizeInput() {
  const el = $("chatInput");
  el.style.height = "auto";
  el.style.height = Math.min(110, el.scrollHeight) + "px";
}
function scoreOf(row) {
  const value = row.score100?.total_score;
  return value === null ||
    value === undefined ||
    value === "" ||
    !Number.isFinite(Number(value))
    ? null
    : Number(value);
}
async function loadResumes() {
  const el = $("resumeList"),
    job = state.jobId,
    epoch = state.epoch,
    version = ++state.listVersion;
  if (!state.connected) {
    empty(el, "连接工作台后，即可查看你的候选人。", "连接工作台", () =>
      switchPanel("settings"),
    );
    return;
  }
  if (!job) {
    empty(el, "在底部选择岗位，或让助手开始新的招聘。 ");
    return;
  }
  if (!state.resumes.length)
    el.innerHTML =
      '<div class="empty"><span class="loading"></span>正在读取候选人</div>';
  try {
    const data = await api(`/resumes/${job}`);
    if (
      epoch !== state.epoch ||
      job !== state.jobId ||
      version !== state.listVersion
    )
      return;
    state.resumes = data.data || [];
    renderResumes();
  } catch (e) {
    if (epoch === state.epoch && version === state.listVersion)
      empty(el, e.message, "重试", loadResumes);
  }
}
function renderResumes() {
  const q = $("candidateSearch").value.trim().toLowerCase(),
    filter = $("selFilter").value;
  let rows = state.resumes.filter((r) =>
    [r.name, r.title, r.education, r.experience, r.location, r.summary]
      .join(" ")
      .toLowerCase()
      .includes(q),
  );
  rows = rows.filter((r) => {
    const s = scoreOf(r);
    return (
      !filter ||
      (filter === "pending"
        ? s === null
        : s !== null &&
          (filter === "high"
            ? s >= 80
            : filter === "mid"
              ? s >= 60 && s < 80
              : s < 60))
    );
  });
  rows.sort(
    $("selSort").value === "time"
      ? (a, b) =>
          (Date.parse(b.collected_at) || 0) - (Date.parse(a.collected_at) || 0)
      : (a, b) => (scoreOf(b) ?? -1) - (scoreOf(a) ?? -1),
  );
  $("resumeCount").textContent = `${rows.length} 位`;
  const el = $("resumeList");
  el.replaceChildren();
  if (!rows.length) {
    empty(
      el,
      state.resumes.length
        ? "没有符合当前筛选条件的人选。"
        : "还没有候选人，告诉助手你想招谁。",
      state.resumes.length ? "清除筛选" : "开始招聘",
      () => {
        if (state.resumes.length) {
          $("candidateSearch").value = "";
          $("selFilter").value = "";
          renderResumes();
        } else {
          switchPanel("chat");
          $("chatInput").focus();
        }
      },
    );
    return;
  }
  for (const row of rows) {
    const name = row.name || row.title || "未命名候选人",
      score = scoreOf(row),
      url = siteUrl(row.detail_url),
      s = row.score100 || {};
    const card = document.createElement("article");
    card.className = "candidate";
    card.innerHTML = `<div class="candidate-head"><span class="avatar">${escapeHtml(name.slice(0, 1))}</span><span class="candidate-name">${escapeHtml(name)}</span><span class="score">${score === null ? "<small>待分析</small>" : `${score}<small> 分</small>`}</span></div><p class="meta">${escapeHtml([row.title, row.education, row.experience, row.location].filter(Boolean).join(" · ") || "经历待补充")}</p><div class="tags">${(Array.isArray(
      row.skill_tags,
    )
      ? row.skill_tags
      : []
    )
      .slice(0, 4)
      .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
      .join(
        "",
      )}</div><details><summary>查看匹配依据</summary><p>${formatReply(s.recommend_reason || s.summary || row.summary || "暂无分析结果。可让助手继续分析。")}</p>${s.risk ? `<p>待核实：${escapeHtml(s.risk)}</p>` : ""}</details><div class="button-row"><button class="btn secondary" data-action="analyze">询问助手</button><button class="btn secondary" data-action="add">加入跟进</button>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">查看原简历 ↗</a>` : ""}</div>`;
    card.querySelector('[data-action="analyze"]').onclick = () =>
      draft(`请分析候选人「${name}」，说明匹配依据、待核实信息和下一步建议。`);
    card.querySelector('[data-action="add"]').onclick = (e) =>
      addPipeline(row, e.currentTarget);
    el.append(card);
  }
}
function draft(text) {
  switchPanel("chat");
  $("chatInput").value = text;
  resizeInput();
  $("chatInput").focus();
  chrome.storage.local.set({ chatDraft: text });
}
async function addPipeline(row, button) {
  if (!requireConnection() || !state.jobId) return;
  button.disabled = true;
  try {
    await api(`/pipeline/${state.jobId}/add`, {
      method: "POST",
      body: JSON.stringify({
        name: row.name || row.title || "未知",
        detail_url: row.detail_url || "",
        score: scoreOf(row) ?? 0,
      }),
    });
    button.textContent = "已加入";
    toast("已加入招聘跟进。");
  } catch (e) {
    toast(e.message, true);
    button.disabled = false;
  }
}
async function loadPipeline() {
  const el = $("pipelineContent"),
    job = state.jobId,
    epoch = state.epoch;
  if (!state.connected) {
    empty(el, "连接工作台后查看招聘进展。", "连接工作台", () =>
      switchPanel("settings"),
    );
    return;
  }
  if (!job) {
    $("pipelineBar").replaceChildren();
    empty(el, "先在底部选择需要跟进的岗位。 ");
    return;
  }
  try {
    const data = await api(`/pipeline/${job}`);
    if (epoch !== state.epoch || job !== state.jobId) return;
    state.pipeline = data;
    renderPipeline();
  } catch (e) {
    if (epoch === state.epoch && job === state.jobId)
      empty(el, e.message, "重试", loadPipeline);
  }
}
function renderPipeline() {
  const bar = $("pipelineBar"),
    el = $("pipelineContent");
  el.replaceChildren();
  bar.replaceChildren();
  for (const [key, label] of Object.entries({ "": "全部", ...STAGES })) {
    const count = key
      ? (state.pipeline.stages?.[key] || []).length
      : Object.values(state.pipeline.stages || {}).reduce(
          (sum, list) => sum + list.length,
          0,
        );
    const button = document.createElement("button");
    button.className = "pipeline-chip" + (key === state.stage ? " active" : "");
    button.textContent = `${label} ${count}`;
    button.onclick = () => {
      state.stage = key;
      renderPipeline();
    };
    bar.append(button);
  }
  for (const [key, label] of Object.entries(STAGES)) {
    if (state.stage && state.stage !== key) continue;
    const rows = state.pipeline.stages?.[key] || [];
    if (!rows.length) continue;
    const heading = document.createElement("h3");
    heading.className = "stage-heading";
    heading.textContent = label;
    el.append(heading);
    for (const row of rows) {
      const card = document.createElement("article");
      card.className = "candidate";
      card.innerHTML = `<div class="candidate-name">${escapeHtml(row.candidate_name || "未命名候选人")}</div><p class="meta">${escapeHtml(row.reason || "可让助手提供沟通建议。")}</p><div class="button-row"><button class="btn secondary" data-action="ask">下一步建议</button><button class="text-btn" data-action="remove">移出跟进</button></div>`;
      card.querySelector('[data-action="ask"]').onclick = () =>
        draft(
          `候选人「${row.candidate_name}」目前处于「${label}」，请给出下一步跟进建议。`,
        );
      card.querySelector('[data-action="remove"]').onclick = async () => {
        const job = state.jobId;
        if (
          !(await confirmAction(
            "移出跟进？",
            `将「${row.candidate_name}」从当前岗位的跟进记录移除。`,
          ))
        )
          return;
        try {
          await api(`/pipeline/${job}/delete`, {
            method: "POST",
            body: JSON.stringify({ id: row.id }),
          });
          loadPipeline();
          toast("已移出跟进。");
        } catch (e) {
          toast(e.message, true);
        }
      };
      el.append(card);
    }
  }
  if (!el.children.length)
    empty(el, "这个阶段还没有人选。", "查看候选人", () =>
      switchPanel("resumes"),
    );
}
function updateBatch() {
  $("collectBar").hidden = !state.batch.length;
  $("collectedCount").textContent = state.batch.length;
  $("retrySubmit").disabled = state.submitting;
  $("clearCollected").disabled = state.submitting;
}
async function saveBatch() {
  await chrome.storage.local.set({
    collectedResumes: state.batch,
    collectedJobId: state.batchJob,
  });
  updateBatch();
}
async function analyzePage() {
  if (state.collecting || state.submitting) return;
  if (!requireConnection()) return;
  if (!state.jobId) {
    toast("先在底部选择岗位，才能判断当前简历是否匹配。", true);
    $("selJobGlobal").focus();
    return;
  }
  if (state.batch.length && state.batchJob !== state.jobId) {
    toast("还有其他岗位的简历待提交，请先处理待提交记录。", true);
    return;
  }
  const job = state.jobId;
  state.collecting = true;
  $("analyzePage").disabled = true;
  $("selJobGlobal").disabled = true;
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (!siteUrl(tab?.url))
      throw new Error("请在猎聘打开一份简历详情，再点击分析。");
    const info = await chrome.tabs.sendMessage(tab.id, {
      action: "get_page_info",
    });
    if (!info?.isDetailPage)
      throw new Error("当前页面不是简历详情，请先打开候选人的简历。");
    activity("正在读取你打开的简历…");
    const result = await chrome.tabs.sendMessage(tab.id, {
      action: "scrape_resume",
    });
    if (!result?.success || !result.full_text)
      throw new Error(
        result?.error || "没有读取到简历，请刷新猎聘页面后重试。",
      );
    if (!state.batch.some((r) => r.detail_url === result.detail_url))
      state.batch.push({
        name: result.name || "未知",
        full_text: result.full_text,
        detail_url: result.detail_url,
        collected_at: new Date().toISOString(),
      });
    state.batchJob = job;
    await saveBatch();
    await submitBatch();
  } catch (e) {
    const message = e.message.includes("Receiving end")
      ? "请刷新猎聘页面后重试，让扩展连接当前页面。"
      : e.message;
    activity(message);
    toast(message, true);
  } finally {
    state.collecting = false;
    $("analyzePage").disabled = false;
    $("selJobGlobal").disabled = false;
  }
}
async function submitBatch() {
  if (state.submitting || !state.batch.length || !requireConnection()) return;
  if (!state.batchJob) {
    toast("这批待提交简历尚未关联岗位，请先移除后重新读取。", true);
    return;
  }
  state.submitting = true;
  updateBatch();
  activity("正在保存简历并提交分析…");
  try {
    const data = await api("/submit_batch", {
      method: "POST",
      body: JSON.stringify({ job_id: state.batchJob, resumes: state.batch }),
    });
    state.batch = [];
    state.batchJob = null;
    await saveBatch();
    activity(
      data.score_task_id
        ? "简历已保存，正在后台分析；稍后刷新候选人可查看结果。"
        : data.message ||
            "简历已保存。请在工作台完善岗位描述和模型设置后分析。",
    );
    toast("简历已保存到工作台。");
    if (state.panel === "resumes") loadResumes();
  } catch (e) {
    activity("提交未确认，简历保留在待提交列表。请核对工作台后重试。");
    toast(e.message, true);
  } finally {
    state.submitting = false;
    updateBatch();
  }
}
async function saveSettings(event) {
  event.preventDefault();
  if (state.sending || state.submitting || state.collecting) {
    toast("当前操作完成后可更换连接。", true);
    return;
  }
  const button = $("saveSettings");
  try {
    const base = baseUrl($("inputServer").value.trim()),
      token = $("inputApiToken").value.trim();
    if (!token && !(state.legacyKey && base === state.base))
      throw new Error("请粘贴工作台生成的扩展访问令牌。");
    const u = new URL(base),
      origin = `${u.protocol}//${u.hostname}/*`;
    const allowed = await chrome.permissions.request({ origins: [origin] });
    if (!allowed)
      throw new Error("未获得工作台地址的访问权限，连接信息未更改。");
    if (state.batch.length && (base !== state.base || token !== state.token)) {
      toast("更换连接前，请先处理待提交简历。", true);
      return;
    }
    button.disabled = true;
    button.textContent = "连接中…";
    state.base = base;
    state.token = token;
    await chrome.storage.local.set({ serverUrl: base, apiToken: token });
    await connect();
    toast(
      state.connected
        ? "已连接。回到助手，描述你的招聘需求。"
        : "设置已保存，请检查连接提示。",
      !state.connected,
    );
  } catch (e) {
    toast(e.message, true);
    $("settingsStatus").textContent = e.message;
  } finally {
    button.disabled = false;
    button.textContent = "保存并连接";
  }
}
async function init() {
  document
    .querySelectorAll(".tab")
    .forEach((el) => (el.onclick = () => switchPanel(el.dataset.panel)));
  document
    .querySelectorAll("[data-prompt]")
    .forEach((el) => (el.onclick = () => draft(el.dataset.prompt)));
  $("btnSend").onclick = sendChat;
  $("clearChat").onclick = clearChat;
  $("analyzePage").onclick = analyzePage;
  $("retrySubmit").onclick = submitBatch;
  $("clearCollected").onclick = async () => {
    if (
      !(await confirmAction(
        "移除待提交简历？",
        "这些本地暂存简历尚未确认保存。移除后需重新打开页面读取。",
      ))
    )
      return;
    state.batch = [];
    state.batchJob = null;
    await saveBatch();
  };
  $("settingsForm").onsubmit = saveSettings;
  $("openWorkspace").onclick = () => openWorkspace();
  $("runWorkspace").onclick = () => openWorkspace();
  $("managePipeline").onclick = () => openWorkspace("/interview");
  $("openWebSettings").onclick = () =>
    openWorkspace("/settings#extension", true);
  $("openModelSettings").onclick = () => openWorkspace("/settings#model");
  $("connectionState").onclick = () => switchPanel("settings");
  $("guideConnect").onclick = () => switchPanel("settings");
  $("showGuide").onclick = guide;
  $("settingsGuide").onclick = guide;
  $("dismissGuide").onclick = () => {
    $("onboarding").hidden = true;
    chrome.storage.local.set({ extensionGuideSeen: true });
  };
  $("refresh").onclick = async () => {
    const button = $("refresh");
    button.disabled = true;
    await connect();
    await checkPage();
    if (state.panel === "resumes") loadResumes();
    if (state.panel === "pipeline") loadPipeline();
    button.disabled = false;
  };
  $("chatInput").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendChat();
    }
  };
  $("chatInput").oninput = () => {
    resizeInput();
    chrome.storage.local.set({ chatDraft: $("chatInput").value });
  };
  $("selJobGlobal").onchange = () => {
    state.jobId = Number($("selJobGlobal").value) || null;
    state.resumes = [];
    state.pipeline = {};
    state.listVersion++;
    chrome.storage.local.set({ selectedJobId: state.jobId });
    if (state.panel === "resumes") loadResumes();
    if (state.panel === "pipeline") loadPipeline();
  };
  $("candidateSearch").oninput = renderResumes;
  $("selFilter").onchange = renderResumes;
  $("selSort").onchange = renderResumes;
  try {
    const stored = await chrome.storage.local.get([
      "apiToken",
      "apiKey",
      "serverUrl",
      "selectedJobId",
      "collectedResumes",
      "collectedJobId",
      "extensionGuideSeen",
      "chatDraft",
    ]);
    state.token = stored.apiToken || "";
    state.legacyKey = stored.apiKey || "";
    state.base = baseUrl(stored.serverUrl || state.base);
    state.jobId = Number(stored.selectedJobId) || null;
    state.batch = Array.isArray(stored.collectedResumes)
      ? stored.collectedResumes
      : [];
    state.batchJob = Number(stored.collectedJobId) || null;
    $("inputServer").value = state.base;
    $("inputApiToken").value = state.token;
    $("chatInput").value = stored.chatDraft || "";
    $("onboarding").hidden = !!stored.extensionGuideSeen;
    resizeInput();
    updateBatch();
    await Promise.all([connect(), checkPage()]);
    chrome.tabs.onActivated.addListener(checkPage);
    chrome.tabs.onUpdated.addListener((id, change, tab) => {
      if (tab.active && (change.status === "complete" || change.url))
        checkPage();
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      let changed = false;
      if (changes.apiToken && changes.apiToken.newValue !== state.token) {
        state.token = changes.apiToken.newValue || "";
        $("inputApiToken").value = state.token;
        changed = true;
      }
      if (changes.apiKey && changes.apiKey.newValue !== state.legacyKey) {
        state.legacyKey = changes.apiKey.newValue || "";
        changed = true;
      }
      if (changes.serverUrl && changes.serverUrl.newValue !== state.base) {
        try {
          state.base = baseUrl(changes.serverUrl.newValue);
          $("inputServer").value = state.base;
          changed = true;
        } catch (e) {
          toast(e.message, true);
        }
      }
      if (changed) connect();
    });
  } catch (e) {
    connection(false, "请检查连接设置");
    toast(e.message, true);
  }
}
document.addEventListener("DOMContentLoaded", init);
