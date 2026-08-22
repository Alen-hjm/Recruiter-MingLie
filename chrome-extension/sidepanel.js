/**
 * 明猎 - Side Panel 逻辑
 * 主页：AI 对话（调用知识库 + 岗位 + 候选人）
 */

let API_BASE = "http://127.0.0.1:5000";
let API_KEY = "";
let state = { running: false, jobId: null, allResumes: [], stats: {}, currentPage: 0 };

// ══════════════════════════════════════════
// 初始化
// ══════════════════════════════════════════

document.addEventListener("DOMContentLoaded", async () => {
  const stored = await chrome.storage.local.get(["apiKey", "serverUrl"]);
  if (stored.apiKey) { API_KEY = stored.apiKey; document.getElementById("inputApiKey").value = stored.apiKey; }
  if (stored.serverUrl) { API_BASE = stored.serverUrl; document.getElementById("inputServer").value = stored.serverUrl; }

  await checkServer();
  await loadJobs();
  checkPage();

  // 标签切换
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById("panel-" + tab.dataset.panel).classList.add("active");
    });
  });

  // AI 对话
  document.getElementById("btnSend").addEventListener("click", sendChat);
  document.getElementById("chatInput").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  document.getElementById("chatInput").addEventListener("input", autoResize);

  // 快捷按钮
  document.querySelectorAll(".quick-btn, .welcome-card").forEach(el => {
    el.addEventListener("click", () => {
      const msg = el.dataset.msg;
      if (msg) { document.getElementById("chatInput").value = msg; sendChat(); }
    });
  });

  // 清空对话
  document.getElementById("btnClearChat").addEventListener("click", clearChat);
  document.getElementById("btnRefresh").addEventListener("click", () => { checkServer().then(() => loadJobs()); checkPage(); });

  // 抓取
  document.getElementById("btnScrape").addEventListener("click", toggleScrape);

  // 设置
  document.getElementById("btnSaveSettings").addEventListener("click", saveSettings);
  document.getElementById("btnRefreshKnowledge").addEventListener("click", refreshKnowledge);

  // 岗位选择
  document.getElementById("selJob").addEventListener("change", e => { state.jobId = parseInt(e.target.value) || null; });
  document.getElementById("selJobResume").addEventListener("change", e => loadResumes(e.target.value));
  document.getElementById("selJobPipe").addEventListener("change", e => loadPipeline(e.target.value));

  loadKnowledgeStats();
});

// ══════════════════════════════════════════
// API
// ══════════════════════════════════════════

function apiFetch(url, options = {}) {
  const headers = { ...options.headers };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return fetch(url, { ...options, headers });
}

async function checkServer() {
  const dot = document.getElementById("dotServer");
  const txt = document.getElementById("txtServer");
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success) { dot.className = "dot dot-green"; txt.textContent = "已连接"; return true; }
    else { dot.className = "dot dot-orange"; txt.textContent = data.error || "Key 无效"; }
  } catch (e) { dot.className = "dot dot-gray"; txt.textContent = "未连接"; }
  return false;
}

async function loadJobs() {
  const selects = ["selJob", "selJobResume", "selJobPipe"].map(id => document.getElementById(id));
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success && data.data.length > 0) {
      selects.forEach(sel => { sel.innerHTML = data.data.map(j => `<option value="${j.id}">${j.name}</option>`).join(""); });
      state.jobId = data.data[0].id;
    } else {
      selects.forEach(sel => { sel.innerHTML = '<option value="">请先创建岗位</option>'; });
    }
  } catch (e) {
    selects.forEach(sel => { sel.innerHTML = '<option value="">加载失败</option>'; });
  }
}

