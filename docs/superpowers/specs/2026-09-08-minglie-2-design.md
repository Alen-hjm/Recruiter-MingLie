# MingLie 2.0 设计

## 产品目标

MingLie 2.0 是一个从空 SQLite 数据库启动的本地 AI 招聘工作台。猎头输入岗位 JD 后，工作台将自主编排候选人搜寻、数据接入、JD 解析、评分和排序，并把候选人的沟通与漏斗反馈回流到岗位视图。

验收重点是端到端可运行和数据可追溯；旧项目、旧数据库和既有采集实现均不依赖也不修改。

## 范围

包含：本地 Flask 工作台、SQLite、猎头 Agent、版本化 API、候选人导入和去重、JD 结构化、工具化搜寻任务、评分任务、排名表格、候选人漏斗、反馈、Chrome 扩展入口、文档与测试。

不包含：对第三方平台自动化策略的实现、绕过检测能力、迁移旧数据库、生产级分布式队列、多租户部署。

## 架构

采用模块化 Flask 单体。`factory.py` 创建应用并注册蓝图；`web` 只渲染页面，`api` 只处理 HTTP 契约，`services` 承担业务编排，`repositories` 负责 SQLite 存取，`domain` 定义枚举和校验，`integrations` 封装可替换 AI Provider 与搜寻工具，`agent` 负责任务规划和工具选择，`orchestration` 负责招聘工作流状态机。

运行时目录为 `instance/`，数据库为 `instance/minglie.db`。所有页面、扩展和导入器只能通过 `/api/v1` 读写业务数据；SQLite 是唯一事实来源，JSON 文件不作为模块通信手段。

## 目录

```text
MingLie2.0/
  app/
    api/ web/ domain/ repositories/ services/ integrations/
    templates/ static/
    config.py factory.py cli.py
  chrome-extension/
  instance/                 # 本地运行数据，忽略
  tests/
  run.py
  requirements.txt
  .env.example .gitignore README.md
```

## 数据模型

`users` 保存本地账号。`jobs` 保存岗位、原始 JD 与 JD 解析状态。`job_requirements` 保存硬条件、加分项、风险项和解析 JSON。

`ingestions` 是每次导入批次；`candidates` 是以来源与外部 ID 去重的主档；`candidate_sources` 保存具体来源和原始载荷摘要。一次候选人上传必须在同一事务内写入批次结果、候选人及来源记录。

`workflow_runs` 记录从 JD 发起的一次全流程招聘工作流；`workflow_steps` 记录每个工具调用的输入摘要、状态、输出摘要、错误和时间。`search_runs` 保存候选人搜寻任务的关键词、城市、来源、状态和接收数量。

`score_runs` 记录针对一个岗位的一次评分运行；`candidate_scores` 保存总分、推荐结论、匹配项、缺口、证据和评分状态。`pipeline_events` 追加保存候选人状态变化；`feedback` 保存猎头的接受/拒绝/沟通反馈。所有记录带创建时间和更新时间。

`agent_runs` 和 `agent_messages` 分别保存猎头 Agent 的一次任务执行及其可读执行摘要。Agent 不拥有数据库直写权限，只能通过受限工具调用创建岗位、分析 JD、启动搜寻、导入候选人、启动评分、读取排名和更新候选人流程状态。

## 数据流

1. 猎头创建岗位并输入 JD，点击“开始招聘工作流”。
2. 编排器创建 `workflow_run`，先调用 JD 分析工具，得到关键词、城市、经验、技能及硬条件。
3. 编排器使用这些要求调用已配置的候选人搜寻工具。工具只实现统一的 `search(criteria) -> candidates` 契约；平台适配器可以独立部署或由浏览器扩展接入。
4. 搜寻工具将标准化候选人调用 `POST /api/v1/ingestions` 接入。服务校验字段，在事务中创建导入批次，按 `(source, external_id)` 创建或更新候选人及来源；逐项返回 `created`、`updated`、`duplicate` 或 `rejected`。
5. 编排器对本工作流接入或更新的候选人调用评分工具，生成分数、推荐、匹配、缺口和证据。无 AI Provider 时使用可预测的规则评分；已配置 Provider 时可由 AI 增强解释，但不得改变响应结构。
6. 岗位详情呈现按最新总分降序排列的候选人表格，以及每人的匹配原因、缺口、来源和漏斗状态。
7. 猎头从候选人详情更新“待联系/已电话沟通/已邀约/面试中/淘汰”等状态并提交反馈；事件与反馈立即持久化，并显示在岗位详情中。

