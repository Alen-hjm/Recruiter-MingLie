/**
 * 明猎 - Popup 逻辑
 * 控制抓取流程，与 content script 和后端通信
 */

let API_BASE = "http://127.0.0.1:5000";
let API_KEY = "";

// 从 storage 加载 API Key 和 Server URL
chrome.storage.local.get(["apiKey", "serverUrl"], (data) => {
  if (data.apiKey) API_KEY = data.apiKey;
  if (data.serverUrl) API_BASE = data.serverUrl;
});

function apiFetch(url, options = {}) {
  const headers = { ...options.headers };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return fetch(url, { ...options, headers }).catch(e => {
    throw new Error(`网络请求失败 (${e.message})：请确认后端服务已启动在 ${API_BASE}`);
  });
}

// ══════════════════════════════════════════
// 状态
// ══════════════════════════════════════════

let state = {
  running: false,
  jobId: null,
  maxPages: 3,
  currentPage: 0,
  allResumes: [],
  stats: { cards: 0, scraped: 0, filtered: 0 },
};

// ══════════════════════════════════════════
// 日志
// ══════════════════════════════════════════

function log(msg, type = "info") {
  const area = document.getElementById("logArea");
  const entry = document.createElement("div");
  entry.className = `log-entry log-${type}`;
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  entry.textContent = `[${time}] ${msg}`;
  area.appendChild(entry);
  area.scrollTop = area.scrollHeight;
}

// ══════════════════════════════════════════
// 服务器检测
// ══════════════════════════════════════════

async function checkServer() {
  const dot = document.getElementById("serverDot");
  const txt = document.getElementById("serverStatus");
  try {
    if (!API_KEY) {
      dot.className = "server-status server-offline";
      txt.textContent = "请先填写 API Key";
      return false;
    }
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success) {
      dot.className = "server-status server-online";
      txt.textContent = "已连接";
      return true;
    } else {
      dot.className = "server-status server-offline";
      txt.textContent = data.error || "API Key 无效";
    }
  } catch (e) {
    dot.className = "server-status server-offline";
    txt.textContent = e.message || "服务器未连接";
    log(`连接失败: ${e.message}`, "err");
  }
  return false;
}

// ══════════════════════════════════════════
// 加载岗位列表
// ══════════════════════════════════════════

async function loadJobs() {
  const select = document.getElementById("jobSelect");
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success && data.data.length > 0) {
      select.innerHTML = data.data
        .map((j) => `<option value="${j.id}">${j.name}</option>`)
        .join("");
      state.jobId = data.data[0].id;
    } else {
      select.innerHTML = '<option value="">请先在网页端创建岗位</option>';
    }
  } catch (e) {
    select.innerHTML = '<option value="">无法加载岗位</option>';
  }
}

// ══════════════════════════════════════════
// 与 Content Script 通信
// ══════════════════════════════════════════

async function sendToContent(action, data = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return null;
  try {
    return await chrome.tabs.sendMessage(tab.id, { action, ...data });
  } catch (e) {
    log(`页面通信失败: ${e.message}`, "err");
    return null;
  }
}

// ══════════════════════════════════════════
// 抓取流程
// ══════════════════════════════════════════

async function startScrape() {
  const jobId = state.jobId || document.getElementById("jobSelect").value;
  if (!jobId) { log("请先选择岗位", "err"); return; }
  if (!API_KEY) { log("请先填写 API Key", "err"); return; }

  // 检查页面
  const pageInfo = await sendToContent("get_page_info");
  if (!pageInfo || !pageInfo.isLiepin) {
    log("请打开猎聘简历详情页", "err");
    return;
  }

  log("正在抓取当前页面...");

  // 精准提取
  const result = await sendToContent("scrape_resume");
  if (!result || !result.success) {
    log(result?.error || "抓取失败", "err");
    return;
  }

  // 去重
  const exists = state.allResumes.some(r => r.detail_url === result.detail_url);
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
  state.allResumes.push(resume);

  // 保存到 storage
  chrome.storage.local.set({
    collectedResumes: state.allResumes,
    collectedJobId: jobId,
  });

  document.getElementById("collectedCount").textContent = state.allResumes.length;
  log(`✅ 已收集: ${resume.name} (${result.text_length}字)`, "ok");
}

