from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


class ConfigError(ValueError):
    pass


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
    base_dir = Path(path).resolve().parent.parent
    result = []
    for source in sources:
        normalized = dict(source)
        if normalized.get("path") and not Path(normalized["path"]).is_absolute():
            normalized["path"] = str(base_dir / normalized["path"])
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
