from __future__ import annotations

import ssl
import time
from http.client import RemoteDisconnected
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DEFAULT_ATTEMPTS = 2
DEFAULT_BACKOFF_SECONDS = 0.5
RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _is_certificate_error(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, URLError) else exc
    return isinstance(reason, ssl.SSLCertVerificationError)


def urlopen_with_retry(
    request: Any,
    timeout: int,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    opener: Callable[..., Any] = None,
) -> Any:
    """Open a request with one bounded retry for known transient failures."""
    if attempts < 1:
        raise ValueError("attempts 必须至少为 1")

    open_request = opener or urlopen
    for attempt in range(attempts):
        try:
            return open_request(request, timeout=timeout)
        except HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_STATUS or attempt == attempts - 1:
                raise
            if exc.fp is not None:
                exc.close()
        except (
            URLError,
            TimeoutError,
            ConnectionError,
            RemoteDisconnected,
            ssl.SSLError,
        ) as exc:
            if _is_certificate_error(exc) or attempt == attempts - 1:
                raise

        if backoff_seconds > 0:
            time.sleep(backoff_seconds * (attempt + 1))

    raise RuntimeError("网络重试流程异常结束")
