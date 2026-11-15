"""Validate board membership before interpreting absent jobs as missing."""
from typing import Any


def board_jobs(payload: Any, key: str = "jobs") -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ValueError("ATS response does not contain a valid job list")
    jobs = payload[key]
    if any(not isinstance(job, dict) for job in jobs):
        raise ValueError("ATS response contains a malformed job")
    return jobs


def complete_membership(jobs: list[dict[str, Any]], *id_fields: str) -> bool:
    identifiers = [next((str(job[field]) for field in id_fields if job.get(field) is not None and str(job[field])), None) for job in jobs]
    return all(identifiers) and len(set(identifiers)) == len(identifiers)
