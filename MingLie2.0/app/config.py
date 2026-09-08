from __future__ import annotations

import os
from pathlib import Path


def _load_local_env(root: Path) -> None:
    """Load a small local .env file without adding a runtime dependency."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_ROOT = Path(__file__).resolve().parent.parent
_load_local_env(_ROOT)


class Config:
    ROOT = _ROOT
    INSTANCE_PATH = ROOT / "instance"
    DATABASE = Path(os.getenv("MINGLIE_DATABASE", INSTANCE_PATH / "minglie.db"))
    SEARCH_PROVIDER = os.getenv("MINGLIE_SEARCH_PROVIDER", "playwright").lower()
    _browser_path = Path(os.getenv("MINGLIE_BROWSER_DATA_DIR", "instance/browser_data"))
    BROWSER_DATA_DIR = _browser_path if _browser_path.is_absolute() else ROOT / _browser_path
    PLAYWRIGHT_CHANNEL = os.getenv("MINGLIE_PLAYWRIGHT_CHANNEL", "chrome")
    SCRAPE_MAX_PAGES = min(max(int(os.getenv("MINGLIE_SCRAPE_MAX_PAGES", "3")), 1), 5)
    AI_BASE_URL = os.getenv("MINGLIE_AI_BASE_URL", "")
    AI_API_KEY = os.getenv("MINGLIE_AI_API_KEY", "")
    AI_MODEL = os.getenv("MINGLIE_AI_MODEL", "")
    SECRET_KEY = os.getenv("MINGLIE_SECRET_KEY", "minglie-local-development")
