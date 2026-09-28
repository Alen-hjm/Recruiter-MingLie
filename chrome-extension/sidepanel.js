/**
 * 明猎 - Side Panel 逻辑
 * 主页：AI 对话（调用知识库 + 岗位 + 候选人）
 *
 * 重构要点：
 *  - 头部全局岗位下拉 selJobGlobal 驱动所有面板（替代原先 3 个独立下拉）
 *  - 入口页统计卡片（推荐/面试/淘汰 + 进行中）
 *  - 底部「一键抓取」chip 直接发起抓取并跳到候选人页
 *  - 页面间带上下文跳转（候选人/管道 → AI 助手）
 */

let API_BASE = "http://127.0.0.1:5000";
let API_TOKEN = "";   // 扩展访问令牌（推荐），与 LLM Key 无关
let API_KEY = "";     // 兼容旧配置：LLM Key 当凭据（后端可在兼容期内接受）
let state = { running: false, jobId: null, jobName: "", allResumes: [], stats: {}, currentPage: 0, aiContext: null, pendingCandidate: null };

// ══════════════════════════════════════════
// 初始化
// ══════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  // ══════════════════════════════════════════
  // 第一步：同步绑定所有事件（不依赖网络）
  // 每个都加 if 保护，互不影响
  // ══════════════════════════════════════════

  // ✅ 标签切换
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => switchToPanel(tab.dataset.panel));
  });

  // ✅ AI 对话
  const btnSend = document.getElementById("btnSend");
  if (btnSend) btnSend.addEventListener("click", sendChat);

  const chatInput = document.getElementById("chatInput");
  if (chatInput) {
    chatInput.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChat();
      }
    });
    chatInput.addEventListener("input", autoResize);
  }

  // ✅ 快捷卡片
  document.querySelectorAll(".quick-card[data-msg], .chip[data-msg]").forEach(el => {
    el.addEventListener("click", () => {
      const msg = el.dataset.msg;
      if (msg) {
        const input = document.getElementById("chatInput");
        if (input) input.value = msg;
        sendChat();
      }
    });
  });

  // ✅ 一键抓取
  const quickScrape = document.getElementById("quickScrape");
  if (quickScrape) quickScrape.addEventListener("click", onQuickScrape);

  // ✅ 清空对话
  const btnClearChat = document.getElementById("btnClearChat");
  if (btnClearChat) btnClearChat.addEventListener("click", clearChat);

  // ✅ 刷新
  const btnRefresh = document.getElementById("btnRefresh");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => {
      checkServer().then(() => loadJobs());
      checkPage();
    });
  }

  // ✅ 提交评分
  const btnSubmit = document.getElementById("btnSubmit");
  if (btnSubmit) {
    btnSubmit.addEventListener("click", submitResumes);
  }

  // ✅ 清空收集
  const btnClearCollect = document.getElementById("btnClearCollect");
  if (btnClearCollect) btnClearCollect.addEventListener("click", clearCollected);

  // ✅ 设置
  const btnSaveSettings = document.getElementById("btnSaveSettings");
  if (btnSaveSettings) btnSaveSettings.addEventListener("click", saveSettings);

  const btnRefreshKnowledge = document.getElementById("btnRefreshKnowledge");
  if (btnRefreshKnowledge) btnRefreshKnowledge.addEventListener("click", refreshKnowledge);

  const btnViewKnowledge = document.getElementById("btnViewKnowledge");
  if (btnViewKnowledge) btnViewKnowledge.addEventListener("click", viewKnowledge);

  // ✅ 全局岗位下拉
  const selJobGlobal = document.getElementById("selJobGlobal");
  if (selJobGlobal) {
    selJobGlobal.addEventListener("change", e => {
      const sel = e.target;
      state.jobId = parseInt(sel.value) || null;
      state.jobName = sel.options[sel.selectedIndex]?.text || "";
      state.pendingCandidate = null;
      const active = document.querySelector(".panel.active");
      if (active && active.id === "panel-resumes") loadResumes(state.jobId);
      if (active && active.id === "panel-pipeline") loadPipeline(state.jobId);
    });
  }

  // ✅ 候选人排序 / 筛选
  const selSort = document.getElementById("selSort");
  if (selSort) selSort.addEventListener("change", () => renderResumes());
  const selFilter = document.getElementById("selFilter");
  if (selFilter) selFilter.addEventListener("change", () => renderResumes());

  // ✅ 管道阶段筛选
  const pipelineBar = document.getElementById("pipelineBar");
  if (pipelineBar) {
    pipelineBar.addEventListener("click", e => {
      const chip = e.target.closest(".pipeline-chip");
      if (!chip) return;
      document.querySelectorAll(".pipeline-chip").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      state.pipelineFilter = chip.dataset.stage;
      renderPipeline();
    });
  }

  // ══════════════════════════════════════════
  // 第二步：异步加载数据（失败不影响按钮）
  // ══════════════════════════════════════════
  initAsync();
});

