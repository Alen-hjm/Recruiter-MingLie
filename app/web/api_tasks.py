# -*- coding: utf-8 -*-
"""任务类 API：抓取、评分、报告生成与下载。

这个文件只负责「校验参数 + 落一条任务记录 + 交给 services 起后台线程」。
真正的执行逻辑在 app/services/ 里，路由保持轻薄。
"""

import json
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file
from flask_login import current_user, login_required

from .. import config
from ..models import (
    create_scrape_task,
    create_score_task,
    get_job,
    get_scrape_task,
    get_scrape_tasks,
    get_score_task,
    get_score_tasks,
    get_search_strategy,
    get_settings,
    get_unscored_resumes,
)
from ..services.scoring import build_score_config, start_score_task
from ..services.scraping import build_scrape_config, start_scrape_task
from ._helpers import find_resume_and_score, safe_filename

bp = Blueprint("api_tasks", __name__, url_prefix="/api")


# ================================================================== 抓取
@bp.route("/scrape", methods=["POST"])
@login_required
def api_scrape_start():
    data = request.json or {}
    job_id = data.get("job_id")
    strategy_id = data.get("strategy_id")

    if job_id is None:
        return jsonify({"success": False, "error": "请选择归属岗位"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    # 关键词来源优先级：搜索策略 > 前端传入 > 岗位配置
    if strategy_id:
        strategy = get_search_strategy(int(strategy_id))
        if not strategy or strategy["user_id"] != current_user.id:
            return jsonify({"success": False, "error": "搜索策略不存在"}), 404
        keyword = strategy["keyword"]
        city = strategy.get("city") or "上海"
        platform = strategy.get("platform", "liepin")
        max_pages = min(int(strategy.get("max_pages", 3)), config.SCRAPE_MAX_PAGES_LIMIT)
    else:
        platform = data.get("platform", "liepin")
        max_pages = min(int(data.get("max_pages", 3)), config.SCRAPE_MAX_PAGES_LIMIT)
        keyword = (data.get("keyword") or "").strip() or (job.get("keyword") or "").strip()
        city = (data.get("city") or "").strip() or "上海"

    search_mode = data.get("search_mode", "auto")
    if not keyword and search_mode != "manual":
        return jsonify({"success": False, "error": "请填写关键词或切换为手动搜索模式"}), 400

    task_id = create_scrape_task(
        current_user.id, keyword, city, platform, max_pages, job_id=int(job_id)
    )
    scrape_config = build_scrape_config(
        get_settings(current_user.id) or {}, keyword, city, max_pages,
        search_mode=search_mode, user_id=current_user.id,
    )
    start_scrape_task(task_id, scrape_config, user_id=current_user.id, job_id=int(job_id))

    return jsonify({"success": True, "task_id": task_id})


@bp.route("/scrape/<int:task_id>")
@login_required
def api_scrape_status(task_id):
    task = get_scrape_task(task_id)
    if not task or task["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "任务不存在"}), 404

    result = {
        "id": task["id"], "status": task["status"],
        "keyword": task["keyword"], "city": task["city"],
        "result_count": task["result_count"], "error_msg": task["error_msg"],
        "created_at": task["created_at"], "finished_at": task["finished_at"],
    }
    if task["status"] == "done" and task["resumes_json"]:
        result["resumes"] = json.loads(task["resumes_json"])
    return jsonify({"success": True, "data": result})


@bp.route("/scrape/list")
@login_required
def api_scrape_list():
    tasks = get_scrape_tasks(current_user.id, limit=20)
    for t in tasks:
        t.pop("resumes_json", None)  # 列表不需要完整简历，省带宽
    return jsonify({"success": True, "data": tasks})


# ================================================================== 评分
@bp.route("/score", methods=["POST"])
@login_required
def api_score_start():
    data = request.json or {}
    job_id = data.get("job_id")
    mode = data.get("mode", "100")

    if job_id is None:
        return jsonify({"success": False, "error": "请选择岗位"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    job_description = job.get("job_description") or ""
    if not job_description:
        return jsonify({"success": False, "error": "该岗位未配置岗位描述，请先去岗位管理编辑"}), 400

    resumes, already_scored = get_unscored_resumes(current_user.id, int(job_id))
    if not resumes and already_scored == 0:
        return jsonify({"success": False, "error": "该岗位下没有简历，请先抓取"}), 400
    if not resumes:
        return jsonify({
            "success": False,
            "error": "该岗位下 %d 份简历已全部评分，无需重复评分" % already_scored,
        }), 400

    llm = config.resolve_llm_config(get_settings(current_user.id) or {})
    if not llm["api_key"]:
        return jsonify({"success": False, "error": "请先在「设置」中配置 LLM API Key，否则无法评分"}), 400

    score_task_id, _ = start_score_task(
        current_user.id, int(job_id), job["name"], job_description, resumes, mode,
    )

    msg = "开始评分 %d 份简历" % len(resumes)
    if already_scored:
        msg += "（跳过已评分 %d 份）" % already_scored
    return jsonify({
        "success": True, "task_id": score_task_id,
        "resume_count": len(resumes), "skipped": already_scored, "message": msg,
    })


@bp.route("/score/<int:task_id>")
@login_required
def api_score_status(task_id):
    task = get_score_task(task_id)
    if not task or task["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "任务不存在"}), 404

    result = {
        "id": task["id"], "status": task["status"], "job_name": task["job_name"],
        "mode": task["mode"], "passed_count": task["passed_count"],
        "total_count": task["total_count"], "error_msg": task["error_msg"],
        "created_at": task["created_at"], "finished_at": task["finished_at"],
    }
    if task["status"] == "done" and task["scored_json"]:
        result["scored"] = json.loads(task["scored_json"])
    return jsonify({"success": True, "data": result})


@bp.route("/score/list")
@login_required
def api_score_list():
    tasks = get_score_tasks(current_user.id, limit=20)
    for t in tasks:
        t.pop("scored_json", None)
    return jsonify({"success": True, "data": tasks})


# ================================================================== 报告
@bp.route("/report/<int:score_task_id>")
@login_required
def api_report(score_task_id):
    """按评分任务生成 Markdown 汇总报告"""
    task = get_score_task(score_task_id)
    if not task or task["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "评分任务不存在"}), 404
    if task["status"] != "done":
        return jsonify({"success": False, "error": "评分未完成"}), 400

    scored = json.loads(task["scored_json"]) if task["scored_json"] else []
    mode = task["mode"] or "100"
    from ..pipeline_report import generate_markdown_report
    return jsonify({
        "success": True,
        "data": {"report": generate_markdown_report(scored, mode), "mode": mode},
    })


@bp.route("/report/candidate", methods=["POST"])
@login_required
def api_report_candidate():
    """单个候选人的推荐报告（模板驱动，JSON，供前端预览）"""
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key", "")
    template_id = data.get("template_id", "headhunter_v1")

    if job_id is None or resume_key == "":
        return jsonify({"success": False, "error": "缺少 job_id 或 resume_key"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resume, score100, err = find_resume_and_score(current_user.id, int(job_id), resume_key)
    if err:
        return jsonify({"success": False, "error": err}), 404

    try:
        from ..report_engine import build_report, render_markdown
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


@bp.route("/report/candidate/download", methods=["POST"])
@login_required
def api_report_candidate_download():
    """下载推荐报告（.docx）"""
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key", "")
    template_id = data.get("template_id", "headhunter_v1")

    if job_id is None or resume_key == "":
        return jsonify({"success": False, "error": "缺少参数"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resume, score100, err = find_resume_and_score(current_user.id, int(job_id), resume_key)
    if err:
        return jsonify({"success": False, "error": err}), 404

    try:
        from ..report_engine import build_report, render_docx
        report = build_report(resume, score100, job=job, template_id=template_id)

        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        fname = "推荐报告_%s_%s.docx" % (
            safe_filename(resume.get("name", "候选人")),
            datetime.now().strftime("%m%d%H%M"),
        )
        # 必须用绝对路径：Flask 的 send_file 会把相对路径解析到 app.root_path
        #（即 app/ 目录）而不是当前工作目录，会报「找不到路径」。
        fpath = (config.BACKUP_DIR / fname).resolve()
        render_docx(report, str(fpath))
        return send_file(str(fpath), as_attachment=True, download_name=fname)
    except Exception as e:
        return jsonify({"success": False, "error": "生成失败: %s" % e}), 500


@bp.route("/report/templates")
@login_required
def api_report_templates():
    try:
        from ..report_engine import list_templates
        return jsonify({"success": True, "data": list_templates()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
