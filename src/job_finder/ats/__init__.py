from __future__ import annotations

from typing import Any

from job_finder.ats.ashby import fetch_ashby_jobs
from job_finder.ats.greenhouse import fetch_greenhouse_jobs
from job_finder.ats.lever import fetch_lever_jobs
from job_finder.ats.smartrecruiters import fetch_smartrecruiters_jobs
from job_finder.ats.workable import fetch_workable_jobs
from job_finder.models import SourceConfig


FETCHERS = {
    "ashby": fetch_ashby_jobs,
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
    "smartrecruiters": fetch_smartrecruiters_jobs,
    "workable": fetch_workable_jobs,
}


def fetch_jobs(source: SourceConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        fetcher = FETCHERS[source.source_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported source_type: {source.source_type}") from exc
    return fetcher(source)