// 独立的数据初始化函数
async function initAsync() {
  try {
    // 加载存储数据
    const stored = await chrome.storage.local.get(["apiKey", "serverUrl", "collectedResumes", "collectedJobId"]);
    if (stored.apiToken) {
      API_TOKEN = stored.apiToken;
      const inputToken = document.getElementById("inputApiToken");
      if (inputToken) inputToken.value = stored.apiToken;
    }
    if (stored.apiKey) {
      API_KEY = stored.apiKey;
      const inputApiKey = document.getElementById("inputApiKey");
      if (inputApiKey) inputApiKey.value = stored.apiKey;
    }
    if (stored.serverUrl) API_BASE = stored.serverUrl;

    // DeepSeek 直连设置
    if (stored.dsApiKey) {
      const inputDsKey = document.getElementById("inputDsKey");
      if (inputDsKey) inputDsKey.value = stored.dsApiKey;
    }
    if (stored.dsModel) {
      const inputDsModel = document.getElementById("inputDsModel");
      if (inputDsModel) inputDsModel.value = stored.dsModel;
    }

    // 检查服务器
    const serverOk = await checkServer();
    if (serverOk) {
      await loadJobs();
    } else {
      log("无法连接服务器，请检查 API Key 和后端服务", "err");
    }

    // 恢复已收集的简历
    if (stored.collectedResumes && stored.collectedResumes.length > 0) {
      collectedResumes = stored.collectedResumes;
      state.jobId = stored.collectedJobId;
      const countEl = document.getElementById("collectedCount");
      if (countEl) countEl.textContent = collectedResumes.length;
      updateCollectUI();
      log(`从缓存恢复 ${collectedResumes.length} 份简历`, "dim");
    }

    // 监听 storage 变化
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.apiToken) {
        const newToken = changes.apiToken.newValue || "";
        if (newToken && newToken !== API_TOKEN) {
          API_TOKEN = newToken;
          const inputToken = document.getElementById("inputApiToken");
          if (inputToken) inputToken.value = newToken;
          checkServer().then(ok => { if (ok) { loadJobs(); } });
        }
      }
      if (area === "local" && changes.apiKey) {
        const newKey = changes.apiKey.newValue || "";
        if (newKey && newKey !== API_KEY) {
          API_KEY = newKey;
          const inputApiKey = document.getElementById("inputApiKey");
          if (inputApiKey) inputApiKey.value = newKey;
          checkServer().then(ok => { if (ok) { loadJobs(); } });
        }
      }
    });
  } catch (e) {
    console.error("[明猎] 初始化失败:", e);
    log("初始化部分功能失败，但核心按钮仍可使用", "dim");
  }
}

// ══════════════════════════════════════════
// 面板切换
// ══════════════════════════════════════════

function switchToPanel(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  const tab = document.querySelector(`.tab[data-panel="${name}"]`);
  if (tab) tab.classList.add("active");
  const panel = document.getElementById("panel-" + name);
  if (panel) panel.classList.add("active");

  // 进入页面时按需加载
  if (name === "resumes") loadResumes(state.jobId);
  if (name === "pipeline") loadPipeline(state.jobId);
}

// ══════════════════════════════════════════
// API
// ══════════════════════════════════════════

function apiFetch(url, options = {}) {
  const headers = { ...options.headers };
  // 优先用访问令牌；没有令牌时才回落到旧的 LLM Key 凭据
  if (API_TOKEN) headers["X-API-Token"] = API_TOKEN;
  else if (API_KEY) headers["X-API-Key"] = API_KEY;
  return fetch(url, { ...options, headers }).catch(e => {
    throw new Error(`网络请求失败: ${e.message}`);
  });
}

function ensureApiKey() {
  if (API_TOKEN || API_KEY) return true;
  const inputEl = document.getElementById("inputApiToken");
  if (inputEl && inputEl.value.trim()) {
    API_TOKEN = inputEl.value.trim();
    return true;
  }
  const inputEl2 = document.getElementById("inputApiKey");
  if (inputEl2 && inputEl2.value.trim()) {
    API_KEY = inputEl2.value.trim();
    return true;
  }
  return false;
}

