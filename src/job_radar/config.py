from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


class ConfigError(ValueError):
    pass


def _normalize_monitoring(data: Mapping[str, Any]) -> Dict[str, Any]:
    keywords = data.get("excluded_company_keywords", [])
    if not isinstance(keywords, list) or not all(
        isinstance(item, str) and item.strip() for item in keywords
    ):
        raise ConfigError(
            "监控配置 excluded_company_keywords 必须是字符串数组"
        )
    return {
        "daily_scan_all": bool(data.get("daily_scan_all", False)),
        "excluded_company_keywords": list(
            dict.fromkeys(item.strip() for item in keywords)
        ),
    }


def load_monitoring(path: str = "configs/monitoring.json") -> Dict[str, Any]:
    """Load user-specific monitoring preferences.

    The source pool is shared, while exclusions such as companies that the
    user has already applied to belong to the user's monitoring preferences.
    Keeping these preferences in a small config file lets both scheduled
    workflows and local runs apply exactly the same rules.
    """

    if isinstance(path, Mapping):
        return _normalize_monitoring(path)
    config_path = Path(path)
    if not config_path.exists():
        return {"daily_scan_all": False, "excluded_company_keywords": []}
    return _normalize_monitoring(load_json(path))


def _excluded_company_match(value: Any, keywords: Iterable[str]) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return False
    return any(str(keyword).strip().casefold() in text for keyword in keywords)


def source_is_excluded(source: Dict[str, Any], monitoring: Dict[str, Any]) -> bool:
    """Return whether a source belongs to a company the user already applied to."""

    keywords = monitoring.get("excluded_company_keywords", [])
    haystack = " ".join(
        str(source.get(field, ""))
        for field in ("id", "name", "company", "company_prefix")
    )
    return _excluded_company_match(haystack, keywords)


