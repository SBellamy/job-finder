from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from job_finder.ats import fetch_jobs
from job_finder.discovery import discover_sources
from job_finder.models import CrawlRunRecord, DiscoverySettings, JobRecord, SourceConfig, stable_job_id, utc_now_iso
from job_finder.normalize import canonicalize_url, classify_us_location, description_fingerprint, detect_remote_type, is_us_location, strip_html
from job_finder.scoring import FeedbackProfile, build_feedback_profile, score_job
from job_finder.storage import JobRepository, RawPayloadStore


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def build_job_record(
    source: SourceConfig,
    raw_job: dict[str, object],
    raw_payload_key: str | None,
    feedback_profile: FeedbackProfile | None = None,
) -> JobRecord:
    source_job_id = str(raw_job.get("source_job_id")) if raw_job.get("source_job_id") is not None else None
    fallback_url = source.careers_url or f"https://{source.source_type}.local/{source.identifier}/{source_job_id or 'job'}"
    apply_url = canonicalize_url(str(raw_job.get("apply_url") or fallback_url))
    location_text = str(raw_job.get("location_text") or "")
    remote_type = detect_remote_type(location_text, bool(raw_job.get("remote_hint")))
    is_remote = remote_type == "remote"
    us_location_confidence = classify_us_location(location_text, remote_type)
    is_us_based = is_us_location(location_text, remote_type)
    description_text = strip_html(str(raw_job.get("description_text") or ""))
    now = utc_now_iso()
    job = JobRecord(
        job_id=stable_job_id(source.source_type, source_job_id, apply_url),
        source_job_id=source_job_id,
        canonical_url=apply_url,
        apply_url=apply_url,
        source_type=source.source_type,
        source_name=f"{source.company}:{source.identifier}",
        company=str(raw_job.get("company") or source.company),
        title=str(raw_job.get("title") or ""),
        location_text=location_text,
        remote_type=remote_type,
        posted_date=str(raw_job.get("posted_date")) if raw_job.get("posted_date") else None,
        first_seen_at=now,
        last_seen_at=now,
        description_text=description_text,
        score=0,
        score_reasons=[],
        is_eligible=False,
        is_remote=is_remote,
        is_us_based=is_us_based,
        us_location_confidence=us_location_confidence,
        hash_fingerprint=description_fingerprint(str(raw_job.get("title") or ""), str(raw_job.get("company") or source.company), description_text),
        raw_payload_s3_key=raw_payload_key,
        metadata=dict(raw_job.get("metadata") or {}),
    )
    job.score, job.score_reasons, job.is_eligible = score_job(job, source, feedback_profile=feedback_profile)
    return job


def _dedupe_sources(sources: list[SourceConfig]) -> list[SourceConfig]:
    deduped: list[SourceConfig] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.source_type, source.identifier.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def _iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _select_low_value_rechecks(
    repository: JobRepository,
    *,
    retry_after_hours: int,
    limit: int,
) -> list[SourceConfig]:
    if limit <= 0:
        return []
    threshold = datetime.now(UTC).timestamp() - (retry_after_hours * 3600)
    selected: list[SourceConfig] = []
    records = repository.list_source_records(
        statuses=("low_value",),
        limit=max(limit * 4, 20),
    )
    for record in records:
        last_checked_at = _iso_to_datetime(record.last_checked_at)
        if last_checked_at is None or last_checked_at.timestamp() <= threshold:
            selected.append(record.to_source_config())
        if len(selected) >= limit:
            break
    return selected