async function checkServer() {
  const dot = document.getElementById("dotServer");
  const txt = document.getElementById("txtServer");
  const headerStatus = document.getElementById("headerStatus");
  const settingsStatus = document.getElementById("settingsStatus");

  // 优先从输入框读取（用户可能还没点保存）
  const inputEl = document.getElementById("inputApiKey");
  if (inputEl) {
    const inputKey = inputEl.value.trim();
    if (inputKey && inputKey !== API_KEY) {
      API_KEY = inputKey;
    }
  }

  function setStatus(dotClass, msg, statusType) {
    if (dot) { dot.className = "dot " + dotClass; }
    if (txt) { txt.textContent = msg; }
    if (headerStatus) {
      headerStatus.textContent = msg;
      headerStatus.style.color = statusType === "ok" ? "#52c41a" : statusType === "warn" ? "#faad14" : "#ff4d4f";
    }
    if (settingsStatus) {
      settingsStatus.style.display = "block";
      settingsStatus.style.background = statusType === "ok" ? "#e6f9ee" : statusType === "warn" ? "#fff8e6" : "#fff0f0";
      settingsStatus.style.color = statusType === "ok" ? "#1a8a4a" : statusType === "warn" ? "#b26a00" : "#c0392b";
      settingsStatus.textContent = msg;
    }
  }

  try {
    if (!API_KEY) {
      setStatus("dot-orange", "请先填写 API Key", "warn");
      return false;
    }
    setStatus("dot-gray", "连接中...", "warn");
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success) {
      setStatus("dot-green", "已连接 (" + (data.data?.length || 0) + " 个岗位)", "ok");
      return true;
    } else {
      setStatus("dot-orange", data.error || "API Key 无效", "err");
    }
  } catch (e) {
    setStatus("dot-gray", e.message || "未连接", "err");
  }
  return false;
}

async function loadJobs() {
  const sel = document.getElementById("selJobGlobal");
  if (!ensureApiKey()) {
    sel.innerHTML = '<option value="">请先填写 API Key</option>';
    return;
  }
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success && data.data.length > 0) {
      sel.innerHTML = data.data.map(j => `<option value="${j.id}">${j.name}</option>`).join("");
      state.jobId = parseInt(sel.value) || data.data[0].id;
      state.jobName = data.data.find(j => j.id === state.jobId)?.name || "";
    } else {
      sel.innerHTML = '<option value="">请先在网页端创建岗位</option>';
      state.jobId = null;
    }
  } catch (e) {
    sel.innerHTML = '<option value="">加载失败 - 请检查连接</option>';
  }
}

async function checkPage() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && tab.url.includes("liepin.com")) {
      console.log("[明猎] 猎聘页面就绪:", tab.url);
    }
  } catch (e) {}
}

// ══════════════════════════════════════════
// 统计卡片
// ══════════════════════════════════════════



// ══════════════════════════════════════════
// AI 对话
// ══════════════════════════════════════════

let chatHistory = [];
let isChatting = false;

async function sendChat() {
  const input = document.getElementById("chatInput");
  const msg = input.value.trim();
  if (!msg || isChatting) return;

  input.value = "";
  input.style.height = "auto";
  isChatting = true;
  document.getElementById("btnSend").disabled = true;

  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.style.display = "none";
  const help = document.querySelector(".help");
  if (help) help.style.display = "none";

  appendMessage("user", msg);
  const aiMsgEl = appendMessage("ai", "");
  const bubble = aiMsgEl.querySelector(".msg-bubble");
  bubble.innerHTML = '<div class="loading"></div>';

  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    const data = await resp.json();
    if (data.success) {
      bubble.innerHTML = formatReply(data.reply);
      chatHistory.push({ role: "user", content: msg });
      chatHistory.push({ role: "assistant", content: data.reply });
    } else {
      bubble.innerHTML = `<span style="color:#ff4d4f">请求失败: ${data.error}</span>`;
    }
  } catch (e) {
    bubble.innerHTML = `<span style="color:#ff4d4f">请求失败: ${e.message}</span>`;
  }

  isChatting = false;
  document.getElementById("btnSend").disabled = false;
  scrollChat();
}

function appendMessage(role, content) {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = `msg msg-${role}`;
  const avatar = role === "user" ? "你" : "猎";
  const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  div.innerHTML = `
    <div class="msg-avatar">${avatar}</div>
    <div>
      <div class="msg-bubble">${content || ""}</div>
      <div class="msg-time">${time}</div>
    </div>
  `;
  container.appendChild(div);
  scrollChat();
  return div;
}

function formatReply(text) {
  if (!text) return "";
  return text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.*?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

function scrollChat() {
  const container = document.getElementById("chatMessages");
  container.scrollTop = container.scrollHeight;
}

async function clearChat() {
  chatHistory = [];
  const container = document.getElementById("chatMessages");
  container.querySelectorAll(".msg").forEach(m => m.remove());
  const welcome = container.querySelector(".welcome");
  if (welcome) welcome.style.display = "block";
  const help = container.querySelector(".help");
  if (help) help.style.display = "block";
  try { await apiFetch(`${API_BASE}/api/extension/chat/clear`, { method: "POST" }); } catch (e) {}
}

function autoResize() {
  const el = document.getElementById("chatInput");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 100) + "px";
}

