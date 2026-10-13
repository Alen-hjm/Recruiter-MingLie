# 明猎 · Recruiter MingLie

明猎是一个面向猎头和招聘顾问的桌面招聘助手。它把招聘入口收敛成一句话：你描述岗位、城市和目标数量，助手负责理解需求、搜寻候选人、分析匹配度，并整理出下一步可沟通的人选。

> 这是一个个人招聘提效工具。使用第三方平台数据时，请遵守平台服务条款、隐私要求和适用法律。

## 产品定位

明猎的主流程是：

```text
一句话需求 → 岗位理解 → 自动搜寻 → 匹配分析 → 推荐与跟进
```

例如：

```text
帮我找深圳的半导体销售总监，先收集 10 份简历，无其他要求。
```

客户只需要表达招聘目标。搜索翻页、评分口径、任务恢复等执行细节由系统内置；模型服务、平台登录和其他连接项放在“设置”中管理。

## 功能

- **招聘助手**：通过对话启动完整招聘流程，同一页面显示真实执行阶段、数量和推荐结果。
- **自动搜寻**：按岗位、关键词、城市和目标数量创建搜寻任务，支持逐条保存、去重和中断恢复。
- **匹配分析**：使用岗位要求分析简历，区分推荐、待核实和未分析结果；保留原始简历，避免把缺失信息当成结论。
- **候选人跟进**：记录待沟通、面试、通过、待定等招聘进展。
- **决策报告**：展示匹配摘要、推荐理由和可展开的依据，可导出报告文件。
- **多模型接入**：设置页支持 DeepSeek、OpenAI、通义千问、Moonshot、SiliconFlow，以及自定义 OpenAI 兼容接口。
- **桌面工作台**：概览、岗位、候选人、面试、报告和任务详情通过侧边导航访问；支持快捷导航和首次登录引导。
- **Chrome 扩展**：`chrome-extension/` 提供贴近猎聘页面的辅助入口，使用独立访问令牌连接本地后端。

## 界面与交互

前端使用 Jinja 模板、原生 CSS 和 JavaScript，不需要 React 构建链。助手首页是产品主入口，其他页面围绕招聘结果和下一步行动组织。

界面参考 Aceternity UI 的交互思路，用原生方式实现了 Aurora 背景、Tracing Beam、Spotlight、3D Card、Magnetic Button 和 Multi Step Loader 等局部效果：

- 招聘入口有流动渐变、需求到人才的连线和指针视差；
- 发送按钮、输入框和示例入口有即时反馈；
- 项目、人选和报告卡片支持轻微倾斜、指针高光和进入视口动画；
- 招聘步骤只在服务端阶段变化后点亮，不伪造百分比；
- 支持 `prefers-reduced-motion`，切换页面不会等待整页退场动画。

完整的前端设计和验收说明见 [docs/frontend-workspace.md](docs/frontend-workspace.md)。

## 项目结构

```text
app/
├─ web/                  页面路由与 API Blueprint
├─ services/             搜寻、评分等后台服务
├─ agent.py              对话式招聘 Agent
├─ agent_tools.py        Agent 可调用工具
├─ scraper_liepin.py     猎聘采集器
├─ scrapling_session.py  可选 Scrapling 会话适配器
├─ interaction_pacing.py 操作节奏控制
└─ models.py             SQLite 数据模型
templates/               Jinja 页面
static/css/              工作台、登录和动效样式
static/js/               工作台、助手和引导交互
chrome-extension/        Chrome MV3 扩展
config/report_templates/ 推荐报告模板
docs/                    采集、恢复和前端验收记录
tests/                   单元、Web 和流程测试
```

## 快速开始

