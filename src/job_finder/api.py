from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from job_finder.models import utc_now_iso
from job_finder.storage import JobRepository
from job_finder.storage import source_quality_score


VALID_STATUSES = {"new", "saved", "dismissed", "applied"}
VALID_JOB_SORTS = {"ranked", "newest"}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def list_jobs(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = min(int(query.get("limit") or "100"), 250)
    min_score = query.get("min_score")
    sort_by = (query.get("sort") or "ranked").strip().lower()
    if sort_by not in VALID_JOB_SORTS:
        raise ValueError(f"Invalid sort: {sort_by}")
    jobs = repository.list_jobs(
        status=query.get("status") or None,
        source_type=query.get("source_type") or None,
        company=query.get("company") or None,
        min_score=int(min_score) if min_score else None,
        eligible_only=(query.get("eligible_only", "true").lower() != "false"),
        only_new=(query.get("only_new", "false").lower() == "true"),
        sort_by=sort_by,
        limit=limit,
    )
    return {"items": [job.to_item() for job in jobs], "count": len(jobs)}


def get_freshness(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    max_age_hours = max(1, int(query.get("max_age_hours") or "24"))
    latest_run = repository.latest_crawl_run()
    if latest_run is None:
        return {
            "ok": False,
            "max_age_hours": max_age_hours,
            "latest_run": None,
            "age_seconds": None,
            "error": "No completed crawl runs found",
        }

    finished_at = _parse_iso_datetime(latest_run.finished_at)
    if finished_at is None:
        return {
            "ok": False,
            "max_age_hours": max_age_hours,
            "latest_run": latest_run.to_item(),
            "age_seconds": None,
            "error": "Latest crawl run has an invalid finished_at timestamp",
        }
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)

    age_seconds = int((datetime.now(UTC) - finished_at).total_seconds())
    return {
        "ok": age_seconds <= max_age_hours * 3600,
        "max_age_hours": max_age_hours,
        "latest_run": latest_run.to_item(),
        "age_seconds": age_seconds,
    }


def update_job(repository: JobRepository, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    notes = payload.get("notes")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    record = repository.update_job(job_id, status=status, notes=notes)
    if record is None:
        raise KeyError(job_id)
    return record.to_item()


def get_digest(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = min(int(query.get("limit") or "20"), 50)
    min_score = int(query.get("min_score") or "40")
    candidate_jobs = repository.list_jobs(
        min_score=min_score,
        eligible_only=(query.get("eligible_only", "true").lower() != "false"),
        only_new=(query.get("only_new", "true").lower() != "false"),
        limit=max(limit * 5, 100),
    )
    jobs = candidate_jobs[:limit]
    reason_counts = Counter(reason for job in jobs for reason in job.score_reasons)
    company_counts = Counter(job.company for job in jobs)
    source_counts = Counter(job.source_type for job in jobs)
    return {
        "generated_at": utc_now_iso(),
        "count": len(jobs),
        "items": [job.to_item() for job in jobs],
        "summary": {
            "top_companies": company_counts.most_common(10),
            "top_sources": source_counts.most_common(10),
            "top_reasons": reason_counts.most_common(10),
        },
    }


def list_sources(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = min(int(query.get("limit") or "100"), 250)
    status_values = tuple(
        value.strip()
        for value in (query.get("statuses") or "active,candidate").split(",")
        if value.strip()
    )
    records = repository.list_source_records(
        enabled_only=(query.get("enabled_only", "true").lower() != "false"),
        statuses=status_values,
        limit=limit,
    )
    items = []
    for record in records:
        payload = record.to_item()
        payload["quality_score"] = source_quality_score(record)
        items.append(payload)
    return {"items": items, "count": len(items)}
