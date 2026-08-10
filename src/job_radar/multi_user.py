"""Shared-source, per-user multi-agent monitoring.

The company source pool is collected once per run.  Each user then receives an
isolated eligibility, scoring, storage and notification pass using their own
profile and output directories.  This is the local multi-user foundation; a
future web service can replace the JSON user registry without changing the
agent boundaries.
"""

from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .agents.collection import CollectionAgent
from .agents.eligibility import EligibilityAgent
from .agents.notification import NotificationAgent
from .agents.review import ReviewAgent
from .agents.storage import StorageAgent
from .agents.types import AgentResult, AgentStatus, AgentTrace
from .config import ConfigError, load_json, load_profile
from .models import JobPosting, utc_now_iso


_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class UserSpec:
    """Private per-user settings kept outside the public company pool."""

    user_id: str
    profile_path: str
    email: str
    database: str
    report_dir: str
    trace_file: str


@dataclass
class UserRunResult:
    user_id: str
    email: str
    status: AgentStatus
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
    source_errors: List[str] = field(default_factory=list)
    pipeline_errors: List[str] = field(default_factory=list)
    traces: List[AgentTrace] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "status": self.status.value,
            "counts": {
                key: value
                for key, value in asdict(self).items()
                if key
                in {
                    "source_total",
                    "completed_sources",
                    "failed_sources",
                    "collected",
                    "valid",
                    "reviewed",
                    "ready",
                    "review_required",
                    "inserted",
                    "updated",
                    "alerted",
                }
            },
            "email_sent": self.email_sent,
            "source_errors": list(self.source_errors),
            "pipeline_errors": list(self.pipeline_errors),
            "traces": [trace.to_dict() for trace in self.traces],
        }


@dataclass
class MultiUserRunResult:
    run_id: str
    started_at: str
    finished_at: str
    source_total: int
    collected: int
    source_errors: List[str] = field(default_factory=list)
    users: List[UserRunResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "orchestrator": "MultiUserOrchestrator",
            "mode": "multi-user-local",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "shared_collection": {
                "source_total": self.source_total,
                "collected": self.collected,
                "source_errors": list(self.source_errors),
            },
            "users": [user.to_dict() for user in self.users],
        }


def _repo_root(config_path: Path) -> Path:
    """Resolve paths in a config relative to the repository root."""

    if config_path.parent.name == "configs":
        return config_path.parent.parent
    return config_path.parent


