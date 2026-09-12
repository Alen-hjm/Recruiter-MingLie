"""
明猎 - 后端 API 服务
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify, request, redirect, url_for, render_template, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from .models import (
    init_db, get_db, User, create_user, authenticate_user, get_user_by_id,
    create_scrape_task, update_scrape_task, get_scrape_tasks, get_scrape_task,
    create_score_task, update_score_task, get_score_tasks, get_score_task,
    create_job, get_jobs, get_job, update_job, delete_job,
    get_settings, update_settings, get_user_stats, get_resumes_by_job,
    edit_resume, delete_resume, delete_low_score_resumes,
    save_scrape_results_dedup, get_unscored_resumes, extract_resume_id,
    create_interview_feedback, get_interview_feedbacks, mark_feedback_synced,
    create_candidate_from_score, update_candidate_pipeline,
    get_candidates_by_stage, get_pipeline_summary, get_unsynced_feedbacks,
    create_search_strategy, get_search_strategies, get_search_strategy,
    update_search_strategy, delete_search_strategy, get_default_strategy,
)
from .knowledge_engine import init_knowledge_db

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.secret_key = os.urandom(24)

# CORS 支持（Chrome 插件需要跨域访问）
from flask_cors import CORS
CORS(app, resources={r"/api/extension/*": {"origins": "*"}})

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(int(user_id))


# ── 页面路由 ──

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = authenticate_user(username, password)
        if user:
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("用户名或密码错误")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()
        if not username or not password:
            flash("用户名和密码不能为空")
        elif len(password) < 4:
            flash("密码至少4位")
        else:
            try:
                user = create_user(username, password, display_name)
                login_user(user)
                return redirect(url_for("dashboard"))
            except Exception:
                flash("用户名已存在")
    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/scrape")
@login_required
def scrape_page():
    return render_template("scrape.html")


@app.route("/score")
@login_required
def score_page():
    return render_template("score.html")


@app.route("/report")
@login_required
def report_page():
    return render_template("report.html")


@app.route("/jobs")
@login_required
def jobs_page():
    return render_template("jobs.html")


@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html")

@app.route("/interview")
@login_required
def interview_page():
    return render_template("interview.html")


@app.route("/candidates")
@login_required
def candidates_page():
    return render_template("candidates.html")


@app.route("/agent")
@login_required
def agent_page():
    return render_template("agent.html")

@app.route("/logs")
@login_required
def logs_page():
    return render_template("logs.html")


# ── API 路由 ──

@app.route("/api/stats")
@login_required
def api_stats():
    stats = get_user_stats(current_user.id)
    return jsonify({"success": True, "data": stats})


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    if request.method == "GET":
        settings = get_settings(current_user.id)
        # 脱敏
        if settings.get("llm_api_key"):
            key = settings["llm_api_key"]
            settings["llm_api_key_masked"] = key[:6] + "***" + key[-4:] if len(key) > 10 else "***"
        return jsonify({"success": True, "data": settings})

    data = request.json or {}
    allowed = ["llm_api_key", "llm_base_url", "llm_model", "default_city", "default_pages", "obsidian_kb_path", "obsidian_job_path"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if updates:
        update_settings(current_user.id, **updates)
    return jsonify({"success": True})


# ── 岗位管理 API ──

@app.route("/api/jobs", methods=["GET"])
@login_required
def api_jobs_list():
    jobs = get_jobs(current_user.id)
    # 附带每个岗位的简历数量和搜索策略
    for j in jobs:
        resumes = get_resumes_by_job(current_user.id, j["id"])
        j["resume_count"] = len(resumes)
        j["strategies"] = get_search_strategies(current_user.id, j["id"])
    return jsonify({"success": True, "data": jobs})


@app.route("/api/jobs", methods=["POST"])
@login_required
def api_jobs_create():
    data = request.json or {}
    name = data.get("name", "").strip()
    keyword = data.get("keyword", "").strip()
    desc = data.get("job_description", "").strip()
    if not name:
        return jsonify({"success": False, "error": "岗位名称不能为空"}), 400
    job_id = create_job(current_user.id, name, keyword, desc)

    # P0-1: 自动创建默认搜索策略（岗位名和搜索词解耦）
    if keyword:
        create_search_strategy(
            current_user.id, job_id, keyword,
            name=f"{name}-默认搜索",
            city=data.get("city", "上海").strip() or "上海",
            is_default=True,
        )

    # 知识引擎：从新岗位中学习
    try:
        from .knowledge_engine import learn_from_job, search_and_learn_industry
        learn_from_job({"name": name, "keyword": keyword, "job_description": desc})
        search_and_learn_industry(name, user_id=current_user.id)
    except Exception as e:
        print(f"[知识引擎] 岗位学习失败: {e}")

    # ⭐ 新增：同步到 Obsidian        
    try:
        settings = get_settings(current_user.id)
        job_folder = settings.get("obsidian_job_path", "")
        if job_folder:
            from .knowledge import sync_job_to_obsidian
            sync_job_to_obsidian(job_folder, {"name": name, "keyword": keyword, "job_description": desc})
            print(f"[Obsidian] 已同步岗位: {name}")
    except Exception as e:
        print(f"[Obsidian] 同步失败: {e}")

    return jsonify({"success": True, "id": job_id})



@app.route("/api/jobs/<int:job_id>", methods=["PUT"])
@login_required
def api_jobs_update(job_id):
    data = request.json or {}
    allowed = ["name", "keyword", "job_description"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        update_job(job_id, **updates)
    return jsonify({"success": True})


@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
@login_required
def api_jobs_delete(job_id):
    delete_job(job_id)
    return jsonify({"success": True})


@app.route("/api/jobs/<int:job_id>/resumes")
@login_required
def api_jobs_resumes(job_id):
    """获取某个岗位下的所有简历（含评分数据）"""
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404
    resumes = get_resumes_by_job(current_user.id, job_id)

    # 查找该岗位最近一次完成的评分任务，合并评分数据
    conn = get_db()
    try:
        score_row = conn.execute(
            "SELECT * FROM score_tasks WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (current_user.id,),
        ).fetchone()
        if score_row and score_row["scored_json"]:
            scored_list = json.loads(score_row["scored_json"])
            # 按 detail_url 或 name+title 建索引
            score_map = {}
            for sr in scored_list:
                key = sr.get("detail_url", "") or (sr.get("name", "") + sr.get("title", ""))
                if key:
                    score_map[key] = sr
            # 合并到简历
            for resume in resumes:
                rkey = resume.get("detail_url", "") or (resume.get("name", "") + resume.get("title", ""))
                if rkey in score_map:
                    sr = score_map[rkey]
                    if "score100" in sr:
                        resume["score100"] = sr["score100"]
                    if "analysis" in sr:
                        resume["analysis"] = sr["analysis"]
                    if "_error" in sr:
                        resume["score_error"] = sr["_error"]
    finally:
        conn.close()

    return jsonify({"success": True, "data": resumes, "count": len(resumes), "job": job})


# ── 简历编辑/删除 API ──

@app.route("/api/resumes/edit", methods=["POST"])
@login_required
def api_resume_edit():
    """编辑简历信息（如修改姓名）"""
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key")
    updates = data.get("updates", {})

    if not job_id or not resume_key:
        return jsonify({"success": False, "error": "缺少参数"}), 400

    allowed_fields = ["name", "activity", "age", "experience", "education", "company", "title", "location", "salary"]
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}
    if not filtered:
        return jsonify({"success": False, "error": "没有可更新的字段"}), 400

    ok = edit_resume(current_user.id, int(job_id), resume_key, filtered)
    if ok:
        return jsonify({"success": True, "message": "已更新"})
    return jsonify({"success": False, "error": "未找到该简历"}), 404


@app.route("/api/resumes/delete", methods=["POST"])
@login_required
def api_resume_delete():
    """删除单份简历"""
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key")

    if not job_id or not resume_key:
        return jsonify({"success": False, "error": "缺少参数"}), 400

    ok = delete_resume(current_user.id, int(job_id), resume_key)
    if ok:
        return jsonify({"success": True, "message": "已删除"})
    return jsonify({"success": False, "error": "未找到该简历"}), 404


# ── 搜索策略 API ──

@app.route("/api/jobs/<int:job_id>/strategies")
@login_required
def api_strategies_list(job_id):
    """获取岗位的所有搜索策略"""
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404
    strategies = get_search_strategies(current_user.id, job_id)
    return jsonify({"success": True, "data": strategies})


@app.route("/api/jobs/<int:job_id>/strategies", methods=["POST"])
@login_required
def api_strategies_create(job_id):
    """为岗位创建搜索策略"""
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    data = request.json or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "关键词不能为空"}), 400

    name = data.get("name", "").strip() or keyword
    city = data.get("city", "").strip() or "上海"
    platform = data.get("platform", "liepin")
    max_pages = min(int(data.get("max_pages", 3)), 5)
    is_default = data.get("is_default", False)

    sid = create_search_strategy(
        current_user.id, job_id, keyword,
        name=name, city=city, platform=platform,
        max_pages=max_pages, is_default=is_default,
    )
    return jsonify({"success": True, "id": sid})


@app.route("/api/strategies/<int:strategy_id>", methods=["PUT"])
@login_required
def api_strategies_update(strategy_id):
    """更新搜索策略"""
    s = get_search_strategy(strategy_id)
    if not s or s["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "策略不存在"}), 404

    data = request.json or {}
    allowed = ["name", "keyword", "city", "platform", "max_pages", "is_default"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "is_default" in updates:
        updates["is_default"] = 1 if updates["is_default"] else 0
    if updates:
        update_search_strategy(strategy_id, **updates)
    return jsonify({"success": True})


@app.route("/api/strategies/<int:strategy_id>", methods=["DELETE"])
@login_required
def api_strategies_delete(strategy_id):
    """删除搜索策略"""
    s = get_search_strategy(strategy_id)
    if not s or s["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "策略不存在"}), 404
    delete_search_strategy(strategy_id)
    return jsonify({"success": True})


# ── 抓取 API ──

# 全局任务状态
_scrape_threads = {}


def _run_scrape_thread(task_id: int, config: dict, keyword: str, city: str, platform: str, max_pages: int, user_id: int = None, job_id: int = None):
    """后台抓取线程（带去重）"""
    resumes = []
    try:
        update_scrape_task(task_id, status="running")

        # 动态导入抓取器
        if platform == "liepin":
            from .scraper_liepin import LiepinHunterScraper
            scraper = LiepinHunterScraper(config)
        else:
            raise ValueError(f"不支持的平台: {platform}")

        # 运行抓取
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        resumes = loop.run_until_complete(scraper.run())
        loop.close()

        # 去重：过滤掉已存在的简历
        dup_count = 0
        if user_id and job_id and resumes:
            existing_ids = get_existing_resume_ids(user_id, job_id)
            new_resumes = []
            for r in resumes:
                rid = extract_resume_id(r)
                if rid in existing_ids:
                    dup_count += 1
                else:
                    existing_ids.add(rid)
                    new_resumes.append(r)
            if dup_count > 0:
                print(f"[去重] 过滤 {dup_count} 份重复简历，保留 {len(new_resumes)} 份新简历")
            resumes = new_resumes

        # 保存结果
        error_msg = None
        if dup_count > 0:
            error_msg = f"已过滤 {dup_count} 份重复简历"
        update_scrape_task(
            task_id,
            status="done",
            result_count=len(resumes),
            resumes_json=json.dumps(resumes, ensure_ascii=False),
            error_msg=error_msg,
            finished_at=datetime.now().isoformat(),
        )
    except KeyboardInterrupt:
        # 中途退出，保存已抓取的数据
        if resumes:
            update_scrape_task(
                task_id,
                status="done",
                result_count=len(resumes),
                resumes_json=json.dumps(resumes, ensure_ascii=False),
                error_msg="抓取被中断，已保存部分数据",
                finished_at=datetime.now().isoformat(),
            )
        else:
            update_scrape_task(
                task_id,
                status="error",
                error_msg="抓取被中断",
                finished_at=datetime.now().isoformat(),
            )
    except Exception as e:
        # 异常时也保存已抓取的数据
        print(f"[抓取错误] 任务 {task_id}: {e}")
        if resumes:
            update_scrape_task(
                task_id,
                status="done",
                result_count=len(resumes),
                resumes_json=json.dumps(resumes, ensure_ascii=False),
                error_msg=f"抓取出错({e})，已保存部分数据",
                finished_at=datetime.now().isoformat(),
            )
        else:
            update_scrape_task(
                task_id,
                status="error",
                error_msg=str(e),
                finished_at=datetime.now().isoformat(),
            )


@app.route("/api/scrape", methods=["POST"])
@login_required
def api_scrape_start():
    data = request.json or {}
    job_id = data.get("job_id")
    strategy_id = data.get("strategy_id")

    if not job_id:
        return jsonify({"success": False, "error": "请选择归属岗位"}), 400

    # 从岗位获取信息
    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    # 优先用搜索策略，其次用前端传入，最后 fallback 到岗位配置
    if strategy_id:
        strategy = get_search_strategy(int(strategy_id))
        if strategy and strategy["user_id"] == current_user.id:
            keyword = strategy["keyword"]
            city = strategy.get("city", "上海")
            platform = strategy.get("platform", "liepin")
            max_pages = min(int(strategy.get("max_pages", 3)), 5)
        else:
            return jsonify({"success": False, "error": "搜索策略不存在"}), 404
    else:
        platform = data.get("platform", "liepin")
        max_pages = min(int(data.get("max_pages", 3)), 5)
        keyword = data.get("keyword", "").strip() or job.get("keyword", "").strip()
        city = data.get("city", "").strip() or "上海"

    search_mode = data.get("search_mode", "auto")
    if not keyword and search_mode != "manual":
        return jsonify({"error": "请填写关键词或切换为手动搜索模式"}), 400

    # 获取用户 LLM 配置
    settings = get_settings(current_user.id)
    config = {
        "platform": platform,
        "llm": {
            "api_key": settings.get("llm_api_key", ""),
            "base_url": settings.get("llm_base_url", "https://api.deepseek.com"),
            "model": settings.get("llm_model", "deepseek-chat"),
        },
        "scraper": {
            "launch_mode": "cdp",  # cdp=连接已启动的Chrome(推荐), stealth=反检测插件
            "cdp_port": 9222,      # Chrome 调试端口
            "headless": False,
            "slow_mo": 800,
            "timeout": 30000,
            "search_mode": search_mode,
            "user_data_dir": f"./browser_data/user_{current_user.id}",
        },
        "search": {
            "keyword": keyword,
            "city": city,
            "max_pages": max_pages,
        },
    }

    task_id = create_scrape_task(current_user.id, keyword, city, platform, max_pages, job_id=int(job_id))

    # 后台线程运行（传入 user_id 和 job_id 用于去重）
    thread = threading.Thread(
        target=_run_scrape_thread,
        args=(task_id, config, keyword, city, platform, max_pages, current_user.id, int(job_id)),
        daemon=True,
    )
    thread.start()
    _scrape_threads[task_id] = thread

    return jsonify({"success": True, "task_id": task_id})


@app.route("/api/scrape/<int:task_id>")
@login_required
def api_scrape_status(task_id):
    task = get_scrape_task(task_id)
    if not task or task["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "任务不存在"}), 404

    result = {
        "id": task["id"],
        "status": task["status"],
        "keyword": task["keyword"],
        "city": task["city"],
        "result_count": task["result_count"],
        "error_msg": task["error_msg"],
        "created_at": task["created_at"],
        "finished_at": task["finished_at"],
    }

    # 如果完成了，也返回简历数据
    if task["status"] == "done" and task["resumes_json"]:
        result["resumes"] = json.loads(task["resumes_json"])

    return jsonify({"success": True, "data": result})


@app.route("/api/scrape/list")
@login_required
def api_scrape_list():
    tasks = get_scrape_tasks(current_user.id, limit=20)
    # 不返回完整简历数据，节省带宽
    for t in tasks:
        t.pop("resumes_json", None)
    return jsonify({"success": True, "data": tasks})


# ── 评分 API ──

def _score_one_resume(analyzer, resume, job_description, mode):
    """评分单份简历（供并发调用）"""
    try:
        if mode == "100":
            result = analyzer.score_resume_100(resume, job_description)
            resume["score100"] = result
        else:
            result = analyzer.analyze_one(resume)
            resume["analysis"] = result
    except Exception as e:
        print(f"[评分错误] 简历 {resume.get('name', '?')}: {e}")
        resume["_error"] = str(e)
    return resume


def _run_score_thread(task_id: int, config: dict, resumes: list, job_description: str, mode: str, user_id: int = None, job_id: int = None):
    """后台评分线程（并发版，合并历史评分，知识库增强，自动清理低分）"""
    try:
        update_score_task(task_id, status="running")

        # ── 加载知识库上下文 ──
        knowledge_context = ""
        if user_id:
            try:
                settings = get_settings(user_id)
                kb_path = settings.get("obsidian_kb_path", "")
                if kb_path and Path(kb_path).exists():
                    from .knowledge import scan_knowledge_base, find_relevant_notes, build_knowledge_context
                    all_notes = scan_knowledge_base(kb_path)
                    job_name = config.get("job_name", "")
                    relevant = find_relevant_notes(all_notes, job_name, job_description)
                    knowledge_context = build_knowledge_context(relevant)
                    if knowledge_context:
                        print(f"[知识库] 加载 {len(relevant)} 篇相关笔记作为评分参考")
            except Exception as e:
                print(f"[知识库] 加载失败: {e}")

        config["knowledge_context"] = knowledge_context

        # ── 加载知识引擎上下文 ──
        engine_context = ""
        if user_id:
            try:
                from .knowledge_engine import build_scoring_context
                job_name = config.get("job_name", "")
                engine_context = build_scoring_context(job_name)
                if engine_context:
                    print(f"[知识引擎] 加载了 {job_name} 的评分知识上下文")
            except Exception as e:
                print(f"[知识引擎] 加载失败: {e}")

        if engine_context:
            config["knowledge_context"] = (config.get("knowledge_context", "") or "") + "\n\n" + engine_context

        from .analyzer import ResumeAnalyzer
        analyzer = ResumeAnalyzer(config)

        # ── 获取已有评分结果，用于合并 ──
        existing_scored = []
        existing_ids = set()
        if user_id and job_id:
            try:
                conn = get_db()
                row = conn.execute(
                    """SELECT scored_json FROM score_tasks
                       WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
                       AND job_name = (SELECT name FROM jobs WHERE id = ?)
                       ORDER BY created_at DESC LIMIT 1""",
                    (user_id, job_id),
                ).fetchone()
                conn.close()
                if row and row["scored_json"]:
                    existing_scored = json.loads(row["scored_json"])
                    for r in existing_scored:
                        existing_ids.add(extract_resume_id(r))
                    print(f"[评分] 加载已有评分 {len(existing_scored)} 份")
            except Exception as e:
                print(f"[评分] 加载历史评分失败: {e}")

        # ── 只评分新简历（排除已评分的）──
        new_resumes = []
        for r in resumes:
            rid = extract_resume_id(r)
            if rid not in existing_ids:
                new_resumes.append(r)

        if not new_resumes:
            print("[评分] 没有新简历需要评分")
            update_score_task(
                task_id, status="done",
                scored_json=json.dumps(existing_scored, ensure_ascii=False),
                passed_count=sum(1 for r in existing_scored if r.get("score100", {}).get("pass", False)),
                total_count=len(existing_scored),
                finished_at=datetime.now().isoformat(),
                error_msg="全部已评分，无新简历",
            )
            return

        print(f"[评分] 新简历 {len(new_resumes)} 份，已有评分 {len(existing_scored)} 份")

        # ── 并发评分新简历 ──
        scored_new = [None] * len(new_resumes)
        max_workers = min(5, len(new_resumes))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {}
            for idx, resume in enumerate(new_resumes):
                future = executor.submit(_score_one_resume, analyzer, resume, job_description, mode)
                future_to_idx[future] = idx

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    scored_new[idx] = future.result()
                except Exception as e:
                    print(f"[评分异常] 索引 {idx}: {e}")
                    new_resumes[idx]["_error"] = str(e)
                    scored_new[idx] = new_resumes[idx]

        scored_new = [r for r in scored_new if r is not None]

        # ── 合并：已有评分 + 新评分 ──
        all_scored = existing_scored + scored_new

        passed = sum(1 for r in all_scored if r.get("score100", {}).get("pass", False)) if mode == "100" else 0

        # 评分后自动删除 < 60 分的简历
        deleted_count = 0
        if user_id and job_id:
            try:
                deleted_count = delete_low_score_resumes(user_id, job_id, threshold=60)
                if deleted_count > 0:
                    print(f"[自动清理] 已删除 {deleted_count} 份低于60分的简历")
            except Exception as e:
                print(f"[自动清理失败] {e}")

        error_msg = None
        if deleted_count > 0:
            error_msg = f"已自动删除 {deleted_count} 份低分简历"
        if len(existing_scored) > 0:
            extra = f"（合并已有评分 {len(existing_scored)} 份）"
            error_msg = f"{error_msg} {extra}" if error_msg else extra

        update_score_task(
            task_id,
            status="done",
            scored_json=json.dumps(all_scored, ensure_ascii=False),
            passed_count=passed,
            total_count=len(all_scored),
            finished_at=datetime.now().isoformat(),
            error_msg=error_msg,
        )

        # ── 评分完成后，自动将通过的候选人录入管道 ──
        if user_id and job_id:
            try:
                pipeline_count = 0
                for r in all_scored:
                    score = 0
                    if r.get("score100"):
                        s = r["score100"]
                        score = s.get("total_score", 0) if isinstance(s, dict) else s
                    if score >= 60:  # 60分以上的录入管道
                        create_candidate_from_score(user_id, job_id, r)
                        pipeline_count += 1
                print(f"[管道] 已录入 {pipeline_count} 位候选人到管道")

                # 同步管道到 Obsidian
                try:
                    settings = get_settings(user_id)
                    job_folder = settings.get("obsidian_job_path", "")
                    if job_folder:
                        from .knowledge import sync_pipeline_to_obsidian
                        job = get_job(job_id)
                        if job:
                            stages = get_candidates_by_stage(user_id, job_id)
                            summary = get_pipeline_summary(user_id, job_id)
                            sync_pipeline_to_obsidian(job_folder, job["name"], stages, summary)
                            print(f"[管道] 已同步到 Obsidian")
                except Exception as e:
                    print(f"[管道] Obsidian 同步失败: {e}")
            except Exception as e:
                print(f"[管道] 录入失败: {e}")
    except Exception as e:
        print(f"[评分任务异常] 任务 {task_id}: {e}")
        update_score_task(
            task_id,
            status="error",
            error_msg=str(e),
            finished_at=datetime.now().isoformat(),
        )


@app.route("/api/score", methods=["POST"])
@login_required
def api_score_start():
    data = request.json or {}
    job_id = data.get("job_id")
    mode = data.get("mode", "100")

    if not job_id:
        return jsonify({"success": False, "error": "请选择岗位"}), 400

    # 获取岗位信息
    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    job_name = job["name"]
    job_description = job.get("job_description", "") or ""
    if not job_description:
        return jsonify({"success": False, "error": "该岗位未配置岗位描述，请先去岗位管理编辑"}), 400

    # 获取未评分的简历（跳过已评分的）
    resumes, already_scored = get_unscored_resumes(current_user.id, int(job_id))

    if not resumes and already_scored == 0:
        return jsonify({"success": False, "error": "该岗位下没有简历，请先抓取"}), 400

    if not resumes and already_scored > 0:
        return jsonify({"success": False, "error": f"该岗位下 {already_scored} 份简历已全部评分，无需重复评分"}), 400

    # 获取 LLM 配置
    settings = get_settings(current_user.id)

    # ── [修复] 校验 API Key ──
    api_key = settings.get("llm_api_key", "")
    if not api_key:
        return jsonify({"success": False, "error": "请先在「设置」中配置 LLM API Key，否则无法评分"}), 400

    config = {
        "llm": {
            "api_key": api_key,
            "base_url": settings.get("llm_base_url", "https://api.deepseek.com"),
            "model": settings.get("llm_model", "deepseek-chat"),
        },
        "job_description": job_description,
        "job_name": job_name,
    }

    task_id = create_score_task(current_user.id, None, job_name, job_description, mode)

    thread = threading.Thread(
        target=_run_score_thread,
        args=(task_id, config, resumes, job_description, mode, current_user.id, int(job_id)),
        daemon=True,
    )
    thread.start()

    # 返回时告知跳过了多少份
    msg = f"开始评分 {len(resumes)} 份简历"
    if already_scored > 0:
        msg += f"（跳过已评分 {already_scored} 份）"

    return jsonify({"success": True, "task_id": task_id, "resume_count": len(resumes), "skipped": already_scored, "message": msg})


@app.route("/api/score/<int:task_id>")
@login_required
def api_score_status(task_id):
    task = get_score_task(task_id)
    if not task or task["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "任务不存在"}), 404

    result = {
        "id": task["id"],
        "status": task["status"],
        "job_name": task["job_name"],
        "mode": task["mode"],
        "passed_count": task["passed_count"],
        "total_count": task["total_count"],
        "error_msg": task["error_msg"],
        "created_at": task["created_at"],
        "finished_at": task["finished_at"],
    }

    if task["status"] == "done" and task["scored_json"]:
        result["scored"] = json.loads(task["scored_json"])

    return jsonify({"success": True, "data": result})


@app.route("/api/score/list")
@login_required
def api_score_list():
    tasks = get_score_tasks(current_user.id, limit=20)
    for t in tasks:
        t.pop("scored_json", None)
    return jsonify({"success": True, "data": tasks})


# ── 报告 API ──

@app.route("/api/report/<int:score_task_id>")
@login_required
def api_report(score_task_id):
    task = get_score_task(score_task_id)
    if not task or task["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "评分任务不存在"}), 404
    if task["status"] != "done":
        return jsonify({"success": False, "error": "评分未完成"}), 400

    scored = json.loads(task["scored_json"]) if task["scored_json"] else []
    mode = task["mode"] or "100"

    # 生成 Markdown 报告
    from .pipeline_report import generate_markdown_report
    report = generate_markdown_report(scored, mode)

    return jsonify({"success": True, "data": {"report": report, "mode": mode}})


# ── 面试反馈 API ──

@app.route("/api/feedback", methods=["POST"])
@login_required
def api_feedback_create():
    """创建面试反馈（淘汰原因），并同步到 Obsidian"""
    data = request.json or {}
    job_id = data.get("job_id")
    candidate_name = data.get("candidate_name", "")
    resume_key = data.get("resume_key", "")
    ai_score = data.get("ai_score", 0)
    feedback_type = data.get("feedback_type", "reject")
    reason = data.get("reason", "").strip()

    if not job_id or not reason:
        return jsonify({"success": False, "error": "岗位和原因不能为空"}), 400

    fid = create_interview_feedback(
        current_user.id, int(job_id), candidate_name, resume_key,
        ai_score, feedback_type, reason
    )

    settings = get_settings(current_user.id)
    job_folder = settings.get("obsidian_job_path", "")
    synced = False
    if job_folder:
        job = get_job(int(job_id))
        if job:
            from .knowledge import append_to_job_note
            from datetime import datetime
            score_str = f"AI评分{ai_score}分" if ai_score else "未评分"
            content = f"- **{candidate_name}**（{score_str}）：{reason}"
            section = f"面试反馈 - {datetime.now().strftime('%Y-%m-%d')}"
            synced = append_to_job_note(job_folder, job["name"], section, content)
            if synced:
                mark_feedback_synced(fid)

    return jsonify({"success": True, "id": fid, "synced": synced})


@app.route("/api/feedback/list")
@login_required
def api_feedback_list():
    """获取面试反馈列表"""
    job_id = request.args.get("job_id")
    feedbacks = get_interview_feedbacks(current_user.id, int(job_id) if job_id else None)
    return jsonify({"success": True, "data": feedbacks})


# ── 知识库 API ──

@app.route("/api/knowledge/status")
@login_required
def api_knowledge_status():
    """检查知识库连接状态"""
    settings = get_settings(current_user.id)
    kb_path = settings.get("obsidian_kb_path", "").strip()
    job_path = settings.get("obsidian_job_path", "").strip()

    # 规范化路径：将反斜杠替换为正斜杠
    if kb_path:
        kb_path = kb_path.replace('\\', '/')
    if job_path:
        job_path = job_path.replace('\\', '/')

    result = {"kb_path": kb_path, "job_path": job_path, "kb_exists": False, "job_exists": False, "kb_notes": 0}

    if kb_path:
        try:
            kb_path_obj = Path(kb_path)
            if kb_path_obj.exists():
                result["kb_exists"] = True
                from .knowledge import scan_knowledge_base
                notes = scan_knowledge_base(kb_path)
                result["kb_notes"] = len(notes)
            else:
                # 尝试展开用户目录
                expanded = Path(os.path.expanduser(kb_path))
                if expanded.exists():
                    result["kb_exists"] = True
                    from .knowledge import scan_knowledge_base
                    notes = scan_knowledge_base(str(expanded))
                    result["kb_notes"] = len(notes)
        except Exception as e:
            print(f"[知识库] 路径检查失败: {e}")

    if job_path:
        try:
            job_path_obj = Path(job_path)
            if job_path_obj.exists():
                result["job_exists"] = True
            else:
                expanded = Path(os.path.expanduser(job_path))
                if expanded.exists():
                    result["job_exists"] = True
        except Exception as e:
            print(f"[知识库] 岗位路径检查失败: {e}")

    return jsonify({"success": True, "data": result})


# ── JD 智能拆解 API ──

@app.route("/api/jd/analyze", methods=["POST"])
@login_required
def api_jd_analyze():
    """Agent 智能拆解 JD，返回行业、关键词等结构化结果"""
    data = request.json or {}
    job_name = data.get("job_name", "").strip()
    job_description = data.get("job_description", "").strip()

    if not job_name:
        return jsonify({"success": False, "error": "岗位名称不能为空"}), 400

    # 获取 LLM 配置
    settings = get_settings(current_user.id)
    api_key = settings.get("llm_api_key", "")
    base_url = settings.get("llm_base_url", "https://api.deepseek.com")
    model = settings.get("llm_model", "deepseek-chat")

    # 调用 JD 分析
    from .jd_analyzer import analyze_jd
    import asyncio

    try:
        result = asyncio.run(analyze_jd(
            job_name=job_name,
            job_description=job_description,
            api_key=api_key,
            base_url=base_url,
            model=model,
            use_llm=bool(api_key),
        ))
        return jsonify({"success": True, "data": result})
    except Exception as e:
        print(f"[JD分析] 失败: {e}")
        return jsonify({"success": False, "error": f"分析失败: {str(e)}"}), 500


# ── 候选人操作 API（约面/淘汰）──

@app.route("/api/candidate/action", methods=["POST"])
@login_required
def api_candidate_action():
    """候选人操作：约面或淘汰"""
    data = request.json or {}
    action = data.get("action", "")  # "interview" 或 "reject"
    job_id = data.get("job_id")
    candidate_name = data.get("candidate_name", "")
    resume_key = data.get("resume_key", "")
    ai_score = data.get("ai_score", 0)
    reason = data.get("reason", "").strip()
    resume_data = data.get("resume_data", {})  # 完整简历数据

    sync_to_obsidian = data.get("sync_to_obsidian", False)  # 是否同步到 Obsidian

    if not job_id or not action:
        return jsonify({"success": False, "error": "缺少参数"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    if action == "interview":
        # 约面：创建面试记录
        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO interview_feedback 
                   (user_id, job_id, candidate_name, resume_key, ai_score, feedback_type, reason)
                   VALUES (?, ?, ?, ?, ?, 'interview', '推荐约面')""",
                (current_user.id, int(job_id), candidate_name, resume_key, ai_score),
            )
            conn.commit()
        finally:
            conn.close()

        # 约面默认同步到 Obsidian
        settings = get_settings(current_user.id)
        job_folder = settings.get("obsidian_job_path", "")
        synced = False
        if job_folder:
            from .knowledge import append_to_job_note
            score_str = f"AI评分{ai_score}分" if ai_score else "未评分"
            content = f"- **{candidate_name}**（{score_str}）：推荐约面"
            section = f"面试安排 - {datetime.now().strftime('%Y-%m-%d')}"
            synced = append_to_job_note(job_folder, job["name"], section, content)

        return jsonify({"success": True, "action": "interview", "synced": synced})

    elif action == "reject":
        # 淘汰：需要提供原因
        if not reason:
            return jsonify({"success": False, "error": "请输入淘汰原因"}), 400

        fid = create_interview_feedback(
            current_user.id, int(job_id), candidate_name, resume_key,
            ai_score, "reject", reason
        )

        # 根据用户选择决定是否同步到 Obsidian
        synced = False
        if sync_to_obsidian:
            settings = get_settings(current_user.id)
            job_folder = settings.get("obsidian_job_path", "")
            if job_folder:
                from .knowledge import append_to_job_note
                score_str = f"AI评分{ai_score}分" if ai_score else "未评分"
                content = f"- **{candidate_name}**（{score_str}）：{reason}"
                section = f"淘汰反馈 - {datetime.now().strftime('%Y-%m-%d')}"
                synced = append_to_job_note(job_folder, job["name"], section, content)
                if synced:
                    mark_feedback_synced(fid)

        # ── 触发记忆学习 ──
        try:
            from .agent_memory import learn_from_feedback
            learn_from_feedback(current_user.id, int(job_id), [{
                "reason": reason,
                "candidate_name": candidate_name,
                "ai_score": ai_score,
            }])
        except Exception as e:
            print(f"[记忆] 学习失败: {e}")

        return jsonify({"success": True, "action": "reject", "id": fid, "synced": synced})

    else:
        return jsonify({"success": False, "error": "无效的操作"}), 400


