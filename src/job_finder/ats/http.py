from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    retries: int = 2,
    backoff_seconds: float = 0.6,
) -> tuple[Any, str]:
    request_headers = {
        "Accept": "application/json,text/plain;q=0.8,*/*;q=0.5",
        "User-Agent": "job-finder/0.1",
        **(headers or {}),
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = Request(url, headers=request_headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "ignore")
            return json.loads(body), body
        except HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_STATUS_CODES or attempt >= retries:
                raise
        except URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(backoff_seconds * (2**attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch JSON from {url}")