// ══════════════════════════════════════════
// 一键抓取
// ══════════════════════════════════════════

function onQuickScrape() {
  if (!state.jobId) { alert("请先在顶部选择关联职位"); return; }
  toggleScrape();
}

// ══════════════════════════════════════════
// 抓取（单页精准提取）
// ══════════════════════════════════════════

let collectedResumes = [];

// 从 storage 恢复已收集的简历
async function loadCollected() {
  try {
    const data = await chrome.storage.local.get("collectedResumes");
    if (data.collectedResumes && data.collectedResumes.length > 0) {
      collectedResumes = data.collectedResumes;
      updateCollectUI();
      log(`从缓存恢复 ${collectedResumes.length} 份简历`, "dim");
    }
  } catch (e) {}
}

function saveCollected() {
  chrome.storage.local.set({ collectedResumes });
}

function updateCollectUI() {
  const count = collectedResumes.length;
  const bar = document.getElementById("collectBar");
  const countEl = document.getElementById("collectedCount");
  if (count > 0) {
    bar.style.display = "flex";
    countEl.textContent = count;
  } else {
    bar.style.display = "none";
  }
}

async function toggleScrape() {
  if (!state.jobId) { alert("请先在顶部选择关联职位"); return; }
  if (!ensureApiKey()) { alert("请先在设置面板填写 API Key"); return; }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) { alert("无法获取当前标签页"); return; }

  // 直接检查 URL，不依赖 content script
  if (!tab.url || !tab.url.includes("liepin.com")) {
    alert(`当前页面不是猎聘 (${tab.url})\n请打开猎聘简历详情页`);
    return;
  }

  log("正在抓取当前页面...");

  // 用 scripting API 直接注入提取代码，不依赖 content script
  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const url = window.location.href;
        // 尝试 CSS 选择器精准提取
        const selectors = [
          ".resume-content", ".resume-main", ".detail-content",
          ".resume-detail", "[class*='resume-box']", "[class*='resume-body']",
          "[class*='resume-info']", ".content-left", ".main-content",
        ];
        let resumeEl = null;
        for (const sel of selectors) {
          resumeEl = document.querySelector(sel);
          if (resumeEl && resumeEl.innerText.length > 200) break;
          resumeEl = null;
        }
        let fullText = "";
        let method = "";
        if (resumeEl) {
          fullText = resumeEl.innerText.trim();
          method = "selector";
        } else {
          const clone = document.body.cloneNode(true);
          clone.querySelectorAll(
            "script, style, noscript, iframe, nav, header, footer, " +
            "[class*='nav'], [class*='header'], [class*='footer'], " +
            "[class*='sidebar'], [class*='recommend'], [class*='ad-'], " +
            "[class*='banner'], [class*='modal'], [class*='popup']"
          ).forEach(el => el.remove());
          fullText = (clone.innerText || "").trim();
          method = "fallback";
        }
        if (fullText.length > 12000) fullText = fullText.substring(0, 12000);
        if (fullText.length < 50) return { success: false, error: "页面内容过短" };
        // 提取姓名
        let name = "";
        const nameEl = document.querySelector("[class*='name'], [class*='title'], h1, h2, .resume-name, .user-name");
        if (nameEl) { const t = nameEl.innerText.trim(); if (t.length > 1 && t.length < 20) name = t; }
        return { success: true, full_text: fullText, detail_url: url, name: name, method: method, text_length: fullText.length };
      },
    });
  } catch (e) {
    alert(`注入失败: ${e.message}`);
    return;
  }

  const result = results && results[0] && results[0].result;
  if (!result || !result.success) {
    alert(result?.error || "抓取失败，请确认当前页面是猎聘简历详情页");
    return;
  }

  // 去重
  const exists = collectedResumes.some(r => r.detail_url === result.detail_url);
  if (exists) {
    log("该简历已收集过，跳过", "dim");
    return;
  }

  const resume = {
    full_text: result.full_text,
    detail_url: result.detail_url,
    name: result.name || "未知",
    collected_at: new Date().toISOString(),
  };
  collectedResumes.push(resume);
  saveCollected();
  updateCollectUI();
  log(`✅ 已收集: ${resume.name} (${result.text_length}字, ${result.method})`, "ok");
}

