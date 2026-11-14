"""Local dashboard and API, using the same application functions as Lambda."""
from __future__ import annotations

import json
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from job_finder import api
from job_finder.collector import build_job_record
from job_finder.models import CrawlRunRecord, SourceConfig, utc_now_iso
from job_finder.storage import LocalJsonRepository


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, repository: LocalJsonRepository, demo: bool = False, **kwargs: Any) -> None:
        self.repository = repository
        self.demo = demo
        super().__init__(*args, **kwargs)

    def _respond(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = json.dumps(payload).encode() if content_type == "application/json" else str(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api(self, method: str) -> bool:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        routes = {
            "/jobs": api.list_jobs, "/digest": api.get_digest,
            "/sources": api.list_sources, "/health/freshness": api.get_freshness,
        }
        if path == "/config.json" and method == "GET":
            self._respond(200, {"apiBaseUrl": "", "demo": self.demo})
            return True
        if not (path in {*routes, "/health", "/jobs/export"} or path.startswith(("/jobs/", "/sources/"))):
            return False
        try:
            if method == "GET":
                if path == "/health":
                    result = {"ok": True}
                elif path == "/jobs/export":
                    export = api.export_jobs(self.repository, query)
                    self._respond(200, export["body"], export["content_type"])
                    return True
                elif path.startswith("/jobs/"):
                    result = {"item": api.get_job(self.repository, unquote(path.rsplit("/", 1)[-1]))}
                elif path in routes:
                    result = routes[path](self.repository, query)
                else:
                    self._respond(404, {"error": "Unknown resource"})
                    return True
            elif method in {"PATCH", "POST"}:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 1_000_000:
                    raise ValueError("Request body too large")
                payload = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object")
                if method == "POST" and path == "/sources":
                    result = api.create_source(self.repository, payload)
                elif method == "PATCH" and path.startswith("/jobs/"):
                    result = {"item": api.update_job(self.repository, unquote(path.rsplit("/", 1)[-1]), payload)}
                elif method == "PATCH" and path.startswith("/sources/"):
                    result = {"item": api.update_source_control(self.repository, unquote(path.rsplit("/", 1)[-1]), payload)}
                else:
                    self._respond(405, {"error": "Method not allowed"})
                    return True
            else:
                self._respond(405, {"error": "Method not allowed"})
                return True
            self._respond(200, result)
        except KeyError:
            self._respond(404, {"error": "Unknown resource"})
        except (ValueError, TypeError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.log_error("Local API failed: %s", exc)
            self._respond(500, {"error": "Local storage or API error; inspect the server log"})
        return True

    def do_GET(self) -> None:
        if not self._api("GET"):
            super().do_GET()

    def do_PATCH(self) -> None:
        if not self._api("PATCH"):
            self._respond(404, {"error": "Unknown resource"})

    def do_POST(self) -> None:
        if not self._api("POST"):
            self._respond(404, {"error": "Unknown resource"})


def populate_demo(repository: LocalJsonRepository) -> None:
    """Illustrative fixtures only: never fetched or represented as live openings."""
    roles = [
        ("Demo Northstar", "Senior DevOps Engineer", "Remote, United States", "Own AWS cloud infrastructure using Terraform and Ansible. Improve CI/CD deployment pipelines, Linux automation, observability and incident response."),
        ("Demo Cedar", "Site Reliability Engineer", "Remote, United States", "Build production reliability with AWS, Terraform, Prometheus, Grafana and Python automation."),
        ("Demo Harbor", "Release Engineer", "Remote, United States", "Speed up build pipelines and release pipelines using GitLab CI/CD, Linux RPM packaging, Ansible and Bash."),
        ("Demo Northstar", "Platform Engineer", "Remote, United States", "Maintain AWS cloud infrastructure and developer tooling using Terraform, GitHub Actions and observability."),
        ("Demo Orbit", "DevOps Engineer", "Remote", "AWS Terraform Ansible CI/CD automation Linux"),
        ("Demo Spruce", "Senior DevOps Engineer (Contract)", "Remote, United States", "AWS Terraform CI/CD automation for Linux systems. This is a 1099 contract role."),
        ("Demo Meadow", "Software Engineer", "Remote, United States", "Build customer-facing React applications. Our stack includes AWS and Terraform."),
        ("Demo Maple", "Platform Engineer", "Remote, Canada", "AWS Terraform Kubernetes Helm"),
    ]
    for index, (company, title, location, description) in enumerate(roles):
        source = SourceConfig(company=company, source_type="greenhouse", identifier=f"demo-{index}")
        repository.upsert_source(source)
        job = build_job_record(source, {
            "source_job_id": str(index), "title": title, "location_text": location,
            "description_text": description, "apply_url": f"https://example.com/demo-role/{index}",
            "posted_date": utc_now_iso(),
        }, None)
        repository.upsert_job(job)
    repository.record_crawl_run(CrawlRunRecord(
        run_id="demo", started_at=utc_now_iso(), finished_at=utc_now_iso(),
        pages_checked=len(roles), jobs_found=len(roles), jobs_inserted=len(roles),
    ))


def serve_dashboard(*, jobs_path: str, runs_path: str, sources_path: str | None, port: int, frontend: str, demo: bool) -> None:
    directory = str(Path(frontend).resolve())
    if not (Path(directory) / "index.html").is_file():
        raise ValueError(f"Dashboard not found: {directory}. Pass --frontend with the frontend directory.")
    with TemporaryDirectory(prefix="job-finder-demo-") as temporary:
        repository = LocalJsonRepository(
            jobs_path=f"{temporary}/jobs.json" if demo else jobs_path,
            runs_path=f"{temporary}/runs.json" if demo else runs_path,
            sources_path=f"{temporary}/sources.json" if demo else sources_path,
        )
        if demo:
            populate_demo(repository)
        handler = partial(DashboardHandler, directory=directory, repository=repository, demo=demo)
        # Loopback only: deployed hosting continues to use API Gateway/CloudFront.
        with HTTPServer(("127.0.0.1", port), handler) as server:
            print(f"Job Finder: http://127.0.0.1:{server.server_port}" + (" (illustrative demo data)" if demo else ""), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
