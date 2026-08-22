"""
知识引擎 - 自进化的招聘知识系统

职责：
1. 从各种来源采集知识（简历、岗位、行业资讯、面试反馈）
2. 结构化存储到知识库（Obsidian + SQLite）
3. 评分时检索相关知识增强判断
4. 自动发现规律并更新知识
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path


# ══════════════════════════════════════════
# 知识类型定义
# ══════════════════════════════════════════

KNOWLEDGE_TYPES = {
    # ── 行业知识 ──
    "industry_trend": {
        "desc": "行业趋势（技术方向、市场变化）",
        "source": "ai_search",
        "ttl_days": 30,
    },
    "skill_demand": {
        "desc": "技能需求（热门技能、薪资溢价）",
        "source": "resume_analysis",
        "ttl_days": 14,
    },
    "salary_range": {
        "desc": "薪资范围（城市×岗位×经验的薪资分布）",
        "source": "resume_scrape",
        "ttl_days": 7,
    },

    # ── 岗位知识 ──
    "job_profile": {
        "desc": "岗位画像（硬需求、软需求、典型背景）",
        "source": "job_creation",
        "ttl_days": 90,
    },
    "success_pattern": {
        "desc": "成功案例（被录用候选人的特征）",
        "source": "hiring_feedback",
        "ttl_days": 180,
    },
    "reject_pattern": {
        "desc": "淘汰规律（常见淘汰原因及频率）",
        "source": "interview_feedback",
        "ttl_days": 90,
    },

    # ── 候选人知识 ──
    "candidate_pool": {
        "desc": "候选人池（按技能/经验分组的候选人画像）",
        "source": "resume_scrape",
        "ttl_days": 30,
    },
    "scoring_calibration": {
        "desc": "评分校准（AI评分与实际结果的偏差）",
        "source": "feedback_loop",
        "ttl_days": 60,
    },
}


# ══════════════════════════════════════════
# 知识存储（SQLite + Obsidian 双写）
# ══════════════════════════════════════════

def _get_db():
    from .models import get_db
    return get_db()


def init_knowledge_db():
    """初始化知识库表"""
    conn = _get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_type TEXT NOT NULL,
                category TEXT DEFAULT '',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                confidence REAL DEFAULT 0.5,
                use_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge(knowledge_type);
            CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
            CREATE INDEX IF NOT EXISTS idx_knowledge_expires ON knowledge(expires_at);

            CREATE TABLE IF NOT EXISTS knowledge_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                knowledge_type TEXT,
                detail TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
    finally:
        conn.close()


def save_knowledge(knowledge_type: str, title: str, content: str,
                   category: str = "", source: str = "", source_id: str = "",
                   confidence: float = 0.5, ttl_days: int = None) -> int:
    """保存一条知识"""
    if ttl_days is None:
        ttl_days = KNOWLEDGE_TYPES.get(knowledge_type, {}).get("ttl_days", 90)

    expires_at = (datetime.now() + timedelta(days=ttl_days)).isoformat()

    conn = _get_db()
    try:
        # 检查是否已存在相同知识（去重）
        existing = conn.execute(
            "SELECT id FROM knowledge WHERE knowledge_type = ? AND title = ? AND category = ?",
            (knowledge_type, title, category),
        ).fetchone()

        if existing:
            # 更新已有知识
            conn.execute(
                """UPDATE knowledge SET content = ?, confidence = ?, source = ?,
                   updated_at = ?, expires_at = ? WHERE id = ?""",
                (content, confidence, source, datetime.now().isoformat(), expires_at, existing["id"]),
            )
            conn.commit()
            return existing["id"]

        cursor = conn.execute(
            """INSERT INTO knowledge (knowledge_type, category, title, content, source, source_id, confidence, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (knowledge_type, category, title, content, source, source_id, confidence, expires_at),
        )
        conn.commit()
        kid = cursor.lastrowid

        # 记录日志
        conn.execute(
            "INSERT INTO knowledge_log (action, knowledge_type, detail) VALUES (?, ?, ?)",
            ("create", knowledge_type, f"{title}: {content[:100]}"),
        )
        conn.commit()

        return kid
    finally:
        conn.close()