async function submitResumes() {
  if (collectedResumes.length === 0) {
    log("没有待提交的简历，请先抓取", "err");
    return;
  }
  if (!state.jobId) { alert("请先选择岗位"); return; }

  const stored = await chrome.storage.local.get(["dsApiKey", "dsModel"]);
  const dsKey = stored.dsApiKey;
  const dsModel = stored.dsModel || "deepseek-chat";

  // 直连 DeepSeek 流式评分
  if (dsKey) {
    log(`正在用 DeepSeek 直连评分 ${collectedResumes.length} 份简历...`, "info");
    let okCount = 0;
    for (const r of collectedResumes) {
      const entry = createEvalEntry(r.name || "未知");
      try {
        const userPrompt = buildScorePrompt(r, state.jobName);
        await window.MinglieDeepseek.streamChat({
          systemPrompt: SCORE_SYSTEM_PROMPT,
          userPrompt,
          apiKey: dsKey,
          model: dsModel,
          onToken: (token, full) => { entry.textContent = `✅ ${r.name}：${full}`; },
        });
        entry.className = "log-entry log-ok";
        okCount++;
      } catch (e) {
        entry.className = "log-entry log-err";
        entry.textContent = `❌ ${r.name}：${e.message}`;
      }
    }
    log(
      `直连评分完成：${okCount}/${collectedResumes.length} 份`,
      okCount === collectedResumes.length ? "ok" : "warn"
    );
    collectedResumes = [];
    saveCollected();
    updateCollectUI();
    return;
  }

  // 回退：后端评分
  if (!ensureApiKey()) { alert("请先配置 API Key（或填写 DeepSeek Key 走直连）"); return; }
  const count = collectedResumes.length;
  log(`正在提交 ${count} 份简历到后端评分...`);

  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/submit_batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        resumes: collectedResumes,
      }),
    });
    const data = await resp.json();
    if (data.success) {
      log(`✅ 提交成功！${data.message || ""}`, "ok");
      collectedResumes = [];
      saveCollected();
      updateCollectUI();
    } else {
      log(`提交失败: ${data.error}`, "err");
    }
  } catch (e) {
    log(`提交失败: ${e.message}`, "err");
  }
}

function clearCollected() {
  alert(`clearCollected called: resumes=${collectedResumes.length}`);
  if (collectedResumes.length === 0) { alert("没有简历可清空"); return; }
  collectedResumes = [];
  saveCollected();
  updateCollectUI();
  alert("已清空");
}

// ══════════════════════════════════════════
// 候选人
// ══════════════════════════════════════════

