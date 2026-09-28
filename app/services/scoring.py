# -*- coding: utf-8 -*-
"""
简历评分后台任务。

保留能力：知识库上下文增强、知识引擎上下文、历史评分合并、只评新简历、
并发评分、低于阈值的简历自动清理、评分后自动录入候选人管道并同步 Obsidian。

改进点：
  1. 不再从 server 导入 —— 解开了 agent_tools ↔ server 的循环依赖。
  2. LLM 配置统一走 config.resolve_llm_config（环境变量可作兜底）。
  3. 并发度、低分阈值提为模块常量，方便按机器性能调整。
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from ..models import (
    create_candidate_from_score,
    create_score_task,
    delete_low_score_resumes,
    extract_resume_id,
    get_candidates_by_stage,
    get_db,
    get_job,
    get_pipeline_summary,
    get_settings,
    update_score_task,
)

# 评分并发度。太低拖慢批量，太高会撞到 LLM 侧限流。
SCORE_MAX_WORKERS = 5

# 低于这个分数的简历在评分后会被自动清理
LOW_SCORE_THRESHOLD = 60

# 达到这个分数的候选人自动进入管道
PIPELINE_MIN_SCORE = 60


def build_score_config(user_settings: dict, job_name: str, job_description: str,
                       knowledge_context: str = "") -> dict:
    """组装评分器配置"""
    return {
        "llm": _resolve_llm(user_settings),
        "job_description": job_description,
        "job_name": job_name,
        "knowledge_context": knowledge_context,
    }


def _resolve_llm(user_settings: dict) -> dict:
    from .. import config
    return config.resolve_llm_config(user_settings)


# ------------------------------------------------------------------ 上下文加载
def _load_knowledge_context(user_id: int, job_name: str, job_description: str) -> str:
    """Obsidian 知识库 + 知识引擎，两路上下文合并"""
    chunks: list[str] = []

    try:
        settings = get_settings(user_id)
        kb_path = (settings or {}).get("obsidian_kb_path", "")
        if kb_path and Path(kb_path).exists():
            from ..knowledge import build_knowledge_context, find_relevant_notes, scan_knowledge_base
            notes = scan_knowledge_base(kb_path)
            relevant = find_relevant_notes(notes, job_name, job_description)
            ctx = build_knowledge_context(relevant)
            if ctx:
                chunks.append(ctx)
                print("[知识库] 加载 %d 篇相关笔记作为评分参考" % len(relevant))
    except Exception as e:
        print("[知识库] 加载失败: %s" % e)

    try:
        from ..knowledge_engine import build_scoring_context
        ctx = build_scoring_context(job_name)
        if ctx:
            chunks.append(ctx)
            print("[知识引擎] 加载了 %s 的评分知识上下文" % job_name)
    except Exception as e:
        print("[知识引擎] 加载失败: %s" % e)

    return "\n\n".join(chunks)


def _load_existing_scores(user_id: int, job_id: int) -> list[dict]:
    """读该岗位最近一次完成的评分结果，用于增量合并"""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT scored_json FROM score_tasks
               WHERE user_id = ? AND status = 'done' AND scored_json IS NOT NULL
               AND job_name = (SELECT name FROM jobs WHERE id = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, job_id),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["scored_json"]:
        return []
    try:
        return json.loads(row["scored_json"])
    except json.JSONDecodeError:
        return []


# ------------------------------------------------------------------ 单份评分
def _score_one(analyzer, resume: dict, job_description: str, mode: str) -> dict:
    """评分单份简历（供线程池调用）；异常时把错误写进简历而不抛出"""
    try:
        if mode == "100":
            resume["score100"] = analyzer.score_resume_100(resume, job_description)
        else:
            resume["analysis"] = analyzer.analyze_one(resume)
    except Exception as e:
        print("[评分错误] 简历 %s: %s" % (resume.get("name", "?"), e))
        resume["_error"] = str(e)
    return resume


# ------------------------------------------------------------------ 任务主体
def run_score_task(task_id: int, score_config: dict, resumes: list,
                   job_description: str, mode: str,
                   user_id: int | None = None, job_id: int | None = None) -> None:
    """评分任务主体（在线程中运行）"""
    try:
        update_score_task(task_id, status="running")

        job_name = score_config.get("job_name", "")
        if user_id:
            score_config["knowledge_context"] = _load_knowledge_context(
                user_id, job_name, job_description
            )

        from ..analyzer import ResumeAnalyzer
        analyzer = ResumeAnalyzer(score_config)

        # ── 合并历史评分，只评新简历 ──
        existing_scored: list[dict] = []
        if user_id and job_id:
            existing_scored = _load_existing_scores(user_id, job_id)
            if existing_scored:
                print("[评分] 加载已有评分 %d 份" % len(existing_scored))

        existing_ids = {extract_resume_id(r) for r in existing_scored}
        new_resumes = [r for r in resumes if extract_resume_id(r) not in existing_ids]

        if not new_resumes:
            print("[评分] 没有新简历需要评分")
            update_score_task(
                task_id, status="done",
                scored_json=json.dumps(existing_scored, ensure_ascii=False),
                passed_count=sum(1 for r in existing_scored if (r.get("score100") or {}).get("pass", False)),
                total_count=len(existing_scored),
                finished_at=datetime.now().isoformat(),
                error_msg="全部已评分，无新简历",
            )
            return

        print("[评分] 新简历 %d 份，已有评分 %d 份" % (len(new_resumes), len(existing_scored)))

        # ── 并发评分 ──
        scored_new: list = [None] * len(new_resumes)
        with ThreadPoolExecutor(max_workers=min(SCORE_MAX_WORKERS, len(new_resumes))) as executor:
            future_to_idx = {
                executor.submit(_score_one, analyzer, resume, job_description, mode): idx
                for idx, resume in enumerate(new_resumes)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    scored_new[idx] = future.result()
                except Exception as e:
                    print("[评分异常] 索引 %s: %s" % (idx, e))
                    new_resumes[idx]["_error"] = str(e)
                    scored_new[idx] = new_resumes[idx]

        all_scored = existing_scored + [r for r in scored_new if r is not None]
        passed = sum(1 for r in all_scored if (r.get("score100") or {}).get("pass", False)) if mode == "100" else 0

        # ── 低分清理 ──
        deleted_count = 0
        if user_id and job_id:
            try:
                deleted_count = delete_low_score_resumes(user_id, job_id, threshold=LOW_SCORE_THRESHOLD)
                if deleted_count:
                    print("[自动清理] 已删除 %d 份低于 %d 分的简历" % (deleted_count, LOW_SCORE_THRESHOLD))
            except Exception as e:
                print("[自动清理失败] %s" % e)

        notes = []
        if deleted_count:
            notes.append("已自动删除 %d 份低分简历" % deleted_count)
        if existing_scored:
            notes.append("（合并已有评分 %d 份）" % len(existing_scored))

        update_score_task(
            task_id,
            status="done",
            scored_json=json.dumps(all_scored, ensure_ascii=False),
            passed_count=passed,
            total_count=len(all_scored),
            finished_at=datetime.now().isoformat(),
            error_msg=" ".join(notes) or None,
        )

        # ── 录入管道 + 同步 Obsidian ──
        if user_id and job_id:
            _ingest_pipeline(user_id, job_id, all_scored)

    except Exception as e:
        print("[评分任务异常] 任务 %s: %s" % (task_id, e))
        update_score_task(
            task_id, status="error", error_msg=str(e),
            finished_at=datetime.now().isoformat(),
        )


def _ingest_pipeline(user_id: int, job_id: int, all_scored: list) -> None:
    """评分完成后把达标候选人录入管道，并同步到 Obsidian"""
    try:
        count = 0
        for r in all_scored:
            s = r.get("score100")
            score = (s.get("total_score", 0) if isinstance(s, dict) else s) if s else 0
            if score >= PIPELINE_MIN_SCORE:
                create_candidate_from_score(user_id, job_id, r)
                count += 1
        print("[管道] 已录入 %d 位候选人到管道" % count)

        settings = get_settings(user_id)
        job_folder = (settings or {}).get("obsidian_job_path", "")
        if not job_folder:
            return
        from ..knowledge import sync_pipeline_to_obsidian
        job = get_job(job_id)
        if not job:
            return
        stages = get_candidates_by_stage(user_id, job_id)
        summary = get_pipeline_summary(user_id, job_id)
        sync_pipeline_to_obsidian(job_folder, job["name"], stages, summary)
        print("[管道] 已同步到 Obsidian")
    except Exception as e:
        print("[管道] 录入失败: %s" % e)


def start_score_task(user_id: int, job_id: int, job_name: str, job_description: str,
                     resumes: list, mode: str = "100") -> tuple[int, threading.Thread]:
    """
    创建评分任务记录并在后台线程启动。

    返回 (score_task_id, thread)。
    """
    settings = get_settings(user_id)
    score_config = build_score_config(settings, job_name, job_description)
    task_id = create_score_task(user_id, None, job_name, job_description, mode)

    thread = threading.Thread(
        target=run_score_task,
        args=(task_id, score_config, resumes, job_description, mode, user_id, job_id),
        daemon=True,
        name="minglie-score-%s" % task_id,
    )
    thread.start()
    return task_id, thread
