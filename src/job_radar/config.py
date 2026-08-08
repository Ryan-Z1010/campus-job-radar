from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


class ConfigError(ValueError):
    pass


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
        included_sources = included.get("sources")
        if not isinstance(included_sources, list):
            raise ConfigError("来源配置 include 文件必须包含 sources 数组: {}".format(include_path))
        sources.extend(included_sources)

    base_dir = Path(path).resolve().parent.parent
    # Recruitment-season labels and graduation-cohort labels are not the
    # same thing. In August 2026, employers may call the same upcoming
    # cycle either "2026秋招" or "2027届校园招聘". Keep both families so a
    # campaign watch does not miss an announcement merely because the portal
    # uses the season year instead of the cohort year. Eligibility still
    # needs to be checked from the linked official notice.
    campaign_keywords = [
        "2026秋招",
        "2026届秋招",
        "2026秋季校园招聘",
        "2026年秋季校园招聘",
        "2026届秋季校园招聘",
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
                # Preserve source-specific markers while adding the current
                # autumn-season vocabulary. This covers portals that announce
                # the 2026 recruitment season even when their source config
                # was originally written around the 2027 cohort.
                normalized["target_keywords"] = list(
                    dict.fromkeys(
                        list(configured_keywords) + campaign_keywords[:5]
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
