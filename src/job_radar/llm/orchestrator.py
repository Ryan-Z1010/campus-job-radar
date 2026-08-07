from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from ..agents.types import AgentResult, AgentStatus
from ..models import JobPosting, utc_now_iso
from ..reporting import render_digest, write_reports
from ..storage import JobStore
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
DEFAULT_LLM_NOTIFY_MIN_SCORE = 70


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
    notify_eligible: int = 0
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
                "notify_eligible": self.notify_eligible,
            },
            "safety": {
                "writes_primary_job_database": False,
                "sends_email": False,
                "notification_gate_only": True,
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
        notify_min_score: int = DEFAULT_LLM_NOTIFY_MIN_SCORE,
    ) -> LlmRunResult:
        if max_jobs < 1:
            raise ValueError("max_jobs 必须大于 0")
        if not 0 <= notify_min_score <= 100:
            raise ValueError("notify_min_score 必须位于 0 到 100")
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
                cached = self._apply_notification_gate(
                    cached, job, notify_min_score
                )
                result.analyses.append(cached)
                result.cache_hits += 1
                if cached.get("notify_eligible"):
                    result.notify_eligible += 1
                self._count_analysis(result, cached)
                continue

            analysis = self._analyze_job(job, profile)
            analysis = self._apply_notification_gate(
                analysis, job, notify_min_score
            )
            result.analyses.append(analysis)
            if analysis.get("notify_eligible"):
                result.notify_eligible += 1
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

    @staticmethod
    def _apply_notification_gate(
        analysis: Dict[str, Any],
        job: JobPosting,
        notify_min_score: int,
    ) -> Dict[str, Any]:
        semantic_match = analysis.get("semantic_match", {})
        critic_review = analysis.get("critic_review", {})
        llm_score = (
            semantic_match.get("score")
            if isinstance(semantic_match, Mapping)
            else None
        )
        critic_verdict = (
            critic_review.get("verdict")
            if isinstance(critic_review, Mapping)
            else None
        )
        direct_company = job.company_type in {"央企", "国企"}
        deterministic_ok = job.eligibility == "符合"
        score_ok = (
            isinstance(llm_score, int)
            and not isinstance(llm_score, bool)
            and llm_score >= notify_min_score
        )
        status_ok = analysis.get("status") == AgentStatus.SUCCESS.value
        critic_ok = critic_verdict == "accept"
        if direct_company:
            semantic_verdict = (
                semantic_match.get("verdict")
                if isinstance(semantic_match, Mapping)
                else None
            )
            matched_requirements = (
                semantic_match.get("matched_requirements", [])
                if isinstance(semantic_match, Mapping)
                else []
            )
            semantic_fit = (
                semantic_verdict in {"strong_match", "match"}
                and isinstance(matched_requirements, list)
                and bool(matched_requirements)
                and critic_ok
            )
            eligible = status_ok and deterministic_ok and semantic_fit
            gate_mode = "direct_state_owned_with_fit"
        else:
            eligible = status_ok and deterministic_ok and score_ok and critic_ok
            gate_mode = "fit_score"
        reasons = []
        if not status_ok:
            reasons.append("分析未成功")
        if not deterministic_ok:
            reasons.append("确定性资格不是“符合”")
        if direct_company and not (
            isinstance(semantic_match, Mapping)
            and semantic_match.get("verdict") in {"strong_match", "match"}
            and isinstance(semantic_match.get("matched_requirements"), list)
            and bool(semantic_match.get("matched_requirements"))
            and critic_ok
        ):
            reasons.append("LLM 未确认岗位方向适配")
        if not direct_company and not score_ok:
            reasons.append("LLM 分数未达到 {}".format(notify_min_score))
        if not direct_company and not critic_ok:
            reasons.append("Critic 未判定 accept")
        analysis["notification_gate"] = {
            "eligible": eligible,
            "mode": gate_mode,
            "company_type": job.company_type,
            "deterministic_eligibility": job.eligibility,
            "deterministic_score": job.score,
            "llm_score": llm_score,
            "llm_minimum_score": notify_min_score,
            "critic_verdict": critic_verdict,
            "reasons": reasons,
        }
        analysis["notify_eligible"] = bool(
            analysis["notification_gate"]["eligible"]
        )
        return analysis


def write_llm_report(result: LlmRunResult, path: str) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return report_path


def notification_jobs(result: LlmRunResult) -> List[JobPosting]:
    """Convert only gate-approved analyses into notification jobs."""

    jobs = []
    for analysis in result.analyses:
        if analysis.get("notify_eligible") is not True:
            continue
        job_data = analysis.get("job")
        if isinstance(job_data, Mapping):
            jobs.append(JobPosting.from_mapping(dict(job_data)))
    return jobs


def write_llm_notification_preview(
    result: LlmRunResult, directory: str
) -> Path:
    """Write a no-send digest containing only gate-approved jobs."""

    output = Path(directory)
    write_reports(notification_jobs(result), str(output))
    return output


def send_llm_notification_email(
    result: LlmRunResult,
    sender: Callable[[str, str], None],
    database: Optional[str] = None,
) -> int:
    """Send gate-approved jobs with optional retry-safe fingerprint deduplication."""

    jobs = notification_jobs(result)
    store = JobStore(database) if database else None
    try:
        if store is not None:
            jobs = store.new_jobs(jobs)
        if not jobs:
            return 0
        sender(
            "CampusJobRadar：LLM审核通过 {} 个岗位".format(len(jobs)),
            render_digest(jobs),
        )
        if store is not None:
            store.upsert(jobs)
        return len(jobs)
    finally:
        if store is not None:
            store.close()
