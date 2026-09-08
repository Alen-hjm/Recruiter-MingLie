"""Visible Playwright adapter used by the recruiter agent's scrape_liepin tool."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path


class PlaywrightLiepinScraper:
    SEARCH_URL = "https://h.liepin.com/search/getConditionItem"

    def __init__(self, profile_dir: str | Path, channel: str, logger):
        self.profile_dir, self.channel, self.log = Path(profile_dir), channel, logger

    async def run(self, keyword: str, city: str, max_pages: int) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright 未安装。请执行 pip install -r requirements.txt，并执行 python -m playwright install chromium") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.log("正在启动可见 Playwright 浏览器…")
        async with async_playwright() as playwright:
            options = {"user_data_dir": str(self.profile_dir), "headless": False, "viewport": {"width": 1440, "height": 900}, "locale": "zh-CN", "slow_mo": 250}
            if self.channel: options["channel"] = self.channel
            try:
                context = await playwright.chromium.launch_persistent_context(**options)
            except Exception as exc:
                self.log(f"Chrome 通道启动失败，尝试 Playwright Chromium：{exc}", "warning")
                options.pop("channel", None); context = await playwright.chromium.launch_persistent_context(**options)
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto("https://h.liepin.com", wait_until="domcontentloaded", timeout=30000)
                if "login" in page.url or "passport" in page.url:
                    self.log("请在打开的浏览器完成登录；登录成功后任务会继续。", "waiting_login")
                    await page.wait_for_url(re.compile(r"https://h\.liepin\.com/.*"), timeout=300000)
                self.log("登录态可用，进入候选人搜索页面。")
                await page.goto(self.SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
                input_box = page.locator('.auto-input-wrap-v3 input[role="combobox"], input[role="combobox"], input[placeholder*="搜索"]').first
                await input_box.wait_for(state="visible", timeout=15000)
                await input_box.click(); await page.keyboard.press("Control+A"); await input_box.fill(keyword); await input_box.press("Enter")
                self.log(f"已提交搜索：{keyword}" + (f" · {city}" if city else ""))
                await page.wait_for_timeout(2500)
                results=[]
                for number in range(1, max_pages + 1):
                    cards = await page.query_selector_all('.tlog-common-resume-card, [class*="resume-card"], [class*="result-item"]')
                    visible=[card for card in cards if await card.is_visible()]
                    self.log(f"第 {number} 页发现 {len(visible)} 个候选人卡片。")
                    for index, card in enumerate(visible):
                        item = await self._extract(card, number, index)
                        if item: results.append(item)
                    if number == max_pages or not await self._next(page): break
                    await page.wait_for_timeout(1800)
                self.log(f"采集完成，共解析 {len(results)} 位候选人。")
                return results
            finally:
                await context.close()

    async def _extract(self, card, page_number: int, index: int) -> dict | None:
        try:
            raw = await card.evaluate("""(card) => {
              const text = e => e ? (e.innerText || e.textContent || '').trim() : '';
              const pick = selectors => { for (const s of selectors) { const e=card.querySelector(s); if(text(e)) return text(e); } return ''; };
              const link=card.querySelector('a[href*="res_id_encode"],a[href]');
              return {name:pick(['em','[class*="name"]']), headline:pick(['[class*="position"]','[class*="title"]']), company:pick(['[class*="company"]']), location:pick(['[class*="area"]','[class*="city"]','[class*="location"]']), education:pick(['[class*="edu"]']), experience:pick(['[class*="exp"]','[class*="year"]']), detail_url:link ? link.href : '', content:text(card)};
            }""")
            name = raw.get("name") or "未知候选人"
            stable = raw.get("detail_url") or f"{name}:{page_number}:{index}:{raw.get('content','')[:80]}"
            years = re.search(r"(\d+(?:\.\d+)?)\s*年", raw.get("experience", ""))
            skills = [x for x in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,}|[\u4e00-\u9fff]{2,}", raw.get("content", "")) if len(x) < 20][:20]
            return {"external_id": stable, "name": name, "headline": raw.get("headline", ""), "location": raw.get("location", ""), "experience_years": float(years.group(1)) if years else None, "education": raw.get("education", ""), "skills": list(dict.fromkeys(skills)), "summary": " ".join(x for x in (raw.get("company"), raw.get("headline"), raw.get("content")) if x)[:3000], "detail_url": raw.get("detail_url", "")}
        except Exception as exc:
            self.log(f"候选人卡片解析失败：{exc}", "warning"); return None

    async def _next(self, page) -> bool:
        for selector in ('li.ant-pagination-next', 'button.ant-pagination-next', '[class*="pagination"] [class*="next"]', 'a:has-text("下一页")'):
            button = page.locator(selector).first
            try:
                if await button.count() and await button.is_visible():
                    if await button.get_attribute('disabled') or 'disabled' in (await button.get_attribute('class') or ''): return False
                    await button.click(); return True
            except Exception: continue
        return False


def run_scraper_sync(*args, **kwargs):
    return asyncio.run(PlaywrightLiepinScraper(*args, **kwargs).run(kwargs.pop('keyword'), kwargs.pop('city'), kwargs.pop('max_pages')))
