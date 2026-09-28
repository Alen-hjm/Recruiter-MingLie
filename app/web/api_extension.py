# -*- coding: utf-8 -*-
"""
Chrome 扩展专用接口（18 个）。

重构要点：
  1. 鉴权从「拿 LLM API Key 当登录凭据」换成独立的访问令牌（app/auth.py）。
     旧扩展发送的 X-API-Key 仍在兼容期内被接受，可用
     MINGLIE_ALLOW_LEGACY_KEY_AUTH=0 关闭。
  2. 不再接受 ?api_key= 查询串 —— 凭据只从请求头读，避免密钥进访问日志。
  3. 原先每个视图开头都抄一遍
         user = _get_user_from_api_key(request)
         if not user: return jsonify(...), 401
     现在统一由 @require_api_token 处理，路由体只写业务。
  4. 修掉一个真实 bug：原 api_extension_score 里
         save_scrape_results_dedup(user.id, int(job_id), [resume_data])
     只传了 3 个参数，而被调用函数的签名要求 7 个 —— 一旦走到那行就会
     TypeError。这里改用带正确参数的保存函数。
"""

import json
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from .. import config
from ..auth import current_api_user, require_api_token
from ..models import (
    create_candidate_from_score,
    create_job,
    create_scrape_task,
    create_search_strategy,
    get_candidates_by_stage,
    get_db,
    get_job,
    get_jobs,
    get_pipeline_summary,
    get_resumes_by_job,
    get_scrape_task,
    get_search_strategies,
    get_settings,
    save_scrape_results_dedup,
    update_scrape_task,
)
from ..services.scoring import start_score_task
from ._helpers import (
    build_organized_text,
    latest_score_map,
    merge_scores_into_resumes,
    organize_webpage_to_resume,
    resume_key,
)

bp = Blueprint("api_extension", __name__, url_prefix="/api/extension")


# ------------------------------------------------------------------ 小工具
def _user():
    """当前令牌对应的用户（由 @require_api_token 注入）"""
    return current_api_user()


def _owned_job_or_404(job_id: int):
    """返回 (job, error_response)"""
    job = get_job(job_id)
    user = _user()
    if not job or job["user_id"] != user.id:
        return None, (jsonify({"success": False, "error": "岗位不存在"}), 404)
    return job, None


def _llm_cfg():
    settings = get_settings(_user().id) or {}
    return config.resolve_llm_config(settings)


def _save_resumes(user_id: int, job_id: int, job: dict, resumes: list, platform: str) -> int:
    """保存抓取到的简历（按候选人键去重）。返回新保存的条数。"""
    result = save_scrape_results_dedup(
        user_id, job_id,
        keyword=job.get("keyword", "") or "",
        city="",
        platform=platform,
        max_pages=1,
        new_resumes=resumes,
    )
    return result.get("saved_count", 0)


def _start_scoring(user_id: int, job_id: int, job: dict, resumes: list):
    """未配置 Key / 未配置 JD 时返回 (None, 提示语)，否则返回 (task_id, 提示语)"""
    llm = _llm_cfg()
    if not llm["api_key"]:
        return None, "已保存 %d 份简历，但未配置 LLM API Key，跳过评分" % len(resumes)

    job_description = job.get("job_description", "")
    if not job_description:
        return None, "已保存 %d 份简历，但岗位未配置 JD，跳过评分" % len(resumes)

    task_id, _ = start_score_task(user_id, job_id, job["name"], job_description, resumes, "100")
    return task_id, "已接收 %d 份简历，正在后台评分" % len(resumes)


# ================================================================== 岗位与策略
@bp.route("/jobs")
@require_api_token
def api_jobs():
    user = _user()
    result = []
    for j in get_jobs(user.id):
        result.append({
            "id": j["id"],
            "name": j["name"],
            "keyword": j.get("keyword", ""),
            "strategies": get_search_strategies(user.id, j["id"]),
        })
    return jsonify({"success": True, "data": result})


