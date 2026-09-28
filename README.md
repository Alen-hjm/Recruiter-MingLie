# 明猎 · Recruiter-MingLie

面向猎头与招聘方的 AI 招聘自动化工作台。

把「拿到岗位 → 找候选人 → 判断合不合适 → 推荐给甲方」这条链路尽量自动化：
从猎聘抓取候选人简历，用大模型按岗位要求打分，再把合格的人整理成一份可直接发出的推荐报告。

项目同时提供两种使用方式：浏览器内的 **Chrome 扩展**（贴着猎聘页面干活）和完整的 **网页工作台**（管理岗位、候选人、报告）。

---

## 一、项目是做什么的

一个招聘动作，通常是这样的：

1. 甲方给一个岗位，先要把岗位描述（JD）拆清楚——硬性要求是什么、加分项是什么、该去搜什么关键词；
2. 拿着关键词去猎聘一页页翻候选人，看简历；
3. 逐个判断这人合不合适，写下理由；
4. 把合适的人整理成推荐报告发给甲方；
5. 后续还要跟踪：打过电话没有、推给甲方没有、进面试没有、发 offer 没有。

明猎就是把 1–4 步交给程序和大模型去做，第 5 步用「候选人管道」记录下来。

---

## 二、系统组成

整个系统分四层，从外到内：

| 层次 | 组成 | 说明 |
|---|---|---|
| 入口层 | Chrome 扩展、网页工作台 | 扩展贴在猎聘页面上用；工作台是完整的管理界面 |
| 服务层 | Flask 后端（`app/web/`） | 按职责分为 6 个 Blueprint，统一对外提供接口 |
| 能力层 | 抓取、评分、知识库、报告 | 四个相对独立的核心模块 |
| 数据层 | SQLite、`data/` 运行时数据、`browser_data/` 浏览器状态 | 数据落地的地方（均不入库，见第七节） |

### 目录结构

```
WorkSpace/
├─ run.py                       启动入口（默认 127.0.0.1:5000）
├─ requirements.txt             运行依赖（顶层）
├─ requirements-dev.txt         开发依赖（测试、检查工具）
├─ pytest.ini                   测试配置
├─ .env.example                 环境变量样例（复制为 .env 使用）
├─ app/                         后端全部逻辑
│  ├─ config.py                 ★ 集中配置：环境变量优先，路径/密钥/开关的唯一来源
│  ├─ auth.py                   ★ 扩展访问令牌：签发、校验、撤销（与 LLM Key 解耦）
│  ├─ models.py                 SQLite 数据模型与建表逻辑
│  ├─ web/                      ★ Web 层（原 server.py 拆分为 Blueprint 包）
│  │  ├─ __init__.py             应用工厂 create_app() / 初始化 init_all()
│  │  ├─ pages.py                13 个页面路由
│  │  ├─ api_core.py             统计、设置、岗位、搜索策略、简历编辑
│  │  ├─ api_tasks.py            抓取 / 评分 / 报告
│  │  ├─ api_agent.py            Agent 对话、记忆、候选人、管道、知识库、JD
│  │  ├─ api_extension.py        给 Chrome 扩展的 18 个接口（令牌鉴权）
│  │  ├─ api_tokens.py           令牌管理接口
│  │  └─ _helpers.py             共用工具（候选人键、评分合并、网页整理）
│  ├─ services/                 ★ 后台任务层（从路由中剥离的执行逻辑）
│  │  ├─ scraping.py             猎聘抓取任务：配置组装、线程执行、去重落库
│  │  └─ scoring.py              评分任务：知识增强、并发、低分清理、管道录入
│  ├─ scraper_liepin.py         猎聘猎头端简历抓取器（Playwright）
│  ├─ anti_detect.py            反检测：把自动化操作模拟得更像真人
│  ├─ analyzer.py               LLM 简历分析与打分
│  ├─ jd_analyzer.py            岗位描述（JD）智能拆解
│  ├─ adaptive.py               评分不理想时自动调整搜索策略重试
│  ├─ knowledge.py              知识库读取（对接 Obsidian 笔记）
│  ├─ knowledge_engine.py       招聘知识引擎：采集、存储、检索、发现规律
│  ├─ agent.py                  Agent 核心：规划 + 执行循环
│  ├─ agent_tools.py            Agent 可调用的 12 个工具
│  ├─ agent_memory.py           Agent 记忆：从反馈中提炼经验
│  ├─ resume_parser.py          简历分段解析（锚点切分 + 置信度）
│  ├─ report_engine.py          推荐报告生成引擎（模板驱动）
│  ├─ pipeline_report.py        精简版 Markdown 评分报告
│  └─ platforms.py              其他招聘平台的适配器模板
├─ tests/                       ★ pytest 测试（鉴权 / 报告 / 解析 / Web / 架构约束）
├─ scripts/smoke_test.py        ★ 启动冒烟测试：真跑一遍服务
├─ templates/                   网页工作台的 13 个页面
├─ static/css/style.css         页面样式
├─ config/report_templates/     推荐报告模板（headhunter_v1.yaml）
├─ chrome-extension/            Chrome MV3 扩展
├─ data/                        运行时数据（不入库）
└─ browser_data/                浏览器用户资料，含登录态（不入库）
```

