# -*- coding: utf-8 -*-
"""核心 API：统计、设置、岗位、搜索策略、简历编辑与删除。"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import config
from ..models import (
    create_job,
    create_search_strategy,
    delete_job,
    delete_resume,
    edit_resume,
    delete_search_strategy,
    get_job,
    get_jobs,
    get_resumes_by_job,
    get_search_strategies,
    get_search_strategy,
    get_settings,
    get_user_stats,
    update_job,
    update_search_strategy,
    update_settings,
)
from ._helpers import latest_score_map, merge_scores_into_resumes

bp = Blueprint("api_core", __name__, url_prefix="/api")

# 允许通过设置接口写入的字段白名单（防越权写任意列）
_SETTING_FIELDS = [
    "llm_api_key", "llm_base_url", "llm_model",
    "default_city", "default_pages",
    "obsidian_kb_path", "obsidian_job_path",
]

# 简历可编辑字段白名单
_RESUME_EDITABLE = [
    "name", "activity", "age", "experience", "education",
    "company", "title", "location", "salary",
]


def _owned_job(job_id: int):
    """取岗位并校验归属；返回 (job, error_response)"""
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return None, (jsonify({"success": False, "error": "岗位不存在"}), 404)
    return job, None


# ------------------------------------------------------------------ 统计
@bp.route("/stats")
@login_required
def api_stats():
    return jsonify({"success": True, "data": get_user_stats(current_user.id)})


# ------------------------------------------------------------------ 设置
@bp.route("/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    if request.method == "GET":
        settings = get_settings(current_user.id) or {}
        key = settings.get("llm_api_key") or ""
        # 只返回脱敏串，绝不回传明文 —— 明文 Key 一旦能在页面上读到，
        # 就等于任何能访问本机的人都拿得到，也让「密钥当凭据」的老路可复制。
        settings["llm_api_key"] = ""
        if key:
            settings["llm_api_key_masked"] = (
                key[:6] + "***" + key[-4:] if len(key) > 10 else "***"
            )
        # 环境变量兜底是否可用，前端可据此提示「无需在页面填写」
        settings["llm_key_from_env"] = bool(config.LLM_API_KEY) and not key
        settings["llm_base_url_default"] = config.LLM_BASE_URL
        settings["llm_model_default"] = config.LLM_MODEL
        return jsonify({"success": True, "data": settings})

    data = request.json or {}
    updates = {
        k: v for k, v in data.items()
        if k in _SETTING_FIELDS and v is not None
        # 空串会让「留空 = 不修改」的语义失效，把已保存的 Key 清掉
        and not (k == "llm_api_key" and v == "")
    }
    if updates:
        update_settings(current_user.id, **updates)
    return jsonify({"success": True})


# ------------------------------------------------------------------ 岗位
@bp.route("/jobs", methods=["GET"])
@login_required
def api_jobs_list():
    jobs = get_jobs(current_user.id)
    for j in jobs:
        j["resume_count"] = len(get_resumes_by_job(current_user.id, j["id"]))
        j["strategies"] = get_search_strategies(current_user.id, j["id"])
    return jsonify({"success": True, "data": jobs})


@bp.route("/jobs", methods=["POST"])
@login_required
def api_jobs_create():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    keyword = (data.get("keyword") or "").strip()
    desc = (data.get("job_description") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "岗位名称不能为空"}), 400

    job_id = create_job(current_user.id, name, keyword, desc)

    if keyword:
        create_search_strategy(
            current_user.id, job_id, keyword,
            name="%s-默认搜索" % name,
            city=(data.get("city") or "上海").strip() or "上海",
            is_default=True,
        )

    _learn_from_new_job(current_user.id, name, keyword, desc)
    return jsonify({"success": True, "id": job_id})


def _learn_from_new_job(user_id: int, name: str, keyword: str, desc: str) -> None:
    """岗位创建后的两个副作用：知识引擎学习 + Obsidian 同步。

    两者都是可选增强，失败只打印不抛 —— 岗位本身已经建好了，
    不应该因为知识库路径写错就让用户看到 500。
    """
    try:
        from ..knowledge_engine import learn_from_job, search_and_learn_industry
        learn_from_job({"name": name, "keyword": keyword, "job_description": desc})
        search_and_learn_industry(name, user_id=user_id)
    except Exception as e:
        print("[知识引擎] 岗位学习失败: %s" % e)

    try:
        settings = get_settings(user_id) or {}
        job_folder = settings.get("obsidian_job_path", "")
        if job_folder:
            from ..knowledge import sync_job_to_obsidian
            sync_job_to_obsidian(job_folder, {"name": name, "keyword": keyword, "job_description": desc})
            print("[Obsidian] 已同步岗位: %s" % name)
    except Exception as e:
        print("[Obsidian] 同步失败: %s" % e)


@bp.route("/jobs/<int:job_id>", methods=["PUT"])
@login_required
def api_jobs_update(job_id):
    _, err = _owned_job(job_id)
    if err:
        return err
    data = request.json or {}
    updates = {k: v for k, v in data.items() if k in ("name", "keyword", "job_description")}
    if updates:
        update_job(job_id, **updates)
    return jsonify({"success": True})


@bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@login_required
def api_jobs_delete(job_id):
    _, err = _owned_job(job_id)
    if err:
        return err
    delete_job(job_id)
    return jsonify({"success": True})


@bp.route("/jobs/<int:job_id>/resumes")
@login_required
def api_jobs_resumes(job_id):
    job, err = _owned_job(job_id)
    if err:
        return err
    resumes = get_resumes_by_job(current_user.id, job_id)
    merge_scores_into_resumes(resumes, latest_score_map(current_user.id, job_id))
    return jsonify({"success": True, "data": resumes, "count": len(resumes), "job": job})


# ------------------------------------------------------------------ 简历编辑/删除
@bp.route("/resumes/edit", methods=["POST"])
@login_required
def api_resume_edit():
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key")
    updates = data.get("updates") or {}

    if job_id is None or not resume_key:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    _, err = _owned_job(int(job_id))
    if err:
        return err

    filtered = {k: v for k, v in updates.items() if k in _RESUME_EDITABLE}
    if not filtered:
        return jsonify({"success": False, "error": "没有可更新的字段"}), 400

    if edit_resume(current_user.id, int(job_id), resume_key, filtered):
        return jsonify({"success": True, "message": "已更新"})
    return jsonify({"success": False, "error": "未找到该简历"}), 404


@bp.route("/resumes/delete", methods=["POST"])
@login_required
def api_resume_delete():
    data = request.json or {}
    job_id = data.get("job_id")
    resume_key = data.get("resume_key")

    if job_id is None or not resume_key:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    _, err = _owned_job(int(job_id))
    if err:
        return err

    if delete_resume(current_user.id, int(job_id), resume_key):
        return jsonify({"success": True, "message": "已删除"})
    return jsonify({"success": False, "error": "未找到该简历"}), 404


# ------------------------------------------------------------------ 搜索策略
@bp.route("/jobs/<int:job_id>/strategies")
@login_required
def api_strategies_list(job_id):
    _, err = _owned_job(job_id)
    if err:
        return err
    return jsonify({"success": True, "data": get_search_strategies(current_user.id, job_id)})


@bp.route("/jobs/<int:job_id>/strategies", methods=["POST"])
@login_required
def api_strategies_create(job_id):
    _, err = _owned_job(job_id)
    if err:
        return err

    data = request.json or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "关键词不能为空"}), 400

    sid = create_search_strategy(
        current_user.id, job_id, keyword,
        name=(data.get("name") or "").strip() or keyword,
        city=(data.get("city") or "").strip() or "上海",
        platform=data.get("platform", "liepin"),
        max_pages=min(int(data.get("max_pages", 3)), config.SCRAPE_MAX_PAGES_LIMIT),
        is_default=data.get("is_default", False),
    )
    return jsonify({"success": True, "id": sid})


def _owned_strategy(strategy_id: int):
    s = get_search_strategy(strategy_id)
    if not s or s["user_id"] != current_user.id:
        return None, (jsonify({"success": False, "error": "策略不存在"}), 404)
    return s, None


@bp.route("/strategies/<int:strategy_id>", methods=["PUT"])
@login_required
def api_strategies_update(strategy_id):
    _, err = _owned_strategy(strategy_id)
    if err:
        return err

    data = request.json or {}
    allowed = ["name", "keyword", "city", "platform", "max_pages", "is_default"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "is_default" in updates:
        updates["is_default"] = 1 if updates["is_default"] else 0
    if "max_pages" in updates:
        updates["max_pages"] = min(int(updates["max_pages"]), config.SCRAPE_MAX_PAGES_LIMIT)
    if updates:
        update_search_strategy(strategy_id, **updates)
    return jsonify({"success": True})


@bp.route("/strategies/<int:strategy_id>", methods=["DELETE"])
@login_required
def api_strategies_delete(strategy_id):
    _, err = _owned_strategy(strategy_id)
    if err:
        return err
    delete_search_strategy(strategy_id)
    return jsonify({"success": True})
