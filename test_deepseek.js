/**
 * 本地验证 deepseek.js 的流式 SSE 解析（无需真实 API Key）
 * 覆盖：多字节 UTF-8 被 TCP 拆包、keep-alive 注释行、[DONE] 收尾、401 错误路径
 */
const http = require("http");
const { streamChat } = require("D:/extension/WorkSpace/chrome-extension/deepseek.js");

const TOKENS = ["你好", "，候选人", "匹配度", "评分高"];
const EXPECTED = TOKENS.join("");

function buildBody() {
  const lines = [
    `data: {"choices":[{"delta":{"content":"${TOKENS[0]}"}}]}`,
    `data: {"choices":[{"delta":{"content":"${TOKENS[1]}"}}]}`,
    `data: {"choices":[{"delta":{"content":"${TOKENS[2]}"}}]}`,
    `data: {"choices":[{"delta":{"content":"${TOKENS[3]}"}}]}`,
    `:keep-alive`,                 // 注释行，应被跳过
    `data: [DONE]`,
  ];
  return lines.join("\n\n") + "\n\n";
}

const body = buildBody();
const buf = Buffer.from(body, "utf-8");
// 在「评」(E8 AF 95) 的第一个字节之后切开，制造真实的多字节拆包
const marker = Buffer.from("评分高", "utf-8");
const idx = buf.indexOf(marker);
const splitAt = idx + 1; // 落在「评」的第二个字节处
const chunk1 = buf.slice(0, splitAt);
const chunk2 = buf.slice(splitAt);

const server = http.createServer((req, res) => {
  const auth = req.headers["authorization"] || "";
  if (auth.includes("force-401")) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "invalid api key" }));
    return;
  }
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  res.write(chunk1);
  setTimeout(() => {
    res.write(chunk2);
    res.end();
  }, 30);
});

function assert(cond, msg) {
  if (!cond) {
    console.error("❌ FAIL:", msg);
    process.exitCode = 1;
  } else {
    console.log("✅ PASS:", msg);
  }
}

async function run() {
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  const apiBase = `http://127.0.0.1:${port}`;

  // ── 测试 1：成功流式 + UTF-8 拆包 + keep-alive 跳过 ──
  const gotTokens = [];
  let full = "";
  try {
    full = await streamChat({
      systemPrompt: "sys",
      userPrompt: "hi",
      apiKey: "sk-test",
      apiBase,
      onToken: (t, f) => {
        gotTokens.push(t);
        // 中途校验「前缀一致性」：每次 full 都是已收 token 的拼接
        assert(f === gotTokens.join(""), "onToken 的 full 等于已收 token 拼接");
      },
    });
  } catch (e) {
    assert(false, "正常流未抛错，实际: " + e.message);
  }
  assert(full === EXPECTED, `完整文本拼接正确 (得到="${full}" / 期望="${EXPECTED}")`);
  assert(gotTokens.length === TOKENS.length, `onToken 回调次数=${gotTokens.length} (期望 ${TOKENS.length})`);

  // ── 测试 2：401 错误路径 ──
  let threw = false;
  let errMsg = "";
  try {
    await streamChat({ systemPrompt: "s", userPrompt: "u", apiKey: "force-401", apiBase });
  } catch (e) {
    threw = true;
    errMsg = e.message;
  }
  assert(threw, "401 时抛错");
  assert(errMsg.includes("401"), `401 错误信息含状态 (得到="${errMsg}")`);

  // ── 测试 3：缺 Key ──
  let threw3 = false;
  try {
    await streamChat({ systemPrompt: "s", userPrompt: "u", apiKey: "", apiBase });
  } catch (e) {
    threw3 = true;
  }
  assert(threw3, "缺 apiKey 时抛错");

  server.close();
  console.log(process.exitCode ? "\n=== 存在 BUG ===" : "\n=== 全部通过，无 BUG ===");
}

run();
