# MingLie 2.0

一个本地运行的猎头 Agent 工作台：输入 JD，Agent 依次调用 JD 分析、候选人搜寻/接入、评分和排序工具，最后呈现带匹配原因、缺口和电话沟通优先级的候选人表。

## 数据流

```text
JD → 猎头 Agent / 工作流 → 搜寻工具或浏览器扩展
   → 幂等接入与去重 → JD 规则评分 → 候选人排名
   → 电话沟通状态与反馈回流
```

SQLite 是唯一事实来源。每一次导入、评分和工作流步骤都可查询；旧 WorkSpace 项目及其数据库不会被读取或修改。

## 快速启动

要求：Python 3.11+。

```powershell
cd D:\extension\WorkSpace\MingLie2.0
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.cli check
python run.py
```

打开 [http://127.0.0.1:5050](http://127.0.0.1:5050)。首次启动会创建 `instance/minglie.db`。

## 两种候选人接入模式

- 默认 `MINGLIE_SEARCH_PROVIDER=manual`：Agent 分析 JD 后进入“等待候选人接入”。浏览器扩展或授权连接器提交候选人后，在岗位页点击评分即可获得排名。这是接入真实候选人的模式。
- `MINGLIE_SEARCH_PROVIDER=demo`：仅用于验证端到端流程，会创建明确写有“演示数据”的本地样例候选人。修改 `.env` 后重启服务。

工作台不包含第三方平台的自动化或绕过策略。任何实际数据接入端只需调用 `POST /api/v1/ingestions`。

## Chrome 扩展

在 Chrome 的“扩展程序 → 开发者模式 → 加载已解压的扩展程序”中选择 `chrome-extension/`。弹窗中填入服务地址、岗位 ID 和标准化候选人 JSON 数组：

```json
[{"external_id":"source-stable-id","name":"候选人姓名","headline":"当前职位","location":"上海","experience_years":6,"education":"本科","skills":["Python","SQL"],"summary":"经历摘要"}]
```

相同岗位、来源和 `external_id` 会去重；重复网络提交使用 `Idempotency-Key` 安全重试，服务端会逐项返回新增、更新、重复或拒绝结果。

## 验证

```powershell
python -m pytest
python -m app.cli check
```

测试覆盖数据库初始化、导入幂等与去重、JD 解析、规则评分、按匹配度排名、漏斗/反馈回流，以及 Agent 工具工作流审计。