---

## 三、核心能力

### 1. 简历抓取（`scraper_liepin.py`）

用 Playwright 驱动真实浏览器，登录猎聘猎头端（h.liepin.com），按关键词搜索候选人，自动翻页、提取简历卡片、进入详情页抓取完整信息。

支持三种浏览器启动模式：

| 模式 | 做法 | 特点 |
|---|---|---|
| `cdp` | 连接一个已启动的 Chrome 调试端口，WebSocket 直连 | 最难被识别为脚本，**推荐** |
| `stealth` | 用反检测参数启动 + 复用真实浏览器的登录数据 | 无需手动开浏览器 |
| `basic` | 纯 Playwright 启动 | 最弱，容易触发风控 |

配套 `anti_detect.py` 做反检测：随机延迟、模拟真人鼠标轨迹/滚动、操作节奏控制（`HumanPacer`）。

### 2. AI 评分（`analyzer.py` + `jd_analyzer.py`）

- `jd_analyzer.py` 先把岗位描述拆成结构化信息：行业方向、职业类别、硬性要求、加分项、搜索关键词；
- `analyzer.py` 再拿这份标准去逐份分析简历，给出分数和理由；
- 打分时会检索知识库做增强；
- `adaptive.py` 是兜底：如果一轮下来高分候选人太少，会自动扩展关键词、换策略重跑。

分数分三档处理：**≥80 分推荐面试，60–80 分待定，<60 分淘汰**。推荐档的人会自动进入候选人管道。

### 3. 招聘知识库（`knowledge_engine.py`）

一个会自己积累的招聘知识系统：

- 从简历、岗位、行业资讯、面试反馈里采集知识；
- 结构化存到知识库（本地笔记 + SQLite 双写）；
- 评分时检索相关知识，让判断更有依据；
- 从历史反馈里发现规律（比如"这类背景的人后来都在面试挂了"），沉淀成经验。

### 4. 推荐报告（`report_engine.py` + `resume_parser.py`）

围绕四个原则设计：

- **零编造**——报告里每个字都来自「简历原文」或「评分阶段已经生成的内容」，报告引擎自己不再调 LLM 重写任何叙述，避免二次幻觉、也避免重复花钱；
- **可归因**——每个字段都记录来源和置信度，缺的一律留空并标「需人工补充」，绝不用全文兜底填充；
- **模板驱动**——字段清单写在 `config/report_templates/headhunter_v1.yaml` 里，加字段、改顺序、改标题都不用动 Python 代码；
- **校验先行**——生成后检查必填缺失和占位符残留，有问题明确提示，不静默放行。

模板里每个字段的 `source` 分三种，含义不同：

| source | 含义 |
|---|---|
| `code` | 代码直接取值，不经 LLM |
| `resume` | 简历原文直引，禁止改写 |
| `llm` | LLM 生成，但必须附原文证据，对不上就丢弃 |

报告可导出为 Word 文档。

### 5. Agent 闭环（`agent.py` + `agent_tools.py`）

一个对话式 Agent，把上述能力串成完整的招聘闭环。它可以调用 12 个工具：

