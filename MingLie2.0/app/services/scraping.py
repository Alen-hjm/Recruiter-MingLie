from __future__ import annotations

import asyncio
import threading

from app.services.ingestion import ingest_candidates
from app.services.playwright_scraper import PlaywrightLiepinScraper


class ScrapeService:
    def __init__(self, store, profile_dir, channel, max_pages):
        self.store, self.profile_dir, self.channel, self.max_pages = store, profile_dir, channel, max_pages

    def start(self, job_id: int, criteria: dict, workflow_id: int | None = None, background: bool = True) -> int:
        keywords = criteria.get("keywords") or [criteria.get("title") or ""]
        cities = criteria.get("locations") or [""]
        run_id = self.store.create_scrape_run(job_id, workflow_id, " ".join(keywords), cities[0], self.max_pages, str(self.profile_dir))
        if background: threading.Thread(target=self.execute, args=(run_id, criteria), daemon=True).start()
        else: self.execute(run_id, criteria)
        return run_id

    def execute(self, run_id: int, criteria: dict) -> None:
        run = self.store.get_scrape_run(run_id)
        self.store.update_scrape_run(run_id, "running")
        def log(message: str, level: str = "info"):
            self.store.add_scrape_log(run_id, message, level)
            if level == "waiting_login": self.store.update_scrape_run(run_id, "waiting_login")
        try:
            scraper = PlaywrightLiepinScraper(run["browser_profile"], self.channel, log)
            candidates = asyncio.run(scraper.run(run["keyword"], run["city"], run["max_pages"]))
            if candidates:
                outcome = ingest_candidates(self.store, run["job_id"], "liepin-playwright", candidates, f"scrape-run-{run_id}")
                log(f"已去重入库：新增 {outcome['ingestion']['created_count']}，更新 {outcome['ingestion']['updated_count']}。")
            self.store.update_scrape_run(run_id, "completed", len(candidates))
        except Exception as exc:
            log(f"采集失败：{exc}", "error"); self.store.update_scrape_run(run_id, "failed", error_message=str(exc))