@app.route("/api/candidates/<int:job_id>")
@login_required
def api_candidates_list(job_id):
    """获取岗位下的候选人列表（带分类状态）"""
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resumes = get_resumes_by_job(current_user.id, job_id)

    # ── 从 score_tasks 获取评分数据 ──
    score_map = {}
    conn = get_db()
    try:
        # 获取该岗位最近一次完成的评分任务
        score_row = conn.execute(
            """SELECT scored_json FROM score_tasks
               WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
               AND job_name = (SELECT name FROM jobs WHERE id = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (current_user.id, job_id),
        ).fetchone()
        if score_row and score_row["scored_json"]:
            scored_list = json.loads(score_row["scored_json"])
            for sr in scored_list:
                # 用 detail_url 或 name+title 作为 key
                key = sr.get("detail_url", "") or (sr.get("name", "") + sr.get("title", ""))
                if key:
                    score_map[key] = sr

        # 获取已有的面试/淘汰记录
        feedbacks = conn.execute(
            """SELECT candidate_name, resume_key, feedback_type, ai_score, reason
               FROM interview_feedback WHERE user_id = ? AND job_id = ?""",
            (current_user.id, job_id),
        ).fetchall()
    finally:
        conn.close()

    # 建立反馈索引
    feedback_map = {}
    for fb in feedbacks:
        key = fb["resume_key"] or fb["candidate_name"]
        feedback_map[key] = dict(fb)

    # 分类简历
    result = {
        "recommended": [],   # >= 80 推荐约面
        "pending": [],       # 60-80 待淘汰
        "rejected": [],      # < 60 或已淘汰
        "interviewed": [],   # 已约面
    }

    for resume in resumes:
        key = resume.get("detail_url", "") or (resume.get("name", "") + resume.get("title", ""))
        resume["_key"] = key

        # ── 从 score_map 合并评分数据 ──
        if key in score_map:
            sr = score_map[key]
            if "score100" in sr:
                resume["score100"] = sr["score100"]
            if "analysis" in sr:
                resume["analysis"] = sr["analysis"]
            if "_error" in sr:
                resume["score_error"] = sr["_error"]

        # ── 提取分数 ──
        score = 0
        if resume.get("score100"):
            s = resume["score100"]
            if isinstance(s, dict):
                score = s.get("total_score", 0) or s.get("score", 0) or 0
            elif isinstance(s, (int, float)):
                score = s
        elif resume.get("analysis"):
            s = resume["analysis"]
            if isinstance(s, dict):
                score = s.get("total_score", 0) or s.get("score", 0) or 0
                if score < 10:  # 10分制转100分制
                    score = score * 10
            elif isinstance(s, (int, float)):
                score = s

        resume["_score"] = round(score)

        # ── 分类 ──
        if key in feedback_map:
            fb = feedback_map[key]
            resume["_feedback"] = fb
            if fb["feedback_type"] == "interview":
                result["interviewed"].append(resume)
            else:
                result["rejected"].append(resume)
        elif score >= 80:
            result["recommended"].append(resume)
        elif score >= 60:
            result["pending"].append(resume)
        else:
            result["rejected"].append(resume)

    # 按分数排序
    for key in result:
        result[key].sort(key=lambda x: x.get("_score", 0), reverse=True)

    return jsonify({"success": True, "data": result, "job": job})


# ── Agent API ──

# Agent 会话历史（内存存储，重启丢失）
_agent_sessions = {}


@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    """Agent 对话接口"""
    data = request.json or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"success": False, "error": "消息不能为空"}), 400

    # 获取会话历史
    session_key = f"user_{current_user.id}"
    if session_key not in _agent_sessions:
        _agent_sessions[session_key] = []
    history = _agent_sessions[session_key]

    # 添加用户消息到历史
    history.append({"role": "user", "content": message})

    # 只保留最近20轮
    if len(history) > 40:
        history = history[-40:]
        _agent_sessions[session_key] = history

    # 调用 Agent
    from .agent import run_agent
    import asyncio

    try:
        result = asyncio.run(run_agent(
            user_id=current_user.id,
            goal=message,
            history=history[:-1],  # 不包含当前消息（已在 goal 中）
        ))
    except Exception as e:
        print(f"[Agent] 执行失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    # 添加助手回复到历史
    reply = result.get("summary", "处理完成")
    history.append({"role": "assistant", "content": reply})
    _agent_sessions[session_key] = history

    return jsonify({
        "success": True,
        "reply": reply,
        "plan": result.get("plan", {}),
        "results": result.get("results", []),
        "is_chat": result.get("is_chat", False),
    })


@app.route("/api/chat/clear", methods=["POST"])
@login_required
def api_chat_clear():
    """清空 Agent 会话历史"""
    session_key = f"user_{current_user.id}"
    _agent_sessions.pop(session_key, None)
    return jsonify({"success": True})


@app.route("/api/chat/history")
@login_required
def api_chat_history():
    """获取 Agent 会话历史"""
    session_key = f"user_{current_user.id}"
    history = _agent_sessions.get(session_key, [])
    return jsonify({"success": True, "data": history})


@app.route("/api/memory/stats")
@login_required
def api_memory_stats():
    """获取记忆统计"""
    from .agent_memory import get_memory_stats
    stats = get_memory_stats(current_user.id)
    return jsonify({"success": True, "data": stats})


@app.route("/api/memory/list")
@login_required
def api_memory_list():
    """获取记忆列表"""
    from .agent_memory import get_memories
    memory_type = request.args.get("type")
    memories = get_memories(current_user.id, memory_type)
    return jsonify({"success": True, "data": memories})


# ══════════════════════════════════════════
# 候选人管道 API
# ══════════════════════════════════════════

@app.route("/api/pipeline/<int:job_id>")
@login_required
def api_get_pipeline(job_id):
    """获取候选人管道状态（各阶段分组 + 统计）"""
    stages = get_candidates_by_stage(current_user.id, job_id)
    summary = get_pipeline_summary(current_user.id, job_id)
    return jsonify({"success": True, "summary": summary, "stages": stages})


@app.route("/api/pipeline/update", methods=["POST"])
@login_required
def api_update_pipeline():
    """更新候选人管道状态，自动同步到 Obsidian"""
    from .knowledge import sync_pipeline_to_obsidian

    data = request.get_json()
    job_id = data.get("job_id")
    candidate_name = data.get("candidate_name", "")
    stage = data.get("stage", "")
    reason = data.get("reason", "")
    reject_stage = data.get("reject_stage", "")
    company_rejected = data.get("company_rejected", "")

    if not job_id or not candidate_name or not stage:
        return jsonify({"success": False, "error": "缺少参数"})

    # 查找候选人
    feedbacks = get_interview_feedbacks(current_user.id, job_id)
    target = None
    for f in feedbacks:
        if candidate_name in (f.get("candidate_name", ""), ""):
            target = f
            break
    if not target:
        return jsonify({"success": False, "error": f"未找到候选人: {candidate_name}"})

    ok = update_candidate_pipeline(
        target["id"], stage, reason=reason,
        reject_stage=reject_stage, company_rejected=company_rejected,
    )

    if ok:
        # 知识引擎：从反馈中学习
        try:
            from .knowledge_engine import learn_from_feedback
            learn_from_feedback(
                job_id, candidate_name,
                ai_score=target.get("ai_score", 0),
                stage=stage, reason=reason,
                user_id=current_user.id,
            )
        except Exception as e:
            print(f"[知识引擎] 反馈学习失败: {e}")

        # 自动同步到 Obsidian
        try:
            settings = get_settings(current_user.id)
            job_folder = settings.get("obsidian_job_path", "")
            if job_folder:
                job = get_job(job_id)
                if job:
                    stages = get_candidates_by_stage(current_user.id, job_id)
                    summary = get_pipeline_summary(current_user.id, job_id)
                    sync_pipeline_to_obsidian(job_folder, job["name"], stages, summary)
        except Exception as e:
            print(f"[管道] Obsidian 同步失败: {e}")

        return jsonify({"success": True})
    return jsonify({"success": False, "error": "更新失败"})


@app.route("/api/my-api-key")
@login_required
def api_my_api_key():
    """获取当前用户的 API Key（供插件使用）"""
    settings = get_settings(current_user.id)
    key = settings.get("llm_api_key", "")
    if key:
        return jsonify({"success": True, "api_key": key})
    return jsonify({"success": False, "error": "未配置 LLM API Key，请先在设置中填写"})


# ══════════════════════════════════════════
# Chrome 插件 API（无需登录，用 API Key 认证）
# ══════════════════════════════════════════

def _get_user_from_api_key(request):
    """从 API Key 获取用户"""
    api_key = request.headers.get('X-API-Key', '') or request.args.get('api_key', '')
    if not api_key:
        return None
    from .models import get_db
    conn = get_db()
    try:
        row = conn.execute("SELECT user_id FROM settings WHERE llm_api_key = ?", (api_key,)).fetchone()
        if row:
            from .models import get_user_by_id
            return get_user_by_id(row["user_id"])
    finally:
        conn.close()
    return None


@app.route("/api/extension/jobs")
def api_extension_jobs():
    """插件获取岗位列表（含搜索策略）（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401
    jobs = get_jobs(user.id)
    result = []
    for j in jobs:
        strategies = get_search_strategies(user.id, j["id"])
        result.append({
            "id": j["id"],
            "name": j["name"],
            "keyword": j.get("keyword", ""),
            "strategies": strategies,
        })
    return jsonify({"success": True, "data": result})


@app.route("/api/extension/jobs", methods=["POST"])
def api_extension_jobs_create():
    """插件创建岗位（用 API Key 认证）
    
    接收 {name, keyword?, job_description?}，创建岗位并返回 id。
    exe/插件新增项目时调用此接口，实现与 web 端同步。
    """
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    name = data.get("name", "").strip()
    keyword = data.get("keyword", "").strip()
    desc = data.get("job_description", "").strip()

    if not name:
        return jsonify({"success": False, "error": "岗位名称不能为空"}), 400

    job_id = create_job(user.id, name, keyword, desc)

    # P0-1: 自动创建默认搜索策略
    if keyword:
        create_search_strategy(
            user.id, job_id, keyword,
            name=f"{name}-默认搜索",
            city=data.get("city", "上海").strip() or "上海",
            is_default=True,
        )

    # 知识引擎学习
    try:
        from .knowledge_engine import learn_from_job, search_and_learn_industry
        learn_from_job({"name": name, "keyword": keyword, "job_description": desc})
        search_and_learn_industry(name, user_id=user.id)
    except Exception as e:
        print(f"[知识引擎] 岗位学习失败: {e}")

    return jsonify({"success": True, "id": job_id})


@app.route("/api/extension/strategies/<int:job_id>")
def api_extension_strategies(job_id):
    """插件获取岗位的搜索策略列表（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401
    strategies = get_search_strategies(user.id, job_id)
    return jsonify({"success": True, "data": strategies})


@app.route("/api/extension/strategies/<int:job_id>", methods=["POST"])
def api_extension_strategies_create(job_id):
    """插件为岗位创建搜索策略（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "关键词不能为空"}), 400

    sid = create_search_strategy(
        user.id, job_id, keyword,
        name=data.get("name", "").strip() or keyword,
        city=data.get("city", "上海"),
        platform=data.get("platform", "liepin"),
        max_pages=min(int(data.get("max_pages", 3)), 5),
        is_default=data.get("is_default", False),
    )
    return jsonify({"success": True, "id": sid})


