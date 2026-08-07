from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from ..models import JobPosting, utc_now_iso
from .collection import CollectionAgent
from .eligibility import EligibilityAgent
from .notification import NotificationAgent
from .review import ReviewAgent
from .storage import StorageAgent
from .types import AgentResult, AgentStatus, AgentTrace


@dataclass
class MultiAgentRunResult:
    """Auditable outcome of an OrchestratorAgent run."""

    run_id: str
    status: AgentStatus
    started_at: str
    finished_at: str
    mode: str = "shadow"
    source_total: int = 0
    completed_sources: int = 0
    failed_sources: int = 0
    collected: int = 0
    valid: int = 0
    reviewed: int = 0
    ready: int = 0
    review_required: int = 0
    inserted: int = 0
    updated: int = 0
    alerted: int = 0
    email_sent: bool = False
    jobs: List[JobPosting] = field(default_factory=list)
    traces: List[AgentTrace] = field(default_factory=list)
    final_steps: List[AgentResult] = field(default_factory=list)
    source_errors: List[str] = field(default_factory=list)
    pipeline_errors: List[str] = field(default_factory=list)

    def to_dict(self, include_jobs: bool = True) -> Dict[str, Any]:
        result = {
            "run_id": self.run_id,
            "orchestrator": "OrchestratorAgent",
            "mode": self.mode,
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
                "inserted": self.inserted,
                "updated": self.updated,
                "alerted": self.alerted,
            },
            "email_sent": self.email_sent,
            "source_errors": list(self.source_errors),
            "pipeline_errors": list(self.pipeline_errors),
            "traces": [trace.to_dict() for trace in self.traces],
            "final_steps": [step.to_dict() for step in self.final_steps],
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
    """Coordinate specialist agents in shadow or production mode."""

    name = "OrchestratorAgent"

    def __init__(
        self,
        collection_agent: Optional[CollectionAgent] = None,
        eligibility_agent: Optional[EligibilityAgent] = None,
        review_agent: Optional[ReviewAgent] = None,
        storage_agent: Optional[StorageAgent] = None,
        notification_agent: Optional[NotificationAgent] = None,
    ):
        self.collection_agent = collection_agent or CollectionAgent()
        self.eligibility_agent = eligibility_agent or EligibilityAgent()
        self.review_agent = review_agent or ReviewAgent()
        self.storage_agent = storage_agent or StorageAgent()
        self.notification_agent = notification_agent or NotificationAgent()

    def run(
        self,
        profile: Dict[str, Any],
        sources: Iterable[Dict[str, Any]],
        include_demo: bool = False,
        source_ids: Optional[Iterable[str]] = None,
        database: Optional[str] = None,
        report_dir: Optional[str] = None,
        dry_run: bool = False,
    ) -> MultiAgentRunResult:
        started_at = utc_now_iso()
        production_requested = database is not None or report_dir is not None
        if production_requested and (not database or not report_dir):
            raise ValueError("正式 Agent 模式必须同时配置 database 和 report_dir")
        mode = "production" if production_requested else "shadow"
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
            run_id="agent-{}-{}".format(mode, uuid.uuid4().hex),
            status=AgentStatus.SUCCESS,
            started_at=started_at,
            finished_at=started_at,
            mode=mode,
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

        if production_requested:
            storage = self.storage_agent.run(result.jobs, database or "")
            result.final_steps.append(storage)
            if storage.status == AgentStatus.FAILED:
                result.pipeline_errors.append(
                    "{}: {}".format(storage.agent_name, storage.error)
                )
            else:
                result.inserted = int(storage.metadata.get("inserted_count", 0))
                result.updated = int(storage.metadata.get("updated_count", 0))
                notification = self.notification_agent.run(
                    storage.jobs,
                    profile,
                    report_dir or "",
                    dry_run=dry_run,
                )
                result.final_steps.append(notification)
                result.alerted = int(
                    notification.metadata.get("alerted_count", 0)
                )
                result.email_sent = bool(
                    notification.metadata.get("email_sent", False)
                )
                if notification.status == AgentStatus.FAILED:
                    result.pipeline_errors.append(
                        "{}: {}".format(notification.agent_name, notification.error)
                    )

        if result.pipeline_errors:
            result.status = AgentStatus.FAILED
        elif result.failed_sources and result.completed_sources == 0:
            result.status = AgentStatus.FAILED
        elif result.failed_sources or any(
            trace.status == AgentStatus.PARTIAL for trace in result.traces
        ) or any(
            step.status == AgentStatus.PARTIAL for step in result.final_steps
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