async function checkPage() {
  const dot = document.getElementById("dotPage");
  const txt = document.getElementById("txtPage");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return;
    const resp = await chrome.tabs.sendMessage(tab.id, { action: "get_page_info" });
    if (resp && resp.isLiepin) {
      dot.className = "dot dot-green";
      txt.textContent = `${resp.cardCount} 个卡片`;
      document.getElementById("statCards").textContent = resp.cardCount;
    } else {
      dot.className = "dot dot-orange";
      txt.textContent = "请打开猎聘";
    }
  } catch (e) { dot.className = "dot dot-gray"; txt.textContent = "请打开猎聘"; }
}

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

  // 隐藏欢迎界面
  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.style.display = "none";

  // 显示用户消息
  appendMessage("user", msg);

  // 显示 AI 加载
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
      bubble.innerHTML = `<span style="color:#ff4d4f">❌ ${data.error}</span>`;
    }
  } catch (e) {
    bubble.innerHTML = `<span style="color:#ff4d4f">❌ 请求失败: ${e.message}</span>`;
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
  // 简单 Markdown 格式化
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
  container.innerHTML = "";
  const welcome = document.querySelector(".welcome");
  if (welcome) { welcome.style.display = "block"; container.appendChild(welcome); }
  try { await apiFetch(`${API_BASE}/api/extension/chat/clear`, { method: "POST" }); } catch (e) {}
}

function autoResize() {
  const el = document.getElementById("chatInput");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 100) + "px";
}

// ══════════════════════════════════════════
// 抓取
// ══════════════════════════════════════════

