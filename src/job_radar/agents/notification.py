from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Iterable

from ..models import JobPosting, utc_now_iso
from ..notifications import send_email
from ..reporting import render_digest, write_reports
from .types import AgentEvidence, AgentResult, AgentStatus


class NotificationAgent:
    """Generate reports and notify only for new jobs above the score threshold."""

    name = "NotificationAgent"

    def run(
        self,
        jobs: Iterable[JobPosting],
        profile: Dict[str, Any],
        report_dir: str,
        dry_run: bool = False,
    ) -> AgentResult:
        task_id = "notification-{}".format(uuid.uuid4().hex[:8])
        started_at = utc_now_iso()
        job_list = list(jobs)
        threshold = int(profile.get("minimum_score", 0))
        alert_jobs = [job for job in job_list if job.score >= threshold]
        email_sent = False
        evidence = [
            AgentEvidence("首次出现岗位", "{} 个".format(len(job_list))),
            AgentEvidence("提醒阈值", str(threshold)),
            AgentEvidence("达到阈值", "{} 个".format(len(alert_jobs))),
            AgentEvidence("报告目录", Path(report_dir).name),
        ]

        try:
            write_reports(alert_jobs, report_dir)
            if alert_jobs and not dry_run:
                send_email(
                    "CampusJobRadar：发现 {} 个新岗位".format(len(alert_jobs)),
                    render_digest(alert_jobs),
                )
                email_sent = True
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                task_id=task_id,
                status=AgentStatus.FAILED,
                jobs=alert_jobs,
                evidence=evidence,
                next_action="检查报告目录或 SMTP 配置后重试通知步骤。",
                confidence=0.0,
                error="{}: {}".format(type(exc).__name__, exc),
                started_at=started_at,
                finished_at=utc_now_iso(),
                metadata={
                    "input_count": len(job_list),
                    "alerted_count": len(alert_jobs),
                    "email_sent": False,
                    "dry_run": dry_run,
                },
            )

        if email_sent:
            next_action = "邮件已发送，等待下一次定时监控。"
        elif dry_run:
            next_action = "报告已生成；dry-run 未发送邮件。"
        else:
            next_action = "本次没有达到阈值的新岗位，无需发送邮件。"
        return AgentResult(
            agent_name=self.name,
            task_id=task_id,
            status=AgentStatus.SUCCESS,
            jobs=alert_jobs,
            evidence=evidence,
            next_action=next_action,
            confidence=1.0,
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata={
                "input_count": len(job_list),
                "alerted_count": len(alert_jobs),
                "email_sent": email_sent,
                "dry_run": dry_run,
            },
        )
