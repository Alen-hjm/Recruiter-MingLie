"""
数据库模型 - SQLite
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = Path("data/minglie.db")


def _safe_add_column(conn, table: str, column: str, col_type: str):
    """安全地给表加列（已存在则跳过）"""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在


def get_db():
    """获取数据库连接"""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scrape_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_id INTEGER,
            keyword TEXT NOT NULL,
            city TEXT,
            platform TEXT DEFAULT 'liepin',
            max_pages INTEGER DEFAULT 3,
            status TEXT DEFAULT 'pending',
            result_count INTEGER DEFAULT 0,
            resumes_json TEXT,
            error_msg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS score_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            scrape_task_id INTEGER,
            job_name TEXT,
            job_description TEXT,
            mode TEXT DEFAULT '100',
            status TEXT DEFAULT 'pending',
            scored_json TEXT,
            passed_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (scrape_task_id) REFERENCES scrape_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            keyword TEXT,
            job_description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            llm_api_key TEXT,
            llm_base_url TEXT DEFAULT 'https://api.deepseek.com',
            llm_model TEXT DEFAULT 'deepseek-chat',
            default_city TEXT DEFAULT '上海',
            default_pages INTEGER DEFAULT 3,
            obsidian_kb_path TEXT,
            obsidian_job_path TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS interview_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            candidate_name TEXT,
            resume_key TEXT,
            ai_score INTEGER,
            feedback_type TEXT DEFAULT 'reject',
            reason TEXT NOT NULL,
            -- 候选人管道状态
            pipeline_stage TEXT DEFAULT 'recommended',
            -- recommended: AI推荐
            -- contacted: 已电话联系
            -- submitted: 已推荐给甲方
            -- interview: 进入面试
            -- offer: 发了offer
            -- hired: 已录用
            -- rejected: 已淘汰
            reject_stage TEXT,
            -- phone_screen: 电话筛选淘汰
            -- client_reject: 甲方淘汰
            -- interview_reject: 面试淘汰
            -- offer_declined: 候选人拒offer
            company_rejected TEXT,
            -- 甲方公司名称（如果被淘汰，记录是哪个甲方淘汰的）
            interview_round INTEGER DEFAULT 0,
            -- 面试轮次
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            synced_to_obsidian INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_scrape_user ON scrape_tasks(user_id);
        CREATE INDEX IF NOT EXISTS idx_score_user ON score_tasks(user_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
    """)
    conn.commit()

    # ── 迁移：给旧表加新字段（安全，已存在则跳过）──
    _safe_add_column(conn, "interview_feedback", "pipeline_stage", "TEXT DEFAULT 'recommended'")
    _safe_add_column(conn, "interview_feedback", "reject_stage", "TEXT")
    _safe_add_column(conn, "interview_feedback", "company_rejected", "TEXT")
    _safe_add_column(conn, "interview_feedback", "interview_round", "INTEGER DEFAULT 0")
    _safe_add_column(conn, "interview_feedback", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _safe_add_column(conn, "interview_feedback", "score100_json", "TEXT")

    conn.close()


# ── 简历唯一ID提取 ──

def extract_resume_id(resume: dict) -> str:
    """从简历数据中提取唯一标识（优先用猎聘URL中的res_id_encode）"""
    url = resume.get("detail_url", "")
    if url and "res_id_encode=" in url:
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if "res_id_encode" in params:
                return params["res_id_encode"][0]
        except Exception:
            pass
    # fallback: 用URL本身
    if url:
        return url
    # 最后fallback: name+title
    return (resume.get("name", "") + resume.get("title", "")).strip()


def _get_resume_key(resume: dict) -> str:
    """生成简历的唯一标识（兼容旧数据）"""
    return resume.get("detail_url", "") or (resume.get("name", "") + resume.get("title", ""))


# ── User 操作 ──

class User:
    def __init__(self, id, username, display_name=None):
        self.id = id
        self.username = username
        self.display_name = display_name or username

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)


