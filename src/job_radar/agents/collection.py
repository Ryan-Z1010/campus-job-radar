from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict

from ..collectors import build_collector
from ..models import utc_now_iso
from .types import AgentEvidence, AgentResult, AgentStatus


def _source_locator(source: Dict[str, Any]) -> str:
    for key in ("homepage", "url", "campaign_url"):
        value = str(source.get(key, "") or "").strip()
        if value:
            return value
    path = str(source.get("path", "") or "").strip()
    if path:
        return Path(path).name
    return ""


class CollectionAgent:
    """Run exactly one configured collector and report failures as data."""

    name = "CollectionAgent"

    def run(self, source: Dict[str, Any]) -> AgentResult:
        task_id = "collect-{}-{}".format(
            source.get("id", "unknown"), uuid.uuid4().hex[:8]
        )
        started_at = utc_now_iso()
        locator = _source_locator(source)
        evidence = [
            AgentEvidence(
                "采集器类型",
                str(source.get("type", "未配置")),
                locator=locator,
            )
        ]
        try:
            jobs = build_collector(source).collect()
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                task_id=task_id,
                status=AgentStatus.FAILED,
                evidence=evidence,
                next_action="检查该来源的公开页面、接口或采集器配置。",
                confidence=0.0,
                error="{}: {}".format(type(exc).__name__, exc),
                started_at=started_at,
                finished_at=utc_now_iso(),
                metadata={
                    "source_id": source.get("id", ""),
                    "source_name": source.get("name", ""),
                },
            )

        evidence.append(AgentEvidence("采集结果", "{} 个原始岗位".format(len(jobs))))
        return AgentResult(
            agent_name=self.name,
            task_id=task_id,
            status=AgentStatus.SUCCESS,
            jobs=jobs,
            evidence=evidence,
            next_action="交给 EligibilityAgent 统一字段并判断求职资格。",
            confidence=1.0,
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata={
                "source_id": source.get("id", ""),
                "source_name": source.get("name", ""),
            },
        )
