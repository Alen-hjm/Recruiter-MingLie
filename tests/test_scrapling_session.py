import asyncio
import json

import pytest


def test_callback_failure_is_not_reported_as_success(tmp_path):
    from app.scrapling_session import run_session
    from app.scrape_state import ScrapeNeedsAttention
    calls = []
    class Session:
        def __init__(self, **options):
            assert options["retries"] == 1
            assert options["google_search"] is False
            assert options["disable_resources"] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): calls.append("closed")
        async def fetch(self, url, **options):
            # Match upstream: callback errors need not escape fetch().
            await options["page_action"](object())
            await options["page_action"](object())
    async def fail(page):
        calls.append("saved_partial")
        raise ScrapeNeedsAttention("页面空白")
    with pytest.raises(ScrapeNeedsAttention, match="页面空白"):
        asyncio.run(run_session("https://example.com", {"user_data_dir": str(tmp_path)},
                                fail, session_factory=Session))
    assert calls == ["saved_partial", "closed"]


@pytest.mark.parametrize("invoke", [True, False])
def test_success_requires_callback_completion(tmp_path, invoke):
    from app.scrapling_session import run_session
    calls = []
    class Session:
        def __init__(self, **options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def fetch(self, url, **options):
            if invoke:
                await options["page_action"](object())
    async def action(page): calls.append("collected")
    run = run_session("https://example.com", {"user_data_dir": str(tmp_path)},
                      action, session_factory=Session)
    if invoke:
        asyncio.run(run)
        assert calls == ["collected"]
    else:
        with pytest.raises(RuntimeError, match="did not execute"):
            asyncio.run(run)


def test_navigation_log_excludes_private_urls(tmp_path):
    from app.scrape_diagnostics import NavigationLog, page_kind
    log = NavigationLog(tmp_path)
    log.stage("navigation", page=page_kind("https://h.liepin.com/resume/private-person?token=secret"))
    log.stage("navigation", page=page_kind("about:blank"))
    content = log.save().read_text(encoding="utf-8")
    assert "secret" not in content and "private-person" not in content
    assert json.loads(content)["events"][-1]["page"] == "blank"


def test_stealth_isolates_profile_and_leaves_verification_manual(tmp_path):
    from app.scrapling_session import run_session
    options_seen = {}
    class Session:
        def __init__(self, **options): options_seen.update(options)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def fetch(self, url, **options):
            await options["page_action"](object())
    async def action(page): pass
    asyncio.run(run_session("https://example.com", {
        "user_data_dir": str(tmp_path), "scrapling_engine": "stealth",
    }, action, session_factory=Session))
    assert options_seen["user_data_dir"] == str(tmp_path / "scrapling-stealth")
    assert options_seen["solve_cloudflare"] is False
    assert options_seen["retries"] == 1


def test_unknown_engine_does_not_fall_back(tmp_path):
    from app.scrapling_session import run_session
    with pytest.raises(ValueError, match="引擎"):
        asyncio.run(run_session("https://example.com", {
            "user_data_dir": str(tmp_path), "scrapling_engine": "typo",
        }, None))


def test_duplicate_detail_does_not_persist_twice(tmp_path, monkeypatch):
    from app import scraper_liepin as module
    monkeypatch.setattr(module.app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module.app_config, "SCREENSHOT_DIR", tmp_path)
    saved = []
    scraper = module.LiepinHunterScraper({"on_resume": saved.append})
    row = {"detail_url": "https://h.liepin.com/resume?res_id_encode=fictional-a"}
    assert scraper._record_resume(row) is True
    assert scraper._record_resume(dict(row)) is False
    assert len(saved) == len(scraper.results) == 1


def test_completed_duplicate_details_pass_but_unread_details_do_not(tmp_path, monkeypatch):
    from app import scraper_liepin as module
    from app.scrape_state import ScrapeNeedsAttention
    monkeypatch.setattr(module.app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module.app_config, "SCREENSHOT_DIR", tmp_path)
    scraper = module.LiepinHunterScraper({"scraper": {"require_detail": True, "required_pages": 2}})
    scraper.pages_processed = 2
    with pytest.raises(ScrapeNeedsAttention, match="详情读取"):
        scraper._validate_collection()
    scraper.details_completed = 2
    scraper.duplicates_skipped = 2
    scraper._validate_collection()
    assert scraper.results == []
    scraper.pages_processed = 1
    with pytest.raises(ScrapeNeedsAttention, match="页数"):
        scraper._validate_collection()


def test_upstream_log_urls_are_redacted():
    import logging
    from app.scrapling_session import _RedactUrls
    record = logging.LogRecord("scrapling", logging.INFO, "", 0,
                               "Fetched %s", ("https://safe.liepin.com/x?token=secret",), None)
    assert _RedactUrls().filter(record)
    assert "secret" not in record.getMessage()
    assert "[URL omitted]" in record.getMessage()


def test_login_handoff_waits_for_user_before_accepting_login(tmp_path, monkeypatch):
    from app import scraper_liepin as module
    monkeypatch.setattr(module.app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module.app_config, "SCREENSHOT_DIR", tmp_path)
    notes = []
    scraper = module.LiepinHunterScraper({
        "scraper": {"manual_login_handoff": True}, "on_progress": notes.append,
    })
    class Locator:
        first = property(lambda self: self)
        async def count(self): return 1
        async def inner_text(self, **kwargs): return "页面内容"
        async def is_visible(self): return True
    class Page:
        url = "https://safe.liepin.com/intercept/ip/captcha/dispatch"
        def is_closed(self): return False
        def locator(self, *args): return Locator()
        def get_by_role(self, *args, **kwargs): return Locator()
    page = Page()
    waits = []
    async def user_completes_login(seconds):
        waits.append(page.url)
        page.url = "https://h.liepin.com/"
    monkeypatch.setattr(module.asyncio, "sleep", user_completes_login)
    asyncio.run(scraper._ensure_login(page, navigate=False))
    assert len(waits) == 2  # wait on verification, then confirm login twice
    assert any("手动完成" in note for note in notes)


@pytest.mark.parametrize("changed", [True, False])
def test_pagination_requires_new_list_not_only_new_number(tmp_path, monkeypatch, changed):
    from app import scraper_liepin as module
    from app.scrape_state import ScrapeNeedsAttention
    monkeypatch.setattr(module.app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module.app_config, "SCREENSHOT_DIR", tmp_path)
    scraper = module.LiepinHunterScraper({})
    class Pacer:
        async def click(self, target, check_page):
            await target.click()
    scraper.pacer = Pacer()
    clicks = []
    checks = []
    class Button:
        first = property(lambda self: self)
        async def count(self): return 1
        async def is_visible(self): return True
        async def get_attribute(self, name): return None
        async def click(self): clicks.append(1)
    class Page:
        def locator(self, selector): return Button()
    async def marker(page):
        return ("2", "new" if changed else "old") if clicks else ("1", "old")
    async def check(page):
        checks.append(1)
        if len(checks) > 2:
            raise ScrapeNeedsAttention("页面空白")
    async def no_wait(seconds): pass
    monkeypatch.setattr(scraper, "_page_marker", marker)
    monkeypatch.setattr(scraper, "_check_page", check)
    monkeypatch.setattr(module.asyncio, "sleep", no_wait)
    if changed:
        assert asyncio.run(scraper._go_next_page(Page())) is True
    else:
        with pytest.raises(ScrapeNeedsAttention, match="页面空白"):
            asyncio.run(scraper._go_next_page(Page()))
    assert clicks == [1]


def test_delayed_blank_aborts_stability_check(tmp_path, monkeypatch):
    from app import scraper_liepin as module
    from app.scrape_state import ScrapeNeedsAttention
    monkeypatch.setattr(module.app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module.app_config, "SCREENSHOT_DIR", tmp_path)
    scraper = module.LiepinHunterScraper({"scraper": {"stability_seconds": 60}})
    checks = []
    async def check(page):
        checks.append(1)
        if len(checks) == 2:
            raise ScrapeNeedsAttention("延迟空白")
    async def no_wait(seconds): pass
    monkeypatch.setattr(scraper, "_check_page", check)
    monkeypatch.setattr(module.asyncio, "sleep", no_wait)
    with pytest.raises(ScrapeNeedsAttention, match="延迟空白"):
        asyncio.run(scraper._observe_search_stability(object()))
    assert len(checks) == 2
