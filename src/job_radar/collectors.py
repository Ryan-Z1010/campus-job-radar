from __future__ import annotations

import base64
import binascii
import calendar
import hashlib
import json
import re
import secrets
import threading
import time
import zlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
    urlopen,
)

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

from .models import JobPosting
from .network import urlopen_with_retry


USER_AGENT = "CampusJobRadar/0.1 (+https://github.com/Ryan-Z1010/campus-job-radar)"


# A large part of the expanded company pool intentionally points at shared
# official catalog pages (for example a central SASAC or municipal catalog).
# Keep one in-memory response per GET URL/header set during a run so duplicate
# sources do not make hundreds of identical network requests.  The event map
# also coalesces concurrent requests for the same key.
_GET_CACHE: Dict[Any, bytes] = {}
_GET_INFLIGHT: Dict[Any, threading.Event] = {}
_GET_CACHE_LOCK = threading.Lock()


def fetch_bytes(
    url: str,
    timeout: int = 20,
    method: str = "GET",
    json_body: Any = None,
    form_body: Dict[str, Any] = None,
    headers: Dict[str, str] = None,
) -> bytes:
    cacheable = (
        method.upper() == "GET"
        and json_body is None
        and form_body is None
    )
    cache_key = None
    if cacheable:
        cache_key = (
            url,
            tuple(
                sorted(
                    (str(key).lower(), str(value))
                    for key, value in (headers or {}).items()
                )
            ),
        )
        while True:
            with _GET_CACHE_LOCK:
                cached = _GET_CACHE.get(cache_key)
                if cached is not None:
                    return cached
                inflight = _GET_INFLIGHT.get(cache_key)
                if inflight is None:
                    _GET_INFLIGHT[cache_key] = threading.Event()
                    break
            # Another worker is fetching this exact page. Wait for it to
            # publish a result, then re-check the cache (or become the owner
            # if the previous request failed).
            inflight.wait()

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    request_headers.update(headers or {})
    if json_body is not None and form_body is not None:
        raise ValueError("请求不能同时使用 JSON 和表单正文")
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urlencode(form_body).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(
        url,
        data=data,
        headers=request_headers,
        method=method.upper(),
    )
    try:
        with urlopen_with_retry(
            request, timeout=timeout, opener=urlopen
        ) as response:
            body = response.read()
    except Exception:
        if cacheable and cache_key is not None:
            with _GET_CACHE_LOCK:
                event = _GET_INFLIGHT.pop(cache_key, None)
                if event is not None:
                    event.set()
        raise

    if cacheable and cache_key is not None:
        with _GET_CACHE_LOCK:
            _GET_CACHE[cache_key] = body
            event = _GET_INFLIGHT.pop(cache_key, None)
            if event is not None:
                event.set()
    return body


class Collector(ABC):
    def __init__(self, source: Dict[str, Any]):
        self.source = source

    @abstractmethod
    def collect(self) -> List[JobPosting]:
        raise NotImplementedError


