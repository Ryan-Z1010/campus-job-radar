from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

from .models import JobPosting
from .network import urlopen_with_retry


USER_AGENT = "CampusJobRadar/0.1 (+https://github.com/Ryan-Z1010/campus-job-radar)"


def fetch_bytes(
    url: str,
    timeout: int = 20,
    method: str = "GET",
    json_body: Any = None,
    form_body: Dict[str, Any] = None,
    headers: Dict[str, str] = None,
) -> bytes:
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
    with urlopen_with_retry(request, timeout=timeout, opener=urlopen) as response:
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


class _MissingValueDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


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
                description_parts = [
                    str(item.get("postTypeName") or ""),
                    project_name,
                    str(item.get("department") or ""),
                ]
                values = {
                    "external_id": post_id,
                    "title": title,
                    "company": company,
                    "company_type": self.source.get(
                        "company_type", "未知"
                    ),
                    "location": location,
                    "description": "｜".join(
                        part for part in description_parts if part
                    ),
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
    "beisen_portal_campaign": BeisenPortalCampaignCollector,
    "campaign_watch": CampaignWatchCollector,
    "csg_api": ChinaSouthernPowerGridCollector,
    "fixture_json": FixtureJsonCollector,
    "gdut_campus_notice": GdutCampusNoticeCollector,
    "gzrecruit_company": GzRecruitCompanyCollector,
    "hotjob_campus": HotjobCampusCollector,
    "json_api": JsonApiCollector,
    "html_links": HtmlLinksCollector,
    "notice_json": NoticeJsonCollector,
    "web_notice": WebNoticeCollector,
    "zhaopin_campus_company": ZhaopinCampusCompanyCollector,
}


def build_collector(source: Dict[str, Any]) -> Collector:
    collector_type = source.get("type")
    try:
        collector_class = COLLECTOR_TYPES[collector_type]
    except KeyError as exc:
        raise ValueError("不支持的采集器类型: {}".format(collector_type)) from exc
    return collector_class(source)