def query_knowledge(knowledge_type: str = None, category: str = None,
                    keyword: str = None, limit: int = 10) -> list:
    """查询知识"""
    conn = _get_db()
    try:
        query = "SELECT * FROM knowledge WHERE (expires_at IS NULL OR expires_at > ?)"
        params = [datetime.now().isoformat()]

        if knowledge_type:
            query += " AND knowledge_type = ?"
            params.append(knowledge_type)
        if category:
            query += " AND category = ?"
            params.append(category)
        if keyword:
            query += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        query += " ORDER BY confidence DESC, use_count DESC, updated_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_knowledge_usage(knowledge_id: int):
    """更新知识使用次数"""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE knowledge SET use_count = use_count + 1, last_used = ? WHERE id = ?",
            (datetime.now().isoformat(), knowledge_id),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_knowledge():
    """清理过期知识"""
    conn = _get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM knowledge WHERE expires_at IS NOT NULL AND expires_at < ?",
            (datetime.now().isoformat(),),
        )
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            print(f"[知识库] 清理了 {deleted} 条过期知识")
        return deleted
    finally:
        conn.close()


# ══════════════════════════════════════════
# 知识采集器 1: 从简历中提取知识
# ══════════════════════════════════════════

def learn_from_resume(resume: dict, job_name: str = ""):
    """从一份简历中提取并存储知识"""
    name = resume.get("name", "")
    education = resume.get("education", "")
    experience = resume.get("experience", "")
    company = resume.get("company", "")
    title = resume.get("title", "")
    skills = resume.get("skill_tags", [])
    location = resume.get("location", "")
    salary = resume.get("salary", "")

    # 1. 薪资知识
    if salary and location and title:
        save_knowledge(
            "salary_range",
            title=f"{location} {title} 薪资",
            content=f"{salary}（来源：{name}，{experience}，{education}）",
            category=f"{location}/{title}",
            source="resume_scrape",
            confidence=0.6,
            ttl_days=14,
        )

    # 2. 技能需求知识
    if skills and job_name:
        for skill in skills:
            save_knowledge(
                "skill_demand",
                title=f"{job_name} 技能: {skill}",
                content=f"{skill} 是 {job_name} 岗位的常见技能要求",
                category=job_name,
                source="resume_scrape",
                confidence=0.5,
                ttl_days=30,
            )

    # 3. 候选人画像
    if title and experience and education:
        profile = f"{education} | {experience} | {company or '未知公司'} | {title}"
        if skills:
            profile += f" | 技能: {', '.join(skills[:5])}"
        save_knowledge(
            "candidate_pool",
            title=f"候选人画像: {name or title}",
            content=profile,
            category=job_name or title,
            source="resume_scrape",
            confidence=0.4,
            ttl_days=30,
        )


# ══════════════════════════════════════════
# 知识采集器 2: 从岗位中提取知识
# ══════════════════════════════════════════

def learn_from_job(job: dict):
    """从岗位配置中提取知识"""
    job_name = job.get("name", "")
    jd = job.get("job_description", "")
    keyword = job.get("keyword", "")

    if not job_name:
        return

    # 岗位画像
    content_parts = []
    if jd:
        content_parts.append(f"岗位描述:\n{jd[:2000]}")
    if keyword:
        content_parts.append(f"搜索关键词: {keyword}")

    save_knowledge(
        "job_profile",
        title=f"岗位画像: {job_name}",
        content="\n\n".join(content_parts) if content_parts else "暂无详细描述",
        category=job_name,
        source="job_creation",
        confidence=0.8,
        ttl_days=90,
    )

    # 如果有 JD，用 LLM 提取结构化知识
    if jd:
        _extract_jd_knowledge_async(job_name, jd)


