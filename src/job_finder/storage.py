from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from job_finder.models import CrawlRunRecord, JobRecord, SourceCatalogRecord, SourceConfig, stable_source_id, utc_now_iso


def source_quality_score(record: SourceCatalogRecord) -> int:
    jobs_seen = max(record.jobs_seen_total, 1)
    eligible_ratio = record.eligible_jobs_total / jobs_seen
    high_score_ratio = record.high_score_jobs_total / jobs_seen
    score = 0
    score += int(high_score_ratio * 80)
    score += int(eligible_ratio * 25)
    score += min(max(record.top_score - 40, 0) // 3, 25)
    score += min(record.success_count * 2, 12)
    score -= min(record.error_count * 10, 40)
    if record.jobs_seen_total >= 50 and record.high_score_jobs_total == 0:
        score -= 20
    elif record.jobs_seen_total >= 100 and high_score_ratio <= 0.04:
        score -= 10
    if record.jobs_seen_total >= 50 and record.top_score < 60:
        score -= 12
    return score


def should_mark_source_low_value(record: SourceCatalogRecord) -> bool:
    if record.jobs_seen_total < 50 or record.success_count < 3:
        return False
    return source_quality_score(record) < 10


def _sort_source_records(records: list[SourceCatalogRecord]) -> list[SourceCatalogRecord]:
    status_priority = {"manual": 0, "active": 1, "candidate": 2, "low_value": 3, "dead": 9}
    return sorted(
        records,
        key=lambda item: (
            status_priority.get(item.status, 5),
            -source_quality_score(item),
            item.last_checked_at or "",
            item.error_count,
            -item.success_count,
            item.first_seen_at,
        ),
    )


def _job_matches_filters(
    record: JobRecord,
    *,
    status: str | None = None,
    source_type: str | None = None,
    company: str | None = None,
    min_score: int | None = None,
    eligible_only: bool = True,
    only_new: bool = False,
) -> bool:
    if status and record.status != status:
        return False
    if source_type and record.source_type != source_type:
        return False
    if company and record.company.lower() != company.lower():
        return False
    if min_score is not None and record.score < min_score:
        return False
    if eligible_only and not record.is_eligible:
        return False
    if only_new and not record.is_new:
        return False
    return True


def _sort_jobs(records: list[JobRecord], *, sort_by: str = "ranked") -> list[JobRecord]:
    if sort_by == "newest":
        return sorted(
            records,
            key=lambda item: (item.first_seen_at, item.last_seen_at, item.score, item.is_eligible),
            reverse=True,
        )
    return sorted(records, key=lambda item: (item.is_eligible, item.score, item.first_seen_at), reverse=True)


class JobRepository(ABC):
    @abstractmethod
    def get_job(self, job_id: str) -> JobRecord | None:
        raise NotImplementedError

    @abstractmethod
    def upsert_job(self, job: JobRecord) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_jobs(
        self,
        *,
        status: str | None = None,
        source_type: str | None = None,
        company: str | None = None,
        min_score: int | None = None,
        eligible_only: bool = True,
        only_new: bool = False,
        sort_by: str = "ranked",
        limit: int = 100,
    ) -> list[JobRecord]:
        raise NotImplementedError

    @abstractmethod
    def list_feedback_jobs(self, *, limit: int = 1000) -> list[JobRecord]:
        raise NotImplementedError

    @abstractmethod
    def update_job(self, job_id: str, *, status: str | None = None, notes: str | None = None) -> JobRecord | None:
        raise NotImplementedError

    @abstractmethod
    def record_crawl_run(self, run: CrawlRunRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def latest_crawl_run(self) -> CrawlRunRecord | None:
        raise NotImplementedError

    @abstractmethod
    def acquire_collector_lock(self, *, lock_id: str, expires_at_epoch: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def release_collector_lock(self, *, lock_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_source(self, source: SourceConfig) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_sources(
        self,
        *,
        enabled_only: bool = True,
        statuses: tuple[str, ...] = ("candidate", "active"),
        limit: int = 100,
    ) -> list[SourceConfig]:
        raise NotImplementedError

    @abstractmethod
    def list_source_records(
        self,
        *,
        enabled_only: bool = True,
        statuses: tuple[str, ...] = ("candidate", "active"),
        limit: int = 100,
    ) -> list[SourceCatalogRecord]:
        raise NotImplementedError

    @abstractmethod
    def record_source_result(
        self,
        source: SourceConfig,
        *,
        was_success: bool,
        error_message: str | None = None,
        jobs_found: int = 0,
        eligible_jobs: int = 0,
        high_score_jobs: int = 0,
        total_score: int = 0,
        top_score: int = 0,
        disable_after_errors: int = 2,
    ) -> None:
        raise NotImplementedError


class RawPayloadStore(ABC):
    @abstractmethod
    def store(self, run_id: str, source_name: str, payload: str) -> str | None:
        raise NotImplementedError


class LocalJsonRepository(JobRepository):
    def __init__(self, jobs_path: str = "data/jobs.json", runs_path: str = "data/crawl_runs.json", sources_path: str | None = None) -> None:
        self.jobs_path = Path(jobs_path)
        self.runs_path = Path(runs_path)
        self.sources_path = Path(sources_path) if sources_path else self.jobs_path.with_name("sources.json")
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        self.sources_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: list[dict[str, Any]]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def get_job(self, job_id: str) -> JobRecord | None:
        for item in self._read_json(self.jobs_path):
            if item["job_id"] == job_id:
                return JobRecord.from_item(item)
        return None

    def upsert_job(self, job: JobRecord) -> str:
        items = self._read_json(self.jobs_path)
        for index, item in enumerate(items):
            if item["job_id"] == job.job_id:
                existing = JobRecord.from_item(item)
                job.first_seen_at = existing.first_seen_at
                job.status = existing.status
                job.notes = existing.notes
                items[index] = job.to_item()
                self._write_json(self.jobs_path, items)
                return "updated"
        items.append(job.to_item())
        self._write_json(self.jobs_path, items)
        return "inserted"

    def list_jobs(
        self,
        *,
        status: str | None = None,
        source_type: str | None = None,
        company: str | None = None,
        min_score: int | None = None,
        eligible_only: bool = True,
        only_new: bool = False,
        sort_by: str = "ranked",
        limit: int = 100,
    ) -> list[JobRecord]:
        records = [JobRecord.from_item(item) for item in self._read_json(self.jobs_path)]
        filtered = [
            record
            for record in records
            if _job_matches_filters(
                record,
                status=status,
                source_type=source_type,
                company=company,
                min_score=min_score,
                eligible_only=eligible_only,
                only_new=only_new,
            )
        ]
        return _sort_jobs(filtered, sort_by=sort_by)[:limit]

    def list_feedback_jobs(self, *, limit: int = 1000) -> list[JobRecord]:
        feedback_statuses = {"saved", "applied", "dismissed"}
        records = [
            record
            for record in (JobRecord.from_item(item) for item in self._read_json(self.jobs_path))
            if record.status in feedback_statuses
        ]
        return _sort_jobs(records, sort_by="newest")[:limit]

    def update_job(self, job_id: str, *, status: str | None = None, notes: str | None = None) -> JobRecord | None:
        items = self._read_json(self.jobs_path)
        for index, item in enumerate(items):
            if item["job_id"] != job_id:
                continue
            record = JobRecord.from_item(item)
            if status is not None:
                record.status = status
            if notes is not None:
                record.notes = notes
            items[index] = record.to_item()
            self._write_json(self.jobs_path, items)
            return record
        return None

    def record_crawl_run(self, run: CrawlRunRecord) -> None:
        runs = self._read_json(self.runs_path)
        runs.append(run.to_item())
        self._write_json(self.runs_path, runs)

    def latest_crawl_run(self) -> CrawlRunRecord | None:
        records = [
            CrawlRunRecord.from_item(item)
            for item in self._read_json(self.runs_path)
            if item.get("finished_at") and item.get("run_status", "completed") not in {"failed", "running", "skipped"}
        ]
        if not records:
            return None
        return sorted(records, key=lambda item: item.finished_at or item.started_at, reverse=True)[0]

    def _lock_path(self) -> Path:
        return self.runs_path.with_name("collector_lock.json")

    def acquire_collector_lock(self, *, lock_id: str, expires_at_epoch: int) -> bool:
        now_epoch = int(time.time())
        path = self._lock_path()
        if path.exists():
            item = json.loads(path.read_text(encoding="utf-8"))
            if int(item.get("expires_at_epoch", 0)) > now_epoch:
                return False
        path.write_text(json.dumps({"lock_id": lock_id, "expires_at_epoch": expires_at_epoch}), encoding="utf-8")
        return True

    def release_collector_lock(self, *, lock_id: str) -> None:
        path = self._lock_path()
        if not path.exists():
            return
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("lock_id") == lock_id:
            path.unlink()

    def upsert_source(self, source: SourceConfig) -> str:
        items = self._read_json(self.sources_path)
        source_id = stable_source_id(source.source_type, source.identifier)
        now = utc_now_iso()
        for index, item in enumerate(items):
            if item["source_id"] != source_id:
                continue
            existing = SourceCatalogRecord.from_item(item)
            existing.company = source.company or existing.company
            existing.careers_url = source.careers_url or existing.careers_url
            existing.enabled = source.enabled
            existing.last_seen_at = now
            existing.metadata = {**existing.metadata, **dict(source.metadata)}
            if existing.status == "dead":
                existing.status = "candidate"
                existing.error_count = 0
                existing.last_error_at = None
            items[index] = existing.to_item()
            self._write_json(self.sources_path, items)
            return "updated"
        items.append(SourceCatalogRecord.from_source(source, now=now).to_item())
        self._write_json(self.sources_path, items)
        return "inserted"

    def list_sources(
        self,
        *,
        enabled_only: bool = True,
        statuses: tuple[str, ...] = ("candidate", "active"),
        limit: int = 100,
    ) -> list[SourceConfig]:
        return [record.to_source_config() for record in self.list_source_records(enabled_only=enabled_only, statuses=statuses, limit=limit)]

    def list_source_records(
        self,
        *,
        enabled_only: bool = True,
        statuses: tuple[str, ...] = ("candidate", "active"),
        limit: int = 100,
    ) -> list[SourceCatalogRecord]:
        records = [SourceCatalogRecord.from_item(item) for item in self._read_json(self.sources_path)]
        filtered: list[SourceCatalogRecord] = []
        for record in records:
            if enabled_only and not record.enabled:
                continue
            if statuses and record.status not in statuses:
                continue
            filtered.append(record)
        return _sort_source_records(filtered)[:limit]

    def record_source_result(
        self,
        source: SourceConfig,
        *,
        was_success: bool,
        error_message: str | None = None,
        jobs_found: int = 0,
        eligible_jobs: int = 0,
        high_score_jobs: int = 0,
        total_score: int = 0,
        top_score: int = 0,
        disable_after_errors: int = 2,
    ) -> None:
        items = self._read_json(self.sources_path)
        source_id = stable_source_id(source.source_type, source.identifier)
        now = utc_now_iso()
        updated = False
        for index, item in enumerate(items):
            if item["source_id"] != source_id:
                continue
            record = SourceCatalogRecord.from_item(item)
            record.last_seen_at = now
            record.last_checked_at = now
            if was_success:
                record.status = "active"
                record.last_success_at = now
                record.error_count = 0
                record.success_count += 1
                record.jobs_seen_total += jobs_found
                record.eligible_jobs_total += eligible_jobs
                record.high_score_jobs_total += high_score_jobs
                record.total_score += total_score
                record.top_score = max(record.top_score, top_score)
                record.last_jobs_found = jobs_found
                record.last_eligible_jobs = eligible_jobs
                record.last_high_score_jobs = high_score_jobs
                if should_mark_source_low_value(record):
                    record.status = "low_value"
                record.metadata.pop("last_error_message", None)
            else:
                record.last_error_at = now
                record.error_count += 1
                if error_message:
                    record.metadata["last_error_message"] = error_message[:500]
                record.last_jobs_found = 0
                record.last_eligible_jobs = 0
                record.last_high_score_jobs = 0
                lowered = (error_message or "").lower()
                if "404" in lowered or "not found" in lowered or record.error_count >= disable_after_errors:
                    record.status = "dead"
            items[index] = record.to_item()
            updated = True
            break
        if not updated:
            self.upsert_source(source)
            self.record_source_result(
                source,
                was_success=was_success,
                error_message=error_message,
                jobs_found=jobs_found,
                eligible_jobs=eligible_jobs,
                high_score_jobs=high_score_jobs,
                total_score=total_score,
                top_score=top_score,
                disable_after_errors=disable_after_errors,
            )
            return
        self._write_json(self.sources_path, items)


class LocalPayloadStore(RawPayloadStore):
    def __init__(self, base_path: str = "data/raw_payloads") -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def store(self, run_id: str, source_name: str, payload: str) -> str | None:
        safe_name = source_name.lower().replace(" ", "-")
        path = self.base_path / run_id / f"{safe_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return str(path)


class DynamoJobRepository(JobRepository):
    def __init__(self, jobs_table_name: str, crawl_runs_table_name: str, sources_table_name: str) -> None:
        import boto3

        dynamodb = boto3.resource("dynamodb")
        self.jobs_table = dynamodb.Table(jobs_table_name)
        self.crawl_runs_table = dynamodb.Table(crawl_runs_table_name)
        self.sources_table = dynamodb.Table(sources_table_name)

    def _scan_table(self, table: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {}
        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        return items

    def _list_jobs_projected(
        self,
        *,
        status: str | None = None,
        source_type: str | None = None,
        company: str | None = None,
        min_score: int | None = None,
        eligible_only: bool = True,
        only_new: bool = False,
        sort_by: str = "ranked",
        limit: int = 100,
    ) -> list[JobRecord]:
        from boto3.dynamodb.conditions import Attr

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": (
                "#job_id, source_job_id, canonical_url, apply_url, source_type, source_name, "
                "company, title, location_text, remote_type, posted_date, first_seen_at, "
                "last_seen_at, score, score_reasons, is_eligible, is_remote, is_us_based, "
                "us_location_confidence, #status, notes, hash_fingerprint, description_excerpt"
            ),
            "ExpressionAttributeNames": {
                "#job_id": "job_id",
                "#status": "status",
            },
        }
        filter_expression = None
        if status:
            filter_expression = Attr("status").eq(status)
        if source_type:
            clause = Attr("source_type").eq(source_type)
            filter_expression = clause if filter_expression is None else filter_expression & clause
        if min_score is not None:
            clause = Attr("score").gte(min_score)
            filter_expression = clause if filter_expression is None else filter_expression & clause
        if eligible_only:
            clause = Attr("is_eligible").eq(True)
            filter_expression = clause if filter_expression is None else filter_expression & clause
        if only_new:
            clause = Attr("status").eq("new")
            filter_expression = clause if filter_expression is None else filter_expression & clause
        if filter_expression is not None:
            scan_kwargs["FilterExpression"] = filter_expression

        matches: list[tuple[tuple[Any, ...], str, JobRecord]] = []
        while True:
            response = self.jobs_table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                record = JobRecord(
                    job_id=item["job_id"],
                    source_job_id=item.get("source_job_id"),
                    canonical_url=str(item.get("canonical_url") or ""),
                    apply_url=str(item.get("apply_url") or ""),
                    source_type=str(item.get("source_type") or ""),
                    source_name=str(item.get("source_name") or ""),
                    company=str(item.get("company") or ""),
                    title=str(item.get("title") or ""),
                    location_text=str(item.get("location_text") or ""),
                    remote_type=str(item.get("remote_type") or "unknown"),
                    posted_date=item.get("posted_date"),
                    first_seen_at=str(item.get("first_seen_at") or ""),
                    last_seen_at=str(item.get("last_seen_at") or ""),
                    description_text=str(item.get("description_excerpt") or ""),
                    score=int(item.get("score") or 0),
                    score_reasons=list(item.get("score_reasons") or []),
                    is_eligible=bool(item.get("is_eligible")),
                    is_remote=bool(item.get("is_remote")),
                    is_us_based=bool(item.get("is_us_based")),
                    us_location_confidence=str(item.get("us_location_confidence") or "unknown"),
                    status=str(item.get("status") or "new"),
                    notes=str(item.get("notes") or ""),
                    hash_fingerprint=str(item.get("hash_fingerprint") or ""),
                )
                if not _job_matches_filters(
                    record,
                    status=status,
                    source_type=source_type,
                    company=company,
                    min_score=min_score,
                    eligible_only=eligible_only,
                    only_new=only_new,
                ):
                    continue
                if sort_by == "newest":
                    sort_key = (record.first_seen_at, record.score, record.is_eligible)
                else:
                    sort_key = (record.is_eligible, record.score, record.first_seen_at)
                matches.append((sort_key, record.job_id, record))
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        return [record for _sort_key, _job_id, record in sorted(matches, reverse=True)[:limit]]

    def list_feedback_jobs(self, *, limit: int = 1000) -> list[JobRecord]:
        from boto3.dynamodb.conditions import Attr

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": "#status, title, company, first_seen_at, last_seen_at",
            "ExpressionAttributeNames": {
                "#status": "status",
            },
            "FilterExpression": Attr("status").eq("saved") | Attr("status").eq("applied") | Attr("status").eq("dismissed"),
        }
        records: list[JobRecord] = []
        while True:
            response = self.jobs_table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                records.append(
                    JobRecord(
                        job_id="feedback-only",
                        source_job_id=None,
                        canonical_url="",
                        apply_url="",
                        source_type="",
                        source_name="",
                        company=str(item.get("company") or ""),
                        title=str(item.get("title") or ""),
                        location_text="",
                        remote_type="unknown",
                        posted_date=None,
                        first_seen_at=str(item.get("first_seen_at") or ""),
                        last_seen_at=str(item.get("last_seen_at") or ""),
                        description_text="",
                        score=0,
                        score_reasons=[],
                        is_eligible=False,
                        is_remote=False,
                        is_us_based=False,
                        status=str(item.get("status") or "new"),
                    )
                )
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        return _sort_jobs(records, sort_by="newest")[:limit]

    def get_job(self, job_id: str) -> JobRecord | None:
        response = self.jobs_table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        return JobRecord.from_item(item) if item else None

    def upsert_job(self, job: JobRecord) -> str:
        existing = self.get_job(job.job_id)
        if existing:
            job.first_seen_at = existing.first_seen_at
            job.status = existing.status
            job.notes = existing.notes
            operation = "updated"
        else:
            operation = "inserted"
        self.jobs_table.put_item(Item=job.to_item())
        return operation

    def list_jobs(
        self,
        *,
        status: str | None = None,
        source_type: str | None = None,
        company: str | None = None,
        min_score: int | None = None,
        eligible_only: bool = True,
        only_new: bool = False,
        sort_by: str = "ranked",
        limit: int = 100,
    ) -> list[JobRecord]:
        if limit <= 250:
            return self._list_jobs_projected(
                status=status,
                source_type=source_type,
                company=company,
                min_score=min_score,
                eligible_only=eligible_only,
                only_new=only_new,
                sort_by=sort_by,
                limit=limit,
            )
        records = [JobRecord.from_item(item) for item in self._scan_table(self.jobs_table)]
        filtered = [
            record
            for record in records
            if _job_matches_filters(
                record,
                status=status,
                source_type=source_type,
                company=company,
                min_score=min_score,
                eligible_only=eligible_only,
                only_new=only_new,
            )
        ]
        return _sort_jobs(filtered, sort_by=sort_by)[:limit]

    def update_job(self, job_id: str, *, status: str | None = None, notes: str | None = None) -> JobRecord | None:
        record = self.get_job(job_id)
        if record is None:
            return None
        if status is not None:
            record.status = status
        if notes is not None:
            record.notes = notes
        self.jobs_table.put_item(Item=record.to_item())
        return record

    def record_crawl_run(self, run: CrawlRunRecord) -> None:
        self.crawl_runs_table.put_item(Item=run.to_item())

    def latest_crawl_run(self) -> CrawlRunRecord | None:
        response = self.crawl_runs_table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = self.crawl_runs_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
        records = [
            CrawlRunRecord.from_item(item)
            for item in items
            if item.get("finished_at") and item.get("run_status", "completed") not in {"failed", "running", "skipped"}
        ]
        if not records:
            return None
        return sorted(records, key=lambda item: item.finished_at or item.started_at, reverse=True)[0]

    def acquire_collector_lock(self, *, lock_id: str, expires_at_epoch: int) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.crawl_runs_table.put_item(
                Item={
                    "run_id": "__collector_lock__",
                    "lock_id": lock_id,
                    "expires_at_epoch": expires_at_epoch,
                },
                ConditionExpression="attribute_not_exists(run_id) OR expires_at_epoch < :now",
                ExpressionAttributeValues={":now": int(time.time())},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def release_collector_lock(self, *, lock_id: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.crawl_runs_table.update_item(
                Key={"run_id": "__collector_lock__"},
                UpdateExpression="SET expires_at_epoch = :expired",
                ConditionExpression="lock_id = :lock_id",
                ExpressionAttributeValues={
                    ":expired": 0,
                    ":lock_id": lock_id,
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return
            raise

    def upsert_source(self, source: SourceConfig) -> str:
        source_id = stable_source_id(source.source_type, source.identifier)
        response = self.sources_table.get_item(Key={"source_id": source_id})
        existing_item = response.get("Item")
        now = utc_now_iso()
        if existing_item:
            record = SourceCatalogRecord.from_item(existing_item)
            record.company = source.company or record.company
            record.careers_url = source.careers_url or record.careers_url
            record.enabled = source.enabled
            record.last_seen_at = now
            record.metadata = {**record.metadata, **dict(source.metadata)}
            if record.status == "dead":
                record.status = "candidate"
                record.error_count = 0
                record.last_error_at = None
            operation = "updated"
        else:
            record = SourceCatalogRecord.from_source(source, now=now)
            operation = "inserted"
        self.sources_table.put_item(Item=record.to_item())
        return operation

    def list_sources(
        self,
        *,
        enabled_only: bool = True,
        statuses: tuple[str, ...] = ("candidate", "active"),
        limit: int = 100,
    ) -> list[SourceConfig]:
        return [record.to_source_config() for record in self.list_source_records(enabled_only=enabled_only, statuses=statuses, limit=limit)]

    def list_source_records(
        self,
        *,
        enabled_only: bool = True,
        statuses: tuple[str, ...] = ("candidate", "active"),
        limit: int = 100,
    ) -> list[SourceCatalogRecord]:
        records = [SourceCatalogRecord.from_item(item) for item in self._scan_table(self.sources_table)]
        filtered: list[SourceCatalogRecord] = []
        for record in records:
            if enabled_only and not record.enabled:
                continue
            if statuses and record.status not in statuses:
                continue
            filtered.append(record)
        return _sort_source_records(filtered)[:limit]

    def record_source_result(
        self,
        source: SourceConfig,
        *,
        was_success: bool,
        error_message: str | None = None,
        jobs_found: int = 0,
        eligible_jobs: int = 0,
        high_score_jobs: int = 0,
        total_score: int = 0,
        top_score: int = 0,
        disable_after_errors: int = 2,
    ) -> None:
        source_id = stable_source_id(source.source_type, source.identifier)
        response = self.sources_table.get_item(Key={"source_id": source_id})
        record = SourceCatalogRecord.from_item(response["Item"]) if "Item" in response else SourceCatalogRecord.from_source(source)
        now = utc_now_iso()
        record.last_seen_at = now
        record.last_checked_at = now
        if was_success:
            record.status = "active"
            record.last_success_at = now
            record.error_count = 0
            record.success_count += 1
            record.jobs_seen_total += jobs_found
            record.eligible_jobs_total += eligible_jobs
            record.high_score_jobs_total += high_score_jobs
            record.total_score += total_score
            record.top_score = max(record.top_score, top_score)
            record.last_jobs_found = jobs_found
            record.last_eligible_jobs = eligible_jobs
            record.last_high_score_jobs = high_score_jobs
            if should_mark_source_low_value(record):
                record.status = "low_value"
            record.metadata.pop("last_error_message", None)
        else:
            record.last_error_at = now
            record.error_count += 1
            if error_message:
                record.metadata["last_error_message"] = error_message[:500]
            record.last_jobs_found = 0
            record.last_eligible_jobs = 0
            record.last_high_score_jobs = 0
            lowered = (error_message or "").lower()
            if "404" in lowered or "not found" in lowered or record.error_count >= disable_after_errors:
                record.status = "dead"
        self.sources_table.put_item(Item=record.to_item())


class S3PayloadStore(RawPayloadStore):
    def __init__(self, bucket_name: str) -> None:
        import boto3

        self.bucket_name = bucket_name
        self.client = boto3.client("s3")

    def store(self, run_id: str, source_name: str, payload: str) -> str | None:
        safe_name = source_name.lower().replace(" ", "-")
        key = f"raw/{run_id}/{safe_name}.json"
        self.client.put_object(Bucket=self.bucket_name, Key=key, Body=payload.encode("utf-8"), ContentType="application/json")
        return key