async function loadResumes(jobId) {
  if (!jobId) { document.getElementById("resumeList").innerHTML = '<div class="empty">请先选择关联职位</div>'; return; }
  const el = document.getElementById("resumeList");
  el.innerHTML = '<div style="text-align:center;padding:20px"><div class="loading"></div></div>';
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/resumes/${jobId}`);
    const data = await resp.json();
    if (!data.success || !data.data || data.data.length === 0) { el.innerHTML = '<div class="empty">暂无候选人</div>'; return; }
    state.allResumes = data.data;
    renderResumes();
  } catch (e) { el.innerHTML = '<div class="empty">加载失败</div>'; }
}

function renderResumes() {
  const el = document.getElementById("resumeList");
  if (!state.allResumes || !state.allResumes.length) { el.innerHTML = '<div class="empty">暂无候选人</div>'; return; }
  const sort = document.getElementById("selSort").value;
  const filter = document.getElementById("selFilter").value;
  let list = state.allResumes.slice();
  const scoreOf = r => ((r.score100 || {}).total_score || 0);
  if (sort === "score") list.sort((a, b) => scoreOf(b) - scoreOf(a));
  if (filter === "high") list = list.filter(r => scoreOf(r) >= 80);
  else if (filter === "mid") list = list.filter(r => scoreOf(r) >= 60 && scoreOf(r) < 80);
  else if (filter === "low") list = list.filter(r => scoreOf(r) < 60);

  if (!list.length) { el.innerHTML = '<div class="empty">无匹配候选人</div>'; return; }

  el.innerHTML = list.map((r, i) => {
    const name = r.name || r.title || "未知";
    const score = scoreOf(r);
    const cls = score >= 80 ? "score-high" : score >= 60 ? "score-mid" : "score-low";
    const skills = (r.skill_tags || []).slice(0, 3);
    const detail = buildResumeDetail(r);
    return `<div class="resume-card" data-idx="${i}">
      <span class="score ${cls}">${score || "-"}分</span>
      <div class="name">${name}</div>
      <div class="meta">${r.education || ""} · ${r.experience || ""} · ${r.location || ""}</div>
      <div class="tags">${skills.map(s => `<span class="tag">${s}</span>`).join("")}</div>
      <div class="resume-detail" id="rd-${i}">${detail}</div>
      <div class="actions">
        <div class="mini" data-act="pipe" data-i="${i}">加入管道</div>
        <div class="mini primary" data-act="ai" data-i="${i}">AI 分析</div>
      </div>
      ${r.detail_url ? `<a href="${r.detail_url}" target="_blank" style="font-size:11px;color:#534ab7;text-decoration:none;margin-top:6px;display:inline-block">查看猎聘简历</a>` : ""}
    </div>`;
  }).join("");

  el.querySelectorAll(".resume-card").forEach(card => {
    card.addEventListener("click", e => {
      if (e.target.closest(".mini") || e.target.closest("a")) return;
      const i = card.dataset.idx;
      const d = document.getElementById("rd-" + i);
      d.classList.toggle("show");
    });
  });
  el.querySelectorAll(".mini[data-act='ai']").forEach(b => b.addEventListener("click", e => {
    e.stopPropagation();
    const r = list[parseInt(b.dataset.i)];
    onAnalyzeCandidate(r);
  }));
  el.querySelectorAll(".mini[data-act='pipe']").forEach(b => b.addEventListener("click", e => {
    e.stopPropagation();
    const r = list[parseInt(b.dataset.i)];
    onAddToPipeline(r);
  }));
}

function buildResumeDetail(r) {
  const s = r.score100 || {};
  let html = "";
  if (s.recommend_reason) html += `<div class="dd-h">推荐理由</div>${s.recommend_reason}<br>`;
  if (s.risk) html += `<div class="dd-h">风险点</div>${s.risk}`;
  if (!html) {
    const parts = [];
    if (r.summary) parts.push(r.summary);
    if (r.highlights) parts.push(r.highlights);
    html = parts.join("<br>") || "暂无 AI 分析详情";
  }
  return html;
}

// 候选人 → AI 助手（带上下文，自动发问）
function onAnalyzeCandidate(r) {
  const name = r.name || r.title || "未知";
  const score = ((r.score100 || {}).total_score || 0);
  switchToPanel("chat");
  document.getElementById("chatInput").value = `分析候选人：${name}，${score}分。给出推荐理由和人才风险点。`;
  sendChat();
}

// 候选人 → 管道（跳转 + 待后端接口落地真正录入）
function onAddToPipeline(r) {
  const name = r.name || r.title || "未知";
  const score = ((r.score100 || {}).total_score || 0);
  const detailUrl = r.detail_url || "";

  if (!state.jobId) { alert("请先选择岗位"); return; }
  if (!ensureApiKey()) { alert("请先配置 API Key"); return; }

  apiFetch(`${API_BASE}/api/extension/pipeline/${state.jobId}/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, detail_url: detailUrl, score }),
  }).then(resp => resp.json()).then(data => {
    if (data.success) {
      alert(data.message || `已将 ${name} 加入管道`);
    } else {
      alert(data.error || "加入管道失败");
    }
  }).catch(e => {
    alert(`加入管道失败: ${e.message}`);
  });
}

// ══════════════════════════════════════════
// 管道
// ══════════════════════════════════════════

const STAGE_LABELS = { recommended:{label:"AI推荐",color:"#e6f9ee",text:"#52c41a"}, contacted:{label:"已联系",color:"#e8f4fd",text:"#1890ff"}, submitted:{label:"已推荐",color:"#fff7e6",text:"#faad14"}, interview:{label:"面试中",color:"#f0e6ff",text:"#722ed1"}, offer:{label:"Offer",color:"#e6ffe6",text:"#52c41a"}, hired:{label:"已录用",color:"#d4edda",text:"#389e0d"}, rejected:{label:"已淘汰",color:"#fff0f0",text:"#ff4d4f"} };

