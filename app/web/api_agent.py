# -*- coding: utf-8 -*-
"""Agent 与人才运营 API：对话、记忆、候选人分类、面试反馈、管道、知识库、JD 拆解。"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import config
from ..models import (
    create_interview_feedback,
    get_candidates_by_stage,
    get_db,
    get_interview_feedbacks,
    get_job,
    get_jobs,
    get_pipeline_summary,
    get_resumes_by_job,
    get_settings,
    mark_feedback_synced,
    update_candidate_pipeline,
)
from ._helpers import extract_total_score, latest_score_map, merge_scores_into_resumes, resume_key

bp = Blueprint("api_agent", __name__, url_prefix="/api")

# Agent 会话历史：进程内内存，重启即丢。
# 前端已经会通过 /api/chat/history 回补，所以这里不做持久化；
# 若要长期保留，应该落到一张 chat_messages 表，而不是继续加内存字典。
_agent_sessions: dict[str, list] = {}

# 单个会话保留的最大消息条数（user + assistant 各算一条）
_MAX_HISTORY = 40
# 每次送给模型的最近轮数
_HISTORY_WINDOW = 5


def _session_key(user_id: int) -> str:
    return "user_%s" % user_id


def _get_history(user_id: int) -> list:
    return _agent_sessions.get(_session_key(user_id), [])


def _push_history(user_id: int, history: list) -> None:
    _agent_sessions[_session_key(user_id)] = history[-_MAX_HISTORY:]


def _run_agent_sync(user_id: int, message: str, history: list) -> dict:
    """同步执行 Agent（路由是同步上下文，这里自己管事件循环）"""
    from ..agent import run_agent
    return asyncio.run(run_agent(user_id=user_id, goal=message, history=history))


def _chat_turn(user_id: int, message: str) -> dict:
    """一轮对话：拼历史 → 调 Agent → 记历史"""
    history = _get_history(user_id)
    history.append({"role": "user", "content": message})
    _push_history(user_id, history)

    result = _run_agent_sync(user_id, message, history[:-1])  # 当前消息已在 goal 里

    reply = result.get("summary", "处理完成")
    history = _get_history(user_id)
    history.append({"role": "assistant", "content": reply})
    _push_history(user_id, history)
    return result


# ================================================================== Agent 对话
@bp.route("/chat", methods=["POST"])
@login_required
def api_chat():
    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "消息不能为空"}), 400

    try:
        result = _chat_turn(current_user.id, message)
    except Exception as e:
        print("[Agent] 执行失败: %s" % e)
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": True,
        "reply": result.get("summary", "处理完成"),
        "plan": result.get("plan", {}),
        "results": result.get("results", []),
        "is_chat": result.get("is_chat", False),
    })


@bp.route("/chat/clear", methods=["POST"])
@login_required
def api_chat_clear():
    _agent_sessions.pop(_session_key(current_user.id), None)
    return jsonify({"success": True})


@bp.route("/chat/history")
@login_required
def api_chat_history():
    return jsonify({"success": True, "data": _get_history(current_user.id)})


# ================================================================== 记忆
@bp.route("/memory/stats")
@login_required
def api_memory_stats():
    from ..agent_memory import get_memory_stats
    return jsonify({"success": True, "data": get_memory_stats(current_user.id)})


@bp.route("/memory/list")
@login_required
def api_memory_list():
    from ..agent_memory import get_memories
    return jsonify({
        "success": True,
        "data": get_memories(current_user.id, request.args.get("type")),
    })


# ================================================================== 候选人分类
@bp.route("/candidates/<int:job_id>")
@login_required
def api_candidates_list(job_id):
    """按「推荐 / 待定 / 已淘汰 / 已约面」分组岗位下的候选人"""
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    resumes = get_resumes_by_job(current_user.id, job_id)
    merge_scores_into_resumes(resumes, latest_score_map(current_user.id, job_id))

    conn = get_db()
    try:
        feedbacks = conn.execute(
            """SELECT candidate_name, resume_key, feedback_type, ai_score, reason
               FROM interview_feedback WHERE user_id = ? AND job_id = ?""",
            (current_user.id, job_id),
        ).fetchall()
    finally:
        conn.close()

    feedback_map = {fb["resume_key"] or fb["candidate_name"]: dict(fb) for fb in feedbacks}

    result = {"recommended": [], "pending": [], "rejected": [], "interviewed": []}
    for resume in resumes:
        key = resume_key(resume)
        resume["_key"] = key
        resume["_score"] = extract_total_score(resume)

        fb = feedback_map.get(key)
        if fb:
            resume["_feedback"] = fb
            if fb["feedback_type"] == "interview":
                result["interviewed"].append(resume)
            else:
                result["rejected"].append(resume)
        elif resume["_score"] >= 80:
            result["recommended"].append(resume)
        elif resume["_score"] >= 60:
            result["pending"].append(resume)
        else:
            result["rejected"].append(resume)

    for group in result.values():
        group.sort(key=lambda x: x.get("_score", 0), reverse=True)

    return jsonify({"success": True, "data": result, "job": job})


# ================================================================== 候选人操作
@bp.route("/candidate/action", methods=["POST"])
@login_required
def api_candidate_action():
    """约面 / 淘汰 两个动作，副作用（Obsidian、记忆学习）与主流程解耦"""
    data = request.json or {}
    action = data.get("action", "")
    job_id = data.get("job_id")
    candidate_name = data.get("candidate_name", "")
    resume_key_value = data.get("resume_key", "")
    ai_score = data.get("ai_score", 0)
    reason = (data.get("reason") or "").strip()

    if job_id is None or not action:
        return jsonify({"success": False, "error": "缺少参数"}), 400

    job = get_job(int(job_id))
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404

    job_folder = (get_settings(current_user.id) or {}).get("obsidian_job_path", "")

    if action == "interview":
        _record_interview(current_user.id, int(job_id), candidate_name, resume_key_value, ai_score)
        synced = _append_note(
            job_folder, job["name"], "面试安排", candidate_name, ai_score, "推荐约面"
        )
        return jsonify({"success": True, "action": "interview", "synced": synced})

    if action == "reject":
        if not reason:
            return jsonify({"success": False, "error": "请输入淘汰原因"}), 400

        fid = create_interview_feedback(
            current_user.id, int(job_id), candidate_name, resume_key_value,
            ai_score, "reject", reason,
        )

        synced = False
        if data.get("sync_to_obsidian"):
            synced = _append_note(
                job_folder, job["name"], "淘汰反馈", candidate_name, ai_score, reason
            )
            if synced:
                mark_feedback_synced(fid)

        # 淘汰原因沉淀为记忆，供后续评分参考
        try:
            from ..agent_memory import learn_from_feedback
            learn_from_feedback(current_user.id, int(job_id), [{
                "reason": reason,
                "candidate_name": candidate_name,
                "ai_score": ai_score,
            }])
        except Exception as e:
            print("[记忆] 学习失败: %s" % e)

        return jsonify({"success": True, "action": "reject", "id": fid, "synced": synced})

    return jsonify({"success": False, "error": "无效的操作"}), 400


def _record_interview(user_id: int, job_id: int, candidate_name: str,
                      resume_key_value: str, ai_score: int) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO interview_feedback
               (user_id, job_id, candidate_name, resume_key, ai_score, feedback_type, reason)
               VALUES (?, ?, ?, ?, ?, 'interview', '推荐约面')""",
            (user_id, job_id, candidate_name, resume_key_value, ai_score),
        )
        conn.commit()
    finally:
        conn.close()


