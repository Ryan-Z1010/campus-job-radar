from __future__ import annotations

import json
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from .models import JobPosting


USER_AGENT = "CampusJobRadar/0.1 (+https://github.com/Ryan-Z1010/campus-job-radar)"


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


class ChinaSouthernPowerGridCollector(Collector):
    """Collect public vacancies through CSG's anonymous website session."""

    DEFAULT_GUEST_TOKEN_URL = (
        "https://zhaopin.csg.cn/hrcommonauthentication/service/"
        "authLoginParam/guest/getGuestToken"
    )
    DEFAULT_SEARCH_URL = (
        "https://zhaopin.csg.cn/recruitment-dmz/service/webPost/search"
    )
    DEFAULT_REQUEST = {
        "pageNo": 1,
        "pageSize": 100,
        "keyword": "",
        "orgId": "",
        "postLocation": "",
        "educationReq": "",
        "postType": "",
        "professionId": "",
        "postName": "",
        "activityId": "",
    }

    @staticmethod
    def _decode_response(raw: bytes, label: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("南方电网{}返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict):
            raise ValueError("南方电网{}响应结构异常".format(label))
        if payload.get("code") != 200:
            raise ValueError(
                "南方电网{}失败（code={}）".format(label, payload.get("code"))
            )
        return payload

    @staticmethod
    def _first_value(item: Dict[str, Any], *fields: str) -> Any:
        for field in fields:
            value = item.get(field)
            if value not in (None, ""):
                return value
        return ""

    def _guest_token(self) -> str:
        raw = fetch_bytes(
            self.source.get("guest_token_url", self.DEFAULT_GUEST_TOKEN_URL),
            method="POST",
            json_body={},
            headers=None,
        )
        payload = self._decode_response(raw, "匿名会话")
        data = payload.get("data")
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError("南方电网匿名会话响应缺少访问令牌")
        return token

    def _search_page(
        self, token: str, request_json: Dict[str, Any], page_no: int
    ) -> Dict[str, Any]:
        body = dict(request_json)
        body["pageNo"] = page_no
        raw = fetch_bytes(
            self.source.get("url", self.DEFAULT_SEARCH_URL),
            method="POST",
            json_body=body,
            headers={"Authorization": "Bearer {}".format(token)},
        )
        payload = self._decode_response(raw, "岗位搜索")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise ValueError("南方电网岗位搜索响应缺少 data.list 数组")
        return data

    def _to_job(self, item: Dict[str, Any]) -> JobPosting:
        external_id = self._first_value(item, "id", "postId")
        homepage = self.source.get("homepage", "https://zhaopin.csg.cn/")
        detail_url = "{}/#/post-list-detail?gobackUrl=/job-list&postId={}&canback=no".format(
            homepage.rstrip("/"),
            quote(str(external_id), safe=""),
        )
        values = {
            "external_id": external_id,
            "title": self._first_value(item, "postName", "title"),
            "company": self._first_value(item, "orgName", "companyName"),
            "company_type": self.source.get("company_type", "央企"),
            "location": self._first_value(
                item, "postLocationName", "workLocationName", "location"
            ),
            "description": self._first_value(
                item, "postTypeName", "professionName", "description"
            ),
            "education": self._first_value(
                item, "educationRequireName", "educationName"
            ),
            "published_at": self._first_value(
                item, "publishTime", "releaseTime", "createTime"
            ),
            "deadline": self._first_value(
                item, "deliverDeadLineTime", "deadline"
            ),
            "url": detail_url,
            "source_name": self.source.get("name", self.source["id"]),
        }
        if not values["company"]:
            values["company"] = self.source.get("company", "中国南方电网")
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        token = self._guest_token()
        request_json = dict(self.DEFAULT_REQUEST)
        request_json.update(self.source.get("request_json", {}))
        page_size = max(1, int(request_json.get("pageSize", 100)))
        request_json["pageSize"] = page_size
        max_pages = max(1, int(self.source.get("max_pages", 20)))

        items: List[Dict[str, Any]] = []
        for page_no in range(1, max_pages + 1):
            data = self._search_page(token, request_json, page_no)
            page_items = data["list"]
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("南方电网岗位搜索列表元素结构异常")
            items.extend(page_items)
            try:
                total = int(data.get("count", len(items)))
            except (TypeError, ValueError):
                total = len(items)
            if not page_items or len(items) >= total or len(page_items) < page_size:
                break

        return [self._to_job(item) for item in items]


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
            "url": self.source.get("url", homepage),
            "source_name": self.source["name"],
        }
        return [JobPosting.from_mapping(values)]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self.text_parts: List[str] = []
        self._href = ""
        self._parts: List[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: Iterable[Any]) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth += 1
            return
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href", "")
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text_parts.append(data)
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if tag.lower() == "a" and self._href:
            self.links.append({"href": self._href, "text": " ".join(self._parts)})
            self._href = ""
            self._parts = []


class CampaignWatchCollector(Collector):
    """Emit one notice when an official page announces a target campaign."""

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        body = fetch_bytes(homepage).decode("utf-8", errors="replace")
        parser = _LinkParser()
        parser.feed(body)
        visible_text = " ".join(" ".join(parser.text_parts).split())

        required_text = self.source.get("required_text", "")
        if required_text and required_text not in visible_text:
            raise ValueError("活动监控页未出现预期标识，可能已经改版")

        target_keywords = self.source.get("target_keywords", [])
        compacted_text = "".join(visible_text.lower().split())
        matched_keyword = next(
            (
                keyword
                for keyword in target_keywords
                if "".join(keyword.lower().split()) in compacted_text
            ),
            "",
        )
        if not matched_keyword:
            return []

        link_keywords = list(self.source.get("link_keywords", []))
        link_keywords.append(matched_keyword)
        campaign_url = homepage
        for link in parser.links:
            candidate_text = " ".join(link["text"].split())
            searchable = "{} {}".format(candidate_text, link["href"]).lower()
            if any(keyword.lower() in searchable for keyword in link_keywords):
                campaign_url = urljoin(homepage, link["href"])
                break

        values = {
            "external_id": self.source.get(
                "external_id",
                "{}:{}".format(self.source["id"], matched_keyword),
            ),
            "title": self.source["title"],
            "company": self.source.get("company", self.source["name"]),
            "company_type": self.source.get("company_type", "未知"),
            "location": self.source.get("location", "待核对"),
            "description": self.source.get("description", ""),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self.source.get("published_at", ""),
            "deadline": self.source.get("deadline", ""),
            "url": campaign_url,
            "source_name": self.source["name"],
        }
        return [JobPosting.from_mapping(values)]


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
    "campaign_watch": CampaignWatchCollector,
    "csg_api": ChinaSouthernPowerGridCollector,
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
