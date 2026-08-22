/**
 * 明猎 - Popup 逻辑
 * 控制抓取流程，与 content script 和后端通信
 */

const API_BASE = "http://127.0.0.1:5000";
let API_KEY = "";

// 从 storage 加载 API Key
chrome.storage.local.get("apiKey", (data) => {
  if (data.apiKey) API_KEY = data.apiKey;
});

function apiFetch(url, options = {}) {
  const headers = { ...options.headers };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return fetch(url, { ...options, headers });
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
  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/jobs`);
    const data = await resp.json();
    if (data.success) {
      document.getElementById("serverDot").className = "server-status server-online";
      document.getElementById("serverStatus").textContent = "已连接";
      return true;
    } else {
      document.getElementById("serverDot").className = "server-status server-offline";
      document.getElementById("serverStatus").textContent = data.error || "API Key 无效";
    }
  } catch (e) {}
  document.getElementById("serverDot").className = "server-status server-offline";
  document.getElementById("serverStatus").textContent = "服务器未连接";
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
  if (state.running) return;

  // 检查页面
  const pageInfo = await sendToContent("get_page_info");
  if (!pageInfo || !pageInfo.isLiepin) {
    log("请先打开猎聘搜索页面 (h.liepin.com)", "err");
    return;
  }
  if (pageInfo.cardCount === 0) {
    log("当前页面没有简历卡片，请先搜索", "err");
    return;
  }

  state.running = true;
  state.currentPage = 0;
  state.allResumes = [];
  state.stats = { cards: 0, scraped: 0, filtered: 0 };
  state.maxPages = parseInt(document.getElementById("maxPages").value) || 3;

  // 清空旧缓存
  chrome.storage.local.remove("lastScrape");

  document.getElementById("btnStart").style.display = "none";
  document.getElementById("btnStop").style.display = "inline-block";

  log(`开始抓取，最多 ${state.maxPages} 页`);
  await scrapeLoop();
}

async function scrapeLoop() {
  while (state.running && state.currentPage < state.maxPages) {
    state.currentPage++;
    log(`--- 第 ${state.currentPage}/${state.maxPages} 页 ---`);

    // 获取筛选规则（从后端岗位配置）
    let filterRules = null;
    if (state.jobId) {
      try {
        const resp = await apiFetch(`${API_BASE}/api/extension/filter_rules/${state.jobId}`);
        const data = await resp.json();
        if (data.success && data.rules) {
          filterRules = data.rules;
        }
      } catch (e) {}
    }

    // 抓取当前页
    const result = await sendToContent("scrape_page", { filterRules });
    if (!result) {
      log("抓取失败，停止", "err");
      break;
    }

    state.stats.cards += result.total;
    state.stats.filtered += result.filtered;
    state.stats.scraped += result.resumes.length;

    log(
      `卡片: ${result.total} | 抓取: ${result.resumes.length} | 过滤: ${result.filtered} | 跳过已阅: ${result.skipped}`,
      result.resumes.length > 0 ? "ok" : "dim"
    );

    state.allResumes.push(...result.resumes);
    updateStats();

    // 每抓完一页，自动同步到后端
    if (result.resumes.length > 0 && state.jobId && API_KEY) {
      try {
        await apiFetch(`${API_BASE}/api/extension/submit_batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            job_id: state.jobId,
            resumes: result.resumes,
          }),
        });
        log(`已同步 ${result.resumes.length} 份到后端`, "ok");
        // 发送成功后清空缓存，避免重复
        state.allResumes = [];
        chrome.storage.local.remove("lastScrape");
      } catch (e) {
        log(`同步失败: ${e.message}`, "err");
      }
    }

    // 检查是否还有下一页
    if (state.currentPage < state.maxPages) {
      const nextResult = await sendToContent("click_next_page");
      if (!nextResult || !nextResult.success) {
        log("已到最后一页", "dim");
        break;
      }
      // 等待页面加载
      await sleep(2000 + Math.random() * 1500);
    }
  }

  // 抓取完成
  state.running = false;
  document.getElementById("btnStart").style.display = "inline-block";
  document.getElementById("btnStop").style.display = "none";

  log(`抓取完成！共 ${state.allResumes.length} 份简历`, "ok");

  // 保存到 storage
  chrome.storage.local.set({
    lastScrape: {
      resumes: state.allResumes,
      jobId: state.jobId,
      timestamp: Date.now(),
    },
  });
}