@bp.route("/jobs", methods=["POST"])
@require_api_token
def api_jobs_create():
    user = _user()
    data = request.json or {}
    name = (data.get("name") or "").strip()
    keyword = (data.get("keyword") or "").strip()
    desc = (data.get("job_description") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "岗位名称不能为空"}), 400

    job_id = create_job(user.id, name, keyword, desc)
    if keyword:
        create_search_strategy(
            user.id, job_id, keyword,
            name="%s-默认搜索" % name,
            city=(data.get("city") or "上海").strip() or "上海",
            is_default=True,
        )

    try:
        from ..knowledge_engine import learn_from_job, search_and_learn_industry
        learn_from_job({"name": name, "keyword": keyword, "job_description": desc})
        search_and_learn_industry(name, user_id=user.id)
    except Exception as e:
        print("[知识引擎] 岗位学习失败: %s" % e)

    return jsonify({"success": True, "id": job_id})


@bp.route("/strategies/<int:job_id>")
@require_api_token
def api_strategies(job_id):
    _, err = _owned_job_or_404(job_id)
    if err:
        return err
    return jsonify({"success": True, "data": get_search_strategies(_user().id, job_id)})


@bp.route("/strategies/<int:job_id>", methods=["POST"])
@require_api_token
def api_strategies_create(job_id):
    _, err = _owned_job_or_404(job_id)
    if err:
        return err

    data = request.json or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "关键词不能为空"}), 400

    sid = create_search_strategy(
        _user().id, job_id, keyword,
        name=(data.get("name") or "").strip() or keyword,
        city=data.get("city", "上海"),
        platform=data.get("platform", "liepin"),
        max_pages=min(int(data.get("max_pages", 3)), config.SCRAPE_MAX_PAGES_LIMIT),
        is_default=data.get("is_default", False),
    )
    return jsonify({"success": True, "id": sid})


@bp.route("/filter_rules/<int:job_id>")
@require_api_token
def api_filter_rules(job_id):
    job, err = _owned_job_or_404(job_id)
    if err:
        return err
    from ..jd_analyzer import extract_filter_rules_from_jd
    rules = extract_filter_rules_from_jd({}, job.get("job_description", ""))
    return jsonify({"success": True, "rules": rules, "job_name": job["name"]})


# ================================================================== 简历与评分
@bp.route("/resumes/<int:job_id>")
@require_api_token
def api_resumes(job_id):
    _, err = _owned_job_or_404(job_id)
    if err:
        return err
    resumes = get_resumes_by_job(_user().id, job_id)
    merge_scores_into_resumes(resumes, latest_score_map(_user().id, job_id))
    return jsonify({"success": True, "data": resumes})


@bp.route("/score", methods=["POST"])
@require_api_token
def api_score():
    """扩展提交单份简历：LLM 结构化解析 → 评分 → 落库"""
    user = _user()
    data = request.json or {}
    resume_data = data.get("resume") or {}
    job_id = data.get("job_id")

    if not resume_data or job_id is None:
        return jsonify({"success": False, "error": "缺少简历数据或岗位 ID"}), 400

    job, err = _owned_job_or_404(int(job_id))
    if err:
        return err

    job_description = job.get("job_description", "")
    if not job_description:
        return jsonify({"success": False, "error": "该岗位未配置岗位描述"}), 400

    llm = _llm_cfg()
    if not llm["api_key"]:
        return jsonify({"success": False, "error": "未配置 LLM API Key"}), 400

    _parse_resume_text_with_llm(llm, resume_data)

    try:
        from ..analyzer import ResumeAnalyzer
        analyzer = ResumeAnalyzer({
            "llm": llm,
            "job_description": job_description,
            "job_name": job["name"],
        })
        result = analyzer.score_resume_100(resume_data, job_description)
    except Exception as e:
        print("[插件评分] 失败: %s" % e)
        return jsonify({"success": False, "error": str(e)}), 500

    try:
        _save_resumes(user.id, int(job_id), job, [resume_data], platform="extension")
        print("[插件] 简历已保存: %s" % resume_data.get("name", "?"))
    except Exception as e:
        print("[插件] 保存简历失败: %s" % e)

    return jsonify({"success": True, "data": {"score100": result, "resume": resume_data}})