def create_user(username: str, password: str, display_name: str = "") -> User:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), display_name or username),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.execute("INSERT INTO settings (user_id) VALUES (?)", (user["id"],))
        conn.commit()
        return User(user["id"], user["username"], user["display_name"])
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> User | None:
    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                         (datetime.now().isoformat(), user["id"]))
            conn.commit()
            return User(user["id"], user["username"], user["display_name"])
        return None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> User | None:
    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            return User(user["id"], user["username"], user["display_name"])
        return None
    finally:
        conn.close()


# ── 抓取任务 ──

def create_scrape_task(user_id: int, keyword: str, city: str, platform: str, max_pages: int, job_id: int = None) -> int:
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO scrape_tasks (user_id, job_id, keyword, city, platform, max_pages) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, job_id, keyword, city, platform, max_pages),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_scrape_task(task_id: int, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(task_id)
        conn.execute(f"UPDATE scrape_tasks SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_scrape_tasks(user_id: int, limit: int = 20, job_id: int = None) -> list:
    conn = get_db()
    try:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM scrape_tasks WHERE user_id = ? AND job_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scrape_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_scrape_task(task_id: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM scrape_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_scrape_results_dedup(user_id: int, job_id: int, keyword: str, city: str, platform: str, max_pages: int, new_resumes: list) -> dict:
    """保存抓取结果并去重，返回 {task_id, saved_count, dup_count}"""
    # 1. 先获取该岗位已有的简历ID集合
    existing_ids = get_existing_resume_ids(user_id, job_id)

    # 2. 过滤重复
    saved = []
    dup_count = 0
    for r in new_resumes:
        rid = extract_resume_id(r)
        if rid in existing_ids:
            dup_count += 1
        else:
            existing_ids.add(rid)
            saved.append(r)

    # 3. 创建任务并保存
    task_id = create_scrape_task(user_id, keyword, city, platform, max_pages, job_id=job_id)
    update_scrape_task(
        task_id,
        status="done",
        result_count=len(saved),
        resumes_json=json.dumps(saved, ensure_ascii=False),
        finished_at=datetime.now().isoformat(),
    )

    return {"task_id": task_id, "saved_count": len(saved), "dup_count": dup_count}


def get_existing_resume_ids(user_id: int, job_id: int) -> set:
    """获取岗位下已有的简历唯一ID集合"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT resumes_json FROM scrape_tasks WHERE user_id = ? AND job_id = ? AND status = 'done' AND resumes_json IS NOT NULL",
            (user_id, job_id),
        ).fetchall()
        ids = set()
        for row in rows:
            resumes = json.loads(row["resumes_json"])
            for r in resumes:
                ids.add(extract_resume_id(r))
        return ids
    finally:
        conn.close()


# ── 评分任务 ──

def create_score_task(user_id: int, scrape_task_id: int, job_name: str, job_description: str, mode: str) -> int:
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO score_tasks (user_id, scrape_task_id, job_name, job_description, mode) VALUES (?, ?, ?, ?, ?)",
            (user_id, scrape_task_id, job_name, job_description, mode),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_score_task(task_id: int, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(task_id)
        conn.execute(f"UPDATE score_tasks SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_score_tasks(user_id: int, limit: int = 20) -> list:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM score_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_score_task(task_id: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM score_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_scored_resume_ids(user_id: int, job_id: int) -> set:
    """获取岗位下已经评分过的简历唯一ID集合（从最新一次评分任务中取）"""
    conn = get_db()
    try:
        # 取该用户最近一次该岗位的已完成评分任务
        row = conn.execute(
            """SELECT scored_json FROM score_tasks
               WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
               AND job_name = (SELECT name FROM jobs WHERE id = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, job_id),
        ).fetchone()
        if not row or not row["scored_json"]:
            return set()
        scored = json.loads(row["scored_json"])
        ids = set()
        for r in scored:
            ids.add(extract_resume_id(r))
        return ids
    finally:
        conn.close()


def get_unscored_resumes(user_id: int, job_id: int) -> tuple[list, int]:
    """获取岗位下未评分的简历，返回 (unscored_list, already_scored_count)"""
    all_resumes = get_resumes_by_job(user_id, job_id)
    scored_ids = get_scored_resume_ids(user_id, job_id)

    if not scored_ids:
        return all_resumes, 0

    unscored = []
    for r in all_resumes:
        rid = extract_resume_id(r)
        if rid not in scored_ids:
            unscored.append(r)

    return unscored, len(all_resumes) - len(unscored)


# ── 岗位配置 ──

def create_job(user_id: int, name: str, keyword: str, job_description: str) -> int:
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO jobs (user_id, name, keyword, job_description) VALUES (?, ?, ?, ?)",
            (user_id, name, keyword, job_description),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_jobs(user_id: int) -> list:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_job(job_id: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_job(job_id: int, **kwargs):
    conn = get_db()
    try:
        sets = ["updated_at = ?"]
        vals = [datetime.now().isoformat()]
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def delete_job(job_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


# ── 设置 ──

def get_settings(user_id: int) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def update_settings(user_id: int, **kwargs):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO settings (user_id) VALUES (?)", (user_id,))
            conn.commit()
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(user_id)
        conn.execute(f"UPDATE settings SET {', '.join(sets)} WHERE user_id = ?", vals)
        conn.commit()
    finally:
        conn.close()


# ── 简历编辑/删除 ──

def edit_resume(user_id: int, job_id: int, resume_key: str, updates: dict) -> bool:
    """编辑岗位下某份简历的信息"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, resumes_json FROM scrape_tasks WHERE user_id = ? AND job_id = ? AND status = 'done' AND resumes_json IS NOT NULL",
            (user_id, job_id),
        ).fetchall()
        for row in rows:
            resumes = json.loads(row["resumes_json"])
            modified = False
            for r in resumes:
                if _get_resume_key(r) == resume_key:
                    for k, v in updates.items():
                        r[k] = v
                    modified = True
                    break
            if modified:
                conn.execute(
                    "UPDATE scrape_tasks SET resumes_json = ? WHERE id = ?",
                    (json.dumps(resumes, ensure_ascii=False), row["id"]),
                )
                conn.commit()
                return True
        return False
    finally:
        conn.close()


def delete_resume(user_id: int, job_id: int, resume_key: str) -> bool:
    """删除岗位下某份简历"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, resumes_json, result_count FROM scrape_tasks WHERE user_id = ? AND job_id = ? AND status = 'done' AND resumes_json IS NOT NULL",
            (user_id, job_id),
        ).fetchall()
        deleted = False
        for row in rows:
            resumes = json.loads(row["resumes_json"])
            new_resumes = [r for r in resumes if _get_resume_key(r) != resume_key]
            if len(new_resumes) < len(resumes):
                conn.execute(
                    "UPDATE scrape_tasks SET resumes_json = ?, result_count = ? WHERE id = ?",
                    (json.dumps(new_resumes, ensure_ascii=False), len(new_resumes), row["id"]),
                )
                conn.commit()
                deleted = True
        return deleted
    finally:
        conn.close()


def delete_low_score_resumes(user_id: int, job_id: int, threshold: int = 60) -> int:
    """删除岗位下评分低于阈值的简历"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, resumes_json, result_count FROM scrape_tasks WHERE user_id = ? AND job_id = ? AND status = 'done' AND resumes_json IS NOT NULL",
            (user_id, job_id),
        ).fetchall()
        total_deleted = 0
        for row in rows:
            resumes = json.loads(row["resumes_json"])
            new_resumes = []
            for r in resumes:
                score = 0
                if r.get("score100"):
                    score = r["score100"].get("total_score", 0)
                elif r.get("analysis"):
                    score = r["analysis"].get("total_score", 0) * 10
                if score < threshold and score > 0:
                    total_deleted += 1
                    continue
                new_resumes.append(r)
            if len(new_resumes) < len(resumes):
                conn.execute(
                    "UPDATE scrape_tasks SET resumes_json = ?, result_count = ? WHERE id = ?",
                    (json.dumps(new_resumes, ensure_ascii=False), len(new_resumes), row["id"]),
                )
                conn.commit()
        return total_deleted
    finally:
        conn.close()


# ── 统计 ──

def get_resumes_by_job(user_id: int, job_id: int) -> list:
    """获取岗位下所有已抓取的简历（用 res_id_encode 去重，保留最新的）"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM scrape_tasks WHERE user_id = ? AND job_id = ? AND status = 'done' AND resumes_json IS NOT NULL ORDER BY created_at DESC",
            (user_id, job_id),
        ).fetchall()
        all_resumes = []
        seen = set()
        for row in rows:
            resumes = json.loads(row["resumes_json"])
            for r in resumes:
                rid = extract_resume_id(r)
                if rid not in seen:
                    seen.add(rid)
                    all_resumes.append(r)
        return all_resumes
    finally:
        conn.close()


def get_user_stats(user_id: int) -> dict:
    conn = get_db()
    try:
        total_scraped = conn.execute(
            "SELECT COALESCE(SUM(result_count), 0) as total FROM scrape_tasks WHERE user_id = ? AND status = 'done'",
            (user_id,),
        ).fetchone()["total"]

        total_scored = conn.execute(
            "SELECT COUNT(*) as total FROM score_tasks WHERE user_id = ? AND status = 'done'",
            (user_id,),
        ).fetchone()["total"]

        total_passed = conn.execute(
            "SELECT COALESCE(SUM(passed_count), 0) as total FROM score_tasks WHERE user_id = ? AND status = 'done'",
            (user_id,),
        ).fetchone()["total"]

        pending_score = conn.execute(
            "SELECT COUNT(*) as total FROM scrape_tasks WHERE user_id = ? AND status = 'done' AND id NOT IN (SELECT DISTINCT scrape_task_id FROM score_tasks WHERE user_id = ? AND status = 'done' AND scrape_task_id IS NOT NULL)",
            (user_id, user_id),
        ).fetchone()["total"]

        recent_scrapes = conn.execute(
            "SELECT * FROM scrape_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()

        recent_scores = conn.execute(
            "SELECT * FROM score_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()

        return {
            "total_scraped": total_scraped,
            "total_scored": total_scored,
            "total_passed": total_passed,
            "pending_score": pending_score,
            "recent_scrapes": [dict(r) for r in recent_scrapes],
            "recent_scores": [dict(r) for r in recent_scores],
        }
    finally:
        conn.close()


# ── 面试反馈 ──

def create_interview_feedback(user_id: int, job_id: int, candidate_name: str, resume_key: str, ai_score: int, feedback_type: str, reason: str) -> int:
    """创建面试反馈记录"""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO interview_feedback (user_id, job_id, candidate_name, resume_key, ai_score, feedback_type, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, job_id, candidate_name, resume_key, ai_score, feedback_type, reason),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_interview_feedbacks(user_id: int, job_id: int = None, limit: int = 50) -> list:
    """获取面试反馈列表"""
    conn = get_db()
    try:
        if job_id:
            rows = conn.execute(
                """SELECT f.*, j.name as job_name FROM interview_feedback f
                   LEFT JOIN jobs j ON f.job_id = j.id
                   WHERE f.user_id = ? AND f.job_id = ?
                   ORDER BY f.created_at DESC LIMIT ?""",
                (user_id, job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT f.*, j.name as job_name FROM interview_feedback f
                   LEFT JOIN jobs j ON f.job_id = j.id
                   WHERE f.user_id = ?
                   ORDER BY f.created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_feedback_synced(feedback_id: int):
    """标记反馈已同步到 Obsidian"""
    conn = get_db()
    try:
        conn.execute("UPDATE interview_feedback SET synced_to_obsidian = 1 WHERE id = ?", (feedback_id,))
        conn.commit()
    finally:
        conn.close()


# ── 候选人管道管理 ──

def create_candidate_from_score(user_id: int, job_id: int, resume: dict) -> int:
    """评分后自动创建候选人记录（AI推荐阶段）"""
    name = resume.get("name", resume.get("title", "未知"))
    resume_key = resume.get("detail_url", "") or (name + resume.get("title", ""))
    ai_score = 0
    if resume.get("score100"):
        s = resume["score100"]
        ai_score = s.get("total_score", 0) if isinstance(s, dict) else s

    conn = get_db()
    try:
        # 检查是否已存在
        existing = conn.execute(
            "SELECT id FROM interview_feedback WHERE user_id = ? AND job_id = ? AND resume_key = ?",
            (user_id, job_id, resume_key),
        ).fetchone()
        if existing:
            return existing["id"]

        cursor = conn.execute(
            """INSERT INTO interview_feedback
               (user_id, job_id, candidate_name, resume_key, ai_score, feedback_type, reason, pipeline_stage)
               VALUES (?, ?, ?, ?, ?, 'recommend', 'AI评分推荐', 'recommended')""",
            (user_id, job_id, name, resume_key, ai_score),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def update_candidate_pipeline(feedback_id: int, stage: str, reason: str = "",
                               reject_stage: str = "", company_rejected: str = "",
                               interview_round: int = 0) -> bool:
    """更新候选人管道状态
    
    stage: recommended → contacted → submitted → interview → offer → hired / rejected
    reject_stage: phone_screen / client_reject / interview_reject / offer_declined
    """
    valid_stages = ["recommended", "contacted", "submitted", "interview", "offer", "hired", "rejected"]
    if stage not in valid_stages:
        return False

    conn = get_db()
    try:
        sets = ["pipeline_stage = ?", "updated_at = ?"]
        vals = [stage, datetime.now().isoformat()]

        if reason:
            sets.append("reason = ?")
            vals.append(reason)
        if stage == "rejected":
            sets.append("feedback_type = 'reject'")
            if reject_stage:
                sets.append("reject_stage = ?")
                vals.append(reject_stage)
            if company_rejected:
                sets.append("company_rejected = ?")
                vals.append(company_rejected)
        else:
            sets.append("feedback_type = ?")
            vals.append(stage)
        if interview_round > 0:
            sets.append("interview_round = ?")
            vals.append(interview_round)

        vals.append(feedback_id)
        conn.execute(f"UPDATE interview_feedback SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()

        # 获取更新后的记录，用于触发学习
        row = conn.execute("SELECT * FROM interview_feedback WHERE id = ?", (feedback_id,)).fetchone()

    finally:
        conn.close()

    # 淘汰时触发 Agent 记忆学习
    if stage == "rejected" and row and reason:
        try:
            from .agent_memory import learn_from_feedback
            feedback_data = [{"reason": reason, "feedback_type": "reject", "candidate_name": row["candidate_name"]}]
            learn_from_feedback(row["user_id"], row["job_id"], feedback_data)
            print(f"[记忆] 已学习淘汰原因: {reason}")
        except Exception as e:
            print(f"[记忆] 学习失败: {e}")

    return True

def get_candidates_by_stage(user_id: int, job_id: int) -> dict:
    """获取岗位下按管道阶段分组的候选人"""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT f.*, j.name as job_name FROM interview_feedback f
               LEFT JOIN jobs j ON f.job_id = j.id
               WHERE f.user_id = ? AND f.job_id = ?
               ORDER BY f.ai_score DESC""",
            (user_id, job_id),
        ).fetchall()
    finally:
        conn.close()

    result = {
        "recommended": [],
        "contacted": [],
        "submitted": [],
        "interview": [],
        "offer": [],
        "hired": [],
        "rejected": [],
    }
    for row in rows:
        stage = row["pipeline_stage"] or "recommended"
        if stage in result:
            result[stage].append(dict(row))

    return result

def get_pipeline_summary(user_id: int, job_id: int) -> dict:
    """获取岗位候选人管道摘要"""
    stages = get_candidates_by_stage(user_id, job_id)
    summary = {}
    for stage, candidates in stages.items():
        summary[stage] = len(candidates)
    summary["total"] = sum(summary.values())
    return summary

def get_unsynced_feedbacks(user_id: int, job_id: int = None) -> list:
    """获取未同步到 Obsidian 的反馈"""
    conn = get_db()
    try:
        if job_id:
            rows = conn.execute(
                """SELECT f.*, j.name as job_name FROM interview_feedback f
                   LEFT JOIN jobs j ON f.job_id = j.id
                   WHERE f.user_id = ? AND f.job_id = ? AND f.synced_to_obsidian = 0
                   ORDER BY f.updated_at DESC""",
                (user_id, job_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT f.*, j.name as job_name FROM interview_feedback f
                   LEFT JOIN jobs j ON f.job_id = j.id
                   WHERE f.user_id = ? AND f.synced_to_obsidian = 0
                   ORDER BY f.updated_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
