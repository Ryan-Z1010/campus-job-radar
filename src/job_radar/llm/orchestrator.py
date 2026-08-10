from __future__ import annotations

from html import escape
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
DEFAULT_LLM_MAX_JOBS = 50
REVIEW_ELIGIBILITIES = frozenset(("待核对", "需核对"))


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
    deterministic_source_errors: List[str] = field(default_factory=list)
    deterministic_counts: Dict[str, int] = field(default_factory=dict)
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
            "deterministic": {
                "counts": dict(self.deterministic_counts),
                "source_errors": list(self.deterministic_source_errors),
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
        max_jobs: int = DEFAULT_LLM_MAX_JOBS,
        notify_min_score: int = DEFAULT_LLM_NOTIFY_MIN_SCORE,
    ) -> LlmRunResult:
        if max_jobs < 1:
            raise ValueError("max_jobs 必须大于 0")
        if not 0 <= notify_min_score <= 100:
            raise ValueError("notify_min_score 必须位于 0 到 100")
        started_at = utc_now_iso()
        ordered = sorted(
            list(jobs),
            key=lambda job: (
                0 if job.eligibility in REVIEW_ELIGIBILITIES else 1,
                -job.score,
                job.company,
                job.title,
                job.fingerprint,
            ),
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
        review_queue_job = job.eligibility in REVIEW_ELIGIBILITIES
        eligibility_verdict = (
            semantic_match.get("eligibility_verdict")
            if isinstance(semantic_match, Mapping)
            else None
        )
        eligibility_evidence = (
            semantic_match.get("eligibility_evidence", [])
            if isinstance(semantic_match, Mapping)
            else []
        )
        eligibility_evidence_ok = (
            isinstance(eligibility_evidence, list)
            and any(str(item).strip() for item in eligibility_evidence)
        )
        job_text = " ".join(
            (
                job.title,
                job.description,
                job.education,
                job.published_at,
                job.deadline,
            )
        ).lower()
        eligibility_evidence_grounded = (
            eligibility_evidence_ok
            and any(str(item).strip().lower() in job_text for item in eligibility_evidence)
        )
        review_eligibility_confirmed = (
            review_queue_job
            and eligibility_verdict == "confirmed_fit"
            and eligibility_evidence_grounded
        )
        deterministic_ok = job.eligibility == "符合" or review_eligibility_confirmed
        score_ok = (
            isinstance(llm_score, int)
            and not isinstance(llm_score, bool)
            and llm_score >= notify_min_score
        )
        status_ok = analysis.get("status") == AgentStatus.SUCCESS.value
        critic_ok = critic_verdict == "accept"
        if (
            review_queue_job
            and status_ok
            and (
                eligibility_verdict not in {"confirmed_fit", "confirmed_unfit"}
                or (
                    eligibility_verdict == "confirmed_fit"
                    and not eligibility_evidence_grounded
                )
            )
        ):
            analysis["status"] = AgentStatus.NEEDS_REVIEW.value
            analysis["review_reason"] = (
                "LLM未能用岗位原文证据确认毕业届别或招聘批次，仍需人工核对。"
            )
            status_ok = False
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
            gate_mode = (
                "direct_state_owned_with_fit_and_llm_eligibility"
                if review_queue_job
                else "direct_state_owned_with_fit"
            )
        else:
            eligible = status_ok and deterministic_ok and score_ok and critic_ok
            gate_mode = "fit_score_with_llm_eligibility" if review_queue_job else "fit_score"
        reasons = []
        if not status_ok:
            reasons.append("分析未成功")
        if not deterministic_ok:
            if review_queue_job and eligibility_verdict == "confirmed_unfit":
                reasons.append("LLM确认毕业届别或招聘批次不符合")
            elif review_queue_job:
                reasons.append("LLM未确认确定性资格")
            else:
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
            "eligibility_verdict": eligibility_verdict,
            "eligibility_evidence": list(eligibility_evidence)
            if isinstance(eligibility_evidence, list)
            else [],
            "eligibility_evidence_grounded": eligibility_evidence_grounded,
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


def manual_review_analyses(result: LlmRunResult) -> List[Mapping[str, Any]]:
    """Return analyses that need a human check or a retry.

    A successful but low-fit analysis is intentionally not included: it is a
    normal non-match.  Only an LLM ``needs_review`` or ``failed`` status is
    actionable for the human-review digest.
    """

    return [
        analysis
        for analysis in result.analyses
        if analysis.get("status")
        in {AgentStatus.NEEDS_REVIEW.value, AgentStatus.FAILED.value}
        and isinstance(analysis.get("job"), Mapping)
    ]


def deterministic_review_jobs(
    jobs: Iterable[JobPosting],
) -> List[JobPosting]:
    """Return jobs retained by deterministic screening for human checking.

    These jobs are deliberately not sent through the bounded LLM sample.  They
    still need to reach the candidate, however, because the deterministic
    review queue is actionable information rather than a silent counter in the
    run summary.
    """

    return [
        job
        for job in jobs
        if job.eligibility in REVIEW_ELIGIBILITIES
    ]


def review_notification_analyses(
    result: LlmRunResult,
) -> List[Mapping[str, Any]]:
    """Return only items still unresolved after LLM analysis.

    Deterministic review jobs are selected for LLM analysis first.  They are
    not emailed merely because deterministic rules marked them uncertain; the
    human-review email is reserved for LLM uncertainty or failure.
    """

    return list(manual_review_analyses(result))


def _manual_review_record(analysis: Mapping[str, Any]) -> Dict[str, Any]:
    job = analysis.get("job", {})
    semantic = analysis.get("semantic_match", {})
    critic = analysis.get("critic_review", {})
    return {
        "fingerprint": str(job.get("fingerprint", "")),
        "company": str(job.get("company", "")),
        "title": str(job.get("title", "")),
        "location": str(job.get("location", "")),
        "company_type": str(job.get("company_type", "")),
        "url": str(job.get("url", "")),
        "deterministic_score": job.get("score", 0),
        "deterministic_eligibility": str(job.get("eligibility", "")),
        "status": str(analysis.get("status", "")),
        "reason": str(analysis.get("review_reason", "")),
        "llm_score": (
            semantic.get("score")
            if isinstance(semantic, Mapping)
            else None
        ),
        "critic_verdict": (
            critic.get("verdict") if isinstance(critic, Mapping) else None
        ),
        "critic_issues": (
            list(critic.get("issues", []))[:5]
            if isinstance(critic, Mapping)
            and isinstance(critic.get("issues", []), list)
            else []
        ),
        "hard_constraint_risks": (
            list(semantic.get("hard_constraint_risks", []))[:5]
            if isinstance(semantic, Mapping)
            and isinstance(semantic.get("hard_constraint_risks", []), list)
            else []
        ),
    }


def render_manual_review_digest(analyses: Iterable[Mapping[str, Any]]) -> str:
    """Render a clearly non-approval digest for human follow-up."""

    records = [_manual_review_record(analysis) for analysis in analyses]
    items = []
    for record in records:
        reason = record["reason"] or "自动分析未能给出可直接通知的结论。"
        issues = record["critic_issues"] + record["hard_constraint_risks"]
        details = "；".join(str(item) for item in issues if item)
        if details:
            reason = "{} {}".format(reason, details)
        url = record["url"]
        link = (
            '<a href="{}">查看官方岗位</a>'.format(escape(url, quote=True))
            if url.startswith(("http://", "https://"))
            else "官方链接不可用"
        )
        items.append(
            "<li><strong>{company} · {title}</strong> · {location} · {company_type}"
            "<br>确定性评分：{score}；LLM评分：{llm_score}；状态：{status}"
            "<br>复核原因：{reason}<br>{link}</li>".format(
                company=escape(record["company"]),
                title=escape(record["title"]),
                location=escape(record["location"]),
                company_type=escape(record["company_type"]),
                score=escape(str(record["deterministic_score"])),
                llm_score=escape(str(record["llm_score"] or "未完成")),
                status=escape(record["status"]),
                reason=escape(reason),
                link=link,
            )
        )
    return (
        "<html><body>"
        "<h1>CampusJobRadar：待人工复核岗位</h1>"
        "<p>以下岗位未通过自动通知门槛，不代表系统建议直接投递。请先核对官方公告、"
        "毕业届别和岗位要求，再决定是否申请。</p>"
        "<ul>{}</ul>"
        "</body></html>".format("".join(items))
    )


def write_llm_review_preview(
    result: LlmRunResult,
    directory: str,
) -> Path:
    """Write a review-only preview, separate from approved notifications."""

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    analyses = review_notification_analyses(result)
    records = [_manual_review_record(item) for item in analyses]
    json_path = output / "manual-review.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    html_path = output / "manual-review.html"
    html_path.write_text(
        render_manual_review_digest(analyses),
        encoding="utf-8",
    )
    return output


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


def send_llm_review_notification_email(
    result: LlmRunResult,
    sender: Callable[[str, str], None],
    database: Optional[str] = None,
) -> int:
    """Send all human-review items with retry-safe fingerprint deduplication."""

    analyses = review_notification_analyses(result)
    jobs = [
        JobPosting.from_mapping(dict(analysis["job"]))
        for analysis in analyses
        if isinstance(analysis.get("job"), Mapping)
    ]
    unique_jobs = []
    seen = set()
    for job in jobs:
        if job.fingerprint in seen:
            continue
        unique_jobs.append(job)
        seen.add(job.fingerprint)
    jobs = unique_jobs
    store = JobStore(database) if database else None
    try:
        if store is not None:
            jobs = store.new_jobs(jobs)
        if not jobs:
            return 0
        fingerprints = {job.fingerprint for job in jobs}
        selected = [
            analysis
            for analysis in analyses
            if str(analysis.get("job", {}).get("fingerprint", ""))
            in fingerprints
        ]
        sender(
            "CampusJobRadar：有 {} 个岗位需要人工复核".format(len(jobs)),
            render_manual_review_digest(selected),
        )
        if store is not None:
            store.upsert(jobs)
        return len(jobs)
    finally:
        if store is not None:
            store.close()