def _append_note(job_folder: str, job_name: str, section: str,
                 candidate_name: str, ai_score: int, text: str) -> bool:
    if not job_folder:
        return False
    try:
        from ..knowledge import append_to_job_note
        score_str = "AI评分%s分" % ai_score if ai_score else "未评分"
        return append_to_job_note(
            job_folder, job_name,
            "%s - %s" % (section, datetime.now().strftime("%Y-%m-%d")),
            "- **%s**（%s）：%s" % (candidate_name, score_str, text),
        )
    except Exception as e:
        print("[Obsidian] 写入失败: %s" % e)
        return False


# ================================================================== 面试反馈
@bp.route("/feedback", methods=["POST"])
@login_required
def api_feedback_create():
    data = request.json or {}
    job_id = data.get("job_id")
    reason = (data.get("reason") or "").strip()
    if job_id is None or not reason:
        return jsonify({"success": False, "error": "岗位和原因不能为空"}), 400

    candidate_name = data.get("candidate_name", "")
    ai_score = data.get("ai_score", 0)

    fid = create_interview_feedback(
        current_user.id, int(job_id), candidate_name, data.get("resume_key", ""),
        ai_score, data.get("feedback_type", "reject"), reason,
    )

    job = get_job(int(job_id))
    job_folder = (get_settings(current_user.id) or {}).get("obsidian_job_path", "")
    synced = False
    if job and job_folder:
        synced = _append_note(
            job_folder, job["name"], "面试反馈", candidate_name, ai_score, reason
        )
        if synced:
            mark_feedback_synced(fid)

    return jsonify({"success": True, "id": fid, "synced": synced})


@bp.route("/feedback/list")
@login_required
def api_feedback_list():
    job_id = request.args.get("job_id")
    return jsonify({
        "success": True,
        "data": get_interview_feedbacks(current_user.id, int(job_id) if job_id else None),
    })


# ================================================================== 管道
@bp.route("/pipeline/<int:job_id>")
@login_required
def api_get_pipeline(job_id):
    job = get_job(job_id)
    if not job or job["user_id"] != current_user.id:
        return jsonify({"success": False, "error": "岗位不存在"}), 404
    return jsonify({
        "success": True,
        "summary": get_pipeline_summary(current_user.id, job_id),
        "stages": get_candidates_by_stage(current_user.id, job_id),
    })


