from __future__ import annotations

from typing import Any

from job_finder.ats.http import fetch_json
from job_finder.models import SourceConfig


def fetch_greenhouse_jobs(source: SourceConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{source.identifier}/jobs?content=true"
    payload, raw_body = fetch_json(url)
    normalized_jobs: list[dict[str, Any]] = []
    for job in payload.get("jobs", []):
        location = job.get("location") or {}
        metadata = {
            "department": (job.get("departments") or [{}])[0].get("name") if job.get("departments") else None,
            "office": (job.get("offices") or [{}])[0].get("name") if job.get("offices") else None,
        }
        normalized_jobs.append(
            {
                "source_job_id": str(job.get("id")) if job.get("id") is not None else None,
                "company": job.get("company_name"),
                "title": job.get("title") or "",
                "location_text": location.get("name") if isinstance(location, dict) else str(location),
                "description_text": job.get("content") or "",
                "posted_date": job.get("updated_at"),
                "apply_url": job.get("absolute_url") or "",
                "remote_hint": "remote" in str(location.get("name", "") if isinstance(location, dict) else location).lower(),
                "metadata": metadata,
            }
        )
    payload["_raw_body"] = raw_body
    return payload, normalized_jobs
