from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List

from ..models import JobPosting, utc_now_iso


class AgentStatus(str, Enum):
    """Outcome of one bounded agent task."""

    SUCCESS = "success"
    NEEDS_REVIEW = "needs_review"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class AgentEvidence:
    """A compact fact that explains how an agent reached its result."""

    label: str
    value: str
    locator: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class AgentResult:
    """Shared contract used by every specialist agent."""

    agent_name: str
    task_id: str
    status: AgentStatus
    jobs: List[JobPosting] = field(default_factory=list)
    evidence: List[AgentEvidence] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    next_action: str = ""
    confidence: float = 1.0
    error: str = ""
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize a trace step without repeating complete job payloads."""

        return {
            "agent_name": self.agent_name,
            "task_id": self.task_id,
            "status": self.status.value,
            "job_count": len(self.jobs),
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": list(self.warnings),
            "next_action": self.next_action,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class AgentTrace:
    """All specialist decisions made for one recruitment source."""

    source_id: str
    source_name: str
    status: AgentStatus
    steps: List[AgentResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "status": self.status.value,
            "steps": [step.to_dict() for step in self.steps],
        }