def filter_sources_for_monitoring(
    sources: Iterable[Dict[str, Any]], monitoring: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Remove sources for companies that should no longer be scanned/notified."""

    return [source for source in sources if not source_is_excluded(source, monitoring)]


def job_is_excluded(job: Any, monitoring: Dict[str, Any]) -> bool:
    """Return whether a collected job belongs to a user's excluded company."""

    keywords = monitoring.get("excluded_company_keywords", [])
    haystack = " ".join(
        str(getattr(job, field, "") or "")
        for field in ("company", "source_name")
    )
    return _excluded_company_match(haystack, keywords)


def _campaign_fallback(source: Dict[str, Any]) -> Dict[str, str]:
    """Return an official, read-only fallback portal for campaign watches.

    A number of state-owned enterprise sites are intermittently unavailable to
    automated clients (timeouts, TLS errors, or anti-bot responses).  The
    enterprise homepage remains the primary source; this fallback is only used
    by the collector when the primary page cannot be read or its marker is
    temporarily unavailable.
    """
    location = str(source.get("location", ""))
    portals = [
        ("北京", "https://gzw.beijing.gov.cn/", "国资"),
        ("上海", "https://www.gzw.sh.gov.cn/", "国资"),
        ("深圳", "https://gzw.sz.gov.cn/", "国资"),
        ("广州", "https://gzw.gz.gov.cn/", "国资"),
        ("重庆", "https://gzw.cq.gov.cn/", "国资"),
        ("湖南", "https://gzw.hunan.gov.cn/", "国资"),
        ("福建", "https://gzw.fujian.gov.cn/", "国资"),
    ]
    for city, homepage, required_text in portals:
        if city in location:
            return {
                "homepage": homepage,
                "required_text": required_text,
            }
    return {"homepage": "https://www.gov.cn/", "required_text": "国务院"}


def load_json(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError("配置文件不存在: {}".format(config_path)) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError("JSON 配置格式错误 {}: {}".format(config_path, exc)) from exc
    if not isinstance(value, dict):
        raise ConfigError("配置文件顶层必须是 JSON 对象: {}".format(config_path))
    return value


def load_profile(path: str) -> Dict[str, Any]:
    profile = load_json(path)
    required = ["graduation", "preferred_cities", "company_type_priority"]
    missing = [key for key in required if key not in profile]
    if missing:
        raise ConfigError("求职配置缺少字段: {}".format(", ".join(missing)))
    return profile


def load_sources(path: str) -> List[Dict[str, Any]]:
    data = load_json(path)
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise ConfigError("来源配置必须包含 sources 数组")
    include_paths = data.get("includes", [])
    if include_paths is None:
        include_paths = []
    if not isinstance(include_paths, list) or not all(
        isinstance(item, str) and item.strip() for item in include_paths
    ):
        raise ConfigError("来源配置 includes 必须是非空字符串数组")
    config_dir = Path(path).resolve().parent
    for include in include_paths:
        include_path = config_dir / include
        included = load_json(str(include_path))
        included_sources = included.get("sources", [])
        if included.get("company_groups"):
            defaults = included.get("source_defaults", {})
            if not isinstance(defaults, dict):
                raise ConfigError("来源配置 source_defaults 必须是对象: {}".format(include_path))
            expanded = []
            for group in included["company_groups"]:
                if not isinstance(group, dict):
                    raise ConfigError("来源配置 company_groups 的每项必须是对象: {}".format(include_path))
                names = group.get("names")
                prefix = group.get("id_prefix")
                if not isinstance(names, list) or not all(isinstance(name, str) and name.strip() for name in names):
                    raise ConfigError("来源配置 company_groups.names 必须是非空字符串数组: {}".format(include_path))
                if not isinstance(prefix, str) or not prefix.strip():
                    raise ConfigError("来源配置 company_groups.id_prefix 必须是非空字符串: {}".format(include_path))
                overrides = {key: value for key, value in group.items() if key not in {"names", "id_prefix"}}
                for index, company in enumerate(names, start=1):
                    source = dict(defaults)
                    source.update(overrides)
                    source.update(
                        {
                            "id": "{}_{}".format(prefix, index),
                            "name": "{}2027校招监控".format(company),
                            "company": company,
                        }
                    )
                    expanded.append(source)
            included_sources = list(included_sources) + expanded
        if not isinstance(included_sources, list):
            raise ConfigError("来源配置 include 文件必须包含 sources 数组: {}".format(include_path))
        sources.extend(included_sources)

    base_dir = Path(path).resolve().parent.parent
    # Recruitment-season labels and graduation-cohort labels are not the
    # same thing. The active monitoring window is 2026-07-01 through
    # 2027-06-30, so portals may describe a matching campaign as 2026
    # second-half recruitment, 2026 autumn recruitment, 2027 spring
    # recruitment, or 2027-campus hiring. Keep all common labels so a
    # campaign watch does not miss an announcement merely because the portal
    # uses the season year instead of the cohort year. Eligibility still
    # needs to be checked from the linked official notice.
    campaign_keywords = [
        "2026下半年招聘",
        "2026年下半年招聘",
        "2026下半年校招",
        "2026年下半年校招",
        "2026下半年校园招聘",
        "2026年下半年校园招聘",
        "2026秋招",
        "2026校园招聘",
        "2026届校园招聘",
        "2026年校园招聘",
        "2026届秋招",
        "2026秋季招聘",
        "2026年秋季招聘",
        "2026秋季校园招聘",
        "2026年秋季校园招聘",
        "2026届秋季校园招聘",
        "2026-2027校园招聘",
        "2026-2027年校园招聘",
        "2026-2027年度校园招聘",
        "2026-2027年秋季校园招聘",
        "2026-2027年度秋季校园招聘",
        "2027上半年招聘",
        "2027年上半年招聘",
        "2027上半年校招",
        "2027年上半年校招",
        "2027上半年校园招聘",
        "2027年上半年校园招聘",
        "2027春招",
        "2027届春招",
        "2027春季招聘",
        "2027年春季招聘",
        "2027春季校园招聘",
        "2027年春季校园招聘",
        "2027届春季校园招聘",
        "2027校园招聘",
        "2027届校园招聘",
        "2027年校园招聘",
        "2027秋招",
        "2027届秋招",
    ]
    result = []
    for source in sources:
        normalized = dict(source)
        if normalized.get("path") and not Path(normalized["path"]).is_absolute():
            normalized["path"] = str(base_dir / normalized["path"])
        if normalized.get("type") == "campaign_watch":
            configured_keywords = normalized.get("target_keywords")
            if configured_keywords:
                # Preserve source-specific markers while adding the complete
                # active-window vocabulary. This covers portals that announce
                # the recruitment season even when their source config was
                # originally written around only one cohort label.
                normalized["target_keywords"] = list(
                    dict.fromkeys(
                        list(configured_keywords) + campaign_keywords
                    )
                )
            else:
                normalized["target_keywords"] = list(campaign_keywords)
            normalized.setdefault(
                "title",
                "{}（等待近期秋招/校园招聘公告）".format(
                    normalized.get("name", normalized["id"])
                ),
            )
            normalized.setdefault("location", "北京、上海、广州、深圳及全国所属单位")
            normalized.setdefault("graduation_years", [2027])
            normalized.setdefault(
                "campaign_window", {"start": "2026-07-01", "end": "2027-06-30"}
            )
            normalized.setdefault("description", "请进入官方入口核对AI、数据、软件与数字化岗位。")
            normalized.setdefault("education", "应届毕业生，具体要求以官方公告为准")
            fallback = _campaign_fallback(normalized)
            normalized.setdefault("fallback_homepage", fallback["homepage"])
            normalized.setdefault(
                "fallback_required_text", fallback["required_text"]
            )
        result.append(normalized)
    return result


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
