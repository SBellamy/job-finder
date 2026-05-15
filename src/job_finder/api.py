from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from job_finder.models import text_excerpt, utc_now_iso
from job_finder.storage import JobRepository
from job_finder.storage import source_quality_score


VALID_STATUSES = {"new", "saved", "dismissed", "applied"}
VALID_JOB_SORTS = {"ranked", "newest"}
DEFAULT_JOBS_LIMIT = 50
MAX_JOBS_LIMIT = 100
MAX_EXPORT_LIMIT = 1000
VALID_DISMISS_REASONS = {
    "already_seen",
    "low_priority",
    "not_infrastructure",
    "not_us",
    "off_target",
    "too_junior",
    "too_senior",
    "weak_company_fit",
}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _job_summary(job: Any) -> dict[str, Any]:
    item = job.to_item()
    description = str(item.pop("description_text", "") or "")
    metadata = dict(item.get("metadata") or {})
    item.pop("metadata", None)
    item.pop("raw_payload_s3_key", None)
    item["dismiss_reason"] = metadata.get("dismiss_reason", "")
    item["description_excerpt"] = text_excerpt(description)
    item["has_description"] = bool(description.strip())
    return item


def _cursor_signature(filters: dict[str, Any]) -> str:
    payload = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(*, offset: int, signature: str) -> str:
    payload = json.dumps({"offset": offset, "signature": signature}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(value: str | None, *, expected_signature: str) -> int:
    if not value:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
        offset = int(payload.get("offset", 0))
        signature = str(payload.get("signature") or "")
    except Exception as exc:
        raise ValueError("Invalid cursor") from exc
    if offset < 0 or signature != expected_signature:
        raise ValueError("Invalid cursor")
    return offset


def list_jobs(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = min(int(query.get("limit") or str(DEFAULT_JOBS_LIMIT)), MAX_JOBS_LIMIT)
    min_score = query.get("min_score")
    sort_by = (query.get("sort") or "ranked").strip().lower()
    if sort_by not in VALID_JOB_SORTS:
        raise ValueError(f"Invalid sort: {sort_by}")
    filters = {
        "company": query.get("company") or None,
        "eligible_only": query.get("eligible_only", "true").lower() != "false",
        "min_score": int(min_score) if min_score else None,
        "only_new": query.get("only_new", "false").lower() == "true",
        "sort": sort_by,
        "source_type": query.get("source_type") or None,
        "status": query.get("status") or None,
    }
    cursor_signature = _cursor_signature(filters)
    offset = _decode_cursor(query.get("cursor"), expected_signature=cursor_signature)
    jobs = repository.list_jobs(
        status=filters["status"],
        source_type=filters["source_type"],
        company=filters["company"],
        min_score=filters["min_score"],
        eligible_only=filters["eligible_only"],
        only_new=filters["only_new"],
        sort_by=sort_by,
        offset=offset,
        limit=limit + 1,
    )
    page_jobs = jobs[:limit]
    next_cursor = None
    if len(jobs) > limit:
        next_cursor = _encode_cursor(offset=offset + limit, signature=cursor_signature)
    return {"items": [_job_summary(job) for job in page_jobs], "count": len(page_jobs), "next_cursor": next_cursor}


def get_job(repository: JobRepository, job_id: str) -> dict[str, Any]:
    record = repository.get_job(job_id)
    if record is None:
        raise KeyError(job_id)
    return record.to_item()


def _job_export_row(job: Any) -> dict[str, Any]:
    item = job.to_item()
    metadata = dict(item.get("metadata") or {})
    return {
        "company": item.get("company", ""),
        "title": item.get("title", ""),
        "status": item.get("status", ""),
        "applied_at": metadata.get("applied_at", ""),
        "score": item.get("score", ""),
        "location": item.get("location_text", ""),
        "source_type": item.get("source_type", ""),
        "posted_date": item.get("posted_date", ""),
        "first_seen_at": item.get("first_seen_at", ""),
        "last_seen_at": item.get("last_seen_at", ""),
        "apply_url": item.get("apply_url", ""),
        "notes": item.get("notes", ""),
        "dismiss_reason": metadata.get("dismiss_reason", ""),
        "score_reasons": "; ".join(item.get("score_reasons") or []),
    }


def export_jobs(repository: JobRepository, query: dict[str, str | None]) -> dict[str, str]:
    export_format = (query.get("format") or "csv").strip().lower()
    if export_format not in {"csv", "json"}:
        raise ValueError(f"Invalid export format: {export_format}")

    min_score = query.get("min_score")
    sort_by = (query.get("sort") or "ranked").strip().lower()
    if sort_by not in VALID_JOB_SORTS:
        raise ValueError(f"Invalid sort: {sort_by}")
    jobs = repository.list_jobs(
        status=query.get("status") or None,
        source_type=query.get("source_type") or None,
        company=query.get("company") or None,
        min_score=int(min_score) if min_score else None,
        eligible_only=(query.get("eligible_only", "false").lower() != "false"),
        only_new=(query.get("only_new", "false").lower() == "true"),
        sort_by=sort_by,
        limit=min(int(query.get("limit") or str(MAX_EXPORT_LIMIT)), MAX_EXPORT_LIMIT),
    )
    rows = [_job_export_row(job) for job in jobs]
    filename = f"job-finder-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.{export_format}"

    if export_format == "json":
        return {
            "body": json.dumps({"generated_at": utc_now_iso(), "count": len(rows), "items": rows}),
            "content_type": "application/json",
            "filename": filename,
        }

    buffer = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else list(_job_export_row_placeholder().keys())
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return {"body": buffer.getvalue(), "content_type": "text/csv; charset=utf-8", "filename": filename}


def _job_export_row_placeholder() -> dict[str, str]:
    return {
        "company": "",
        "title": "",
        "status": "",
        "applied_at": "",
        "score": "",
        "location": "",
        "source_type": "",
        "posted_date": "",
        "first_seen_at": "",
        "last_seen_at": "",
        "apply_url": "",
        "notes": "",
        "dismiss_reason": "",
        "score_reasons": "",
    }


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
    dismiss_reason = payload.get("dismiss_reason")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if dismiss_reason is not None and dismiss_reason not in VALID_DISMISS_REASONS and dismiss_reason != "":
        raise ValueError(f"Invalid dismiss_reason: {dismiss_reason}")
    record = repository.update_job(job_id, status=status, notes=notes, dismiss_reason=dismiss_reason)
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
        "items": [_job_summary(job) for job in jobs],
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


def update_source_control(repository: JobRepository, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    blocked = payload.get("blocked")
    priority = payload.get("priority")
    note = payload.get("note")
    if blocked is not None and not isinstance(blocked, bool):
        raise ValueError("blocked must be a boolean")
    if priority is not None:
        priority = max(-100, min(100, int(priority)))
    if note is not None:
        note = str(note)

    record = repository.update_source_control(source_id, blocked=blocked, priority=priority, note=note)
    if record is None:
        raise KeyError(source_id)
    item = record.to_item()
    item["quality_score"] = source_quality_score(record)
    return item