需要 Python 3.12 或兼容版本。

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py
```

打开 <http://127.0.0.1:5000>，注册账号后进入工作台。

默认只监听本机回环地址。如果要在局域网使用，请明确设置 `MINGLIE_HOST`，同时配置持久化的 `MINGLIE_SECRET_KEY`，不要直接暴露候选人数据服务。

## 模型配置

可以在“设置 → 模型服务”中填写服务商、接口地址、模型名和密钥，也可以使用环境变量：

```powershell
$env:MINGLIE_LLM_API_KEY = "你的 API Key"
$env:MINGLIE_LLM_BASE_URL = "https://api.deepseek.com"
$env:MINGLIE_LLM_MODEL = "deepseek-chat"
python run.py
```

也可以复制 `.env.example` 作为配置参考。设置页保存的字段优先于环境变量，空字段回落到环境变量。密钥不会写入浏览器的 `sessionStorage`，保存后页面只显示掩码状态。

自定义服务需要兼容项目使用的 OpenAI Chat Completions 接口。没有模型配置时，仍可使用岗位、搜寻和候选人管理页面，但自动分析不可用。

## 猎聘搜寻

默认模式是独立 Playwright 浏览器：

```powershell
$env:MINGLIE_SCRAPE_MODE = "browser"
python run.py
```

首次搜寻时在采集浏览器中人工登录。遇到登录失效、空白页或平台验证，任务会进入“待人工处理”，已保存的结果会保留，确认恢复访问后可以继续任务。

项目也提供可选 Scrapling 会话适配器：

```powershell
python -m pip install -r requirements-scrapling.txt
python -m playwright install chromium
$env:MINGLIE_SCRAPE_MODE = "scrapling"
$env:MINGLIE_SCRAPLING_ENGINE = "stealth"
python run.py
```

Scrapling 的实站验收目前只覆盖有限的人工登录、搜索、两页翻页和单份详情读取。它不代表可以规避所有平台风控，也不代表大批量、长期运行或完整 JD 筛选已经验收。详细边界见 [Scrapling 验收记录](docs/scrapling-validation.md) 和 [中断恢复说明](docs/collection-recovery.md)。

## Chrome 扩展

1. 打开 `chrome://extensions/`，开启“开发者模式”；
2. 选择“加载已解压的扩展程序”，指定 `chrome-extension/`；
3. 在明猎“设置 → 扩展连接”生成访问令牌；
4. 将令牌填入扩展设置。

扩展访问令牌与模型 API Key 相互独立。令牌可以撤销，后端只保存摘要；生产环境建议设置 `MINGLIE_EXTENSION_ORIGINS`，限制允许访问的扩展来源。

## 本地预览

项目包含隔离的前端预览服务，使用虚构简历和本地模拟助手，不访问真实猎聘、不调用外部模型：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python data/ui-redesign/preview_server.py
```

打开 <http://127.0.0.1:5088/>，预览账号为：

```text
用户名：preview
密码：preview-local-2026
```

预览只用于检查页面、导航、动效和对话状态，不能代表真实采集结果。

## 测试

```powershell
python -m pytest -q
```

测试覆盖认证与用户隔离、岗位和候选人流程、任务恢复、评分链路、模型配置、扩展鉴权、架构依赖方向及本地模拟招聘流程。

如果只检查助手工作台：

```powershell
python -m pytest tests/test_assistant_workspace.py tests/test_onboarding_overview.py -q
node --check static/js/assistant.js
node --check static/js/workspace.js
node --check static/js/workspace-motion.js
```

## 数据与安全

以下内容只保存在本地运行目录，不应提交到 Git：

- `data/`：SQLite 数据库、简历和任务结果；
- `browser_data/`：浏览器登录态和 Cookie；
- `.env`：环境变量和模型密钥；
- `screenshots/`：采集截图和诊断文件。

默认数据目录和浏览器目录由 `.env.example` 中的 `MINGLIE_DATA_DIR`、`MINGLIE_BROWSER_DIR` 控制。请使用独立测试数据目录验证采集流程，不要把真实简历、Cookie 或密钥发到聊天、日志或版本库。

## 当前边界

- Agent 回合使用进程内线程，服务重启后不会恢复正在运行的对话回合；已落库的岗位、简历和任务仍然保留。
- 当前部署面向单机桌面使用，不是分布式任务队列。
- 城市和关键词用于缩小搜寻范围，不等于模型已经验证全部 JD 硬性条件。
- 报告中的匹配分是沟通参考，不是自动录用结论；招聘决定仍需人工核对。
- 第三方平台页面、登录状态和可访问范围可能变化，真实采集结果以当次任务状态为准。

## 许可证与使用

本项目当前作为个人招聘业务提效工具维护。使用前请确认目标平台授权范围、候选人隐私处理方式和所在地区的法律要求。

