"""Manual-login acceptance run: one or two pages, one detail attempt per page."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", help="本次验证的岗位关键词；仅检查入口时可省略")
    parser.add_argument("--data-dir", type=Path, required=True, help="独立验证数据目录")
    parser.add_argument("--engine", choices=("dynamic", "stealth"), default="dynamic")
    parser.add_argument("--entry-only", action="store_true", help="仅验证入口保持 60 秒，不搜索或读取简历")
    parser.add_argument("--pages", type=int, choices=(1, 2), default=1)
    parser.add_argument("--resume-saved", action="store_true", help="跳过该验证目录先前保存的简历")
    args = parser.parse_args()
    if not args.entry_only and not args.keyword:
        parser.error("完整验证需要 --keyword")
    import os
    os.environ["MINGLIE_DATA_DIR"] = str(args.data_dir.resolve())
    os.environ["MINGLIE_SCREENSHOT_DIR"] = str(args.data_dir.resolve() / "screenshots")
    from app.scraper_liepin import LiepinHunterScraper
    from app.scrape_state import ScrapeNeedsAttention

    if args.entry_only:
        return asyncio.run(check_entry(args))

    import json
    from app.models import extract_resume_id
    seen = set()
    if args.resume_saved:
        for path in args.data_dir.glob("liepin_resumes_*.json"):
            seen.update(extract_resume_id(record) for record in json.loads(path.read_text(encoding="utf-8")))
    print(f"本次最多 {args.pages} 页，每页最多尝试 1 份详情；已载入 {len(seen)} 个去重标识。", flush=True)

    scraper = LiepinHunterScraper({
        "seen_ids": list(seen),
        "search": {"keyword": args.keyword, "max_pages": args.pages},
        "scraper": {"launch_mode": "scrapling", "headless": False,
                    "scrapling_engine": args.engine,
                    "user_data_dir": str(args.data_dir.resolve() / "browser"),
                    "search_mode": "auto", "timeout": 30000,
                    "manual_login_handoff": True, "login_wait_seconds": 300,
                    "stability_seconds": 60, "max_results": args.pages, "require_detail": True,
                    "required_pages": args.pages, "detail_attempts_per_page": 1},
        "on_progress": lambda note: print(note, flush=True),
    })
    try:
        results = asyncio.run(scraper.run())
    except ScrapeNeedsAttention as exc:
        print(f"待人工处理：{exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"验证未通过：{type(exc).__name__}；检查依赖和本地导航记录。", flush=True)
        return 1
    print(f"验证通过：处理 {scraper.pages_processed} 页，完成 {scraper.details_completed} 份详情读取与返回，跳过 {scraper.duplicates_skipped} 份重复记录，保存 {len(results)} 份新简历；未调用模型或发送消息。", flush=True)
    return 0


async def check_entry(args):
    from app.scrapling_session import run_session
    from app.scrape_diagnostics import NavigationLog, page_kind
    from app.scrape_state import ScrapeNeedsAttention, page_problem
    from urllib.parse import urlsplit
    log = NavigationLog(args.data_dir.resolve() / "diagnostics")
    log.stage("entry_probe", engine=args.engine)

    async def setup(page):
        log.attach(page)

    async def observe(page):
        deadline = asyncio.get_running_loop().time() + 60
        print("入口验证开始：保持 60 秒，本次不需要输入账号。", flush=True)
        while True:
            text = await page.locator("body").inner_text(timeout=5000)
            problem = page_problem(page.url, text)
            if problem and "登录已失效" not in problem:
                raise ScrapeNeedsAttention(problem)
            if urlsplit(page.url).hostname != "h.liepin.com":
                raise ScrapeNeedsAttention("页面离开预期猎聘入口，已停止验证。")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                log.stage("entry_stable", page=page_kind(page.url))
                return
            await asyncio.sleep(min(2, remaining))

    status = 1
    try:
        await run_session("https://h.liepin.com", {
            "user_data_dir": str(args.data_dir.resolve() / "browser"),
            "scrapling_engine": args.engine, "headless": False,
        }, observe, setup)
        print("入口稳定性通过：60 秒未见空白；尚未验证登录、搜索、详情。", flush=True)
        status = 0
    except ScrapeNeedsAttention as exc:
        log.stage("attention", error_type=type(exc).__name__)
        print(f"待人工处理：{exc}", flush=True)
        status = 2
    except Exception as exc:
        log.stage("error", error_type=type(exc).__name__)
        print(f"入口验证失败：{type(exc).__name__}", flush=True)
    finally:
        log.save()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