async function loadPipeline(jobId) {
  if (!jobId) { document.getElementById("pipelineContent").innerHTML = '<div class="empty">请先选择关联职位</div>'; return; }
  const el = document.getElementById("pipelineContent");
  el.innerHTML = '<div style="text-align:center;padding:20px"><div class="loading"></div></div>';
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/pipeline/${jobId}`);
    const data = await resp.json();
    if (!data.success) { el.innerHTML = '<div class="empty">加载失败（需后端 /api/extension/pipeline 接口）</div>'; return; }
    state.pipelineData = { summary: data.summary || {}, stages: data.stages || {} };
    renderPipelineBar();
    renderPipeline();
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败（需后端 /api/extension/pipeline 接口）</div>';
  }
}

function renderPipelineBar() {
  const bar = document.getElementById("pipelineBar");
  const summary = (state.pipelineData || {}).summary || {};
  bar.innerHTML = Object.entries(STAGE_LABELS).map(([k, c]) =>
    `<div class="pipeline-chip" data-stage="${k}" style="background:${c.color};color:${c.text}">${c.label} ${summary[k] || 0}</div>`
  ).join("");
  if (!state.pipelineFilter) { const first = bar.querySelector(".pipeline-chip"); if (first) first.classList.add("active"); }
  else { const f = bar.querySelector(`.pipeline-chip[data-stage="${state.pipelineFilter}"]`); if (f) f.classList.add("active"); }
}

function renderPipeline() {
  const el = document.getElementById("pipelineContent");
  const data = state.pipelineData || {};
  const stages = data.stages || {};
  const filter = state.pipelineFilter;
  let html = "";
  const keys = filter ? [filter] : Object.keys(STAGE_LABELS);
  for (const k of keys) {
    const c = STAGE_LABELS[k];
    const list = stages[k] || [];
    if (!list.length) continue;
    html += `<div style="margin:8px 0 4px;font-size:11px;color:#8a90a0;font-weight:600">${c.label} (${list.length})</div>`;
    for (const p of list) {
      html += `<div class="resume-card" data-cand="${p.candidate_name || ""}">
        <span class="score" style="color:${c.text}">${p.ai_score || "-"}分</span>
        <div class="name">${p.candidate_name || "未知"}</div>
        <div class="meta">${p.reason || ""}</div>
        <div class="actions">
          <div class="mini primary" data-act="ai" data-name="${p.candidate_name || ""}">AI 分析</div>
          <div class="mini" data-act="delete" data-id="${p.id}" data-name="${p.candidate_name || ""}" style="color:#ff4d4f">删除</div>
        </div>
      </div>`;
    }
  }
  if (state.pendingCandidate) {
    const target = el.querySelector(`.resume-card[data-cand="${cssEsc(state.pendingCandidate)}"]`);
    if (target) target.style.borderColor = "#7f77dd";
  }
  el.innerHTML = html || '<div class="empty">暂无候选人</div>';

  el.querySelectorAll(".mini[data-act='ai']").forEach(b => b.addEventListener("click", () => {
    onAnalyzeFromPipeline(b.dataset.name);
  }));
  el.querySelectorAll(".mini[data-act='delete']").forEach(b => b.addEventListener("click", () => {
    onDeleteFromPipeline(b.dataset.id, b.dataset.name);
  }));
}

// 管道 → AI 助手（带上下文）
function onAnalyzeFromPipeline(name) {
  switchToPanel("chat");
  document.getElementById("chatInput").value = `分析候选人：${name}。结合当前管道阶段，给出下一步建议和人才风险点。`;
  sendChat();
}


function onDeleteFromPipeline(feedbackId, name) {
  if (!confirm(`确定删除候选人「${name}」？`)) return;
  if (!state.jobId || !ensureApiKey()) { alert("请先选择岗位和配置 API Key"); return; }

  apiFetch(`${API_BASE}/api/extension/pipeline/${state.jobId}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: parseInt(feedbackId) }),
  }).then(r => r.json()).then(data => {
    if (data.success) {
      loadPipeline(state.jobId);
    } else {
      alert(data.error || "删除失败");
    }
  }).catch(e => alert(`删除失败: ${e.message}`));
}
function cssEsc(s) { return (s || "").replace(/"/g, '\\"'); }

// ══════════════════════════════════════════
// 设置
// ══════════════════════════════════════════

function saveSettings() {
  const tokenEl = document.getElementById("inputApiToken");
  const token = tokenEl ? tokenEl.value.trim() : "";
  const key = document.getElementById("inputApiKey").value.trim();
  const server = document.getElementById("inputServer").value.trim();
  const dsKey = document.getElementById("inputDsKey").value.trim();
  const dsModel = document.getElementById("inputDsModel").value.trim() || "deepseek-chat";
  if (!token && !key && !dsKey) {
    document.getElementById("settingsMsg").innerHTML = '<span style="color:#ff4d4f">请至少填写一个 Key（后端或 DeepSeek）</span>';
    return;
  }
  API_TOKEN = token;
  API_KEY = key;
  if (server) API_BASE = server;
  chrome.storage.local.set(
    { apiToken: token, apiKey: key, serverUrl: server || API_BASE, dsApiKey: dsKey, dsModel },
    () => {
      console.log("[明猎] 已保存到 storage:", {
        apiToken: token ? token.substring(0, 6) + "***" : "(空)",
        apiKey: key ? key.substring(0, 6) + "***" : "(空)",
        serverUrl: server || API_BASE,
        dsApiKey: dsKey ? dsKey.substring(0, 6) + "***" : "(空)",
      });
      document.getElementById("settingsMsg").innerHTML = '<span style="color:#52c41a">已保存 ✓</span>';
    }
  );
  // 填了令牌或后端 Key 就去探测后端
  if (token || key) {
    checkServer().then(ok => {
      if (ok) loadJobs();
    });
  } else {
    loadJobs();
  }
}

async function loadKnowledgeStats() {
  if (!ensureApiKey()) {
    document.getElementById("knowledgeStats").innerHTML = '<span style="color:#faad14">请先配置 API Key</span>';
    return;
  }
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/knowledge/stats`);
    const data = await resp.json();
    if (data.success) {
      document.getElementById("knowledgeStats").innerHTML = `共 ${data.data.total || 0} 条知识`;
    } else {
      document.getElementById("knowledgeStats").innerHTML = `<span style="color:#ff4d4f">${data.error || "加载失败"}</span>`;
    }
  } catch (e) {
    document.getElementById("knowledgeStats").innerHTML = `<span style="color:#ff4d4f">${e.message}</span>`;
  }
}

async function refreshKnowledge() {
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/knowledge/refresh`, { method: "POST" });
    const data = await resp.json();
    if (data.success) loadKnowledgeStats();
  } catch (e) {}
}

