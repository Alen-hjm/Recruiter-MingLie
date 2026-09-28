# -*- coding: utf-8 -*-
"""Web 层共用的小工具：候选人键、简历+评分查找、网页内容整理。

这些函数原先散在 server.py 的多个位置（有的还被复制了两三份），
现在收拢到一处，改口径只改一个地方。
"""

import json
import re

from ..models import get_db, get_resumes_by_job


def resume_key(resume: dict) -> str:
    """
    候选人唯一键。

    优先 detail_url（猎聘简历详情链接天然唯一）；
    没有链接时退化为 name+title，与 models._get_resume_key 保持同一口径。
    """
    if not resume:
        return ""
    return resume.get("detail_url", "") or (
        str(resume.get("name", "")) + str(resume.get("title", "") or resume.get("work_summary", "") or "")
    )


def latest_score_map(user_id: int, job_id: int) -> dict:
    """取该岗位最近一次完成评分的结果，建成 {候选人键: 评分记录} 索引"""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT scored_json FROM score_tasks
               WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
               AND job_name = (SELECT name FROM jobs WHERE id = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, job_id),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["scored_json"]:
        return {}
    try:
        scored = json.loads(row["scored_json"])
    except json.JSONDecodeError:
        return {}

    result = {}
    for sr in scored:
        key = resume_key(sr)
        if key:
            result[key] = sr
    return result


def merge_scores_into_resumes(resumes: list, score_map: dict) -> list:
    """把评分数据合并进简历列表（原地修改并返回）"""
    for r in resumes:
        sr = score_map.get(resume_key(r))
        if not sr:
            continue
        if "score100" in sr:
            r["score100"] = sr["score100"]
        if "analysis" in sr:
            r["analysis"] = sr["analysis"]
        if "_error" in sr:
            r["score_error"] = sr["_error"]
    return resumes


def extract_total_score(resume: dict) -> int:
    """从简历里提取百分制总分（兼容 score100 与旧版 10 分制 analysis）"""
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
            if score < 10:  # 旧的 10 分制 → 百分制
                score = score * 10
        elif isinstance(s, (int, float)):
            score = s
    try:
        return round(float(score))
    except (TypeError, ValueError):
        return 0


def find_resume_and_score(user_id: int, job_id: int, resume_key_value: str):
    """
    按 key 找简历及其评分。

    返回 (resume, score100, error)。error 非空时 resume/score100 不可用。
    """
    resumes = get_resumes_by_job(user_id, job_id) or []
    target = None
    for r in resumes:
        if resume_key(r) == resume_key_value:
            target = r
            break

    if target is None:
        # 兼容前端传下标的情况
        try:
            idx = int(resume_key_value)
            if 0 <= idx < len(resumes):
                target = resumes[idx]
        except (TypeError, ValueError):
            pass

    if target is None:
        return None, None, "未找到候选人（key=%s）" % resume_key_value

    score_map = latest_score_map(user_id, job_id)
    sr = score_map.get(resume_key(target)) or {}
    return target, sr.get("score100") or {}, None


def build_organized_text(resume: dict) -> str:
    """从结构化简历数据构建人类可读的 Markdown 文本"""
    lines = []
    if resume.get("name"):
        lines.append("# %s" % resume["name"])
    if resume.get("title"):
        lines.append("**%s**" % resume["title"])
    if resume.get("company"):
        lines.append("现公司：%s" % resume["company"])
    lines.append("")

    info = []
    for key, label in [("education", "学历"), ("school", "院校"),
                       ("experience", "经验"), ("location", "城市")]:
        if resume.get(key):
            info.append("%s：%s" % (label, resume[key]))
    if info:
        lines.append(" | ".join(info))
        lines.append("")

    if resume.get("summary"):
        lines.extend(["## 个人简介", resume["summary"], ""])

    if resume.get("work_experience"):
        lines.append("## 工作经历")
        for we in resume["work_experience"]:
            lines.append("**%s** - %s (%s)" % (we.get("company", ""), we.get("position", ""), we.get("period", "")))
            if we.get("description"):
                lines.append(we["description"])
            lines.append("")

    if resume.get("projects"):
        lines.append("## 项目经历")
        for p in resume["projects"]:
            lines.append("**%s** - %s (%s)" % (p.get("name", ""), p.get("role", ""), p.get("period", "")))
            if p.get("description"):
                lines.append(p["description"])
            lines.append("")

    if resume.get("education_detail"):
        lines.append("## 教育经历")
        for ed in resume["education_detail"]:
            lines.append("**%s** - %s - %s (%s)" % (
                ed.get("school", ""), ed.get("major", ""), ed.get("degree", ""), ed.get("period", "")))
            lines.append("")

    if resume.get("skill_tags"):
        lines.extend(["## 技能标签", "、".join(resume["skill_tags"])])

    return "\n".join(lines)


def organize_webpage_to_resume(llm_config: dict, raw_content: dict,
                               job_name: str, job_description: str) -> dict:
    """
    用 LLM 把网页原始内容整理为结构化简历。

    失败时退化为「标题 + 全文」的最小结构 —— 保证调用方永远拿到一个 dict，
    不需要自己判空。
    """
    fallback = {
        "name": (raw_content.get("title", "") or "未知")[:20],
        "title": raw_content.get("title", ""),
        "detail_url": raw_content.get("url", ""),
        "source": "webpage",
        "source_url": raw_content.get("url", ""),
        "full_text": (raw_content.get("full_text", "") or "")[:15000],
        "organized_text": (raw_content.get("full_text", "") or "")[:8000],
        "scraped_at": raw_content.get("timestamp", ""),
    }

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

        content_text = "标题: %s\n" % title
        if description:
            content_text += "描述: %s\n\n" % description
        if paragraphs:
            content_text += "正文段落:\n"
            for i, p in enumerate(paragraphs[:50], 1):
                content_text += "%d. %s\n" % (i, p)
        elif full_text:
            content_text += "\n全文:\n%s" % full_text[:12000]

        system_prompt = (
            "你是一个专业的简历整理助手。你会收到从网页抓取的原始内容，"
            "可能是候选人的在线简历、个人主页、LinkedIn页面等。"
            "你的任务是：从原始内容中提取候选人的关键信息，整理成结构化的简历格式。"
            "如果某些信息缺失，留空即可，不要编造。\n输出严格的 JSON 格式（不要输出其他内容）。"
        )

        user_prompt = """请将以下网页内容整理为结构化简历。
%s%s

网页原始内容：
%s

请输出 JSON：
{
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
}""" % (
            ("目标岗位: %s\n" % job_name) if job_name else "",
            ("岗位描述: %s\n" % job_description[:500]) if job_description else "",
            content_text,
        )

        response = client.chat.completions.create(
            model=llm_config.get("model", "deepseek-chat"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)

        result["detail_url"] = raw_content.get("url", "")
        result["source"] = "webpage"
        result["source_url"] = raw_content.get("url", "")
        result["full_text"] = full_text[:15000] if full_text else ""
        result["scraped_at"] = raw_content.get("timestamp", "")
        if not result.get("organized_text"):
            result["organized_text"] = build_organized_text(result)
        return result

    except Exception as e:
        print("[LLM整理简历] 失败: %s" % e)
        return fallback


def safe_filename(name: str, max_len: int = 20) -> str:
    """把候选人姓名转成可用的文件名片段（去掉路径分隔符等非法字符）"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "候选人")).strip()
    return cleaned[:max_len] or "候选人"