def _extract_jd_knowledge_async(job_name: str, jd: str):
    """异步提取 JD 中的结构化知识（后台执行）"""
    import threading

    def _extract():
        try:
            from .jd_analyzer import analyze_jd
            import asyncio
            result = asyncio.run(analyze_jd(job_name, jd))
            if not result:
                return

            # 保存硬需求知识
            for req in result.get("hard_requirements", []):
                save_knowledge(
                    "job_profile",
                    title=f"{job_name} 硬需求: {req}",
                    content=req,
                    category=job_name,
                    source="jd_analysis",
                    confidence=0.9,
                    ttl_days=90,
                )

            # 保存软需求知识
            for req in result.get("soft_requirements", []):
                save_knowledge(
                    "job_profile",
                    title=f"{job_name} 软需求: {req}",
                    content=req,
                    category=job_name,
                    source="jd_analysis",
                    confidence=0.7,
                    ttl_days=90,
                )

            print(f"[知识库] 从 JD 提取了 {job_name} 的结构化知识")
        except Exception as e:
            print(f"[知识库] JD 知识提取失败: {e}")

    threading.Thread(target=_extract, daemon=True).start()


# ══════════════════════════════════════════
# 知识采集器 3: 从面试反馈中学习
# ══════════════════════════════════════════

def learn_from_feedback(job_id: int, candidate_name: str, ai_score: int,
                        stage: str, reason: str, user_id: int = None):
    """从面试反馈中学习"""
    from .models import get_job
    job = get_job(job_id) if job_id else None
    job_name = job["name"] if job else ""

    if stage == "rejected" and reason:
        # 淘汰规律
        save_knowledge(
            "reject_pattern",
            title=f"{job_name} 淘汰原因: {reason[:50]}",
            content=json.dumps({
                "reason": reason,
                "candidate": candidate_name,
                "ai_score": ai_score,
                "stage": stage,
            }, ensure_ascii=False),
            category=job_name,
            source="interview_feedback",
            confidence=0.8,
            ttl_days=90,
        )

        # 评分校准：AI 给了高分但被淘汰 → 评分可能偏高
        if ai_score >= 70:
            save_knowledge(
                "scoring_calibration",
                title=f"评分校准: {candidate_name} ({ai_score}分→淘汰)",
                content=json.dumps({
                    "candidate": candidate_name,
                    "ai_score": ai_score,
                    "result": "rejected",
                    "reason": reason,
                    "lesson": f"AI评分{ai_score}但被淘汰，原因: {reason}，类似情况应降分",
                }, ensure_ascii=False),
                category=job_name,
                source="feedback_loop",
                confidence=0.7,
                ttl_days=60,
            )

    elif stage in ("hired", "offer"):
        # 成功案例
        save_knowledge(
            "success_pattern",
            title=f"{job_name} 成功案例: {candidate_name}",
            content=json.dumps({
                "candidate": candidate_name,
                "ai_score": ai_score,
                "stage": stage,
                "lesson": f"AI评分{ai_score}最终{stage}，验证了评分准确性",
            }, ensure_ascii=False),
            category=job_name,
            source="hiring_feedback",
            confidence=0.9,
            ttl_days=180,
        )


# ══════════════════════════════════════════
# 知识采集器 4: AI 主动搜索行业知识
# ══════════════════════════════════════════

def search_and_learn_industry(job_name: str, user_id: int = None):
    """AI 主动搜索行业知识并存入知识库"""
    import threading

    def _search():
        try:
            # 构建搜索查询
            queries = [
                f"{job_name} 岗位职责 技能要求 2024 2025",
                f"{job_name} 薪资水平 市场行情",
                f"{job_name} 行业趋势 发展前景",
            ]

            all_knowledge = []

            for query in queries:
                try:
                    # 使用 web search 获取信息
                    from .knowledge import find_relevant_notes
                    # 这里可以接入实际的搜索 API
                    # 暂时用 LLM 生成行业洞察
                    pass
                except Exception:
                    pass

            # 用 LLM 生成行业知识摘要
            _generate_industry_knowledge(job_name)

            print(f"[知识库] 行业知识搜索完成: {job_name}")
        except Exception as e:
            print(f"[知识库] 行业搜索失败: {e}")

    threading.Thread(target=_search, daemon=True).start()


