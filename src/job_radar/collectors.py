from __future__ import annotations

import json
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .models import JobPosting


USER_AGENT = "CampusJobRadar/0.1 (+https://github.com/your-name/campus-job-radar)"


def fetch_bytes(
    url: str,
    timeout: int = 20,
    method: str = "GET",
    json_body: Any = None,
    headers: Dict[str, str] = None,
) -> bytes:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    request_headers.update(headers or {})
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=data,
        headers=request_headers,
        method=method.upper(),
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


class Collector(ABC):
    def __init__(self, source: Dict[str, Any]):
        self.source = source

    @abstractmethod
    def collect(self) -> List[JobPosting]:
        raise NotImplementedError


class FixtureJsonCollector(Collector):
    def collect(self) -> List[JobPosting]:
        path = Path(self.source["path"])
        with path.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
        defaults = {
            "source_name": self.source.get("name", self.source["id"]),
            "company": self.source.get("company", ""),
            "company_type": self.source.get("company_type", "未知"),
            "location": self.source.get("location", ""),
        }
        return [JobPosting.from_mapping(item, defaults) for item in items]


def _walk_json(value: Any, path: str) -> Any:
    current = value
    for part in filter(None, path.split(".")):
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


class JsonApiCollector(Collector):
    def collect(self) -> List[JobPosting]:
        payload = json.loads(
            fetch_bytes(
                self.source["url"],
                method=self.source.get("method", "GET"),
                json_body=self.source.get("request_json"),
                headers=self.source.get("headers"),
            ).decode("utf-8")
        )
        items = _walk_json(payload, self.source.get("list_path", ""))
        if not isinstance(items, list):
            raise ValueError("JSON 来源的 list_path 没有指向数组")
        field_map = self.source.get("field_map", {})
        defaults = {
            "source_name": self.source.get("name", self.source["id"]),
            "company": self.source.get("company", ""),
            "company_type": self.source.get("company_type", "未知"),
            "location": self.source.get("location", ""),
        }
        jobs = []
        for item in items:
            mapped = {}
            for target, origin in field_map.items():
                try:
                    mapped[target] = _walk_json(item, origin)
                except (KeyError, IndexError, TypeError, ValueError):
                    mapped[target] = ""
            if self.source.get("url_template"):
                mapped["url"] = self.source["url_template"].format_map(
                    _MissingValueDict(item)
                )
            jobs.append(JobPosting.from_mapping(mapped, defaults))
        return jobs


class _MissingValueDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


class WebNoticeCollector(Collector):
    """Turn a verified public campaign page into a one-time recruitment notice."""

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        body = fetch_bytes(homepage).decode("utf-8", errors="replace")
        required_text = self.source.get("required_text", "")
        if required_text and required_text not in body:
            raise ValueError("页面未出现预期标识，可能已经改版")
        values = {
            "external_id": self.source.get("external_id", homepage),
            "title": self.source["title"],
            "company": self.source.get("company", self.source["name"]),
            "company_type": self.source.get("company_type", "未知"),
            "location": self.source.get("location", "待核对"),
            "description": self.source.get("description", ""),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self.source.get("published_at", ""),
            "deadline": self.source.get("deadline", ""),
            "url": homepage,
            "source_name": self.source["name"],
        }
        return [JobPosting.from_mapping(values)]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self._href = ""
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Iterable[Any]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href", "")
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append({"href": self._href, "text": " ".join(self._parts)})
            self._href = ""
            self._parts = []


class HtmlLinksCollector(Collector):
    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        body = fetch_bytes(homepage).decode("utf-8", errors="replace")
        parser = _LinkParser()
        parser.feed(body)
        include = self.source.get("include_keywords", [])
        exclude = self.source.get("exclude_keywords", [])
        jobs = []
        for link in parser.links:
            title = " ".join(link["text"].split())
            if not title:
                continue
            if include and not any(word.lower() in title.lower() for word in include):
                continue
            if any(word.lower() in title.lower() for word in exclude):
                continue
            jobs.append(
                JobPosting(
                    title=title,
                    company=self.source.get("company", self.source["name"]),
                    company_type=self.source.get("company_type", "未知"),
                    location=self.source.get("location", "待核对"),
                    url=urljoin(homepage, link["href"]),
                    source_name=self.source["name"],
                )
            )
        return jobs


COLLECTOR_TYPES = {
    "fixture_json": FixtureJsonCollector,
    "json_api": JsonApiCollector,
    "html_links": HtmlLinksCollector,
    "web_notice": WebNoticeCollector,
}


def build_collector(source: Dict[str, Any]) -> Collector:
    collector_type = source.get("type")
    try:
        collector_class = COLLECTOR_TYPES[collector_type]
    except KeyError as exc:
        raise ValueError("不支持的采集器类型: {}".format(collector_type)) from exc
    return collector_class(source)