class MeituanCampusCollector(Collector):
    """Collect current full-time campus roles from Meituan's official API.

    The public Meituan recruitment page is a client-rendered application.  A
    plain HTML campaign watcher therefore only sees the page shell and misses
    the 2026 autumn/2027 campus positions that are visible in the browser.
    This collector uses the same read-only JSON endpoint as that official
    page, while restricting the request to the "应届校招" job type (code 1).
    """

    DEFAULT_API_URL = (
        "https://zhaopin.meituan.com/api/official/job/getJobList"
    )
    DEFAULT_REFERER = "https://zhaopin.meituan.com/web/campus"

    @staticmethod
    def _decode_page(raw: bytes) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("美团校园招聘接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or str(payload.get("status")) != "1":
            raise ValueError("美团校园招聘接口返回失败状态")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("美团校园招聘接口缺少 data")
        items = data.get("list")
        page = data.get("page")
        if not isinstance(items, list) or not isinstance(page, dict):
            raise ValueError("美团校园招聘接口缺少岗位列表或分页信息")
        return {"items": items, "page": page}

    def _request_page(self, page_no: int) -> Dict[str, Any]:
        page_size = max(1, int(self.source.get("page_size", 100)))
        request_json = {
            "page": {"pageNo": page_no, "pageSize": page_size},
            "jobShareType": self.source.get("job_share_type", "1"),
            "keywords": "",
            "cityList": [],
            "department": [],
            "jfJgList": [],
            "jobType": self.source.get(
                "job_type", [{"code": "1", "subCode": []}]
            ),
            "typeCode": [],
            "specialCode": [],
        }
        overrides = self.source.get("request_json", {})
        if isinstance(overrides, dict):
            request_json.update(overrides)
        request_json["page"] = {
            "pageNo": page_no,
            "pageSize": page_size,
        }
        try:
            raw = fetch_bytes(
                self.source.get("api_url", self.DEFAULT_API_URL),
                timeout=int(self.source.get("timeout", 20)),
                method="POST",
                json_body=request_json,
                headers={
                    "Accept": "application/json;charset=utf-8",
                    "Origin": "https://zhaopin.meituan.com",
                    "Referer": self.source.get(
                        "referer", self.DEFAULT_REFERER
                    ),
                },
            )
        except Exception as exc:
            raise ValueError("美团校园招聘接口请求失败: {}".format(exc)) from exc
        return self._decode_page(raw)

    @staticmethod
    def _date_from_epoch(value: Any) -> str:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return ""
        if timestamp <= 0:
            return ""
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).date().isoformat()

    @staticmethod
    def _names(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        names = []
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name and name not in names:
                names.append(name)
        return "、".join(names)

    def collect(self) -> List[JobPosting]:
        max_pages = max(1, int(self.source.get("max_pages", 10)))
        jobs: List[JobPosting] = []
        seen_ids = set()
        expected_total = None
        total_pages = None

        for page_no in range(1, max_pages + 1):
            page_data = self._request_page(page_no)
            items = page_data["items"]
            page = page_data["page"]
            try:
                current_page = int(page["pageNo"])
                current_total = int(page["totalCount"])
                current_total_pages = int(page["totalPage"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("美团校园招聘接口分页字段异常") from exc
            if current_page != page_no:
                raise ValueError("美团校园招聘接口分页页码异常")
            if expected_total is None:
                expected_total = current_total
                total_pages = max(1, current_total_pages)
                if total_pages > max_pages:
                    raise ValueError("美团校园招聘接口分页超过安全上限")
            elif current_total != expected_total or current_total_pages != total_pages:
                raise ValueError("美团校园招聘接口分页总数发生变化")

            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("美团校园招聘岗位元素结构异常")
                external_id = str(item.get("jobUnionId") or "").strip()
                title = str(item.get("name") or "").strip()
                if not external_id or not title:
                    raise ValueError("美团校园招聘岗位缺少 ID 或标题")
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)

                location = self._names(item.get("cityList")) or self.source.get(
                    "location", "待核对"
                )
                department = self._names(item.get("department"))
                description_parts = [
                    str(item.get("projectName") or "").strip(),
                    str(item.get("jobFamily") or "").strip(),
                    department,
                    str(item.get("jobDuty") or "").strip(),
                    str(item.get("jobRequirement") or "").strip(),
                    str(item.get("highLight") or "").strip(),
                ]
                description = "\n".join(
                    part for part in description_parts if part
                )
                values = {
                    "external_id": external_id,
                    "title": title,
                    "company": self.source.get("company", "美团"),
                    "company_type": self.source.get("company_type", "私企"),
                    "location": location,
                    "description": description,
                    "education": self.source.get(
                        "education", "美团应届生校园招聘岗位，具体要求以官方岗位详情为准"
                    ),
                    "graduation_years": self.source.get(
                        "graduation_years", [2027]
                    ),
                    "published_at": self._date_from_epoch(
                        item.get("refreshTime") or item.get("firstPostTime")
                    ),
                    "deadline": self._date_from_epoch(item.get("expiredTime")),
                    "url": self.source.get(
                        "url_template",
                        self.DEFAULT_REFERER.replace(
                            "/web/campus", "/web/position/detail"
                        )
                        + "?jobUnionId={jobUnionId}&jobShareType=1",
                    ).format_map(_MissingValueDict(item)),
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))

            if page_no >= total_pages:
                break
        else:
            raise ValueError("美团校园招聘接口分页超过配置上限")
        return jobs


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


def _html_fragment_text(value: Any) -> str:
    parser = _LinkParser()
    parser.feed(str(value or ""))
    return " ".join(" ".join(parser.text_parts).split())


class NoticeJsonCollector(Collector):
    """Collect matching campaign notices from a public JSON announcement feed."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _location(self, text: str) -> str:
        for keyword, location in self.source.get("location_map", {}).items():
            if str(keyword).lower() in text.lower():
                return str(location)
        return self.source.get("location", "待核对")

    def _company(self, value: Any) -> str:
        company = _html_fragment_text(value)
        if not company:
            return self.source.get("company", self.source["name"])
        prefix = self.source.get("company_prefix", "")
        if prefix and not company.startswith(prefix):
            return "{}{}".format(prefix, company)
        return company

    def collect(self) -> List[JobPosting]:
        try:
            payload = json.loads(fetch_bytes(self.source["url"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("公告 JSON 来源返回了无效 JSON") from exc

        try:
            items = _walk_json(payload, self.source.get("list_path", ""))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("公告 JSON 来源的 list_path 不存在") from exc
        if not isinstance(items, list):
            raise ValueError("公告 JSON 来源的 list_path 没有指向数组")

        target_keywords = self.source.get("target_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        homepage = self.source.get("homepage", self.source["url"])
        jobs = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("公告 JSON 来源的列表元素结构异常")
            title = _html_fragment_text(item.get("text3", ""))
            if not title or (
                target_keywords and not self._contains(title, target_keywords)
            ):
                continue
            if self._contains(title, exclude_keywords):
                continue

            detail_href = item.get("detail_href") or item.get("jump_link") or homepage
            company = self._company(item.get("text1"))
            searchable = "{} {}".format(company, title)
            values = {
                "external_id": item.get("_orderId") or detail_href,
                "title": title,
                "company": company,
                "company_type": self.source.get("company_type", "未知"),
                "location": self._location(searchable),
                "description": self.source.get("description", ""),
                "education": self.source.get("education", ""),
                "graduation_years": self.source.get("graduation_years", []),
                "published_at": item.get("text4", ""),
                "deadline": item.get("text5", ""),
                "url": urljoin(homepage, str(detail_href)),
                "source_name": self.source.get("name", self.source["id"]),
            }
            jobs.append(JobPosting.from_mapping(values))
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


class ZhaopinCampusCompanyCollector(Collector):
    """Collect campus jobs exposed in a public Zhaopin company page."""

    INITIAL_DATA_MARKER = "window.__INITIAL_DATA__"

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _deadline(value: Any) -> str:
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return ""
        if milliseconds <= 0:
            return ""
        china_time = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(
            milliseconds / 1000, tz=china_time
        ).strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def _initial_data(cls, body: str) -> Dict[str, Any]:
        marker_at = body.find(cls.INITIAL_DATA_MARKER)
        if marker_at < 0:
            raise ValueError("智联校园公司页缺少公开初始数据，可能已经改版")
        script_end = body.find("</script>", marker_at)
        if script_end < 0:
            raise ValueError("智联校园公司页初始数据标签不完整")
        assignment = body[marker_at:script_end]
        if "=" not in assignment:
            raise ValueError("智联校园公司页初始数据格式异常")
        raw = assignment.split("=", 1)[1].strip().rstrip(";")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("智联校园公司页返回了无效初始数据") from exc
        if not isinstance(payload, dict):
            raise ValueError("智联校园公司页初始数据结构异常")
        return payload

    @staticmethod
    def _location(item: Dict[str, Any]) -> str:
        try:
            address = _walk_json(
                item, "jobDetailData.position.workLocation.workAddress"
            )
        except (KeyError, IndexError, TypeError, ValueError):
            address = ""
        if address:
            return str(address).replace("、", "/")
        return "/".join(
            str(value)
            for value in (item.get("workCity"), item.get("cityDistrict"))
            if value
        )

    def _is_target_cycle(self, item: Dict[str, Any]) -> bool:
        cutoff = str(self.source.get("min_first_published_at", "")).strip()
        if not cutoff:
            return True
        first_published = str(item.get("firstPublishTime", "")).strip()
        return bool(first_published) and first_published[:10] >= cutoff[:10]

    def _to_job(self, item: Dict[str, Any]) -> JobPosting:
        deadline = ""
        campus_detail = item.get("campusJobDetail")
        if isinstance(campus_detail, dict):
            deadline = self._deadline(campus_detail.get("applyEndTime"))
        values = {
            "external_id": item.get("number") or item.get("jobId"),
            "title": item.get("name", ""),
            "company": self.source.get("company", item.get("companyName", "")),
            "company_type": self.source.get("company_type", "未知"),
            "location": self._location(item),
            "description": item.get("jobSummary", ""),
            "education": item.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": item.get("firstPublishTime", ""),
            "deadline": deadline,
            "url": self.source["homepage"],
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        body = fetch_bytes(self.source["homepage"]).decode(
            "utf-8", errors="replace"
        )
        payload = self._initial_data(body)
        try:
            state = _walk_json(payload, "company.recruitingPositionsState")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("智联校园公司页缺少招聘岗位数据") from exc
        if not isinstance(state, dict) or not isinstance(state.get("list"), list):
            raise ValueError("智联校园公司页招聘岗位结构异常")
        items = state["list"]
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("智联校园公司页岗位列表元素结构异常")
        try:
            total = int(state.get("count", len(items)))
        except (TypeError, ValueError):
            total = len(items)
        if total > len(items):
            raise ValueError("智联校园公司页只返回了部分岗位，需要升级分页采集")

        expected_company_number = self.source.get("company_number", "")
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        work_types = set(self.source.get("work_types", ["校园"]))
        jobs = []
        for item in items:
            item_company_number = item.get("companyNumber", "")
            if (
                expected_company_number
                and item_company_number != expected_company_number
            ):
                raise ValueError("智联校园公司页返回了非目标公司的岗位")
            if work_types and item.get("workType") not in work_types:
                continue
            if not self._is_target_cycle(item):
                continue
            searchable = " ".join(
                str(item.get(field, ""))
                for field in ("name", "jobSummary", "subJobTypeLevelName")
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(self._to_job(item))
        return jobs


class GzRecruitCompanyCollector(Collector):
    """Collect campus jobs from a public company page on 才聚羊城."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _china_datetime(value: Any) -> str:
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return ""
        if milliseconds <= 0:
            return ""
        china_time = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(
            milliseconds / 1000, tz=china_time
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _is_target_cycle(self, item: Dict[str, Any]) -> bool:
        cutoff = str(self.source.get("min_published_at", "")).strip()
        if not cutoff:
            return True
        published_at = self._china_datetime(item.get("regDate"))
        return bool(published_at) and published_at[:10] >= cutoff[:10]

    def _fetch_page(self, page_index: int) -> Dict[str, Any]:
        request_json = {
            "unitNo": self.source["unit_no"],
            "subUnitNo": self.source.get("sub_unit_no", ""),
            "pageIndex": page_index,
        }
        payload = json.loads(
            fetch_bytes(
                self.source["url"],
                method="POST",
                json_body=request_json,
                headers={
                    "Origin": "https://www.gzrecruit.com",
                    "Referer": self.source["homepage"],
                    "X-Requested-With": "XMLHttpRequest",
                },
            ).decode("utf-8-sig")
        )
        if not isinstance(payload, dict):
            raise ValueError("才聚羊城岗位接口返回结构异常")
        if payload.get("success") is not True:
            raise ValueError(
                "才聚羊城岗位接口失败（errCode={}）".format(
                    payload.get("errCode", "未知")
                )
            )
        if not isinstance(payload.get("data"), list):
            raise ValueError("才聚羊城岗位接口缺少 data 数组")
        if "totalCount" not in payload or "totalPages" not in payload:
            raise ValueError("才聚羊城岗位接口缺少分页字段")
        return payload

    def _to_job(self, item: Dict[str, Any]) -> JobPosting:
        company = item.get("company")
        company_name = company.get("name", "") if isinstance(company, dict) else ""
        locations = []
        for field in ("workLoc1st", "workLoc2nd", "workLoc3rd"):
            value = str(item.get(field, "") or "").strip()
            if value and value not in locations:
                locations.append(value)
        description_parts = []
        salary = str(item.get("salary", "") or "").strip()
        if salary:
            description_parts.append("参考薪资：{}".format(salary))
        tags = item.get("tags")
        if isinstance(tags, list):
            tag_text = "、".join(str(tag) for tag in tags if tag)
            if tag_text:
                description_parts.append("平台标签：{}".format(tag_text))
        external_id = str(item.get("recruitNo", "") or "").strip()
        values = {
            "external_id": external_id,
            "title": item.get("station", ""),
            "company": self.source.get("company", company_name),
            "company_type": self.source.get("company_type", "未知"),
            "location": "/".join(locations)
            or self.source.get("location", "待核对"),
            "description": "；".join(description_parts),
            "education": item.get("degree", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self._china_datetime(item.get("regDate")),
            "url": self.source.get(
                "detail_url_template",
                "https://www.gzrecruit.com/jobs/recruit/detail/{recruitNo}",
            ).format(recruitNo=external_id),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        expected_unit_no = self.source["unit_no"]
        campus_property = int(self.source.get("recruit_property", 2))
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        items: List[Dict[str, Any]] = []
        total_count = 0

        for page_index in range(1, max_pages + 1):
            payload = self._fetch_page(page_index)
            page_items = payload["data"]
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("才聚羊城岗位列表元素结构异常")
            items.extend(page_items)
            try:
                total_count = int(payload.get("totalCount", len(items)))
                total_pages = int(payload.get("totalPages", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("才聚羊城岗位分页字段异常") from exc
            if not page_items or len(items) >= total_count or page_index >= total_pages:
                break
        if len(items) < total_count:
            raise ValueError("才聚羊城岗位没有完整返回全部分页")

        jobs = []
        for item in items:
            company = item.get("company")
            if not isinstance(company, dict):
                raise ValueError("才聚羊城岗位缺少公司信息")
            if company.get("unitNo") != expected_unit_no:
                raise ValueError("才聚羊城岗位接口返回了非目标公司的岗位")
            if item.get("recruitProperty") != campus_property:
                continue
            if not item.get("recruitNo") or not item.get("station"):
                raise ValueError("才聚羊城岗位缺少稳定 ID 或岗位名称")
            if not self._is_target_cycle(item):
                continue
            searchable = " ".join(
                [
                    str(item.get("station", "")),
                    " ".join(str(tag) for tag in item.get("tags", []) if tag)
                    if isinstance(item.get("tags"), list)
                    else "",
                ]
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(self._to_job(item))
        return jobs


class GdrcGroupCollector(Collector):
    """Collect campus jobs from 广东省人才市场's public SOE portal."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _date(value: Any) -> str:
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) < 8:
            return ""
        return "{}-{}-{}".format(digits[:4], digits[4:6], digits[6:8])

    def _fetch_page(self, page: int) -> Dict[str, Any]:
        request_json = {
            "city": "",
            "degreelevel": "",
            "isfulltime": "",
            "keyword": "",
            "posttype": "",
            "salary": "",
            "length": int(self.source.get("page_size", 50)),
            "page": page,
            "shzp": int(self.source.get("campus_flag", 0)),
            "gid": str(self.source["group_id"]),
            "gqzp": 1,
        }
        try:
            payload = json.loads(
                fetch_bytes(
                    self.source["url"],
                    method="POST",
                    json_body=request_json,
                    headers={
                        "Origin": "https://jq.gdrc.com",
                        "Referer": self.source["homepage"],
                    },
                ).decode("utf-8-sig")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("广东国企招聘岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError(
                "广东国企招聘岗位接口失败（code={}）".format(
                    payload.get("code", "未知")
                    if isinstance(payload, dict)
                    else "未知"
                )
            )
        data = payload.get("data")
        page_data = data.get("pageData") if isinstance(data, dict) else None
        if not isinstance(page_data, dict):
            raise ValueError("广东国企招聘岗位接口缺少分页数据")
        if not isinstance(page_data.get("list"), list):
            raise ValueError("广东国企招聘岗位接口缺少岗位数组")
        if "total" not in page_data:
            raise ValueError("广东国企招聘岗位接口缺少岗位总数")
        return page_data

    def _to_job(self, item: Dict[str, Any]) -> JobPosting:
        external_id = str(item.get("j_id", "") or "").strip()
        description = _html_fragment_text(item.get("detailrequirement", ""))
        headcount = str(item.get("headcount", "") or "").strip()
        if headcount and headcount != "0":
            description = "{}{}招聘人数：{}".format(
                description,
                "；" if description else "",
                headcount,
            )
        degree_map = self.source.get("degree_map", {})
        education = degree_map.get(
            str(item.get("degreelevel", "") or ""),
            "",
        )
        values = {
            "external_id": external_id,
            "title": item.get("position", ""),
            "company": item.get("companyname")
            or self.source.get("company", self.source["name"]),
            "company_type": self.source.get("company_type", "未知"),
            "location": item.get("address")
            or self.source.get("location", "待核对"),
            "description": description,
            "education": education,
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self._date(item.get("recruitstartday")),
            "deadline": self._date(item.get("recruitendday")),
            "url": self.source.get(
                "detail_url_template",
                (
                    "https://jq.gdrc.com/recruitCon/"
                    "recruit-job-detail.html?id={j_id}"
                ),
            ).format(j_id=external_id),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        expected_group_id = str(self.source["group_id"])
        campus_flag = str(self.source.get("campus_flag", 0))
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        cutoff = str(self.source.get("min_published_at", "")).strip()
        items: List[Dict[str, Any]] = []
        total = 0

        for page in range(1, max_pages + 1):
            page_data = self._fetch_page(page)
            page_items = page_data["list"]
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("广东国企招聘岗位列表元素结构异常")
            try:
                total = int(page_data["total"])
            except (TypeError, ValueError) as exc:
                raise ValueError("广东国企招聘岗位总数字段异常") from exc
            items.extend(page_items)
            if not page_items or len(items) >= total:
                break
        if len(items) < total:
            raise ValueError("广东国企招聘岗位没有完整返回全部分页")

        jobs = []
        for item in items:
            if str(item.get("gid", "")) != expected_group_id:
                raise ValueError("广东国企招聘岗位接口返回了非目标集团岗位")
            if str(item.get("shzp", "")) != campus_flag:
                continue
            if not item.get("j_id") or not item.get("position"):
                raise ValueError("广东国企招聘岗位缺少稳定 ID 或岗位名称")
            published_at = self._date(item.get("recruitstartday"))
            if cutoff and (not published_at or published_at < cutoff[:10]):
                continue
            searchable = " ".join(
                str(item.get(field, "") or "")
                for field in (
                    "position",
                    "companyname",
                    "address",
                    "detailrequirement",
                )
            )
            if location_keywords and not self._contains(
                searchable, location_keywords
            ):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(self._to_job(item))
        return jobs


class IguopinCompanyCollector(Collector):
    """Collect public campus jobs from an IGuopin company recruitment site."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _is_target_cycle(self, item: Dict[str, Any]) -> bool:
        cutoff = str(self.source.get("min_published_at", "")).strip()
        if not cutoff:
            return True
        published_at = str(
            item.get("create_time") or item.get("start_time") or ""
        ).strip()
        return bool(published_at) and published_at[:10] >= cutoff[:10]

    def _request_headers(self) -> Dict[str, str]:
        homepage = urlsplit(self.source["homepage"])
        return {
            "Device": "pc",
            "Version": "5",
            "Origin": "{}://{}".format(homepage.scheme, homepage.netloc),
            "Referer": self.source["homepage"],
        }

    def _target_campaign_started(self) -> bool:
        target_keywords = self.source.get("target_campaign_keywords", [])
        if not target_keywords:
            return True
        campaign_info_url = self.source.get("campaign_info_url")
        campaign_domain = self.source.get("campaign_domain")
        if not campaign_info_url or not campaign_domain:
            raise ValueError("国聘目标届别监控缺少专页配置")
        url = "{}?{}".format(
            campaign_info_url,
            urlencode({"domain": campaign_domain}),
        )
        try:
            payload = json.loads(
                fetch_bytes(
                    url,
                    headers=self._request_headers(),
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("国聘专页配置接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise ValueError("国聘专页配置接口响应异常")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("国聘专页配置接口缺少 data 对象")
        if str(data.get("company_id", "")) != str(self.source["company_id"]):
            raise ValueError("国聘专页配置返回了非目标集团")
        title = str(data.get("title", "") or "")
        if not title:
            raise ValueError("国聘专页配置缺少招聘标题")
        campaign_text = "{} {}".format(
            title,
            str(data.get("content", "") or ""),
        )
        return self._contains(campaign_text, target_keywords)

    def _fetch_page(self, page: int) -> Dict[str, Any]:
        request_json = {
            "page": page,
            "page_size": int(self.source.get("page_size", 50)),
            "company_id_with_sub": self.source["company_id"],
        }
        campus_natures = self.source.get("campus_natures", [])
        if campus_natures:
            request_json["nature"] = campus_natures
        try:
            payload = json.loads(
                fetch_bytes(
                    self.source["url"],
                    method="POST",
                    json_body=request_json,
                    headers=self._request_headers(),
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("国聘岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("国聘岗位接口返回结构异常")
        if payload.get("code") != 200:
            raise ValueError(
                "国聘岗位接口失败（code={}）".format(payload.get("code", "未知"))
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("国聘岗位接口缺少 data 对象")
        if not isinstance(data.get("list"), list):
            raise ValueError("国聘岗位接口缺少 list 数组")
        if any(field not in data for field in ("total", "page", "page_size")):
            raise ValueError("国聘岗位接口缺少分页字段")
        return data

    def _location(self, item: Dict[str, Any]) -> str:
        districts = item.get("district_list")
        locations = []
        if isinstance(districts, list):
            for district in districts:
                if not isinstance(district, dict):
                    raise ValueError("国聘岗位地点结构异常")
                location = str(district.get("area_cn", "") or "").strip()
                if location and location not in locations:
                    locations.append(location)
        return "/".join(locations) or self.source.get("location", "待核对")

    def _description(self, item: Dict[str, Any]) -> str:
        parts = []
        category = str(item.get("category_cn", "") or "").strip()
        if category:
            parts.append("岗位类别：{}".format(category))
        majors = item.get("major_cn")
        if isinstance(majors, list):
            major_text = "、".join(str(major) for major in majors if major)
            if major_text:
                parts.append("专业要求：{}".format(major_text))
        contents = _html_fragment_text(item.get("contents", ""))
        if contents:
            parts.append(contents)
        return "；".join(parts)

    def _to_job(self, item: Dict[str, Any]) -> JobPosting:
        external_id = str(item.get("job_id", "") or "").strip()
        company_info = item.get("company_info")
        company_info = company_info if isinstance(company_info, dict) else {}
        company = (
            item.get("company_name")
            or company_info.get("show_name")
            or company_info.get("name")
            or self.source.get("company", "")
        )
        values = {
            "external_id": external_id,
            "title": item.get("job_name", ""),
            "company": company,
            "company_type": self.source.get("company_type", "未知"),
            "location": self._location(item),
            "description": self._description(item),
            "education": item.get("education_cn", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": item.get("create_time")
            or item.get("start_time", ""),
            "deadline": item.get("end_time", ""),
            "url": self.source.get(
                "detail_url_template",
                "https://gdghr.iguopin.com/job/detail?id={job_id}",
            ).format(job_id=external_id),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        if not self._target_campaign_started():
            return []

        max_pages = max(1, int(self.source.get("max_pages", 20)))
        campus_natures = set(self.source.get("campus_natures", []))
        company_name_keywords = self.source.get("company_name_keywords", [])
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        items: List[Dict[str, Any]] = []
        total = 0

        for page in range(1, max_pages + 1):
            data = self._fetch_page(page)
            page_items = data["list"]
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("国聘岗位列表元素结构异常")
            items.extend(page_items)
            try:
                total = int(data["total"])
                current_page = int(data["page"])
                page_size = int(data["page_size"])
            except (TypeError, ValueError) as exc:
                raise ValueError("国聘岗位分页字段异常") from exc
            if current_page != page or page_size <= 0:
                raise ValueError("国聘岗位分页响应与请求不一致")
            if not page_items or len(items) >= total:
                break
        if len(items) < total:
            raise ValueError("国聘岗位没有完整返回全部分页")

        jobs = []
        for item in items:
            if not item.get("job_id") or not item.get("job_name"):
                raise ValueError("国聘岗位缺少稳定 ID 或岗位名称")
            company_name = str(item.get("company_name", "") or "")
            if company_name_keywords and not self._contains(
                company_name, company_name_keywords
            ):
                raise ValueError("国聘岗位接口返回了非目标集团的岗位")
            if campus_natures and item.get("nature") not in campus_natures:
                continue
            if item.get("recruitment_type_cn") != "校园招聘":
                continue
            if self.source.get("only_applicable", True) and not item.get(
                "is_apply", False
            ):
                continue
            if not self._is_target_cycle(item):
                continue

            description = self._description(item)
            searchable = " ".join(
                [
                    str(item.get("job_name", "")),
                    str(item.get("category_cn", "")),
                    " ".join(
                        str(major) for major in item.get("major_cn", []) if major
                    )
                    if isinstance(item.get("major_cn"), list)
                    else "",
                    description,
                ]
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            location = self._location(item)
            if location_keywords and not self._contains(
                location, location_keywords
            ):
                continue
            jobs.append(self._to_job(item))
        return jobs


class _MissingValueDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


class CvteCampusCollector(Collector):
    """Collect full-time target-cycle jobs from CVTE's public campus APIs."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _decode(raw: bytes, label: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("CVTE{}接口返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict):
            raise ValueError("CVTE{}接口响应结构异常".format(label))
        return payload

    @staticmethod
    def _china_datetime(value: Any) -> str:
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return ""
        if milliseconds <= 0:
            return ""
        china_time = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(
            milliseconds / 1000, tz=china_time
        ).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _education(text: str, fallback: str) -> str:
        levels = [
            level
            for level in ("博士", "硕士", "本科", "大专")
            if level in text
        ]
        return "/".join(levels) or fallback

    def _target_projects(self) -> List[Dict[str, Any]]:
        payload = self._decode(
            fetch_bytes(self.source["projects_url"]),
            "项目",
        )
        projects = payload.get("projects")
        if not isinstance(projects, list):
            raise ValueError("CVTE项目接口缺少 projects 数组")
        if any(not isinstance(project, dict) for project in projects):
            raise ValueError("CVTE项目列表元素结构异常")

        target_keywords = self.source.get("target_project_keywords", [])
        excluded_keywords = self.source.get("exclude_project_keywords", [])
        targets = []
        for project in projects:
            project_id = str(project.get("id", "") or "").strip()
            project_name = str(project.get("name", "") or "").strip()
            if not project_id or not project_name:
                raise ValueError("CVTE项目缺少稳定 ID 或名称")
            if target_keywords and not self._contains(
                project_name, target_keywords
            ):
                continue
            if self._contains(project_name, excluded_keywords):
                continue
            targets.append(project)
        return targets

    def _positions(
        self, projects: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        project_ids = [str(project["id"]) for project in projects]
        query = urlencode(
            [("projectIds", project_id) for project_id in project_ids]
        )
        separator = "&" if "?" in self.source["url"] else "?"
        payload = self._decode(
            fetch_bytes("{}{}{}".format(self.source["url"], separator, query)),
            "岗位",
        )
        positions = payload.get("projectPositions")
        if not isinstance(positions, list):
            raise ValueError("CVTE岗位接口缺少 projectPositions 数组")
        if any(not isinstance(position, dict) for position in positions):
            raise ValueError("CVTE岗位列表元素结构异常")
        return positions

    @staticmethod
    def _locations(item: Dict[str, Any]) -> str:
        areas = item.get("areaViews")
        if not isinstance(areas, list):
            raise ValueError("CVTE岗位地点结构异常")
        locations = []
        for area in areas:
            if not isinstance(area, dict):
                raise ValueError("CVTE岗位地点元素结构异常")
            city = str(area.get("cityName", "") or "").strip()
            if city and city not in locations:
                locations.append(city)
        return "/".join(locations)

    def _to_job(
        self,
        item: Dict[str, Any],
        project: Dict[str, Any],
        location: str,
    ) -> JobPosting:
        external_id = str(item["id"])
        requirement = str(item.get("requirement", "") or "").strip()
        duty = str(item.get("duty", "") or "").strip()
        description_parts = [
            "招聘性质：{}".format(item["propertyName"]),
            "项目：{}".format(item.get("projectName") or project["name"]),
            "岗位类别：{}".format(item.get("typeName", ""))
            if item.get("typeName")
            else "",
            "岗位职责：{}".format(duty) if duty else "",
            "任职要求：{}".format(requirement) if requirement else "",
        ]
        values = {
            "external_id": external_id,
            "title": item["name"],
            "company": self.source.get("company", "视源股份（CVTE）"),
            "company_type": self.source.get("company_type", "私企"),
            "location": location or self.source.get("location", "待核对"),
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self._education(
                requirement,
                self.source.get("education", ""),
            ),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self._china_datetime(item.get("updatedTime")),
            "deadline": self._china_datetime(project.get("endTime")),
            "url": self.source.get(
                "detail_url_template",
                "https://campus.cvte.com/position/{position_id}",
            ).format(position_id=external_id),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        projects = self._target_projects()
        if not projects:
            return []

        project_by_id = {str(project["id"]): project for project in projects}
        positions = self._positions(projects)
        full_time_names = set(
            self.source.get("full_time_property_names", ["全职岗位"])
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        exclude_title_keywords = self.source.get(
            "exclude_title_keywords", []
        )
        location_keywords = self.source.get("location_keywords", [])

        jobs = []
        for item in positions:
            external_id = str(item.get("id", "") or "").strip()
            title = str(item.get("name", "") or "").strip()
            project_id = str(item.get("projectId", "") or "").strip()
            property_name = str(item.get("propertyName", "") or "").strip()
            if (
                not external_id
                or not title
                or not project_id
                or not property_name
            ):
                raise ValueError("CVTE岗位缺少稳定 ID、名称、项目或招聘性质")
            if project_id not in project_by_id:
                raise ValueError("CVTE岗位接口返回了非目标招聘项目的岗位")
            if property_name not in full_time_names:
                continue
            if self._contains(title, exclude_title_keywords):
                continue

            location = self._locations(item)
            searchable = " ".join(
                str(item.get(field, "") or "")
                for field in (
                    "name",
                    "typeName",
                    "duty",
                    "requirement",
                )
            )
            if location_keywords and not self._contains(
                location, location_keywords
            ):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(
                self._to_job(item, project_by_id[project_id], location)
            )
        return jobs


class NeteaseGameCampusCollector(Collector):
    """Collect target-cycle jobs from NetEase Games' public campus APIs."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _decode(raw: bytes, label: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("网易互娱{}接口返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise ValueError("网易互娱{}接口响应异常".format(label))
        return payload

    @staticmethod
    def _china_datetime(value: Any) -> str:
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return ""
        if milliseconds <= 0:
            return ""
        china_time = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(
            milliseconds / 1000, tz=china_time
        ).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _education(text: str, fallback: str) -> str:
        levels = [
            level
            for level in ("博士", "硕士", "本科", "大专")
            if level in text
        ]
        return "/".join(levels) or fallback

    def _target_project(self) -> Dict[str, str] | None:
        payload = self._decode(
            fetch_bytes(self.source["navigation_url"]),
            "导航",
        )
        navigation = payload.get("data")
        if not isinstance(navigation, list):
            raise ValueError("网易互娱导航接口缺少 data 数组")
        if any(not isinstance(group, dict) for group in navigation):
            raise ValueError("网易互娱导航分组结构异常")

        group_title = self.source.get("project_group_title", "应届生")
        groups = [
            group
            for group in navigation
            if str(group.get("title", "") or "").strip() == group_title
        ]
        if len(groups) != 1:
            raise ValueError("网易互娱导航接口缺少唯一的应届生分组")
        children = groups[0].get("children")
        if not isinstance(children, list):
            raise ValueError("网易互娱应届生分组缺少 children 数组")
        if any(not isinstance(child, dict) for child in children):
            raise ValueError("网易互娱应届生项目结构异常")

        target_keywords = self.source.get("target_project_keywords", [])
        targets = []
        for child in children:
            title = str(child.get("title", "") or "").strip()
            link = str(child.get("link", "") or "").strip()
            if not title or not link:
                raise ValueError("网易互娱应届生项目缺少名称或链接")
            if target_keywords and not self._contains(
                title, target_keywords
            ):
                continue
            targets.append({"title": title, "link": link})

        if not targets:
            return None
        if len(targets) != 1:
            raise ValueError("网易互娱导航接口匹配到多个目标招聘项目")

        project = targets[0]
        parsed = urlsplit(project["link"])
        expected_host = self.source.get(
            "project_link_host", "campus.game.163.com"
        )
        expected_path = self.source.get(
            "project_link_path", "/app/job/position"
        )
        project_ids = parse_qs(parsed.query).get("id", [])
        if (
            parsed.hostname != expected_host
            or parsed.path != expected_path
            or len(project_ids) != 1
            or not project_ids[0].isdigit()
        ):
            raise ValueError("网易互娱目标招聘项目链接结构异常")
        project["id"] = project_ids[0]
        return project

    def _positions(self, project_id: str) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 100))
        max_pages = int(self.source.get("max_pages", 10))
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("网易互娱岗位分页配置必须为正整数")

        positions = []
        seen_ids = set()
        total = None
        for page in range(1, max_pages + 1):
            query = urlencode(
                {
                    "projectId": project_id,
                    "page": page,
                    "pageSize": page_size,
                }
            )
            separator = "&" if "?" in self.source["url"] else "?"
            payload = self._decode(
                fetch_bytes(
                    "{}{}{}".format(self.source["url"], separator, query)
                ),
                "岗位",
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("网易互娱岗位接口缺少 data 对象")
            page_items = data.get("list")
            current_total = data.get("total")
            pages = data.get("pages")
            if (
                not isinstance(page_items, list)
                or not isinstance(current_total, int)
                or current_total < 0
                or not isinstance(pages, int)
                or pages < 0
            ):
                raise ValueError("网易互娱岗位分页结构异常")
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("网易互娱岗位列表元素结构异常")
            if total is None:
                total = current_total
                if pages > max_pages:
                    raise ValueError("网易互娱岗位页数超过配置上限")
            elif current_total != total:
                raise ValueError("网易互娱岗位分页总数发生变化")

            for item in page_items:
                external_id = str(item.get("id", "") or "").strip()
                if not external_id:
                    raise ValueError("网易互娱岗位缺少稳定 ID")
                if external_id in seen_ids:
                    raise ValueError("网易互娱岗位分页返回了重复 ID")
                seen_ids.add(external_id)
                positions.append(item)

            if len(positions) >= total:
                break
            if not page_items or page >= pages:
                break

        if total is None or len(positions) != total:
            raise ValueError("网易互娱岗位没有完整返回全部分页")
        return positions

    def _to_job(
        self,
        item: Dict[str, Any],
        project: Dict[str, str],
    ) -> JobPosting:
        external_id = str(item["id"])
        requirement = str(
            item.get("positionRequirement", "") or ""
        ).strip()
        duty = str(item.get("positionDescription", "") or "").strip()
        tags = item.get("tagList")
        if not isinstance(tags, list) or any(
            not isinstance(tag, dict) for tag in tags
        ):
            raise ValueError("网易互娱岗位标签结构异常")
        tag_names = [
            str(tag.get("name", "") or "").strip()
            for tag in tags
            if str(tag.get("name", "") or "").strip()
        ]
        description_parts = [
            "项目：{}".format(project["title"]),
            "岗位类别：{}".format(item.get("positionTypeName", ""))
            if item.get("positionTypeName")
            else "",
            "岗位标签：{}".format("、".join(tag_names))
            if tag_names
            else "",
            "岗位职责：{}".format(duty) if duty else "",
            "任职要求：{}".format(requirement) if requirement else "",
        ]
        values = {
            "external_id": external_id,
            "title": str(item["positionName"]).strip(),
            "company": self.source.get("company", "网易游戏（互娱）"),
            "company_type": self.source.get("company_type", "私企"),
            "location": str(item["workPlaceName"]).strip(),
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self._education(
                requirement,
                self.source.get("education", ""),
            ),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self._china_datetime(item.get("updateTime")),
            "deadline": self.source.get("deadline", ""),
            "url": self.source.get(
                "detail_url_template",
                (
                    "https://campus.game.163.com/app/detail/index"
                    "?id={position_id}"
                ),
            ).format(position_id=external_id, project_id=project["id"]),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        project = self._target_project()
        if project is None:
            return []

        positions = self._positions(project["id"])
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        exclude_position_types = set(
            self.source.get("exclude_position_types", [])
        )
        excluded_type_title_exceptions = self.source.get(
            "excluded_type_title_exceptions", []
        )
        location_keywords = self.source.get("location_keywords", [])
        jobs = []
        for item in positions:
            external_id = str(item.get("id", "") or "").strip()
            title = str(item.get("positionName", "") or "").strip()
            project_id = str(item.get("projectId", "") or "").strip()
            position_type = str(
                item.get("positionTypeName", "") or ""
            ).strip()
            location = str(item.get("workPlaceName", "") or "").strip()
            if not external_id or not title or not project_id or not location:
                raise ValueError("网易互娱岗位缺少 ID、名称、项目或工作地点")
            if project_id != project["id"]:
                raise ValueError("网易互娱岗位接口返回了非目标招聘项目的岗位")
            if (
                position_type in exclude_position_types
                and not self._contains(
                    title, excluded_type_title_exceptions
                )
            ):
                continue

            tags = item.get("tagList")
            if not isinstance(tags, list) or any(
                not isinstance(tag, dict) for tag in tags
            ):
                raise ValueError("网易互娱岗位标签结构异常")
            searchable = " ".join(
                [
                    title,
                    position_type,
                    str(item.get("positionDescription", "") or ""),
                    str(item.get("positionRequirement", "") or ""),
                    " ".join(
                        str(tag.get("name", "") or "") for tag in tags
                    ),
                ]
            )
            if location_keywords and not self._contains(
                location, location_keywords
            ):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(self._to_job(item, project))
        return jobs


class _MokaInitDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.init_data = ""

    def handle_starttag(self, tag: str, attrs: Iterable[Any]) -> None:
        if tag.lower() != "input":
            return
        values = dict(attrs)
        if values.get("id") == "init-data":
            self.init_data = values.get("value", "")


class MokaCampusCollector(Collector):
    """Collect target-cycle jobs from a public Moka campus portal."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _portal_data(self, opener: Any) -> Dict[str, Any]:
        request = Request(
            self.source["homepage"],
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen_with_retry(
            request,
            timeout=20,
            opener=opener.open,
        ) as response:
            body = response.read().decode("utf-8", errors="replace")

        parser = _MokaInitDataParser()
        parser.feed(body)
        if not parser.init_data:
            raise ValueError("Moka校招门户缺少公开初始化数据")
        try:
            payload = json.loads(parser.init_data)
        except json.JSONDecodeError as exc:
            raise ValueError("Moka校招门户返回了无效初始化数据") from exc
        if not isinstance(payload, dict):
            raise ValueError("Moka校招门户初始化数据结构异常")

        org = payload.get("org")
        if not isinstance(org, dict):
            raise ValueError("Moka校招门户缺少机构信息")
        expected_org_id = self.source["org_id"]
        expected_site_id = str(self.source["site_id"])
        if (
            str(org.get("id", "")) != expected_org_id
            or str(payload.get("siteId", "")) != expected_site_id
            or payload.get("mode") != "campus"
        ):
            raise ValueError("Moka校招门户返回了非目标校招站点")
        expected_company = self.source.get("company", "")
        if expected_company and org.get("name") != expected_company:
            raise ValueError("Moka校招门户返回了非目标公司")

        aes_iv = payload.get("aesIv")
        if not isinstance(aes_iv, str) or len(aes_iv.encode("utf-8")) != 16:
            raise ValueError("Moka校招门户缺少有效公开解码参数")
        required_keywords = self.source.get("portal_required_keywords", [])
        if required_keywords:
            portal_text = json.dumps(payload, ensure_ascii=False)
            missing_keywords = [
                keyword
                for keyword in required_keywords
                if not self._contains(portal_text, [keyword])
            ]
            if missing_keywords:
                raise ValueError("Moka校招门户未匹配目标招聘届别")
        return payload

    def _validate_campaign(self) -> None:
        campaign_url = self.source.get("campaign_url")
        if not campaign_url:
            return
        body = fetch_bytes(
            campaign_url,
            timeout=int(self.source.get("campaign_timeout", 20)),
        ).decode("utf-8", errors="replace")
        required_keywords = self.source.get(
            "campaign_required_keywords", []
        )
        missing_keywords = [
            keyword
            for keyword in required_keywords
            if not self._contains(body, [keyword])
        ]
        if missing_keywords:
            raise ValueError("Moka校招活动页未匹配目标招聘届别")

    @staticmethod
    def _decode_api_payload(raw: bytes, aes_iv: str, label: str) -> Dict[str, Any]:
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Moka{}接口返回了无效 JSON".format(label)) from exc
        if not isinstance(envelope, dict):
            raise ValueError("Moka{}接口响应结构异常".format(label))

        if "necromancer" in envelope:
            key = envelope.get("necromancer")
            encrypted = envelope.get("data")
            if (
                not isinstance(key, str)
                or len(key.encode("utf-8")) != 16
                or not isinstance(encrypted, str)
            ):
                raise ValueError("Moka{}接口缺少有效公开解码参数".format(label))
            try:
                ciphertext = base64.b64decode(encrypted, validate=True)
                plaintext = unpad(
                    AES.new(
                        key.encode("utf-8"),
                        AES.MODE_CBC,
                        aes_iv.encode("utf-8"),
                    ).decrypt(ciphertext),
                    AES.block_size,
                )
                payload = json.loads(plaintext.decode("utf-8"))
            except (
                ValueError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                binascii.Error,
            ) as exc:
                raise ValueError(
                    "Moka{}接口公开数据解码失败".format(label)
                ) from exc
        else:
            payload = envelope

        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or payload.get("code") != 0
            or not isinstance(payload.get("data"), dict)
        ):
            raise ValueError("Moka{}接口响应异常".format(label))
        return payload["data"]

    def _post(
        self,
        opener: Any,
        url: str,
        payload: Dict[str, Any],
        aes_iv: str,
        label: str,
    ) -> Dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": self.source.get(
                    "origin", "https://app-tc.mokahr.com"
                ),
                "Referer": self.source["homepage"],
            },
            method="POST",
        )
        with urlopen_with_retry(
            request,
            timeout=20,
            opener=opener.open,
        ) as response:
            raw = response.read()
        return self._decode_api_payload(raw, aes_iv, label)

    def _positions(
        self,
        opener: Any,
        aes_iv: str,
    ) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 30))
        max_pages = int(self.source.get("max_pages", 10))
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("Moka岗位分页配置必须为正整数")

        positions = []
        seen_ids = set()
        total = None
        for page in range(max_pages):
            payload = {
                "orgId": self.source["org_id"],
                "siteId": self.source["site_id"],
                "limit": page_size,
                "offset": page * page_size,
                "needStat": True,
                "site": "campus",
                "locale": "zh-CN",
            }
            data = self._post(
                opener,
                self.source["url"],
                payload,
                aes_iv,
                "岗位列表",
            )
            stats = data.get("jobStats")
            page_items = data.get("jobs")
            if (
                not isinstance(stats, dict)
                or not isinstance(stats.get("total"), int)
                or stats["total"] < 0
                or not isinstance(page_items, list)
                or any(not isinstance(item, dict) for item in page_items)
            ):
                raise ValueError("Moka岗位列表分页结构异常")
            if total is None:
                total = stats["total"]
                if total > page_size * max_pages:
                    raise ValueError("Moka岗位总数超过配置分页上限")
            elif stats["total"] != total:
                raise ValueError("Moka岗位列表分页总数发生变化")

            for item in page_items:
                external_id = str(item.get("id", "") or "").strip()
                if not external_id:
                    raise ValueError("Moka岗位缺少稳定 ID")
                if external_id in seen_ids:
                    raise ValueError("Moka岗位分页返回了重复 ID")
                seen_ids.add(external_id)
                positions.append(item)
            if len(positions) >= total:
                break
            if not page_items:
                break

        if total is None or len(positions) != total:
            raise ValueError("Moka岗位列表没有完整返回全部分页")
        return positions

    def _detail(
        self,
        opener: Any,
        aes_iv: str,
        job_id: str,
    ) -> Dict[str, Any]:
        return self._post(
            opener,
            self.source["detail_url"],
            {
                "orgId": self.source["org_id"],
                "siteId": self.source["site_id"],
                "jobId": job_id,
                "locale": "zh-CN",
            },
            aes_iv,
            "岗位详情",
        )

    def _locations(self, value: Any) -> str:
        if not isinstance(value, list):
            raise ValueError("Moka岗位工作地点结构异常")
        city_id_map = {
            str(city_id): str(name)
            for city_id, name in self.source.get("city_id_map", {}).items()
        }
        names = []
        for item in value:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                name = str(
                    item.get("name")
                    or item.get("cityName")
                    or item.get("label")
                    or city_id_map.get(str(item.get("cityId", "")))
                    or item.get("address")
                    or ""
                ).strip()
            else:
                raise ValueError("Moka岗位工作地点元素结构异常")
            if name and name not in names:
                names.append(name)
        return "、".join(names)

    def _to_job(self, detail: Dict[str, Any]) -> JobPosting:
        external_id = str(detail["id"]).strip()
        title = str(detail["title"]).strip()
        location = self._locations(detail.get("locations"))
        if not location:
            location = self.source.get("location", "待核对")
        department = detail.get("department")
        department_name = (
            str(department.get("name", "") or "").strip()
            if isinstance(department, dict)
            else ""
        )
        function = detail.get("zhineng")
        function_name = (
            str(function.get("name", "") or "").strip()
            if isinstance(function, dict)
            else ""
        )
        job_description = _html_fragment_text(
            detail.get("jobDescription", "")
        )
        description_parts = [
            "招聘性质：{}".format(detail.get("commitment", ""))
            if detail.get("commitment")
            else "",
            "职能：{}".format(function_name) if function_name else "",
            "部门：{}".format(department_name) if department_name else "",
            job_description,
        ]
        values = {
            "external_id": external_id,
            "title": title,
            "company": self.source["company"],
            "company_type": self.source.get("company_type", "私企"),
            "location": location,
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": str(detail.get("education", "") or "").strip()
            or self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": str(
                detail.get("publishedAt", "") or ""
            ).strip(),
            "deadline": self.source.get("deadline", ""),
            "url": self.source.get(
                "detail_url_template",
                (
                    "https://app-tc.mokahr.com/campus-recruitment/"
                    "{org_id}/{site_id}#/job/{position_id}"
                ),
            ).format(
                org_id=self.source["org_id"],
                site_id=self.source["site_id"],
                position_id=external_id,
            ),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        self._validate_campaign()
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        portal_data = self._portal_data(opener)
        positions = self._positions(opener, portal_data["aesIv"])
        target_cycle_keywords = self.source.get(
            "target_cycle_keywords", []
        )
        exclude_title_keywords = self.source.get(
            "exclude_title_keywords", []
        )
        prefilter_title_keywords = self.source.get(
            "prefilter_title_keywords", []
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        exclude_commitments = set(
            self.source.get("exclude_commitments", [])
        )
        location_keywords = self.source.get("location_keywords", [])
        target_project_ids = {
            str(project_id)
            for project_id in self.source.get("target_project_ids", [])
        }

        jobs = []
        for item in positions:
            title = str(item.get("title", "") or "").strip()
            if not title:
                raise ValueError("Moka岗位缺少名称")
            if target_cycle_keywords and not self._contains(
                title, target_cycle_keywords
            ):
                continue
            if self._contains(title, exclude_title_keywords):
                continue
            if prefilter_title_keywords and not self._contains(
                title, prefilter_title_keywords
            ):
                continue

            external_id = str(item["id"]).strip()
            if self.source.get("details_in_list"):
                detail = item
            else:
                detail = self._detail(
                    opener,
                    portal_data["aesIv"],
                    external_id,
                )
            if (
                str(detail.get("id", "") or "").strip() != external_id
                or str(detail.get("orgId", "") or "").strip()
                != self.source["org_id"]
                or str(detail.get("status", "") or "").strip() != "open"
            ):
                raise ValueError("Moka岗位详情返回了非目标在招岗位")
            if target_project_ids:
                project = detail.get("projectFolder")
                if (
                    not isinstance(project, dict)
                    or str(project.get("id", "") or "")
                    not in target_project_ids
                ):
                    raise ValueError("Moka岗位详情返回了非目标招聘项目")
            commitment = str(
                detail.get("commitment", "") or ""
            ).strip()
            if commitment in exclude_commitments:
                continue

            location = self._locations(detail.get("locations"))
            if (
                location
                and location_keywords
                and not self._contains(location, location_keywords)
            ):
                continue
            searchable = " ".join(
                [
                    title,
                    commitment,
                    _html_fragment_text(
                        detail.get("jobDescription", "")
                    ),
                    json.dumps(
                        detail.get("department", {}),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        detail.get("zhineng", {}),
                        ensure_ascii=False,
                    ),
                ]
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(self._to_job(detail))
        return jobs


class SheinCampusCollector(Collector):
    """Collect target-cycle jobs from SHEIN's public careers API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _decode(raw: bytes) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("SHEIN岗位接口返回了无效 JSON") from exc
        if (
            not isinstance(payload, dict)
            or str(payload.get("code", "")) != "0"
        ):
            raise ValueError("SHEIN岗位接口响应异常")
        return payload

    @staticmethod
    def _locations(value: Any) -> str:
        if not isinstance(value, list):
            raise ValueError("SHEIN岗位地点结构异常")
        locations = []
        for city in value:
            if not isinstance(city, dict):
                raise ValueError("SHEIN岗位地点元素结构异常")
            city_id = str(city.get("cityId", "") or "").strip()
            city_name = str(city.get("cityName", "") or "").strip()
            if not city_id or not city_name:
                raise ValueError("SHEIN岗位地点缺少城市 ID 或名称")
            if city_name not in locations:
                locations.append(city_name)
        return "/".join(locations)

    @staticmethod
    def _education(text: str, fallback: str) -> str:
        levels = [
            level
            for level in ("博士", "硕士", "本科", "大专")
            if level in text
        ]
        return "/".join(levels) or fallback

    def _request_json(self, page: int, page_size: int) -> Dict[str, Any]:
        return {
            "current": page,
            "cityName": "",
            "countryIds": self.source.get("country_ids", ["CHN"]),
            "cityIds": self.source.get("city_ids", []),
            "jobCategoryIds": self.source.get("job_category_ids", []),
            "jobTypeIds": [
                self.source.get("campus_job_type_id", "CAMPUS")
            ],
            "key": "",
            "langCode": self.source.get("lang_code", "CN"),
            "size": page_size,
        }

    def _positions(self) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 100))
        max_pages = int(self.source.get("max_pages", 10))
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("SHEIN岗位分页配置必须为正整数")

        positions = []
        seen_ids = set()
        total = None
        for page in range(1, max_pages + 1):
            payload = self._decode(
                fetch_bytes(
                    self.source["url"],
                    method="POST",
                    json_body=self._request_json(page, page_size),
                    headers={"Accept": "application/json"},
                )
            )
            info = payload.get("info")
            if not isinstance(info, dict):
                raise ValueError("SHEIN岗位接口缺少 info 对象")
            current = info.get("current")
            returned_size = info.get("size")
            current_total = info.get("total")
            page_items = info.get("records")
            if (
                current != page
                or returned_size != page_size
                or not isinstance(current_total, int)
                or current_total < 0
                or not isinstance(page_items, list)
                or len(page_items) > page_size
            ):
                raise ValueError("SHEIN岗位分页结构异常")
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("SHEIN岗位列表元素结构异常")
            if total is None:
                total = current_total
                expected_pages = (
                    (total + page_size - 1) // page_size if total else 0
                )
                if expected_pages > max_pages:
                    raise ValueError("SHEIN岗位页数超过配置上限")
            elif current_total != total:
                raise ValueError("SHEIN岗位分页总数发生变化")

            for item in page_items:
                external_id = str(item.get("jobId", "") or "").strip()
                if not external_id:
                    raise ValueError("SHEIN岗位缺少稳定 ID")
                if external_id in seen_ids:
                    raise ValueError("SHEIN岗位分页返回了重复 ID")
                seen_ids.add(external_id)
                positions.append(item)

            if len(positions) >= total:
                break
            if not page_items:
                break

        if total is None or len(positions) != total:
            raise ValueError("SHEIN岗位没有完整返回全部分页")
        return positions

    def _to_job(self, item: Dict[str, Any], location: str) -> JobPosting:
        description = _html_fragment_text(item.get("description", ""))
        description_parts = [
            "招聘类型：正式校园招聘",
            "岗位类别：{}".format(item.get("jobCategoryName", ""))
            if item.get("jobCategoryName")
            else "",
            description,
        ]
        external_id = str(item["jobId"]).strip()
        values = {
            "external_id": external_id,
            "title": str(item["jobTitle"]).strip(),
            "company": self.source.get("company", "SHEIN"),
            "company_type": self.source.get("company_type", "私企"),
            "location": location or self.source.get("location", "待核对"),
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self._education(
                description,
                self.source.get("education", ""),
            ),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": str(
                item.get("releaseDate", "") or ""
            ).strip(),
            "deadline": self.source.get("deadline", ""),
            "url": str(
                item.get("jobDetailUrl", "")
                or self.source.get("homepage", self.source["url"])
            ).strip(),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        positions = self._positions()
        country_ids = set(self.source.get("country_ids", ["CHN"]))
        city_ids = set(self.source.get("city_ids", []))
        category_ids = set(self.source.get("job_category_ids", []))
        campus_type = self.source.get("campus_job_type_id", "CAMPUS")
        target_cycle_keywords = self.source.get(
            "target_cycle_keywords", []
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        jobs = []
        for item in positions:
            external_id = str(item.get("jobId", "") or "").strip()
            title = str(item.get("jobTitle", "") or "").strip()
            country_id = str(item.get("countryId", "") or "").strip()
            category_id = str(
                item.get("jobCategoryId", "") or ""
            ).strip()
            job_type_id = str(item.get("jobTypeId", "") or "").strip()
            if (
                not external_id
                or not title
                or not country_id
                or not category_id
                or not job_type_id
            ):
                raise ValueError(
                    "SHEIN岗位缺少 ID、名称、国家、类别或招聘类型"
                )
            if (
                (country_ids and country_id not in country_ids)
                or (category_ids and category_id not in category_ids)
                or job_type_id != campus_type
            ):
                raise ValueError("SHEIN岗位接口返回了非目标筛选条件的岗位")

            city_infos = item.get("cityInfos")
            location = self._locations(city_infos)
            returned_city_ids = {
                str(city["cityId"]).strip() for city in city_infos
            }
            if city_ids and not returned_city_ids.intersection(city_ids):
                raise ValueError("SHEIN岗位接口返回了非目标城市岗位")

            description = _html_fragment_text(
                item.get("description", "")
            )
            cycle_text = "{} {}".format(title, description)
            if target_cycle_keywords and not self._contains(
                cycle_text, target_cycle_keywords
            ):
                continue
            searchable = "{} {} {}".format(
                title,
                item.get("jobCategoryName", ""),
                description,
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            jobs.append(self._to_job(item, location))
        return jobs


class _HsbcFinderStateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.states: List[Dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        if tag != "div":
            return
        values = dict(attrs)
        if values.get("data-component") == "ProgramFinder":
            self.states.append(values)


class _HsbcProgrammeParser(HTMLParser):
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    FIELD_CLASSES = {
        "program-location": "location",
        "program-text__area": "area",
        "program-text__destination": "title",
        "program-text__short-description": "description",
        "program-text__group--type": "programme_type",
        "program-text__group--opening": "opening_date",
        "program-text__group--closing": "closing_date",
        "program-text__group--start": "start_date",
    }

    def __init__(self):
        super().__init__()
        self.programmes: List[Dict[str, str]] = []
        self.current: Dict[str, str] | None = None
        self.field = ""
        self.group_field = ""
        self.capture_group_value = False
        self.ignore_text_depth = 0
        self.depth = 0

    @staticmethod
    def _classes(attrs) -> set:
        return set(dict(attrs).get("class", "").split())

    def handle_starttag(self, tag: str, attrs):
        values = dict(attrs)
        classes = self._classes(attrs)
        if (
            tag == "li"
            and "program-item" in classes
            and self.current is None
        ):
            self.current = {
                "external_id": values.get("data-cs-override-id", "")
            }
            self.depth = 1
            return
        if self.current is None:
            return

        if tag not in self.VOID_TAGS:
            self.depth += 1
        if self.ignore_text_depth:
            if tag not in self.VOID_TAGS:
                self.ignore_text_depth += 1
            return
        if "sr-only" in classes:
            self.ignore_text_depth = 1
            return
        if (
            tag == "a"
            and "program-text__destination-link" in classes
        ):
            self.current["url"] = values.get("href", "")
        if tag == "dd" and "program-text__value" in classes:
            self.capture_group_value = bool(self.group_field)
        for class_name, field in self.FIELD_CLASSES.items():
            if class_name not in classes:
                continue
            if field in {
                "programme_type",
                "opening_date",
                "closing_date",
                "start_date",
            }:
                self.group_field = field
            else:
                self.field = field

    def handle_data(self, data: str):
        if self.current is None or self.ignore_text_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        target = self.group_field if self.capture_group_value else self.field
        if target:
            existing = self.current.get(target, "")
            self.current[target] = "{} {}".format(existing, text).strip()

    def handle_endtag(self, tag: str):
        if self.current is None:
            return
        if self.ignore_text_depth:
            self.ignore_text_depth -= 1
            self.depth -= 1
            return
        if tag == "dd":
            self.capture_group_value = False
        elif tag in {"h2", "div"}:
            self.field = ""
        self.depth -= 1
        if tag == "li" and self.depth == 0:
            self.programmes.append(self.current)
            self.current = None
            self.field = ""
            self.group_field = ""
            self.capture_group_value = False
            self.ignore_text_depth = 0


class HsbcProgrammeCollector(Collector):
    """Collect target-cycle graduate programmes from HSBC's public API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _date(value: Any, label: str) -> datetime | None:
        text = " ".join(str(value or "").split())
        if not text:
            return None
        normalized = re.sub(
            r"(?i)(\d{1,2})(st|nd|rd|th)", r"\1", text
        )
        formats = (
            "%d %b %Y",
            "%a %b %d, %Y",
            "%b %Y",
            "%B %Y",
        )
        for date_format in formats:
            try:
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue
        raise ValueError("汇丰项目{}格式异常".format(label))

    def _finder_state(self) -> Dict[str, Any]:
        raw = fetch_bytes(self.source["url"])
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("汇丰项目筛选页返回了无效 UTF-8") from exc
        parser = _HsbcFinderStateParser()
        parser.feed(body)
        if len(parser.states) != 1:
            raise ValueError("汇丰项目筛选页缺少唯一 ProgramFinder")
        state = parser.states[0]
        settings_id = state.get("data-props-settings", "")
        total_text = state.get("data-props-total-count", "")
        if not re.fullmatch(r"[a-f0-9]{32}", settings_id):
            raise ValueError("汇丰项目筛选页 settings ID 结构异常")
        try:
            total = int(total_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("汇丰项目筛选页缺少项目总数") from exc
        if total < 0:
            raise ValueError("汇丰项目筛选页项目总数异常")
        return {"settings_id": settings_id, "total": total}

    def _validate_filters(self) -> Dict[str, str]:
        query = parse_qs(urlsplit(self.source["url"]).query)
        required = {
            "location": self.source.get(
                "location_filter", "mainland-china"
            ),
            "programme-type": self.source.get(
                "programme_type_filter", "graduate-programme"
            ),
        }
        for key, expected in required.items():
            if query.get(key) != [expected]:
                raise ValueError("汇丰项目筛选页缺少目标{}参数".format(key))
        return required

    def _programmes(
        self, state: Dict[str, Any], filters: Dict[str, str]
    ) -> List[Dict[str, str]]:
        max_programmes = int(self.source.get("max_programmes", 50))
        if max_programmes <= 0:
            raise ValueError("汇丰项目数量上限必须为正整数")
        if state["total"] > max_programmes:
            raise ValueError("汇丰项目数量超过配置上限")
        request_query = {
            "skip": 0,
            "take": max(state["total"] + 1, 1),
            "count": 0,
            "s": state["settings_id"],
            **filters,
        }
        separator = "&" if "?" in self.source["api_url"] else "?"
        raw = fetch_bytes(
            "{}{}{}".format(
                self.source["api_url"],
                separator,
                urlencode(request_query),
            ),
            headers={"Accept": "application/json"},
        )
        try:
            fragments = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("汇丰项目接口返回了无效 JSON") from exc
        if not isinstance(fragments, list) or any(
            not isinstance(fragment, str) for fragment in fragments
        ):
            raise ValueError("汇丰项目接口响应结构异常")

        parser = _HsbcProgrammeParser()
        for fragment in fragments:
            parser.feed(fragment)
        programmes = parser.programmes
        if len(programmes) != state["total"]:
            raise ValueError("汇丰项目接口没有完整返回筛选结果")
        ids = [item.get("external_id", "") for item in programmes]
        if any(not external_id for external_id in ids):
            raise ValueError("汇丰项目缺少稳定 ID")
        if len(ids) != len(set(ids)):
            raise ValueError("汇丰项目接口返回了重复 ID")
        return programmes

    def _location(self, value: str) -> str:
        locations = []
        for keyword, translated in self.source.get(
            "location_map", {}
        ).items():
            if keyword.lower() in value.lower() and translated not in locations:
                locations.append(translated)
        if locations:
            return "/".join(locations)
        if self.source.get("allow_generic_mainland", True) and (
            "mainland china" in value.lower()
        ):
            return self.source.get(
                "location", "中国大陆（城市待官网详情确认）"
            )
        return value

    def _is_target_cycle(
        self,
        opening: datetime,
        closing: datetime,
        start: datetime | None,
    ) -> bool:
        target_start_years = set(
            int(year) for year in self.source.get("target_start_years", [])
        )
        if start is not None and (
            target_start_years and start.year not in target_start_years
        ):
            return False
        opening_start = datetime.strptime(
            self.source["target_opening_start"], "%Y-%m-%d"
        )
        opening_end = datetime.strptime(
            self.source["target_opening_end"], "%Y-%m-%d"
        )
        if not opening_start <= opening <= opening_end:
            return False
        reference_date = self.source.get("reference_date")
        today = (
            datetime.strptime(reference_date, "%Y-%m-%d")
            if reference_date
            else datetime.now()
        )
        return closing.date() >= today.date()

    def _to_job(
        self,
        item: Dict[str, str],
        location: str,
        opening: datetime,
        closing: datetime,
        start: datetime | None,
    ) -> JobPosting:
        description_parts = [
            "项目类型：{}".format(item["programme_type"]),
            "业务方向：{}".format(item["area"]) if item.get("area") else "",
            item.get("description", ""),
            "预计开始时间：{}".format(start.strftime("%Y-%m-%d"))
            if start
            else "",
        ]
        values = {
            "external_id": item["external_id"],
            "title": item["title"],
            "company": self.source.get("company", "汇丰中国（HSBC）"),
            "company_type": self.source.get("company_type", "外企"),
            "location": location,
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": opening.strftime("%Y-%m-%d"),
            "deadline": closing.strftime("%Y-%m-%d"),
            "url": urljoin(self.source["homepage"], item["url"]),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        filters = self._validate_filters()
        state = self._finder_state()
        programmes = self._programmes(state, filters)
        programme_type = self.source.get(
            "programme_type", "Graduate Programme"
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        target_locations = self.source.get("location_keywords", [])
        jobs = []
        for item in programmes:
            required = (
                "external_id",
                "title",
                "location",
                "programme_type",
                "opening_date",
                "closing_date",
                "url",
            )
            if any(
                not str(item.get(field, "") or "").strip()
                for field in required
            ):
                raise ValueError("汇丰项目缺少 ID、名称、地点、类型、日期或链接")
            if item["programme_type"] != programme_type:
                raise ValueError("汇丰项目接口返回了非毕业生项目")
            if "mainland china" not in item["location"].lower():
                raise ValueError("汇丰项目接口返回了非中国大陆项目")

            generic_mainland = item["location"].strip().lower() in {
                "mainland china",
                "mainland china cities - hsbc bank china",
                "mainland china cities - hsbc qianhai",
            }
            if (
                target_locations
                and not self._contains(
                    item["location"], target_locations
                )
                and not (
                    generic_mainland
                    and self.source.get("allow_generic_mainland", True)
                )
            ):
                continue
            searchable = " ".join(
                [
                    item["title"],
                    item.get("area", ""),
                    item.get("description", ""),
                ]
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue

            opening = self._date(item["opening_date"], "开放日期")
            closing = self._date(item["closing_date"], "截止日期")
            start = self._date(item.get("start_date"), "开始日期")
            if opening is None or closing is None:
                raise ValueError("汇丰项目缺少开放日期或截止日期")
            if not self._is_target_cycle(opening, closing, start):
                continue
            jobs.append(
                self._to_job(
                    item,
                    self._location(item["location"]),
                    opening,
                    closing,
                    start,
                )
            )
        return jobs


class AccentureEarlyCareerCollector(Collector):
    """Collect target early-career jobs from Accenture's public search API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _text_list(value: Any, label: str) -> List[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError("埃森哲岗位{}结构异常".format(label))
        return [item.strip() for item in value]

    @staticmethod
    def _updated_at(value: Any) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise ValueError("埃森哲岗位缺少更新时间")
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("埃森哲岗位更新时间格式异常") from exc

    def _positions(self) -> List[Dict[str, Any]]:
        max_results = int(self.source.get("max_results", 50))
        if max_results <= 0:
            raise ValueError("埃森哲岗位数量上限必须为正整数")
        experience = self.source.get(
            "experience_filter", "Early Career"
        )
        job_filters = [
            {
                "fieldName": "jobTypeDescription.keyword",
                "items": [experience],
                "multiSelect": False,
            }
        ]
        form = {
            "startIndex": 0,
            "maxResultSize": max_results,
            "jobKeyword": "",
            "jobCountry": self.source.get(
                "country_filter", "China/Mainland"
            ),
            "jobLanguage": self.source.get("language", "en"),
            "countrySite": self.source.get("country_site", "cn-en"),
            "sortBy": 2,
            "searchType": "vectorSearch",
            "enableQueryBoost": "true",
            "minScore": self.source.get("min_score", 0.6),
            "getFeedbackJudgmentEnabled": "true",
            "useCleanEmbedding": "true",
            "score": "true",
            "totalHits": "true",
            "debugQuery": "false",
            "jobFilters": json.dumps(
                job_filters, ensure_ascii=False, separators=(",", ":")
            ),
        }
        raw = fetch_bytes(
            self.source["url"],
            timeout=int(self.source.get("timeout", 40)),
            method="POST",
            form_body=form,
            headers={"Accept": "application/json"},
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("埃森哲岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("message") != "Success":
            raise ValueError("埃森哲岗位接口响应状态异常")
        positions = payload.get("data")
        total_hits = payload.get("totalHits")
        if (
            not isinstance(positions, list)
            or not isinstance(total_hits, dict)
            or not isinstance(total_hits.get("total"), int)
        ):
            raise ValueError("埃森哲岗位接口响应结构异常")
        total = total_hits["total"]
        if total < 0 or total > max_results:
            raise ValueError("埃森哲岗位数量超过配置上限")
        if len(positions) != total:
            raise ValueError("埃森哲岗位接口没有完整返回筛选结果")
        if any(not isinstance(item, dict) for item in positions):
            raise ValueError("埃森哲岗位列表元素结构异常")

        ids = [str(item.get("guid", "") or "").strip() for item in positions]
        if any(not external_id for external_id in ids):
            raise ValueError("埃森哲岗位缺少稳定 ID")
        if len(ids) != len(set(ids)):
            raise ValueError("埃森哲岗位接口返回了重复 ID")
        return positions

    def _location(self, values: List[str]) -> str:
        mapping = self.source.get("location_map", {})
        locations = []
        for value in values:
            translated = mapping.get(value, value)
            if translated not in locations:
                locations.append(str(translated))
        return "/".join(locations)

    def _explicit_cycle(self, title: str) -> bool | None:
        lowered = title.lower()
        markers = self.source.get(
            "cycle_markers", ["graduate program", "campus", "AAP"]
        )
        if not self._contains(lowered, markers):
            return None
        years = {
            int(year) for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", title)
        }
        years.update(
            2000 + int(year)
            for year in re.findall(r"(?i)\bFY\s*(\d{2})\b", title)
        )
        if not years:
            return None
        target_years = {
            int(year) for year in self.source.get("target_cycle_years", [])
        }
        return bool(years.intersection(target_years))

    def _is_target_cycle(
        self,
        item: Dict[str, Any],
        title: str,
        updated_at: datetime,
    ) -> bool:
        start = datetime.strptime(
            self.source["target_updated_start"], "%Y-%m-%d"
        ).date()
        end = datetime.strptime(
            self.source["target_updated_end"], "%Y-%m-%d"
        ).date()
        if not start <= updated_at.date() <= end:
            return False
        explicit_cycle = self._explicit_cycle(title)
        if explicit_cycle is not None:
            return explicit_cycle
        return str(item.get("yearsOfExperience", "") or "").strip() in set(
            self.source.get("entry_experience_ranges", ["0-2"])
        )

    def _to_job(
        self,
        item: Dict[str, Any],
        title: str,
        locations: List[str],
        updated_at: datetime,
    ) -> JobPosting:
        education = self._text_list(item.get("education") or [], "学历")
        functions = self._text_list(item.get("function") or [], "职能")
        skills = self._text_list(item.get("skill") or [], "技能")
        areas = self._text_list(item.get("areaOfInterest") or [], "方向")
        description_parts = [
            "招聘阶段：{}".format(item["jobTypeDescription"]),
            "职级：{}".format(item.get("careerLevel", ""))
            if item.get("careerLevel")
            else "",
            "经验范围：{}".format(item.get("yearsOfExperience", ""))
            if item.get("yearsOfExperience")
            else "",
            "方向：{}".format("/".join(areas)) if areas else "",
            "职能：{}".format("/".join(functions)) if functions else "",
            "技能：{}".format("/".join(skills)) if skills else "",
            str(item.get("jobDescriptionClean", "") or "").strip(),
            str(item.get("qualificationClean", "") or "").strip(),
        ]
        detail_url = str(item["jobDetailUrl"]).replace(
            "{0}", self.source.get("country_site", "cn-en")
        )
        parsed_url = urlsplit(detail_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc not in {
                "www.accenture.com",
                "www.accenture.cn",
            }
            or "/careers/jobdetails" not in parsed_url.path
        ):
            raise ValueError("埃森哲岗位详情链接结构异常")
        values = {
            "external_id": item["guid"],
            "title": title,
            "company": self.source.get("company", "埃森哲中国"),
            "company_type": self.source.get("company_type", "外企"),
            "location": self._location(locations),
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": "/".join(education),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": updated_at.date().isoformat(),
            "deadline": self.source.get("deadline", ""),
            "url": detail_url,
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        positions = self._positions()
        expected_country = self.source.get(
            "country_filter", "China/Mainland"
        )
        allowed_countries = set(
            self.source.get("allowed_api_countries", [expected_country])
        )
        expected_experience = self.source.get(
            "experience_filter", "Early Career"
        )
        expected_employee_type = self.source.get(
            "employee_type", "Full-time"
        )
        target_locations = set(self.source.get("location_keywords", []))
        include_keywords = self.source.get("include_keywords", [])
        exclude_title_keywords = self.source.get(
            "exclude_title_keywords", []
        )
        jobs = []
        for item in positions:
            required = (
                "guid",
                "title",
                "country",
                "location",
                "jobTypeDescription",
                "employeeType",
                "updateDate",
                "jobDetailUrl",
            )
            if any(item.get(field) is None for field in required):
                raise ValueError("埃森哲岗位缺少必要字段")
            title = str(item["title"]).strip()
            if not title:
                raise ValueError("埃森哲岗位缺少名称")
            if (
                item["country"] not in allowed_countries
                or item["jobTypeDescription"] != expected_experience
                or item["employeeType"] != expected_employee_type
            ):
                raise ValueError("埃森哲岗位接口返回了非目标筛选条件的岗位")
            if item["country"] != expected_country:
                continue
            locations = self._text_list(item["location"], "地点")
            if target_locations and not target_locations.intersection(
                locations
            ):
                continue
            if self._contains(title, exclude_title_keywords):
                continue

            searchable_parts = [
                title,
                str(item.get("jobDescriptionClean", "") or ""),
                str(item.get("qualificationClean", "") or ""),
                " ".join(
                    self._text_list(item.get("function") or [], "职能")
                ),
                " ".join(
                    self._text_list(item.get("skill") or [], "技能")
                ),
                " ".join(
                    self._text_list(
                        item.get("areaOfInterest") or [], "方向"
                    )
                ),
                " ".join(
                    self._text_list(
                        item.get("jobFamilyGroup") or [], "岗位族"
                    )
                ),
            ]
            searchable = " ".join(searchable_parts)
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            updated_at = self._updated_at(item["updateDate"])
            if not self._is_target_cycle(item, title, updated_at):
                continue
            jobs.append(
                self._to_job(item, title, locations, updated_at)
            )
        return jobs


class IbmEntryLevelCollector(Collector):
    """Collect China entry-level jobs from IBM's public search API."""

    SOURCE_FIELDS = [
        "_id",
        "title",
        "url",
        "description",
        "language",
        "entitled",
        "dcdate",
        "field_keyword_05",
        "field_keyword_08",
        "field_keyword_17",
        "field_keyword_18",
        "field_keyword_19",
    ]

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        lowered = str(text).lower()
        compacted = "".join(lowered.split())
        for keyword in keywords:
            normalized = " ".join(str(keyword).lower().split())
            if not normalized:
                continue
            if re.fullmatch(r"[a-z0-9+#.\- ]+", normalized):
                pattern = (
                    r"(?<![a-z0-9])"
                    + re.escape(normalized).replace(r"\ ", r"\s+")
                    + r"(?![a-z0-9])"
                )
                if re.search(pattern, lowered):
                    return True
            elif "".join(normalized.split()) in compacted:
                return True
        return False

    @staticmethod
    def _published_at(value: Any) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise ValueError("IBM 岗位缺少发布日期")
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("IBM 岗位发布日期格式异常") from exc

    def _request_body(self, offset: int, page_size: int) -> Dict[str, Any]:
        country = self.source.get("country_filter", "China")
        career_level = self.source.get(
            "career_level_filter", "Entry Level"
        )
        return {
            "appId": self.source.get("app_id", "careers"),
            "scopes": self.source.get("scopes", ["careers2"]),
            "query": {"bool": {"must": []}},
            "post_filter": {
                "bool": {
                    "must": [
                        {"term": {"field_keyword_05": country}},
                        {"term": {"field_keyword_18": career_level}},
                    ]
                }
            },
            "from": offset,
            "size": page_size,
            "sort": [{"dcdate": "desc"}, {"_score": "desc"}],
            "lang": self.source.get("language", "zz"),
            "localeSelector": {},
            "sm": {
                "query": "",
                "lang": self.source.get("language", "zz"),
            },
            "_source": self.SOURCE_FIELDS,
        }

    def _page(
        self, offset: int, page_size: int
    ) -> tuple[int, List[Dict[str, Any]]]:
        raw = fetch_bytes(
            self.source["url"],
            timeout=int(self.source.get("timeout", 40)),
            method="POST",
            json_body=self._request_body(offset, page_size),
            headers={
                "Accept": "application/json",
                "Origin": "https://www.ibm.com",
                "Referer": self.source.get(
                    "search_page", "https://www.ibm.com/careers/search"
                ),
            },
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("IBM 岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("timed_out") is True:
            raise ValueError("IBM 岗位接口响应状态异常")
        hits_block = payload.get("hits")
        if not isinstance(hits_block, dict):
            raise ValueError("IBM 岗位接口响应结构异常")
        total_block = hits_block.get("total")
        hits = hits_block.get("hits")
        if (
            not isinstance(total_block, dict)
            or not isinstance(total_block.get("value"), int)
            or total_block.get("relation") != "eq"
            or not isinstance(hits, list)
            or any(not isinstance(item, dict) for item in hits)
        ):
            raise ValueError("IBM 岗位接口响应结构异常")
        total = total_block["value"]
        expected = min(page_size, max(total - offset, 0))
        if len(hits) != expected:
            raise ValueError("IBM 岗位接口没有完整返回筛选结果")
        return total, hits

    def _positions(self) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 100))
        max_results = int(self.source.get("max_results", 500))
        if page_size <= 0 or max_results <= 0:
            raise ValueError("IBM 岗位分页参数必须为正整数")
        page_size = min(page_size, 100)

        positions = []
        total = None
        while total is None or len(positions) < total:
            current_total, page = self._page(len(positions), page_size)
            if current_total < 0 or current_total > max_results:
                raise ValueError("IBM 岗位数量超过配置上限")
            if total is not None and current_total != total:
                raise ValueError("IBM 岗位接口分页总数发生变化")
            total = current_total
            positions.extend(page)

        ids = [str(item.get("_id", "") or "").strip() for item in positions]
        if any(not external_id for external_id in ids):
            raise ValueError("IBM 岗位缺少稳定 ID")
        if len(ids) != len(set(ids)):
            raise ValueError("IBM 岗位接口返回了重复 ID")
        return positions

    def _location(self, value: str) -> str | None:
        mapping = self.source.get("location_map", {})
        for keyword in self.source.get("location_keywords", []):
            if self._contains(value, [keyword]):
                return str(mapping.get(keyword, keyword))
        generic = self.source.get(
            "generic_location_keywords", ["Multiple Cities"]
        )
        if (
            self.source.get("allow_generic_location", True)
            and self._contains(value, generic)
        ):
            return self.source.get(
                "generic_location",
                "中国大陆（官网标注多城市，具体地点待核对）",
            )
        return None

    def _target_cycle(
        self, title: str, published_at: datetime
    ) -> tuple[bool, List[int]]:
        start = datetime.strptime(
            self.source["target_published_start"], "%Y-%m-%d"
        ).date()
        end = datetime.strptime(
            self.source["target_published_end"], "%Y-%m-%d"
        ).date()
        if not start <= published_at.date() <= end:
            return False, []
        years = {
            int(year)
            for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", title)
        }
        target_years = {
            int(year) for year in self.source.get("target_cycle_years", [])
        }
        if years:
            matched = sorted(years.intersection(target_years))
            return bool(matched), matched
        return True, []

    def _to_job(
        self,
        item: Dict[str, Any],
        source: Dict[str, Any],
        location: str,
        published_at: datetime,
        graduation_years: List[int],
    ) -> JobPosting:
        detail_url = str(source["url"]).strip()
        parsed = urlsplit(detail_url)
        job_ids = parse_qs(parsed.query).get("jobId", [])
        if (
            parsed.scheme != "https"
            or parsed.netloc != "careers.ibm.com"
            or parsed.path.rstrip("/") != "/careers/JobDetail"
            or len(job_ids) != 1
            or not job_ids[0].strip()
        ):
            raise ValueError("IBM 岗位详情链接结构异常")
        description_parts = [
            "职业级别：{}".format(source["field_keyword_18"]),
            "方向：{}".format(source["field_keyword_08"]),
            "办公方式：{}".format(source.get("field_keyword_17", ""))
            if source.get("field_keyword_17")
            else "",
            str(source.get("description", "") or "").strip(),
        ]
        values = {
            "external_id": job_ids[0].strip() or item["_id"],
            "title": str(source["title"]).strip(),
            "company": self.source.get("company", "IBM 中国"),
            "company_type": self.source.get("company_type", "外企"),
            "location": location,
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self.source.get("education", ""),
            "graduation_years": graduation_years,
            "published_at": published_at.date().isoformat(),
            "deadline": self.source.get("deadline", ""),
            "url": detail_url,
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        expected_country = self.source.get("country_filter", "China")
        expected_level = self.source.get(
            "career_level_filter", "Entry Level"
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_title_keywords = self.source.get(
            "exclude_title_keywords", []
        )
        jobs = []
        for item in self._positions():
            source = item.get("_source")
            required = (
                "title",
                "url",
                "dcdate",
                "field_keyword_05",
                "field_keyword_08",
                "field_keyword_18",
                "field_keyword_19",
            )
            if (
                not isinstance(source, dict)
                or any(
                    not str(source.get(field, "") or "").strip()
                    for field in required
                )
            ):
                raise ValueError("IBM 岗位缺少必要字段")
            if (
                source["field_keyword_05"] != expected_country
                or source["field_keyword_18"] != expected_level
            ):
                raise ValueError("IBM 岗位接口返回了非目标筛选条件的岗位")

            title = str(source["title"]).strip()
            if self._contains(title, exclude_title_keywords):
                continue
            location = self._location(str(source["field_keyword_19"]))
            if location is None:
                continue
            searchable = " ".join(
                [
                    title,
                    str(source.get("description", "") or ""),
                    str(source["field_keyword_08"]),
                ]
            )
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            published_at = self._published_at(source["dcdate"])
            target_cycle, graduation_years = self._target_cycle(
                title, published_at
            )
            if not target_cycle:
                continue
            jobs.append(
                self._to_job(
                    item,
                    source,
                    location,
                    published_at,
                    graduation_years,
                )
            )
        return jobs


class HuaweiCampusCollector(Collector):
    """Collect target-cycle overseas graduate jobs from Huawei's public API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        lowered = str(text).lower()
        compacted = "".join(lowered.split())
        for keyword in keywords:
            normalized = " ".join(str(keyword).lower().split())
            if not normalized:
                continue
            if re.fullmatch(r"[a-z0-9+#.\- ]+", normalized):
                pattern = (
                    r"(?<![a-z0-9])"
                    + re.escape(normalized).replace(r"\ ", r"\s+")
                    + r"(?![a-z0-9])"
                )
                if re.search(pattern, lowered):
                    return True
            elif "".join(normalized.split()) in compacted:
                return True
        return False

    @staticmethod
    def _date(value: Any, label: str) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise ValueError("华为岗位缺少{}".format(label))
        for date_format in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, date_format)
            except ValueError:
                continue
        raise ValueError("华为岗位{}格式异常".format(label))

    def _campaign_started(self) -> bool:
        raw = fetch_bytes(
            self.source.get(
                "announcement_url",
                (
                    "https://career.huawei.com/reccampportal/"
                    "portal5/news.html"
                ),
            ),
            timeout=int(self.source.get("timeout", 40)),
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Referer": "https://career.huawei.com/",
            },
        )
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("华为校园招聘页返回了无效 UTF-8") from exc
        required_text = str(
            self.source.get("required_text", "news-bulletin-list")
        ).strip()
        if required_text and required_text not in body:
            raise ValueError("华为招聘公告页缺少预期标识，可能已经改版")
        raw_titles = re.findall(
            r'\btitle\s*:\s*"((?:[^"\\]|\\.)*)"', body
        )
        if not raw_titles:
            raise ValueError("华为招聘公告页缺少公告列表")
        titles = []
        for raw_title in raw_titles:
            try:
                titles.append(json.loads('"{}"'.format(raw_title)))
            except json.JSONDecodeError as exc:
                raise ValueError("华为招聘公告标题结构异常") from exc
        launch_markers = self.source.get(
            "launch_markers", ["华为2027届应届生招聘启动"]
        )
        return any(
            self._contains(title, launch_markers) for title in titles
        )

    def _page(
        self, page: int, page_size: int
    ) -> tuple[int, int, List[Dict[str, Any]]]:
        query = {
            "jobTypes": self.source.get("job_types", "1"),
            "jobType": self.source.get("job_type", "0"),
            "jobFamClsCode": "",
            "searchText": "",
            "cityCode": "",
            "countryCode": "",
            "graduateItem": "",
            "language": self.source.get("language", "zh_CN"),
            "orderBy": "ISS_STARTDATE_DESC_AND_IS_HOT_JOB",
        }
        url = "{}/{}/{}?{}".format(
            self.source["url"].rstrip("/"),
            page_size,
            page,
            urlencode(query),
        )
        raw = fetch_bytes(
            url,
            timeout=int(self.source.get("timeout", 40)),
            headers={
                "Accept": "application/json",
                "Referer": self.source["homepage"],
                "x-jalor-tenantAlias": self.source.get(
                    "tenant_alias", "hcm"
                ),
            },
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("华为岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("华为岗位接口响应结构异常")
        page_vo = payload.get("pageVO")
        positions = payload.get("result")
        if (
            not isinstance(page_vo, dict)
            or not isinstance(page_vo.get("totalRows"), int)
            or not isinstance(page_vo.get("totalPages"), int)
            or not isinstance(page_vo.get("curPage"), int)
            or not isinstance(positions, list)
            or any(not isinstance(item, dict) for item in positions)
        ):
            raise ValueError("华为岗位接口响应结构异常")
        total = page_vo["totalRows"]
        total_pages = page_vo["totalPages"]
        expected_pages = (total + page_size - 1) // page_size
        expected_size = min(page_size, max(total - (page - 1) * page_size, 0))
        if (
            total < 0
            or page_vo["curPage"] != page
            or total_pages != expected_pages
            or len(positions) != expected_size
        ):
            raise ValueError("华为岗位接口没有完整返回筛选结果")
        return total, total_pages, positions

    def _positions(self) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 50))
        max_results = int(self.source.get("max_results", 500))
        if page_size <= 0 or max_results <= 0:
            raise ValueError("华为岗位分页参数必须为正整数")
        page_size = min(page_size, 100)

        positions = []
        expected_total = None
        expected_pages = None
        page = 1
        while expected_pages is None or page <= expected_pages:
            total, total_pages, current = self._page(page, page_size)
            if total > max_results:
                raise ValueError("华为岗位数量超过配置上限")
            if expected_total is not None and (
                total != expected_total or total_pages != expected_pages
            ):
                raise ValueError("华为岗位接口分页总数发生变化")
            expected_total = total
            expected_pages = total_pages
            positions.extend(current)
            page += 1

        if expected_total is None or len(positions) != expected_total:
            raise ValueError("华为岗位接口没有完整返回筛选结果")
        ids = [
            str(item.get("advertisementCode", "") or "").strip()
            for item in positions
        ]
        if any(not external_id for external_id in ids):
            raise ValueError("华为岗位缺少稳定 ID")
        if len(ids) != len(set(ids)):
            raise ValueError("华为岗位接口返回了重复 ID")
        return positions

    def _location(self, item: Dict[str, Any]) -> str | None:
        searchable = "{} {}".format(
            item.get("jobArea", ""), item.get("jobAddress", "")
        )
        mapping = self.source.get("location_map", {})
        locations = []
        for keyword in self.source.get("location_keywords", []):
            if self._contains(searchable, [keyword]):
                location = str(mapping.get(keyword, keyword))
                if location not in locations:
                    locations.append(location)
        return "/".join(locations) if locations else None

    def _target_cycle(
        self, title: str, published_at: datetime
    ) -> bool:
        start = datetime.strptime(
            self.source["target_published_start"], "%Y-%m-%d"
        ).date()
        end = datetime.strptime(
            self.source["target_published_end"], "%Y-%m-%d"
        ).date()
        if not start <= published_at.date() <= end:
            return False
        years = {
            int(year)
            for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", title)
        }
        if not years:
            return True
        target_years = {
            int(year) for year in self.source.get("target_cycle_years", [])
        }
        return bool(years.intersection(target_years))

    def _to_job(
        self,
        item: Dict[str, Any],
        title: str,
        location: str,
        published_at: datetime,
        deadline: datetime,
    ) -> JobPosting:
        job_id = str(item["jobId"]).strip()
        data_source = str(item["dataSource"]).strip()
        query = urlencode(
            {
                "dataSource": data_source,
                "jobId": job_id,
                "jobType": self.source.get("detail_job_type", "2"),
                "recruitType": "CR",
                "sourceType": "001",
            }
        )
        detail_url = "{}?{}".format(
            self.source.get(
                "detail_url",
                (
                    "https://career.huawei.com/reccampportal/portal5/"
                    "campus-recruitment-detail.html"
                ),
            ),
            query,
        )
        parsed = urlsplit(detail_url)
        detail_query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "career.huawei.com"
            or not parsed.path.endswith("/campus-recruitment-detail.html")
            or detail_query.get("jobId") != [job_id]
            or detail_query.get("dataSource") != [data_source]
        ):
            raise ValueError("华为岗位详情链接结构异常")
        description_parts = [
            "岗位族：{}".format(item.get("jobFamilyName", ""))
            if item.get("jobFamilyName")
            else "",
            str(item.get("mainBusiness", "") or "").strip(),
            str(item.get("jobRequire", "") or "").strip(),
        ]
        values = {
            "external_id": str(item["advertisementCode"]).strip(),
            "title": title,
            "company": self.source.get("company", "华为"),
            "company_type": self.source.get("company_type", "私企"),
            "location": location,
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get(
                "graduation_years", [2027]
            ),
            "published_at": published_at.date().isoformat(),
            "deadline": deadline.date().isoformat(),
            "url": detail_url,
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        if not self._campaign_started():
            return []

        expected_job_type = str(self.source.get("job_type", "0"))
        expected_priority = str(
            self.source.get("student_abroad_priority", "1")
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        jobs = []
        for item in self._positions():
            required = (
                "jobId",
                "advertisementCode",
                "jobname",
                "jobType",
                "studentAbroadPriority",
                "releaseDate",
                "expirationDate",
                "dataSource",
            )
            if any(
                item.get(field) is None
                or not str(item.get(field, "")).strip()
                for field in required
            ) or not (
                str(item.get("jobArea", "") or "").strip()
                or str(item.get("jobAddress", "") or "").strip()
            ):
                raise ValueError("华为岗位缺少必要字段")
            if (
                str(item["jobType"]) != expected_job_type
                or str(item["studentAbroadPriority"])
                != expected_priority
            ):
                raise ValueError("华为岗位接口返回了非目标筛选条件的岗位")

            title = str(item["jobname"]).strip()
            searchable = " ".join(
                [
                    title,
                    str(item.get("jobFamilyName", "") or ""),
                    str(item.get("mainBusiness", "") or ""),
                    str(item.get("jobRequire", "") or ""),
                ]
            )
            if self._contains(searchable, exclude_keywords):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            location = self._location(item)
            if location is None:
                continue
            published_at = self._date(item["releaseDate"], "发布日期")
            if not self._target_cycle(title, published_at):
                continue
            deadline = self._date(item["expirationDate"], "截止日期")
            jobs.append(
                self._to_job(
                    item,
                    title,
                    location,
                    published_at,
                    deadline,
                )
            )
        return jobs


class TencentCampusCollector(Collector):
    """Collect date-eligible graduate jobs from Tencent's public campus API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        lowered = str(text).lower()
        compacted = "".join(lowered.split())
        for keyword in keywords:
            normalized = " ".join(str(keyword).lower().split())
            if not normalized:
                continue
            if re.fullmatch(r"[a-z0-9+#.\- ]+", normalized):
                pattern = (
                    r"(?<![a-z0-9])"
                    + re.escape(normalized).replace(r"\ ", r"\s+")
                    + r"(?![a-z0-9])"
                )
                if re.search(pattern, lowered):
                    return True
            elif "".join(normalized.split()) in compacted:
                return True
        return False

    @staticmethod
    def _decode(raw: bytes, label: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("腾讯{}返回了无效 JSON".format(label)) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("status") != 0
            or not isinstance(payload.get("message"), str)
        ):
            raise ValueError("腾讯{}返回失败状态".format(label))
        return payload

    @staticmethod
    def _graduation_range(text: Any) -> tuple[datetime, datetime]:
        normalized = " ".join(str(text or "").split())
        match = re.search(
            (
                r"毕业时间[：:]\s*(20\d{2})年(\d{1,2})月"
                r"(?:(\d{1,2})日)?\s*[-—–至到~～]+\s*"
                r"(20\d{2})年(\d{1,2})月(?:(\d{1,2})日)?"
            ),
            normalized,
        )
        if not match:
            raise ValueError("腾讯招聘项目毕业时间范围格式异常")
        start_year, start_month, start_day, end_year, end_month, end_day = (
            int(value) if value else None for value in match.groups()
        )
        if not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
            raise ValueError("腾讯招聘项目毕业时间范围格式异常")
        start_day = start_day or 1
        end_day = end_day or calendar.monthrange(end_year, end_month)[1]
        try:
            start = datetime(start_year, start_month, start_day)
            end = datetime(end_year, end_month, end_day)
        except ValueError as exc:
            raise ValueError("腾讯招聘项目毕业时间范围格式异常") from exc
        if start > end:
            raise ValueError("腾讯招聘项目毕业时间范围格式异常")
        return start, end

    @staticmethod
    def _project_ids(value: Any) -> List[int]:
        values = [part.strip() for part in str(value or "").split(",")]
        if not values or any(not part.isdigit() for part in values):
            raise ValueError("腾讯招聘项目缺少有效项目 ID")
        return [int(part) for part in values]

    def _projects(self) -> tuple[List[int], Dict[int, Dict[str, Any]]]:
        payload = self._decode(
            fetch_bytes(
                self.source.get(
                    "project_url",
                    "https://join.qq.com/api/v1/position/getProjectMapping",
                ),
                timeout=int(self.source.get("timeout", 40)),
                headers={
                    "Accept": "application/json",
                    "Referer": self.source["homepage"],
                },
            ),
            "招聘项目接口",
        )
        groups = payload.get("data")
        if (
            not isinstance(groups, list)
            or any(not isinstance(group, dict) for group in groups)
        ):
            raise ValueError("腾讯招聘项目接口响应结构异常")

        try:
            target_date = datetime.strptime(
                self.source["target_graduation_date"], "%Y-%m-%d"
            )
        except (KeyError, ValueError) as exc:
            raise ValueError("腾讯目标毕业日期格式异常") from exc
        include_keywords = self.source.get(
            "project_include_keywords", ["校园招聘", "应届生"]
        )
        exclude_keywords = self.source.get(
            "project_exclude_keywords", ["实习"]
        )
        selected_mapping_ids = []
        project_meta: Dict[int, Dict[str, Any]] = {}
        for group in groups:
            if (
                not isinstance(group.get("status"), int)
                or not isinstance(group.get("subProjectList"), list)
            ):
                raise ValueError("腾讯招聘项目接口响应结构异常")
            if group["status"] != 1:
                continue
            for project in group["subProjectList"]:
                if not isinstance(project, dict):
                    raise ValueError("腾讯招聘项目接口响应结构异常")
                required = (
                    "mappingId",
                    "projectId",
                    "projectName",
                    "recruitYear",
                    "status",
                    "recruitRangDesc",
                )
                if any(
                    project.get(field) is None
                    or not str(project.get(field, "")).strip()
                    for field in required
                ):
                    raise ValueError("腾讯招聘项目缺少必要字段")
                if not isinstance(project["mappingId"], int) or not isinstance(
                    project["status"], int
                ):
                    raise ValueError("腾讯招聘项目接口响应结构异常")
                if project["status"] != 1:
                    continue

                name = str(project["projectName"]).strip()
                if self._contains(name, exclude_keywords):
                    continue
                if include_keywords and not self._contains(
                    name, include_keywords
                ):
                    continue
                start, end = self._graduation_range(
                    project["recruitRangDesc"]
                )
                if not start <= target_date <= end:
                    continue

                years = list(range(start.year, end.year + 1))
                selected_mapping_ids.append(project["mappingId"])
                for project_id in self._project_ids(project["projectId"]):
                    current = project_meta.setdefault(
                        project_id,
                        {
                            "names": [],
                            "graduation_years": [],
                        },
                    )
                    if name not in current["names"]:
                        current["names"].append(name)
                    for year in years:
                        if year not in current["graduation_years"]:
                            current["graduation_years"].append(year)

        if len(selected_mapping_ids) != len(set(selected_mapping_ids)):
            raise ValueError("腾讯招聘项目接口返回了重复映射 ID")
        return selected_mapping_ids, project_meta

    def _page(
        self,
        mapping_ids: List[int],
        page: int,
        page_size: int,
    ) -> tuple[int, List[Dict[str, Any]]]:
        request_json = {
            "projectIdList": [],
            "projectMappingIdList": mapping_ids,
            "keyword": "",
            "bgList": [],
            "workCountryType": 1,
            "workCityList": [],
            "recruitCityList": [],
            "positionFidList": [],
            "pageIndex": page,
            "pageSize": page_size,
        }
        payload = self._decode(
            fetch_bytes(
                self.source.get(
                    "url",
                    "https://join.qq.com/api/v1/position/searchPosition",
                ),
                timeout=int(self.source.get("timeout", 40)),
                method="POST",
                json_body=request_json,
                headers={
                    "Accept": "application/json",
                    "Referer": self.source["homepage"],
                },
            ),
            "岗位搜索接口",
        )
        data = payload.get("data")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("count"), int)
            or not isinstance(data.get("positionList"), list)
            or any(
                not isinstance(item, dict)
                for item in data.get("positionList", [])
            )
        ):
            raise ValueError("腾讯岗位搜索接口响应结构异常")
        total = data["count"]
        positions = data["positionList"]
        expected_size = min(
            page_size, max(total - (page - 1) * page_size, 0)
        )
        if total < 0 or len(positions) != expected_size:
            raise ValueError("腾讯岗位接口没有完整返回筛选结果")
        return total, positions

    def _positions(
        self, mapping_ids: List[int]
    ) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 100))
        max_results = int(self.source.get("max_results", 1000))
        if page_size <= 0 or max_results <= 0:
            raise ValueError("腾讯岗位分页参数必须为正整数")
        page_size = min(page_size, 1000)

        positions = []
        expected_total = None
        page = 1
        while expected_total is None or len(positions) < expected_total:
            total, current = self._page(
                mapping_ids, page, page_size
            )
            if total > max_results:
                raise ValueError("腾讯岗位数量超过配置上限")
            if expected_total is not None and total != expected_total:
                raise ValueError("腾讯岗位接口分页总数发生变化")
            expected_total = total
            positions.extend(current)
            page += 1

        if expected_total is None or len(positions) != expected_total:
            raise ValueError("腾讯岗位接口没有完整返回筛选结果")
        ids = [
            str(item.get("postId", "") or "").strip()
            for item in positions
        ]
        if any(not external_id for external_id in ids):
            raise ValueError("腾讯岗位缺少稳定 ID")
        if len(ids) != len(set(ids)):
            raise ValueError("腾讯岗位接口返回了重复 ID")
        return positions

    def _location(self, value: Any) -> str | None:
        locations = []
        mapping = self.source.get("location_map", {})
        for keyword in self.source.get("location_keywords", []):
            if self._contains(value, [keyword]):
                location = str(mapping.get(keyword, keyword))
                if location not in locations:
                    locations.append(location)
        return "/".join(locations) if locations else None

    def _to_job(
        self,
        item: Dict[str, Any],
        title: str,
        location: str,
        meta: Dict[str, Any],
    ) -> JobPosting:
        post_id = str(item["postId"]).strip()
        detail_url = "{}?{}".format(
            self.source.get(
                "detail_url", "https://join.qq.com/post_detail.html"
            ),
            urlencode({"postid": post_id}),
        )
        parsed = urlsplit(detail_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "join.qq.com"
            or not parsed.path.endswith("/post_detail.html")
            or parse_qs(parsed.query).get("postid") != [post_id]
        ):
            raise ValueError("腾讯岗位详情链接结构异常")
        description_parts = [
            "招聘项目：{}".format("/".join(meta["names"])),
            "岗位族：{}".format(item.get("positionFamily", ""))
            if item.get("positionFamily")
            else "",
            "事业群：{}".format(item.get("bgs", ""))
            if item.get("bgs")
            else "",
            "招聘标签：{}".format(item.get("recruitLabelName", "")),
        ]
        values = {
            "external_id": post_id,
            "title": title,
            "company": self.source.get("company", "腾讯"),
            "company_type": self.source.get("company_type", "私企"),
            "location": location,
            "description": "；".join(
                part for part in description_parts if part
            ),
            "education": self.source.get("education", ""),
            "graduation_years": meta["graduation_years"],
            "published_at": "",
            "deadline": self.source.get(
                "deadline", "以官方项目页面为准"
            ),
            "url": detail_url,
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        mapping_ids, project_meta = self._projects()
        if not mapping_ids:
            return []

        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        project_exclude_keywords = self.source.get(
            "project_exclude_keywords", ["实习"]
        )
        jobs = []
        for item in self._positions(mapping_ids):
            required = (
                "id",
                "postId",
                "positionTitle",
                "projectId",
                "projectName",
                "recruitLabelName",
                "workCities",
            )
            if any(
                item.get(field) is None
                or not str(item.get(field, "")).strip()
                for field in required
            ):
                raise ValueError("腾讯岗位缺少必要字段")
            try:
                project_id = int(item["projectId"])
            except (TypeError, ValueError) as exc:
                raise ValueError("腾讯岗位项目 ID 格式异常") from exc
            if project_id not in project_meta or self._contains(
                "{} {}".format(
                    item["projectName"], item["recruitLabelName"]
                ),
                project_exclude_keywords,
            ):
                raise ValueError("腾讯岗位接口返回了非目标招聘项目")

            title = str(item["positionTitle"]).strip()
            searchable = " ".join(
                [
                    title,
                    str(item.get("positionFamily", "") or ""),
                    str(item.get("bgs", "") or ""),
                ]
            )
            if self._contains(searchable, exclude_keywords):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            location = self._location(item["workCities"])
            if location is None:
                continue
            jobs.append(
                self._to_job(
                    item,
                    title,
                    location,
                    project_meta[project_id],
                )
            )
        return jobs


class HotjobCampusCollector(Collector):
    """Collect target-cycle jobs from a public Dayee/Hotjob campus portal."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _decode_page(raw: bytes) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Hotjob 校招接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or str(payload.get("state")) != "200":
            raise ValueError("Hotjob 校招接口返回失败状态")
        data = payload.get("data")
        page_form = data.get("pageForm") if isinstance(data, dict) else None
        if not isinstance(page_form, dict):
            raise ValueError("Hotjob 校招接口缺少 pageForm")
        page_data = page_form.get("pageData")
        if not isinstance(page_data, list):
            raise ValueError("Hotjob 校招接口缺少岗位数组")
        return page_form

    def _fetch_page(self, page: int) -> Dict[str, Any]:
        tenant_id = self.source["tenant_id"]
        url = self.source.get(
            "url",
            (
                "https://wecruit.hotjob.cn/wecruit/positionInfo/"
                "listPosition/{}?iSaJAx=isAjax&request_locale=zh_CN"
            ).format(tenant_id),
        )
        return self._decode_page(
            fetch_bytes(
                url,
                method="POST",
                form_body={
                    "isFrompb": "true",
                    "recruitType": self.source.get("recruit_type", 1),
                    "pageSize": self.source.get("page_size", 15),
                    "currentPage": page,
                },
                headers={
                    "Referer": self.source["homepage"],
                },
            )
        )

    def collect(self) -> List[JobPosting]:
        target_keywords = self.source.get("target_keywords", [])
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        min_published_at = self.source.get("min_published_at", "")
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        homepage = self.source["homepage"]

        jobs = []
        seen_ids = set()
        page = 1
        while page <= max_pages:
            page_form = self._fetch_page(page)
            items = page_form["pageData"]
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Hotjob 校招岗位元素结构异常")
                post_id = str(item.get("postId") or "")
                title = str(item.get("postName") or "")
                if not post_id or not title:
                    raise ValueError("Hotjob 校招岗位缺少 ID 或标题")
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                published_at = str(item.get("publishFirstDate") or "")
                project_name = str(item.get("projectName") or "")
                if self.source.get("prefer_source_company"):
                    company = str(
                        self.source.get("company", self.source["name"])
                    )
                else:
                    company = str(
                        item.get("company")
                        or self.source.get("company", self.source["name"])
                    )
                location = str(
                    item.get("workPlaceStr")
                    or self.source.get("location", "待核对")
                )
                searchable = " ".join(
                    [
                        project_name,
                        title,
                        str(item.get("postTypeName") or ""),
                        company,
                        str(item.get("department") or ""),
                    ]
                )
                if target_keywords and not self._contains(
                    searchable, target_keywords
                ):
                    continue
                if min_published_at and (
                    not published_at
                    or published_at[:10] < min_published_at[:10]
                ):
                    continue
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                if include_keywords and not self._contains(
                    searchable, include_keywords
                ):
                    continue
                if self._contains(searchable, exclude_keywords):
                    continue

                deadline = str(item.get("endDate") or "")
                if deadline.startswith("3000-"):
                    deadline = "长期招聘"
                description_parts = []
                if not self.source.get("omit_post_type_name"):
                    description_parts.append(
                        str(item.get("postTypeName") or "")
                    )
                description_parts.extend(
                    [
                        project_name,
                        str(item.get("department") or ""),
                    ]
                )
                description_parts = list(
                    dict.fromkeys(
                        part
                        for part in description_parts
                        if part and part != title
                    )
                )
                values = {
                    "external_id": post_id,
                    "title": title,
                    "company": company,
                    "company_type": self.source.get(
                        "company_type", "未知"
                    ),
                    "location": location,
                    "description": "｜".join(description_parts)
                    or self.source.get("description", ""),
                    "education": str(item.get("educationStr") or ""),
                    "graduation_years": self.source.get(
                        "graduation_years", []
                    ),
                    "published_at": published_at,
                    "deadline": deadline,
                    "url": self.source.get(
                        "url_template",
                        homepage
                        + "?postId={postId}&postType=campus",
                    ).format_map(_MissingValueDict(item)),
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))

            try:
                total_page = int(page_form.get("totalPage", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("Hotjob 校招接口分页字段异常") from exc
            if page >= total_page:
                break
            page += 1
        else:
            raise ValueError("Hotjob 校招接口分页超过配置上限")

        return jobs


class TclHotjobCampusCollector(Collector):
    """Collect one TCL business unit from its legacy public Hotjob portal."""

    @staticmethod
    def _contains(text: Any, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text or "").lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _date(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        if not match:
            raise ValueError("TCL华星岗位{}格式异常".format(label))
        return match.group(1)

    def _validate_portal(self) -> None:
        body = fetch_bytes(self.source["homepage"]).decode(
            "utf-8", errors="replace"
        )
        for keyword in self.source.get(
            "portal_required_keywords", ["TCL", "校园招聘"]
        ):
            if keyword not in body:
                raise ValueError("TCL校招门户缺少预期标识，可能已经改版")
        company_part = str(self.source["company_part"])
        expected_company = self.source["required_company_name"]
        if company_part not in body or expected_company not in body:
            raise ValueError("TCL校招门户未出现目标事业部")

    def _page(self, page: int) -> Dict[str, Any]:
        recruit_type = int(self.source.get("recruit_type", 1))
        query = urlencode(
            {
                "positionType": "",
                "comPart": self.source["company_part"],
                "sicCorpCode": "",
                "brandCode": self.source.get("brand_code", 1),
                "releaseTime": "",
                "trademark": self.source.get("trademark", 1),
                "useForm": "",
                "recruitType": recruit_type,
                "projectId": "",
                "lanType": self.source.get("language_type", 1),
                "positionName": "",
                "workPlace": "",
                "page": page,
                "site": "",
                "keyWord": "",
            }
        )
        separator = "&" if "?" in self.source["url"] else "?"
        raw = fetch_bytes(
            self.source["url"] + separator + query,
            headers={
                "Accept": "application/json",
                "Referer": self.source["homepage"],
            },
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("TCL华星校招接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("req_state") != 9200:
            raise ValueError("TCL华星校招接口返回失败状态")
        try:
            current_page = int(payload["page"])
            page_count = int(payload["pageCount"])
            row_count = int(payload["rowCount"])
            row_size = int(payload["rowSize"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("TCL华星校招接口分页字段异常") from exc
        items = payload.get("postList")
        if (
            current_page != page
            or page_count < 0
            or row_count < 0
            or row_size <= 0
            or not isinstance(items, list)
            or len(items) > row_size
            or any(not isinstance(item, dict) for item in items)
        ):
            raise ValueError("TCL华星校招接口分页结构异常")
        expected_pages = (
            (row_count + row_size - 1) // row_size if row_count else 0
        )
        if page_count != expected_pages:
            raise ValueError("TCL华星校招接口页数与岗位总数不一致")
        return {
            "items": items,
            "page_count": page_count,
            "row_count": row_count,
        }

    def _graduation_years(self, text: str) -> List[int]:
        graduation_date = datetime.strptime(
            self.source["graduation_date"], "%Y-%m-%d"
        ).date()
        ranges = re.findall(
            r"(20\d{2})年(\d{1,2})月\s*[-—–至~～]\s*"
            r"(20\d{2})年(\d{1,2})月",
            text,
        )
        for start_year, start_month, end_year, end_month in ranges:
            start = datetime(
                int(start_year), int(start_month), 1
            ).date()
            last_day = calendar.monthrange(
                int(end_year), int(end_month)
            )[1]
            end = datetime(
                int(end_year), int(end_month), last_day
            ).date()
            if start <= graduation_date <= end:
                return [graduation_date.year]
        if ranges:
            return []
        if self._contains(
            text, self.source.get("target_cycle_keywords", [])
        ):
            return [
                int(year)
                for year in self.source.get("graduation_years", [])
            ]
        return []

    def _detail_url(self, item: Dict[str, Any]) -> str:
        return self.source["detail_url_template"].format_map(
            _MissingValueDict(item)
        )

    def _is_open(self, item: Dict[str, Any]) -> bool:
        body = fetch_bytes(
            self._detail_url(item),
            headers={"Referer": self.source["homepage"]},
        ).decode("utf-8", errors="replace")
        if self._contains(
            body,
            self.source.get(
                "closed_markers",
                ["该职位招聘已经关闭", "该职位已关闭", "职位已下线"],
            ),
        ):
            return False
        if (
            str(item["postName"]) not in body
            or self.source["required_company_name"] not in body
        ):
            raise ValueError("TCL华星岗位详情页缺少目标岗位标识")
        return True

    def collect(self) -> List[JobPosting]:
        self._validate_portal()
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        expected_company_id = str(self.source["company_part"])
        expected_company = self.source["required_company_name"]
        recruit_type = str(self.source.get("recruit_type", 1))
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        min_published_at = self.source.get("min_published_at", "")[:10]
        reference_date = datetime.strptime(
            self.source.get(
                "reference_date", datetime.now().date().isoformat()
            ),
            "%Y-%m-%d",
        ).date()

        jobs = []
        seen_ids = set()
        expected_total = None
        page_count = None
        for page in range(1, max_pages + 1):
            result = self._page(page)
            if expected_total is None:
                expected_total = result["row_count"]
                page_count = result["page_count"]
                if page_count > max_pages:
                    raise ValueError("TCL华星校招接口分页超过配置上限")
            elif (
                result["row_count"] != expected_total
                or result["page_count"] != page_count
            ):
                raise ValueError("TCL华星校招接口分页总数发生变化")

            for item in result["items"]:
                external_id = str(item.get("postId") or "").strip()
                title = str(item.get("postName") or "").strip()
                company_id = str(item.get("orgId") or "").strip()
                company = str(item.get("orgName") or "").strip()
                if not external_id or not title:
                    raise ValueError("TCL华星校招岗位缺少 ID 或标题")
                if external_id in seen_ids:
                    raise ValueError("TCL华星校招接口返回了重复岗位 ID")
                seen_ids.add(external_id)
                if (
                    company_id != expected_company_id
                    or company != expected_company
                    or str(item.get("deptOrgName") or "").strip()
                    != expected_company
                    or str(item.get("recruitType") or "").strip()
                    != recruit_type
                ):
                    raise ValueError("TCL华星校招接口混入非目标事业部岗位")

                work_type = str(item.get("workType") or "").strip()
                if work_type not in self.source.get(
                    "work_types", ["全职"]
                ):
                    continue
                location = str(item.get("workPlace") or "").strip()
                duties = _html_fragment_text(item.get("workContent", ""))
                requirements = _html_fragment_text(
                    item.get("serviceCondition", "")
                )
                post_type = str(item.get("postType") or "").strip()
                searchable = " ".join(
                    [title, post_type, location, duties, requirements]
                )
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                if include_keywords and not self._contains(
                    searchable, include_keywords
                ):
                    continue
                if self._contains(searchable, exclude_keywords):
                    continue

                published_at = self._date(
                    item.get("publishDate"), "发布日期"
                )
                if min_published_at and (
                    not published_at or published_at < min_published_at
                ):
                    continue
                deadline = self._date(item.get("endDate"), "截止日期")
                if deadline and not deadline.startswith("3000-"):
                    deadline_date = datetime.strptime(
                        deadline, "%Y-%m-%d"
                    ).date()
                    if deadline_date < reference_date:
                        continue
                graduation_years = self._graduation_years(requirements)
                if not graduation_years:
                    continue
                if not self._is_open(item):
                    continue

                values = {
                    "external_id": external_id,
                    "title": title,
                    "company": self.source.get("company", company),
                    "company_type": self.source.get("company_type", "私企"),
                    "location": location.replace(",", "、"),
                    "description": "｜".join(
                        part
                        for part in [
                            "岗位类别：{}".format(post_type)
                            if post_type
                            else "",
                            "岗位职责：{}".format(duties) if duties else "",
                            "任职要求：{}".format(requirements)
                            if requirements
                            else "",
                        ]
                        if part
                    ),
                    "education": self.source.get(
                        "education", "本科及以上，具体要求以岗位为准"
                    ),
                    "graduation_years": graduation_years,
                    "published_at": published_at,
                    "deadline": "长期招聘"
                    if deadline.startswith("3000-")
                    else deadline,
                    "url": self._detail_url(item),
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))

            if page_count == 0 or page >= page_count:
                break
        else:
            raise ValueError("TCL华星校招接口分页超过配置上限")

        if expected_total is None or len(seen_ids) != expected_total:
            raise ValueError("TCL华星校招接口没有完整返回全部岗位")
        return jobs


class HonorCampusCollector(HotjobCampusCollector):
    """Validate HONOR's official campaign page before reading campus jobs."""

    def collect(self) -> List[JobPosting]:
        campaign_url = self.source["campaign_url"]
        try:
            campaign_html = fetch_bytes(
                campaign_url,
                timeout=int(self.source.get("campaign_timeout", 60)),
                headers=self.source.get("campaign_headers"),
            ).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("荣耀招聘官网返回了无效 UTF-8 页面") from exc

        required_text = self.source.get("required_text", "校招入口")
        expected_portal_url = self.source["expected_portal_url"]
        if (
            required_text not in campaign_html
            or expected_portal_url not in campaign_html
        ):
            raise ValueError("荣耀招聘官网未出现预期校招入口，可能已经改版")

        target_campaign_keywords = self.source.get(
            "target_campaign_keywords", []
        )
        if target_campaign_keywords and not self._contains(
            campaign_html, target_campaign_keywords
        ):
            return []

        return super().collect()


class GiihgCampusCollector(Collector):
    """Collect campus jobs from Guangzhou Industrial Investment Group."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _decode_page(raw: bytes) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("广州工控招聘接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise ValueError("广州工控招聘接口返回失败状态")
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError("广州工控招聘接口缺少岗位数组")
        try:
            total = int(payload["total"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("广州工控招聘接口岗位总数字段异常") from exc
        if total < 0:
            raise ValueError("广州工控招聘接口岗位总数不能为负数")
        return {"rows": rows, "total": total}

    def _fetch_page(self, page: int) -> Dict[str, Any]:
        params = urlencode(
            {
                "type": self.source.get("recruit_type", 1),
                "pageNum": page,
                "pageSize": self.source.get("page_size", 100),
            }
        )
        separator = "&" if "?" in self.source["url"] else "?"
        return self._decode_page(
            fetch_bytes(
                self.source["url"] + separator + params,
                headers={"Referer": self.source["homepage"]},
            )
        )

    def collect(self) -> List[JobPosting]:
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        min_published_at = self.source.get("min_published_at", "")
        recruit_type = str(self.source.get("recruit_type", 1))
        page_size = max(1, int(self.source.get("page_size", 100)))
        max_pages = max(1, int(self.source.get("max_pages", 10)))

        jobs = []
        seen_ids = set()
        expected_total = None
        for page in range(1, max_pages + 1):
            page_result = self._fetch_page(page)
            rows = page_result["rows"]
            if expected_total is None:
                expected_total = page_result["total"]
            if not rows:
                break

            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError("广州工控招聘岗位元素结构异常")
                external_id = str(item.get("id") or "")
                title = _html_fragment_text(item.get("jobName", ""))
                if not external_id or not title:
                    raise ValueError("广州工控招聘岗位缺少 ID 或岗位名称")
                if str(item.get("type")) != recruit_type:
                    raise ValueError("广州工控招聘接口返回了非校园招聘岗位")
                if str(item.get("isDisplay", "1")) != "1" or str(
                    item.get("isDel", "0")
                ) != "0":
                    continue
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)

                published_at = str(
                    item.get("publishTime")
                    or item.get("createTime")
                    or ""
                )
                if min_published_at and (
                    not published_at
                    or published_at[:10] < min_published_at[:10]
                ):
                    continue
                company = _html_fragment_text(
                    item.get("companyName")
                    or self.source.get("company", self.source["name"])
                )
                location = _html_fragment_text(
                    item.get("address")
                    or self.source.get("location", "待核对")
                )
                duties = _html_fragment_text(item.get("jobContent", ""))
                requirements = _html_fragment_text(item.get("jobDesc", ""))
                other_info = _html_fragment_text(item.get("otherInfo", ""))
                searchable = " ".join(
                    [
                        title,
                        company,
                        duties,
                        requirements,
                        other_info,
                    ]
                )
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                if include_keywords and not self._contains(
                    searchable, include_keywords
                ):
                    continue
                if self._contains(searchable, exclude_keywords):
                    continue

                description_parts = []
                if duties:
                    description_parts.append("岗位职责：" + duties)
                if requirements:
                    description_parts.append("任职要求：" + requirements)
                if other_info:
                    description_parts.append(other_info)
                values = {
                    "external_id": external_id,
                    "title": title,
                    "company": company,
                    "company_type": self.source.get(
                        "company_type", "未知"
                    ),
                    "location": location,
                    "description": "｜".join(description_parts),
                    "education": self.source.get(
                        "education",
                        "校园招聘，具体学历要求见任职要求",
                    ),
                    "graduation_years": self.source.get(
                        "graduation_years", []
                    ),
                    "published_at": published_at,
                    "deadline": str(item.get("deadline") or ""),
                    "url": self.source["homepage"],
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))

            if len(seen_ids) >= expected_total:
                break
            if len(rows) < page_size:
                break
        else:
            raise ValueError("广州工控招聘接口分页超过配置上限")

        if expected_total is not None and len(seen_ids) < expected_total:
            raise ValueError("广州工控招聘接口返回岗位数少于声明总数")
        return jobs


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


class BydCampusCollector(Collector):
    """Watch BYD's public fresh-graduate topic API for the target cycle."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def collect(self) -> List[JobPosting]:
        try:
            payload = json.loads(
                fetch_bytes(
                    self.source["url"],
                    timeout=int(self.source.get("timeout", 20)),
                    headers=self.source.get("headers"),
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("比亚迪校招主题接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("比亚迪校招主题接口响应结构异常")
        if payload.get("code") != 0 or payload.get("oK") is not True:
            raise ValueError(
                "比亚迪校招主题接口返回失败状态（code={}）".format(
                    payload.get("code")
                )
            )

        topic = payload.get("data")
        if topic is None:
            return []
        if not isinstance(topic, dict):
            raise ValueError("比亚迪校招主题接口 data 结构异常")

        required = ("topicCode", "topic", "zpNature")
        if any(
            not str(topic.get(field, "") or "").strip()
            for field in required
        ):
            raise ValueError("比亚迪校招主题缺少必要字段")
        expected_nature = str(self.source.get("expected_zp_nature", "") or "")
        actual_nature = str(topic["zpNature"]).strip()
        if expected_nature and actual_nature != expected_nature:
            raise ValueError("比亚迪校招主题招聘性质与请求不一致")

        searchable = "{} {}".format(
            topic["topic"], topic.get("graduationYear", "")
        )
        if self._contains(searchable, self.source.get("exclude_keywords", [])):
            return []
        target_keywords = self.source.get("target_keywords", [])
        if target_keywords and not self._contains(searchable, target_keywords):
            return []

        topic_code = str(topic["topicCode"]).strip()
        values = {
            "external_id": "{}:{}".format(self.source["id"], topic_code),
            "title": self.source.get(
                "title", "比亚迪{}已启动".format(str(topic["topic"]).strip())
            ),
            "company": self.source.get("company", "比亚迪"),
            "company_type": self.source.get("company_type", "私企"),
            "location": self.source.get("location", "待核对"),
            "description": self.source.get("description", ""),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self.source.get("published_at", ""),
            "deadline": self.source.get("deadline", ""),
            "url": self.source.get("homepage", self.source["url"]),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return [JobPosting.from_mapping(values)]


class PinganCampusCollector(Collector):
    """Collect formal graduate roles from Ping An's public campus API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _request_json(
        self,
        url: str,
        request_json: Dict[str, Any],
        label: str,
    ) -> Any:
        try:
            payload = json.loads(
                fetch_bytes(
                    url,
                    timeout=int(self.source.get("timeout", 20)),
                    method="POST",
                    json_body=request_json,
                    headers={"Accept": "application/json;charset=utf-8"},
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("{}返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict):
            raise ValueError("{}响应结构异常".format(label))
        if str(payload.get("responseCode", "")) != "10001":
            raise ValueError(
                "{}返回失败状态（responseCode={}）".format(
                    label, payload.get("responseCode")
                )
            )
        return payload.get("data")

    def _official_configs(self) -> List[Dict[str, Any]]:
        official_units = self.source.get("official_units", [])
        if not isinstance(official_units, list) or not official_units:
            raise ValueError("中国平安校招来源未配置监控单位")

        configs = []
        seen_wecruit_ids = set()
        for unit in official_units:
            if not isinstance(unit, dict):
                raise ValueError("中国平安校招监控单位配置异常")
            official_url = str(unit.get("official_url", "") or "").strip()
            data = self._request_json(
                self.source["official_config_url"],
                {
                    "officialUrl": official_url,
                    "recruitType": str(self.source.get("recruit_type", "3")),
                },
                "中国平安校招官网配置接口",
            )
            if not isinstance(data, dict):
                raise ValueError("中国平安校招官网配置 data 结构异常")

            required = ("wecruitId", "businessUnitId", "businessUnitName")
            if any(not str(data.get(field, "") or "").strip() for field in required):
                raise ValueError("中国平安校招官网配置缺少必要字段")
            expected_unit_id = str(unit.get("business_unit_id", "") or "")
            actual_unit_id = str(data["businessUnitId"]).strip()
            if expected_unit_id and actual_unit_id != expected_unit_id:
                raise ValueError("中国平安校招官网返回了非目标监控单位")
            if str(data.get("hasAvalibleWebsiteModel", "")) != "Y":
                raise ValueError("中国平安目标校招官网当前未发布")

            wecruit_id = str(data["wecruitId"]).strip()
            if wecruit_id in seen_wecruit_ids:
                raise ValueError("中国平安校招官网返回了重复招聘活动 ID")
            seen_wecruit_ids.add(wecruit_id)
            configs.append(
                {
                    "official_url": official_url,
                    "wecruit_id": wecruit_id,
                    "business_unit_id": actual_unit_id,
                    "business_unit_name": str(data["businessUnitName"]).strip(),
                }
            )
        return configs

    def _positions(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        page_size = int(self.source.get("page_size", 50))
        max_pages = int(self.source.get("max_pages", 20))
        positions = []
        seen_ids = set()
        expected_total = None
        total_pages = None

        for page_no in range(1, max_pages + 1):
            data = self._request_json(
                self.source["positions_url"],
                {
                    "PageNum": page_no,
                    "businessUnitId": "",
                    "keyWord": "",
                    "pageSize": page_size,
                    "positionCategoryId": "",
                    "wecruitId": config["wecruit_id"],
                    "positionType": str(
                        self.source.get("position_type", "1")
                    ),
                    "wecruitPlatform": True,
                    "workCity": "",
                    "interviewCity": "",
                },
                "中国平安校招岗位接口",
            )
            if data is None:
                return []
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise ValueError("中国平安校招岗位 data 结构异常")
            try:
                current_page = int(data["pageNo"])
                current_total = int(data["totalCount"])
                current_total_pages = int(data["totalPage"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("中国平安校招岗位分页字段异常") from exc
            if current_page != page_no:
                raise ValueError("中国平安校招岗位分页页码异常")
            if expected_total is None:
                expected_total = current_total
                total_pages = current_total_pages
                if total_pages > max_pages:
                    raise ValueError("中国平安校招岗位分页超过安全上限")
            elif current_total != expected_total or current_total_pages != total_pages:
                raise ValueError("中国平安校招岗位分页总数发生变化")

            for item in data["list"]:
                if not isinstance(item, dict):
                    raise ValueError("中国平安校招岗位列表项结构异常")
                position_id = str(item.get("idPosition", "") or "").strip()
                if not position_id:
                    raise ValueError("中国平安校招岗位缺少稳定 ID")
                if position_id in seen_ids:
                    raise ValueError("中国平安校招岗位分页返回重复 ID")
                seen_ids.add(position_id)
                positions.append(item)

            if page_no >= total_pages:
                break

        if expected_total is None or len(positions) != expected_total:
            raise ValueError("中国平安校招岗位分页数量不完整")
        return positions

    def _job(
        self,
        item: Dict[str, Any],
        config: Dict[str, Any],
    ) -> JobPosting | None:
        position_id = str(item.get("idPosition", "") or "").strip()
        title = str(item.get("positionName", "") or "").strip()
        location = str(item.get("workCity", "") or "").strip()
        if not title or not location:
            raise ValueError("中国平安校招岗位缺少标题或工作城市")

        item_position_type = str(item.get("positionType", "") or "").strip()
        expected_type = str(self.source.get("position_type", "1"))
        if item_position_type and item_position_type != expected_type:
            raise ValueError("中国平安校招岗位接口返回了非正式应届生岗位")

        company = str(
            item.get("businessUnitName")
            or config["business_unit_name"]
            or self.source.get("company", "中国平安")
        ).strip()
        department = str(
            item.get("deptShowName") or item.get("deptName") or ""
        ).strip()
        category = str(item.get("positionCategoryName", "") or "").strip()
        searchable = " ".join((title, company, department, category))
        if self._contains(searchable, self.source.get("exclude_keywords", [])):
            return None
        include_keywords = self.source.get("include_keywords", [])
        if include_keywords and not self._contains(searchable, include_keywords):
            return None
        if not self._contains(location, self.source.get("location_keywords", [])):
            return None

        description_parts = [part for part in (category, company, department) if part]
        official_url = config["official_url"]
        official_path = "/{}".format(official_url) if official_url else ""
        url = self.source["detail_url_template"].format(
            official_path=official_path,
            position_id=quote(position_id),
        )
        values = {
            "external_id": position_id,
            "title": title,
            "company": company,
            "company_type": self.source.get("company_type", "私企"),
            "location": location.replace(",", "、"),
            "description": " / ".join(description_parts),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": item.get("createdDate", ""),
            "deadline": item.get("deadline") or self.source.get("deadline", ""),
            "url": url,
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        jobs = []
        seen_ids = set()
        for config in self._official_configs():
            for item in self._positions(config):
                position_id = str(item.get("idPosition", "") or "").strip()
                if position_id in seen_ids:
                    continue
                seen_ids.add(position_id)
                job = self._job(item, config)
                if job is not None:
                    jobs.append(job)
        return jobs


class CmbCampusCollector(Collector):
    """Collect target graduate roles from CMB's public recruitment API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _request_json(
        self,
        url: str,
        label: str,
        method: str = "GET",
        request_json: Dict[str, Any] = None,
    ) -> Any:
        try:
            payload = json.loads(
                fetch_bytes(
                    url,
                    timeout=int(self.source.get("timeout", 20)),
                    method=method,
                    json_body=request_json,
                    headers={
                        "Accept": "application/json;charset=utf-8",
                        "Referer": self.source["homepage"],
                    },
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("{}返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict):
            raise ValueError("{}响应结构异常".format(label))
        if str(payload.get("returnCode", "")) != "SUC0000":
            raise ValueError(
                "{}返回失败状态（returnCode={}）".format(
                    label, payload.get("returnCode")
                )
            )
        return payload.get("body")

    def _recruiting_org_ids(self) -> set[str]:
        recruitment_type_id = self.source["recruitment_type_id"]
        url = "{}?{}".format(
            self.source["recruiting_info_url"],
            urlencode({"recruitmentTypeId": recruitment_type_id}),
        )
        body = self._request_json(url, "招商银行校招筛选接口")
        if not isinstance(body, dict):
            raise ValueError("招商银行校招筛选接口 body 结构异常")
        organizations = body.get("recruitingOrgList")
        cities = body.get("recruitingCityList")
        if not isinstance(organizations, list) or not isinstance(cities, list):
            raise ValueError("招商银行校招筛选接口缺少机构或城市列表")

        organization_ids = set()
        for item in organizations:
            if not isinstance(item, dict):
                raise ValueError("招商银行校招招聘机构结构异常")
            organization_id = str(item.get("orgId", "") or "").strip()
            organization_name = str(item.get("orgName", "") or "").strip()
            if not organization_id or not organization_name:
                raise ValueError("招商银行校招招聘机构缺少必要字段")
            if organization_id in organization_ids:
                raise ValueError("招商银行校招招聘机构返回重复 ID")
            organization_ids.add(organization_id)
        return organization_ids

    def _positions(self, organization_ids: set[str]) -> List[Dict[str, Any]]:
        page_size = max(1, int(self.source.get("page_size", 50)))
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        recruitment_type_id = self.source["recruitment_type_id"]
        positions = []
        seen_ids = set()
        expected_total = None
        expected_pages = None

        for page_index in range(1, max_pages + 1):
            body = self._request_json(
                self.source["positions_url"],
                "招商银行校招岗位接口",
                method="POST",
                request_json={
                    "orgIdList": [],
                    "keywords": "",
                    "locationIdList": [],
                    "pageIndex": page_index,
                    "pageSize": page_size,
                    "recruitmentTypeId": recruitment_type_id,
                },
            )
            if not isinstance(body, dict) or not isinstance(
                body.get("data"), list
            ):
                raise ValueError("招商银行校招岗位接口 body 结构异常")
            try:
                total = int(body["total"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("招商银行校招岗位总数字段异常") from exc
            pages = (total + page_size - 1) // page_size
            if expected_total is None:
                expected_total = total
                expected_pages = pages
                if expected_pages > max_pages:
                    raise ValueError("招商银行校招岗位分页超过安全上限")
            elif total != expected_total or pages != expected_pages:
                raise ValueError("招商银行校招岗位分页总数发生变化")

            items = body["data"]
            if len(items) > page_size:
                raise ValueError("招商银行校招岗位单页数量超过请求上限")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("招商银行校招岗位列表项结构异常")
                publish_id = str(item.get("publishGID", "") or "").strip()
                title = str(item.get("jobDisplay", "") or "").strip()
                company = str(item.get("branchCodeName", "") or "").strip()
                location = str(item.get("locationName", "") or "").strip()
                organization_id = str(item.get("branchCode", "") or "").strip()
                if not publish_id or not title or not company or not location:
                    raise ValueError("招商银行校招岗位缺少必要字段")
                if organization_ids and organization_id not in organization_ids:
                    raise ValueError("招商银行校招岗位返回了非公开招聘机构")
                if publish_id in seen_ids:
                    raise ValueError("招商银行校招岗位分页返回重复 ID")
                seen_ids.add(publish_id)
                positions.append(item)

            if page_index >= pages:
                break

        if expected_total is None or len(positions) != expected_total:
            raise ValueError("招商银行校招岗位分页数量不完整")
        return positions

    def _detail(self, publish_id: str) -> Dict[str, Any]:
        url = "{}?{}".format(
            self.source["detail_api_url"],
            urlencode({"publishId": publish_id}),
        )
        body = self._request_json(url, "招商银行校招岗位详情接口")
        if not isinstance(body, dict):
            raise ValueError("招商银行校招岗位详情 body 结构异常")
        if str(body.get("publishGID", "") or "").strip() != publish_id:
            raise ValueError("招商银行校招岗位详情 ID 与列表不一致")
        actual_type = str(body.get("recruitmentTypeID", "") or "").strip()
        if actual_type != self.source["recruitment_type_id"]:
            raise ValueError("招商银行校招岗位详情不是正式应届生入口")
        return body

    @staticmethod
    def _education(requirement: str, fallback: str) -> str:
        match = re.search(
            r"(?:博士|硕士|本科|大学本科)(?:学历)?及以上",
            requirement,
        )
        return match.group(0) if match else fallback

    def _job(self, item: Dict[str, Any]) -> JobPosting | None:
        publish_id = str(item["publishGID"]).strip()
        title = str(item["jobDisplay"]).strip()
        company = str(item["branchCodeName"]).strip()
        location = str(item["locationName"]).strip()
        searchable = " ".join((title, company, location))
        if self._contains(searchable, self.source.get("exclude_keywords", [])):
            return None
        include_keywords = self.source.get("include_keywords", [])
        if include_keywords and not self._contains(searchable, include_keywords):
            return None
        location_keywords = self.source.get("location_keywords", [])
        if location_keywords and not self._contains(location, location_keywords):
            return None

        detail = self._detail(publish_id)
        detail_title = str(detail.get("jobDisplay", "") or "").strip()
        detail_company = str(detail.get("branchCodeName", "") or "").strip()
        detail_location = str(detail.get("locationName", "") or "").strip()
        if (detail_title, detail_company, detail_location) != (
            title,
            company,
            location,
        ):
            raise ValueError("招商银行校招岗位详情与列表必要字段不一致")

        responsibility = _html_fragment_text(detail.get("jobResponsibility"))
        requirement = _html_fragment_text(detail.get("jobRequirement"))
        campaign_text = " ".join(
            (
                str(detail.get("jobCode", "") or ""),
                detail_title,
                responsibility,
                requirement,
            )
        )
        target_keywords = self.source.get("target_campaign_keywords", [])
        if target_keywords and not self._contains(campaign_text, target_keywords):
            return None

        description_parts = []
        if responsibility:
            description_parts.append("岗位职责：{}".format(responsibility))
        if requirement:
            description_parts.append("岗位要求：{}".format(requirement))
        values = {
            "external_id": publish_id,
            "title": title,
            "company": company,
            "company_type": self.source.get("company_type", "私企"),
            "location": location,
            "description": "｜".join(description_parts),
            "education": self._education(
                requirement, self.source.get("education", "")
            ),
            "graduation_years": self.source.get("graduation_years", []),
            "deadline": detail.get("expiredOn") or item.get("expiredOn", ""),
            "url": self.source["detail_url_template"].format(
                publish_id=quote(publish_id)
            ),
            "source_name": self.source.get("name", self.source["id"]),
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        organization_ids = self._recruiting_org_ids()
        jobs = []
        for item in self._positions(organization_ids):
            job = self._job(item)
            if job is not None:
                jobs.append(job)
        return jobs


class SfTechCampusCollector(Collector):
    """Collect formal graduate roles from SF's public campus API."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _request_json(self, url: str, label: str) -> Any:
        service_root = self.source.get(
            "service_root", urljoin(self.source["homepage"], "/")
        )
        try:
            return json.loads(
                fetch_bytes(
                    url,
                    timeout=int(self.source.get("timeout", 20)),
                    headers={
                        "Accept": "application/json",
                        "Referer": self.source["homepage"],
                        "cr-service": quote(service_root, safe=""),
                    },
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("{}返回了无效 JSON".format(label)) from exc

    def _positions(self) -> List[Dict[str, Any]]:
        page_size = max(1, int(self.source.get("page_size", 100)))
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        positions = []
        seen_ids = set()
        expected_total = None
        expected_pages = None

        for page_number in range(1, max_pages + 1):
            url = "{}?{}".format(
                self.source["positions_url"],
                urlencode({"pageNum": page_number, "pageSize": page_size}),
            )
            payload = self._request_json(url, "顺丰校园岗位接口")
            if not isinstance(payload, dict) or not isinstance(
                payload.get("list"), list
            ):
                raise ValueError("顺丰校园岗位接口响应结构异常")
            try:
                total = int(payload["total"])
                pages = int(payload["pages"])
                actual_page = int(payload["pageNum"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("顺丰校园岗位分页字段异常") from exc
            calculated_pages = (total + page_size - 1) // page_size
            if pages != calculated_pages:
                raise ValueError("顺丰校园岗位总页数不一致")
            if total and actual_page != page_number:
                raise ValueError("顺丰校园岗位返回页码不一致")
            if expected_total is None:
                expected_total = total
                expected_pages = pages
                if pages > max_pages:
                    raise ValueError("顺丰校园岗位分页超过安全上限")
            elif total != expected_total or pages != expected_pages:
                raise ValueError("顺丰校园岗位分页总数发生变化")

            items = payload["list"]
            if len(items) > page_size:
                raise ValueError("顺丰校园岗位单页数量超过请求上限")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("顺丰校园岗位列表项结构异常")
                position_id = str(item.get("id", "") or "").strip()
                title = str(item.get("positionName", "") or "").strip()
                organization = str(
                    item.get("orgSourceName", "") or ""
                ).strip()
                location = str(item.get("demandCity", "") or "").strip()
                season_id = str(item.get("seasonId", "") or "").strip()
                if not all(
                    (position_id, title, organization, location, season_id)
                ):
                    raise ValueError("顺丰校园岗位缺少必要字段")
                if position_id in seen_ids:
                    raise ValueError("顺丰校园岗位分页返回重复 ID")
                seen_ids.add(position_id)
                positions.append(item)

            if page_number >= pages:
                break

        if expected_total is None or len(positions) != expected_total:
            raise ValueError("顺丰校园岗位分页数量不完整")
        return positions

    def _detail(self, position_id: str) -> Dict[str, Any]:
        url = self.source["detail_api_url_template"].format(
            position_id=quote(position_id)
        )
        payload = self._request_json(url, "顺丰校园岗位详情接口")
        if not isinstance(payload, dict):
            raise ValueError("顺丰校园岗位详情响应结构异常")
        if str(payload.get("id", "") or "").strip() != position_id:
            raise ValueError("顺丰校园岗位详情 ID 与列表不一致")
        return payload

    def _job(self, item: Dict[str, Any]) -> JobPosting | None:
        position_id = str(item["id"]).strip()
        detail = self._detail(position_id)
        fields = ("positionName", "orgSourceName", "demandCity", "seasonId")
        if any(
            str(detail.get(field, "") or "").strip()
            != str(item.get(field, "") or "").strip()
            for field in fields
        ):
            raise ValueError("顺丰校园岗位详情与列表必要字段不一致")

        title = str(detail["positionName"]).strip()
        company = str(detail["orgSourceName"]).strip()
        organization_code = str(detail.get("orgSource", "") or "").strip()
        location = str(detail["demandCity"]).strip()
        season_name = str(detail.get("seasonName", "") or "").strip()
        intern_type = " ".join(
            (
                str(detail.get("internType", "") or ""),
                str(detail.get("internTypeName", "") or ""),
            )
        )
        responsibility = str(detail.get("postDuty", "") or "").strip()
        requirement = str(detail.get("jobRequirement", "") or "").strip()
        education = str(detail.get("educationName", "") or "").strip()

        target_codes = {
            str(value).strip()
            for value in self.source.get("target_org_sources", [])
        }
        target_names = {
            str(value).strip()
            for value in self.source.get("target_org_names", [])
        }
        if target_codes and organization_code not in target_codes:
            return None
        if target_names and company not in target_names:
            return None
        if not season_name:
            raise ValueError("顺丰校园岗位详情缺少招聘届别名称")
        if self._contains(
            season_name, self.source.get("campaign_exclude_keywords", [])
        ):
            return None
        target_campaigns = self.source.get("target_campaign_keywords", [])
        if target_campaigns and not self._contains(
            season_name, target_campaigns
        ):
            return None
        formal_keywords = self.source.get("formal_campaign_keywords", [])
        if formal_keywords and not self._contains(season_name, formal_keywords):
            return None

        searchable = " ".join(
            (
                title,
                company,
                location,
                season_name,
                intern_type,
                responsibility,
                requirement,
                education,
            )
        )
        if self._contains(searchable, self.source.get("exclude_keywords", [])):
            return None
        include_keywords = self.source.get("include_keywords", [])
        if include_keywords and not self._contains(searchable, include_keywords):
            return None
        location_keywords = self.source.get("location_keywords", [])
        if location_keywords and not self._contains(location, location_keywords):
            return None
        if self._contains(
            " ".join((title, requirement, education)),
            self.source.get("education_exclude_keywords", []),
        ):
            return None

        description_parts = []
        if responsibility:
            description_parts.append("岗位职责：{}".format(responsibility))
        if requirement:
            description_parts.append("岗位要求：{}".format(requirement))
        return JobPosting.from_mapping(
            {
                "external_id": position_id,
                "title": title,
                "company": company,
                "company_type": self.source.get("company_type", "私企"),
                "location": location,
                "description": "｜".join(description_parts),
                "education": education or self.source.get("education", ""),
                "graduation_years": self.source.get(
                    "graduation_years", []
                ),
                "deadline": self.source.get(
                    "deadline", "以官方岗位页面为准"
                ),
                "url": self.source["detail_url_template"].format(
                    position_id=quote(position_id)
                ),
                "source_name": self.source.get("name", self.source["id"]),
            }
        )

    def collect(self) -> List[JobPosting]:
        jobs = []
        for item in self._positions():
            job = self._job(item)
            if job is not None:
                jobs.append(job)
        return jobs


class CeairCampusCollector(Collector):
    """Collect target-cycle jobs from China Eastern's public campus API."""

    @staticmethod
    def _contains(text: Any, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text or "").lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _asp_date(value: Any) -> str:
        match = re.fullmatch(r"/Date\((\d+)\)/", str(value or ""))
        if not match:
            return ""
        return datetime.fromtimestamp(
            int(match.group(1)) / 1000,
            timezone(timedelta(hours=8)),
        ).strftime("%Y-%m-%d %H:%M:%S")

    def collect(self) -> List[JobPosting]:
        try:
            payload = json.loads(
                fetch_bytes(
                    self.source["url"],
                    method="POST",
                    form_body={
                        "pageIndex": 1,
                        "pageSize": int(self.source.get("page_size", 100)),
                        "positionName": "",
                        "workCity": "",
                        "cateId": "",
                        "deptId": "",
                        "banKuaiId": self.source.get("category_id", "4000"),
                    },
                    headers={"Referer": self.source["homepage"]},
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("东航校园岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("data"), list
        ):
            raise ValueError("东航校园岗位接口缺少 data 数组")
        try:
            total = int(payload["total"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("东航校园岗位接口缺少有效总数") from exc
        items = payload["data"]
        if total != len(items):
            raise ValueError("东航校园岗位接口没有完整返回岗位")

        target_keywords = self.source.get("target_keywords", [])
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        jobs = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("东航校园岗位元素结构异常")
            external_id = str(item.get("zp_ActiveInfoID") or "").strip()
            title = str(item.get("Active_Name") or "").strip()
            if not external_id or not title:
                raise ValueError("东航校园岗位缺少稳定 ID 或岗位名称")
            if str(item.get("News_CategoryId", "")) != str(
                self.source.get("category_id", "4000")
            ):
                raise ValueError("东航校园岗位接口返回了非目标栏目岗位")
            searchable = " ".join(
                str(item.get(field) or "")
                for field in (
                    "Active_Name",
                    "News_Title",
                    "PC_Name",
                    "PD_Name",
                    "Active_Remark",
                    "Active_Notice",
                )
            )
            if target_keywords and not self._contains(
                searchable, target_keywords
            ):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            location = str(item.get("Active_WorkAddress") or "待核对")
            if location_keywords and not self._contains(
                location, location_keywords
            ):
                continue
            description = "｜".join(
                dict.fromkeys(
                    part
                    for part in (
                        str(item.get("PC_Name") or "").strip(),
                        str(item.get("PD_Name") or "").strip(),
                        str(item.get("News_Title") or "").strip(),
                    )
                    if part and part != title
                )
            )
            values = {
                "external_id": external_id,
                "title": title,
                "company": self.source.get("company", "中国东方航空"),
                "company_type": self.source.get("company_type", "央企"),
                "location": location,
                "description": description,
                "education": self.source.get("education", "以岗位为准"),
                "graduation_years": self.source.get(
                    "graduation_years", [2027]
                ),
                "published_at": self._asp_date(
                    item.get("Active_CreateDate")
                    or item.get("News_CreateDate")
                ),
                "deadline": self._asp_date(item.get("Active_EndTime")),
                "url": self.source.get(
                    "detail_url", self.source["homepage"]
                ),
                "source_name": self.source["name"],
            }
            jobs.append(JobPosting.from_mapping(values))
        return jobs


class XiaohongshuCampusCollector(Collector):
    """Collect formal target-cycle jobs from Xiaohongshu's public API."""

    @staticmethod
    def _contains(text: Any, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text or "").lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _fetch_page(self, page: int) -> Dict[str, Any]:
        try:
            payload = json.loads(
                fetch_bytes(
                    self.source["url"],
                    method="POST",
                    json_body={
                        "pageNum": page,
                        "pageSize": int(self.source.get("page_size", 100)),
                        "recruitType": "campus",
                    },
                    headers={
                        "Origin": "https://job.xiaohongshu.com",
                        "Referer": self.source["homepage"],
                    },
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("小红书校园岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("statusCode") != 200:
            raise ValueError("小红书校园岗位接口返回失败状态")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise ValueError("小红书校园岗位接口缺少岗位数组")
        if any(
            field not in data
            for field in ("pageNum", "pageSize", "total", "totalPage")
        ):
            raise ValueError("小红书校园岗位接口缺少分页字段")
        return data

    def collect(self) -> List[JobPosting]:
        max_pages = max(1, int(self.source.get("max_pages", 10)))
        items = []
        total = 0
        for page in range(1, max_pages + 1):
            data = self._fetch_page(page)
            try:
                current_page = int(data["pageNum"])
                total_pages = int(data["totalPage"])
                total = int(data["total"])
            except (TypeError, ValueError) as exc:
                raise ValueError("小红书校园岗位分页字段异常") from exc
            if current_page != page or total_pages > max_pages:
                raise ValueError("小红书校园岗位分页响应超出配置")
            page_items = data["list"]
            if any(not isinstance(item, dict) for item in page_items):
                raise ValueError("小红书校园岗位元素结构异常")
            items.extend(page_items)
            if page >= total_pages:
                break
        if len(items) != total:
            raise ValueError("小红书校园岗位接口没有完整返回岗位")

        target_keywords = self.source.get("target_keywords", [])
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        min_published_at = str(
            self.source.get("min_published_at", "") or ""
        )
        active_status = self.source.get("active_status", "in_recruitment")
        jobs = []
        seen_ids = set()
        for item in items:
            external_id = str(item.get("positionId") or "").strip()
            title = str(item.get("positionName") or "").strip()
            if not external_id or not title:
                raise ValueError("小红书校园岗位缺少稳定 ID 或岗位名称")
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)
            if active_status and item.get("recruitStatus") != active_status:
                continue
            searchable = " ".join(
                str(item.get(field) or "")
                for field in (
                    "positionName",
                    "jobProjectName",
                    "jobType",
                    "duty",
                    "qualification",
                )
            )
            if target_keywords and not self._contains(
                searchable, target_keywords
            ):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            location = str(item.get("workplace") or "待核对")
            if location_keywords and not self._contains(
                location, location_keywords
            ):
                continue
            published_at = str(item.get("publishTime") or "")
            if min_published_at and (
                not published_at
                or published_at[:10] < min_published_at[:10]
            ):
                continue
            description = "；".join(
                part
                for part in (
                    str(item.get("jobProjectName") or "").strip(),
                    _html_fragment_text(item.get("duty", "")),
                    _html_fragment_text(item.get("qualification", "")),
                )
                if part
            )
            values = {
                "external_id": external_id,
                "title": title,
                "company": self.source.get("company", "小红书"),
                "company_type": self.source.get("company_type", "私企"),
                "location": location,
                "description": description,
                "education": self.source.get("education", "以岗位为准"),
                "graduation_years": self.source.get(
                    "graduation_years", [2027]
                ),
                "published_at": published_at,
                "deadline": str(item.get("offlineTime") or ""),
                "url": self.source.get(
                    "detail_url_template",
                    "https://job.xiaohongshu.com/campus/position/{positionId}",
                ).format_map(_MissingValueDict(item)),
                "source_name": self.source["name"],
            }
            jobs.append(JobPosting.from_mapping(values))
        return jobs


class CampaignWatchCollector(Collector):
    """Emit one notice when an official page announces a target campaign."""

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        active_homepage = homepage
        try:
            body = fetch_bytes(
                homepage,
                headers=self.source.get("headers"),
            ).decode("utf-8", errors="replace")
        except Exception:
            fallback = self.source.get("fallback_homepage", "")
            if not fallback or fallback == homepage:
                raise
            active_homepage = fallback
            body = fetch_bytes(
                fallback,
                headers=self.source.get("fallback_headers"),
            ).decode("utf-8", errors="replace")
        parser = _LinkParser()
        parser.feed(body)
        visible_text = " ".join(" ".join(parser.text_parts).split())
        searchable_text = (
            body if self.source.get("search_raw_html") else visible_text
        )

        required_text = self.source.get("required_text", "")
        if required_text and required_text not in searchable_text:
            fallback = self.source.get("fallback_homepage", "")
            if not fallback or fallback == homepage:
                raise ValueError("活动监控页未出现预期标识，可能已经改版")
            if active_homepage == homepage:
                body = fetch_bytes(
                    fallback,
                    headers=self.source.get("fallback_headers"),
                ).decode("utf-8", errors="replace")
                active_homepage = fallback
                parser = _LinkParser()
                parser.feed(body)
                visible_text = " ".join(" ".join(parser.text_parts).split())
                searchable_text = (
                    body if self.source.get("search_raw_html") else visible_text
                )
            fallback_required_text = self.source.get("fallback_required_text", "")
            if fallback_required_text and fallback_required_text not in searchable_text:
                raise ValueError("主页面与官方兜底入口均未出现预期标识")

        target_keywords = self.source.get("target_keywords", [])
        compacted_text = "".join(searchable_text.lower().split())
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
        campaign_url = active_homepage
        for link in parser.links:
            candidate_text = " ".join(link["text"].split())
            searchable = "{} {}".format(candidate_text, link["href"]).lower()
            if any(keyword.lower() in searchable for keyword in link_keywords):
                campaign_url = urljoin(active_homepage, link["href"])
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
            "description": "{} 官方页面命中招聘窗口标识：{}".format(
                self.source.get("description", ""), matched_keyword
            ).strip(),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self.source.get("published_at", ""),
            "deadline": self.source.get("deadline", ""),
            "url": campaign_url,
            "source_name": self.source["name"],
        }
        return [JobPosting.from_mapping(values)]


class _SectionParser(HTMLParser):
    """Collect visible text and links from one identified HTML section."""

    def __init__(self, section_id: str) -> None:
        super().__init__()
        self.section_id = section_id
        self.found = False
        self.section_depth = 0
        self.ignored_depth = 0
        self.text_parts: List[str] = []
        self.links: List[Dict[str, str]] = []
        self.current_href = ""
        self.current_link_parts: List[str] = []

    @property
    def active(self) -> bool:
        return self.section_depth > 0

    def handle_starttag(self, tag: str, attrs: Iterable[Any]) -> None:
        lowered = tag.lower()
        values = dict(attrs)
        if (
            not self.active
            and lowered == "section"
            and values.get("id") == self.section_id
        ):
            self.found = True
            self.section_depth = 1
            return
        if not self.active:
            return
        if lowered == "section":
            self.section_depth += 1
        if lowered in {"script", "style"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if lowered == "a":
            self.current_href = str(values.get("href", "") or "")
            self.current_link_parts = []

    def handle_data(self, data: str) -> None:
        if not self.active or self.ignored_depth:
            return
        self.text_parts.append(data)
        if self.current_href:
            self.current_link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.active:
            return
        lowered = tag.lower()
        if lowered in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if lowered == "a" and self.current_href:
            self.links.append(
                {
                    "href": self.current_href,
                    "text": " ".join(self.current_link_parts),
                }
            )
            self.current_href = ""
            self.current_link_parts = []
        if lowered == "section":
            self.section_depth -= 1


class PwcGraduateCampaignCollector(Collector):
    """Watch PwC China's official graduate section for the target cycle."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def _application_url(
        self, homepage: str, links: List[Dict[str, str]]
    ) -> str:
        allowed_hosts = set(self.source.get("allowed_application_hosts", []))
        required_path = self.source.get("application_path_prefix", "")
        apply_keywords = self.source.get("application_link_keywords", [])
        for link in links:
            candidate = urljoin(homepage, link["href"])
            searchable = "{} {}".format(link["text"], candidate)
            if apply_keywords and not self._contains(
                searchable, apply_keywords
            ):
                continue
            parsed = urlsplit(candidate)
            if allowed_hosts and parsed.netloc not in allowed_hosts:
                continue
            if required_path and not parsed.path.startswith(required_path):
                continue
            return candidate
        raise ValueError("普华永道毕业生计划缺少有效官方申请入口")

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        body = fetch_bytes(homepage).decode("utf-8", errors="replace")
        parser = _SectionParser(
            self.source.get("section_id", "graduate")
        )
        parser.feed(body)
        if not parser.found:
            raise ValueError("普华永道官网缺少毕业生计划区块")

        visible_text = " ".join(" ".join(parser.text_parts).split())
        required_text = self.source.get("required_text", "毕业生计划")
        if required_text and required_text not in visible_text:
            raise ValueError("普华永道毕业生计划区块缺少预期标识")
        application_url = self._application_url(homepage, parser.links)

        target_keywords = self.source.get("target_keywords", [])
        if target_keywords and not self._contains(
            visible_text, target_keywords
        ):
            return []

        values = {
            "external_id": self.source.get(
                "external_id", "{}:graduate".format(self.source["id"])
            ),
            "title": self.source["title"],
            "company": self.source.get("company", self.source["name"]),
            "company_type": self.source.get("company_type", "外企"),
            "location": self.source.get("location", "待官网岗位确认"),
            "description": self.source.get("description", ""),
            "education": self.source.get("education", ""),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self.source.get("published_at", ""),
            "deadline": self.source.get("deadline", ""),
            "url": application_url,
            "source_name": self.source["name"],
        }
        return [JobPosting.from_mapping(values)]


class BeisenPortalCampaignCollector(Collector):
    """Follow public Beisen portal page pointers and detect a target campaign."""

    @staticmethod
    def _portal_data(body: str) -> Dict[str, Any]:
        marker = "var BSGlobal ="
        marker_at = body.find(marker)
        if marker_at < 0:
            raise ValueError("北森招聘门户缺少公开站点配置，可能已经改版")
        raw = body[marker_at + len(marker) :].lstrip()
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("北森招聘门户返回了无效站点配置") from exc
        if not isinstance(payload, dict):
            raise ValueError("北森招聘门户站点配置结构异常")
        return payload

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        portal_body = fetch_bytes(homepage).decode("utf-8", errors="replace")
        portal_data = self._portal_data(portal_body)

        expected_tenant = self.source.get("tenant_name", "")
        tenant_info = portal_data.get("tenantInfo")
        actual_tenant = (
            tenant_info.get("Name", "") if isinstance(tenant_info, dict) else ""
        )
        if expected_tenant and actual_tenant != expected_tenant:
            raise ValueError("北森招聘门户返回了非目标租户")

        pages = portal_data.get("Pages")
        if not isinstance(pages, list):
            raise ValueError("北森招聘门户站点配置缺少 Pages 数组")
        page_names = set(self.source.get("page_names", []))
        page_urls = []
        for page in pages:
            if not isinstance(page, dict):
                raise ValueError("北森招聘门户 Pages 列表元素结构异常")
            if page_names and page.get("Name") not in page_names:
                continue
            address = page.get("HtmlAddress")
            if address and address not in page_urls:
                page_urls.append(address)
        if not page_urls:
            raise ValueError("北森招聘门户没有找到目标公告页面")

        target_keywords = self.source.get("target_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        matched_keyword = ""
        matched_url = ""
        for page_url in page_urls:
            page_body = fetch_bytes(page_url).decode("utf-8", errors="replace")
            parser = _LinkParser()
            parser.feed(page_body)
            visible_text = " ".join(" ".join(parser.text_parts).split())
            page_keyword = next(
                (
                    keyword
                    for keyword in target_keywords
                    if self._contains(visible_text, [keyword])
                ),
                "",
            )
            if not page_keyword:
                continue
            candidate_url = ""
            for link in parser.links:
                searchable = "{} {}".format(link["text"], link["href"])
                if self._contains(searchable, [page_keyword]) and not (
                    self._contains(searchable, exclude_keywords)
                ):
                    candidate_url = urljoin(homepage, link["href"])
                    break
            if not candidate_url and self._contains(
                visible_text, exclude_keywords
            ):
                continue
            matched_keyword = page_keyword
            matched_url = candidate_url or self.source.get(
                "campus_jobs_url", homepage
            )
            break

        if not matched_keyword:
            return []

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
            "url": matched_url,
            "source_name": self.source["name"],
        }
        return [JobPosting.from_mapping(values)]


class BeisenModernCampusCollector(Collector):
    """Collect live campus jobs from a modern public Beisen portal."""

    DISPLAY_FIELDS = [
        "Category",
        "Kind",
        "LocId",
        "DetailAddress",
        "Org",
        "HeadCount",
        "Station",
        "EndTime",
        "PostDate",
        "Salary",
        "Degree",
        "YearsOfWorking",
        "ClassificationOne",
        "ClassificationTwo",
        "Classification3",
        "Classification4",
        "Classification5",
        "Classification6",
    ]

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _date(value: Any) -> str:
        rendered = str(value or "").strip()
        if not rendered or rendered.startswith("0001-01-01"):
            return ""
        return rendered[:10]

    @staticmethod
    def _decode_api(raw: bytes) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("北森新版校园岗位接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("北森新版校园岗位接口响应结构异常")
        if payload.get("Code") != 200:
            raise ValueError(
                "北森新版校园岗位接口失败（Code={}）".format(
                    payload.get("Code")
                )
            )
        if not isinstance(payload.get("Count"), int):
            raise ValueError("北森新版校园岗位接口缺少 Count")
        if not isinstance(payload.get("Data"), list):
            raise ValueError("北森新版校园岗位接口缺少 Data 数组")
        return payload

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        portal_body = fetch_bytes(homepage).decode(
            "utf-8", errors="replace"
        )
        portal_data = BeisenPortalCampaignCollector._portal_data(portal_body)

        expected_tenant = self.source.get("tenant_name", "")
        tenant_info = portal_data.get("tenantInfo")
        actual_tenant = (
            tenant_info.get("Name", "")
            if isinstance(tenant_info, dict)
            else ""
        )
        if expected_tenant and actual_tenant != expected_tenant:
            raise ValueError("北森新版招聘门户返回了非目标租户")

        portal_id = portal_data.get("PortalId")
        if not isinstance(portal_id, str) or not portal_id:
            raise ValueError("北森新版招聘门户缺少 PortalId")
        expected_portal_id = self.source.get("portal_id", "")
        if expected_portal_id and portal_id != expected_portal_id:
            raise ValueError("北森新版招聘门户 PortalId 已变化")

        pages = portal_data.get("Pages")
        if not isinstance(pages, list):
            raise ValueError("北森新版招聘门户缺少 Pages 数组")
        campus_pages = [
            page
            for page in pages
            if isinstance(page, dict)
            and str(page.get("BusinessType")) == "2"
            and page.get("PageType") == 2
        ]
        if not campus_pages:
            raise ValueError("北森新版招聘门户缺少校园招聘列表页")

        api_url = self.source.get(
            "url",
            urljoin(homepage, "/api/Jobad/GetJobAdPageList"),
        )
        page_size = max(1, min(int(self.source.get("page_size", 50)), 100))
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        min_published_at = self.source.get("min_published_at", "")[:10]
        detail_template = self.source.get(
            "detail_url_template",
            urljoin(homepage, "/campus/detail?jobAdId={job_ad_id}"),
        )

        jobs = []
        seen_ids = set()
        expected_count = None
        for page_index in range(max_pages):
            request_json = {
                "PageIndex": page_index,
                "PageSize": page_size,
                "PortalId": portal_id,
                "Category": [2],
                "DisplayFields": self.DISPLAY_FIELDS,
            }
            payload = self._decode_api(
                fetch_bytes(
                    api_url,
                    method="POST",
                    json_body=request_json,
                    headers={"Referer": homepage},
                )
            )
            if expected_count is None:
                expected_count = payload["Count"]
            elif payload["Count"] != expected_count:
                raise ValueError("北森新版校园岗位分页总数发生变化")

            items = payload["Data"]
            if not items and len(seen_ids) < expected_count:
                raise ValueError("北森新版校园岗位分页提前结束")

            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("北森新版校园岗位列表元素结构异常")
                external_id = str(item.get("Id") or "").strip()
                title = str(item.get("JobAdName") or "").strip()
                if not external_id or not title:
                    raise ValueError("北森新版校园岗位缺少必要字段")
                if str(item.get("CategoryId")) != "2":
                    raise ValueError("北森新版校园岗位接口混入非校招岗位")
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)

                raw_locations = item.get("LocNames") or []
                if not isinstance(raw_locations, list):
                    raise ValueError("北森新版校园岗位地点字段结构异常")
                location = "、".join(
                    str(value).strip()
                    for value in raw_locations
                    if str(value).strip()
                ) or self.source.get("location", "待核对")
                published_at = self._date(item.get("PostDate"))
                if min_published_at and (
                    not published_at or published_at < min_published_at
                ):
                    continue

                org = str(item.get("Org") or "").strip()
                duty = str(item.get("Duty") or "").strip()
                requirement = str(item.get("Require") or "").strip()
                description = "\n".join(
                    value
                    for value in [
                        "招聘单位/部门：{}".format(org) if org else "",
                        "岗位职责：{}".format(duty) if duty else "",
                        "任职要求：{}".format(requirement)
                        if requirement
                        else "",
                    ]
                    if value
                )
                searchable = " ".join(
                    [title, org, location, duty, requirement]
                )
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                if include_keywords and not self._contains(
                    searchable, include_keywords
                ):
                    continue
                if self._contains(searchable, exclude_keywords):
                    continue

                values = {
                    "external_id": external_id,
                    "title": title,
                    "company": self.source.get(
                        "company", self.source["name"]
                    ),
                    "company_type": self.source.get(
                        "company_type", "未知"
                    ),
                    "location": location,
                    "description": description,
                    "education": self.source.get(
                        "education",
                        "校园招聘，具体学历及毕业时间要求以岗位详情为准",
                    ),
                    "graduation_years": self.source.get(
                        "graduation_years", []
                    ),
                    "published_at": published_at,
                    "deadline": self._date(item.get("EndTime")),
                    "url": detail_template.format(
                        job_ad_id=external_id,
                        jobAdId=external_id,
                    ),
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))

            if len(seen_ids) >= expected_count:
                return jobs

        raise ValueError("北森新版校园岗位分页超过配置上限")


class ChinaResourcesCampusCollector(Collector):
    """Collect campus jobs from China Resources' public recruitment site."""

    DEFAULT_API_URL = "https://ssdp.crc.com.cn/ssdp/sys/rf/"
    PUBLIC_API_CONFIG = {
        "Api_Version": "1.0",
        "Api_ID": "crinfo.hrms",
        "App_Sub_ID": "0006000908YA",
        "App_Token": "60fe2d19e5ad491f8a02508da3efe532",
        "Sys_ID": "00060009",
        "Partner_ID": "00060000",
        "Sign": "NO_SIGN",
        "User_Token": "",
    }
    WEBSITE_API = "crc.HRMS.rm.websiteView"
    POSITION_API = "crc.HRMS.rm.synthesizeHomepagePosition"

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _date(value: Any) -> str:
        rendered = str(value or "").strip()
        if not rendered:
            return ""
        return rendered[:10]

    @staticmethod
    def _decode_response(raw: bytes, label: str) -> Any:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("华润{}接口返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict):
            raise ValueError("华润{}接口响应结构异常".format(label))
        response = payload.get("RESPONSE")
        if not isinstance(response, dict):
            raise ValueError("华润{}接口缺少 RESPONSE".format(label))
        if response.get("RETURN_CODE") != "MS000A000":
            raise ValueError(
                "华润{}接口失败（RETURN_CODE={}）".format(
                    label, response.get("RETURN_CODE")
                )
            )
        encoded = response.get("RETURN_DATA")
        if not isinstance(encoded, str):
            raise ValueError("华润{}接口缺少 RETURN_DATA".format(label))
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("华润{}接口数据编码异常".format(label)) from exc
        try:
            return json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ValueError("华润{}接口业务数据不是 JSON".format(label)) from exc

    def _api_call(
        self,
        api_id: str,
        method: str,
        param: Dict[str, Any],
        label: str,
    ) -> Any:
        public_config = dict(self.PUBLIC_API_CONFIG)
        public_config.update(self.source.get("public_api_config", {}))
        if "HRMS" not in api_id:
            raise ValueError("华润公开接口 Api_ID 配置异常")
        public_config["Api_ID"] = "{}{}".format(
            public_config["Api_ID"], api_id.split("HRMS", 1)[1]
        )
        now = datetime.now(timezone(timedelta(hours=8)))
        public_config["Time_Stamp"] = "{}:{}".format(
            now.strftime("%Y-%m-%d %H:%M:%S"),
            now.microsecond // 1000,
        )
        ssdp = base64.b64encode(
            "&".join(
                "{}={}".format(key, value)
                for key, value in public_config.items()
            ).encode("utf-8")
        ).decode("ascii")
        request_data = {"biz": {"method": method, "param": param}}
        request_json = {
            "base64String": base64.b64encode(
                json.dumps(
                    request_data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).decode("ascii")
        }
        api_url = "{}?{}".format(
            self.source.get("api_url", self.DEFAULT_API_URL),
            urlencode({"ssdp": ssdp}),
        )
        raw = fetch_bytes(
            api_url,
            method="POST",
            json_body=request_json,
            headers={
                "Referer": self.source["homepage"],
                "languageIndex": "0",
                "homepageConfigId": self.source["website_config_id"],
            },
        )
        return self._decode_response(raw, label)

    def _validate_portal(self) -> Dict[str, Any]:
        homepage = self.source["homepage"]
        homepage_body = fetch_bytes(homepage).decode(
            "utf-8", errors="replace"
        )
        native_script = self.source.get(
            "native_script_url", urljoin(homepage, "indexNative.js")
        )
        if "indexNative.js" not in homepage_body:
            raise ValueError("华润招聘官网未加载预期入口脚本，可能已经改版")
        native_body = fetch_bytes(
            native_script, headers={"Referer": homepage}
        ).decode("utf-8", errors="replace")
        website_config_id = self.source["website_config_id"]
        if website_config_id not in native_body:
            raise ValueError("华润招聘官网默认站点 ID 已变化")

        portal = self._api_call(
            self.WEBSITE_API,
            "loadComplexWebsiteStyle",
            {"id": website_config_id},
            "官网配置",
        )
        if not isinstance(portal, dict):
            raise ValueError("华润招聘官网配置响应结构异常")
        website = portal.get("websiteConfig")
        position = portal.get("positionConfig")
        if not isinstance(website, dict) or not isinstance(position, dict):
            raise ValueError("华润招聘官网配置缺少网站或岗位配置")
        if str(website.get("id")) != website_config_id:
            raise ValueError("华润招聘官网配置返回了非目标站点")
        required_name = self.source.get("required_website_name", "华润")
        if required_name not in str(website.get("websiteName") or ""):
            raise ValueError("华润招聘官网名称与预期不符")
        if website.get("configStatus") != 1:
            raise ValueError("华润招聘官网当前未启用")
        if str(position.get("complexWsConfigId")) != website_config_id:
            raise ValueError("华润招聘岗位配置不属于目标站点")
        return portal

    def _position_url(self, item: Dict[str, Any]) -> str:
        homepage = self.source["homepage"].rstrip("/") + "/"
        template = self.source.get(
            "detail_url_template",
            homepage
            + "#/complex/RecruitDetail?id={position_id}"
            "&comId={website_config_id}&typeId={type_id}",
        )
        return template.format(
            position_id=quote(str(item["pubPositionId"]), safe=""),
            website_config_id=quote(
                self.source["website_config_id"], safe=""
            ),
            type_id=quote(str(item.get("typeId") or "A02"), safe=""),
        )

    def _to_job(self, item: Dict[str, Any]) -> JobPosting:
        brand = str(item.get("brandName") or "").strip()
        company_descr = str(item.get("companyDescr") or "").strip()
        department = str(item.get("deptIdDescr") or "").strip()
        duty = str(item.get("rmJobDuty") or "").strip()
        requirement = str(item.get("rmJobRqmt") or "").strip()
        group_name = self.source.get("company", "华润集团")
        company = (
            "{} · {}".format(group_name, brand)
            if brand and brand != group_name
            else group_name
        )
        description = "\n".join(
            value
            for value in [
                "雇主品牌：{}".format(brand) if brand else "",
                "招聘单位：{}".format(company_descr) if company_descr else "",
                "招聘部门：{}".format(department) if department else "",
                "岗位职责：{}".format(duty) if duty else "",
                "任职要求：{}".format(requirement) if requirement else "",
            ]
            if value
        )
        education = str(item.get("rmEducationalRqmtDescr") or "").strip()
        if not education:
            education = self.source.get(
                "education", "校园招聘，具体学历要求以岗位详情为准"
            )
        values = {
            "external_id": str(item["pubPositionId"]),
            "title": str(item["pubPositionName"]).strip(),
            "company": company,
            "company_type": self.source.get("company_type", "央企"),
            "location": str(item.get("locationDescr") or "").strip()
            or self.source.get("location", "待核对"),
            "description": description,
            "education": education,
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": self._date(item.get("publishDate")),
            "deadline": self.source.get(
                "deadline", "以官方岗位页面为准"
            ),
            "url": self._position_url(item),
            "source_name": self.source["name"],
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        self._validate_portal()
        website_config_id = self.source["website_config_id"]
        page_size = max(1, min(int(self.source.get("page_size", 100)), 200))
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        campus_type = self.source.get("campus_type", "A02")
        min_published_at = self.source.get("min_published_at", "")[:10]
        location_keywords = self.source.get("location_keywords", [])
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        include_fields = self.source.get(
            "include_search_fields", ["pubPositionName", "deptIdDescr"]
        )

        jobs = []
        seen_ids = set()
        page_signatures = set()
        received_count = 0
        expected_total = None
        for page_num in range(1, max_pages + 1):
            result = self._api_call(
                self.POSITION_API,
                "getSynthesizeHomepagePosition",
                {
                    "homepageConfigId": website_config_id,
                    "locationDescr": "",
                    "industryType": "",
                    "rmType": campus_type,
                    "rmWorkYearsRqmt": "",
                    "rmEducationalRqmt": "",
                    "keyword": "",
                    "positionType": "",
                    "rmBusiness": "",
                    "pageNum": page_num,
                    "pageSize": page_size,
                },
                "校园岗位",
            )
            if not isinstance(result, dict):
                raise ValueError("华润校园岗位响应结构异常")
            items = result.get("data")
            total = result.get("total")
            if not isinstance(items, list) or not isinstance(total, int):
                raise ValueError("华润校园岗位响应缺少 data 或 total")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise ValueError("华润校园岗位分页总数发生变化")
            if not items and received_count < expected_total:
                raise ValueError("华润校园岗位分页提前结束")

            page_signature = tuple(
                str(item.get("pubPositionId") or "")
                for item in items
                if isinstance(item, dict)
            )
            if items and page_signature in page_signatures:
                raise ValueError("华润校园岗位接口重复返回同一分页")
            page_signatures.add(page_signature)

            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("华润校园岗位列表元素结构异常")
                external_id = str(item.get("pubPositionId") or "").strip()
                title = str(item.get("pubPositionName") or "").strip()
                if not external_id or not title:
                    raise ValueError("华润校园岗位缺少必要字段")
                if str(item.get("typeId") or "") != campus_type:
                    raise ValueError("华润校园岗位接口混入非校招岗位")
                received_count += 1
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)

                published_at = self._date(item.get("publishDate"))
                if min_published_at and (
                    not published_at or published_at < min_published_at
                ):
                    continue
                location = str(item.get("locationDescr") or "").strip()
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                include_text = " ".join(
                    str(item.get(field) or "") for field in include_fields
                )
                if include_keywords and not self._contains(
                    include_text, include_keywords
                ):
                    continue
                exclude_text = " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "pubPositionName",
                        "brandName",
                        "companyDescr",
                        "deptIdDescr",
                        "rmJobDuty",
                        "rmJobRqmt",
                    )
                )
                if self._contains(exclude_text, exclude_keywords):
                    continue
                jobs.append(self._to_job(item))

            if received_count >= expected_total:
                return jobs

        raise ValueError("华润校园岗位分页超过配置上限")


class ChinaElectronicsCampusCollector(Collector):
    """Collect campus jobs from China Electronics' public portal."""

    DEFAULT_API_URL = (
        "https://campus.cec.com.cn/student-api/api/position/search"
    )
    DEFAULT_DETAIL_URL = (
        "https://campus.cec.com.cn/student-api/api/position/find/{id}"
    )

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _decode_response(raw: bytes, label: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("中国电子{}接口返回了无效 JSON".format(label)) from exc
        if not isinstance(payload, dict):
            raise ValueError("中国电子{}接口响应结构异常".format(label))
        if payload.get("code") != "000000":
            raise ValueError(
                "中国电子{}接口失败（code={}）".format(
                    label, payload.get("code")
                )
            )
        return payload

    @staticmethod
    def _position_date(position_no: Any) -> str:
        match = re.search(r"20\d{6}", str(position_no or ""))
        if not match:
            return ""
        try:
            return datetime.strptime(match.group(0), "%Y%m%d").strftime(
                "%Y-%m-%d"
            )
        except ValueError:
            return ""

    def _validate_portal(self) -> None:
        body = fetch_bytes(self.source["homepage"]).decode(
            "utf-8", errors="replace"
        )
        required_title = self.source.get(
            "required_title", "中国电子信息产业集团有限公司招聘"
        )
        if required_title not in body:
            raise ValueError("中国电子校招官网标题与预期不符")
        if not re.search(r'/assets/index-[^"\']+\.js', body):
            raise ValueError("中国电子校招官网未加载预期入口脚本")

    def _search_page(self, page: int, page_size: int) -> Dict[str, Any]:
        body = {
            "page": page,
            "size": page_size,
            "name": "",
            "positionType": int(self.source.get("position_type", 0)),
            "functions": [],
            "cities": [],
            "degree": None,
            "orgId": [],
        }
        raw = fetch_bytes(
            self.source.get("url", self.DEFAULT_API_URL),
            method="POST",
            json_body=body,
            headers={"Referer": self.source["homepage"]},
        )
        payload = self._decode_response(raw, "校园岗位")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("中国电子校园岗位接口缺少 data")
        return data

    def _job_detail(self, item: Dict[str, Any]) -> Dict[str, Any]:
        external_id = str(item["id"])
        template = self.source.get(
            "detail_api_url_template", self.DEFAULT_DETAIL_URL
        )
        raw = fetch_bytes(
            template.format(id=quote(external_id, safe="")),
            headers={"Referer": self.source["homepage"]},
        )
        payload = self._decode_response(raw, "岗位详情")
        detail = payload.get("data")
        if not isinstance(detail, dict):
            raise ValueError("中国电子岗位详情接口缺少 data")
        if str(detail.get("id") or "") != external_id:
            raise ValueError("中国电子岗位详情返回了其他岗位")
        if detail.get("positionType") != int(
            self.source.get("position_type", 0)
        ):
            raise ValueError("中国电子校园岗位接口混入非校招岗位")
        if str(detail.get("name") or "").strip() != str(
            item.get("name") or ""
        ).strip():
            raise ValueError("中国电子岗位列表与详情标题不一致")
        return detail

    def _education(self, requirements: str) -> str:
        for part in re.split(r"[\r\n]+", requirements):
            rendered = " ".join(part.split()).strip(" ;；")
            if rendered and self._contains(
                rendered, ["学历", "本科", "硕士", "博士", "毕业生"]
            ):
                return rendered
        return self.source.get(
            "education", "校园招聘，学历及毕业时间以官网为准"
        )

    def _to_job(
        self,
        item: Dict[str, Any],
        detail: Dict[str, Any],
        position_date: str,
    ) -> JobPosting:
        requirements = str(
            detail.get("jobRequirements")
            or item.get("jobRequirements")
            or ""
        ).strip()
        duties = str(
            detail.get("jobDescription")
            or item.get("jobDescription")
            or ""
        ).strip()
        employer = str(
            detail.get("orgName") or item.get("org") or ""
        ).strip()
        second_org = str(
            detail.get("positionSecondOrg")
            or item.get("positionSecondOrg")
            or ""
        ).strip()
        function_name = str(detail.get("functionName") or "").strip()
        position_no = str(
            detail.get("positionNo") or item.get("positionNo") or ""
        ).strip()
        group_name = self.source.get("company", "中国电子")
        company = (
            "{} · {}".format(group_name, employer)
            if employer and employer != group_name
            else group_name
        )
        description = "\n".join(
            value
            for value in [
                "二级单位：{}".format(second_org) if second_org else "",
                "招聘单位：{}".format(employer) if employer else "",
                "岗位类别：{}".format(function_name)
                if function_name
                else "",
                "岗位编号：{}".format(position_no)
                if position_no
                else "",
                "岗位职责：{}".format(duties) if duties else "",
                "任职要求：{}".format(requirements)
                if requirements
                else "",
            ]
            if value
        )
        external_id = str(item["id"])
        detail_template = self.source.get(
            "detail_url_template",
            "https://campus.cec.com.cn/positionDetail?id={id}",
        )
        values = {
            "external_id": external_id,
            "title": str(item["name"]).strip(),
            "company": company,
            "company_type": self.source.get("company_type", "央企"),
            "location": str(item.get("cityName") or "").strip()
            or self.source.get("location", "待核对"),
            "description": description,
            "education": self._education(requirements),
            "graduation_years": self.source.get("graduation_years", []),
            "published_at": position_date,
            "deadline": self.source.get(
                "deadline", "以官方岗位页面为准"
            ),
            "url": detail_template.format(
                id=quote(external_id, safe="")
            ),
            "source_name": self.source["name"],
        }
        return JobPosting.from_mapping(values)

    def collect(self) -> List[JobPosting]:
        self._validate_portal()
        page_size = max(1, min(int(self.source.get("page_size", 100)), 100))
        max_pages = max(1, int(self.source.get("max_pages", 20)))
        min_position_date = self.source.get(
            "min_position_no_date", ""
        )[:10]
        location_keywords = self.source.get("location_keywords", [])
        include_title_keywords = self.source.get(
            "include_title_keywords", []
        )
        exclude_title_keywords = self.source.get(
            "exclude_title_keywords", []
        )
        exclude_text_keywords = self.source.get(
            "exclude_text_keywords", []
        )
        allowed_work_natures = self.source.get(
            "work_natures", ["全职"]
        )
        active_status = self.source.get("active_status", 5)

        jobs = []
        seen_ids = set()
        page_signatures = set()
        received_count = 0
        expected_total = None
        expected_pages = None
        for page in range(1, max_pages + 1):
            data = self._search_page(page, page_size)
            items = data.get("records")
            total = data.get("total")
            current = data.get("current")
            pages = data.get("pages")
            if (
                not isinstance(items, list)
                or not isinstance(total, int)
                or not isinstance(current, int)
                or not isinstance(pages, int)
            ):
                raise ValueError("中国电子校园岗位分页字段异常")
            if current != page:
                raise ValueError("中国电子校园岗位返回错误页码")
            if expected_total is None:
                expected_total = total
                expected_pages = pages
            elif total != expected_total or pages != expected_pages:
                raise ValueError("中国电子校园岗位分页总数发生变化")
            calculated_pages = (
                (expected_total + page_size - 1) // page_size
                if expected_total
                else 0
            )
            if expected_pages != calculated_pages:
                raise ValueError("中国电子校园岗位页数与总数不一致")
            if not items and received_count < expected_total:
                raise ValueError("中国电子校园岗位分页提前结束")

            signature = tuple(
                str(item.get("id") or "")
                for item in items
                if isinstance(item, dict)
            )
            if items and signature in page_signatures:
                raise ValueError("中国电子校园岗位重复返回同一分页")
            page_signatures.add(signature)
            received_count += len(items)

            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("中国电子校园岗位列表元素结构异常")
                external_id = str(item.get("id") or "").strip()
                title = str(item.get("name") or "").strip()
                if not external_id or not title:
                    raise ValueError("中国电子校园岗位缺少必要字段")
                if external_id in seen_ids:
                    raise ValueError("中国电子校园岗位出现重复岗位 ID")
                seen_ids.add(external_id)

                location = str(item.get("cityName") or "").strip()
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                if include_title_keywords and not self._contains(
                    title, include_title_keywords
                ):
                    continue
                if self._contains(title, exclude_title_keywords):
                    continue
                work_nature = str(item.get("workNature") or "").strip()
                if allowed_work_natures and work_nature not in allowed_work_natures:
                    continue
                all_text = " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "name",
                        "jobDescription",
                        "jobRequirements",
                        "org",
                    )
                )
                if self._contains(all_text, exclude_text_keywords):
                    continue

                position_date = self._position_date(item.get("positionNo"))
                if min_position_date and not position_date:
                    raise ValueError(
                        "中国电子候选岗位编号缺少可核验日期"
                    )
                if min_position_date and position_date < min_position_date:
                    continue

                detail = self._job_detail(item)
                if detail.get("status") != active_status:
                    continue
                jobs.append(self._to_job(item, detail, position_date))

            if received_count >= expected_total:
                return jobs

        raise ValueError("中国电子校园岗位分页超过配置上限")


class ShenzhenInvestmentHoldingsCollector(Collector):
    """Collect one company's campus jobs from Shenzhen SASAC's Elite portal."""

    PUBLIC_KEY = (
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArBl1wJJsfl2LAw1X"
        "kLzr1ON1bYAIfAIWCYBUm+GLIOtJxjluRdxwNG6jLtx8b+fns7gBJvYBLs8J"
        "63szC2+OSp2Z9qluHPKad14w+nJP7r0Nk2jAeuVy47zapYdlKbMRU6LeEvsL"
        "6dm6yy78aqrNedBznEH3zeqfq+B7rcg9t+mYrnRV/Nsbdi0xJmUEmh6ziGdW"
        "RPfOL9lI5YFynqZ3bK3WjYkbFkakPdhOR8YiPdbaqgcYFe4/n8Qcov6/80v"
        "SF6BKS0YqI+NNHH4XmrbH3mAW70lfM9eyBA/ynFd/LF9/Z+6LGF11U4dDlHO"
        "w4wYqAGUIDVNRnn2Dl3nMlobvwwIDAQAB"
    )

    @staticmethod
    def _contains(text: Any, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text or "").lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else ""

    def _validate_portal(self) -> None:
        try:
            payload = json.loads(
                fetch_bytes(self.source["portal_data_url"]).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("菁英聚鹏城门户返回了无效初始化数据") from exc
        if not isinstance(payload, dict):
            raise ValueError("菁英聚鹏城门户初始化数据结构异常")

        tenant = payload.get("tenant")
        companies = payload.get("listCompany")
        if not isinstance(tenant, dict) or not isinstance(companies, list):
            raise ValueError("菁英聚鹏城门户缺少租户或企业列表")
        if (
            str(tenant.get("tenantId") or "")
            != self.source["tenant_id"]
            or str(tenant.get("tenantName") or "")
            != self.source["tenant_name"]
        ):
            raise ValueError("菁英聚鹏城门户租户与预期不符")

        matches = [
            item
            for item in companies
            if isinstance(item, dict)
            and str(item.get("companyId") or "")
            == self.source["company_id"]
        ]
        if len(matches) != 1:
            raise ValueError(
                "菁英聚鹏城门户未唯一匹配目标企业：{}".format(
                    self.source["required_company_name"]
                )
            )
        company = matches[0]
        if (
            str(company.get("displayName") or "")
            != self.source["required_company_name"]
            or str(company.get("tenantId") or "")
            != self.source["tenant_id"]
            or str(company.get("companyPropDsc") or "") != "国企"
        ):
            raise ValueError(
                "菁英聚鹏城门户返回了非目标企业：{}".format(
                    self.source["required_company_name"]
                )
            )

    def _encrypt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        aes_key = secrets.token_hex(16)
        timestamp = int(time.time() * 1000)
        encrypted = AES.new(
            aes_key.encode("utf-8"), AES.MODE_ECB
        ).encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
        signature = hashlib.md5(
            (plaintext + str(timestamp) + aes_key).encode("utf-8")
        ).hexdigest().upper()
        public_key = RSA.import_key(base64.b64decode(self.PUBLIC_KEY))
        encrypted_key = PKCS1_v1_5.new(public_key).encrypt(
            aes_key.encode("utf-8")
        )
        return {
            "t": timestamp,
            "e": base64.b64encode(encrypted).decode("ascii"),
            "s": signature,
            "k": base64.b64encode(encrypted_key).decode("ascii"),
        }

    def _post(
        self, url: str, payload: Dict[str, Any], label: str
    ) -> Dict[str, Any]:
        raw = fetch_bytes(
            url,
            method="POST",
            json_body=self._encrypt_payload(payload),
            headers={
                "Accept": "application/json",
                "Origin": self.source.get(
                    "origin", "https://jyjpc.iucai.com.cn"
                ),
                "Referer": self.source.get(
                    "homepage", "https://jyjpc.iucai.com.cn/"
                ),
                "sourceKey": "ELITE_PC",
                "x-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "菁英聚鹏城{}接口返回了无效 JSON".format(label)
            ) from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("status") != "success"
            or envelope.get("success") is not True
            or not isinstance(envelope.get("data"), (str, dict))
        ):
            raise ValueError("菁英聚鹏城{}接口响应异常".format(label))
        data = envelope["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "菁英聚鹏城{}接口数据结构异常".format(label)
                ) from exc
        if not isinstance(data, dict):
            raise ValueError("菁英聚鹏城{}接口数据结构异常".format(label))
        return data

    def _list_page(self, page_index: int, page_size: int) -> Dict[str, Any]:
        return self._post(
            self.source["url"],
            {
                "pageIndex": page_index,
                "pageSize": page_size,
                "params": {"companyId": self.source["company_id"]},
            },
            "岗位列表",
        )

    def _detail(self, item: Dict[str, Any]) -> Dict[str, Any]:
        detail = self._post(
            self.source["detail_url"],
            {"recruitId": str(item["recruitLibId"])},
            "岗位详情",
        )
        posting = detail.get("companyRecruitLib")
        company = detail.get("company")
        if not isinstance(posting, dict) or not isinstance(company, dict):
            raise ValueError("菁英聚鹏城岗位详情缺少岗位或企业信息")
        external_id = str(item["recruitLibId"])
        if (
            str(posting.get("recruitLibId") or posting.get("id") or "")
            != external_id
            or str(posting.get("jobTitle") or "")
            != str(item.get("jobTitle") or "")
            or str(posting.get("displayName") or "")
            != str(item.get("displayName") or "")
        ):
            raise ValueError("菁英聚鹏城岗位列表与详情不一致")
        child_company_id = str(item.get("CR_COMPANY_ID") or "")
        if (
            not child_company_id
            or str(posting.get("companyId") or "") != child_company_id
            or str(company.get("companyId") or "") != child_company_id
            or str(company.get("displayName") or "")
            != str(item.get("displayName") or "")
        ):
            raise ValueError("菁英聚鹏城岗位所属企业不一致")
        return detail

    def _candidate(self, item: Dict[str, Any]) -> bool:
        title = str(item.get("jobTitle") or "").strip()
        position = str(item.get("positionDsc") or "").strip()
        location = str(item.get("workCityDsc") or "").strip()
        if self.source.get("location_keywords") and not self._contains(
            location, self.source["location_keywords"]
        ):
            return False
        searchable = " ".join([title, position])
        if self.source.get("include_title_keywords") and not self._contains(
            searchable, self.source["include_title_keywords"]
        ):
            return False
        if self._contains(
            searchable, self.source.get("exclude_title_keywords", [])
        ):
            return False
        if str(item.get("jobTypeDsc") or "") not in self.source.get(
            "work_natures", ["全职"]
        ):
            return False
        published_at = self._date(
            item.get("startDate") or item.get("createDt")
        )
        minimum = str(self.source.get("min_published_at") or "")[:10]
        if minimum and not published_at:
            raise ValueError("菁英聚鹏城候选岗位缺少可核验发布日期")
        return not minimum or published_at >= minimum

    def _to_job(
        self, item: Dict[str, Any], detail: Dict[str, Any]
    ) -> JobPosting:
        posting = detail["companyRecruitLib"]
        company = detail["company"]
        member = str(company.get("displayName") or "").strip()
        group = self.source.get(
            "company", self.source["required_company_name"]
        )
        company_name = (
            "{} · {}".format(group, member)
            if member and member != group
            else group
        )
        requirements = str(posting.get("jobReq") or "").strip()
        responsibilities = str(posting.get("jobResp") or "").strip()
        position = str(posting.get("positionDsc") or "").strip()
        description = "\n".join(
            value
            for value in [
                "所属系统企业：{}".format(member) if member else "",
                "岗位类别：{}".format(position) if position else "",
                "岗位职责：{}".format(responsibilities)
                if responsibilities
                else "",
                "任职要求：{}".format(requirements)
                if requirements
                else "",
            ]
            if value
        )
        education = str(posting.get("requireEduDsc") or "").strip()
        qualification = self.source.get("education", "")
        if qualification:
            education = "；".join(
                value for value in [education, qualification] if value
            )
        all_text = " ".join(
            [str(posting.get("jobTitle") or ""), requirements, responsibilities]
        )
        graduation_years = [2027] if "2027届" in all_text else []
        external_id = str(posting["recruitLibId"])
        return JobPosting.from_mapping(
            {
                "external_id": external_id,
                "title": str(posting["jobTitle"]).strip(),
                "company": company_name,
                "company_type": self.source.get("company_type", "国企"),
                "location": str(item.get("workCityDsc") or "").strip()
                or str(company.get("cityDsc") or "").strip()
                or self.source.get("location", "待核对"),
                "description": description,
                "education": education,
                "graduation_years": graduation_years,
                "published_at": self._date(
                    posting.get("startDate") or posting.get("createDt")
                ),
                "deadline": self._date(posting.get("endDate"))
                or self.source.get("deadline", "以官方岗位页面为准"),
                "url": self.source["detail_url_template"].format(
                    recruit_id=quote(external_id, safe=""),
                    company_id=quote(self.source["company_id"], safe=""),
                    tenant_id=quote(self.source["tenant_id"], safe=""),
                ),
                "source_name": self.source["name"],
            }
        )

    def collect(self) -> List[JobPosting]:
        self._validate_portal()
        page_size = max(1, min(int(self.source.get("page_size", 100)), 100))
        max_pages = max(1, int(self.source.get("max_pages", 10)))
        expected_total = None
        received_count = 0
        seen_ids = set()
        page_signatures = set()
        jobs = []

        for page_index in range(max_pages):
            data = self._list_page(page_index, page_size)
            items = data.get("data")
            params = data.get("params")
            try:
                total = int(data.get("total"))
                returned_page = int(data.get("pageIndex"))
            except (TypeError, ValueError) as exc:
                raise ValueError("菁英聚鹏城岗位分页字段异常") from exc
            if (
                not isinstance(items, list)
                or not isinstance(params, dict)
                or returned_page != page_index
                or str(params.get("companyId") or "")
                != self.source["company_id"]
                or str(params.get("companyType") or "") != "1"
                or str(params.get("recruitType") or "")
                != str(self.source.get("campus_recruit_type", "2"))
            ):
                raise ValueError("菁英聚鹏城岗位分页或筛选条件异常")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise ValueError("菁英聚鹏城岗位分页总数发生变化")
            if not items and received_count < expected_total:
                raise ValueError("菁英聚鹏城岗位分页提前结束")

            signature = tuple(
                str(item.get("recruitLibId") or "")
                for item in items
                if isinstance(item, dict)
            )
            if items and signature in page_signatures:
                raise ValueError("菁英聚鹏城岗位重复返回同一分页")
            page_signatures.add(signature)
            received_count += len(items)

            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("菁英聚鹏城岗位列表元素结构异常")
                external_id = str(item.get("recruitLibId") or "").strip()
                title = str(item.get("jobTitle") or "").strip()
                if not external_id or not title:
                    raise ValueError("菁英聚鹏城岗位缺少必要字段")
                if external_id in seen_ids:
                    raise ValueError("菁英聚鹏城岗位出现重复岗位 ID")
                seen_ids.add(external_id)
                if not self._candidate(item):
                    continue

                detail = self._detail(item)
                posting = detail["companyRecruitLib"]
                if (
                    str(posting.get("recruitType") or "")
                    != str(self.source.get("campus_recruit_type", "2"))
                    or str(posting.get("pubState") or "") != "1"
                ):
                    continue
                text = " ".join(
                    str(posting.get(field) or "")
                    for field in (
                        "jobTitle",
                        "positionDsc",
                        "jobReq",
                        "jobResp",
                        "workYearsDsc",
                    )
                )
                if self._contains(
                    text, self.source.get("exclude_text_keywords", [])
                ):
                    continue
                allowed_experience = self.source.get(
                    "work_years", ["应届生", "不限"]
                )
                if (
                    allowed_experience
                    and str(posting.get("workYearsDsc") or "")
                    not in allowed_experience
                ):
                    continue
                jobs.append(self._to_job(item, detail))

            if received_count >= expected_total:
                if received_count != expected_total:
                    raise ValueError("菁英聚鹏城岗位数量超过接口总数")
                return jobs

        raise ValueError("菁英聚鹏城岗位分页超过配置上限")


class _BeisenLegacyJobListParser(HTMLParser):
    """Parse the server-rendered job table used by older Beisen portals."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[Dict[str, Any]] = []
        self.max_page = 1
        self._in_row = False
        self._in_cell = False
        self._cells: List[Dict[str, str]] = []
        self._cell_parts: List[str] = []
        self._cell_href = ""

    def handle_starttag(self, tag: str, attrs: Iterable[Any]) -> None:
        tag = tag.lower()
        values = dict(attrs)
        if tag == "a":
            href = values.get("href", "")
            page_match = re.search(r"[?&]PageIndex=(\d+)", href, re.I)
            if page_match:
                self.max_page = max(self.max_page, int(page_match.group(1)))
            if self._in_cell and not self._cell_href:
                self._cell_href = href
        elif tag == "tr":
            self._in_row = True
            self._cells = []
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._cell_parts = []
            self._cell_href = ""

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td" and self._in_cell:
            self._cells.append(
                {
                    "text": " ".join(" ".join(self._cell_parts).split()),
                    "href": self._cell_href,
                }
            )
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if (
                len(self._cells) == 5
                and "/xzxq" in self._cells[0]["href"].lower()
            ):
                self.rows.append(
                    {
                        "title": self._cells[0]["text"],
                        "href": self._cells[0]["href"],
                        "company": self._cells[1]["text"],
                        "location": self._cells[2]["text"],
                        "published_at": self._cells[3]["text"],
                    }
                )
            self._in_row = False
            self._cells = []


class BeisenLegacyCampusCollector(Collector):
    """Collect campus jobs from a server-rendered legacy Beisen portal."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        page_url_template = self.source.get(
            "page_url_template",
            homepage.rstrip("/") + "/?PageIndex={page}",
        )
        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        min_published_at = self.source.get("min_published_at", "")
        max_pages = max(1, int(self.source.get("max_pages", 20)))

        jobs = []
        seen_ids = set()
        last_page = 1
        for page in range(1, max_pages + 1):
            page_url = (
                homepage
                if page == 1
                else page_url_template.format(page=page)
            )
            body = fetch_bytes(page_url).decode("utf-8", errors="replace")
            required_text = self.source.get("required_text", "职位名称")
            if required_text and required_text not in body:
                raise ValueError("北森旧版校招页未出现预期标识，可能已经改版")

            parser = _BeisenLegacyJobListParser()
            parser.feed(body)
            last_page = max(last_page, parser.max_page)
            for item in parser.rows:
                title = item["title"]
                company = item["company"]
                location = item["location"]
                published_at = item["published_at"].replace(".", "-")[:10]
                href = item["href"]
                job_ids = parse_qs(urlsplit(href).query).get("jobId", [])
                if (
                    not title
                    or not company
                    or not location
                    or not published_at
                    or not job_ids
                ):
                    raise ValueError("北森旧版校招岗位缺少必要字段")

                external_id = str(job_ids[0])
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                if min_published_at and published_at < min_published_at[:10]:
                    continue

                searchable = " ".join([title, company, location])
                if location_keywords and not self._contains(
                    location, location_keywords
                ):
                    continue
                if include_keywords and not self._contains(
                    searchable, include_keywords
                ):
                    continue
                if self._contains(searchable, exclude_keywords):
                    continue

                values = {
                    "external_id": external_id,
                    "title": title,
                    "company": company,
                    "company_type": self.source.get(
                        "company_type", "未知"
                    ),
                    "location": location,
                    "description": self.source.get("description", ""),
                    "education": self.source.get(
                        "education",
                        "校园招聘，具体学历要求以岗位详情为准",
                    ),
                    "graduation_years": self.source.get(
                        "graduation_years", []
                    ),
                    "published_at": published_at,
                    "deadline": "",
                    "url": urljoin(homepage, href),
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))

            if page >= last_page:
                return jobs

        raise ValueError("北森旧版校招岗位分页超过配置上限")


class LiepinStaticCampusCollector(Collector):
    """Collect a verified campaign from a public Liepin static job dataset."""

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(str(text).lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    @staticmethod
    def _visible_text(body: str) -> str:
        parser = _LinkParser()
        parser.feed(body)
        return " ".join(" ".join(parser.text_parts).split())

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        page_body = fetch_bytes(homepage).decode("utf-8", errors="replace")
        required_text = self.source.get("required_text", "招聘岗位")
        if required_text and required_text not in page_body:
            raise ValueError("猎聘静态校招页未出现预期标识，可能已经改版")

        try:
            items = json.loads(
                fetch_bytes(
                    self.source["url"],
                    headers={"Referer": homepage},
                ).decode("utf-8-sig")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("猎聘静态校招岗位返回了无效 JSON") from exc
        if not isinstance(items, list):
            raise ValueError("猎聘静态校招岗位缺少岗位数组")
        if len(items) > max(1, int(self.source.get("max_items", 500))):
            raise ValueError("猎聘静态校招岗位超过配置数量上限")

        include_keywords = self.source.get("include_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])
        location_keywords = self.source.get("location_keywords", [])
        candidates = []
        seen_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("猎聘静态校招岗位列表元素结构异常")
            title = str(
                item.get("jobName") or item.get("jobTitle") or ""
            ).strip()
            company = str(item.get("company") or "").strip()
            location = str(
                item.get("address") or item.get("workplace") or ""
            ).strip()
            link = str(
                item.get("link") or item.get("shareUrl") or ""
            ).strip()
            if not title or not company or not location or not link:
                raise ValueError("猎聘静态校招岗位缺少必要字段")

            external_id = (
                urlsplit(link).path.rstrip("/").split("/")[-1]
                .removesuffix(".shtml")
            )
            if not external_id:
                raise ValueError("猎聘静态校招岗位缺少稳定 ID")
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            searchable = " ".join(
                str(item.get(field) or "")
                for field in (
                    "jobName",
                    "jobTitle",
                    "Department",
                    "Category",
                    "major",
                    "job_requirements",
                    "job_description",
                    "所属企业",
                    "company",
                )
            )
            if location_keywords and not self._contains(
                location, location_keywords
            ):
                continue
            if include_keywords and not self._contains(
                searchable, include_keywords
            ):
                continue
            if self._contains(searchable, exclude_keywords):
                continue
            candidates.append((external_id, item, title, company, location, link))

        if not candidates:
            return []

        target_campaign_keywords = self.source.get(
            "target_campaign_keywords", []
        )
        previous_campaign_keywords = self.source.get(
            "previous_campaign_keywords", []
        )
        if not target_campaign_keywords:
            raise ValueError("猎聘静态校招来源未配置目标届别")

        preferred_domains = self.source.get(
            "campaign_probe_domains", ["duomian.com"]
        )
        probe_links = list(dict.fromkeys(item[5] for item in candidates))
        probe_links.sort(
            key=lambda link: 0
            if any(
                domain.lower() in urlsplit(link).netloc.lower()
                for domain in preferred_domains
            )
            else 1
        )
        campaign_active = False
        max_probes = max(1, int(self.source.get("max_campaign_probes", 5)))
        for link in probe_links[:max_probes]:
            detail_body = fetch_bytes(
                link,
                headers={"Referer": homepage},
            ).decode("utf-8", errors="replace")
            visible_text = self._visible_text(detail_body)
            if self._contains(visible_text, target_campaign_keywords):
                campaign_active = True
                break
            if previous_campaign_keywords and self._contains(
                visible_text, previous_campaign_keywords
            ):
                return []
        if not campaign_active:
            raise ValueError("猎聘静态校招岗位无法确认目标届别")

        jobs = []
        for external_id, item, title, company, location, link in candidates:
            description_parts = []
            owner = _html_fragment_text(item.get("所属企业", ""))
            department = _html_fragment_text(item.get("Department", ""))
            category = _html_fragment_text(item.get("Category", ""))
            major = _html_fragment_text(item.get("major", ""))
            requirements = _html_fragment_text(
                item.get("job_requirements", "")
            )
            duties = _html_fragment_text(item.get("job_description", ""))
            salary = _html_fragment_text(item.get("Salary", ""))
            recruits = str(item.get("recruits") or "").strip()
            if owner and owner != company:
                description_parts.append("所属企业：{}".format(owner))
            if department:
                description_parts.append("部门/方向：{}".format(department))
            if category:
                description_parts.append("岗位类别：{}".format(category))
            if major:
                description_parts.append("专业要求：{}".format(major))
            if requirements:
                description_parts.append("任职要求：{}".format(requirements))
            if duties:
                description_parts.append("职位描述：{}".format(duties))
            if salary:
                description_parts.append("参考薪资：{}".format(salary))
            if recruits:
                description_parts.append("招聘人数：{}".format(recruits))

            values = {
                "external_id": external_id,
                "title": title,
                "company": company,
                "company_type": self.source.get("company_type", "未知"),
                "location": location,
                "description": "｜".join(description_parts),
                "education": str(item.get("edu") or ""),
                "graduation_years": self.source.get(
                    "graduation_years", []
                ),
                "deadline": self.source.get("deadline", ""),
                "url": link,
                "source_name": self.source["name"],
            }
            jobs.append(JobPosting.from_mapping(values))
        return jobs


class GdutCampusNoticeCollector(Collector):
    """Scan recent public GDUT recruitment notices for a target campaign."""

    _OBFUSCATED_FRAGMENT = re.compile(
        r'Base64\.decode\(unzip\("([^"]+)"\)\.substr\((\d+)\)\)'
        r"\.substr\((\d+)\)"
    )

    @classmethod
    def _decoded_fragments(cls, body: str) -> List[str]:
        matches = cls._OBFUSCATED_FRAGMENT.findall(body)
        if not matches:
            raise ValueError("广工招聘公告页缺少公开内容片段，可能已经改版")

        fragments = []
        try:
            for encoded, compressed_prefix, html_prefix in matches:
                compressed = base64.b64decode(encoded, validate=True)
                wrapped_base64 = zlib.decompress(compressed)
                inner_base64 = wrapped_base64[int(compressed_prefix) :]
                wrapped_html = base64.b64decode(inner_base64, validate=True)
                fragments.append(
                    wrapped_html[int(html_prefix) :].decode("utf-8")
                )
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            zlib.error,
        ) as exc:
            raise ValueError("广工招聘公告页公开内容无法解码，可能已经改版") from exc
        return fragments

    @staticmethod
    def _contains(text: str, keywords: Iterable[str]) -> bool:
        compacted = "".join(text.lower().split())
        return any(
            "".join(str(keyword).lower().split()) in compacted
            for keyword in keywords
        )

    def collect(self) -> List[JobPosting]:
        homepage = self.source["homepage"]
        first_page_url = self.source.get("first_page_url", homepage)
        page_url_template = self.source.get("page_url_template", "")
        max_pages = max(1, int(self.source.get("max_pages", 1)))
        company_keywords = self.source.get(
            "company_keywords",
            [self.source.get("company", self.source["name"])],
        )
        target_keywords = self.source.get("target_keywords", [])
        exclude_keywords = self.source.get("exclude_keywords", [])

        jobs = []
        seen_urls = set()
        for page in range(1, max_pages + 1):
            if page == 1:
                page_url = first_page_url
            elif page_url_template:
                page_url = page_url_template.format(page=page)
            else:
                break

            body = fetch_bytes(page_url).decode("utf-8", errors="replace")
            fragments = self._decoded_fragments(body)
            decoded_html = "\n".join(fragments)
            parser = _LinkParser()
            parser.feed(decoded_html)
            notice_links = [
                link
                for link in parser.links
                if "/campus/view/id/" in link["href"]
            ]
            if not notice_links:
                if "empty-container" in decoded_html:
                    break
                raise ValueError("广工招聘公告页没有找到公告链接，可能已经改版")

            for link in notice_links:
                title = " ".join(link["text"].split())
                searchable = "{} {}".format(title, link["href"])
                if not self._contains(searchable, company_keywords):
                    continue
                if target_keywords and not self._contains(
                    searchable, target_keywords
                ):
                    continue
                if self._contains(searchable, exclude_keywords):
                    continue

                detail_url = urljoin(homepage, link["href"])
                if detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
                values = {
                    "external_id": link["href"].rstrip("/").split("/")[-1],
                    "title": title,
                    "company": self.source.get("company", self.source["name"]),
                    "company_type": self.source.get("company_type", "未知"),
                    "location": self.source.get("location", "待核对"),
                    "description": self.source.get("description", ""),
                    "education": self.source.get("education", ""),
                    "graduation_years": self.source.get(
                        "graduation_years", []
                    ),
                    "url": detail_url,
                    "source_name": self.source["name"],
                }
                jobs.append(JobPosting.from_mapping(values))
        return jobs


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
    "accenture_early_career": AccentureEarlyCareerCollector,
    "beisen_legacy_campus": BeisenLegacyCampusCollector,
    "beisen_modern_campus": BeisenModernCampusCollector,
    "beisen_portal_campaign": BeisenPortalCampaignCollector,
    "byd_campus": BydCampusCollector,
    "campaign_watch": CampaignWatchCollector,
    "meituan_official_campus": MeituanCampusCollector,
    "ceair_campus": CeairCampusCollector,
    "cmb_campus": CmbCampusCollector,
    "csg_api": ChinaSouthernPowerGridCollector,
    "china_electronics_campus": ChinaElectronicsCampusCollector,
    "china_resources_campus": ChinaResourcesCampusCollector,
    "cvte_campus": CvteCampusCollector,
    "fixture_json": FixtureJsonCollector,
    "gdut_campus_notice": GdutCampusNoticeCollector,
    "gdrc_group": GdrcGroupCollector,
    "giihg_campus": GiihgCampusCollector,
    "gzrecruit_company": GzRecruitCompanyCollector,
    "hsbc_programme": HsbcProgrammeCollector,
    "hotjob_campus": HotjobCampusCollector,
    "honor_campus": HonorCampusCollector,
    "iguopin_company": IguopinCompanyCollector,
    "huawei_campus": HuaweiCampusCollector,
    "ibm_entry_level": IbmEntryLevelCollector,
    "json_api": JsonApiCollector,
    "liepin_static_campus": LiepinStaticCampusCollector,
    "moka_campus": MokaCampusCollector,
    "netease_game_campus": NeteaseGameCampusCollector,
    "pingan_campus": PinganCampusCollector,
    "pwc_graduate_campaign": PwcGraduateCampaignCollector,
    "shein_campus": SheinCampusCollector,
    "shenzhen_investment_holdings": ShenzhenInvestmentHoldingsCollector,
    "sf_tech_campus": SfTechCampusCollector,
    "tencent_campus": TencentCampusCollector,
    "tcl_hotjob_campus": TclHotjobCampusCollector,
    "html_links": HtmlLinksCollector,
    "notice_json": NoticeJsonCollector,
    "web_notice": WebNoticeCollector,
    "xiaohongshu_campus": XiaohongshuCampusCollector,
    "zhaopin_campus_company": ZhaopinCampusCompanyCollector,
}


def build_collector(source: Dict[str, Any]) -> Collector:
    collector_type = source.get("collector", source.get("type"))
    try:
        collector_class = COLLECTOR_TYPES[collector_type]
    except KeyError as exc:
        raise ValueError("不支持的采集器类型: {}".format(collector_type)) from exc
    return collector_class(source)
