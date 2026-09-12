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
  const area = document.getElementById("logArea");
  if (!area) return { textContent: "" };
  const entry = document.createElement("div");
  entry.className = "log-entry log-info";
  entry.textContent = `⏳ ${name}：连接中...`;
  area.appendChild(entry);
  area.scrollTop = area.scrollHeight;
  return entry;
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

  const dsStored = await chrome.storage.local.get(["dsApiKey", "dsModel"]);
  const dsKey = dsStored.dsApiKey;
  const dsModel = dsStored.dsModel || "deepseek-chat";

  // 直连 DeepSeek 流式评分
  if (dsKey) {
    const jobName = document.getElementById("jobSelect").selectedOptions?.[0]?.text || "";
    log(`正在用 DeepSeek 直连评分 ${state.allResumes.length} 份简历...`, "info");
    let okCount = 0;
    for (const r of state.allResumes) {
      const entry = createEvalEntry(r.name || "未知");
      try {
        const userPrompt = buildScorePrompt(r, jobName);
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
    log(`直连评分完成：${okCount}/${state.allResumes.length} 份`, okCount === state.allResumes.length ? "ok" : "warn");
    state.allResumes = [];
    chrome.storage.local.remove(["collectedResumes", "collectedJobId"]);
    document.getElementById("collectedCount").textContent = "0";
    return;
  }

  // 回退：后端评分
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
  const stored = await chrome.storage.local.get(["apiKey", "serverUrl", "collectedResumes", "collectedJobId", "dsApiKey", "dsModel"]);
  if (stored.apiKey) {
    API_KEY = stored.apiKey;
    document.getElementById("apiKeyInput").value = stored.apiKey;
  }
  if (stored.serverUrl) API_BASE = stored.serverUrl;
  if (stored.dsApiKey) {
    const dsEl = document.getElementById("dsKeyInput");
    if (dsEl) dsEl.value = stored.dsApiKey;
  }

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

// API Key 保存（后端 Key 与 DeepSeek Key 各自独立）
document.getElementById("btnSaveKey").addEventListener("click", () => {
  const key = document.getElementById("apiKeyInput").value.trim();
  const dsKey = document.getElementById("dsKeyInput") ? document.getElementById("dsKeyInput").value.trim() : "";
  if (!key && !dsKey) {
    log("请至少填写一个 Key（后端或 DeepSeek）", "err");
    return;
  }
  API_KEY = key;
  chrome.storage.local.set({ apiKey: key, serverUrl: API_BASE, dsApiKey: dsKey }, () => {
    log("Key 已保存到本地存储", "ok");
    if (key) {
      checkServer().then(ok => { if (ok) loadJobs(); });
    } else {
      log("未填后端 Key：提交评分将走 DeepSeek 直连", "info");
    }
  });
});
