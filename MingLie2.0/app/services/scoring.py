from __future__ import annotations

from app.repositories import Store


def _contains(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def score_candidate(candidate: dict, requirements: dict) -> dict:
    profile = " ".join([candidate.get("headline", ""), candidate.get("summary", ""), " ".join(candidate.get("skills", []))])
    required = requirements.get("required_skills", [])
    matched = [skill for skill in required if _contains(profile, skill)]
    gaps = [skill for skill in required if skill not in matched]
    evidence = [{"requirement": skill, "candidate_text": "技能/摘要中命中"} for skill in matched]
    score = 35
    score += round(40 * len(matched) / max(len(required), 1)) if required else 20
    exp_min = requirements.get("minimum_experience_years", 0)
    exp = candidate.get("experience_years") or 0
    if exp_min:
        if exp >= exp_min:
            score += 15; matched.append(f"{exp}年经验（要求{exp_min}年）"); evidence.append({"requirement": "工作年限", "candidate_text": f"{exp} 年"})
        else: gaps.append(f"经验 {exp} 年，低于要求 {exp_min} 年")
    locations = requirements.get("locations", [])
    if locations:
        if any(location in candidate.get("location", "") for location in locations):
            score += 10; matched.append("地点匹配")
        else: gaps.append("地点未明确匹配")
    score = max(0, min(100, score))
    recommendation = "contact" if score >= 75 else "review" if score >= 55 else "hold"
    return {"total_score": score, "recommendation": recommendation, "matched": matched, "gaps": gaps, "evidence": evidence}


def score_job(store: Store, job_id: int) -> dict:
    requirements_row = store.get_requirements(job_id)
    if not requirements_row:
        raise ValueError("analyze the JD before scoring candidates")
    candidates = store.list_candidates(job_id)
    run_id = store.create_score_run(job_id, "rules", len(candidates))
    try:
        for candidate in candidates:
            store.save_score(run_id, candidate["id"], score_candidate(candidate, requirements_row["requirements"]), "rules")
        store.finish_score_run(run_id)
    except Exception:
        store.finish_score_run(run_id, "failed")
        raise
    return store.get_score_run(run_id)
