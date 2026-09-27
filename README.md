# 明猎 · Recruiter-MingLie

面向猎头与招聘方的 **AI 招聘自动化工作台**。

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
| --- | --- | --- |
| 入口层 | Chrome 扩展、网页工作台 | 扩展贴在猎聘页面上用；工作台是完整的管理界面 |
| 服务层 | Flask 后端（`app/server.py`） | 统一对外提供接口，调度下面几个能力模块 |
| 能力层 | 抓取、评分、知识库、报告 | 四个相对独立的核心模块 |
| 数据层 | SQLite、`data/` 运行时数据、`browser_data/` 浏览器状态 | 数据落地的地方（均不入库，见第七节） |

### 目录结构

```
WorkSpace/
├─ run.py                      启动入口（默认 5000 端口）
├─ requirements.txt            依赖清单
├─ app/                        后端全部逻辑
│  ├─ server.py                后端 API 服务，所有接口都在这里
│  ├─ models.py                SQLite 数据模型与建表逻辑
│  ├─ scraper_liepin.py        猎聘猎头端简历抓取器（Playwright）
│  ├─ anti_detect.py           反检测：把自动化操作模拟得更像真人
│  ├─ analyzer.py              LLM 简历分析与打分
│  ├─ jd_analyzer.py           岗位描述（JD）智能拆解
│  ├─ adaptive.py              评分不理想时自动调整搜索策略重试
│  ├─ knowledge.py             知识库读取（对接 Obsidian 笔记）
│  ├─ knowledge_engine.py      招聘知识引擎：采集、存储、检索、发现规律
│  ├─ agent.py                 Agent 核心：规划 + 执行循环
│  ├─ agent_tools.py           Agent 可调用的工具注册表
│  ├─ agent_memory.py          Agent 记忆：从反馈中提炼经验
│  ├─ resume_parser.py         简历分段解析（锚点切分 + 置信度）
│  ├─ report_engine.py         推荐报告生成引擎（模板驱动）
│  ├─ pipeline_report.py       精简版 Markdown 评分报告
│  ├─ platforms.py             其他招聘平台的适配器模板
│  └─ browser_agent/           通用浏览器 Agent 骨架（实验性，见第六节）
├─ templates/                  网页工作台的 13 个页面
├─ static/css/style.css        页面样式
├─ config/report_templates/    推荐报告模板（headhunter_v1.yaml）
├─ chrome-extension/           Chrome MV3 扩展
├─ data/                       运行时数据（不入库）
│  └─ minglie.db               SQLite 数据库
├─ browser_data/               浏览器用户资料，含登录态（不入库）
└─ docs/                       设计文档
```

---

## 三、核心能力

### 1. 简历抓取（`scraper_liepin.py`）

用 Playwright 驱动真实浏览器，登录猎聘猎头端（`h.liepin.com`），按关键词搜索候选人，自动翻页、提取简历卡片、进入详情页抓取完整信息。

配套 `anti_detect.py` 做反检测处理：随机延迟、模拟真人操作节奏，降低被识别为脚本的概率。

抓取结果会同时存成 `.csv` 和 `.json` 两份到 `data/`。

### 2. AI 评分（`analyzer.py` + `jd_analyzer.py`）

- `jd_analyzer.py` 先把岗位描述拆成结构化信息：行业方向、职业类别、硬性要求、加分项、搜索关键词；
- `analyzer.py` 再拿这份标准去逐份分析简历，给出分数和理由；
- 打分时会检索知识库做增强（即下面第 3 项）；
- `adaptive.py` 是个兜底：如果一轮下来高分候选人太少，会自动扩展关键词、换策略重跑。

分数分三档处理：**≥80 分推荐面试**，**60–80 分待定**，**<60 分淘汰**。推荐档的人会自动进入候选人管道。

### 3. 招聘知识库（`knowledge_engine.py`）

一个会自己积累的招聘知识系统。职责是：

- 从简历、岗位、行业资讯、面试反馈里采集知识；
- 结构化存到知识库（本地笔记 + SQLite 双写）；
- 评分时检索相关知识，让判断更有依据；
- 从历史反馈里发现规律（比如"这类背景的人后来都在面试挂了"），沉淀成经验。

### 4. 推荐报告（`report_engine.py` + `resume_parser.py`）

这是最近一次提交的重头戏，围绕四个原则设计：

- **零编造**——报告里每个字都来自「简历原文」或「评分阶段已经生成的内容」，报告引擎自己不再调 LLM 重写任何叙述，避免二次幻觉、也避免重复花钱；
- **可归因**——每个字段都记录来源和置信度，缺的一律留空并标「需人工补充」，绝不用全文兜底填充；
- **模板驱动**——字段清单写在 `config/report_templates/headhunter_v1.yaml` 里，加字段、改顺序、改标题都不用动 Python 代码；
- **校验先行**——生成后检查必填缺失和占位符残留，有问题明确提示，不静默放行。

模板里每个字段的 `source` 分三种，含义不同：`code`（代码直接取值，不经 LLM）、`resume`（简历原文直引，禁止改写）、`llm`（LLM 生成，但必须附原文证据，对不上就丢弃）。

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

Manifest V3 扩展，以**侧边栏**为主界面，直接贴在猎聘页面上使用。

- `content.js` 负责读取当前页面的内容；
- `sidepanel.js` 是主界面逻辑；
- `deepseek.js` 是直连 DeepSeek 的流式（SSE）客户端，评分时不经过后端，直接调用大模型接口。

