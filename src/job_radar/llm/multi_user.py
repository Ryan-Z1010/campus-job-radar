"""Shared collection with isolated LLM analysis for multiple users."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..agents.collection import CollectionAgent
from ..agents.eligibility import EligibilityAgent
from ..agents.review import ReviewAgent
from ..agents.types import AgentResult, AgentStatus
from ..config import job_is_excluded, source_is_excluded
from ..models import JobPosting
from ..multi_user import (
    UserSpec,
    load_user_monitoring,
    load_user_profile,
)
from .cache import LlmAnalysisCache
from .orchestrator import LlmRecruitmentOrchestrator, LlmRunResult
from .client import StructuredLlmClient


def _clone_job(job: JobPosting) -> JobPosting:
    return JobPosting.from_mapping(job.to_dict())


@dataclass
class MultiUserLlmUserResult:
    user: UserSpec
    result: LlmRunResult
    deterministic_counts: Dict[str, int] = field(default_factory=dict)
    source_errors: List[str] = field(default_factory=list)


@dataclass
class MultiUserLlmRunResult:
    source_total: int
    collected: int
    source_errors: List[str] = field(default_factory=list)
    users: List[MultiUserLlmUserResult] = field(default_factory=list)


class MultiUserLlmOrchestrator:
    """Collect each source once, then run an isolated pipeline per user."""

    def __init__(
        self,
        collection_agent: Optional[CollectionAgent] = None,
        eligibility_agent: Optional[EligibilityAgent] = None,
        review_agent: Optional[ReviewAgent] = None,
    ):
        self.collection_agent = collection_agent or CollectionAgent()
        self.eligibility_agent = eligibility_agent or EligibilityAgent()
        self.review_agent = review_agent or ReviewAgent()

    def _collect(
        self,
        sources: Sequence[Dict[str, Any]],
        include_demo: bool,
        source_ids: Optional[Iterable[str]],
        collection_workers: int,
    ) -> Tuple[List[Tuple[Dict[str, Any], AgentResult]], List[str]]:
        if collection_workers < 1:
            raise ValueError("collection_workers 必须大于 0")
        requested = set(source_ids) if source_ids else None
        available = {str(source.get("id", "")) for source in sources}
        missing = sorted(requested - available) if requested else []
        if missing:
            raise ValueError("未找到来源 ID: {}".format(", ".join(missing)))
        selected = [
            source
            for source in sources
            if source.get("enabled", False)
            and (include_demo or not source.get("demo", False))
            and (requested is None or str(source.get("id", "")) in requested)
        ]
        if collection_workers == 1 or len(selected) < 2:
            collections = [self.collection_agent.run(source) for source in selected]
        else:
            workers = min(collection_workers, len(selected))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                collections = list(executor.map(self.collection_agent.run, selected))
        pairs = list(zip(selected, collections))
        errors = [
            "{}: {}".format(source.get("name", source.get("id", "")), collection.error)
            for source, collection in pairs
            if collection.status == AgentStatus.FAILED
        ]
        return pairs, errors

    def _prepare_user_jobs(
        self,
        user: UserSpec,
        pairs: Sequence[Tuple[Dict[str, Any], AgentResult]],
        job_keywords: Optional[Iterable[str]] = None,
    ) -> Tuple[List[JobPosting], Dict[str, int], List[str]]:
        profile = load_user_profile(user)
        monitoring = load_user_monitoring(user)
        source_errors: List[str] = []
        jobs: List[JobPosting] = []
        counts = {
            "source_total": 0,
            "excluded_sources": 0,
            "completed_sources": 0,
            "failed_sources": 0,
            "collected": 0,
            "valid": 0,
            "reviewed": 0,
            "ready": 0,
            "review_required": 0,
            "company_excluded_jobs": 0,
            "keyword_filtered": 0,
        }
        terms = [
            str(keyword).strip().casefold()
            for keyword in (job_keywords or [])
            if str(keyword).strip()
        ]

        for source, collection in pairs:
            if source_is_excluded(source, monitoring):
                counts["excluded_sources"] += 1
                continue
            counts["source_total"] += 1
            counts["collected"] += len(collection.jobs)
            if collection.status == AgentStatus.FAILED:
                counts["failed_sources"] += 1
                source_errors.append(
                    "{}: {}".format(
                        source.get("name", source.get("id", "")), collection.error
                    )
                )
                continue
            counts["completed_sources"] += 1
            source_id = str(source.get("id", ""))
            source_jobs = []
            for job in collection.jobs:
                if job_is_excluded(job, monitoring):
                    counts["company_excluded_jobs"] += 1
                    continue
                source_jobs.append(_clone_job(job))
            eligibility = self.eligibility_agent.run(
                source_jobs,
                profile,
                source_id=source_id,
            )
            counts["valid"] += len(eligibility.jobs)
            review = self.review_agent.run(eligibility.jobs, source_id=source_id)
            counts["reviewed"] += len(review.jobs)
            counts["review_required"] += int(
                review.metadata.get("review_required_count", 0)
            )
            counts["ready"] += int(review.metadata.get("ready_count", 0))
            jobs.extend(review.jobs)

        if terms:
            before = len(jobs)
            jobs = [
                job
                for job in jobs
                if any(
                    term in " ".join(
                        (job.title, job.description, job.education, job.source_name)
                    ).casefold()
                    for term in terms
                )
            ]
            counts["keyword_filtered"] = before - len(jobs)
        return jobs, counts, source_errors

    def run(
        self,
        users: Sequence[UserSpec],
        sources: Sequence[Dict[str, Any]],
        client: StructuredLlmClient,
        include_demo: bool = False,
        source_ids: Optional[Iterable[str]] = None,
        max_jobs: int = 50,
        notify_min_score: int = 70,
        collection_workers: int = 4,
        analysis_workers: int = 1,
        no_cache: bool = False,
        job_keywords: Optional[Iterable[str]] = None,
    ) -> MultiUserLlmRunResult:
        if not users:
            raise ValueError("至少需要一个用户配置")
        pairs, shared_errors = self._collect(
            sources, include_demo, source_ids, collection_workers
        )
        user_results: List[MultiUserLlmUserResult] = []
        for user in users:
            profile = load_user_profile(user)
            jobs, counts, user_errors = self._prepare_user_jobs(
                user, pairs, job_keywords=job_keywords
            )
            cache = (
                None
                if no_cache
                else LlmAnalysisCache(user.llm_cache_database)
            )
            result = LlmRecruitmentOrchestrator(client, cache=cache).run(
                jobs,
                profile,
                max_jobs=max_jobs,
                notify_min_score=notify_min_score,
                analysis_workers=analysis_workers,
            )
            result.deterministic_source_errors = list(shared_errors) + list(
                user_errors
            )
            result.deterministic_counts = dict(counts)
            user_results.append(
                MultiUserLlmUserResult(
                    user=user,
                    result=result,
                    deterministic_counts=counts,
                    source_errors=user_errors,
                )
            )
        return MultiUserLlmRunResult(
            source_total=len(pairs),
            collected=sum(len(collection.jobs) for _, collection in pairs),
            source_errors=shared_errors,
            users=user_results,
        )