def _generate_industry_knowledge(job_name: str):
    """用 LLM 生成行业知识"""
    try:
        from .models import get_settings
        # 获取任意用户的 API 配置
        conn = _get_db()
        row = conn.execute("SELECT * FROM settings WHERE llm_api_key IS NOT NULL AND llm_api_key != '' LIMIT 1").fetchone()
        conn.close()
        if not row:
            return

        import httpx
        api_key = row["llm_api_key"]
        base_url = row.get("llm_base_url", "https://api.deepseek.com")
        model = row.get("llm_model", "deepseek-chat")

        prompt = f"""请作为招聘行业专家，分析「{job_name}」这个岗位，提供以下信息（JSON格式）：

{{
  "key_skills": ["核心技能1", "核心技能2", ...],
  "salary_range": "典型薪资范围",
  "career_path": "职业发展路径",
  "market_demand": "市场需求描述",
  "red_flags": ["筛选时需要注意的风险点1", ...],
  "screening_tips": ["简历筛选建议1", ...]
}}"""

        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是招聘行业专家，返回JSON格式。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1000,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # 提取 JSON
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            data = json.loads(json_match.group())

            # 保存各项知识
            if data.get("key_skills"):
                save_knowledge(
                    "skill_demand",
                    title=f"{job_name} 核心技能",
                    content=", ".join(data["key_skills"]),
                    category=job_name,
                    source="ai_analysis",
                    confidence=0.8,
                    ttl_days=30,
                )

            if data.get("salary_range"):
                save_knowledge(
                    "salary_range",
                    title=f"{job_name} 薪资范围",
                    content=data["salary_range"],
                    category=job_name,
                    source="ai_analysis",
                    confidence=0.7,
                    ttl_days=14,
                )

            if data.get("screening_tips"):
                save_knowledge(
                    "job_profile",
                    title=f"{job_name} 筛选建议",
                    content="\n".join(f"- {tip}" for tip in data["screening_tips"]),
                    category=job_name,
                    source="ai_analysis",
                    confidence=0.8,
                    ttl_days=60,
                )

            if data.get("red_flags"):
                save_knowledge(
                    "reject_pattern",
                    title=f"{job_name} 风险点",
                    content="\n".join(f"- {r}" for r in data["red_flags"]),
                    category=job_name,
                    source="ai_analysis",
                    confidence=0.7,
                    ttl_days=60,
                )

    except Exception as e:
        print(f"[知识库] LLM 行业知识生成失败: {e}")


# ══════════════════════════════════════════
# 知识应用：增强评分
# ══════════════════════════════════════════

def build_scoring_context(job_name: str, resume: dict = None) -> str:
    """为评分构建知识上下文（注入到 LLM prompt 中）"""
    sections = []

    # 1. 岗位画像知识
    job_knowledge = query_knowledge(knowledge_type="job_profile", category=job_name, limit=5)
    if job_knowledge:
        parts = []
        for k in job_knowledge:
            parts.append(f"【{k['title']}】{k['content'][:300]}")
            update_knowledge_usage(k["id"])
        sections.append("## 岗位知识\n" + "\n".join(parts))

    # 2. 成功案例
    success_knowledge = query_knowledge(knowledge_type="success_pattern", category=job_name, limit=3)
    if success_knowledge:
        parts = []
        for k in success_knowledge:
            data = json.loads(k["content"]) if k["content"].startswith("{") else {"lesson": k["content"]}
            parts.append(f"- {data.get('lesson', k['content'][:200])}")
            update_knowledge_usage(k["id"])
        sections.append("## 成功案例参考\n" + "\n".join(parts))

    # 3. 淘汰规律
    reject_knowledge = query_knowledge(knowledge_type="reject_pattern", category=job_name, limit=3)
    if reject_knowledge:
        parts = []
        for k in reject_knowledge:
            data = json.loads(k["content"]) if k["content"].startswith("{") else {"reason": k["content"]}
            parts.append(f"- {data.get('reason', k['content'][:200])}")
            update_knowledge_usage(k["id"])
        sections.append("## 常见淘汰原因（注意规避）\n" + "\n".join(parts))

    # 4. 评分校准
    calibration = query_knowledge(knowledge_type="scoring_calibration", category=job_name, limit=3)
    if calibration:
        parts = []
        for k in calibration:
            data = json.loads(k["content"]) if k["content"].startswith("{") else {"lesson": k["content"]}
            parts.append(f"- {data.get('lesson', k['content'][:200])}")
            update_knowledge_usage(k["id"])
        sections.append("## 评分校准（历史偏差修正）\n" + "\n".join(parts))

    # 5. 薪资参考
    salary_knowledge = query_knowledge(knowledge_type="salary_range", category=job_name, limit=2)
    if salary_knowledge:
        parts = [f"- {k['content'][:150]}" for k in salary_knowledge]
        for k in salary_knowledge:
            update_knowledge_usage(k["id"])
        sections.append("## 薪资参考\n" + "\n".join(parts))

    return "\n\n".join(sections) if sections else ""