async function toggleScrape() {
  if (state.running) { stopScrape(); return; }
  if (!state.jobId) { log("请先选择岗位", "err"); return; }
  if (!API_KEY) { log("请先配置 API Key", "err"); return; }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const resp = await chrome.tabs.sendMessage(tab.id, { action: "get_page_info" });
    if (!resp || !resp.isLiepin) { log("请打开猎聘搜索页", "err"); return; }
    if (resp.cardCount === 0) { log("当前页面没有简历卡片", "err"); return; }
  } catch (e) { log("无法连接页面，请刷新猎聘", "err"); return; }

  state.running = true;
  state.currentPage = 0;
  state.allResumes = [];
  state.stats = { cards: 0, scraped: 0, filtered: 0, sent: 0 };
  const maxPages = parseInt(document.getElementById("inputPages").value) || 3;

  document.getElementById("btnScrape").textContent = "⏹ 停止";
  document.getElementById("scrapeStats").style.display = "block";

  let filterRules = null;
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/filter_rules/${state.jobId}`);
    const data = await resp.json();
    if (data.success && data.rules) filterRules = data.rules;
  } catch (e) {}

  log(`开始抓取，最多 ${maxPages} 页`);

  while (state.running && state.currentPage < maxPages) {
    state.currentPage++;
    let result;
    try { result = await chrome.tabs.sendMessage(tab.id, { action: "scrape_page", filterRules }); }
    catch (e) { log("页面通信失败", "err"); break; }
    if (!result) { log("抓取失败", "err"); break; }

    state.stats.cards += result.total;
    state.stats.filtered += result.filtered;
    state.stats.scraped += result.resumes.length;
    updateStats();
    log(`第${state.currentPage}页: ${result.resumes.length}/${result.total} 份`, result.resumes.length > 0 ? "ok" : "dim");

    // 详情页抓取
    const detailMode = document.getElementById("selDetailMode").value;
    if (detailMode === "detail" && result.resumes.length > 0) {
      log(`开始抓取详情页...`, "info");
      let linksResp;
      try { linksResp = await chrome.tabs.sendMessage(tab.id, { action: "get_resume_links" }); } catch (e) {}
      if (linksResp && linksResp.links) {
        let detailCount = 0;
        for (let i = 0; i < Math.min(linksResp.links.length, result.resumes.length); i++) {
          if (!state.running) break;
          try {
            const detailTab = await chrome.tabs.create({ url: linksResp.links[i].url, active: false });
            await new Promise(r => {
              const listener = (tid, info) => { if (tid === detailTab.id && info.status === 'complete') { chrome.tabs.onUpdated.removeListener(listener); r(); } };
              chrome.tabs.onUpdated.addListener(listener);
              setTimeout(() => { chrome.tabs.onUpdated.removeListener(listener); r(); }, 10000);
            });
            await sleep(2000 + Math.random() * 1000);
            try {
              const d = await chrome.tabs.sendMessage(detailTab.id, { action: "scrape_detail" });
              if (d && d.full_text && d.full_text.length > 200) { result.resumes[i].full_text = d.full_text; result.resumes[i].detail_url = d.detail_url; detailCount++; }
            } catch (e) {}
            try { await chrome.tabs.remove(detailTab.id); } catch (e) {}
            await sleep(800 + Math.random() * 1200);
          } catch (e) {}
        }
        log(`详情页: ${detailCount}/${result.resumes.length} 份`, "ok");
      }
    }

    // 发送到后端
    if (result.resumes.length > 0) {
      try {
        const resp = await apiFetch(`${API_BASE}/api/extension/submit_batch`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_id: state.jobId, resumes: result.resumes }),
        });
        const data = await resp.json();
        if (data.success) { state.stats.sent += result.resumes.length; log(`✓ 已同步 ${result.resumes.length} 份`, "ok"); }
        else { log(`同步失败: ${data.error}`, "err"); }
      } catch (e) { log(`同步失败: ${e.message}`, "err"); }
    }

    // 翻页
    if (state.currentPage < maxPages) {
      let nextResp;
      try { nextResp = await chrome.tabs.sendMessage(tab.id, { action: "click_next_page" }); } catch (e) {}
      if (!nextResp || !nextResp.success) { log("已到最后一页", "dim"); break; }
      await sleep(2000 + Math.random() * 1500);
    }
  }

  state.running = false;
  document.getElementById("btnScrape").textContent = "▶ 开始抓取";
  log(`完成！抓取 ${state.stats.scraped} 份，同步 ${state.stats.sent} 份`, "ok");
}

function stopScrape() { state.running = false; document.getElementById("btnScrape").textContent = "▶ 开始抓取"; log("已停止", "info"); }
function updateStats() { document.getElementById("statCards").textContent = state.stats.cards; document.getElementById("statScraped").textContent = state.stats.scraped; document.getElementById("statFiltered").textContent = state.stats.filtered; document.getElementById("statPage").textContent = state.currentPage; }

// ══════════════════════════════════════════
// 候选人
// ══════════════════════════════════════════

async function loadResumes(jobId) {
  if (!jobId) return;
  const el = document.getElementById("resumeList");
  el.innerHTML = '<div style="text-align:center;padding:20px"><div class="loading"></div></div>';
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/resumes/${jobId}`);
    const data = await resp.json();
    if (!data.success || !data.data || data.data.length === 0) { el.innerHTML = '<div class="empty">暂无候选人</div>'; return; }
    const resumes = data.data.sort((a, b) => ((b.score100 || {}).total_score || 0) - ((a.score100 || {}).total_score || 0));
    el.innerHTML = resumes.map(r => {
      const name = r.name || r.title || "未知";
      const score = (r.score100 || {}).total_score || 0;
      const cls = score >= 80 ? "score-high" : score >= 60 ? "score-mid" : "score-low";
      const skills = (r.skill_tags || []).slice(0, 3);
      return `<div class="resume-card">
        <span class="score ${cls}">${score || "-"}分</span>
        <div class="name">${name}</div>
        <div class="meta">${r.education || ""} · ${r.experience || ""} · ${r.location || ""}</div>
        <div class="tags">${skills.map(s => `<span class="tag">${s}</span>`).join("")}</div>
        ${r.detail_url ? `<a href="${r.detail_url}" target="_blank" style="font-size:11px;color:#4a6cf7;text-decoration:none;margin-top:4px;display:inline-block">🔗 查看猎聘简历</a>` : ""}
      </div>`;
    }).join("");
  } catch (e) { el.innerHTML = '<div class="empty">加载失败</div>'; }
}

