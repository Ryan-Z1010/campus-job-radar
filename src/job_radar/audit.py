from __future__ import annotations

import time
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .collectors import USER_AGENT
from .network import urlopen_with_retry


def audit_sources(sources: List[Dict[str, Any]], timeout: int = 15) -> List[Dict[str, Any]]:
    results = []
    for source in sources:
        url = source.get("homepage") or source.get("url")
        if not url:
            results.append(
                {"id": source.get("id"), "name": source.get("name"), "status": "无网址"}
            )
            continue
        started = time.monotonic()
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urlopen_with_retry(request, timeout=timeout, opener=urlopen) as response:
                content_type = response.headers.get("Content-Type", "")
                status = str(response.status)
                final_url = response.geturl()
        except HTTPError as exc:
            status = "HTTP {}".format(exc.code)
            content_type = ""
            final_url = url
        except (URLError, TimeoutError, OSError) as exc:
            status = "失败: {}".format(exc)
            content_type = ""
            final_url = url
        results.append(
            {
                "id": source.get("id"),
                "name": source.get("name"),
                "enabled": source.get("enabled", False),
                "status": status,
                "content_type": content_type,
                "final_url": final_url,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "note": source.get("note", ""),
            }
        )
    return results