```
analyze_jd              create_job              scrape_resumes
score_resumes           get_candidates          search_knowledge
get_job_list            full_recruit            save_job_to_obsidian
update_candidate_status get_pipeline            sync_pipeline_to_obsidian
```

引导流程为 6 步：获取 JD → 分析 JD 并建岗位 → 确认后抓取评分 → 跟踪进展 → 推进到 offer/入职。

`agent_memory.py` 会从用户的反馈（比如「这个人淘汰了，原因是……」）里提炼经验，供后续评分参考。

### 6. Chrome 扩展（`chrome-extension/`）

Manifest V3 扩展，以侧边栏为主界面，直接贴在猎聘页面上使用。

- `content.js` 负责读取当前页面的内容；
- `sidepanel.js` 是主界面逻辑；
- `deepseek.js` 是直连 DeepSeek 的流式（SSE）客户端，评分时不经过后端，直接调用大模型接口。

扩展既支持「走本地后端」，也支持「直连大模型」（在后端没启动时也能用）。

---

## 四、技术栈

- **后端**：Python 3.12、Flask 3.1、Flask-Login、Flask-CORS
- **数据库**：SQLite（`data/minglie.db`）
- **前端**：Jinja2 模板 + 原生 CSS
- **浏览器自动化**：Playwright、playwright-stealth
- **大模型调用**：OpenAI SDK / httpx，兼容 DeepSeek 等接口
- **文档处理**：python-docx、pdfplumber、PyMuPDF、reportlab、weasyprint
- **其他**：pandas、numpy、openpyxl、rich

数据表：`users`、`jobs`、`scrape_tasks`、`score_tasks`、`settings`、`interview_feedback`、`knowledge`、`knowledge_log`、`search_strategies`、`api_tokens`

---

## 五、快速开始

### 启动后端

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux: source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium      # 抓取依赖真实浏览器内核

python run.py                    # 默认 127.0.0.1:5000
python run.py 5001               # 也可以指定端口
```

启动后访问 http://127.0.0.1:5000，首次使用需要注册账号。

### 配置大模型

**方式一（推荐）：环境变量**

```bash
cp .env.example .env       # 然后编辑 .env 填入 MINGLIE_LLM_API_KEY
```

**方式二：网页设置页**

首次使用也可以在「设置」页填写大模型 API Key（支持 DeepSeek 等兼容接口），配置会保存到本地数据库。

> **优先级**：逐字段合并，**设置页填的优先，留空的字段回落到环境变量**。
> 也就是说，只在环境变量里配了 Key、设置页留空时，系统会直接用环境变量里的那份。
> 出于安全考虑，设置页保存后不再回显明文 Key（只显示 `sk-abc***mnop` 形式的提示串），
> 输入框留空即表示「不修改已保存的 Key」。

### 安装 Chrome 扩展

1. 打开 `chrome://extensions/`；
2. 打开右上角「开发者模式」；
3. 点「加载已解压的扩展程序」，选择本项目的 `chrome-extension/` 目录；
4. 回到网页端「设置」页，点「生成」创建一个**访问令牌**，复制粘贴到扩展设置里。

