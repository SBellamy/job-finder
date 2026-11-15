from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from job_finder.models import SourceConfig, stable_source_id, text_excerpt, utc_now_iso
from job_finder.storage import JobRepository
from job_finder.storage import _sort_timestamp, source_quality_score
from job_finder.normalize import normalize_whitespace
from job_finder.discovery import ATS_DISCOVERY_CONFIG, extract_source_from_url


VALID_STATUSES = {"new", "saved", "dismissed", "applied"}
VALID_JOB_SORTS = {"ranked", "newest", "newest_posted"}
DEFAULT_JOBS_LIMIT = 50
MAX_JOBS_LIMIT = 1000
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


def _bounded_limit(value: str | None, *, default: int, maximum: int) -> int:
    return max(1, min(int(value or str(default)), maximum))


def _search_filters(query: dict[str, str | None]) -> dict[str, Any]:
    timestamps = {}
    for key in ("found_after", "seen_after"):
        value = query.get(key) or None
        if value:
            parsed = _parse_iso_datetime(value)
            if parsed is None or parsed.tzinfo is None:
                raise ValueError(f"{key} must be an ISO timestamp with a timezone")
            value = parsed.astimezone(UTC).isoformat()
        timestamps[key] = value
    return {
        **timestamps,
        "search": (query.get("search") or "").strip() or None,
        "confirmed_us_only": (query.get("confirmed_us_only") or "false").lower() == "true",
        "available_only": (query.get("available_only") or "false").lower() == "true",
    }


def list_jobs(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = _bounded_limit(query.get("limit"), default=DEFAULT_JOBS_LIMIT, maximum=MAX_JOBS_LIMIT)
    min_score = query.get("min_score")
    sort_by = (query.get("sort") or "ranked").strip().lower()
    if sort_by not in VALID_JOB_SORTS:
        raise ValueError(f"Invalid sort: {sort_by}")
    filters = {
        **_search_filters(query),
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
        search=filters["search"],
        found_after=filters["found_after"],
        seen_after=filters["seen_after"],
        available_only=filters["available_only"],
        confirmed_us_only=filters["confirmed_us_only"],
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
        **_search_filters(query),
        sort_by=sort_by,
        limit=_bounded_limit(query.get("limit"), default=MAX_EXPORT_LIMIT, maximum=MAX_EXPORT_LIMIT),
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


def _duplicate_key(job: Any) -> tuple[str, str, str]:
    return tuple(normalize_whitespace(value).casefold() for value in (job.company, job.title, job.location_text))


def get_digest(repository: JobRepository, query: dict[str, str | None]) -> dict[str, Any]:
    limit = _bounded_limit(query.get("limit"), default=20, maximum=50)
    hours = _bounded_limit(query.get("hours"), default=24, maximum=168)
    max_per_company = _bounded_limit(query.get("max_per_company"), default=2, maximum=50)
    min_score = int(query.get("min_score") or "40")
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=hours)
    candidate_jobs = repository.list_jobs(
        min_score=min_score,
        eligible_only=(query.get("eligible_only", "true").lower() != "false"),
        only_new=(query.get("only_new", "true").lower() != "false"),
        confirmed_us_only=(query.get("confirmed_us_only", "true").lower() != "false"),
        found_after=window_start.isoformat(),
        seen_after=(now - timedelta(days=7)).isoformat(),
        available_only=True,
        limit=1000,
    )
    reviewed = {_duplicate_key(job) for job in repository.list_feedback_jobs(limit=1000)}
    seen: set[tuple[str, str, str]] = set()
    company_counts: Counter[str] = Counter()
    jobs = []
    for job in candidate_jobs:
        key = _duplicate_key(job)
        company_key = normalize_whitespace(job.company).casefold()
        posted = _sort_timestamp(job.posted_date)
        # A newly discovered old posting is not a fresh opportunity.
        if posted and posted < (now - timedelta(days=30)).timestamp():
            continue
        if key in seen or key in reviewed or company_counts[company_key] >= max_per_company:
            continue
        seen.add(key)
        company_counts[company_key] += 1
        jobs.append(job)
        if len(jobs) >= limit:
            break
    reason_counts = Counter(reason for job in jobs for reason in job.score_reasons)
    companies = Counter(job.company for job in jobs)
    source_counts = Counter(job.source_type for job in jobs)
    return {
        "generated_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "hours": hours,
        "count": len(jobs),
        "items": [_job_summary(job) for job in jobs],
        "summary": {
            "top_companies": companies.most_common(10),
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


def create_source(repository: JobRepository, payload: dict[str, Any]) -> dict[str, Any]:
    source_payload = dict(payload)
    url = source_payload.pop("url", None)
    if url:
        detected = next((source for provider in ATS_DISCOVERY_CONFIG if (source := extract_source_from_url(provider, str(url)))), None)
        if detected is None or not str(url).startswith("https://"):
            raise ValueError("Use an HTTPS employer board URL from Ashby, Greenhouse, Lever, SmartRecruiters, or Workable")
        source_payload = {**detected.to_dict(), **source_payload}
    source_payload["source_type"] = str(source_payload.get("source_type") or "").strip().lower()
    if source_payload["source_type"] not in ATS_DISCOVERY_CONFIG:
        raise ValueError("Unsupported source_type")
    metadata = dict(source_payload.get("metadata") or {})
    priority = source_payload.pop("priority", None)
    seeded = source_payload.pop("seeded", True)
    if seeded:
        metadata["seeded"] = True
        metadata["status"] = str(metadata.get("status") or "manual")
        metadata["priority"] = int(metadata.get("priority") or priority or 75)
    elif priority is not None:
        metadata["priority"] = int(priority)
    source_payload["metadata"] = metadata
    source = SourceConfig.from_dict(source_payload)
    operation = repository.upsert_source(source)
    source_id = stable_source_id(source.source_type, source.identifier)
    records = repository.list_source_records(
        enabled_only=False,
        statuses=("manual", "active", "candidate", "low_value", "dead"),
        limit=5000,
    )
    for record in records:
        if record.source_id == source_id:
            item = record.to_item()
            item["quality_score"] = source_quality_score(record)
            return {"operation": operation, "item": item}
    return {"operation": operation, "item": {"source_id": source_id, **source.to_dict()}}


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
