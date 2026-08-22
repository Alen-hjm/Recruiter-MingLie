"""
自适应搜索逻辑
评分结果不理想时，自动调整搜索策略重试
"""
import asyncio
import json
from typing import Optional
from rich.console import Console

console = Console()

# 关键词扩展映射
KEYWORD_EXPANSIONS = {
    "Python开发": ["后端开发", "Python工程师", "服务端开发", "全栈开发"],
    "Python后端": ["后端开发", "Python开发", "服务端开发", "全栈开发"],
    "前端开发": ["Web前端", "前端工程师", "React开发", "Vue开发"],
    "Java开发": ["Java工程师", "后端开发", "服务端开发"],
    "全栈开发": ["Python开发", "前端开发", "后端开发"],
    "数据分析": ["数据分析师", "BI分析师", "数据工程师"],
    "AI工程师": ["机器学习", "深度学习", "算法工程师"],
}


def get_expanded_keywords(original_keyword: str) -> list[str]:
    """获取扩展关键词列表"""
    expansions = KEYWORD_EXPANSIONS.get(original_keyword, [])
    # 去重，保留原始关键词
    all_keywords = [original_keyword]
    for kw in expansions:
        if kw not in all_keywords:
            all_keywords.append(kw)
    return all_keywords


def evaluate_score_quality(scored_resumes: list[dict], min_pass_rate: float = 0.15, min_pass_count: int = 2) -> dict:
    """
    评估评分结果质量
    返回: {"quality": "good"|"poor"|"empty", "pass_rate": float, "pass_count": int, "total": int, "suggestion": str}
    """
    if not scored_resumes:
        return {"quality": "empty", "pass_rate": 0, "pass_count": 0, "total": 0,
                "suggestion": "没有抓取到简历，建议换关键词或扩大搜索范围"}

    valid = [r for r in scored_resumes if "_error" not in r]
    if not valid:
        return {"quality": "empty", "pass_rate": 0, "pass_count": 0, "total": len(scored_resumes),
                "suggestion": "所有简历评分失败，请检查 LLM 配置"}

    passed = sum(1 for r in valid if r.get("score100", {}).get("pass", False))
    total = len(valid)
    pass_rate = passed / total if total > 0 else 0

    if pass_rate >= min_pass_rate and passed >= min_pass_count:
        return {"quality": "good", "pass_rate": pass_rate, "pass_count": passed, "total": total,
                "suggestion": f"结果良好，{passed}/{total} 通过"}
    elif pass_rate > 0:
        return {"quality": "poor", "pass_rate": pass_rate, "pass_count": passed, "total": total,
                "suggestion": f"通过率偏低（{passed}/{total}），建议换关键词重试"}
    else:
        return {"quality": "poor", "pass_rate": 0, "pass_count": 0, "total": total,
                "suggestion": f"0/{total} 通过，建议换关键词或放宽岗位要求"}


async def adaptive_search(
    config: dict,
    scrape_fn,
    score_fn,
    keyword: str,
    job_description: str,
    city: str = "上海",
    max_pages: int = 3,
    platform: str = "liepin",
    mode: str = "100",
    max_retries: int = 2,
    min_pass_rate: float = 0.15,
    min_pass_count: int = 2,
    callback=None,
) -> dict:
    """
    自适应搜索：抓取 → 评分 → 检查质量 → 不达标则换关键词重试

    Args:
        config: 配置
        scrape_fn: 抓取函数 async (config, keyword, city, max_pages, platform) -> list[dict]
        score_fn: 评分函数 (config, resumes, job_description, mode) -> list[dict]
        keyword: 初始关键词
        job_description: 岗位描述
        city: 城市
        max_pages: 每轮抓取页数
        platform: 平台
        mode: 评分模式
        max_retries: 最大重试次数
        min_pass_rate: 最低通过率
        min_pass_count: 最低通过人数
        callback: 进度回调 fn(message: str)

    Returns:
        {
            "success": True/False,
            "keyword_used": str,
            "attempts": [{"keyword": str, "scraped": int, "passed": int, "quality": str}],
            "scored_resumes": list,
            "summary": str,
        }
    """
    keywords_to_try = get_expanded_keywords(keyword)[:max_retries + 1]
    attempts = []
    best_result = None
    best_quality = None

    for i, kw in enumerate(keywords_to_try):
        if callback:
            callback(f"第 {i+1} 轮搜索：关键词「{kw}」")

        # 1. 抓取
        try:
            resumes = await scrape_fn(config, kw, city, max_pages, platform)
        except Exception as e:
            attempts.append({"keyword": kw, "scraped": 0, "passed": 0, "quality": "error", "error": str(e)})
            if callback:
                callback(f"抓取失败: {e}")
            continue

        if not resumes:
            attempts.append({"keyword": kw, "scraped": 0, "passed": 0, "quality": "empty"})
            if callback:
                callback(f"关键词「{kw}」未抓取到简历")
            continue

        # 2. 评分
        try:
            scored = score_fn(config, resumes, job_description, mode)
        except Exception as e:
            attempts.append({"keyword": kw, "scraped": len(resumes), "passed": 0, "quality": "error", "error": str(e)})
            if callback:
                callback(f"评分失败: {e}")
            continue

        # 3. 评估质量
        quality = evaluate_score_quality(scored, min_pass_rate, min_pass_count)
        attempts.append({
            "keyword": kw,
            "scraped": len(resumes),
            "passed": quality["pass_count"],
            "quality": quality["quality"],
            "pass_rate": quality["pass_rate"],
        })

        if callback:
            callback(f"「{kw}」: 抓取 {len(resumes)} 份, 通过 {quality['pass_count']} 份 ({quality['quality']})")

        # 记录最佳结果
        if best_result is None or quality["pass_count"] > (best_quality["pass_count"] if best_quality else 0):
            best_result = scored
            best_quality = quality

        # 达标就停
        if quality["quality"] == "good":
            if callback:
                callback(f"搜索达标，使用关键词「{kw}」的结果")
            return {
                "success": True,
                "keyword_used": kw,
                "attempts": attempts,
                "scored_resumes": scored,
                "summary": f"使用「{kw}」搜索，{quality['pass_count']}/{quality['total']} 通过",
            }

    # 所有尝试结束，返回最佳结果
    if best_result:
        return {
            "success": True,
            "keyword_used": attempts[-1]["keyword"] if attempts else keyword,
            "attempts": attempts,
            "scored_resumes": best_result,
            "summary": f"经过 {len(attempts)} 轮搜索，最佳结果: {best_quality['pass_count']}/{best_quality['total']} 通过",
        }

    return {
        "success": False,
        "keyword_used": keyword,
        "attempts": attempts,
        "scored_resumes": [],
        "summary": f"经过 {len(attempts)} 轮搜索均未获得有效结果",
    }
