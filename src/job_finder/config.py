from __future__ import annotations

import json
import os
from typing import Any

from job_finder.models import DiscoverySettings, SourceConfig


def load_sources_from_event(event: dict[str, Any]) -> list[SourceConfig]:
    raw_sources = event.get("sources")
    if raw_sources is None:
        raw_sources = json.loads(os.environ.get("COLLECTOR_SOURCES_JSON", "[]"))
    return [SourceConfig.from_dict(item) for item in raw_sources]


def load_discovery_settings(event: dict[str, Any]) -> DiscoverySettings:
    raw_settings = event.get("discovery")
    if raw_settings is not None:
        return DiscoverySettings.from_dict(raw_settings)
    if os.environ.get("COLLECTOR_DISCOVERY_JSON"):
        return DiscoverySettings.from_dict(json.loads(os.environ["COLLECTOR_DISCOVERY_JSON"]))
    return DiscoverySettings()


def load_allowed_origin() -> str:
    return os.environ.get("ALLOWED_ORIGIN", "*")


def load_storage_settings() -> dict[str, str | None]:
    return {
        "jobs_table_name": os.environ.get("JOBS_TABLE_NAME"),
        "crawl_runs_table_name": os.environ.get("CRAWL_RUNS_TABLE_NAME"),
        "sources_table_name": os.environ.get("SOURCES_TABLE_NAME"),
        "raw_payload_bucket_name": os.environ.get("RAW_PAYLOAD_BUCKET_NAME"),
    }