async function viewKnowledge() {
  const el = document.getElementById("knowledgeList");
  el.innerHTML = '<div style="text-align:center;padding:8px">加载中...</div>';
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/knowledge/list?limit=50`);
    const data = await resp.json();
    if (!data.success || !data.data || data.data.length === 0) {
      el.innerHTML = '<div style="color:#999;padding:8px">暂无知识条目</div>';
      return;
    }
    el.innerHTML = data.data.map(k => {
      const type = k.knowledge_type || k.type || "";
      const title = k.title || "";
      const content = (k.content || "").substring(0, 200);
      const source = k.source || "";
      const conf = k.confidence ? Math.round(k.confidence * 100) + "%" : "";
      return `<div style="padding:8px 0;border-bottom:1px solid #f0f0f0" data-kid="${k.id}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div style="font-weight:500;color:#333;font-size:12px">${title || type}</div>
          <span style="font-size:10px;color:#999;background:#f5f5f5;padding:1px 6px;border-radius:3px">${type}</span>
        </div>
        <div style="color:#555;font-size:11px;margin-top:4px;line-height:1.6">${content}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px">
          <span style="color:#999;font-size:10px">${source ? "来源: " + source : ""} ${conf ? "可信度: " + conf : ""}</span>
          <span style="color:#ff4d4f;font-size:11px;cursor:pointer" onclick="deleteKnowledge(${k.id}, this)">删除</span>
        </div>
      </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<div style="color:#ff4d4f">加载失败: ${e.message}</div>`;
  }
}

function deleteKnowledge(id, el) {
  if (!confirm("确定删除这条知识？")) return;
  apiFetch(`${API_BASE}/api/extension/knowledge/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  }).then(r => r.json()).then(data => {
    if (data.success) {
      const item = el.closest("[data-kid]");
      if (item) item.remove();
      loadKnowledgeStats();
    } else {
      alert(data.error || "删除失败");
    }
  }).catch(e => alert(`删除失败: ${e.message}`));
}

// ══════════════════════════════════════════
// 直连 DeepSeek 评分（流式）
// ══════════════════════════════════════════

const SCORE_SYSTEM_PROMPT = `你是一名资深技术招聘顾问。请根据候选人的简历，结合目标岗位，给出简洁的评估。
要求：
1. 先给一个 0-100 的综合匹配评分（以“评分：”开头）。
2. 用 1-2 句话说明推荐理由。
3. 用 1-2 句话指出风险点或不足。
4. 列出 3-5 个关键技能标签（以“技能：”开头）。
全部用中文，总字数不超过 200 字。不要使用 Markdown 代码块。`;

function buildScorePrompt(r, jobName) {
  const jobCtx = jobName ? `目标岗位：${jobName}\n` : "";
  const text = (r.full_text || "").slice(0, 6000);
  return `${jobCtx}候选人姓名：${r.name || "未知"}\n\n简历内容：\n${text}`;
}

function createEvalEntry(name) {
  const area = document.getElementById("scrapeLog");
  if (!area) return { textContent: "" };
  const entry = document.createElement("div");
  entry.className = "log-entry log-info";
  entry.textContent = `⏳ ${name}：连接中...`;
  area.appendChild(entry);
  area.scrollTop = area.scrollHeight;
  return entry;
}

// ══════════════════════════════════════════
// 工具
// ══════════════════════════════════════════

function log(msg, type = "info") {
  const area = document.getElementById("scrapeLog");
  if (!area) { console.log("[明猎]", msg); return; }
  const entry = document.createElement("div");
  entry.className = `log-entry log-${type}`;
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  entry.textContent = `[${time}] ${msg}`;
  area.appendChild(entry);
  area.scrollTop = area.scrollHeight;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
