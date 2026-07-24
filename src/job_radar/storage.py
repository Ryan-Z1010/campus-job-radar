from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Tuple

from .models import JobPosting, utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    fingerprint TEXT PRIMARY KEY,
    external_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    company_type TEXT,
    location TEXT,
    description TEXT,
    education TEXT,
    graduation_years TEXT,
    url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    published_at TEXT,
    deadline TEXT,
    collected_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_reasons TEXT,
    eligibility TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source_name);
"""


class JobStore:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(db_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def upsert(self, jobs: Iterable[JobPosting]) -> Tuple[List[JobPosting], int]:
        new_jobs = []
        updated = 0
        now = utc_now_iso()
        for job in jobs:
            exists = self.connection.execute(
                "SELECT 1 FROM jobs WHERE fingerprint = ?", (job.fingerprint,)
            ).fetchone()
            values = (
                job.fingerprint,
                job.external_id,
                job.title,
                job.company,
                job.company_type,
                job.location,
                job.description,
                job.education,
                json.dumps(job.graduation_years, ensure_ascii=False),
                job.url,
                job.source_name,
                job.published_at,
                job.deadline,
                job.collected_at,
                now,
                now,
                job.score,
                json.dumps(job.score_reasons, ensure_ascii=False),
                job.eligibility,
            )
            self.connection.execute(
                """
                INSERT INTO jobs (
                    fingerprint, external_id, title, company, company_type,
                    location, description, education, graduation_years, url,
                    source_name, published_at, deadline, collected_at,
                    first_seen_at, last_seen_at, score, score_reasons, eligibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    title=excluded.title,
                    company=excluded.company,
                    company_type=excluded.company_type,
                    location=excluded.location,
                    description=excluded.description,
                    education=excluded.education,
                    graduation_years=excluded.graduation_years,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    deadline=excluded.deadline,
                    collected_at=excluded.collected_at,
                    last_seen_at=excluded.last_seen_at,
                    score=excluded.score,
                    score_reasons=excluded.score_reasons,
                    eligibility=excluded.eligibility
                """,
                values,
            )
            if exists:
                updated += 1
            else:
                new_jobs.append(job)
        self.connection.commit()
        return new_jobs, updated

    def list_jobs(self, minimum_score: int = -9999) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM jobs WHERE score >= ? ORDER BY score DESC, first_seen_at DESC",
                (minimum_score,),
            )
        )
