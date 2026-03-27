from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from job_finder.api import get_digest, list_jobs, list_sources, update_job
from job_finder.collector import collect_sources
from job_finder.config import load_allowed_origin, load_discovery_settings, load_sources_from_event, load_storage_settings
from job_finder.storage import DynamoJobRepository, S3PayloadStore


def _repository_and_payload_store() -> tuple[DynamoJobRepository, S3PayloadStore]:
    settings = load_storage_settings()
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise RuntimeError(f"Missing storage settings: {', '.join(missing)}")
    repository = DynamoJobRepository(
        jobs_table_name=settings["jobs_table_name"] or "",
        crawl_runs_table_name=settings["crawl_runs_table_name"] or "",
        sources_table_name=settings["sources_table_name"] or "",
    )
    payload_store = S3PayloadStore(bucket_name=settings["raw_payload_bucket_name"] or "")
    return repository, payload_store


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    origin = load_allowed_origin()
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "GET,PATCH,OPTIONS",
        },
        "body": json.dumps(payload, default=_json_default),
    }


def collector_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    repository, payload_store = _repository_and_payload_store()
    sources = load_sources_from_event(event)
    discovery_settings = load_discovery_settings(event)
    run = collect_sources(sources, repository, payload_store, discovery_settings=discovery_settings)
    return _response(200, {"run": run.to_item()})


def api_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET")
    path = event.get("rawPath", "/")
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    repository, _payload_store = _repository_and_payload_store()
    try:
        if method == "GET" and path == "/health":
            return _response(200, {"ok": True})
        if method == "GET" and path == "/jobs":
            query = event.get("queryStringParameters") or {}
            return _response(200, list_jobs(repository, query))
        if method == "GET" and path == "/digest":
            query = event.get("queryStringParameters") or {}
            return _response(200, get_digest(repository, query))
        if method == "GET" and path == "/sources":
            query = event.get("queryStringParameters") or {}
            return _response(200, list_sources(repository, query))
        if method == "PATCH" and path.startswith("/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            payload = json.loads(event.get("body") or "{}")
            return _response(200, {"item": update_job(repository, job_id, payload)})
    except KeyError as exc:
        return _response(404, {"error": f"Unknown job: {exc}"})
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    return _response(404, {"error": f"No route for {method} {path}"})
