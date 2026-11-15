"""Shared job ordering and compact DynamoDB dashboard index keys."""
from __future__ import annotations

import struct
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_finder.models import JobRecord

FEED_INDEXES = {
    "ranked": ("jobs-ranked-v1", "feed_ranked"),
    "newest": ("jobs-newest-v1", "feed_newest"),
    "newest_posted": ("jobs-posted-v1", "feed_posted"),
}
FEED_KEY_ATTRIBUTES = tuple(key for _, key in FEED_INDEXES.values())
FEED_INTERNAL_ATTRIBUTES = (*FEED_KEY_ATTRIBUTES, "feed_summary")
REVIEW_STATUSES = ("new", "saved", "dismissed", "applied")
SUMMARY_ATTRIBUTES = (
    "source_job_id", "canonical_url", "apply_url", "source_type", "source_name",
    "company", "title", "location_text", "remote_type", "posted_date",
    "first_seen_at", "last_seen_at", "description_excerpt", "score", "score_reasons",
    "is_eligible", "is_remote", "is_us_based", "us_location_confidence", "notes",
    "hash_fingerprint", "metadata", "availability",
)


def sort_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if text.isdigit():
        numeric = float(text)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()
    except ValueError:
        return 0.0


def job_sort_key(record: JobRecord, *, sort_by: str) -> tuple[Any, ...]:
    first_seen = sort_timestamp(record.first_seen_at)
    last_seen = sort_timestamp(record.last_seen_at)
    posted = sort_timestamp(record.posted_date)
    if sort_by == "newest":
        values = (first_seen, last_seen, posted, record.score, record.is_eligible)
    elif sort_by == "newest_posted":
        values = (posted or first_seen, first_seen, last_seen, record.score, record.is_eligible)
    else:
        values = (record.is_eligible, record.score, first_seen, posted)
    return (*values, record.job_id)


def sortable_number(value: float) -> str:
    """Encode an IEEE float so string comparison preserves numeric order."""
    bits = int.from_bytes(struct.pack(">d", float(value)), "big")
    bits = (~bits & ((1 << 64) - 1)) if bits >> 63 else bits ^ (1 << 63)
    return f"{bits:016x}"


def index_fields(record: JobRecord) -> dict[str, Any]:
    keys = {
        attribute: "|".join([*(sortable_number(value) for value in job_sort_key(record, sort_by=sort)[:-1]), record.job_id])
        for sort, (_, attribute) in FEED_INDEXES.items()
    }

    item = record.to_item()
    keys["feed_summary"] = {key: item[key] for key in ("job_id", "status", *SUMMARY_ATTRIBUTES) if key in item}
    return keys