def resolve_sources(
    explicit_sources: list[SourceConfig],
    discovery_settings: DiscoverySettings,
    repository: JobRepository,
) -> list[SourceConfig]:
    if explicit_sources:
        return explicit_sources
    fresh_sources = discover_sources(discovery_settings)
    for source in fresh_sources:
        repository.upsert_source(source)
    active_sources = repository.list_sources(
        statuses=("active",),
        limit=max(discovery_settings.max_sources_per_run * 2, discovery_settings.max_sources_per_ats),
    )
    candidate_sources = repository.list_sources(
        statuses=("candidate",),
        limit=max(discovery_settings.max_sources_per_run, discovery_settings.max_sources_per_ats),
    )
    low_value_sources = _select_low_value_rechecks(
        repository,
        retry_after_hours=discovery_settings.low_value_retry_hours,
        limit=discovery_settings.max_low_value_rechecks_per_run,
    )
    combined_sources = _dedupe_sources([*fresh_sources, *active_sources, *candidate_sources, *low_value_sources])
    return combined_sources[: discovery_settings.max_sources_per_run]


def collect_sources(
    sources: list[SourceConfig],
    repository: JobRepository,
    payload_store: RawPayloadStore,
    *,
    discovery_settings: DiscoverySettings | None = None,
) -> CrawlRunRecord:
    settings = discovery_settings or DiscoverySettings()
    active_sources = resolve_sources(sources, settings, repository)
    feedback_jobs = repository.list_feedback_jobs(limit=1000)
    feedback_profile = build_feedback_profile(feedback_jobs)
    started_at = utc_now_iso()
    run = CrawlRunRecord(
        run_id=f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}",
        started_at=started_at,
        source="discovery" if not sources else "manual",
    )
    logger.info(
        "Starting collection run %s with %s sources and %s feedback jobs",
        run.run_id,
        len(active_sources),
        len(feedback_jobs),
    )
    if not active_sources:
        run.errors_count += 1
        run.errors.append("No ATS sources available for collection")
    for source in active_sources:
        if not source.enabled:
            continue
        run.pages_checked += 1
        try:
            payload, raw_jobs = fetch_jobs(source)
            raw_key = payload_store.store(run.run_id, f"{source.company}-{source.identifier}", payload.get("_raw_body", ""))
            run.jobs_found += len(raw_jobs)
            built_jobs = [build_job_record(source, raw_job, raw_key, feedback_profile=feedback_profile) for raw_job in raw_jobs]
            eligible_jobs = sum(1 for job in built_jobs if job.is_eligible)
            high_score_jobs = sum(1 for job in built_jobs if job.score >= 60)
            total_score = sum(job.score for job in built_jobs)
            top_score = max((job.score for job in built_jobs), default=0)
            repository.record_source_result(
                source,
                was_success=True,
                jobs_found=len(built_jobs),
                eligible_jobs=eligible_jobs,
                high_score_jobs=high_score_jobs,
                total_score=total_score,
                top_score=top_score,
                disable_after_errors=settings.source_error_threshold,
            )
            for job in built_jobs:
                result = repository.upsert_job(job)
                if result == "inserted":
                    run.jobs_inserted += 1
                else:
                    run.jobs_updated += 1
            logger.info(
                "Run %s processed %s (%s:%s): found=%s inserted=%s updated=%s eligible=%s high_score=%s",
                run.run_id,
                source.company,
                source.source_type,
                source.identifier,
                len(built_jobs),
                run.jobs_inserted,
                run.jobs_updated,
                eligible_jobs,
                high_score_jobs,
            )
        except Exception as exc:  # pragma: no cover - kept broad to protect scheduled runs
            run.errors_count += 1
            error_message = f"{source.company} ({source.source_type}:{source.identifier}): {exc}"
            repository.record_source_result(
                source,
                was_success=False,
                error_message=error_message,
                disable_after_errors=settings.source_error_threshold,
            )
            run.errors.append(error_message)
            logger.exception("Run %s failed for %s (%s:%s)", run.run_id, source.company, source.source_type, source.identifier)
    run.finished_at = utc_now_iso()
    repository.record_crawl_run(run)
    logger.info(
        "Completed run %s: pages_checked=%s jobs_found=%s inserted=%s updated=%s errors=%s",
        run.run_id,
        run.pages_checked,
        run.jobs_found,
        run.jobs_inserted,
        run.jobs_updated,
        run.errors_count,
    )
    return run
