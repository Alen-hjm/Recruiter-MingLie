"""Optional Scrapling transport; page failures must survive its callback handling."""
from pathlib import Path
import logging
import re


class _RedactUrls(logging.Filter):
    """Upstream fetch messages can contain session/security tokens in URLs."""
    def filter(self, record):
        record.msg = re.sub(r"https?://[^\s<>]+", "[URL omitted]", record.getMessage())
        record.args = ()
        return True


async def run_session(url, settings, action, setup=None, session_factory=None):
    engine = settings.get("scrapling_engine", "dynamic")
    if engine not in {"dynamic", "stealth"}:
        raise ValueError("不支持的 Scrapling 引擎")
    if session_factory is None:
        try:
            from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession
        except ImportError as exc:
            from .scrape_state import ScrapeNeedsAttention
            raise ScrapeNeedsAttention(
                "Scrapling 依赖未安装，请安装 requirements-scrapling.txt 并安装 Chromium。"
            ) from exc
        session_factory = AsyncStealthySession if engine == "stealth" else AsyncDynamicSession

    # Preserve existing DynamicSession profiles; never mix different engines.
    profile = Path(settings["user_data_dir"]) / ("scrapling-stealth" if engine == "stealth" else "scrapling")
    profile.mkdir(parents=True, exist_ok=True)
    failure = None
    completed = False

    async def guarded_action(page):
        nonlocal failure, completed
        # An upstream callback failure must never trigger another collection.
        if failure is not None or completed:
            return
        try:
            await action(page)
            completed = True
        except Exception as exc:
            failure = exc

    async def guarded_setup(page):
        nonlocal failure
        if setup:
            try:
                await setup(page)
            except Exception as exc:
                failure = exc

    try:
        # Apply once per process, before upstream navigation logs are produced.
        logger = logging.getLogger("scrapling")
        if not any(isinstance(item, _RedactUrls) for item in logger.filters):
            logger.addFilter(_RedactUrls())
        engine_options = {"solve_cloudflare": False} if engine == "stealth" else {}
        async with session_factory(
            headless=settings.get("headless", False),
            user_data_dir=str(profile),
            timeout=settings.get("timeout", 30000),
            retries=1,
            google_search=False,
            disable_resources=False,
            block_ads=False,
            locale="zh-CN",
            **engine_options,
        ) as session:
            await session.fetch(url, page_action=guarded_action,
                                page_setup=guarded_setup,
                                load_dom=False, network_idle=False)
    except Exception:
        if failure is not None:
            raise failure
        raise
    if failure is not None:
        raise failure
    if not completed:
        raise RuntimeError("Scrapling did not execute the collection callback")