需要说明的是：扩展既支持「走本地后端」，也支持「直连大模型」（在后端没启动时也能用）。

---

## 四、技术栈

- **后端**：Python 3.12、Flask 3.1、Flask-Login
- **数据库**：SQLite（`data/minglie.db`）
- **前端**：Jinja2 模板 + 原生 CSS
- **浏览器自动化**：Playwright、playwright-stealth
- **大模型调用**：OpenAI SDK / httpx，兼容 DeepSeek 等接口
- **文档处理**：python-docx、pdfplumber、PyMuPDF、reportlab、weasyprint
- **其他**：pandas、numpy、openpyxl

### 数据表

`users`、`jobs`、`scrape_tasks`、`score_tasks`、`settings`、
`interview_feedback`、`knowledge`、`knowledge_log`、`search_strategies`

---

## 五、快速开始

### 启动后端

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux: source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium      # 抓取依赖真实浏览器内核

python run.py                    # 默认 5000 端口，可传参改端口：python run.py 5001
```

启动后访问 <http://localhost:5000>，首次使用需要注册账号。

### 配置大模型

首次使用需要在「设置」页填写大模型 API Key（支持 DeepSeek 等兼容接口），
配置会保存到本地数据库。

> 提示：当前 Key 是明文存在数据库里的。更稳妥的做法是改用环境变量，
> 见第七节。

### 安装 Chrome 扩展

1. 打开 `chrome://extensions/`；
2. 打开右上角「开发者模式」；
3. 点「加载已解压的扩展程序」，选择本项目的 `chrome-extension/` 目录。

---

## 六、目前实际状态

如实说明各部分成熟度，便于判断哪里能直接用、哪里还在路上。

### 已经跑通的

- **抓取**：猎聘猎头端的搜索、翻页、详情抓取整体可用，反检测也做了；是目前代码量最大的模块。
- **评分**：JD 拆解 + 简历打分链路可用，支持知识库增强和低分自动重试。
- **网页工作台**：13 个页面都已完成（看板、抓取、评分、岗位、候选人、面试、报告、设置、日志、登录注册等）。
- **推荐报告**：模板驱动 + 零编造设计已经落地，可导出 Word。
- **Chrome 扩展**：侧边栏抓取与流式评分可用，且支持不依赖后端直连大模型。
- **Agent 闭环**：6 步流程与 12 个工具已实现。

### 还没完成 / 需要改进的

- **`app/browser_agent/` 是实验性骨架**。它按「观察 → 规划 → 执行」的循环搭了架子，但 `planner.py` 里的决策逻辑目前是**写死的**（只认「下一页」按钮），并没有真正接入大模型。功能上和已经成熟的 `scraper_liepin.py` 重复，属于早期探索遗留——**留用还是删除尚未决定**。
- **`app/server.py` 过于臃肿**。单文件约 89KB、集中了 71 个 API 路由（其中相当一部分是给 Chrome 扩展用的 `/api/extension/*`）。后续应按职责拆分。
- **桌面版未实现**。依赖清单里出现了 PyInstaller 和 PyQt6，说明当时有打包成桌面应用的打算，但目前没有落地。
- **测试覆盖薄弱**。`test_deepseek.js` 是扩展直连模块的验证脚本，`pytest` 已在依赖里但项目没有成体系的测试。
- **登录与权限较简单**，用户体系只做了基础注册/登录。

### 已知的坏味道

- `docs/superpowers/specs/` 是空目录；
- `data/` 里有大量成对出现的抓取快照（时间戳仅相差几秒、内容高度相似），怀疑存在重复抓取或双写，尚未排查。

---

## 七、数据与安全约定

**以下几类内容绝不进版本库**（已在 `.gitignore` 中排除）：

| 路径 | 内容 | 原因 |
| --- | --- | --- |
| `data/` | 数据库、抓取结果、简历 | 含**明文 API Key**、候选人真实简历、用户数据 |
| `browser_data/` | 浏览器用户资料 | 含**猎聘登录态 Cookie** |
| `.env` / `.env.*` | 环境变量 | 可能含密钥 |
| `__pycache__/`、`*.pyc` | Python 编译缓存 | 无意义产物 |

`data/` 目录本身保留了一个 `.gitkeep` 占位，保证克隆后目录存在。

### 关于 API Key 的建议

当前大模型 Key 以**明文**形式存在 `data/minglie.db` 的 `settings` 表里。这在本地开发够用，但有两个隐患：一是数据库文件一旦外泄，Key 就跟着泄露；二是备份数据库时容易连带 Key 一起复制出去。

建议改为从环境变量读取：

```bash
# .env（已在 .gitignore 中排除）
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

---

## 八、开发约定

- 中文注释与中文文档，代码内注释解释「为什么这么做」，而不只是「做了什么」；
- 涉及大模型输出的一律遵守**零编造**原则：凡是 LLM 生成的内容都必须附原文证据，证据对不上就丢弃；
- 解析类逻辑（简历、JD）采用**严格模式**：锚点没命中就留空，不用全文兜底填充；
- 交付物以本地可打开的文件为准（报告导出为 docx 等）。

---

_本项目为个人招聘业务提效工具，涉及第三方平台数据的抓取与处理，请遵守目标平台的服务条款，仅用于合规用途。_
