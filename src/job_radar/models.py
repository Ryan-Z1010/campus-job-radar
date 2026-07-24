from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class JobPosting:
    title: str
    company: str
    location: str
    url: str
    source_name: str
    company_type: str = "未知"
    description: str = ""
    education: str = ""
    graduation_years: List[int] = field(default_factory=list)
    external_id: str = ""
    published_at: str = ""
    deadline: str = ""
    collected_at: str = field(default_factory=utc_now_iso)
    score: int = 0
    score_reasons: List[str] = field(default_factory=list)
    eligibility: str = "待核对"

    @property
    def fingerprint(self) -> str:
        if self.external_id:
            identity = [self.source_name, self.external_id]
        else:
            identity = [
                self.company.strip().lower(),
                self.title.strip().lower(),
                self.location.strip().lower(),
                self.url.strip().lower(),
            ]
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_mapping(
        cls,
        item: Dict[str, Any],
        defaults: Optional[Dict[str, Any]] = None,
    ) -> "JobPosting":
        values = dict(defaults or {})
        values.update({key: value for key, value in item.items() if value is not None})
        years = values.get("graduation_years", [])
        if isinstance(years, (str, int)):
            years = [int(years)]
        return cls(
            title=str(values.get("title", "")).strip(),
            company=str(values.get("company", "")).strip(),
            location=str(values.get("location", "")).strip(),
            url=str(values.get("url", "")).strip(),
            source_name=str(values.get("source_name", "")).strip(),
            company_type=str(values.get("company_type", "未知")).strip(),
            description=str(values.get("description", "")).strip(),
            education=str(values.get("education", "")).strip(),
            graduation_years=[int(year) for year in years if str(year).isdigit()],
            external_id=str(values.get("external_id", "")).strip(),
            published_at=str(values.get("published_at", "")).strip(),
            deadline=str(values.get("deadline", "")).strip(),
        )
