from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> tuple[dict[str, Any], str]:
    request = Request(url, headers={"User-Agent": "job-finder/0.1", **(headers or {})})
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body), body