# ══════════════════════════════════════════
# 知识库统计
# ══════════════════════════════════════════

def get_knowledge_stats() -> dict:
    """获取知识库统计"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT knowledge_type, COUNT(*) as cnt, AVG(confidence) as avg_conf FROM knowledge GROUP BY knowledge_type"
        ).fetchall()

        stats = {}
        total = 0
        for row in rows:
            stats[row["knowledge_type"]] = {
                "count": row["cnt"],
                "avg_confidence": round(row["avg_conf"] or 0, 2),
            }
            total += row["cnt"]

        stats["total"] = total

        # 最近学习的
        recent = conn.execute(
            "SELECT * FROM knowledge ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        stats["recent"] = [dict(r) for r in recent]

        return stats
    finally:
        conn.close()


# ══════════════════════════════════════════
# 同步到 Obsidian
# ══════════════════════════════════════════

def sync_knowledge_to_obsidian(job_folder: str, job_name: str):
    """将知识库中的岗位相关知识同步到 Obsidian"""
    from .models import get_settings

    if not job_folder:
        return False

    folder = Path(job_folder.replace('\\', '/'))
    if not folder.exists():
        folder = Path(os.path.expanduser(job_folder))
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return False

    # 查询该岗位的所有知识
    all_knowledge = query_knowledge(category=job_name, limit=50)
    if not all_knowledge:
        return False

    # 按类型分组
    by_type = {}
    for k in all_knowledge:
        kt = k["knowledge_type"]
        if kt not in by_type:
            by_type[kt] = []
        by_type[kt].append(k)

    # 构建 Markdown
    lines = [f"# {job_name} - 知识库\n"]
    lines.append(f"*最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

    type_labels = {
        "job_profile": "📋 岗位画像",
        "success_pattern": "✅ 成功案例",
        "reject_pattern": "❌ 淘汰规律",
        "skill_demand": "🔧 技能需求",
        "salary_range": "💰 薪资参考",
        "candidate_pool": "👥 候选人画像",
        "scoring_calibration": "📊 评分校准",
        "industry_trend": "📈 行业趋势",
    }

    for kt, items in by_type.items():
        label = type_labels.get(kt, kt)
        lines.append(f"\n## {label}\n")
        for item in items:
            lines.append(f"- **{item['title']}**（置信度: {item['confidence']}）")
            content = item["content"][:500]
            if content.startswith("{"):
                try:
                    data = json.loads(content)
                    for k, v in data.items():
                        if isinstance(v, list):
                            lines.append(f"  - {k}: {', '.join(str(i) for i in v[:5])}")
                        else:
                            lines.append(f"  - {k}: {v}")
                except Exception:
                    lines.append(f"  {content}")
            else:
                lines.append(f"  {content}")

    # 写入文件
    target_file = folder / f"{job_name}_知识库.md"
    try:
        target_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"[知识库] 已同步到 Obsidian: {target_file}")
        return True
    except Exception as e:
        print(f"[知识库] Obsidian 同步失败: {e}")
        return False
