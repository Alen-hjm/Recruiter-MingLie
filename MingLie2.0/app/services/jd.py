from __future__ import annotations

import re

KNOWN_SKILLS = ("python", "java", "golang", "go", "javascript", "typescript", "react", "vue", "flask", "django", "sql", "mysql", "postgresql", "redis", "kafka", "docker", "kubernetes", "aws", "机器学习", "人工智能", "大模型", "算法", "招聘", "猎头", "销售")
CITIES = ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "苏州", "远程")


def analyze_jd(job: dict) -> dict:
    text = job.get("jd", "")
    lower = text.lower()
    skills = [skill for skill in KNOWN_SKILLS if skill in lower]
    cities = [city for city in CITIES if city in text]
    experience = re.search(r"(?:[≥>=]|至少|不少于)?\s*(\d{1,2})\s*年(?:工作)?经验", text)
    education = next((item for item in ("博士", "硕士", "本科", "大专") if item in text), "")
    return {
        "title": job["name"],
        "keywords": skills or [word for word in re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", text)[:8]],
        "required_skills": skills,
        "preferred_skills": [],
        "locations": cities,
        "minimum_experience_years": int(experience.group(1)) if experience else 0,
        "minimum_education": education,
        "hard_conditions": [x for x in ([f"{experience.group(1)}年以上经验" if experience else ""] + skills[:4]) if x],
    }
