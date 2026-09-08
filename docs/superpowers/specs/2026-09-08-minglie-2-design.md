# MingLie 2.0 设计

## 产品目标

MingLie 2.0 是一个从空 SQLite 数据库启动的本地 AI 招聘工作台。它接收候选人数据、保存岗位 JD、产生可解释的评分结果，并把候选人的沟通与漏斗反馈回流到岗位视图。

验收重点是端到端可运行和数据可追溯；旧项目、旧数据库和既有采集实现均不依赖也不修改。

## 范围

包含：本地 Flask 工作台、SQLite、版本化 API、候选人导入和去重、JD 结构化、评分任务、候选人漏斗、反馈、Chrome 扩展入口、文档与测试。

不包含：对第三方平台自动化策略的实现、绕过检测能力、迁移旧数据库、生产级分布式队列、多租户部署。

## 架构

采用模块化 Flask 单体。`factory.py` 创建应用并注册蓝图；`web` 只渲染页面，`api` 只处理 HTTP 契约，`services` 承担业务编排，`repositories` 负责 SQLite 存取，`domain` 定义枚举和校验，`integrations` 封装可替换 AI Provider。

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

`score_runs` 记录针对一个岗位的一次评分运行；`candidate_scores` 保存总分、推荐结论、匹配项、缺口、证据和评分状态。`pipeline_events` 追加保存候选人状态变化；`feedback` 保存猎头的接受/拒绝/沟通反馈。所有记录带创建时间和更新时间。

## 数据流

1. Web 页面或扩展创建/选择岗位。
2. 导入器调用 `POST /api/v1/ingestions`，提交 `source`、`external_id`、候选人字段及可选幂等键。
3. 服务校验字段，在事务中创建导入批次，按 `(source, external_id)` 创建或更新候选人及来源；逐项返回 `created`、`updated`、`duplicate` 或 `rejected`。
4. 用户调用 JD 分析，获得结构化要求。无 AI Provider 时使用确定性的关键词分析，保证可演示。
5. 用户创建评分运行。评分服务读取同一岗位下的候选人，生成分数、推荐、匹配、缺口和证据。无 AI Provider 时使用可预测的规则评分；已配置 Provider 时可由 AI 增强解释，但不得改变响应结构。
6. 用户更新候选人漏斗并提交反馈；事件与反馈立即持久化，并显示在岗位详情中。

## API 契约

- `GET /api/v1/health`：应用和数据库健康检查。
- `POST|GET /api/v1/jobs`、`GET|PATCH /api/v1/jobs/{id}`：岗位管理。
- `POST /api/v1/jobs/{id}/analyze-jd`：保存结构化 JD 要求。
- `POST /api/v1/ingestions`、`GET /api/v1/ingestions/{id}`：候选人批量接入和回执。
- `GET /api/v1/jobs/{id}/candidates`、`GET /api/v1/candidates/{id}`：候选人查询。
- `POST /api/v1/jobs/{id}/score-runs`、`GET /api/v1/score-runs/{id}`：评分运行与结果。
- `PATCH /api/v1/candidates/{id}/pipeline`、`POST /api/v1/candidates/{id}/feedback`：漏斗与反馈回流。

所有成功响应包裹为 `{ "data": ... }`，所有失败响应为 `{ "error": { "code", "message", "details" } }`。写入接口使用明确的 HTTP 状态码；验证失败逐字段返回原因。导入接口接受 `Idempotency-Key`，相同键重复提交返回初次处理结果。

## 页面与扩展

页面只有五个：仪表盘、岗位列表/详情、候选人列表/详情、导入记录、设置。岗位详情统一呈现 JD 要求、评分运行、候选人排序及漏斗摘要，避免跨页面丢失上下文。

Chrome 扩展是轻量接入端：配置本地服务地址，选择岗位，将当前页面中的标准化候选人数组提交到导入 API，并显示逐项回执。它不直连 SQLite，也不包含平台自动化策略。

## 配置、错误与任务

`.env` 允许配置端口、数据库路径、AI Provider URL、模型和 API Key。AI 配置缺失不是启动错误；服务使用规则模式并在响应中标记 `provider: "rules"`。

评分在本地同步执行，保证首版简单可运行。接口记录运行的 `queued/running/completed/failed` 状态；失败时保存安全的错误信息，不丢失已完成的候选人评分。Flask 全局错误处理器统一输出 API 错误格式。

## 测试与验收

测试覆盖：应用创建、数据库初始化、岗位创建、幂等导入/去重、JD 解析、规则评分、漏斗更新、反馈写入和 API 错误格式。`python -m pytest` 必须通过；`python run.py` 后 `/api/v1/health` 必须返回健康状态。README 提供从虚拟环境创建到扩展加载和演示导入的完整步骤。
