"""
Agent 工具注册表 - 定义 Agent 可用的所有工具

重构说明：
  原先 _tool_scrape_resumes / _tool_score_resumes 直接从 server 导入后台任务函数
  （`from .server import _run_scrape_thread`），导致「工具层 → HTTP 服务」的反向依赖，
  单独 import 本模块会把整个 Flask 应用连带拉起。现在改为依赖 services 层，
  依赖方向恢复为单向：agent_tools → services → models/scraper/analyzer。
"""
import json
import time
from datetime import datetime
from pathlib import Path

from . import config


# ── 工具定义 ──

TOOLS = [
    {
        "name": "analyze_jd",
        "description": "分析岗位描述（JD），提取行业方向、职业类型、硬性要求、搜索关键词等结构化信息",
        "parameters": {
            "job_name": {"type": "string", "description": "岗位名称"},
            "job_description": {"type": "string", "description": "岗位描述全文"},
        },
        "returns": "行业、职业类型、关键词列表、硬性要求、评分重点",
    },
    {
        "name": "create_job",
        "description": "在系统中创建一个新的招聘岗位",
        "parameters": {
            "name": {"type": "string", "description": "岗位名称"},
            "keyword": {"type": "string", "description": "搜索关键词，逗号分隔"},
            "job_description": {"type": "string", "description": "岗位描述"},
        },
        "returns": "岗位ID",
    },
    {
        "name": "scrape_resumes",
        "description": "从猎聘网爬取简历，需要先有岗位",
        "parameters": {
            "job_id": {"type": "integer", "description": "岗位ID"},
            "keyword": {"type": "string", "description": "搜索关键词"},
            "city": {"type": "string", "description": "城市，如用户未指定则留空"},
            "max_pages": {"type": "integer", "description": "爬取页数，1-5，默认3"},
        },
        "returns": "爬取的简历数量",
    },
    {
        "name": "score_resumes",
        "description": "对岗位下的简历进行AI评分，评分后自动分流（<60删除，60-80待定，>=80推荐）",
        "parameters": {
            "job_id": {"type": "integer", "description": "岗位ID"},
        },
        "returns": "评分结果摘要（推荐/待定/淘汰数量）",
    },
    {
        "name": "get_candidates",
        "description": "获取岗位下的候选人列表，按评分分类",
        "parameters": {
            "job_id": {"type": "integer", "description": "岗位ID"},
        },
        "returns": "分类后的候选人列表（推荐约面/待定/已淘汰）",
    },
    {
        "name": "search_knowledge",
        "description": "搜索Obsidian知识库中的相关笔记",
        "parameters": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "returns": "相关笔记内容",
    },
    {
        "name": "get_job_list",
        "description": "获取当前所有岗位列表",
        "parameters": {},
        "returns": "岗位列表",
    },
    {
        "name": "full_recruit",
        "description": "一键完成完整招聘流程：分析JD → 创建岗位 → 爬取简历 → AI评分 → 返回推荐候选人。适用于用户给出明确招聘目标时。",
        "parameters": {
            "job_name": {"type": "string", "description": "岗位名称"},
            "job_description": {"type": "string", "description": "岗位描述，如果没有则留空"},
            "city": {"type": "string", "description": "城市，默认上海"},
            "max_pages": {"type": "integer", "description": "爬取页数，1-5，默认3"},
        },
        "returns": "招聘结果摘要（推荐候选人列表）",
    },
    {
        "name": "save_job_to_obsidian",
        "description": "将岗位需求保存到 Obsidian 知识库，包含硬需求、软需求、关键词等",
        "parameters": {
            "job_name": {"type": "string", "description": "岗位名称"},
            "job_description": {"type": "string", "description": "岗位描述"},
            "hard_requirements": {"type": "string", "description": "硬需求，用换行分隔"},
            "soft_requirements": {"type": "string", "description": "软需求，用换行分隔"},
            "keywords": {"type": "string", "description": "搜索关键词，逗号分隔"},
        },
        "returns": "是否保存成功",
    },
    {
        "name": "update_candidate_status",
        "description": "更新候选人的招聘管道状态。用于用户反馈招聘进展时调用。",
        "parameters": {
            "job_id": {"type": "integer", "description": "岗位ID"},
            "candidate_name": {"type": "string", "description": "候选人姓名"},
            "stage": {"type": "string", "description": "新状态：contacted(已联系)/submitted(推荐给甲方)/interview(进入面试)/offer(发offer)/hired(录用)/rejected(淘汰)"},
            "reason": {"type": "string", "description": "原因或备注，淘汰时必填"},
            "reject_stage": {"type": "string", "description": "淘汰阶段：phone_screen/client_reject/interview_reject/offer_declined"},
            "company_rejected": {"type": "string", "description": "淘汰候选人的甲方公司名"},
        },
        "returns": "更新是否成功",
    },
    {
        "name": "get_pipeline",
        "description": "获取岗位的候选人管道状态，查看各阶段人数和详情",
        "parameters": {
            "job_id": {"type": "integer", "description": "岗位ID"},
        },
        "returns": "各阶段候选人列表和统计",
    },
    {
        "name": "sync_pipeline_to_obsidian",
        "description": "将当前候选人管道状态同步到 Obsidian 岗位笔记，形成招聘过程记录",
        "parameters": {
            "job_id": {"type": "integer", "description": "岗位ID"},
        },
        "returns": "同步是否成功",
    },
]


