from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Iterable

from .models import JobPosting


def render_digest(jobs: Iterable[JobPosting]) -> str:
    items = []
    for job in sorted(jobs, key=lambda value: value.score, reverse=True):
        reasons = "；".join(job.score_reasons[:5]) or "待人工核对"
        items.append(
            """
            <article>
              <h2><a href="{url}">{title}</a></h2>
              <p><strong>{company}</strong> · {location} · {company_type}</p>
              <p>匹配分：<strong>{score}</strong> · 毕业时间：{eligibility}</p>
              <p>{reasons}</p>
              <p>截止：{deadline}</p>
            </article>
            """.format(
                url=html.escape(job.url, quote=True),
                title=html.escape(job.title),
                company=html.escape(job.company),
                location=html.escape(job.location or "待核对"),
                company_type=html.escape(job.company_type),
                score=job.score,
                eligibility=html.escape(job.eligibility),
                reasons=html.escape(reasons),
                deadline=html.escape(job.deadline or "待核对"),
            )
        )
    content = "\n".join(items) if items else "<p>本次没有达到提醒阈值的新岗位。</p>"
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CampusJobRadar 岗位摘要</title>
  <style>
    body {{ max-width: 760px; margin: 32px auto; padding: 0 20px;
            color: #1f2937; font: 16px/1.6 -apple-system, BlinkMacSystemFont, sans-serif; }}
    article {{ border: 1px solid #e5e7eb; border-radius: 12px;
               padding: 18px 22px; margin: 16px 0; }}
    h1 {{ color: #0f3d56; }} h2 {{ margin: 0 0 8px; font-size: 19px; }}
    a {{ color: #087ea4; }} p {{ margin: 6px 0; }}
  </style>
</head>
<body><h1>CampusJobRadar 新岗位摘要</h1>{}</body>
</html>""".format(content)


def write_reports(jobs: Iterable[JobPosting], directory: str) -> None:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    job_list = list(jobs)
    (output / "digest.html").write_text(render_digest(job_list), encoding="utf-8")
    (output / "jobs.json").write_text(
        json.dumps([job.to_dict() for job in job_list], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output / "jobs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "title",
            "company",
            "company_type",
            "location",
            "score",
            "eligibility",
            "published_at",
            "deadline",
            "url",
            "source_name",
            "score_reasons",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for job in sorted(job_list, key=lambda value: value.score, reverse=True):
            writer.writerow(
                {
                    "title": job.title,
                    "company": job.company,
                    "company_type": job.company_type,
                    "location": job.location,
                    "score": job.score,
                    "eligibility": job.eligibility,
                    "published_at": job.published_at,
                    "deadline": job.deadline,
                    "url": job.url,
                    "source_name": job.source_name,
                    "score_reasons": "；".join(job.score_reasons),
                }
            )