> 令牌与 LLM API Key 是两回事：令牌只用于访问本机后端，可随时撤销；
> 即使令牌泄露，也不会连带暴露你的大模型密钥与余额。

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest                            # 单元测试
python scripts/smoke_test.py      # 启动冒烟测试（真跑一遍服务）
```

---

## 六、目前实际状态

如实说明各部分成熟度，便于判断哪里能直接用、哪里还在路上。

### 已经跑通的

- **抓取**：猎聘猎头端的搜索、翻页、详情抓取整体可用，三种启动模式与反检测均已实现；是目前代码量最大的模块。
- **评分**：JD 拆解 + 简历打分链路可用，支持知识库增强和低分自动重试。
- **网页工作台**：13 个页面都已完成（看板、抓取、评分、岗位、候选人、面试、报告、设置、日志、登录注册等）。
- **推荐报告**：模板驱动 + 零编造设计已经落地，可导出 Word。
- **Chrome 扩展**：侧边栏抓取与流式评分可用，且支持不依赖后端直连大模型。
- **Agent 闭环**：6 步流程与 12 个工具已实现。
- **测试**：`tests/` 下有 35 个用例，覆盖鉴权、报告/解析、Web 路由与架构约束。

### 还没完成 / 需要改进的

- **桌面版未实现**。当时有打包成桌面应用的打算，但目前没有落地。
- **测试覆盖仍偏薄**。现有用例集中在鉴权、报告引擎与路由层；抓取器、评分器、知识引擎这些最复杂的模块还没有测试——它们依赖真实浏览器和 LLM，需要先做可注入的接口抽象。
- **登录与权限较简单**，用户体系只做了基础注册/登录，没有角色、审计日志、密码重置。
- **`app/web/api_*.py` 仍有优化空间**。Blueprint 拆分解决了"单文件 89KB"的问题，但每个文件依然偏大，后续可继续按资源（job / resume / report）细分。
- **候选人键口径不统一**。`models._get_resume_key`、`web._helpers.resume_key`、`agent_tools` 内部各有一份实现，虽然逻辑一致，但应合并到一处。

### 已知的坏味道

- `app/scraper_liepin.py:1176` 内嵌的 JS 正则里有 `\s` 这类序列，Python 会发 `SyntaxWarning: invalid escape sequence`。运行时行为正确（原样保留），属于历史遗留，改动需谨慎：把整段字符串改成 raw 会一并改变同段内其他转义的含义（曾试过全局替换，反倒把 `\\d` 变成 `\\\d` 引发 SyntaxError），要改只能逐行人工核对。
- `data/` 里可能有大量成对出现的抓取快照（时间戳仅相差几秒、内容高度相似），怀疑存在重复抓取或双写，尚未排查。

---

## 七、数据与安全约定

以下几类内容绝不进版本库（已在 `.gitignore` 中排除）：

| 路径 | 内容 | 原因 |
|---|---|---|
| `data/` | 数据库、抓取结果、简历 | 含明文 API Key、候选人真实简历、用户数据 |
| `browser_data/` | 浏览器用户资料 | 含猎聘登录态 Cookie |
| `.env` / `.env.*` | 环境变量 | 可能含密钥 |
| `__pycache__/`、`*.pyc` | Python 编译缓存 | 无意义产物 |

`data/` 目录本身保留了一个 `.gitkeep` 占位，保证克隆后目录存在。

### 凭据设计（重要）

本项目区分两种凭据，**不要把它们混用**：

| 凭据 | 用途 | 存放位置 | 传输头 |
|---|---|---|---|
| **LLM API Key** | 调用大模型 | 环境变量或数据库 | 仅用于向 LLM 服务商发请求 |
| **扩展访问令牌**（`ml_` 开头） | 扩展访问本机后端 | 数据库只存 SHA-256 摘要 | `X-API-Token` |

- 令牌明文只在生成时返回一次；列表接口只给 `token_hint`（如 `ml_abc…xyz`）。
- 令牌可随时撤销，撤销后立即失效。
- 凭据**只从请求头读取**，不接受 `?api_key=` 查询串（避免密钥进入访问日志）。
- 旧版本用 LLM Key 当扩展凭据，这段兼容通道由 `MINGLIE_ALLOW_LEGACY_KEY_AUTH` 控制，
  **默认开启**以免打断已在用的旧扩展；完成迁移后请设为 `0`。

### 建议的网络配置

- 默认只监听 `127.0.0.1`。需要局域网访问时显式设 `MINGLIE_HOST=0.0.0.0`，
  并同时设置 `MINGLIE_SECRET_KEY`，否则同网段任何人都能访问到候选人库。
- 生产环境把扩展的确切 ID 写进 `MINGLIE_EXTENSION_ORIGINS`，收窄 CORS 白名单。

---

## 八、开发约定

- 中文注释与中文文档；代码注释解释「为什么这么做」，而不只是「做了什么」。
- 涉及大模型输出的一律遵守**零编造**原则：凡是 LLM 生成的内容都必须附原文证据，证据对不上就丢弃。
- 解析类逻辑（简历、JD）采用**严格模式**：锚点没命中就留空，不用全文兜底填充。
- 交付物以本地可打开的文件为准（报告导出为 docx 等）。
- 依赖方向保持单向：`web/* → services/* → models/scraper/analyzer`，
  `services/` 不得反向依赖 `web/`（`tests/test_architecture.py` 会检查这一点）。

---

## 附：本次重构说明

本文档描述的是当前版本。相比早先的单文件版本，做了一次围绕「可维护性 + 凭据安全」的重构，涉及面较大，这里留一份记录。

### 解决了什么

**1. 凭据与模型 Key 彻底解耦（安全修复）**

原先扩展用 `X-API-Key` 请求头传的是**大模型 API Key**，后端拿它去 `settings.llm_api_key` 反查用户。连锁后果：轮换模型 Key 会让扩展掉线；任何拿到这把 Key 的人（浏览器存储、截图、日志）同时获得后端完整访问权、以及刷爆余额的能力；`?api_key=` 查询串传参还会把密钥写进 Flask 访问日志与浏览器历史；`/api/my-api-key` 直接把明文 Key 吐给前端。

现在改为独立的访问令牌（`app/auth.py`）：`ml_` 前缀、数据库只存 SHA-256 摘要、可撤销、明文只在生成时返回一次；凭据只从请求头读取。旧 Key 通道保留为兼容过渡，由 `MINGLIE_ALLOW_LEGACY_KEY_AUTH` 控制。

**2. 单文件 89KB → 6 个 Blueprint**

`app/server.py` 的 71 个路由（含 18 个扩展接口）拆到 `app/web/` 下的 6 个模块；后台执行逻辑抽到 `app/services/`。同时解开了 `agent_tools.py` 对 `server.py` 的反向导入——那正是「单独 import 工具层会把整个 Flask 应用连带拉起」的原因。`tests/test_architecture.py` 现在会守住这条边界。

**3. 配置集中（`app/config.py`）**

路径、密钥、监听地址、CORS 白名单、抓取参数统一从环境变量读取。顺带修掉几个隐患：`app.secret_key` 从 `os.urandom(24)`（每次重启换密钥、所有会话立即失效）改为环境变量或持久化文件；`DB_PATH` 从依赖进程工作目录（换个目录启动就会静默新建空库）改为以项目根为锚点；默认监听从 `0.0.0.0` 收窄到 `127.0.0.1`。

**4. 修掉两个真实 bug**

- `api_extension_score` 调用 `save_scrape_results_dedup` 时只传了 3 个参数，而被调函数签名要求 7 个——一旦走到那行必然 `TypeError`。
- `agent_tools._tool_full_recruit` 里用了未定义的变量 `settings`，走到「同步管道到 Obsidian」就会 `NameError`。

**5. 补齐测试与清理**

新增 `tests/`（35 个用例：鉴权、报告引擎、简历解析、Web 路由越权防护、架构约束）与 `scripts/smoke_test.py`（真起服务跑一遍）。删掉 `app/browser_agent/` 这个与 `scraper_liepin.py` 功能重复的实验骨架；修掉 `.gitignore` 里一条从未生效的规则（`{app,templates,static/` 括号未闭合，gitignore 不支持花括号展开）；依赖清单从 129 行全量冻结改为按用途分层。

### 一点历史

仓库早期还有一套 `MingLie2.0/` 的完整分层重写（`domain/`、`repositories/`、`services/`、`orchestration/`，带 `tests/test_workbench.py`），后来被整体删除，当前版本是另一条路线。这个决定当时没有留下文字记录，只存在于提交历史里 —— 这里补记一笔，免得后来者对着 `git log` 猜。

### 本次未改动的部分

**猎聘抓取能力完整保留。** `scraper_liepin.py`（20 个方法）与 `anti_detect.py` 的业务逻辑一行未改：三种启动模式（`cdp` / `stealth` / `basic`）、翻页、详情抓取、快速筛选、CSV+JSON 落盘的行为与重构前一致，只是配置来源改为从 `app/config.py` 取（由 `tests/test_architecture.py` 断言守住）。

---

本项目为个人招聘业务提效工具，涉及第三方平台数据的抓取与处理，请遵守目标平台的服务条款，仅用于合规用途。