def _parse_resume_text_with_llm(llm: dict, resume_data: dict) -> None:
    """
    用 LLM 从网页正文里抽取结构化字段，补进 resume_data。

    只在 full_text 存在时执行；失败不影响评分（评分只用已有的卡片字段）。
    """
    full_text = (resume_data.get("full_text") or "").strip()
    if not full_text:
        return

    import re
    import httpx

    prompt = """你是一个简历解析专家。请从以下从猎聘网页提取的文本中，准确识别并提取简历信息。

注意：文本可能包含网页导航、按钮文字、广告等噪音，请忽略这些，只关注简历内容本身。

请返回严格的 JSON 格式：
{
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
}

简历文本：
%s""" % full_text[:10000]

    fields = ["name", "age", "education", "experience", "company", "title",
              "location", "salary", "expect_city", "expect_position", "skills", "work_summary"]

    try:
        resp = httpx.post(
            "%s/chat/completions" % llm["base_url"].rstrip("/"),
            headers={
                "Authorization": "Bearer %s" % llm["api_key"],
                "Content-Type": "application/json",
            },
            json={
                "model": llm["model"],
                "messages": [
                    {"role": "system", "content": "你是简历解析助手，只返回 JSON，不要其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1000,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return
        parsed = json.loads(match.group())
        for key in fields:
            if parsed.get(key) and not resume_data.get(key):
                resume_data[key] = parsed[key]
        print("[插件] LLM 解析成功: %s" % resume_data.get("name", "?"))
    except Exception as e:
        print("[插件] LLM 解析简历失败: %s，使用基础数据" % e)


@bp.route("/submit_batch", methods=["POST"])
@require_api_token
def api_submit_batch():
    """扩展批量提交多页抓取结果 → 保存 + 后台评分"""
    user = _user()
    data = request.json or {}
    job_id = data.get("job_id")
    resumes = data.get("resumes") or []

    if job_id is None or not resumes:
        return jsonify({"success": False, "error": "缺少 job_id 或 resumes"}), 400

    job, err = _owned_job_or_404(int(job_id))
    if err:
        return err

    task_id = create_scrape_task(
        user.id, keyword=job.get("keyword", ""), city="",
        platform="extension", max_pages=1, job_id=int(job_id),
    )
    update_scrape_task(
        task_id, status="done", result_count=len(resumes),
        resumes_json=json.dumps(resumes, ensure_ascii=False),
        finished_at=datetime.now().isoformat(),
    )

    try:
        from ..knowledge_engine import learn_from_resume
        for r in resumes:
            learn_from_resume(r, job_name=job.get("name", ""))
    except Exception as e:
        print("[知识引擎] 简历学习失败: %s" % e)

    score_task_id, message = _start_scoring(user.id, int(job_id), job, resumes)
    payload = {"success": True, "task_id": task_id, "message": message}
    if score_task_id:
        payload["score_task_id"] = score_task_id
    return jsonify(payload)


@bp.route("/submit_webpage", methods=["POST"])
@require_api_token
def api_submit_webpage():
    """扩展提交网页内容 → AI 整理为简历 → 评分 → 录入岗位"""
    user = _user()
    data = request.json or {}
    job_id = data.get("job_id")
    raw_content = data.get("content") or {}

    if job_id is None or not raw_content:
        return jsonify({"success": False, "error": "缺少 job_id 或 content"}), 400

    job, err = _owned_job_or_404(int(job_id))
    if err:
        return err

    llm = _llm_cfg()
    if not llm["api_key"]:
        return jsonify({"success": False, "error": "未配置 LLM API Key"}), 400

    job_description = job.get("job_description", "")
    organized = organize_webpage_to_resume(llm, raw_content, job.get("name", ""), job_description)
    if not organized:
        return jsonify({"success": False, "error": "AI 整理简历失败"}), 500

    if job_description:
        try:
            from ..analyzer import ResumeAnalyzer
            from ..knowledge_engine import build_scoring_context
            score_cfg = {
                "llm": llm, "job_description": job_description,
                "job_name": job.get("name", ""),
            }
            ctx = build_scoring_context(job.get("name", ""))
            if ctx:
                score_cfg["knowledge_context"] = ctx
            organized["score100"] = ResumeAnalyzer(score_cfg).score_resume_100(
                organized, job_description
            )
        except Exception as e:
            print("[网页简历] 评分失败: %s" % e)
            organized["score100"] = {"total_score": 0, "error": str(e)}

    task_id = create_scrape_task(
        user.id, keyword=job.get("keyword", ""), city="",
        platform="webpage", max_pages=1, job_id=int(job_id),
    )
    update_scrape_task(
        task_id, status="done", result_count=1,
        resumes_json=json.dumps([organized], ensure_ascii=False),
        finished_at=datetime.now().isoformat(),
    )

    try:
        from ..knowledge_engine import learn_from_resume
        learn_from_resume(organized, job_name=job.get("name", ""))
    except Exception:
        pass

    score = (organized.get("score100") or {}).get("total_score", 0)
    return jsonify({
        "success": True, "task_id": task_id, "resume": organized,
        "message": "AI 已整理并评分：%s → %s分" % (organized.get("name", "未知"), score),
    })


# ================================================================== 管道
@bp.route("/pipeline/<int:job_id>")
@require_api_token
def api_pipeline(job_id):
    _, err = _owned_job_or_404(job_id)
    if err:
        return err
    user = _user()
    return jsonify({
        "success": True,
        "summary": get_pipeline_summary(user.id, job_id),
        "stages": get_candidates_by_stage(user.id, job_id),
    })


@bp.route("/pipeline/<int:job_id>/add", methods=["POST"])
@require_api_token
def api_pipeline_add(job_id):
    _, err = _owned_job_or_404(job_id)
    if err:
        return err

    user = _user()
    data = request.json or {}
    candidate_name = data.get("name", "未知")
    key = data.get("detail_url", "") or candidate_name
    payload = {
        "name": candidate_name,
        "detail_url": data.get("detail_url", ""),
        "title": data.get("title", ""),
        "score100": {"total_score": data.get("score", 0)},
    }

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM interview_feedback WHERE user_id = ? AND job_id = ? AND resume_key = ?",
            (user.id, job_id, key),
        ).fetchone()
    finally:
        conn.close()

    if existing:
        return jsonify({"success": True, "message": "该候选人已在管道中", "id": existing["id"]})

    fid = create_candidate_from_score(user.id, job_id, payload)
    return jsonify({"success": True, "id": fid, "message": "已将 %s 加入管道" % candidate_name})


@bp.route("/pipeline/<int:job_id>/delete", methods=["POST"])
@require_api_token
def api_pipeline_delete(job_id):
    _, err = _owned_job_or_404(job_id)
    if err:
        return err

    data = request.json or {}
    feedback_id = data.get("id")
    if not feedback_id:
        return jsonify({"success": False, "error": "缺少候选人 ID"}), 400

    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM interview_feedback WHERE id = ? AND user_id = ? AND job_id = ?",
            (feedback_id, _user().id, job_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True, "message": "已删除"})


# ================================================================== 插件版对话
@bp.route("/chat", methods=["POST"])
@require_api_token
def api_chat():
    """扩展侧 AI 对话。会话按用户共享（与网页端同一份历史）。"""
    user = _user()
    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "消息不能为空"}), 400

    from .api_agent import _chat_turn
    try:
        result = _chat_turn(user.id, message)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": True,
        "reply": result.get("summary", "处理完成"),
        "plan": result.get("plan", {}),
        "results": result.get("results", []),
    })


