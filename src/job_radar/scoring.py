from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from .models import JobPosting


def _graduation_year(profile: Dict[str, Any]) -> int:
    return int(str(profile["graduation"]).split("-", 1)[0])


_WINDOW_MARKERS = {
    "2026秋招": (
        "2026秋招",
        "2026秋季招聘",
        "2026年秋季招聘",
        "2026秋季校园招聘",
        "2026年秋季校园招聘",
        "2026届秋招",
        "2026届秋季校园招聘",
        "2026下半年招聘",
        "2026年下半年招聘",
        "2026下半年校招",
        "2026年下半年校招",
        "2026下半年校园招聘",
        "2026年下半年校园招聘",
        "2026-2027校园招聘",
        "2026-2027年校园招聘",
        "2026-2027年度校园招聘",
        "2026-2027年秋季校园招聘",
        "2026-2027年度秋季校园招聘",
        # 招聘公告常以毕业届别命名；2027届秋招发生在2026年下半年。
        "2027届秋招",
        "2027届秋季校园招聘",
    ),
    "2027春招": (
        "2027春招",
        "2027届春招",
        "2027春季招聘",
        "2027年春季招聘",
        "2027春季校园招聘",
        "2027年春季校园招聘",
        "2027届春季校园招聘",
        "2027上半年招聘",
        "2027年上半年招聘",
        "2027上半年校招",
        "2027年上半年校招",
        "2027上半年校园招聘",
        "2027年上半年校园招聘",
    ),
    "2027校招": (
        "2027校招",
        "2027校园招聘",
        "2027年校园招聘",
        "2027届校招",
        "2027届校园招聘",
        "2027年度校招",
        "2027年度校园招聘",
        "2027届正式校招",
    ),
    "2026春招": (
        "2026春招",
        "2026届春招",
        "2026春季招聘",
        "2026年春季招聘",
        "2026春季校园招聘",
        "2026年春季校园招聘",
        "2026上半年招聘",
        "2026年上半年招聘",
    ),
    "2027秋招": (
        "2027秋招",
        "2027秋季招聘",
        "2027年秋季招聘",
        "2027秋季校园招聘",
        "2027年秋季校园招聘",
    ),
}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _explicit_recruitment_window(job: JobPosting) -> str:
    # Some official collectors put the cycle in source_name while the
    # individual position title is generic. Treat that official source label
    # as evidence, but still require an explicit campus/recruitment marker.
    searchable = _compact(
        "{} {} {} {}".format(
            job.title, job.description, job.education, job.source_name
        )
    )
    for window, markers in _WINDOW_MARKERS.items():
        if any(_compact(marker) in searchable for marker in markers):
            return window
    return ""


def _published_year_month(value: str) -> Tuple[int, int] | None:
    text = str(value or "")
    match = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})", text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return year, month


def recruitment_window(job: JobPosting) -> str:
    """Infer a conservative recruitment-season label from official evidence.

    Explicit season wording wins. A complete publication date is a weaker
    fallback because many official notices name only the graduation cohort.
    Unknown seasons deliberately remain unknown and therefore require review.
    """

    explicit = _explicit_recruitment_window(job)
    if explicit:
        return explicit
    published = _published_year_month(job.published_at)
    if published and published[0] == 2026 and 7 <= published[1] <= 12:
        return "2026秋招"
    if published and published[0] == 2027 and 1 <= published[1] <= 6:
        return "2027春招"
    return ""


def _accepted_recruitment_windows(profile: Dict[str, Any]) -> set[str]:
    accepted = profile.get("accepted_recruitment_windows", [])
    if not isinstance(accepted, (list, tuple, set)):
        return set()
    aliases = {
        "2026下半年招聘": "2026秋招",
        "2026年下半年招聘": "2026秋招",
        "2026下半年校招": "2026秋招",
        "2026下半年校园招聘": "2026秋招",
        "2027上半年招聘": "2027春招",
        "2027年上半年招聘": "2027春招",
        "2027上半年校招": "2027春招",
        "2027上半年校园招聘": "2027春招",
        "2027校招": "2027校招",
        "2027校园招聘": "2027校招",
        "2027年校园招聘": "2027校招",
        "2027届校招": "2027校招",
        "2027届校园招聘": "2027校招",
        "2027年度校招": "2027校招",
        "2027年度校园招聘": "2027校招",
        "2027届正式校招": "2027校招",
        "2027届": "2027届",
    }
    return {
        aliases.get(str(item), str(item))
        for item in accepted
        if str(item).strip()
    }


def evaluate_eligibility(job: JobPosting, profile: Dict[str, Any]) -> str:
    target_year = _graduation_year(profile)
    if not job.graduation_years:
        return "待核对"
    window = recruitment_window(job)
    accepted_windows = _accepted_recruitment_windows(profile)
    review_years = profile.get("review_graduation_years", [])
    if not isinstance(review_years, (list, tuple, set)):
        review_years = []
    year_matches = target_year in job.graduation_years or any(
        year in review_years for year in job.graduation_years
    )
    if window and window in accepted_windows and year_matches:
        return "符合"
    if target_year in job.graduation_years:
        # If an official notice explicitly names a different season, do not
        # silently treat the matching cohort year as eligible.
        if window and accepted_windows:
            return "需核对" if target_year in profile.get(
                "review_graduation_years", []
            ) else "可能不符"
        return "符合"
    if any(year in review_years for year in job.graduation_years):
        # A standalone 2027 cohort label is explicitly accepted by the
        # profile. An explicitly named, unconfigured season (for example
        # 2027秋招) remains review-only.
        if not window:
            return "符合" if "2027届" in accepted_windows else "需核对"
        return "符合" if window in accepted_windows else "需核对"
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
        window = recruitment_window(job)
        if window:
            reasons.append("毕业时间符合（{}） +15".format(window))
        else:
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
