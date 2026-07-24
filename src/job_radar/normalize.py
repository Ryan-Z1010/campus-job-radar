from __future__ import annotations

import re
from typing import Iterable, List

from .models import JobPosting


_SPACE_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"20(?:2[4-9]|3[0-5])")


def clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", (value or "").replace("\u3000", " ")).strip()


def infer_graduation_years(text: str) -> List[int]:
    return sorted({int(value) for value in _YEAR_RE.findall(text or "")})


def normalize_job(job: JobPosting) -> JobPosting:
    job.title = clean_text(job.title)
    job.company = clean_text(job.company)
    job.location = clean_text(job.location)
    job.description = clean_text(job.description)
    job.education = clean_text(job.education)
    if not job.graduation_years:
        job.graduation_years = infer_graduation_years(
            "{} {}".format(job.title, job.description)
        )
    return job


def valid_jobs(jobs: Iterable[JobPosting]) -> List[JobPosting]:
    return [
        job
        for job in jobs
        if job.title and job.company and job.url and job.source_name
    ]
