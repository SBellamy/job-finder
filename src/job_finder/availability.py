"""Conservative availability observations, separate from personal review status."""
from __future__ import annotations

from typing import Any
from job_finder.read_model import sort_timestamp


def missing_observation(previous: dict[str, Any], *, last_seen_at: str,
                        observed_at: str, check_id: str) -> dict[str, Any] | None:
    if previous.get("state") == "closed" or previous.get("check_id") == check_id:
        return None
    now = sort_timestamp(observed_at)
    if sort_timestamp(last_seen_at) >= now or sort_timestamp(previous.get("checked_at")) >= now:
        return None
    since = previous.get("missing_since") or observed_at
    count = int(previous.get("missing_checks") or 0) + 1
    closed = count >= 2 and now - sort_timestamp(since) >= 12 * 3600
    return {
        "state": "closed" if closed else "missing",
        "missing_since": since,
        "missing_checks": count,
        "checked_at": observed_at,
        "check_id": check_id,
    }
