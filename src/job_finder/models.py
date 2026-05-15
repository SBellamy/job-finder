from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def text_excerpt(value: str, *, length: int = 360) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= length:
        return normalized
    return f"{normalized[: length - 3]}..."


def stable_job_id(source_type: str, source_job_id: str | None, canonical_url: str) -> str:
    base = "::".join(
        [
            source_type.strip().lower(),
            (source_job_id or "").strip().lower(),
            canonical_url.strip().lower(),
        ]
    )
    return sha256(base.encode("utf-8")).hexdigest()[:24]


def stable_source_id(source_type: str, identifier: str) -> str:
    return "::".join([source_type.strip().lower(), identifier.strip().lower()])


@dataclass(slots=True)
class SourceConfig:
    company: str
    source_type: str
    identifier: str
    company_size: str = "unknown"
    company_tags: list[str] = field(default_factory=list)
    careers_url: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        identifier = (
            data.get("identifier")
            or data.get("board")
            or data.get("board_token")
            or data.get("job_board_name")
            or data.get("site")
            or data.get("account")
            or data.get("subdomain")
        )
        if not identifier:
            raise ValueError("Source config is missing an ATS identifier")
        company = str(data.get("company") or identifier).strip()
        if not data.get("source_type"):
            raise ValueError(f"Source config for {company} is missing source_type")
        return cls(
            company=company,
            source_type=str(data["source_type"]).strip().lower(),
            identifier=str(identifier).strip(),
            company_size=str(data.get("company_size", "unknown")).strip().lower(),
            company_tags=[str(tag).strip().lower() for tag in data.get("company_tags", [])],
            careers_url=data.get("careers_url"),
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DiscoverySettings:
    enabled: bool = True
    strategy: str = "bing_rss"
    strategies: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    pages_per_query: int = 1
    max_sources_per_ats: int = 40
    max_sources_per_run: int = 60
    source_error_threshold: int = 2
    max_low_value_rechecks_per_run: int = 3
    low_value_retry_hours: int = 48
    source_types: list[str] = field(default_factory=lambda: ["ashby", "greenhouse", "lever", "workable"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoverySettings":
        return cls(
            enabled=bool(data.get("enabled", True)),
            strategy=str(data.get("strategy", "bing_rss")).strip().lower(),
            strategies=[str(item).strip().lower() for item in data.get("strategies", []) if str(item).strip()],
            keywords=[str(item).strip() for item in data.get("keywords", []) if str(item).strip()],
            pages_per_query=max(1, int(data.get("pages_per_query", 1))),
            max_sources_per_ats=max(1, int(data.get("max_sources_per_ats", 40))),
            max_sources_per_run=max(1, int(data.get("max_sources_per_run", 60))),
            source_error_threshold=max(1, int(data.get("source_error_threshold", 2))),
            max_low_value_rechecks_per_run=max(0, int(data.get("max_low_value_rechecks_per_run", 3))),
            low_value_retry_hours=max(1, int(data.get("low_value_retry_hours", 48))),
            source_types=[str(item).strip().lower() for item in data.get("source_types", ["ashby", "greenhouse", "lever", "workable"])],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CrawlRunRecord:
    run_id: str
    started_at: str
    finished_at: str | None = None
    source: str = "scheduled"
    pages_checked: int = 0
    jobs_found: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    errors_count: int = 0
    errors: list[str] = field(default_factory=list)
    run_status: str = "completed"

    def to_item(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "CrawlRunRecord":
        payload = dict(item)
        for key in ("pages_checked", "jobs_found", "jobs_inserted", "jobs_updated", "errors_count"):
            payload[key] = int(payload.get(key, 0) or 0)
        payload["errors"] = list(payload.get("errors") or [])
        payload["run_status"] = str(payload.get("run_status") or "completed")
        return cls(**payload)


@dataclass(slots=True)
class SourceCatalogRecord:
    source_id: str
    source_type: str
    identifier: str
    company: str
    careers_url: str | None
    enabled: bool
    status: str
    first_seen_at: str
    last_seen_at: str
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    error_count: int = 0
    success_count: int = 0
    jobs_seen_total: int = 0
    eligible_jobs_total: int = 0
    high_score_jobs_total: int = 0
    total_score: int = 0
    top_score: int = 0
    last_jobs_found: int = 0
    last_eligible_jobs: int = 0
    last_high_score_jobs: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_source(cls, source: SourceConfig, *, now: str | None = None, status: str = "candidate") -> "SourceCatalogRecord":
        timestamp = now or utc_now_iso()
        return cls(
            source_id=stable_source_id(source.source_type, source.identifier),
            source_type=source.source_type,
            identifier=source.identifier,
            company=source.company,
            careers_url=source.careers_url,
            enabled=source.enabled,
            status=status,
            first_seen_at=timestamp,
            last_seen_at=timestamp,
            metadata=dict(source.metadata),
        )

    def to_source_config(self) -> SourceConfig:
        return SourceConfig(
            company=self.company,
            source_type=self.source_type,
            identifier=self.identifier,
            careers_url=self.careers_url,
            enabled=self.enabled,
            metadata=dict(self.metadata),
        )

    def to_item(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "SourceCatalogRecord":
        return cls(**dict(item))


@dataclass(slots=True)
class JobRecord:
    job_id: str
    source_job_id: str | None
    canonical_url: str
    apply_url: str
    source_type: str
    source_name: str
    company: str
    title: str
    location_text: str
    remote_type: str
    posted_date: str | None
    first_seen_at: str
    last_seen_at: str
    description_text: str
    score: int
    score_reasons: list[str]
    is_eligible: bool
    is_remote: bool
    is_us_based: bool
    us_location_confidence: str = "unknown"
    status: str = "new"
    notes: str = ""
    hash_fingerprint: str = ""
    raw_payload_s3_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_new(self) -> bool:
        return self.status == "new"

    def to_item(self) -> dict[str, Any]:
        item = asdict(self)
        item["is_new"] = self.is_new
        item["description_excerpt"] = text_excerpt(self.description_text)
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "JobRecord":
        payload = dict(item)
        payload.pop("is_new", None)
        payload.pop("description_excerpt", None)
        return cls(**payload)
