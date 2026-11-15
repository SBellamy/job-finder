from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from job_finder.ats.http import fetch_json
from job_finder.models import SourceConfig
from job_finder.ats.snapshots import board_jobs


def _metadata_int(source: SourceConfig, key: str, default: int) -> int:
    try:
        return max(0, int(source.metadata.get(key, default)))
    except (TypeError, ValueError):
        return default


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


def _location_text(job: dict[str, Any]) -> str:
    location = job.get("location")
    if not isinstance(location, dict):
        return str(location or "")
    return ", ".join(
        str(part)
        for part in [
            location.get("city"),
            location.get("region"),
            location.get("country"),
        ]
        if part
    )


def _description_text(job: dict[str, Any]) -> str:
    job_ad = job.get("jobAd") if isinstance(job.get("jobAd"), dict) else {}
    sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else {}
    description = _textify(sections)
    return description or _textify(job.get("description"))


def _posted_date(job: dict[str, Any]) -> str | None:
    for key in ("releasedDate", "publishedDate", "createdOn", "updatedDate"):
        if job.get(key):
            return str(job[key])
    return None


def _apply_url(source: SourceConfig, job: dict[str, Any], posting_id: str | None) -> str:
    url = job.get("postingUrl") or job.get("applyUrl")
    if url:
        return str(url)
    if posting_id:
        return f"https://jobs.smartrecruiters.com/{source.identifier}/{posting_id}"
    return source.careers_url or f"https://jobs.smartrecruiters.com/{source.identifier}"


def _fetch_detail(source: SourceConfig, posting_id: str) -> tuple[dict[str, Any], str]:
    detail_url = f"https://api.smartrecruiters.com/v1/companies/{quote(source.identifier)}/postings/{quote(posting_id)}"
    payload, raw_body = fetch_json(detail_url)
    if not isinstance(payload, dict):
        return {}, raw_body
    return payload, raw_body


def fetch_smartrecruiters_jobs(source: SourceConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    page_limit = max(1, min(_metadata_int(source, "page_limit", 100), 100))
    max_pages = max(1, _metadata_int(source, "max_pages", 2))
    max_detail_fetches = _metadata_int(source, "max_detail_fetches", 40)

    postings: list[dict[str, Any]] = []
    raw_pages: list[str] = []
    for page_index in range(max_pages):
        offset = page_index * page_limit
        url = f"https://api.smartrecruiters.com/v1/companies/{quote(source.identifier)}/postings?limit={page_limit}&offset={offset}"
        payload, raw_body = fetch_json(url)
        raw_pages.append(raw_body)
        page_jobs = board_jobs(payload, "content")
        if not page_jobs:
            break
        postings.extend(job for job in page_jobs if isinstance(job, dict))
        if len(page_jobs) < page_limit:
            break

    normalized_jobs: list[dict[str, Any]] = []
    detail_raw: list[str] = []
    for job in postings:
        posting_id = str(job.get("id") or job.get("uuid") or "") or None
        detail = dict(job)
        if posting_id and len(detail_raw) < max_detail_fetches:
            try:
                detail_payload, raw_body = _fetch_detail(source, posting_id)
                detail = {**job, **detail_payload}
                detail_raw.append(raw_body)
            except Exception:
                detail = dict(job)

        location_text = _location_text(detail)
        remote_hint = bool((detail.get("location") or {}).get("remote") if isinstance(detail.get("location"), dict) else False)
        normalized_jobs.append(
            {
                "source_job_id": posting_id,
                "company": (detail.get("company") or {}).get("name") if isinstance(detail.get("company"), dict) else source.company,
                "title": detail.get("name") or detail.get("title") or "",
                "location_text": location_text,
                "description_text": _description_text(detail),
                "posted_date": _posted_date(detail),
                "apply_url": _apply_url(source, detail, posting_id),
                "remote_hint": remote_hint or "remote" in f"{location_text} {detail.get('name') or ''}".lower(),
                "metadata": {
                    "department": (detail.get("department") or {}).get("label") if isinstance(detail.get("department"), dict) else detail.get("department"),
                    "employment_type": detail.get("typeOfEmployment"),
                    "experience_level": detail.get("experienceLevel"),
                    "smartrecruiters_ref": detail.get("ref"),
                },
            }
        )

    wrapper = {
        "jobs": postings,
        "detail_fetches": len(detail_raw),
        "_raw_body": json.dumps({"list_pages": raw_pages, "details": detail_raw}),
    }
    return wrapper, normalized_jobs