## 猎头 Agent

猎头 Agent 是工作流的自然语言入口，而不是绕过业务规则的第二套系统。用户可以输入“为这个 JD 寻找候选人并给出电话邀约优先级”；Agent 先生成简短计划，再依次调用允许的工具：`analyze_jd`、`search_candidates`、`ingest_candidates`、`score_candidates`、`rank_candidates` 与 `summarize_outreach`。

每次工具调用均写入工作流步骤和 Agent 运行记录。Agent 只能读取和调用经注册的工具；候选人导入、漏斗变更等写操作仍经过同一服务校验。没有配置可用搜寻工具时，Agent 明确报告“等待候选人接入”，并给出扩展需要执行的条件，不得编造候选人或评分结果。

Agent 最终回复包含：任务状态、接入候选人数、评分完成数、按匹配度排序的前若干候选人、每人的匹配理由/风险项，以及建议的电话沟通切入点。电话呼叫和邀约发送始终由猎头确认后执行。

## API 契约

- `GET /api/v1/health`：应用和数据库健康检查。
- `POST|GET /api/v1/jobs`、`GET|PATCH /api/v1/jobs/{id}`：岗位管理。
- `POST /api/v1/jobs/{id}/analyze-jd`：保存结构化 JD 要求。
- `POST /api/v1/jobs/{id}/workflow-runs`、`GET /api/v1/workflow-runs/{id}`：启动及查询 JD 驱动的全流程招聘任务。
- `POST /api/v1/agent-runs`、`GET /api/v1/agent-runs/{id}`：以自然语言创建及查询猎头 Agent 任务。
- `POST /api/v1/jobs/{id}/search-runs`、`GET /api/v1/search-runs/{id}`：独立启动及查询候选人搜寻任务。
- `POST /api/v1/ingestions`、`GET /api/v1/ingestions/{id}`：候选人批量接入和回执。
- `GET /api/v1/jobs/{id}/candidates`、`GET /api/v1/candidates/{id}`：候选人查询。
- `POST /api/v1/jobs/{id}/score-runs`、`GET /api/v1/score-runs/{id}`：评分运行与结果。
- `PATCH /api/v1/candidates/{id}/pipeline`、`POST /api/v1/candidates/{id}/feedback`：漏斗与反馈回流。

所有成功响应包裹为 `{ "data": ... }`，所有失败响应为 `{ "error": { "code", "message", "details" } }`。写入接口使用明确的 HTTP 状态码；验证失败逐字段返回原因。导入接口接受 `Idempotency-Key`，相同键重复提交返回初次处理结果。

## 页面与扩展

页面只有五个：仪表盘、岗位列表/详情、候选人列表/详情、导入/工作流记录、设置。仪表盘提供猎头 Agent 输入框；岗位详情提供“开始招聘工作流”操作，统一呈现 JD 要求、工作流步骤、评分运行、按分数排序的候选人表格、每人的匹配原因/缺口及漏斗摘要，避免跨页面丢失上下文。

Chrome 扩展是轻量接入端：配置本地服务地址，接收工作流生成的搜寻条件，选择岗位，将当前页面中的标准化候选人数组提交到导入 API，并显示逐项回执。它不直连 SQLite，也不包含平台自动化策略。

## 配置、错误与任务

`.env` 允许配置端口、数据库路径、AI Provider URL、模型和 API Key。AI 配置缺失不是启动错误；服务使用规则模式并在响应中标记 `provider: "rules"`。

招聘工作流在本地后台线程执行，保证点击页面不会阻塞，同时不引入外部队列。工作流和步骤均记录 `queued/running/waiting_input/completed/failed` 状态；搜寻工具未配置或需浏览器扩展继续时，工作流进入 `waiting_input` 而不是伪造结果。评分失败时保存安全的错误信息，不丢失已完成的候选人评分。Flask 全局错误处理器统一输出 API 错误格式。

## 测试与验收

测试覆盖：应用创建、数据库初始化、岗位创建、猎头 Agent 工具计划与受限调用、JD 驱动工作流、搜寻工具回执、幂等导入/去重、JD 解析、规则评分、排名、漏斗更新、反馈写入和 API 错误格式。`python -m pytest` 必须通过；`python run.py` 后 `/api/v1/health` 必须返回健康状态。README 提供从虚拟环境创建到扩展加载和完整演示工作流的步骤。
