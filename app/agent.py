"""
Agent 核心 - 规划 + 执行循环
用户输入目标 → LLM 生成计划 → 逐步执行 → 返回结果
"""
import json
import httpx
import asyncio
from datetime import datetime
from typing import Optional
from .agent_tools import TOOLS, ToolExecutor


# ── System Prompt ──

SYSTEM_PROMPT = """你是明猎 AI 招聘助手。你的任务是帮助用户完成从招聘到入职的完整闭环。

## 你可以使用的工具

{tools_desc}

## 完整招聘流程（6步闭环）

当用户说要招人时，按以下流程引导：

### 第一步：获取 JD

如果用户只说了岗位名称（如“招一个半导体销售”），先问：
```
收到！请问你有详细的岗位描述（JD）吗？

1️⃣ 有 → 请直接贴给我
2️⃣ 没有 → 我来根据行业经验帮你生成
```

### 第二步：分析 JD + 创建岗位

1. 调用 analyze_jd 分析 JD
2. 调用 create_job 创建岗位
3. **必须在 reply 中完整展示分析结果**

展示格式：
```
📋 岗位名称

【硬需求】
- xxx
- xxx

【软需求】
- xxx

【加分项】
- xxx

【搜索关键词】xxx
【城市】xxx

以上需求是否准确？确认后我将开始爬取简历。
```

### 第三步：确认后爬取 + 评分

用户说“确认”“开始”等 → 执行 scrape_resumes + score_resumes

评分完成后，系统会自动：
- 将推荐候选人（≥80分）录入候选人管道
- 同步到 Obsidian 岗位笔记

展示结果：
```
✅ 爬取完成！
📄 爬取简历：X份
🟢 推荐面试（≥80分）：X人
🟡 待定（60-80分）：X人
🔴 已淘汰（<60分）：X人

候选人已进入管道，可以开始打电话了！
```

### 第四步：跟踪招聘进展

用户反馈进展时，调用 update_candidate_status 更新状态：
- “打了张三的电话” → stage: contacted
- “推荐给甲方了” → stage: submitted
- “甲方说不行” → stage: rejected, reject_stage: client_reject
- “进面试了” → stage: interview
- “面试过了/发offer了” → stage: offer
- “入职了” → stage: hired
- “面试没过” → stage: rejected, reject_stage: interview_reject

**淘汰时必须追问原因**：“淘汰原因是什么？我记录下来，以后评分会参考。”

### 第五步：同步到 Obsidian

每次状态变更后，自动调用 sync_pipeline_to_obsidian 同步到 Obsidian。
这样用户的 Obsidian 岗位笔记里就有完整的招聘过程记录。

### 第六步：学习闭环

淘汰原因会自动积累为 Agent 记忆，影响后续评分：
- 某岗位多次因“稳定性差”淘汰 → 后续评分会加重稳定性的权重
- 某岗位高分候选人特征被记录 → 类似简历会被优先推荐

## 重要：步骤间传递参数

使用 $stepN.result.key 格式引用上一步的结果：
- $step1.result.job_id → 第1步返回的 job_id
- $step2.result.keywords → 第2步返回的关键词

**注意：必须用 .result. 而不是 .data.**

## 输出格式

你必须返回严格的 JSON 格式（不要包含其他内容）：

```json
{{
    "thinking": "你的思考过程",
    "steps": [
        {{
            "tool": "工具名称",
            "params": {{"参数名": "参数值"}},
            "description": "这一步做什么"
        }}
    ],
    "reply": "给用户的回复",
    "summary": "执行计划概述"
}}
```

询问用户时，steps 为空数组：
```json
{{
    "thinking": "需要用户提供JD",
    "steps": [],
    "reply": "收到！请问你有详细的岗位描述（JD）吗？\n\n1️⃣ 有 → 请直接贴给我\n2️⃣ 没有 → 我来根据行业经验帮你生成",
    "summary": "等待用户提供JD"
}}
```"""


def build_tools_description() -> str:
    """构建工具描述文本"""
    lines = []
    for tool in TOOLS:
        params_desc = []
        for pname, pinfo in tool.get("parameters", {}).items():
            params_desc.append(f"    - {pname}: {pinfo.get('description', '')}")
        params_str = "\n".join(params_desc) if params_desc else "    （无参数）"

        lines.append(f"""### {tool['name']}
{tool['description']}
参数：
{params_str}
返回：{tool.get('returns', '无')}""")
    return "\n\n".join(lines)


async def plan_steps(
    goal: str,
    history: list[dict],
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    user_id: int = None,
) -> dict:
    """调用 LLM 生成执行计划"""
    tools_desc = build_tools_description()
    system = SYSTEM_PROMPT.format(tools_desc=tools_desc)

    # 加载记忆上下文
    memory_context = ""
    if user_id:
        try:
            from .agent_memory import get_relevant_memories
            memory_context = get_relevant_memories(user_id)
        except Exception as e:
            print(f"[Agent] 加载记忆失败: {e}")

    if memory_context:
        system += f"\n\n## 历史记忆（参考）\n\n{memory_context}"

    messages = [{"role": "system", "content": system}]

    # 添加历史对话
    for h in history[-5:]:  # 最近5轮
        messages.append(h)

    messages.append({"role": "user", "content": goal})

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 3000,
                },
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # 提取 JSON（多重容错）
            content = content.strip()
            
            # 方法1：直接解析
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                pass
            
            # 方法2：提取 ```json ... ``` 代码块
            if "```" in content:
                lines = content.split("\n")
                json_lines = []
                in_json = False
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("```") and not in_json:
                        in_json = True
                        continue
                    elif stripped == "```" and in_json:
                        break
                    elif in_json:
                        json_lines.append(line)
                if json_lines:
                    try:
                        return json.loads("\n".join(json_lines))
                    except json.JSONDecodeError:
                        pass
            
            # 方法3：找第一个 { 到最后一个 }
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(content[start:end+1])
                except json.JSONDecodeError:
                    pass
            
            # 方法4：全部失败，返回一个默认结构
            return {
                "thinking": "LLM 返回格式异常",
                "steps": [],
                "summary": content,
                "reply": content,
            }

    except Exception as e:
        print(f"[Agent] 规划失败: {e}")
        return {
            "thinking": f"规划失败: {str(e)}",
            "steps": [],
            "summary": "抱歉，我暂时无法处理这个请求。",
            "reply": f"处理出错了：{str(e)}",
        }


