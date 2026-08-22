"""
Agent 记忆学习模块
- 存储从反馈中提炼的经验
- 评分时检索相关记忆
- 自动发现规律并更新记忆
"""
import json
from datetime import datetime, timedelta
from typing import Optional


# ── 记忆类型 ──
MEMORY_TYPES = {
    "feedback_pattern": "反馈规律（淘汰原因统计）",
    "success_case": "成功案例（高分候选人特征）",
    "user_preference": "用户偏好（用户的筛选习惯）",
    "job_insight": "岗位洞察（某岗位的特殊要求）",
    "market_knowledge": "市场知识（薪资、行业趋势）",
}


# ── 记忆 CRUD ──

def save_memory(user_id: int, memory_type: str, content: dict, related_job: str = "") -> int:
    """保存一条记忆"""
    from .models import get_db
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO agent_memory (user_id, memory_type, content, related_job, created_at, last_used, use_count)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (user_id, memory_type, json.dumps(content, ensure_ascii=False), related_job,
             datetime.now().isoformat(), datetime.now().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_memories(user_id: int, memory_type: str = None, related_job: str = None, limit: int = 20) -> list:
    """获取记忆列表"""
    from .models import get_db
    conn = get_db()
    try:
        query = "SELECT * FROM agent_memory WHERE user_id = ?"
        params = [user_id]

        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)

        if related_job:
            query += " AND (related_job = ? OR related_job = '')"
            params.append(related_job)

        query += " ORDER BY last_used DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["content"] = json.loads(item["content"]) if item["content"] else {}
            result.append(item)
        return result
    finally:
        conn.close()


def update_memory_usage(memory_id: int):
    """更新记忆的使用时间和次数"""
    from .models import get_db
    conn = get_db()
    try:
        conn.execute(
            """UPDATE agent_memory SET last_used = ?, use_count = use_count + 1 WHERE id = ?""",
            (datetime.now().isoformat(), memory_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_old_memories(user_id: int, days: int = 90):
    """删除超过指定天数未使用的记忆"""
    from .models import get_db
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM agent_memory WHERE user_id = ? AND last_used < ? AND use_count < 3",
            (user_id, cutoff),
        )
        conn.commit()
    finally:
        conn.close()


# ── 学习逻辑 ──

def learn_from_feedback(user_id: int, job_id: int, feedbacks: list):
    """从淘汰反馈中学习规律"""
    if not feedbacks:
        return

    from .models import get_job
    job = get_job(job_id)
    job_name = job["name"] if job else ""

    # 统计淘汰原因
    reason_counts = {}
    for fb in feedbacks:
        reason = fb.get("reason", "").strip()
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    # 发现规律：某原因出现 >= 2次，记录为经验
    for reason, count in reason_counts.items():
        if count >= 2:
            # 检查是否已有相同记忆
            existing = get_memories(user_id, "feedback_pattern", job_name)
            already_exists = any(
                m["content"].get("pattern") == reason for m in existing
            )

            if not already_exists:
                save_memory(user_id, "feedback_pattern", {
                    "pattern": reason,
                    "frequency": count,
                    "suggestion": f"注意：'{reason}' 是常见淘汰原因，评分时应重点考察",
                    "first_seen": datetime.now().strftime("%Y-%m-%d"),
                }, related_job=job_name)
                print(f"[记忆] 学习到新规律：{reason}（出现{count}次）")


def learn_from_success(user_id: int, job_id: int, resume: dict, score: int):
    """从高分候选人中学习成功特征"""
    if score < 85:
        return

    from .models import get_job
    job = get_job(job_id)
    job_name = job["name"] if job else ""

    # 提取成功特征
    features = []
    if resume.get("experience"):
        features.append(f"经验：{resume['experience']}")
    if resume.get("education"):
        features.append(f"学历：{resume['education']}")
    if resume.get("company"):
        features.append(f"公司：{resume['company']}")
    if resume.get("title"):
        features.append(f"职位：{resume['title']}")

    if features:
        save_memory(user_id, "success_case", {
            "candidate": resume.get("name", "未知"),
            "score": score,
            "features": features,
            "summary": "、".join(features),
        }, related_job=job_name)


# ── 记忆检索 ──

def get_relevant_memories(user_id: int, job_name: str = "", context: str = "") -> str:
    """获取与当前任务相关的记忆，返回文本格式供 LLM 使用"""
    memories = []

    # 1. 反馈规律
    feedback_memories = get_memories(user_id, "feedback_pattern", job_name)
    if feedback_memories:
        patterns = []
        for m in feedback_memories:
            content = m["content"]
            patterns.append(f"- {content.get('pattern', '')}（出现{content.get('frequency', 0)}次）")
            update_memory_usage(m["id"])
        if patterns:
            memories.append("【历史淘汰规律】\n" + "\n".join(patterns))

    # 2. 成功案例
    success_memories = get_memories(user_id, "success_case", job_name, limit=5)
    if success_memories:
        cases = []
        for m in success_memories:
            content = m["content"]
            cases.append(f"- {content.get('summary', '')}（评分{content.get('score', 0)}）")
            update_memory_usage(m["id"])
        if cases:
            memories.append("【历史高分候选人特征】\n" + "\n".join(cases))

    # 3. 用户偏好
    preference_memories = get_memories(user_id, "user_preference", limit=5)
    if preference_memories:
        prefs = []
        for m in preference_memories:
            content = m["content"]
            prefs.append(f"- {content.get('preference', '')}")
            update_memory_usage(m["id"])
        if prefs:
            memories.append("【用户偏好】\n" + "\n".join(prefs))

    # 4. 岗位洞察
    if job_name:
        insight_memories = get_memories(user_id, "job_insight", job_name, limit=3)
        if insight_memories:
            insights = []
            for m in insight_memories:
                content = m["content"]
                insights.append(f"- {content.get('insight', '')}")
                update_memory_usage(m["id"])
            if insights:
                memories.append("【岗位洞察】\n" + "\n".join(insights))

    if not memories:
        return ""

    return "\n\n".join(memories)


# ── 主动发现规律（定时调用）──

def discover_patterns(user_id: int):
    """从历史数据中主动发现规律"""
    from .models import get_db

    conn = get_db()
    try:
        # 获取最近30天的淘汰反馈
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        rows = conn.execute(
            """SELECT reason, COUNT(*) as cnt FROM interview_feedback
               WHERE user_id = ? AND feedback_type = 'reject' AND created_at > ?
               GROUP BY reason HAVING cnt >= 2 ORDER BY cnt DESC""",
            (user_id, cutoff),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        reason = row["reason"]
        count = row["cnt"]

        # 检查是否已记录
        existing = get_memories(user_id, "feedback_pattern")
        already = any(m["content"].get("pattern") == reason for m in existing)

        if not already:
            save_memory(user_id, "feedback_pattern", {
                "pattern": reason,
                "frequency": count,
                "suggestion": f"注意：'{reason}' 是近30天常见淘汰原因",
                "discovered_at": datetime.now().strftime("%Y-%m-%d"),
            })
            print(f"[记忆] 主动发现规律：{reason}（{count}次）")


# ── 记忆统计 ──

def get_memory_stats(user_id: int) -> dict:
    """获取记忆统计"""
    from .models import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT memory_type, COUNT(*) as cnt FROM agent_memory WHERE user_id = ? GROUP BY memory_type",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    stats = {row["memory_type"]: row["cnt"] for row in rows}
    stats["total"] = sum(stats.values())
    return stats
