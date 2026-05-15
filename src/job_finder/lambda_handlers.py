from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any

from job_finder.api import get_digest, get_freshness, get_job, list_jobs, list_sources, update_job
from job_finder.collector import collect_sources
from job_finder.config import load_allowed_origin, load_discovery_settings, load_sources_from_event, load_storage_settings
from job_finder.models import CrawlRunRecord
from job_finder.storage import DynamoJobRepository, S3PayloadStore


logger = logging.getLogger(__name__)


def _repository() -> DynamoJobRepository:
    settings = load_storage_settings()
    required_settings = ("jobs_table_name", "crawl_runs_table_name", "sources_table_name")
    missing = [key for key in required_settings if not settings[key]]
    if missing:
        raise RuntimeError(f"Missing storage settings: {', '.join(missing)}")
    return DynamoJobRepository(
        jobs_table_name=settings["jobs_table_name"] or "",
        crawl_runs_table_name=settings["crawl_runs_table_name"] or "",
        sources_table_name=settings["sources_table_name"] or "",
    )


def _payload_store() -> S3PayloadStore:
    settings = load_storage_settings()
    if not settings["raw_payload_bucket_name"]:
        raise RuntimeError("Missing storage settings: raw_payload_bucket_name")
    return S3PayloadStore(bucket_name=settings["raw_payload_bucket_name"])


def _repository_and_payload_store() -> tuple[DynamoJobRepository, S3PayloadStore]:
    return _repository(), _payload_store()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _emit_crawl_metrics(run: CrawlRunRecord) -> None:
    try:
        import boto3

        metric_data: list[dict[str, Any]] = [
            {
                "MetricName": "CrawlErrors",
                "Value": run.errors_count,
                "Unit": "Count",
            },
            {
                "MetricName": "JobsInserted",
                "Value": run.jobs_inserted,
                "Unit": "Count",
            },
        ]
        if run.run_status in {"completed", "completed_with_errors"}:
            metric_data.append(
                {
                    "MetricName": "CompletedCrawls",
                    "Value": 1,
                    "Unit": "Count",
                }
            )
        if run.finished_at:
            started_at = datetime.fromisoformat(run.started_at)
            finished_at = datetime.fromisoformat(run.finished_at)
            metric_data.append(
                {
                    "MetricName": "CrawlDurationSeconds",
                    "Value": max(0, int((finished_at - started_at).total_seconds())),
                    "Unit": "Seconds",
                }
            )

        dimensions = [
            {"Name": "Project", "Value": os.environ.get("PROJECT_NAME", "job-finder")},
            {"Name": "Environment", "Value": os.environ.get("ENVIRONMENT", "dev")},
        ]
        for item in metric_data:
            item["Dimensions"] = dimensions
        boto3.client("cloudwatch").put_metric_data(Namespace="JobFinder", MetricData=metric_data)
    except Exception:
        logger.exception("Failed to emit crawl metrics for %s", run.run_id)


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
    _emit_crawl_metrics(run)
    return _response(200, {"run": run.to_item()})


def api_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET")
    path = event.get("rawPath", "/")
    if method == "OPTIONS":
        return _response(200, {"ok": True})
    if method == "GET" and path == "/health":
        return _response(200, {"ok": True})

    try:
        repository = _repository()
        if method == "GET" and path == "/health/freshness":
            query = event.get("queryStringParameters") or {}
            return _response(200, get_freshness(repository, query))
        if method == "GET" and path == "/jobs":
            query = event.get("queryStringParameters") or {}
            return _response(200, list_jobs(repository, query))
        if method == "GET" and path.startswith("/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            return _response(200, {"item": get_job(repository, job_id)})
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
    except Exception:
        logger.exception("Unhandled API error for %s %s", method, path)
        return _response(500, {"error": "Internal server error"})
    return _response(404, {"error": f"No route for {method} {path}"})