// ══════════════════════════════════════════
// 管道
// ══════════════════════════════════════════

const STAGE_LABELS = { recommended:{label:"AI推荐",color:"#e6f9ee",text:"#52c41a"}, contacted:{label:"已联系",color:"#e8f4fd",text:"#1890ff"}, submitted:{label:"已推荐",color:"#fff7e6",text:"#faad14"}, interview:{label:"面试中",color:"#f0e6ff",text:"#722ed1"}, offer:{label:"Offer",color:"#e6ffe6",text:"#52c41a"}, hired:{label:"已录用",color:"#d4edda",text:"#389e0d"}, rejected:{label:"已淘汰",color:"#fff0f0",text:"#ff4d4f"} };

async function loadPipeline(jobId) {
  if (!jobId) return;
  const el = document.getElementById("pipelineContent");
  el.innerHTML = '<div style="text-align:center;padding:20px"><div class="loading"></div></div>';
  try {
    const resp = await apiFetch(`${API_BASE}/api/pipeline/${jobId}`);
    const data = await resp.json();
    if (!data.success) { el.innerHTML = '<div class="empty">加载失败</div>'; return; }
    const summary = data.summary || {}; const stages = data.stages || {};
    let html = '<div class="pipeline-bar">';
    for (const [k, c] of Object.entries(STAGE_LABELS)) { html += `<div class="pipeline-chip" style="background:${c.color};color:${c.text}">${c.label} ${summary[k]||0}</div>`; }
    html += '</div>';
    for (const [k, c] of Object.entries(STAGE_LABELS)) { const list = stages[k]||[]; if (!list.length) continue; html += `<div style="margin:8px 0 4px;font-size:11px;color:#888;font-weight:600">${c.label} (${list.length})</div>`; for (const p of list) { html += `<div class="resume-card"><span class="score" style="color:${c.text}">${p.ai_score||"-"}分</span><div class="name">${p.candidate_name||"未知"}</div><div class="meta">${p.reason||""}</div></div>`; } }
    el.innerHTML = html || '<div class="empty">暂无候选人</div>';
  } catch (e) { el.innerHTML = '<div class="empty">加载失败</div>'; }
}

// ══════════════════════════════════════════
// 设置
// ══════════════════════════════════════════

function saveSettings() {
  const key = document.getElementById("inputApiKey").value.trim();
  const server = document.getElementById("inputServer").value.trim();
  if (key) API_KEY = key;
  chrome.storage.local.set({ apiKey: key, serverUrl: server });
  const msg = document.getElementById("settingsMsg");
  msg.innerHTML = '<span style="color:#52c41a">✓ 已保存</span>';
  setTimeout(() => { msg.innerHTML = ""; }, 2000);
  checkServer().then(() => loadJobs());
}

async function loadKnowledgeStats() {
  try {
    const resp = await apiFetch(`${API_BASE}/api/knowledge/stats`);
    const data = await resp.json();
    if (data.success) {
      const el = document.getElementById("knowledgeStats");
      const d = data.data;
      el.innerHTML = `共 <strong>${d.total || 0}</strong> 条知识`;
    }
  } catch (e) {}
}

async function refreshKnowledge() {
  try {
    const resp = await apiFetch(`${API_BASE}/api/knowledge/refresh`, { method: "POST" });
    const data = await resp.json();
    if (data.success) { loadKnowledgeStats(); }
  } catch (e) {}
}

// ══════════════════════════════════════════
// 工具
// ══════════════════════════════════════════

function log(msg, type = "info") {
  const area = document.getElementById("scrapeLog");
  const entry = document.createElement("div");
  entry.className = `log-entry log-${type}`;
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  entry.textContent = `[${time}] ${msg}`;
  area.appendChild(entry);
  area.scrollTop = area.scrollHeight;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
