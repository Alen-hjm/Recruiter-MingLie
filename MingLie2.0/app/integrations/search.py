from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class SearchResult:
    status: str
    candidates: list[dict]
    message: str


class ManualSearchProvider:
    name = "manual"
    def search(self, criteria: dict) -> SearchResult:
        return SearchResult("waiting_input", [], "等待浏览器扩展或已授权的外部采集器按此条件提交候选人")


class DemoSearchProvider:
    name = "demo"
    def search(self, criteria: dict) -> SearchResult:
        keyword = (criteria.get("keywords") or ["技术"])[0]
        city = (criteria.get("locations") or ["上海"])[0]
        candidates = []
        for index, (name, years, skills) in enumerate((("陈晨", 6, [keyword, "Python", "SQL"]), ("林悦", 4, [keyword, "React", "Docker"]), ("周楠", 8, [keyword, "Python", "Kubernetes"]))):
            external = hashlib.sha256(f"demo:{keyword}:{index}".encode()).hexdigest()[:16]
            candidates.append({"external_id": external, "name": name, "headline": f"{keyword} 候选人（演示数据）", "location": city, "experience_years": years, "education": "本科", "skills": skills, "summary": f"这是本地演示候选人，用于验证 {keyword} 岗位的端到端工作流。"})
        return SearchResult("completed", candidates, "已生成明确标记为演示数据的候选人；请切换为浏览器扩展或授权连接器接入真实候选人")


def build_search_provider(name: str):
    return DemoSearchProvider() if name == "demo" else ManualSearchProvider()