@bp.route("/chat/clear", methods=["POST"])
@require_api_token
def api_chat_clear():
    from .api_agent import _agent_sessions, _session_key
    _agent_sessions.pop(_session_key(_user().id), None)
    return jsonify({"success": True})


# ================================================================== 知识库
@bp.route("/knowledge/stats")
@require_api_token
def api_knowledge_stats():
    from ..knowledge_engine import get_knowledge_stats
    return jsonify({"success": True, "data": get_knowledge_stats()})


@bp.route("/knowledge/refresh", methods=["POST"])
@require_api_token
def api_knowledge_refresh():
    from ..knowledge_engine import cleanup_expired_knowledge, search_and_learn_industry
    user = _user()
    jobs = get_jobs(user.id)
    for job in jobs:
        search_and_learn_industry(job["name"], user_id=user.id)
    cleanup_expired_knowledge()
    return jsonify({"success": True, "message": "已为 %d 个岗位触发知识刷新" % len(jobs)})


@bp.route("/knowledge/list")
@require_api_token
def api_knowledge_list():
    from ..knowledge_engine import query_knowledge
    return jsonify({"success": True, "data": query_knowledge(
        knowledge_type=request.args.get("type"),
        keyword=request.args.get("keyword"),
        limit=int(request.args.get("limit", 50)),
    )})


@bp.route("/knowledge/delete", methods=["POST"])
@require_api_token
def api_knowledge_delete():
    data = request.json or {}
    knowledge_id = data.get("id")
    if not knowledge_id:
        return jsonify({"success": False, "error": "缺少知识 ID"}), 400

    conn = get_db()
    try:
        conn.execute("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True, "message": "已删除"})