@app.route("/api/extension/score", methods=["POST"])
def api_extension_score():
    """插件提交简历进行评分（用 API Key 认证）
    
    接收简历全文，用 LLM 解析结构化信息 + 评分，一步到位。
    """
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    resume_data = data.get("resume", {})
    job_id = data.get("job_id")

    if not resume_data or not job_id:
        return jsonify({"success": False, "error": "缺少简历数据或岗位 ID"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    job_description = job.get("job_description", "")
    if not job_description:
        return jsonify({"success": False, "error": "该岗位未配置岗位描述"}), 400

    settings = get_settings(user.id)
    api_key = settings.get("llm_api_key", "")
    if not api_key:
        return jsonify({"success": False, "error": "未配置 LLM API Key"}), 400

    base_url = settings.get("llm_base_url", "https://api.deepseek.com")
    model = settings.get("llm_model", "deepseek-chat")

    # ── 第一步：用 LLM 解析简历全文，提取结构化信息 ──
    full_text = resume_data.get("full_text", "")
    if full_text:
        try:
            import httpx as _httpx
            parse_prompt = f"""你是一个简历解析专家。请从以下从猎聘网页提取的文本中，准确识别并提取简历信息。

注意：文本可能包含网页导航、按钮文字、广告等噪音，请忽略这些，只关注简历内容本身。

请返回严格的 JSON 格式：
{{
  "name": "候选人姓名（如果被脱敏如张*，就写张*）",
  "age": "年龄（如28岁，找不到就写空字符串）",
  "education": "最高学历（本科/硕士/博士/大专/MBA等）",
  "experience": "总工作年限（如5年经验）",
  "company": "当前或最近公司名称",
  "title": "当前或最近职位名称",
  "location": "所在城市",
  "salary": "当前或期望薪资（如20-30万）",
  "expect_city": "期望工作城市",
  "expect_position": "期望职位",
  "skills": ["技能标签1", "技能标签2"],
  "work_summary": "最近一段工作经历摘要，包含公司、职位、主要职责（100字以内）"
}}

简历文本：
{full_text[:10000]}"""

            resp = _httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是简历解析助手，只返回 JSON，不要其他内容。"},
                        {"role": "user", "content": parse_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1000,
                },
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            
            # 提取 JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                # 合并解析结果到简历数据
                for key in ["name", "age", "education", "experience", "company", "title",
                            "location", "salary", "expect_city", "expect_position", "skills", "work_summary"]:
                    if parsed.get(key) and (not resume_data.get(key) or resume_data.get(key) == ''):
                        resume_data[key] = parsed[key]
                print(f"[插件] LLM 解析成功: {resume_data.get('name', '?')}")
        except Exception as e:
            print(f"[插件] LLM 解析简历失败: {e}，使用基础数据")

    # ── 第二步：评分 ──
    config = {
        "llm": {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        },
        "job_description": job_description,
        "job_name": job["name"],
    }

    try:
        from .analyzer import ResumeAnalyzer
        analyzer = ResumeAnalyzer(config)
        result = analyzer.score_resume_100(resume_data, job_description)
        
        # ── 第三步：保存简历到数据库 ──
        try:
            save_scrape_results_dedup(user.id, int(job_id), [resume_data])
            print(f"[插件] 简历已保存: {resume_data.get('name', '?')}")
        except Exception as e:
            print(f"[插件] 保存简历失败: {e}")

        return jsonify({"success": True, "data": {"score100": result, "resume": resume_data}})
    except Exception as e:
        print(f"[插件评分] 失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/extension/submit_batch", methods=["POST"])
def api_extension_submit_batch():
    """插件批量提交简历（用 API Key 认证）
    
    接收一批简历数据，创建抓取任务，然后在后台进行评分。
    用于 Chrome 插件一次性提交多页抓取结果。
    """
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    job_id = data.get("job_id")
    resumes = data.get("resumes", [])

    if not job_id or not resumes:
        return jsonify({"success": False, "error": "缺少 job_id 或 resumes"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    # 创建抓取任务
    task_id = create_scrape_task(
        user.id, keyword=job.get("keyword", ""), city="",
        platform="extension", max_pages=1, job_id=int(job_id),
    )
    update_scrape_task(
        task_id, status="done", result_count=len(resumes),
        resumes_json=json.dumps(resumes, ensure_ascii=False),
        finished_at=datetime.now().isoformat(),
    )

    # 知识引擎：从简历中学习
    try:
        from .knowledge_engine import learn_from_resume
        for r in resumes:
            learn_from_resume(r, job_name=job.get("name", ""))
    except Exception as e:
        print(f"[知识引擎] 简历学习失败: {e}")

    # 后台评分
    settings = get_settings(user.id)
    api_key = settings.get("llm_api_key", "")
    if not api_key:
        return jsonify({"success": True, "task_id": task_id,
                        "message": f"已保存 {len(resumes)} 份简历，但未配置 API Key，跳过评分"})

    job_description = job.get("job_description", "")
    if not job_description:
        return jsonify({"success": True, "task_id": task_id,
                        "message": f"已保存 {len(resumes)} 份简历，但岗位未配置 JD，跳过评分"})

    # 启动后台评分线程
    score_task_id = create_score_task(user.id, task_id, job["name"], job_description, "100")
    config = {
        "llm": {"api_key": api_key,
                 "base_url": settings.get("llm_base_url", "https://api.deepseek.com"),
                 "model": settings.get("llm_model", "deepseek-chat")},
        "job_description": job_description,
        "job_name": job["name"],
    }
    thread = threading.Thread(
        target=_run_score_thread,
        args=(score_task_id, config, resumes, job_description, "100", user.id, int(job_id)),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True, "task_id": task_id, "score_task_id": score_task_id,
                    "message": f"已接收 {len(resumes)} 份简历，正在后台评分"})


@app.route("/api/extension/submit_webpage", methods=["POST"])
def api_extension_submit_webpage():
    """插件提交网页内容 → AI 整理为简历 → 评分 → 录入岗位"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    job_id = data.get("job_id")
    raw_content = data.get("content", {})

    if not job_id or not raw_content:
        return jsonify({"success": False, "error": "缺少 job_id 或 content"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    settings = get_settings(user.id)
    api_key = settings.get("llm_api_key", "")
    if not api_key:
        return jsonify({"success": False, "error": "未配置 LLM API Key"}), 400

    llm_config = {
        "api_key": api_key,
        "base_url": settings.get("llm_base_url", "https://api.deepseek.com"),
        "model": settings.get("llm_model", "deepseek-chat"),
    }
    job_description = job.get("job_description", "")

    # Step 1: AI 整理网页为结构化简历
    organized_resume = _organize_webpage_to_resume(llm_config, raw_content, job.get("name", ""), job_description)
    if not organized_resume:
        return jsonify({"success": False, "error": "AI 整理简历失败"}), 500

    # Step 2: 评分
    if job_description:
        try:
            from .analyzer import ResumeAnalyzer
            score_config = {"llm": llm_config, "job_description": job_description, "job_name": job.get("name", "")}
            try:
                from .knowledge_engine import build_scoring_context
                ctx = build_scoring_context(job.get("name", ""))
                if ctx: score_config["knowledge_context"] = ctx
            except Exception: pass
            analyzer = ResumeAnalyzer(score_config)
            organized_resume["score100"] = analyzer.score_resume_100(organized_resume, job_description)
        except Exception as e:
            print(f"[网页简历] 评分失败: {e}")
            organized_resume["score100"] = {"total_score": 0, "error": str(e)}

    # Step 3: 保存
    resumes_json = json.dumps([organized_resume], ensure_ascii=False)
    task_id = create_scrape_task(user.id, keyword=job.get("keyword", ""), city="", platform="webpage", max_pages=1, job_id=int(job_id))
    update_scrape_task(task_id, status="done", result_count=1, resumes_json=resumes_json, finished_at=datetime.now().isoformat())

    try:
        from .knowledge_engine import learn_from_resume
        learn_from_resume(organized_resume, job_name=job.get("name", ""))
    except Exception: pass

    score = organized_resume.get("score100", {}).get("total_score", 0)
    return jsonify({"success": True, "task_id": task_id, "resume": organized_resume,
                    "message": f"AI 已整理并评分：{organized_resume.get('name', '未知')} → {score}分"})


def _organize_webpage_to_resume(llm_config, raw_content, job_name, job_description):
    """用 LLM 将网页原始内容整理为结构化简历"""
    try:
        from openai import OpenAI
        client_kwargs = {"api_key": llm_config["api_key"]}
        if llm_config.get("base_url"):
            client_kwargs["base_url"] = llm_config["base_url"]
        client = OpenAI(**client_kwargs)

        title = raw_content.get("title", "")
        description = raw_content.get("description", "")
        paragraphs = raw_content.get("paragraphs", [])
        full_text = raw_content.get("full_text", "")
        source_url = raw_content.get("url", "")

        content_text = f"标题: {title}\n"
        if description:
            content_text += f"描述: {description}\n\n"
        if paragraphs:
            content_text += "正文段落:\n"
            for i, p in enumerate(paragraphs[:50], 1):
                content_text += f"{i}. {p}\n"
        elif full_text:
            content_text += f"\n全文:\n{full_text[:12000]}"

        system_prompt = """你是一个专业的简历整理助手。你会收到从网页抓取的原始内容，可能是候选人的在线简历、个人主页、LinkedIn页面等。
你的任务是：从原始内容中提取候选人的关键信息，整理成结构化的简历格式。如果某些信息缺失，留空即可，不要编造。
输出严格的 JSON 格式（不要输出其他内容）。"""

        user_prompt = f"""请将以下网页内容整理为结构化简历。
{f'目标岗位: {job_name}' if job_name else ''}
{f'岗位描述: {job_description[:500]}' if job_description else ''}

网页原始内容：
{content_text}

请输出 JSON：
{{
  "name": "候选人姓名",
  "title": "当前职位",
  "company": "当前公司",
  "education": "最高学历",
  "school": "毕业院校",
  "major": "专业",
  "experience": "工作年限",
  "location": "所在城市",
  "expect_city": "期望城市",
  "expect_position": "期望职位",
  "salary": "期望薪资",
  "skill_tags": ["技能1", "技能2"],
  "phone": "手机号",
  "email": "邮箱",
  "summary": "个人简介（100-200字）",
  "work_experience": [{"company": "公司名", "position": "职位", "period": "时间段", "description": "工作描述"}],
  "education_detail": [{"school": "学校名", "major": "专业", "degree": "学历", "period": "时间段"}],
  "projects": [{"name": "项目名", "role": "角色", "period": "时间段", "description": "项目描述"}],
  "organized_text": "整理后的完整简历文本（Markdown格式，适合人类阅读）"
}}"""

        response = client.chat.completions.create(
            model=llm_config.get("model", "deepseek-chat"),
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.1, max_tokens=4000, response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        result["detail_url"] = source_url
        result["source"] = "webpage"
        result["source_url"] = source_url
        result["full_text"] = full_text[:15000] if full_text else ""
        result["scraped_at"] = raw_content.get("timestamp", "")
        if "organized_text" not in result:
            result["organized_text"] = _build_organized_text(result)
        return result
    except Exception as e:
        print(f"[LLM整理简历] 失败: {e}")
        return {"name": raw_content.get("title", "未知")[:20], "title": raw_content.get("title", ""),
                "detail_url": raw_content.get("url", ""), "source": "webpage", "source_url": raw_content.get("url", ""),
                "full_text": raw_content.get("full_text", "")[:15000],
                "organized_text": raw_content.get("full_text", "")[:8000], "scraped_at": raw_content.get("timestamp", "")}


def _build_organized_text(resume):
    """从结构化简历数据构建可读文本"""
    lines = []
    if resume.get("name"): lines.append(f"# {resume['name']}")
    if resume.get("title"): lines.append(f"**{resume['title']}**")
    if resume.get("company"): lines.append(f"现公司：{resume['company']}")
    lines.append("")
    info = []
    for k, label in [("education", "学历"), ("school", "院校"), ("experience", "经验"), ("location", "城市")]:
        if resume.get(k): info.append(f"{label}：{resume[k]}")
    if info: lines.append(" | ".join(info)); lines.append("")
    if resume.get("summary"): lines.append("## 个人简介"); lines.append(resume["summary"]); lines.append("")
    if resume.get("work_experience"):
        lines.append("## 工作经历")
        for we in resume["work_experience"]:
            lines.append(f"**{we.get('company','')}** - {we.get('position','')} ({we.get('period','')})")
            if we.get('description'): lines.append(we['description'])
            lines.append("")
    if resume.get("projects"):
        lines.append("## 项目经历")
        for p in resume["projects"]:
            lines.append(f"**{p.get('name','')}** - {p.get('role','')} ({p.get('period','')})")
            if p.get('description'): lines.append(p['description'])
            lines.append("")
    if resume.get("education_detail"):
        lines.append("## 教育经历")
        for ed in resume["education_detail"]:
            lines.append(f"**{ed.get('school','')}** - {ed.get('major','')} - {ed.get('degree','')} ({ed.get('period','')})")
            lines.append("")
    if resume.get("skill_tags"): lines.append("## 技能标签"); lines.append("、".join(resume["skill_tags"]))
    return "\n".join(lines)


@app.route("/api/extension/filter_rules/<int:job_id>")
def api_extension_filter_rules(job_id):
    """插件获取岗位的筛选规则（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    job = get_job(job_id)
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    # 从 JD 提取筛选规则
    from .jd_analyzer import extract_filter_rules_from_jd
    rules = extract_filter_rules_from_jd({}, job.get("job_description", ""))

    return jsonify({"success": True, "rules": rules, "job_name": job["name"]})


@app.route("/api/extension/resumes/<int:job_id>")
def api_extension_resumes(job_id):
    """插件获取岗位下的简历列表（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    job = get_job(job_id)
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resumes = get_resumes_by_job(user.id, job_id)

    # 获取评分数据
    conn = get_db()
    try:
        score_row = conn.execute(
            """SELECT scored_json FROM score_tasks
               WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
               AND job_name = (SELECT name FROM jobs WHERE id = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user.id, job_id),
        ).fetchone()
    finally:
        conn.close()

    score_map = {}
    if score_row and score_row["scored_json"]:
        for sr in json.loads(score_row["scored_json"]):
            key = sr.get("detail_url", "") or (sr.get("name", "") + sr.get("title", ""))
            if key:
                score_map[key] = sr

    result = []
    for r in resumes:
        key = r.get("detail_url", "") or (r.get("name", "") + r.get("title", ""))
        if key in score_map:
            sr = score_map[key]
            if "score100" in sr:
                r["score100"] = sr["score100"]
        result.append(r)

    return jsonify({"success": True, "data": result})


# ══════════════════════════════════════════
# 知识引擎 API
# ══════════════════════════════════════════

@app.route("/api/knowledge/stats")
@login_required
def api_knowledge_stats():
    from .knowledge_engine import get_knowledge_stats
    stats = get_knowledge_stats()
    return jsonify({"success": True, "data": stats})


@app.route("/api/knowledge/refresh", methods=["POST"])
@login_required
def api_knowledge_refresh():
    from .knowledge_engine import search_and_learn_industry, cleanup_expired_knowledge
    jobs = get_jobs(current_user.id)
    for job in jobs:
        search_and_learn_industry(job["name"], user_id=current_user.id)
    cleanup_expired_knowledge()
    return jsonify({"success": True, "message": f"已为 {len(jobs)} 个岗位触发知识刷新"})


# 注：知识库的插件版接口在 /api/extension/knowledge/* 路径下（见上方 extension 区域）


@app.route("/api/knowledge/query")
@login_required
def api_knowledge_query():
    from .knowledge_engine import query_knowledge
    ktype = request.args.get("type")
    category = request.args.get("category")
    keyword = request.args.get("keyword")
    limit = int(request.args.get("limit", 20))
    results = query_knowledge(knowledge_type=ktype, category=category, keyword=keyword, limit=limit)
    return jsonify({"success": True, "data": results})


# ══════════════════════════════════════════
# 插件 AI 对话接口
# ══════════════════════════════════════════

_extension_sessions = {}


@app.route("/api/extension/chat", methods=["POST"])
def api_extension_chat():
    """插件 AI 对话（用 API Key 认证，功能与网页端一致）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"success": False, "error": "消息不能为空"}), 400

    # 会话历史
    session_key = f"ext_{user.id}"
    if session_key not in _extension_sessions:
        _extension_sessions[session_key] = []
    history = _extension_sessions[session_key]
    history.append({"role": "user", "content": message})
    if len(history) > 40:
        history = history[-40:]
        _extension_sessions[session_key] = history

    # 调用 Agent
    from .agent import run_agent
    import asyncio

    try:
        result = asyncio.run(run_agent(
            user_id=user.id,
            goal=message,
            history=history[:-1],
        ))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    reply = result.get("summary", "处理完成")
    history.append({"role": "assistant", "content": reply})
    _extension_sessions[session_key] = history

    return jsonify({
        "success": True,
        "reply": reply,
        "plan": result.get("plan", {}),
        "results": result.get("results", []),
    })


@app.route("/api/extension/chat/clear", methods=["POST"])
def api_extension_chat_clear():
    """清空插件对话历史"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401
    session_key = f"ext_{user.id}"
    _extension_sessions.pop(session_key, None)
    return jsonify({"success": True})


@app.route("/api/extension/pipeline/<int:job_id>")
def api_extension_pipeline(job_id):
    """插件获取候选人管道状态（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    job = get_job(job_id)
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    stages = get_candidates_by_stage(user.id, job_id)
    summary = get_pipeline_summary(user.id, job_id)
    return jsonify({"success": True, "summary": summary, "stages": stages})


@app.route("/api/extension/pipeline/<int:job_id>/add", methods=["POST"])
def api_extension_pipeline_add(job_id):
    """插件将候选人加入管道（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    job = get_job(job_id)
    if not job or job["user_id"] != user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    data = request.json or {}
    candidate_name = data.get("name", "未知")
    detail_url = data.get("detail_url", "")
    ai_score = data.get("score", 0)

    resume_key = detail_url or candidate_name

    conn = get_db()
    try:
        # 检查是否已存在
        existing = conn.execute(
            "SELECT id FROM interview_feedback WHERE user_id = ? AND job_id = ? AND resume_key = ?",
            (user.id, job_id, resume_key),
        ).fetchone()
        if existing:
            return jsonify({"success": True, "message": "该候选人已在管道中", "id": existing["id"]})

        cursor = conn.execute(
            """INSERT INTO interview_feedback
               (user_id, job_id, candidate_name, resume_key, ai_score, feedback_type, reason, pipeline_stage)
               VALUES (?, ?, ?, ?, ?, 'recommend', '插件手动加入', 'recommended')""",
            (user.id, job_id, candidate_name, resume_key, ai_score),
        )
        conn.commit()
        return jsonify({"success": True, "id": cursor.lastrowid, "message": f"已将 {candidate_name} 加入管道"})
    finally:
        conn.close()


@app.route("/api/extension/pipeline/<int:job_id>/delete", methods=["POST"])
def api_extension_pipeline_delete(job_id):
    """插件删除管道候选人（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    feedback_id = data.get("id")
    if not feedback_id:
        return jsonify({"success": False, "error": "缺少候选人 ID"}), 400

    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM interview_feedback WHERE id = ? AND user_id = ? AND job_id = ?",
            (feedback_id, user.id, job_id),
        )
        conn.commit()
        return jsonify({"success": True, "message": "已删除"})
    finally:
        conn.close()


@app.route("/api/extension/knowledge/stats")
def api_extension_knowledge_stats():
    """插件获取知识库统计（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401
    from .knowledge_engine import get_knowledge_stats
    stats = get_knowledge_stats()
    return jsonify({"success": True, "data": stats})


@app.route("/api/extension/knowledge/refresh", methods=["POST"])
def api_extension_knowledge_refresh():
    """插件刷新知识库（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401
    from .knowledge_engine import search_and_learn_industry, cleanup_expired_knowledge
    jobs = get_jobs(user.id)
    for job in jobs:
        search_and_learn_industry(job["name"], user_id=user.id)
    cleanup_expired_knowledge()
    return jsonify({"success": True, "message": f"已为 {len(jobs)} 个岗位触发知识刷新"})


@app.route("/api/extension/knowledge/list")
def api_extension_knowledge_list():
    """插件查询知识库条目（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401
    from .knowledge_engine import query_knowledge
    ktype = request.args.get("type")
    keyword = request.args.get("keyword")
    limit = int(request.args.get("limit", 50))
    results = query_knowledge(knowledge_type=ktype, keyword=keyword, limit=limit)
    return jsonify({"success": True, "data": results})


@app.route("/api/extension/knowledge/delete", methods=["POST"])
def api_extension_knowledge_delete():
    """插件删除知识条目（用 API Key 认证）"""
    user = _get_user_from_api_key(request)
    if not user:
        return jsonify({"success": False, "error": "无效的 API Key"}), 401

    data = request.json or {}
    knowledge_id = data.get("id")
    if not knowledge_id:
        return jsonify({"success": False, "error": "缺少知识 ID"}), 400

    conn = get_db()
    try:
        conn.execute("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
        conn.commit()
        return jsonify({"success": True, "message": "已删除"})
    finally:
        conn.close()


# ==================================================================
#  推荐报告（模板驱动）
# ------------------------------------------------------------------
#  报告内容只来自「简历原文」与「评分阶段已生成的内容」，
#  不调用 LLM 重新生成叙述性文字 —— 避免二次幻觉，也省一次调用。
# ==================================================================

def _resume_key(resume):
    """候选人唯一键：优先 detail_url，退化为 name+title"""
    return resume.get("detail_url", "") or (
        str(resume.get("name", "")) + str(resume.get("title", "") or resume.get("work_summary", "") or "")
    )


def _find_resume_and_score(user_id, job_id, resume_key):
    """按 key 找简历 + 对应评分；返回 (resume, score100, error)"""
    resumes = get_resumes_by_job(user_id, job_id) or []
    target = None
    for r in resumes:
        if _resume_key(r) == resume_key:
            target = r
            break
    if target is None:
        try:
            idx = int(resume_key)
            if 0 <= idx < len(resumes):
                target = resumes[idx]
        except (TypeError, ValueError):
            pass
    if target is None:
        return None, None, "未找到候选人（key=%s）" % resume_key

    score100 = {}
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT scored_json FROM score_tasks
               WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
               AND job_name = (SELECT name FROM jobs WHERE id = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, job_id),
        ).fetchone()
        if row and row["scored_json"]:
            for sr in json.loads(row["scored_json"]):
                if _resume_key(sr) == _resume_key(target):
                    score100 = sr.get("score100") or {}
                    break
    finally:
        conn.close()

    return target, score100, None


@app.route("/api/report/candidate", methods=["POST"])
@login_required
def api_report_candidate():
    """生成单个候选人的推荐报告（JSON，供前端预览）"""
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key", "")
    template_id = data.get("template_id", "headhunter_v1")

    if not job_id or resume_key == "":
        return jsonify({"success": False, "error": "缺少 job_id 或 resume_key"}), 400

    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resume, score100, err = _find_resume_and_score(current_user.id, job_id, resume_key)
    if err:
        return jsonify({"success": False, "error": err}), 404

    try:
        from .report_engine import build_report, render_markdown
        report = build_report(resume, score100, job=job, template_id=template_id)
        return jsonify({
            "success": True,
            "data": {
                "markdown": render_markdown(report),
                "sections": report["sections"],
                "validation": report["validation"],
                "meta": report["meta"],
            },
        })
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": "模板不存在: %s" % e}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "生成失败: %s" % e}), 500


@app.route("/api/report/candidate/download", methods=["POST"])
@login_required
def api_report_candidate_download():
    """下载推荐报告（.docx）"""
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key", "")
    template_id = data.get("template_id", "headhunter_v1")

    if not job_id or resume_key == "":
        return jsonify({"success": False, "error": "缺少参数"}), 400

    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resume, score100, err = _find_resume_and_score(current_user.id, job_id, resume_key)
    if err:
        return jsonify({"success": False, "error": err}), 404

    try:
        from .report_engine import build_report, render_docx
        report = build_report(resume, score100, job=job, template_id=template_id)

        out_dir = Path("data/backups")
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(resume.get("name", "候选人")))[:20]
        fname = "推荐报告_%s_%s.docx" % (safe_name, datetime.now().strftime("%m%d%H%M"))
        # 注意：必须是绝对路径。Flask 的 send_file 会把相对路径解析到 app.root_path
        #（也就是 app/ 目录）下，而不是当前工作目录，会报"找不到路径"。
        fpath = (out_dir / fname).resolve()
        render_docx(report, str(fpath))

        return send_file(str(fpath), as_attachment=True, download_name=fname)
    except Exception as e:
        return jsonify({"success": False, "error": "生成失败: %s" % e}), 500


@app.route("/api/report/templates")
@login_required
def api_report_templates():
    """列出可用报告模板"""
    try:
        from .report_engine import list_templates
        return jsonify({"success": True, "data": list_templates()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
