from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterable

from ..models import JobPosting, utc_now_iso
from ..storage import JobStore
from .types import AgentEvidence, AgentResult, AgentStatus


class StorageAgent:
    """Persist reviewed jobs and return only jobs that are new to the store."""

    name = "StorageAgent"

    def run(self, jobs: Iterable[JobPosting], database: str) -> AgentResult:
        task_id = "storage-{}".format(uuid.uuid4().hex[:8])
        started_at = utc_now_iso()
        job_list = list(jobs)
        evidence = [
            AgentEvidence("待入库岗位", "{} 个".format(len(job_list))),
            AgentEvidence("去重数据库", Path(database).name),
        ]
        try:
            store = JobStore(database)
            try:
                new_jobs, updated = store.upsert(job_list)
            finally:
                store.close()
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                task_id=task_id,
                status=AgentStatus.FAILED,
                evidence=evidence,
                next_action="检查数据库路径、权限和 SQLite 文件状态。",
                confidence=0.0,
                error="{}: {}".format(type(exc).__name__, exc),
                started_at=started_at,
                finished_at=utc_now_iso(),
                metadata={
                    "input_count": len(job_list),
                    "inserted_count": 0,
                    "updated_count": 0,
                },
            )

        evidence.extend(
            [
                AgentEvidence("新增岗位", "{} 个".format(len(new_jobs))),
                AgentEvidence("更新岗位", "{} 个".format(updated)),
            ]
        )
        return AgentResult(
            agent_name=self.name,
            task_id=task_id,
            status=AgentStatus.SUCCESS,
            jobs=new_jobs,
            evidence=evidence,
            next_action="仅将首次出现的岗位交给 NotificationAgent。",
            confidence=1.0,
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata={
                "input_count": len(job_list),
                "inserted_count": len(new_jobs),
                "updated_count": updated,
            },
        )
