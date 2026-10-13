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
    get_scrape_task,
    get_db,
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
            # scrapling 为可选会话适配器；旧 cdp 仅监听手动搜索。
            "launch_mode": config.SCRAPE_LAUNCH_MODE,
            "scrapling_engine": config.SCRAPLING_ENGINE,
            "cdp_port": config.SCRAPE_CDP_PORT,
            "headless": config.SCRAPE_HEADLESS,
            "slow_mo": 800,
            "timeout": config.SCRAPE_TIMEOUT_MS,
            "search_mode": search_mode,
            "user_data_dir": str(profile_dir),
            "stability_seconds": 60 if config.SCRAPE_LAUNCH_MODE == "scrapling" else 0,
        },
        "search": {
            "keyword": keyword,
            "city": city,
            "max_pages": max_pages,
        },
    }


def run_scrape_task(task_id: int, scrape_config: dict, user_id: int | None = None,
                    job_id: int | None = None) -> None:
    """逐条写库；失败和人工处理保留结果，不伪装成完成。"""
    from ..scrape_state import ScrapeNeedsAttention
    task = get_scrape_task(task_id)
    resumes = json.loads(task.get("resumes_json") or "[]") if task else []
    seen = get_existing_resume_ids(user_id, job_id) if user_id and job_id else set()
    seen.update(extract_resume_id(r) for r in resumes)

    def persist(resume):
        key = extract_resume_id(resume)
        if key in seen:
            return
        pending = resumes + [resume]
        update_scrape_task(task_id, result_count=len(pending),
                           resumes_json=json.dumps(pending, ensure_ascii=False))
        resumes.append(resume)
        seen.add(key)

    def progress(note):
        update_scrape_task(task_id, error_msg=note)

    try:
        update_scrape_task(task_id, status="running", finished_at=None, error_msg=None)
        if scrape_config.get("platform", "liepin") != "liepin":
            raise ValueError("不支持的平台")
        from ..scraper_liepin import LiepinHunterScraper
        runtime = dict(scrape_config, on_resume=persist, on_progress=progress, seen_ids=list(seen))
        target = runtime.get("scraper", {}).get("max_results")
        if target is not None:
            runtime["scraper"] = dict(runtime["scraper"], max_results=max(0, target - len(resumes)))
        scraper = LiepinHunterScraper(runtime)
        for resume in asyncio.run(scraper.run()):
            persist(resume)
        note = None
        if scrape_config.get("auto_score") and resumes:
            # 单独记录下游失败，不能把已完成的采集重新标为失败或自动重采。
            try:
                from ..models import get_job, get_unscored_resumes
                from .scoring import start_score_task
                job = get_job(job_id)
                pending, _ = get_unscored_resumes(user_id, job_id)
                if pending and job and job["user_id"] == user_id:
                    score_id, _ = start_score_task(user_id, job_id, job["name"], job["job_description"], pending)
                    update_scrape_task(task_id, score_task_id=score_id)
            except Exception:
                note = "采集已完成，但自动分析启动失败；可前往 AI 评分重试。"
        update_scrape_task(task_id, status="done", result_count=len(resumes),
                           error_msg=note, finished_at=datetime.now().isoformat())
    except ScrapeNeedsAttention as exc:
        update_scrape_task(task_id, status="attention", error_msg=str(exc),
                           finished_at=datetime.now().isoformat())
    except Exception as exc:
        print("[采集任务失败] %s: %s" % (task_id, type(exc).__name__))
        update_scrape_task(task_id, status="error",
                           error_msg="采集执行失败，已保存结果保留。请检查浏览器安装、连接及页面状态后重新继续。",
                           finished_at=datetime.now().isoformat())


def start_scrape_task(task_id: int, scrape_config: dict, user_id: int | None = None,
                      job_id: int | None = None) -> threading.Thread:
    # 配置快照仅包含搜索条件，不持久化模型密钥。恢复时使用当前服务设置。
    snapshot = {key: scrape_config[key] for key in ("platform", "scraper", "search", "quick_filter", "auto_score") if key in scrape_config}
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT status, user_id FROM scrape_tasks WHERE id=?", (task_id,)).fetchone()
        if not task or task["user_id"] != user_id:
            raise ValueError("任务不存在")
        if task["status"] not in ("pending", "attention", "error"):
            raise ValueError("任务已启动或已完成")
        other = conn.execute("SELECT id FROM scrape_tasks WHERE user_id=? AND status IN ('queued','running') AND id<>?", (user_id, task_id)).fetchone()
        if other:
            raise ValueError("该账号已有采集任务运行，请等待完成后再开始")
        conn.execute("UPDATE scrape_tasks SET status='queued', run_config_json=?, error_msg=NULL, finished_at=NULL WHERE id=?",
                     (json.dumps(snapshot, ensure_ascii=False), task_id))
        conn.commit()
    finally:
        conn.close()
    thread = threading.Thread(target=run_scrape_task,
        args=(task_id, scrape_config, user_id, job_id), daemon=True,
        name="minglie-scrape-%s" % task_id)
    _scrape_threads[task_id] = thread
    try:
        thread.start()
    except Exception:
        update_scrape_task(task_id, status="error", error_msg="无法启动采集任务，请重试")
        raise
    return thread


def recover_interrupted_tasks():
    """仅供单进程服务启动时调用；重启后等待人工恢复，不自动重发平台请求。"""
    conn = get_db()
    try:
        conn.execute("UPDATE scrape_tasks SET status='attention', error_msg=? WHERE status IN ('running','queued')",
                     ("服务曾中断，已保存结果保留。请确认浏览器后重新继续。",))
        conn.commit()
    finally:
        conn.close()


def running_task_ids() -> list[int]:
    return [tid for tid, thread in _scrape_threads.items() if thread.is_alive()]
