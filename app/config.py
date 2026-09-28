# -*- coding: utf-8 -*-
"""
明猎 - 集中配置。

为什么要有这个文件：
  重构前，路径、密钥、监听地址、CORS 开关散落在 run.py / server.py / models.py
  三处，改一个行为要翻三个文件，而且 app.secret_key = os.urandom(24) 每次重启
  都换一把，导致所有登录态失效、也无法多进程部署。

设计原则：
  1. 环境变量优先 —— 所有会随环境变化的值（密钥、监听地址、数据目录、CORS 白名单）
     都从环境变量读取；代码里只留「本地开发开箱可用」的默认值。
  2. 单一来源 —— 全项目的路径与开关都从这里取，不在别处再写一遍。
  3. 密钥不必落库 —— LLM API Key 优先读环境变量，数据库里的值只作为兼容旧安装的兜底。
"""

import os
import secrets
from pathlib import Path


# ------------------------------------------------------------------ 基础路径
BASE_DIR = Path(__file__).resolve().parent.parent

TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
REPORT_TEMPLATE_DIR = BASE_DIR / "config" / "report_templates"


def _env(name: str, default: str = "") -> str:
    """读字符串环境变量，空字符串视为未设置"""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    """读布尔环境变量，接受 1/true/yes/on（大小写不敏感）"""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ------------------------------------------------------------------ 运行模式
ENV = _env("MINGLIE_ENV", "development")
IS_PRODUCTION = ENV.lower() in ("prod", "production")
DEBUG = _env_bool("MINGLIE_DEBUG", False)


# ------------------------------------------------------------------ 数据目录
# data/ 与 browser_data/ 都在 .gitignore 中排除
DATA_DIR = Path(_env("MINGLIE_DATA_DIR", str(BASE_DIR / "data")))
BROWSER_DATA_DIR = Path(_env("MINGLIE_BROWSER_DIR", str(BASE_DIR / "browser_data")))
SCREENSHOT_DIR = Path(_env("MINGLIE_SCREENSHOT_DIR", str(BASE_DIR / "screenshots")))
BACKUP_DIR = DATA_DIR / "backups"

DB_PATH = DATA_DIR / "minglie.db"


# ------------------------------------------------------------------ 监听地址
# 默认只监听回环地址。重构前是 0.0.0.0 —— 在公共 WiFi 下同网段任何人都能
# 访问到候选人简历库，对一个本地工具来说没有必要。
HOST = _env("MINGLIE_HOST", "127.0.0.1")
PORT = _env_int("MINGLIE_PORT", 5000)

# 请求体上限（MB）。简历全文 + 批量提交可能较大，但没必要放开到无限。
MAX_CONTENT_LENGTH_MB = _env_int("MINGLIE_MAX_BODY_MB", 32)


# ------------------------------------------------------------------ 会话密钥
def _load_or_create_secret_key() -> str:
    """
    优先用环境变量；没配就持久化一个随机密钥到 data/.secret_key。

    为什么不用 os.urandom(24)：
      那样每次进程重启都会换密钥，所有已登录用户的 session 立即失效，
      而且多 worker 之间无法共享，部署方式被锁死在单进程。
    为什么落盘而不是硬编码：
      硬编码的密钥一旦进 git 就等于公开，任何拿到仓库的人都能伪造会话。
    """
    from_env = os.environ.get("MINGLIE_SECRET_KEY")
    if from_env:
        return from_env

    key_file = DATA_DIR / ".secret_key"
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        key_file.write_text(generated, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass  # Windows 上 chmod 语义有限，忽略
        return generated
    except OSError:
        # 数据目录不可写（只读挂载等）时退化为进程内随机，至少不会崩
        return secrets.token_urlsafe(48)


SECRET_KEY = _load_or_create_secret_key()


# ------------------------------------------------------------------ 大模型
# 供「未在设置页配置 Key」的场景使用；用户在设置页填的值优先。
LLM_API_KEY = _env("MINGLIE_LLM_API_KEY", _env("LLM_API_KEY", ""))
LLM_BASE_URL = _env("MINGLIE_LLM_BASE_URL", _env("LLM_BASE_URL", "https://api.deepseek.com"))
LLM_MODEL = _env("MINGLIE_LLM_MODEL", _env("LLM_MODEL", "deepseek-chat"))


def default_llm_config() -> dict:
    """环境变量里的 LLM 配置（设置页未填时的兜底）"""
    return {
        "api_key": LLM_API_KEY,
        "base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
    }


def resolve_llm_config(user_settings: dict | None) -> dict:
    """
    合并「环境变量」与「用户设置」，用户设置优先。

    返回 {api_key, base_url, model}，api_key 可能为空（调用方需自行校验）。
    """
    env_cfg = default_llm_config()
    s = user_settings or {}
    return {
        "api_key": (s.get("llm_api_key") or "").strip() or env_cfg["api_key"],
        "base_url": (s.get("llm_base_url") or "").strip() or env_cfg["base_url"],
        "model": (s.get("llm_model") or "").strip() or env_cfg["model"],
    }


# ------------------------------------------------------------------ CORS
# Chrome 扩展的 Origin 形如 chrome-extension://<id>，安装后 ID 才会确定。
# 默认放开扩展协议 + 本地回环；把具体 ID 写进 MINGLIE_EXTENSION_ORIGINS 更安全。
_origins_raw = _env("MINGLIE_EXTENSION_ORIGINS", "")
if _origins_raw:
    EXTENSION_ORIGINS: list[str] = [o.strip() for o in _origins_raw.split(",") if o.strip()]
else:
    EXTENSION_ORIGINS = []


# ------------------------------------------------------------------ 鉴权
# 扩展的访问凭据与 LLM API Key 已经完全解耦（见 app/auth.py）。
# 这个开关只用于兼容「升级前就已经装在 Chrome 里、还在拿 LLM Key 当凭据」的旧扩展。
ALLOW_LEGACY_KEY_AUTH = _env_bool("MINGLIE_ALLOW_LEGACY_KEY_AUTH", True)

# 单个用户最多可持有的有效 token 数
MAX_TOKENS_PER_USER = _env_int("MINGLIE_MAX_TOKENS_PER_USER", 20)


# ------------------------------------------------------------------ 抓取
# 猎聘抓取仍然保留，这里只集中默认参数
SCRAPE_CDP_PORT = _env_int("MINGLIE_CDP_PORT", 9222)
SCRAPE_HEADLESS = _env_bool("MINGLIE_SCRAPE_HEADLESS", False)
SCRAPE_TIMEOUT_MS = _env_int("MINGLIE_SCRAPE_TIMEOUT_MS", 30000)
SCRAPE_MAX_PAGES_LIMIT = _env_int("MINGLIE_SCRAPE_MAX_PAGES", 5)


def ensure_dirs() -> None:
    """确保运行期需要的目录存在（启动时调用一次）"""
    for d in (DATA_DIR, BROWSER_DATA_DIR, BACKUP_DIR, REPORT_TEMPLATE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
