from __future__ import annotations

from typing import Any

from job_finder.ats.http import fetch_json
from job_finder.models import SourceConfig


def _location_text(job: dict[str, Any]) -> str:
    parts: list[str] = []
    for location in [job, *(job.get("secondaryLocations") or [])]:
        name = location.get("location") or ""
        if isinstance(name, dict):
            name = name.get("locationName") or ""
        address = (location.get("address") or {}).get("postalAddress") or {}
        parts.extend(str(value) for value in [name, address.get("addressRegion"), address.get("addressCountry")] if value)
    return "; ".join(dict.fromkeys(parts))


def fetch_ashby_jobs(source: SourceConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{source.identifier}?includeCompensation=true"
    payload, raw_body = fetch_json(url)
    jobs = payload.get("jobs", [])
    normalized_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if job.get("isListed") is False:
            continue
        normalized_jobs.append(
            {
                "source_job_id": job.get("id") or job.get("jobPostingId"),
                "title": job.get("title") or "",
                "location_text": _location_text(job),
                "description_text": job.get("descriptionHtml") or job.get("description") or "",
                "posted_date": job.get("publishedAt") or job.get("createdAt"),
                "apply_url": job.get("jobUrl") or job.get("absoluteUrl") or "",
                "remote_type": job.get("workplaceType"),
                "remote_hint": bool(job.get("isRemote")) or "remote" in str(job.get("title", "")).lower(),
                "metadata": {
                    "department": (job.get("team") or {}).get("name") if isinstance(job.get("team"), dict) else None,
                    "employment_type": job.get("employmentType"),
                    "compensation": job.get("compensation"),
                },
            }
        )
    payload["_raw_body"] = raw_body
    return payload, normalized_jobs

