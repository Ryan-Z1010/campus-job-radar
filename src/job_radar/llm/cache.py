from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..models import JobPosting, utc_now_iso
from .agents import public_job_payload, sanitize_profile


def _stable_hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LlmAnalysisCache:
    """SQLite cache keyed by job content, safe profile, model and prompts."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_analysis_cache (
                    cache_key TEXT PRIMARY KEY,
                    job_fingerprint TEXT NOT NULL,
                    job_content_hash TEXT NOT NULL,
                    profile_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def key_parts(
        job: JobPosting,
        profile: Mapping[str, Any],
        model: str,
        prompt_version: str,
    ) -> Dict[str, str]:
        job_content_hash = _stable_hash(public_job_payload(job))
        profile_hash = _stable_hash(sanitize_profile(profile))
        key_material = {
            "job_fingerprint": job.fingerprint,
            "job_content_hash": job_content_hash,
            "profile_hash": profile_hash,
            "model": model,
            "prompt_version": prompt_version,
        }
        return {
            **key_material,
            "cache_key": _stable_hash(key_material),
        }

    def get(
        self,
        job: JobPosting,
        profile: Mapping[str, Any],
        model: str,
        prompt_version: str,
    ) -> Optional[Dict[str, Any]]:
        parts = self.key_parts(job, profile, model, prompt_version)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json, created_at FROM llm_analysis_cache WHERE cache_key = ?",
                (parts["cache_key"],),
            ).fetchone()
        if row is None:
            return None
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(result, dict):
            return None
        result["cached"] = True
        result["cache_created_at"] = row["created_at"]
        return result

    def put(
        self,
        job: JobPosting,
        profile: Mapping[str, Any],
        model: str,
        prompt_version: str,
        result: Mapping[str, Any],
    ) -> None:
        parts = self.key_parts(job, profile, model, prompt_version)
        payload = dict(result)
        payload["cached"] = False
        payload.pop("cache_created_at", None)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO llm_analysis_cache (
                    cache_key, job_fingerprint, job_content_hash, profile_hash,
                    model, prompt_version, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parts["cache_key"],
                    parts["job_fingerprint"],
                    parts["job_content_hash"],
                    parts["profile_hash"],
                    model,
                    prompt_version,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    utc_now_iso(),
                ),
            )
