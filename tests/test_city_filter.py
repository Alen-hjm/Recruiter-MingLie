import asyncio
import pytest


@pytest.mark.parametrize('changes', [True, False])
def test_city_requires_selected_option_and_changed_results(tmp_path, monkeypatch, changes):
    from app import scraper_liepin as module
    from app.scrape_state import ScrapeNeedsAttention
    monkeypatch.setattr(module.app_config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(module.app_config, 'SCREENSHOT_DIR', tmp_path)
    scraper = module.LiepinHunterScraper({})
    clicked = []

    class Selected:
        async def all_text_contents(self): return ['深圳'] if clicked else []
        async def count(self): return int(bool(clicked))

    class Group:
        async def count(self): return 1
        async def is_visible(self): return True
        def filter(self, **kwargs): return self
        def get_by_text(self, *args, **kwargs): return self
        def locator(self, selector): return Selected() if 'selected' in selector else self

    class Page:
        def locator(self, selector): return Group()

    class Pacer:
        async def click(self, target, check): clicked.append(True)

    async def marker(page): return ('1', 'new' if clicked and changes else 'old')
    async def noop(*args): pass
    scraper.pacer = Pacer()
    monkeypatch.setattr(scraper, '_page_marker', marker)
    monkeypatch.setattr(scraper, '_check_page', noop)
    monkeypatch.setattr(module.asyncio, 'sleep', noop)
    if changes:
        asyncio.run(scraper._select_city(Page(), '深圳'))
    else:
        with pytest.raises(ScrapeNeedsAttention, match='未确认城市筛选'):
            asyncio.run(scraper._select_city(Page(), '深圳'))
    assert len(clicked) == 1