function stopScrape() {
  state.running = false;
  document.getElementById("btnStart").style.display = "inline-block";
  document.getElementById("btnStop").style.display = "none";
  log("已停止抓取", "info");
}

function updateStats() {
  document.getElementById("statCards").textContent = state.stats.cards;
  document.getElementById("statScraped").textContent = state.stats.scraped;
  document.getElementById("statFiltered").textContent = state.stats.filtered;
  document.getElementById("statPage").textContent = `${state.currentPage}/${state.maxPages}`;
}

// ══════════════════════════════════════════
// 发送到后端评分
// ══════════════════════════════════════════

async function sendToScore() {
  if (state.allResumes.length === 0) {
    // 尝试从 storage 恢复
    const stored = await chrome.storage.local.get("lastScrape");
    if (stored.lastScrape && stored.lastScrape.resumes.length > 0) {
      state.allResumes = stored.lastScrape.resumes;
      state.jobId = stored.lastScrape.jobId;
      log(`从缓存恢复 ${state.allResumes.length} 份简历`);
    } else {
      log("没有可发送的简历，请先抓取", "err");
      return;
    }
  }

  const jobId = state.jobId || document.getElementById("jobSelect").value;
  if (!jobId) {
    log("请选择岗位", "err");
    return;
  }

  log(`正在发送 ${state.allResumes.length} 份简历到后端...`);

  try {
    const resp = await apiFetch(`${API_BASE}/api/extension/submit_batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: parseInt(jobId),
        resumes: state.allResumes,
      }),
    });

    const data = await resp.json();
    if (data.success) {
      log(`✅ 发送成功！${data.message}`, "ok");
      log(`可在网页端 http://localhost:5000 查看评分结果`, "info");
    } else {
      log(`发送失败: ${data.error}`, "err");
    }
  } catch (e) {
    log(`发送失败: ${e.message}`, "err");
  }
}

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
  // 加载 API Key
  const keyData = await chrome.storage.local.get("apiKey");
  if (keyData.apiKey) {
    API_KEY = keyData.apiKey;
    document.getElementById("apiKeyInput").value = keyData.apiKey;
  }

  await checkServer();
  await loadJobs();

  // 从 storage 恢复状态
  const stored = await chrome.storage.local.get("lastScrape");
  if (stored.lastScrape) {
    const count = stored.lastScrape.resumes ? stored.lastScrape.resumes.length : 0;
    if (count > 0) {
      log(`缓存中有 ${count} 份上次抓取的简历`, "dim");
      state.allResumes = stored.lastScrape.resumes;
      state.jobId = stored.lastScrape.jobId;
      state.stats.scraped = count;
      updateStats();
    }
  }

  // 检查当前页面
  const pageInfo = await sendToContent("get_page_info");
  if (pageInfo) {
    document.getElementById("statCards").textContent = pageInfo.cardCount;
    if (pageInfo.isLiepin) {
      log(`猎聘页面就绪，${pageInfo.cardCount} 个简历卡片`, "ok");
    } else {
      log("请切换到猎聘搜索页面", "info");
    }
  }
});

// 按钮事件
document.getElementById("btnStart").addEventListener("click", startScrape);
document.getElementById("btnStop").addEventListener("click", stopScrape);
document.getElementById("btnSend").addEventListener("click", sendToScore);
document.getElementById("jobSelect").addEventListener("change", (e) => {
  state.jobId = parseInt(e.target.value) || null;
});

// API Key 保存
document.getElementById("btnSaveKey").addEventListener("click", () => {
  const key = document.getElementById("apiKeyInput").value.trim();
  if (key) {
    API_KEY = key;
    chrome.storage.local.set({ apiKey: key });
    log("API Key 已保存", "ok");
    checkServer().then(() => loadJobs());
  }
});