# ── 工具执行器 ──

class ToolExecutor:
    """工具执行器，封装实际的业务逻辑调用"""

    def __init__(self, user_id: int):
        self.user_id = user_id

    async def execute(self, tool_name: str, params: dict) -> dict:
        """执行指定工具，返回结果"""
        method = getattr(self, f"_tool_{tool_name}", None)
        if not method:
            return {"success": False, "error": f"未知工具: {tool_name}"}
        try:
            return await method(params)
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _tool_analyze_jd(self, params: dict) -> dict:
        """分析 JD"""
        from .jd_analyzer import analyze_jd
        from .models import get_settings

        llm = config.resolve_llm_config(get_settings(self.user_id))

        result = await analyze_jd(
            job_name=params.get("job_name", ""),
            job_description=params.get("job_description", ""),
            api_key=llm["api_key"],
            base_url=llm["base_url"],
            model=llm["model"],
            use_llm=bool(llm["api_key"]),
        )
        return {"success": True, "data": result}

    async def _tool_create_job(self, params: dict) -> dict:
        """创建岗位"""
        from .models import create_job

        job_name = params.get("name", "")
        keyword = params.get("keyword", "")
        job_description = params.get("job_description", "")

        job_id = create_job(
            user_id=self.user_id,
            name=job_name,
            keyword=keyword,
            job_description=job_description,
        )

        # 自动同步到 Obsidian
        obsidian_result = await self._tool_save_job_to_obsidian({
            "job_name": job_name,
            "job_description": job_description,
            "hard_requirements": "",
            "soft_requirements": "",
            "keywords": keyword,
        })

        return {"success": True, "job_id": job_id, "obsidian_synced": obsidian_result.get("success", False)}

    async def _tool_scrape_resumes(self, params: dict) -> dict:
        """爬取简历"""
        from .models import create_scrape_task, get_job, get_settings
        from .services.scraping import build_scrape_config, start_scrape_task

        job_id = params.get("job_id")
        if not job_id:
            return {"success": False, "error": "需要 job_id"}

        job = get_job(int(job_id))
        if not job:
            return {"success": False, "error": "岗位不存在"}

        keyword = params.get("keyword") or job.get("keyword", "")
        city = params.get("city", "") or ""
        max_pages = min(int(params.get("max_pages", 3)), config.SCRAPE_MAX_PAGES_LIMIT)

        # 从 JD 中提取快速筛选规则（学历/年限/年龄）
        quick_filter = {}
        try:
            from .jd_analyzer import extract_filter_rules_from_jd
            quick_filter = extract_filter_rules_from_jd({}, job.get("job_description", ""))
            if quick_filter:
                print("[快速筛选] 从 JD 提取规则: %s" % quick_filter)
        except Exception:
            pass

        scrape_config = build_scrape_config(
            get_settings(self.user_id), keyword, city, max_pages,
            search_mode="auto", user_id=self.user_id,
        )
        if quick_filter:
            scrape_config["quick_filter"] = quick_filter

        task_id = create_scrape_task(
            self.user_id, keyword, city, "liepin", max_pages, job_id=int(job_id)
        )
        start_scrape_task(task_id, scrape_config, user_id=self.user_id, job_id=int(job_id))

        # 轮询等待完成（最多 2 分钟）
        from .models import get_scrape_task
        for _ in range(120):
            time.sleep(1)
            task = get_scrape_task(task_id)
            if task and task["status"] in ("done", "error"):
                return {
                    "success": task["status"] == "done",
                    "task_id": task_id,
                    "result_count": task.get("result_count", 0),
                    "error": task.get("error_msg"),
                }

        return {"success": True, "task_id": task_id, "message": "爬取任务已提交，正在后台运行"}

    async def _tool_score_resumes(self, params: dict) -> dict:
        """评分简历"""
        from .models import get_job, get_score_task, get_unscored_resumes, get_settings
        from .services.scoring import start_score_task

        job_id = params.get("job_id")
        if not job_id:
            return {"success": False, "error": "需要 job_id"}

        job = get_job(int(job_id))
        if not job:
            return {"success": False, "error": "岗位不存在"}

        job_description = job.get("job_description", "")
        if not job_description:
            return {"success": False, "error": "岗位缺少描述"}

        resumes, already_scored = get_unscored_resumes(self.user_id, int(job_id))
        if not resumes:
            return {"success": True, "message": "无新简历需要评分（已有 %s 份已评分）" % already_scored}

        if not config.resolve_llm_config(get_settings(self.user_id))["api_key"]:
            return {"success": False, "error": "未配置 LLM API Key"}

        task_id, _ = start_score_task(
            self.user_id, int(job_id), job["name"], job_description, resumes, "100"
        )

        # 轮询等待完成（最多 5 分钟）
        for _ in range(300):
            time.sleep(1)
            task = get_score_task(task_id)
            if task and task["status"] in ("done", "error"):
                scored = json.loads(task.get("scored_json") or "[]")
                return {
                    "success": task["status"] == "done",
                    "task_id": task_id,
                    "total": len(scored),
                    "passed": task.get("passed_count", 0),
                    "error": task.get("error_msg"),
                }

        return {"success": True, "task_id": task_id, "message": "评分任务已提交，正在后台运行"}

    async def _tool_get_candidates(self, params: dict) -> dict:
        """获取候选人列表"""
        from .models import get_job, get_resumes_by_job, get_db
        import json

        job_id = params.get("job_id")
        if not job_id:
            return {"success": False, "error": "需要 job_id"}

        job = get_job(int(job_id))
        if not job:
            return {"success": False, "error": "岗位不存在"}

        resumes = get_resumes_by_job(self.user_id, int(job_id))

        # 获取评分数据
        conn = get_db()
        try:
            score_row = conn.execute(
                """SELECT scored_json FROM score_tasks
                   WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
                   AND job_name = (SELECT name FROM jobs WHERE id = ?)
                   ORDER BY created_at DESC LIMIT 1""",
                (self.user_id, int(job_id)),
            ).fetchone()
        finally:
            conn.close()

        score_map = {}
        if score_row and score_row["scored_json"]:
            for sr in json.loads(score_row["scored_json"]):
                key = sr.get("detail_url", "") or (sr.get("name", "") + sr.get("title", ""))
                if key:
                    score_map[key] = sr

        # 分类
        result = {"recommended": [], "pending": [], "rejected": []}
        for resume in resumes:
            key = resume.get("detail_url", "") or (resume.get("name", "") + resume.get("title", ""))
            if key in score_map:
                sr = score_map[key]
                if "score100" in sr:
                    resume["score100"] = sr["score100"]

            score = 0
            if resume.get("score100"):
                s = resume["score100"]
                if isinstance(s, dict):
                    score = s.get("total_score", 0) or s.get("score", 0) or 0
                elif isinstance(s, (int, float)):
                    score = s

            resume["_score"] = round(score)
            resume.pop("score100", None)
            resume.pop("analysis", None)

            if score >= 80:
                result["recommended"].append(resume)
            elif score >= 60:
                result["pending"].append(resume)
            else:
                result["rejected"].append(resume)

        return {
            "success": True,
            "job_name": job["name"],
            "recommended_count": len(result["recommended"]),
            "pending_count": len(result["pending"]),
            "rejected_count": len(result["rejected"]),
            "recommended": result["recommended"][:10],  # 只返回前10个
        }

    async def _tool_search_knowledge(self, params: dict) -> dict:
        """搜索知识库"""
        from .models import get_settings
        from .knowledge import scan_knowledge_base, find_relevant_notes

        settings = get_settings(self.user_id)
        kb_path = settings.get("obsidian_kb_path", "")

        if not kb_path:
            return {"success": False, "error": "未配置知识库路径"}

        notes = scan_knowledge_base(kb_path)
        if not notes:
            return {"success": True, "data": [], "message": "知识库为空"}

        query = params.get("query", "")
        relevant = find_relevant_notes(notes, query)

        return {
            "success": True,
            "data": [{"filename": n["filename"], "summary": n["body"][:500]} for n in relevant],
        }

    async def _tool_get_job_list(self, params: dict) -> dict:
        """获取岗位列表"""
        from .models import get_jobs

        jobs = get_jobs(self.user_id)
        return {
            "success": True,
            "data": [{"id": j["id"], "name": j["name"], "keyword": j.get("keyword", "")} for j in jobs],
        }

    async def _tool_full_recruit(self, params: dict) -> dict:
        """一键招聘：分析JD → 创建岗位 → 爬取简历 → 评分 → 返回推荐"""
        job_name = params.get("job_name", "")
        job_description = params.get("job_description", "")
        city = params.get("city", "") or ""
        max_pages = min(int(params.get("max_pages", 3)), 5)

        results = {}

        # Step 1: 分析 JD
        print(f"[一键招聘] Step 1: 分析 JD - {job_name}")
        jd_result = await self._tool_analyze_jd({"job_name": job_name, "job_description": job_description})
        if jd_result.get("success"):
            jd_data = jd_result["data"]
            keywords = jd_data.get("keywords", [])
            keyword_str = ", ".join(keywords) if keywords else job_name
            if not job_description and jd_data.get("hard_requirements"):
                job_description = "\n".join(jd_data.get("hard_requirements", []))
        else:
            keyword_str = job_name

        # Step 2: 创建岗位
        print(f"[一键招聘] Step 2: 创建岗位")
        create_result = await self._tool_create_job({
            "name": job_name,
            "keyword": keyword_str,
            "job_description": job_description or f"招聘{job_name}，工作地点{city}",
        })
        if not create_result.get("success"):
            return {"success": False, "error": f"创建岗位失败: {create_result.get('error')}"}

        job_id = create_result["job_id"]
        results["job_id"] = job_id
        print(f"[一键招聘] 岗位已创建，ID: {job_id}")

        # Step 3: 爬取简历
        print(f"[一键招聘] Step 3: 爬取简历")
        scrape_result = await self._tool_scrape_resumes({
            "job_id": job_id,
            "keyword": keyword_str,
            "city": city,
            "max_pages": max_pages,
        })
        results["scrape"] = scrape_result

        if not scrape_result.get("success"):
            return {
                "success": True,
                "data": results,
                "message": f"岗位已创建（ID:{job_id}），但爬取失败: {scrape_result.get('error', '未知错误')}"
            }

        resume_count = scrape_result.get("result_count", 0)
        print(f"[一键招聘] 爬取完成，共 {resume_count} 份简历")

        # Step 3.5: 自动保存到 Obsidian
        print(f"[一键招聘] 保存岗位到 Obsidian")
        obsidian_result = await self._tool_save_job_to_obsidian({
            "job_name": job_name,
            "job_description": job_description,
            "hard_requirements": jd_result.get("data", {}).get("hard_requirements", []) if jd_result.get("success") else [],
            "soft_requirements": jd_result.get("data", {}).get("soft_requirements", []) if jd_result.get("success") else [],
            "keywords": keyword_str,
        })
        results["obsidian"] = obsidian_result

        if resume_count == 0:
            return {
                "success": True,
                "data": results,
                "message": f"岗位已创建（ID:{job_id}），但未爬取到简历，请尝试更换关键词"
            }

        # Step 4: AI 评分
        print(f"[一键招聘] Step 4: AI 评分")
        score_result = await self._tool_score_resumes({"job_id": job_id})
        results["score"] = score_result

        if not score_result.get("success"):
            return {
                "success": True,
                "data": results,
                "message": f"爬取完成（{resume_count}份），但评分失败: {score_result.get('error', '未知错误')}"
            }

        # Step 5: 获取推荐候选人
        print(f"[一键招聘] Step 5: 获取推荐候选人")
        candidates_result = await self._tool_get_candidates({"job_id": job_id})
        results["candidates"] = candidates_result

        # Step 5.5: 自动创建候选人管道记录
        print(f"[一键招聘] Step 5.5: 创建候选人管道记录")
        from .models import create_candidate_from_score
        all_resumes = candidates_result.get("recommended", []) + candidates_result.get("pending", [])
        for r in all_resumes:
            create_candidate_from_score(self.user_id, job_id, r)

        # 同步管道到 Obsidian
        print(f"[一键招聘] 同步管道到 Obsidian")
        try:
            from .models import get_candidates_by_stage, get_pipeline_summary, get_settings
            stages = get_candidates_by_stage(self.user_id, job_id)
            summary_data = get_pipeline_summary(self.user_id, job_id)
            job_folder = (get_settings(self.user_id) or {}).get("obsidian_job_path", "")
            if job_folder:
                from .knowledge import sync_pipeline_to_obsidian
                sync_pipeline_to_obsidian(job_folder, job_name, stages, summary_data)
                results["obsidian_pipeline"] = {"success": True}
        except Exception as e:
            print(f"[一键招聘] 管道同步失败: {e}")

        # 构建摘要
        recommended = candidates_result.get("recommended_count", 0)
        pending = candidates_result.get("pending_count", 0)
        rejected = candidates_result.get("rejected_count", 0)

        summary = "✅ 招聘完成！\n"
        summary += f"📋 岗位：{job_name}（ID:{job_id}）\n"
        summary += f"📄 爬取简历：{resume_count}份\n"
        summary += f"🟢 推荐面试（≥80分）：{recommended}人\n"
        summary += f"🟡 待定（60-80分）：{pending}人\n"
        summary += f"🔴 已淘汰（<60分）：{rejected}人\n"
        summary += f"\n📝 候选人已进入管道，状态变更会自动同步到 Obsidian。"
        summary += f"\n📞 可以开始打电话了！用 update_candidate_status 更新进展。"

        if candidates_result.get("recommended"):
            summary += "\n\n🏆 Top 推荐候选人：\n"
            for i, r in enumerate(candidates_result["recommended"][:5], 1):
                score = r.get("_score", 0)
                summary += f"  {i}. {r.get('name', '未知')} - {score}分 - {r.get('title', '')} - {r.get('company', '')}\n"

        return {
            "success": True,
            "data": results,
            "message": summary,
        }

    async def _tool_save_job_to_obsidian(self, params: dict) -> dict:
        """将岗位需求保存到 Obsidian 知识库"""
        from .models import get_settings
        from pathlib import Path

        job_name = params.get("job_name", "")
        job_description = params.get("job_description", "")
        hard_requirements = params.get("hard_requirements", "")
        soft_requirements = params.get("soft_requirements", "")
        keywords = params.get("keywords", "")

        if not job_name:
            return {"success": False, "error": "岗位名称不能为空"}

        settings = get_settings(self.user_id)
        job_folder = settings.get("obsidian_job_path", "")

        if not job_folder:
            print("[Obsidian] 未配置岗位文件夹路径")
            return {"success": False, "error": "未配置 Obsidian 岗位文件夹路径"}

        # 规范化路径
        job_folder = job_folder.replace('\\', '/')

        # 构建笔记内容
        content = f"# {job_name}\n\n"
        if job_description:
            content += f"## 岗位描述\n\n{job_description}\n\n"
        if hard_requirements:
            if isinstance(hard_requirements, list):
                hard_requirements = "\n".join(f"- {r}" for r in hard_requirements)
            content += f"## 硬需求\n\n{hard_requirements}\n\n"
        if soft_requirements:
            if isinstance(soft_requirements, list):
                soft_requirements = "\n".join(f"- {r}" for r in soft_requirements)
            content += f"## 软需求\n\n{soft_requirements}\n\n"
        if keywords:
            content += f"## 搜索关键词\n\n{keywords}\n\n"
        content += f"---\n\n*创建时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

        # 写入 Obsidian
        folder = Path(job_folder)
        if not folder.exists():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                print(f"[Obsidian] 创建目录: {folder}")
            except Exception as e:
                print(f"[Obsidian] 创建目录失败: {e}")
                return {"success": False, "error": f"创建目录失败: {e}"}

        target_file = folder / f"{job_name}.md"
        try:
            target_file.write_text(content, encoding="utf-8")
            print(f"[Obsidian] 已保存岗位笔记: {target_file}")
            return {"success": True, "path": str(target_file)}
        except Exception as e:
            print(f"[Obsidian] 写入失败: {e}")
            return {"success": False, "error": f"写入失败: {e}"}

    async def _tool_update_candidate_status(self, params: dict) -> dict:
        """更新候选人管道状态"""
        from .models import get_interview_feedbacks, update_candidate_pipeline, get_settings

        job_id = params.get("job_id")
        candidate_name = params.get("candidate_name", "")
        stage = params.get("stage", "")
        reason = params.get("reason", "")
        reject_stage = params.get("reject_stage", "")
        company_rejected = params.get("company_rejected", "")

        if not job_id or not candidate_name or not stage:
            return {"success": False, "error": "缺少 job_id、candidate_name 或 stage"}

        # 查找候选人
        feedbacks = get_interview_feedbacks(self.user_id, int(job_id))
        target = None
        for f in feedbacks:
            if candidate_name in (f.get("candidate_name", ""), ""):
                target = f
                break
        if not target:
            return {"success": False, "error": f"未找到候选人: {candidate_name}"}

        ok = update_candidate_pipeline(
            target["id"], stage, reason=reason,
            reject_stage=reject_stage, company_rejected=company_rejected,
        )

        if ok:
            # 自动同步到 Obsidian
            settings = get_settings(self.user_id)
            job_folder = settings.get("obsidian_job_path", "")
            if job_folder:
                try:
                    from .knowledge import sync_pipeline_to_obsidian
                    from .models import get_candidates_by_stage, get_pipeline_summary, get_job
                    job = get_job(int(job_id))
                    if job:
                        stages = get_candidates_by_stage(self.user_id, int(job_id))
                        summary = get_pipeline_summary(self.user_id, int(job_id))
                        sync_pipeline_to_obsidian(job_folder, job["name"], stages, summary)
                except Exception as e:
                    print(f"[管道] Obsidian 同步失败: {e}")

            return {"success": True, "candidate": candidate_name, "stage": stage}
        return {"success": False, "error": "更新失败"}

    async def _tool_get_pipeline(self, params: dict) -> dict:
        """获取候选人管道状态"""
        from .models import get_candidates_by_stage, get_pipeline_summary, get_job

        job_id = params.get("job_id")
        if not job_id:
            return {"success": False, "error": "需要 job_id"}

        job = get_job(int(job_id))
        if not job:
            return {"success": False, "error": "岗位不存在"}

        stages = get_candidates_by_stage(self.user_id, int(job_id))
        summary = get_pipeline_summary(self.user_id, int(job_id))

        return {
            "success": True,
            "job_name": job["name"],
            "summary": summary,
            "stages": {
                stage: [{
                    "name": c.get("candidate_name"),
                    "ai_score": c.get("ai_score"),
                    "reason": c.get("reason", ""),
                    "reject_stage": c.get("reject_stage", ""),
                    "company": c.get("company_rejected", ""),
                    "updated": c.get("updated_at", ""),
                } for c in candidates]
                for stage, candidates in stages.items()
            },
        }

    async def _tool_sync_pipeline_to_obsidian(self, params: dict) -> dict:
        """同步管道状态到 Obsidian"""
        from .models import get_candidates_by_stage, get_pipeline_summary, get_job, get_settings
        from .knowledge import sync_pipeline_to_obsidian

        job_id = params.get("job_id")
        if not job_id:
            return {"success": False, "error": "需要 job_id"}

        job = get_job(int(job_id))
        if not job:
            return {"success": False, "error": "岗位不存在"}

        settings = get_settings(self.user_id)
        job_folder = settings.get("obsidian_job_path", "")
        if not job_folder:
            return {"success": False, "error": "未配置 Obsidian 岗位文件夹路径"}

        stages = get_candidates_by_stage(self.user_id, int(job_id))
        summary = get_pipeline_summary(self.user_id, int(job_id))

        ok = sync_pipeline_to_obsidian(job_folder, job["name"], stages, summary)
        return {"success": ok, "job_name": job["name"]}