def _resolve_path(value: str, root: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return str(path)


def load_users(path: str) -> List[UserSpec]:
    """Load and validate a private users registry.

    The file is intentionally separate from ``sources.json``.  It should be
    ignored by Git and may contain recipient addresses and profile paths.
    """

    config_path = Path(path).resolve()
    data = load_json(str(config_path))
    raw_users = data.get("users")
    if not isinstance(raw_users, list) or not raw_users:
        raise ConfigError("多人配置必须包含非空 users 数组: {}".format(config_path))

    root = _repo_root(config_path)
    seen = set()
    users: List[UserSpec] = []
    for raw in raw_users:
        if not isinstance(raw, dict):
            raise ConfigError("users 的每项必须是对象: {}".format(config_path))
        user_id = str(raw.get("id", "")).strip()
        if not _USER_ID_RE.match(user_id):
            raise ConfigError("用户 id 只能包含字母、数字、点、下划线和短横线: {}".format(user_id))
        if user_id in seen:
            raise ConfigError("多人配置存在重复用户 id: {}".format(user_id))
        seen.add(user_id)
        profile_path = str(raw.get("profile", raw.get("profile_path", ""))).strip()
        email = str(raw.get("email", "")).strip()
        if not profile_path:
            raise ConfigError("用户 {} 缺少 profile 路径".format(user_id))
        if not email or "@" not in email:
            raise ConfigError("用户 {} 缺少有效 email".format(user_id))
        users.append(
            UserSpec(
                user_id=user_id,
                profile_path=_resolve_path(profile_path, root),
                email=email,
                database=_resolve_path(
                    str(raw.get("database", "data/users/{}/job_radar.db".format(user_id))),
                    root,
                ),
                report_dir=_resolve_path(
                    str(raw.get("report_dir", "reports/users/{}".format(user_id))),
                    root,
                ),
                trace_file=_resolve_path(
                    str(raw.get("trace_file", "reports/users/{}/agent-trace.json".format(user_id))),
                    root,
                ),
            )
        )
    return users


def _clone_job(job: JobPosting) -> JobPosting:
    """Avoid profile-specific scoring mutating the shared collection snapshot."""

    return JobPosting.from_mapping(job.to_dict())


def _write_user_trace(result: UserRunResult, path: str) -> None:
    trace_path = Path(path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class MultiUserOrchestrator:
    """Collect once, then fan out the deterministic agents per user."""

    name = "MultiUserOrchestrator"

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
        errors = []
        for source, collection in pairs:
            if collection.status == AgentStatus.FAILED:
                errors.append(
                    "{}: {}".format(
                        source.get("name", source.get("id", "")), collection.error
                    )
                )
        return pairs, errors

    def _run_user(
        self,
        user: UserSpec,
        pairs: Sequence[Tuple[Dict[str, Any], AgentResult]],
        shared_errors: Sequence[str],
        dry_run: bool,
    ) -> UserRunResult:
        profile = load_profile(user.profile_path)
        result = UserRunResult(
            user_id=user.user_id,
            email=user.email,
            status=AgentStatus.SUCCESS,
            source_total=len(pairs),
            source_errors=list(shared_errors),
        )
        reviewed_jobs: List[JobPosting] = []
        for source, collection in pairs:
            source_id = str(source.get("id", ""))
            source_name = str(source.get("name", source_id))
            result.collected += len(collection.jobs)
            steps = [collection]
            if collection.status == AgentStatus.FAILED:
                result.failed_sources += 1
                result.traces.append(
                    AgentTrace(source_id, source_name, AgentStatus.FAILED, steps)
                )
                continue

            eligibility = self.eligibility_agent.run(
                [_clone_job(job) for job in collection.jobs],
                profile,
                source_id=source_id,
            )
            result.valid += len(eligibility.jobs)
            steps.append(eligibility)
            review = self.review_agent.run(eligibility.jobs, source_id=source_id)
            result.reviewed += len(review.jobs)
            result.review_required += int(review.metadata.get("review_required_count", 0))
            result.ready += int(review.metadata.get("ready_count", 0))
            reviewed_jobs.extend(review.jobs)
            result.completed_sources += 1
            result.traces.append(
                AgentTrace(source_id, source_name, _trace_status(steps), steps)
            )

        try:
            storage = self.storage_agent.run(reviewed_jobs, user.database)
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
                    user.report_dir,
                    dry_run=dry_run,
                    recipient=user.email,
                )
                result.alerted = int(notification.metadata.get("alerted_count", 0))
                result.email_sent = bool(notification.metadata.get("email_sent", False))
                if notification.status == AgentStatus.FAILED:
                    result.pipeline_errors.append(
                        "{}: {}".format(notification.agent_name, notification.error)
                    )
        except Exception as exc:
            result.pipeline_errors.append("{}: {}".format(type(exc).__name__, exc))

        if result.pipeline_errors:
            result.status = AgentStatus.FAILED
        elif result.failed_sources and result.completed_sources == 0:
            result.status = AgentStatus.FAILED
        elif result.failed_sources or result.review_required:
            result.status = AgentStatus.PARTIAL if result.failed_sources else AgentStatus.NEEDS_REVIEW
        return result

    def run(
        self,
        users: Sequence[UserSpec],
        sources: Sequence[Dict[str, Any]],
        include_demo: bool = False,
        source_ids: Optional[Iterable[str]] = None,
        collection_workers: int = 4,
        dry_run: bool = False,
    ) -> MultiUserRunResult:
        if not users:
            raise ValueError("至少需要一个用户配置")
        started_at = utc_now_iso()
        pairs, errors = self._collect(
            sources, include_demo, source_ids, collection_workers
        )
        users_result = []
        for user in users:
            user_result = self._run_user(user, pairs, errors, dry_run)
            _write_user_trace(user_result, user.trace_file)
            users_result.append(user_result)
        return MultiUserRunResult(
            run_id="multi-user-{}".format(uuid.uuid4().hex),
            started_at=started_at,
            finished_at=utc_now_iso(),
            source_total=len(pairs),
            collected=sum(len(collection.jobs) for _, collection in pairs),
            source_errors=errors,
            users=users_result,
        )


def write_multi_user_trace(result: MultiUserRunResult, path: str) -> Path:
    trace_path = Path(path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return trace_path


def _trace_status(steps: Sequence[AgentResult]) -> AgentStatus:
    statuses = {step.status for step in steps}
    if AgentStatus.FAILED in statuses:
        return AgentStatus.FAILED
    if AgentStatus.PARTIAL in statuses:
        return AgentStatus.PARTIAL
    if AgentStatus.NEEDS_REVIEW in statuses:
        return AgentStatus.NEEDS_REVIEW
    return AgentStatus.SUCCESS
