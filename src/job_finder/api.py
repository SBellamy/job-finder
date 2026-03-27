from __future__ import annotations

from collections import Counter
from typing import Any

from job_finder.models import utc_now_iso
from job_finder.storage import JobRepository
from job_finder.storage import source_quality_score


VALID_STATUSES = {"new", "saved", "dismissed", "applied"}


def list_jobs(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = min(int(query.get("limit") or "100"), 250)
    min_score = query.get("min_score")
    jobs = repository.list_jobs(
        status=query.get("status") or None,
        source_type=query.get("source_type") or None,
        company=query.get("company") or None,
        min_score=int(min_score) if min_score else None,
        eligible_only=(query.get("eligible_only", "true").lower() != "false"),
        only_new=(query.get("only_new", "false").lower() == "true"),
        limit=limit,
    )
    return {"items": [job.to_item() for job in jobs], "count": len(jobs)}


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
