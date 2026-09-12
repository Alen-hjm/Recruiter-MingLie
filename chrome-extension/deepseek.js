/**
 * 明猎 - 直连 DeepSeek（流式 SSE 客户端）
 *
 * 设计要点：
 *  - 经典脚本：浏览器中挂到 window.MinglieDeepseek；Node 中通过 module.exports 暴露，便于单测。
 *  - 与 BOSS 插件同款：直接 fetch api.deepseek.com，单次 chat/completions，stream:true。
 *  - SSE 解析：按行切分，跳过空行与非 data: 行，遇到 data:[DONE] 结束。
 *  - 对 UTF-8 多字节被 TCP 拆包做了保护（TextDecoder stream:true 会缓存半截字符）。
 *
 * @param {object}   opts
 * @param {string}   opts.systemPrompt
 * @param {string}   opts.userPrompt
 * @param {string}   opts.apiKey         DeepSeek API Key（Bearer）
 * @param {string}   [opts.model]        默认 deepseek-chat
 * @param {string}   [opts.apiBase]      默认官方地址；测试时可指向本地 mock
 * @param {function} [opts.onToken]      每个 token 回调 (token, full)
 * @param {AbortSignal} [opts.signal]
 * @param {number}   [opts.temperature]
 * @returns {Promise<string>} 完整文本
 */
const DS_API_BASE = "https://api.deepseek.com/v1";

async function streamChat(opts) {
  const {
    systemPrompt = "",
    userPrompt = "",
    apiKey,
    model = "deepseek-chat",
    apiBase = DS_API_BASE,
    onToken = null,
    signal = null,
    temperature = 0.5,
  } = opts || {};

  if (!apiKey) {
    throw new Error("未配置 DeepSeek API Key（请在设置里填写 DeepSeek Key）");
  }

  let resp;
  try {
    resp = await fetch(`${apiBase}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
        stream: true,
        temperature,
      }),
      signal,
    });
  } catch (e) {
    throw new Error(`请求 DeepSeek 失败：${e.message}`);
  }

  if (!resp.ok) {
    let detail = "";
    try {
      detail = (await resp.text()).slice(0, 200);
    } catch (_) {
      /* ignore */
    }
    if (resp.status === 401) throw new Error("DeepSeek Key 无效或被拒（401），请检查 Key");
    if (resp.status === 429) throw new Error("DeepSeek 触发限流（429），稍后再试");
    throw new Error(`DeepSeek 返回 ${resp.status}：${detail}`);
  }

  if (!resp.body || typeof resp.body.getReader !== "function") {
    throw new Error("当前环境不支持流式读取（resp.body 不可用）");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let full = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const rawLine = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        const line = rawLine.trim();
        if (!line) continue;
        if (!line.startsWith("data:")) continue;

        const dataStr = line.slice(5).trim();
        if (dataStr === "[DONE]") {
          return full;
        }
        let payload;
        try {
          payload = JSON.parse(dataStr);
        } catch (_) {
          // 跳过无法解析的片段（如 ":keep-alive" 或注释行）
          continue;
        }
        const token = payload && payload.choices && payload.choices[0] && payload.choices[0].delta && payload.choices[0].delta.content;
        if (token) {
          full += token;
          if (onToken) onToken(token, full);
        }
      }
    }

    // flush 残留 buffer（末尾可能没有换行）
    const tail = buffer.trim();
    if (tail.startsWith("data:")) {
      const dataStr = tail.slice(5).trim();
      if (dataStr && dataStr !== "[DONE]") {
        try {
          const payload = JSON.parse(dataStr);
          const token = payload && payload.choices && payload.choices[0] && payload.choices[0].delta && payload.choices[0].delta.content;
          if (token) {
            full += token;
            if (onToken) onToken(token, full);
          }
        } catch (_) {
          /* ignore */
        }
      }
    }
    return full;
  } finally {
    // 释放底层流，避免连接挂起
    try {
      await reader.cancel();
    } catch (_) {
      /* ignore */
    }
  }
}

if (typeof window !== "undefined") {
  window.MinglieDeepseek = { streamChat, DS_API_BASE };
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { streamChat };
}