@bp.route("/pipeline/update", methods=["POST"])
@login_required
def api_update_pipeline():
    data = request.json or {}
    job_id = data.get("job_id")
    candidate_name = data.get("candidate_name", "")
    stage = data.get("stage", "")

    if job_id is None or not candidate_name or not stage:
        return jsonify({"success": False, "error": "缺少参数"}), 400

    target = None
    for f in get_interview_feedbacks(current_user.id, int(job_id)):
        if candidate_name == f.get("candidate_name", ""):
            target = f
            break
    if not target:
        return jsonify({"success": False, "error": "未找到候选人: %s" % candidate_name}), 400

    ok = update_candidate_pipeline(
        target["id"], stage,
        reason=data.get("reason", ""),
        reject_stage=data.get("reject_stage", ""),
        company_rejected=data.get("company_rejected", ""),
    )
    if not ok:
        return jsonify({"success": False, "error": "更新失败（阶段值不合法）"}), 400

    # 知识引擎学习
    try:
        from ..knowledge_engine import learn_from_feedback
        learn_from_feedback(
            int(job_id), candidate_name,
            ai_score=target.get("ai_score", 0),
            stage=stage, reason=data.get("reason", ""),
            user_id=current_user.id,
        )
    except Exception as e:
        print("[知识引擎] 反馈学习失败: %s" % e)

    # 同步 Obsidian
    try:
        job = get_job(int(job_id))
        job_folder = (get_settings(current_user.id) or {}).get("obsidian_job_path", "")
        if job and job_folder:
            from ..knowledge import sync_pipeline_to_obsidian
            sync_pipeline_to_obsidian(
                job_folder, job["name"],
                get_candidates_by_stage(current_user.id, int(job_id)),
                get_pipeline_summary(current_user.id, int(job_id)),
            )
    except Exception as e:
        print("[管道] Obsidian 同步失败: %s" % e)

    return jsonify({"success": True})


# ================================================================== JD 拆解
@bp.route("/jd/analyze", methods=["POST"])
@login_required
def api_jd_analyze():
    data = request.json or {}
    job_name = (data.get("job_name") or "").strip()
    job_description = (data.get("job_description") or "").strip()
    if not job_name:
        return jsonify({"success": False, "error": "岗位名称不能为空"}), 400

    llm = config.resolve_llm_config(get_settings(current_user.id) or {})
    from ..jd_analyzer import analyze_jd
    try:
        result = asyncio.run(analyze_jd(
            job_name=job_name,
            job_description=job_description,
            api_key=llm["api_key"],
            base_url=llm["base_url"],
            model=llm["model"],
            use_llm=bool(llm["api_key"]),
        ))
        return jsonify({"success": True, "data": result})
    except Exception as e:
        print("[JD分析] 失败: %s" % e)
        return jsonify({"success": False, "error": "分析失败: %s" % e}), 500


# ================================================================== 知识库
@bp.route("/knowledge/status")
@login_required
def api_knowledge_status():
    settings = get_settings(current_user.id) or {}
    kb_path = (settings.get("obsidian_kb_path") or "").strip().replace("\\", "/")
    job_path = (settings.get("obsidian_job_path") or "").strip().replace("\\", "/")

    result = {
        "kb_path": kb_path, "job_path": job_path,
        "kb_exists": False, "job_exists": False, "kb_notes": 0,
    }

    if kb_path:
        resolved = _resolve_dir(kb_path)
        if resolved:
            result["kb_exists"] = True
            try:
                from ..knowledge import scan_knowledge_base
                result["kb_notes"] = len(scan_knowledge_base(str(resolved)))
            except Exception as e:
                print("[知识库] 扫描失败: %s" % e)

    if job_path and _resolve_dir(job_path):
        result["job_exists"] = True

    return jsonify({"success": True, "data": result})


def _resolve_dir(path: str) -> Path | None:
    """路径原样不存在时，尝试展开 ~ 后再判断"""
    p = Path(path)
    if p.exists():
        return p
    expanded = Path(path).expanduser()
    return expanded if expanded.exists() else None


@bp.route("/knowledge/stats")
@login_required
def api_knowledge_stats():
    from ..knowledge_engine import get_knowledge_stats
    return jsonify({"success": True, "data": get_knowledge_stats()})


@bp.route("/knowledge/refresh", methods=["POST"])
@login_required
def api_knowledge_refresh():
    from ..knowledge_engine import cleanup_expired_knowledge, search_and_learn_industry
    jobs = get_jobs(current_user.id)
    for job in jobs:
        search_and_learn_industry(job["name"], user_id=current_user.id)
    cleanup_expired_knowledge()
    return jsonify({"success": True, "message": "已为 %d 个岗位触发知识刷新" % len(jobs)})


@bp.route("/knowledge/query")
@login_required
def api_knowledge_query():
    from ..knowledge_engine import query_knowledge
    return jsonify({"success": True, "data": query_knowledge(
        knowledge_type=request.args.get("type"),
        category=request.args.get("category"),
        keyword=request.args.get("keyword"),
        limit=int(request.args.get("limit", 20)),
    )})
