from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from ..models import JobPosting, utc_now_iso
from .collection import CollectionAgent
from .eligibility import EligibilityAgent
from .review import ReviewAgent
from .types import AgentStatus, AgentTrace


@dataclass
class MultiAgentRunResult:
    """Auditable outcome of an OrchestratorAgent shadow run."""

    run_id: str
    status: AgentStatus
    started_at: str
    finished_at: str
    source_total: int = 0
    completed_sources: int = 0
    failed_sources: int = 0
    collected: int = 0
    valid: int = 0
    reviewed: int = 0
    ready: int = 0
    review_required: int = 0
    jobs: List[JobPosting] = field(default_factory=list)
    traces: List[AgentTrace] = field(default_factory=list)
    source_errors: List[str] = field(default_factory=list)

    def to_dict(self, include_jobs: bool = True) -> Dict[str, Any]:
        result = {
            "run_id": self.run_id,
            "orchestrator": "OrchestratorAgent",
            "mode": "shadow",
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "counts": {
                "source_total": self.source_total,
                "completed_sources": self.completed_sources,
                "failed_sources": self.failed_sources,
                "collected": self.collected,
                "valid": self.valid,
                "reviewed": self.reviewed,
                "ready": self.ready,
                "review_required": self.review_required,
            },
            "source_errors": list(self.source_errors),
            "traces": [trace.to_dict() for trace in self.traces],
        }
        if include_jobs:
            result["jobs"] = [job.to_dict() for job in self.jobs]
        return result


def _trace_status(steps) -> AgentStatus:
    statuses = {step.status for step in steps}
    if AgentStatus.FAILED in statuses:
        return AgentStatus.FAILED
    if AgentStatus.PARTIAL in statuses:
        return AgentStatus.PARTIAL
    if AgentStatus.NEEDS_REVIEW in statuses:
        return AgentStatus.NEEDS_REVIEW
    return AgentStatus.SUCCESS


class OrchestratorAgent:
    """Coordinate specialist agents without changing the production pipeline."""

    name = "OrchestratorAgent"

    def __init__(
        self,
        collection_agent: Optional[CollectionAgent] = None,
        eligibility_agent: Optional[EligibilityAgent] = None,
        review_agent: Optional[ReviewAgent] = None,
    ):
        self.collection_agent = collection_agent or CollectionAgent()
        self.eligibility_agent = eligibility_agent or EligibilityAgent()
        self.review_agent = review_agent or ReviewAgent()

    def run(
        self,
        profile: Dict[str, Any],
        sources: Iterable[Dict[str, Any]],
        include_demo: bool = False,
        source_ids: Optional[Iterable[str]] = None,
    ) -> MultiAgentRunResult:
        started_at = utc_now_iso()
        source_list = list(sources)
        requested: Optional[Set[str]] = set(source_ids) if source_ids else None
        available_ids = {str(source.get("id", "")) for source in source_list}
        if requested:
            missing = sorted(requested - available_ids)
            if missing:
                raise ValueError("未找到来源 ID: {}".format(", ".join(missing)))

        selected = [
            source
            for source in source_list
            if source.get("enabled", False)
            and (include_demo or not source.get("demo", False))
            and (requested is None or str(source.get("id", "")) in requested)
        ]
        result = MultiAgentRunResult(
            run_id="agent-run-{}".format(uuid.uuid4().hex),
            status=AgentStatus.SUCCESS,
            started_at=started_at,
            finished_at=started_at,
            source_total=len(selected),
        )

        for source in selected:
            source_id = str(source.get("id", ""))
            source_name = str(source.get("name", source_id))
            collection = self.collection_agent.run(source)
            result.collected += len(collection.jobs)
            steps = [collection]

            if collection.status == AgentStatus.FAILED:
                result.failed_sources += 1
                result.source_errors.append("{}: {}".format(source_name, collection.error))
                result.traces.append(
                    AgentTrace(source_id, source_name, AgentStatus.FAILED, steps)
                )
                continue

            eligibility = self.eligibility_agent.run(
                collection.jobs, profile, source_id=source_id
            )
            result.valid += len(eligibility.jobs)
            steps.append(eligibility)

            review = self.review_agent.run(eligibility.jobs, source_id=source_id)
            result.reviewed += len(review.jobs)
            result.review_required += int(
                review.metadata.get("review_required_count", 0)
            )
            result.ready += int(review.metadata.get("ready_count", 0))
            result.jobs.extend(review.jobs)
            steps.append(review)
            result.completed_sources += 1
            result.traces.append(
                AgentTrace(source_id, source_name, _trace_status(steps), steps)
            )

        if result.failed_sources and result.completed_sources == 0:
            result.status = AgentStatus.FAILED
        elif result.failed_sources or any(
            trace.status == AgentStatus.PARTIAL for trace in result.traces
        ):
            result.status = AgentStatus.PARTIAL
        elif result.review_required:
            result.status = AgentStatus.NEEDS_REVIEW
        result.finished_at = utc_now_iso()
        return result


def write_agent_trace(result: MultiAgentRunResult, path: str) -> Path:
    trace_path = Path(path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return trace_path
