from __future__ import annotations

from typing import Any

from job_finder.ats.http import fetch_json
from job_finder.models import SourceConfig
from job_finder.ats.snapshots import board_jobs


def fetch_workable_jobs(source: SourceConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = f"https://www.workable.com/api/accounts/{source.identifier}?details=true"
    payload, raw_body = fetch_json(url)
    jobs = board_jobs(payload)
    normalized_jobs: list[dict[str, Any]] = []
    for job in jobs:
        location = job.get("location") or {}
        location_text = ", ".join(part for part in [location.get("city"), location.get("region"), location.get("country")] if part)
        remote_hint = bool(job.get("remote")) or "remote" in str(job.get("title", "")).lower()
        normalized_jobs.append(
            {
                "source_job_id": job.get("shortcode") or job.get("id"),
                "title": job.get("title") or "",
                "location_text": location_text,
                "description_text": job.get("description") or "",
                "posted_date": job.get("published"),
                "apply_url": job.get("url") or "",
                "remote_hint": remote_hint,
                "metadata": {
                    "employment_type": job.get("employment_type"),
                    "department": (job.get("department") or {}).get("title") if isinstance(job.get("department"), dict) else job.get("department"),
                },
            }
        )
    payload["_snapshot_complete"] = False
    payload["_raw_body"] = raw_body
    return payload, normalized_jobs

