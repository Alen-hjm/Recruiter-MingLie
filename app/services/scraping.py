# -*- coding: utf-8 -*-
"""
猎聘抓取后台任务。

保留能力：CDP 监听 / stealth / 基础模式三种启动方式、翻页、详情抓取、
快速筛选、结果去重 —— 全部与重构前一致。

改进点：
  1. 依赖方向反转：不再需要 import server。
  2. 路径集中：browser_data / 截图目录从 app.config 取，不再写死 "./browser_data/user_N"。
  3. 任务状态收敛：所有分支都通过统一的收尾函数写库，避免「异常路径漏写
     finished_at」这类只在排查时才发现的问题。
"""

import asyncio
import json
import threading
from datetime import datetime

from .. import config
from ..models import (
    extract_resume_id,
    get_existing_resume_ids,
    update_scrape_task,
)

# 任务句柄登记表：task_id → Thread
# 注意：进程内内存，多 worker 部署时各 worker 之间不共享。
# 重构前也是同样的问题（原 _scrape_threads），这里保持行为一致并显式标注。
_scrape_threads: dict[int, threading.Thread] = {}


def build_scrape_config(user_settings: dict, keyword: str, city: str,
                        max_pages: int, search_mode: str = "auto",
                        user_id: int | None = None) -> dict:
    """组装抓取器配置（单一入口，避免各路由各自拼装）"""
    llm = config.resolve_llm_config(user_settings)
    profile_dir = config.BROWSER_DATA_DIR / ("user_%s" % (user_id if user_id is not None else "default"))
    return {
        "platform": "liepin",
        "llm": llm,
        "scraper": {
            # cdp=连接已启动的 Chrome（最难被判定为脚本）；stealth=反检测启动
            "launch_mode": "cdp",
            "cdp_port": config.SCRAPE_CDP_PORT,
            "headless": config.SCRAPE_HEADLESS,
            "slow_mo": 800,
            "timeout": config.SCRAPE_TIMEOUT_MS,
            "search_mode": search_mode,
            "user_data_dir": str(profile_dir),
        },
        "search": {
            "keyword": keyword,
            "city": city,
            "max_pages": max_pages,
        },
    }


def _save_partial(task_id: int, resumes: list, note: str) -> None:
    """中断/异常时保存已抓到的部分数据（而不是整批丢掉）"""
    update_scrape_task(
        task_id,
        status="done",
        result_count=len(resumes),
        resumes_json=json.dumps(resumes, ensure_ascii=False),
        error_msg=note,
        finished_at=datetime.now().isoformat(),
    )


def run_scrape_task(task_id: int, scrape_config: dict, user_id: int | None = None,
                    job_id: int | None = None) -> None:
    """抓取任务主体（在线程中运行）"""
    resumes: list = []
    try:
        update_scrape_task(task_id, status="running")

        platform = scrape_config.get("platform", "liepin")
        if platform != "liepin":
            raise ValueError("不支持的平台: %s" % platform)

        from ..scraper_liepin import LiepinHunterScraper
        scraper = LiepinHunterScraper(scrape_config)

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            resumes = loop.run_until_complete(scraper.run())
        finally:
            # 原代码在异常路径上漏掉了 close，这里放到 finally 里
            try:
                loop.close()
            except Exception:
                pass

        # ── 去重：过滤掉该岗位下已存在的简历 ──
        dup_count = 0
        if user_id and job_id and resumes:
            existing_ids = get_existing_resume_ids(user_id, job_id)
            fresh = []
            for r in resumes:
                rid = extract_resume_id(r)
                if rid in existing_ids:
                    dup_count += 1
                else:
                    existing_ids.add(rid)
                    fresh.append(r)
            if dup_count:
                print("[去重] 过滤 %d 份重复简历，保留 %d 份新简历" % (dup_count, len(fresh)))
            resumes = fresh

        update_scrape_task(
            task_id,
            status="done",
            result_count=len(resumes),
            resumes_json=json.dumps(resumes, ensure_ascii=False),
            error_msg=("已过滤 %d 份重复简历" % dup_count) if dup_count else None,
            finished_at=datetime.now().isoformat(),
        )

    except KeyboardInterrupt:
        if resumes:
            _save_partial(task_id, resumes, "抓取被中断，已保存部分数据")
        else:
            update_scrape_task(
                task_id, status="error", error_msg="抓取被中断",
                finished_at=datetime.now().isoformat(),
            )
    except Exception as e:
        print("[抓取错误] 任务 %s: %s" % (task_id, e))
        if resumes:
            _save_partial(task_id, resumes, "抓取出错(%s)，已保存部分数据" % e)
        else:
            update_scrape_task(
                task_id, status="error", error_msg=str(e),
                finished_at=datetime.now().isoformat(),
            )


def start_scrape_task(task_id: int, scrape_config: dict, user_id: int | None = None,
                      job_id: int | None = None) -> threading.Thread:
    """在后台线程启动抓取，返回线程对象"""
    thread = threading.Thread(
        target=run_scrape_task,
        args=(task_id, scrape_config, user_id, job_id),
        daemon=True,
        name="minglie-scrape-%s" % task_id,
    )
    thread.start()
    _scrape_threads[task_id] = thread
    return thread


def running_task_ids() -> list[int]:
    """当前仍在运行的抓取任务（调试/状态页用）"""
    return [tid for tid, th in _scrape_threads.items() if th.is_alive()]
