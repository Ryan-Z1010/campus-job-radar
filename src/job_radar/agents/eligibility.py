from __future__ import annotations

import uuid
from collections import Counter
from typing import Any, Dict, Iterable, List

from ..models import JobPosting, utc_now_iso
from ..normalize import normalize_job
from ..scoring import score_job
from .types import AgentEvidence, AgentResult, AgentStatus


def _has_required_fields(job: JobPosting) -> bool:
    return bool(job.title and job.company and job.url and job.source_name)


class EligibilityAgent:
    """Normalize jobs and apply deterministic eligibility and scoring rules."""

    name = "EligibilityAgent"

    def run(
        self,
        jobs: Iterable[JobPosting],
        profile: Dict[str, Any],
        source_id: str = "",
    ) -> AgentResult:
        task_id = "eligibility-{}-{}".format(source_id or "batch", uuid.uuid4().hex[:8])
        started_at = utc_now_iso()
        normalized: List[JobPosting] = []
        invalid_count = 0
        for job in jobs:
            normalize_job(job)
            if not _has_required_fields(job):
                invalid_count += 1
                continue
            score_job(job, profile)
            normalized.append(job)

        distribution = Counter(job.eligibility for job in normalized)
        uncertain_count = sum(
            distribution.get(label, 0) for label in ("待核对", "需核对")
        )
        warnings = []
        if invalid_count:
            warnings.append("{} 个岗位缺少标题、公司、链接或来源，已剔除。".format(invalid_count))
        if uncertain_count:
            warnings.append("{} 个岗位的毕业届别需要人工核对。".format(uncertain_count))

        if invalid_count:
            status = AgentStatus.PARTIAL
        elif uncertain_count:
            status = AgentStatus.NEEDS_REVIEW
        else:
            status = AgentStatus.SUCCESS

        total = len(normalized)
        known_count = total - uncertain_count
        confidence = known_count / total if total else 1.0
        evidence = [
            AgentEvidence("有效字段", "{} 个岗位".format(total)),
            AgentEvidence(
                "资格分布",
                "；".join(
                    "{} {}".format(label, distribution[label])
                    for label in ("符合", "需核对", "待核对", "可能不符")
                    if distribution[label]
                )
                or "无岗位",
            ),
        ]
        return AgentResult(
            agent_name=self.name,
            task_id=task_id,
            status=status,
            jobs=normalized,
            evidence=evidence,
            warnings=warnings,
            next_action=(
                "交给 ReviewAgent 复核不确定届别与数据质量。"
                if normalized
                else "本来源没有可进入复核阶段的岗位。"
            ),
            confidence=confidence,
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata={
                "source_id": source_id,
                "invalid_count": invalid_count,
                "eligibility_counts": dict(distribution),
            },
        )
