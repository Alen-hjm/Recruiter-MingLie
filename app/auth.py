# -*- coding: utf-8 -*-
"""
明猎 - 扩展鉴权。

重构前的问题（这是原代码里最严重的一处）：
    server.py 的 _get_user_from_api_key() 直接拿请求头里的 X-API-Key 去匹配
    数据库 settings.llm_api_key：

        SELECT user_id FROM settings WHERE llm_api_key = ?

    于是「大模型密钥」同时充当了「后端登录凭据」，连锁后果：
      1. 轮换 LLM Key 会直接让扩展掉线；
      2. 任何拿到这把 Key 的人（浏览器存储、截图、日志）同时获得你后端的完整
         访问权，以及刷爆你余额的能力；
      3. 该函数还接受 ?api_key= 查询串传参，密钥会进 Flask 访问日志与浏览器历史；
      4. 轮换密钥 = 必须同时改后端与所有前端。

现在的做法：
    - 独立的访问令牌，与 LLM Key 无关。令牌形如 ml_<43 字符>，
      数据库只存 SHA-256 摘要，明文只在创建时返回一次。
    - 支持撤销、带标签、记录最后使用时间。
    - 兼容期：旧扩展仍可能发送 LLM Key 作为 X-API-Key。为了让已经在用的扩展
      不立刻失效，保留一条「遗留兼容」通道，但可通过 MINGLIE_ALLOW_LEGACY_KEY_AUTH=0
      随时关闭（关闭后旧扩展会收到 401，提示重新配置令牌）。
    - 查询串传参不再接受：凭据只从请求头读，避免密钥进入访问日志。
"""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

from flask import g, jsonify, request

from . import config


TOKEN_PREFIX = "ml_"
TOKEN_BYTES = 32          # secrets.token_urlsafe(32) → 43 字符
TOKEN_HEADER = "X-API-Token"
LEGACY_HEADER = "X-API-Key"


# ------------------------------------------------------------------ 建表
def init_auth_db() -> None:
    """创建访问令牌表（幂等）"""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                label TEXT DEFAULT '',
                token_hash TEXT NOT NULL UNIQUE,
                token_hint TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP,
                revoked_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _hash_token(token: str) -> str:
    """令牌是高熵随机串，SHA-256 摘要足够，且可建索引直接查"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 令牌生命周期
def create_token(user_id: int, label: str = "") -> str:
    """
    为用户签发一个新令牌，返回明文（仅此一次可见）。

    调用方有责任把明文立刻展示给用户并提醒保存；数据库里只有摘要。
    """
    conn = _connect()
    try:
        # 先清理超量的旧令牌，避免无限增长
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,),
        ).fetchone()["n"]
        if active >= config.MAX_TOKENS_PER_USER:
            conn.execute(
                """UPDATE api_tokens SET revoked_at = ?
                   WHERE id IN (
                       SELECT id FROM api_tokens
                       WHERE user_id = ? AND revoked_at IS NULL
                       ORDER BY COALESCE(last_used_at, created_at) ASC
                       LIMIT ?
                   )""",
                (datetime.now().isoformat(), user_id, active - config.MAX_TOKENS_PER_USER + 1),
            )

        token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        conn.execute(
            "INSERT INTO api_tokens (user_id, label, token_hash, token_hint) VALUES (?, ?, ?, ?)",
            (user_id, (label or "").strip()[:64], _hash_token(token), token[:7] + "…" + token[-4:]),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def list_tokens(user_id: int) -> list[dict]:
    """列出该用户的令牌（不含明文，只有提示串）"""
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT id, label, token_hint, created_at, last_used_at, revoked_at
               FROM api_tokens WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_token(user_id: int, token_id: int) -> bool:
    """撤销令牌。只能撤销自己的（user_id 参与 WHERE，防止越权）"""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (datetime.now().isoformat(), token_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def revoke_all_tokens(user_id: int) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (datetime.now().isoformat(), user_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _touch(token_hash: str) -> None:
    """更新最后使用时间（失败不影响主流程）"""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?",
            (datetime.now().isoformat(), token_hash),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


# ------------------------------------------------------------------ 校验
def _user_from_token(raw_token: str):
    """按新令牌校验；未命中返回 None"""
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None
    digest = _hash_token(raw_token)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT user_id, token_hash FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
            (digest,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    # 双重校验：即便数据库索引命中，也再做一次常量时间比较
    if not hmac.compare_digest(row["token_hash"], digest):
        return None
    _touch(row["token_hash"])
    from .models import get_user_by_id
    return get_user_by_id(row["user_id"])


def _user_from_legacy_key(raw_key: str):
    """
    兼容旧扩展：把 LLM API Key 当凭据用。

    这是一条**过渡通道**，新部署应当设 MINGLIE_ALLOW_LEGACY_KEY_AUTH=0 关掉。
    用 hmac.compare_digest 逐条比对，避免 SQL 层比较带来的时间侧信道。
    """
    if not config.ALLOW_LEGACY_KEY_AUTH or not raw_key:
        return None
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT user_id, llm_api_key FROM settings WHERE llm_api_key IS NOT NULL AND llm_api_key != ''"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        if hmac.compare_digest(str(row["llm_api_key"]), raw_key):
            from .models import get_user_by_id
            return get_user_by_id(row["user_id"])
    return None


def extract_credentials(req) -> tuple[str, str]:
    """
    从请求头取凭据。只认请求头，不认查询串 —— 避免密钥进入访问日志。

    返回 (token, legacy_key)，两者都可能为空。
    """
    token = (req.headers.get(TOKEN_HEADER) or "").strip()
    legacy = (req.headers.get(LEGACY_HEADER) or "").strip()
    return token, legacy


def authenticate_request(req):
    """
    校验一次请求，返回 user 或 None。

    优先新令牌；没有新令牌时才回落到遗留 Key。
    """
    token, legacy = extract_credentials(req)
    user = _user_from_token(token)
    if user:
        return user
    return _user_from_legacy_key(legacy)


# ------------------------------------------------------------------ 装饰器
def require_api_token(view):
    """
    用于 /api/extension/* 的鉴权装饰器。

    与 @login_required 的区别：这个走令牌（给无 Cookie 的浏览器扩展用），
    校验通过后把 user 挂在 g.api_user 上，同时兼容 current_user 语义。
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        user = authenticate_request(request)
        if user is None:
            hint = "请配置扩展访问令牌（设置页可生成）" if config.ALLOW_LEGACY_KEY_AUTH else \
                   "访问令牌无效或已撤销"
            return jsonify({"success": False, "error": hint, "code": "unauthorized"}), 401
        g.api_user = user
        return view(*args, **kwargs)

    return wrapper


def current_api_user():
    """在 @require_api_token 保护的视图里取当前用户"""
    return getattr(g, "api_user", None)
