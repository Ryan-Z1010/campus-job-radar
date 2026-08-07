from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..agents.types import AgentResult, AgentStatus
from ..models import JobPosting, utc_now_iso
from .agents import (
    CRITIC_PROMPT_VERSION,
    JD_PROMPT_VERSION,
    MATCH_PROMPT_VERSION,
    CriticAgent,
    JDUnderstandingAgent,
    SemanticMatchingAgent,
)
from .cache import LlmAnalysisCache
from .client import StructuredLlmClient


PROMPT_BUNDLE_VERSION = "{}|{}|{}".format(
    JD_PROMPT_VERSION,
    MATCH_PROMPT_VERSION,
    CRITIC_PROMPT_VERSION,
)


@dataclass
class LlmRunResult:
    model: str
    started_at: str
    finished_at: str
    status: AgentStatus = AgentStatus.SUCCESS
    selected: int = 0
    analyzed: int = 0
    cache_hits: int = 0
    needs_review: int = 0
    failed: int = 0
    analyses: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "orchestrator": "LlmRecruitmentOrchestrator",
            "mode": "llm-analysis-only",
            "model": self.model,
            "prompt_version": PROMPT_BUNDLE_VERSION,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "counts": {
                "selected": self.selected,
                "analyzed": self.analyzed,
                "cache_hits": self.cache_hits,
                "needs_review": self.needs_review,
                "failed": self.failed,
            },
            "safety": {
                "writes_primary_job_database": False,
                "sends_email": False,
                "max_semantic_revisions_per_job": 1,
                "profile_is_sanitized": True,
            },
            "analyses": list(self.analyses),
        }


