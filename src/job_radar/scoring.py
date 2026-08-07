from __future__ import annotations

from typing import Any, Dict, Tuple

from .models import JobPosting


def _graduation_year(profile: Dict[str, Any]) -> int:
    return int(str(profile["graduation"]).split("-", 1)[0])


def evaluate_eligibility(job: JobPosting, profile: Dict[str, Any]) -> str:
    target_year = _graduation_year(profile)
    if not job.graduation_years:
        return "待核对"
    if target_year in job.graduation_years:
        return "符合"
    if any(
        year in profile.get("review_graduation_years", [])
        for year in job.graduation_years
    ):
        return "需核对"
    return "可能不符"


def score_job(job: JobPosting, profile: Dict[str, Any]) -> Tuple[int, list]:
    score = 0
    reasons = []
    searchable = "{} {}".format(job.title, job.description).lower()

    types = profile.get("company_type_priority", [])
    if job.company_type in types:
        points = max(4, 20 - types.index(job.company_type) * 5)
        score += points
        reasons.append("企业类型 {} +{}".format(job.company_type, points))

    for keyword, raw_points in profile.get("positive_keywords", {}).items():
        if keyword.lower() in searchable:
            points = int(raw_points)
            score += points
            reasons.append("{} +{}".format(keyword, points))

    for keyword, raw_points in profile.get("negative_keywords", {}).items():
        if keyword.lower() in searchable:
            points = int(raw_points)
            score += points
            reasons.append("{} {}".format(keyword, points))

    eligibility = evaluate_eligibility(job, profile)
    job.eligibility = eligibility
    if eligibility == "符合":
        score += 15
        reasons.append("毕业时间符合 +15")
    elif eligibility == "需核对":
        score += 5
        reasons.append("届别可能覆盖海外毕业时间 +5")
    elif eligibility == "可能不符":
        score -= 25
        reasons.append("毕业时间可能不符 -25")

    job.score = score
    job.score_reasons = reasons
    return score, reasons