async def run_agent(
    user_id: int,
    goal: str,
    history: list[dict] = None,
) -> dict:
    """
    Agent 主入口
    返回: { "plan": {...}, "results": [...], "summary": "..." }
    """
    from .models import get_settings

    settings = get_settings(user_id)
    api_key = settings.get("llm_api_key", "")
    base_url = settings.get("llm_base_url", "https://api.deepseek.com")
    model = settings.get("llm_model", "deepseek-chat")

    if not api_key:
        return {"success": False, "error": "请先配置 LLM API Key"}

    history = history or []

    # ── Step 1: 规划（带记忆上下文）──
    plan = await plan_steps(goal, history, api_key, base_url, model, user_id=user_id)

    # 如果只是聊天，直接返回回复
    if plan.get("reply") and not plan.get("steps"):
        return {
            "success": True,
            "plan": plan,
            "results": [],
            "summary": plan["reply"],
            "is_chat": True,
        }

    steps = plan.get("steps", [])
    if not steps:
        return {
            "success": True,
            "plan": plan,
            "results": [],
            "summary": plan.get("summary", "没有需要执行的步骤"),
        }

    # ── Step 2: 逐步执行 ──
    executor = ToolExecutor(user_id)
    results = []
    context = {}  # 存储中间结果，供后续步骤使用

    for i, step in enumerate(steps):
        tool_name = step.get("tool", "")
        params = step.get("params", {})
        description = step.get("description", "")

        print(f"[Agent] 执行步骤 {i+1}/{len(steps)}: {tool_name} - {description}")

        # 替换参数中的变量引用（如 $step1.result.job_id）
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                try:
                    # 解析 $step1.result.job_id 这样的引用
                    parts = value[1:].split(".")
                    if len(parts) >= 2:
                        step_ref = parts[0]  # step1
                        step_idx = int(step_ref.replace("step", "")) - 1
                        if 0 <= step_idx < len(results):
                            # 获取结果数据
                            step_result = results[step_idx].get("result", {})
                            # 尝试直接从 result 中获取（跳过 .result. 层级）
                            remaining_parts = parts[1:]
                            # 如果第一个 key 是 "data" 或 "result"，跳过它
                            if remaining_parts and remaining_parts[0] in ("data", "result"):
                                remaining_parts = remaining_parts[1:]
                            # 逐层深入获取值
                            result_data = step_result
                            for part in remaining_parts:
                                if isinstance(result_data, dict):
                                    result_data = result_data.get(part, None)
                                else:
                                    result_data = None
                                    break
                            if result_data is not None:
                                params[key] = result_data
                                print(f"[Agent] 替换变量 {value} -> {result_data}")
                except Exception as e:
                    print(f"[Agent] 变量替换失败 {value}: {e}")

        # 执行工具
        result = await executor.execute(tool_name, params)
        results.append({
            "step": i + 1,
            "tool": tool_name,
            "description": description,
            "result": result,
        })

        # 存储到上下文
        context[f"step{i+1}"] = result

        # 如果失败且是关键步骤，停止执行
        if not result.get("success") and tool_name in ("create_job", "scrape_resumes"):
            results.append({
                "step": i + 1,
                "tool": "error",
                "description": f"步骤失败，停止执行: {result.get('error', '未知错误')}",
                "result": result,
            })
            break

    # ── Step 3: 生成总结 ──
    summary = await generate_summary(goal, plan, results, api_key, base_url, model)

    return {
        "success": True,
        "plan": plan,
        "results": results,
        "summary": summary,
    }


async def generate_summary(
    goal: str,
    plan: dict,
    results: list[dict],
    api_key: str,
    base_url: str,
    model: str,
) -> str:
    """让 LLM 根据执行结果生成总结"""
    results_text = json.dumps(results, ensure_ascii=False, indent=2)

    prompt = f"""用户目标：{goal}

执行计划：{plan.get('summary', '')}

执行结果：
{results_text}

请用简洁的中文总结执行结果，包括：
1. 完成了什么
2. 关键数据（如数量、分数等）
3. 如果有失败，说明原因和建议
4. 下一步建议（如果有）

直接返回总结文字，不要 JSON。"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是一个简洁的总结助手。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                },
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]

    except Exception as e:
        # 降级：手动拼接总结
        parts = [f"目标：{goal}"]
        for r in results:
            status = "✅" if r.get("result", {}).get("success") else "❌"
            parts.append(f"{status} {r.get('description', '')}")
        return "\n".join(parts)
