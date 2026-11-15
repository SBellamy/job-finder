from __future__ import annotations

from typing import Any

from job_finder.ats.http import fetch_json
from job_finder.models import SourceConfig
from job_finder.ats.snapshots import board_jobs


def _textify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_textify(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_textify(item) for item in value.values())
    return str(value)


def fetch_lever_jobs(source: SourceConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = f"https://api.lever.co/v0/postings/{source.identifier}?mode=json"
    payload, raw_body = fetch_json(url)
    postings = board_jobs({"jobs": payload}) if isinstance(payload, list) else board_jobs(payload, "data")
    normalized_jobs: list[dict[str, Any]] = []
    for job in postings:
        categories = job.get("categories") or {}
        metadata = {
            "department": categories.get("team"),
            "commitment": categories.get("commitment"),
            "salary_range": job.get("salaryRange"),
        }
        normalized_jobs.append(
            {
                "source_job_id": job.get("id"),
                "title": job.get("text") or "",
                "location_text": "; ".join(dict.fromkeys([str(value) for value in [categories.get("location"), *(categories.get("allLocations") or []), job.get("country")] if value])),
                "description_text": " ".join(
                    part
                    for part in [
                        _textify(job.get("descriptionPlain")),
                        _textify(job.get("additionalPlain")),
                        _textify(job.get("lists")),
                    ]
                    if part
                ),
                "posted_date": job.get("createdAt"),
                "apply_url": job.get("hostedUrl") or job.get("applyUrl") or "",
                "remote_type": job.get("workplaceType"),
                "remote_hint": job.get("workplaceType", categories.get("workplaceType")) == "remote" or "remote" in str(categories.get("location", "")).lower(),
                "metadata": metadata,
            }
        )
    wrapper = {"jobs": postings, "_raw_body": raw_body, "_snapshot_complete": False}
    return wrapper, normalized_jobs