async function submitResumes() {
  if (state.allResumes.length === 0) {
    // 尝试从 storage 恢复
    const stored = await chrome.storage.local.get("collectedResumes");
    if (stored.collectedResumes && stored.collectedResumes.length > 0) {
      state.allResumes = stored.collectedResumes;
      state.jobId = stored.collectedJobId;
    } else {
      log("没有待提交的简历，请先抓取", "err");
      return;
    }
  }

  const jobId = state.jobId || document.getElementById("jobSelect").value;
  if (!jobId) { log("请选择岗位", "err"); return; }

  const count = state.allResumes.length;
  log(`正在提交 ${count} 份简历到后端评分...`);

  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/submit_batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: parseInt(jobId), resumes: state.allResumes }),
    });
    const data = await resp.json();
    if (data.success) {
      log(`✅ 提交成功！${data.message || ""}`, "ok");
      state.allResumes = [];
      chrome.storage.local.remove(["collectedResumes", "collectedJobId"]);
      document.getElementById("collectedCount").textContent = "0";
    } else {
      log(`提交失败: ${data.error}`, "err");
    }
  } catch (e) {
    log(`提交失败: ${e.message}`, "err");
  }
}
// 发送到后端评分
// ══════════════════════════════════════════

// ══════════════════════════════════════════
// 工具函数
// ══════════════════════════════════════════

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ══════════════════════════════════════════
// 初始化
// ══════════════════════════════════════════

document.addEventListener("DOMContentLoaded", async () => {
  // 加载 storage 数据
  const stored = await chrome.storage.local.get(["apiKey", "serverUrl", "collectedResumes", "collectedJobId"]);
  if (stored.apiKey) {
    API_KEY = stored.apiKey;
    document.getElementById("apiKeyInput").value = stored.apiKey;
  }
  if (stored.serverUrl) API_BASE = stored.serverUrl;

  const serverOk = await checkServer();
  if (serverOk) await loadJobs();
  else log("无法连接服务器，请检查 API Key 和后端服务", "err");

  // 从 storage 恢复已收集的简历
  if (stored.collectedResumes && stored.collectedResumes.length > 0) {
    state.allResumes = stored.collectedResumes;
    state.jobId = stored.collectedJobId;
    document.getElementById("collectedCount").textContent = state.allResumes.length;
    log(`从缓存恢复 ${state.allResumes.length} 份简历`, "dim");
  }

  // 检查当前页面
  const pageInfo = await sendToContent("get_page_info");
  if (pageInfo) {
    if (pageInfo.isLiepin) {
      log(`猎聘页面就绪${pageInfo.isDetailPage ? " (详情页)" : ""}`, "ok");
    } else {
      log("请切换到猎聘简历详情页", "info");
    }
  }
});

// 按钮事件
document.getElementById("btnScrape").addEventListener("click", startScrape);
document.getElementById("btnSubmit").addEventListener("click", submitResumes);
document.getElementById("jobSelect").addEventListener("change", (e) => {
  state.jobId = parseInt(e.target.value) || null;
});

// API Key 保存
document.getElementById("btnSaveKey").addEventListener("click", () => {
  const key = document.getElementById("apiKeyInput").value.trim();
  if (!key) {
    log("请输入 API Key", "err");
    return;
  }
  API_KEY = key;
  chrome.storage.local.set({ apiKey: key, serverUrl: API_BASE }, () => {
    log("API Key 已保存到本地存储", "ok");
    chrome.storage.local.get(["apiKey", "serverUrl"], (data) => {
      if (data.apiKey === key) {
        log("存储验证成功 ✓", "ok");
      } else {
        log("存储验证失败！请重试", "err");
      }
    });
    checkServer().then(ok => { if (ok) loadJobs(); });
  });
});
