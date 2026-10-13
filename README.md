# 明猎 · Recruiter MingLie

**从一句招聘需求开始，找到值得沟通的人。**

明猎是面向猎头和招聘顾问的 AI 招聘助手。输入岗位、城市和招聘目标后，系统可以协助完成岗位理解、候选人搜寻、简历匹配和推荐整理，并在候选人管道中继续跟进。

> 明猎用于辅助招聘判断，不替代人工核实或录用决定。使用第三方平台数据前，请确认已获得必要授权，并遵守目标平台的服务条款和适用法律。

[选择入口](#选择入口) · [快速启动](#快速启动) · [使用流程](#使用流程) · [模型接入](#模型接入) · [浏览器扩展](#浏览器扩展) · [安全与边界](#安全与边界)

## 选择入口

明猎由两个相互配合的入口组成。它们使用同一套工作台账号、岗位和执行进度，但承担的任务不同：

| 入口 | 适合做什么 | 是否必须安装 |
| --- | --- | --- |
| **招聘助手工作台** | 输入一句话需求、启动搜寻、查看候选人、查看匹配分析和招聘进展 | 必须 |
| **明猎 Chrome 扩展** | 在猎聘简历详情页旁边快速读取当前简历、提交分析，或继续使用招聘助手 | 可选 |

第一次使用时，先启动工作台并完成模型服务配置，再决定是否安装扩展。只想体验“说一句话找人”的主流程，只需要工作台；需要边浏览猎聘简历边分析时，再安装扩展。扩展不能脱离工作台单独运行，也不会替代工作台的模型配置。

### 推荐使用路径

1. 启动工作台并注册账号；
2. 在“设置 → 模型服务”接入一个模型服务；
3. 回到“招聘助手”，直接输入岗位、城市和目标数量；
4. 需要处理猎聘当前打开的单份简历时，再安装 `chrome-extension/` 中的插件并连接工作台。

仓库中的前端工作台代码位于 `app/web/`、`templates/` 和 `static/`；浏览器插件完整代码位于 `chrome-extension/`。插件目录有单独的安装说明，见 [`chrome-extension/README.md`](chrome-extension/README.md)。

## 能做什么

- 用自然语言描述招聘需求，不必先填写复杂的专业参数。
- 按岗位、关键词、城市和目标数量创建候选人搜寻任务。
- 对已获取的简历进行岗位匹配分析，展示推荐理由和待核实信息。
- 查看候选人、招聘项目、面试进展和决策报告。
- 在任务中断、登录失效或需要人工验证时保留已完成结果，并支持继续处理。
- 通过设置页接入 DeepSeek、OpenAI、通义千问、Moonshot、SiliconFlow 或其他 OpenAI 兼容服务。

## 使用流程

1. 登录工作台，在设置中配置模型服务；
2. 在招聘助手中输入一句话需求，例如：

   ```text
   帮我找深圳的半导体销售总监，先收集 10 份简历，无其他要求。
   ```

3. 助手提取岗位和搜寻条件，创建或继续对应招聘项目；
4. 首次搜寻时在采集浏览器中完成平台登录；
5. 查看搜寻结果、匹配依据和推荐人选，再进行人工核实与后续沟通。

搜索、翻页、任务恢复和评分执行参数由系统处理。需要调整模型或平台连接时，再进入设置页面。

## 快速启动

需要 Python 3.12 或兼容版本，以及 Chromium 浏览器运行环境。

```powershell
git clone https://github.com/Alen-hjm/Recruiter-MingLie.git
cd Recruiter-MingLie

python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium

python run.py
```

打开 <http://127.0.0.1:5000>，注册账号后使用。服务默认只监听本机地址，适合个人桌面使用。

### 配置模型

模型可以在“设置 → 模型服务”中配置，也可以通过环境变量提供默认值：

```powershell
$env:MINGLIE_LLM_API_KEY = "你的 API Key"
$env:MINGLIE_LLM_BASE_URL = "https://api.deepseek.com"
$env:MINGLIE_LLM_MODEL = "deepseek-chat"
python run.py
```

设置页保存的配置优先于环境变量。自定义服务需要兼容项目使用的 OpenAI Chat Completions 接口。没有模型配置时，仍可以管理岗位、搜寻任务和候选人，但无法执行自动匹配分析。

## 猎聘搜寻

默认使用独立浏览器会话执行搜寻。首次执行时，系统会打开采集浏览器，请在其中完成登录。

```powershell
# 默认模式
$env:MINGLIE_SCRAPE_MODE = "browser"
python run.py
```

项目也提供可选的 Scrapling 会话适配器：

```powershell
python -m pip install -r requirements-scrapling.txt
python -m playwright install chromium

$env:MINGLIE_SCRAPE_MODE = "scrapling"
$env:MINGLIE_SCRAPLING_ENGINE = "stealth"
python run.py
```

空白页、登录失效或平台验证会使任务进入待处理状态，已保存的结果会保留。Scrapling 目前只完成有限的人工登录、搜索、翻页和详情读取验证，不代表可以规避平台风控，也不代表长期批量采集已经验收。详见 [采集验收记录](docs/scrapling-validation.md) 和 [任务恢复说明](docs/collection-recovery.md)。

## 浏览器扩展

项目包含一个 Chrome MV3 扩展，可在猎聘页面旁打开招聘助手：

1. 打开 `chrome://extensions/`；
2. 开启“开发者模式”；
3. 选择“加载已解压的扩展程序”，指定 `chrome-extension/` 目录；
4. 在工作台“设置 → 扩展连接”生成访问令牌，并填入扩展。

点击扩展图标打开侧边栏，在助手中直接描述招聘需求。扩展与工作台共享对话和执行进度；浏览猎聘简历详情时，也可选定岗位后点击“分析当前简历”。候选人支持搜索、筛选和加入跟进。

扩展访问令牌与模型 API Key 相互独立。令牌可撤销，后端不保存令牌明文。模型统一在工作台配置。安装、更新、连接排查和权限说明见 [浏览器扩展指南](docs/browser-extension.md)。

## 本地预览

仓库提供隔离预览环境，用于查看工作台、动效和助手流程。预览使用虚构简历和本地模拟助手，不访问真实猎聘或外部模型。

```powershell
$env:PYTHONIOENCODING = "utf-8"
python data/ui-redesign/preview_server.py
```

打开 <http://127.0.0.1:5088/>：

```text
用户名：preview
密码：preview-local-2026
```

预览结果不代表真实采集或模型分析结果。

## 开发与测试

运行完整测试：

```powershell
python -m pytest -q
```

只检查招聘助手工作台：

```powershell
python -m pytest tests/test_assistant_workspace.py tests/test_onboarding_overview.py -q
node --check static/js/assistant.js
node --check static/js/workspace.js
node --check static/js/workspace-motion.js
```

前端使用 Jinja 模板、原生 CSS 和 JavaScript，不需要 React 或前端构建步骤。后端入口为 `run.py`，业务页面和接口位于 `app/web/`，运行数据默认保存在本地 SQLite 和浏览器用户目录中。

## 安全与边界

- `data/`、`browser_data/`、`.env` 和截图目录可能包含简历、登录态或密钥，不应提交到 Git。
- 默认只监听 `127.0.0.1`。对外提供服务前，应配置会话密钥、HTTPS、访问控制和数据备份。
- 启用远程模型后，相关简历文本会发送到所配置的模型服务商，请先确认授权和数据保留政策。
- 城市和关键词用于缩小搜寻范围，不等于系统已经验证 JD 的全部硬性条件。
- 匹配分和推荐理由是辅助信息，最终招聘判断需要人工核对。
- 当前 Agent 回合使用单进程后台线程，服务重启不会恢复正在执行的对话回合；已经保存到数据库的岗位、简历和任务仍然保留。

## 文档

- [前端工作台说明](docs/frontend-workspace.md)
- [任务恢复说明](docs/collection-recovery.md)
- [Scrapling 采集验收](docs/scrapling-validation.md)
- [猎聘空白页调查](docs/liepin-blank-investigation.md)

## 许可与使用

本项目当前作为个人招聘业务提效工具维护。仓库未附带开源许可证；如需在商业环境或团队内分发，请先确认代码、第三方依赖、平台数据和候选人信息的使用权限。
