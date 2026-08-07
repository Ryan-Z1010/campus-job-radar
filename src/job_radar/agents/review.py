from __future__ import annotations

import uuid
from typing import Iterable, List, Set
from urllib.parse import urlsplit

from ..models import JobPosting, utc_now_iso
from .types import AgentEvidence, AgentResult, AgentStatus


class ReviewAgent:
    """Check duplicates, link safety and fields that require human review."""

    name = "ReviewAgent"

    def run(
        self,
        jobs: Iterable[JobPosting],
        source_id: str = "",
    ) -> AgentResult:
        task_id = "review-{}-{}".format(source_id or "batch", uuid.uuid4().hex[:8])
        started_at = utc_now_iso()
        reviewed: List[JobPosting] = []
        fingerprints: Set[str] = set()
        duplicate_count = 0
        unsafe_url_count = 0
        review_required_count = 0

        for job in jobs:
            if job.fingerprint in fingerprints:
                duplicate_count += 1
                continue
            fingerprints.add(job.fingerprint)

            scheme = urlsplit(job.url).scheme.lower()
            if scheme not in ("http", "https"):
                unsafe_url_count += 1
                continue
            if job.eligibility in ("待核对", "需核对"):
                review_required_count += 1
            reviewed.append(job)

        warnings = []
        if duplicate_count:
            warnings.append("{} 个批次内重复岗位已合并。".format(duplicate_count))
        if unsafe_url_count:
            warnings.append("{} 个非 HTTP(S) 链接已拒绝。".format(unsafe_url_count))
        if review_required_count:
            warnings.append("{} 个岗位进入人工核对队列。".format(review_required_count))

        if unsafe_url_count:
            status = AgentStatus.PARTIAL
        elif review_required_count:
            status = AgentStatus.NEEDS_REVIEW
        else:
            status = AgentStatus.SUCCESS

        total = len(reviewed)
        ready_count = total - review_required_count
        confidence = ready_count / total if total else 1.0
        return AgentResult(
            agent_name=self.name,
            task_id=task_id,
            status=status,
            jobs=reviewed,
            evidence=[
                AgentEvidence("复核后岗位", "{} 个".format(total)),
                AgentEvidence("可直接进入后续流程", "{} 个".format(ready_count)),
                AgentEvidence("需人工核对", "{} 个".format(review_required_count)),
            ],
            warnings=warnings,
            next_action=(
                "人工核对招聘官网中的毕业时间后，再决定是否提醒。"
                if review_required_count
                else "可进入存储、去重和通知流程。"
            ),
            confidence=confidence,
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata={
                "source_id": source_id,
                "duplicate_count": duplicate_count,
                "unsafe_url_count": unsafe_url_count,
                "review_required_count": review_required_count,
                "ready_count": ready_count,
            },
        )
