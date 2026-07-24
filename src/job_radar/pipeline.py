from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .collectors import build_collector
from .models import JobPosting
from .normalize import normalize_job, valid_jobs
from .notifications import send_email
from .reporting import render_digest, write_reports
from .scoring import score_job
from .storage import JobStore


@dataclass
class RunResult:
    collected: int = 0
    valid: int = 0
    inserted: int = 0
    updated: int = 0
    alerted: int = 0
    source_errors: List[str] = field(default_factory=list)


def run_pipeline(
    profile: Dict[str, Any],
    sources: List[Dict[str, Any]],
    database: str,
    report_dir: str,
    dry_run: bool = False,
    include_demo: bool = False,
) -> RunResult:
    result = RunResult()
    collected_jobs: List[JobPosting] = []
    for source in sources:
        if not source.get("enabled", False):
            continue
        if source.get("demo", False) and not include_demo:
            continue
        try:
            jobs = build_collector(source).collect()
            collected_jobs.extend(jobs)
            result.collected += len(jobs)
        except Exception as exc:
            result.source_errors.append("{}: {}".format(source.get("name"), exc))

    normalized = valid_jobs(normalize_job(job) for job in collected_jobs)
    result.valid = len(normalized)
    for job in normalized:
        score_job(job, profile)

    store = JobStore(database)
    try:
        new_jobs, result.updated = store.upsert(normalized)
    finally:
        store.close()
    result.inserted = len(new_jobs)

    threshold = int(profile.get("minimum_score", 0))
    alert_jobs = [job for job in new_jobs if job.score >= threshold]
    result.alerted = len(alert_jobs)
    write_reports(alert_jobs, report_dir)
    if alert_jobs and not dry_run:
        send_email("CampusJobRadar：发现 {} 个新岗位".format(len(alert_jobs)), render_digest(alert_jobs))
    return result