class LlmRecruitmentOrchestrator:
    """Bounded JD -> matching -> critic workflow with at most one revision."""

    name = "LlmRecruitmentOrchestrator"

    def __init__(
        self,
        client: StructuredLlmClient,
        cache: Optional[LlmAnalysisCache] = None,
        jd_agent: Optional[JDUnderstandingAgent] = None,
        matching_agent: Optional[SemanticMatchingAgent] = None,
        critic_agent: Optional[CriticAgent] = None,
    ):
        self.client = client
        self.cache = cache
        self.jd_agent = jd_agent or JDUnderstandingAgent(client)
        self.matching_agent = matching_agent or SemanticMatchingAgent(client)
        self.critic_agent = critic_agent or CriticAgent(client)

    def run(
        self,
        jobs: Iterable[JobPosting],
        profile: Mapping[str, Any],
        max_jobs: int = 10,
    ) -> LlmRunResult:
        if max_jobs < 1:
            raise ValueError("max_jobs 必须大于 0")
        started_at = utc_now_iso()
        ordered = sorted(
            list(jobs),
            key=lambda job: (-job.score, job.company, job.title, job.fingerprint),
        )
        uncached: List[tuple] = []
        cached_jobs: List[tuple] = []
        for job in ordered:
            cached = self._cache_get(job, profile)
            if cached is None:
                uncached.append((job, None))
            else:
                cached_jobs.append((job, cached))
        selected = uncached[:max_jobs]
        remaining = max_jobs - len(selected)
        if remaining:
            selected.extend(cached_jobs[:remaining])
        result = LlmRunResult(
            model=self.client.model,
            started_at=started_at,
            finished_at=started_at,
            selected=len(selected),
        )

        for job, cached in selected:
            if cached is not None:
                result.analyses.append(cached)
                result.cache_hits += 1
                self._count_analysis(result, cached)
                continue

            analysis = self._analyze_job(job, profile)
            result.analyses.append(analysis)
            self._count_analysis(result, analysis)
            if analysis["status"] != AgentStatus.FAILED.value and analysis.get(
                "cacheable", True
            ):
                self._cache_put(job, profile, analysis)

        if result.failed and result.analyzed == 0:
            result.status = AgentStatus.FAILED
        elif result.failed:
            result.status = AgentStatus.PARTIAL
        elif result.needs_review:
            result.status = AgentStatus.NEEDS_REVIEW
        result.finished_at = utc_now_iso()
        return result

    def _analyze_job(
        self, job: JobPosting, profile: Mapping[str, Any]
    ) -> Dict[str, Any]:
        steps: List[AgentResult] = []
        jd_result = self.jd_agent.run(job)
        steps.append(jd_result)
        if jd_result.status == AgentStatus.FAILED:
            return self._failed_analysis(job, steps, jd_result.error)
        jd = dict(jd_result.metadata["analysis"])

        match_result = self.matching_agent.run(job, profile, jd)
        steps.append(match_result)
        if match_result.status == AgentStatus.FAILED:
            return self._failed_analysis(job, steps, match_result.error)
        semantic_match = dict(match_result.metadata["analysis"])

        critic_result = self.critic_agent.run(job, profile, jd, semantic_match)
        steps.append(critic_result)
        if critic_result.status == AgentStatus.FAILED:
            return self._review_analysis(
                job,
                jd,
                semantic_match,
                {},
                steps,
                revisions=0,
                reason="CriticAgent 调用失败，需要人工复核: {}".format(
                    critic_result.error
                ),
                cacheable=False,
            )

        critic = dict(critic_result.metadata["analysis"])
        revisions = 0
        if critic.get("verdict") == "revise":
            revisions = 1
            revised_match = self.matching_agent.run(
                job,
                profile,
                jd,
                critique=critic,
            )
            steps.append(revised_match)
            if revised_match.status == AgentStatus.FAILED:
                return self._review_analysis(
                    job,
                    jd,
                    semantic_match,
                    critic,
                    steps,
                    revisions,
                    "语义匹配修订失败，需要人工复核: {}".format(
                        revised_match.error
                    ),
                    cacheable=False,
                )
            semantic_match = dict(revised_match.metadata["analysis"])
            final_critic = self.critic_agent.run(
                job, profile, jd, semantic_match
            )
            steps.append(final_critic)
            if final_critic.status == AgentStatus.FAILED:
                return self._review_analysis(
                    job,
                    jd,
                    semantic_match,
                    critic,
                    steps,
                    revisions,
                    "最终审校失败，需要人工复核: {}".format(final_critic.error),
                    cacheable=False,
                )
            critic = dict(final_critic.metadata["analysis"])

        if critic.get("verdict") != "accept":
            reason = (
                "审校要求人工复核"
                if critic.get("verdict") == "manual_review"
                else "达到一次修订上限后仍未通过审校"
            )
            return self._review_analysis(
                job,
                jd,
                semantic_match,
                critic,
                steps,
                revisions,
                reason,
            )

        return {
            "job": job.to_dict(),
            "status": AgentStatus.SUCCESS.value,
            "cached": False,
            "cacheable": True,
            "revisions": revisions,
            "jd_understanding": jd,
            "semantic_match": semantic_match,
            "critic_review": critic,
            "review_reason": "",
            "trace": [step.to_dict() for step in steps],
        }

    @staticmethod
    def _failed_analysis(
        job: JobPosting, steps: List[AgentResult], error: str
    ) -> Dict[str, Any]:
        return {
            "job": job.to_dict(),
            "status": AgentStatus.FAILED.value,
            "cached": False,
            "cacheable": False,
            "revisions": 0,
            "jd_understanding": {},
            "semantic_match": {},
            "critic_review": {},
            "review_reason": error,
            "trace": [step.to_dict() for step in steps],
        }

    @staticmethod
    def _review_analysis(
        job: JobPosting,
        jd: Mapping[str, Any],
        semantic_match: Mapping[str, Any],
        critic: Mapping[str, Any],
        steps: List[AgentResult],
        revisions: int,
        reason: str,
        cacheable: bool = True,
    ) -> Dict[str, Any]:
        return {
            "job": job.to_dict(),
            "status": AgentStatus.NEEDS_REVIEW.value,
            "cached": False,
            "cacheable": cacheable,
            "revisions": revisions,
            "jd_understanding": dict(jd),
            "semantic_match": dict(semantic_match),
            "critic_review": dict(critic),
            "review_reason": reason,
            "trace": [step.to_dict() for step in steps],
        }

    def _cache_get(
        self, job: JobPosting, profile: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if self.cache is None:
            return None
        return self.cache.get(
            job,
            profile,
            self.client.model,
            PROMPT_BUNDLE_VERSION,
        )

    def _cache_put(
        self,
        job: JobPosting,
        profile: Mapping[str, Any],
        analysis: Mapping[str, Any],
    ) -> None:
        if self.cache is not None:
            self.cache.put(
                job,
                profile,
                self.client.model,
                PROMPT_BUNDLE_VERSION,
                analysis,
            )

    @staticmethod
    def _count_analysis(result: LlmRunResult, analysis: Mapping[str, Any]) -> None:
        status = analysis.get("status")
        if status == AgentStatus.FAILED.value:
            result.failed += 1
        else:
            result.analyzed += 1
            if status == AgentStatus.NEEDS_REVIEW.value:
                result.needs_review += 1


def write_llm_report(result: LlmRunResult, path: str) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return report_path
